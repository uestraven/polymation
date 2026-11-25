"""Content-addressed caching of independently rendered scenes.

A cache entry is keyed by a hash of **everything that can change the rendered
bytes**: the scene's timeline content, the checksums of every asset it
references, the resolved text and font identity of its overlays, the brand
values composition reads, the render profile including encoder settings, the
renderer's own implementation version, and the FFmpeg build.

Modification timestamps are deliberately *not* part of the key. A timestamp is
not evidence about content: touching a file changes it, restoring a backup does
not, and a filesystem with coarse timestamps can miss a change entirely.
Content hashes answer the question that actually matters.

Two consequences follow from hashing inputs this way, and both are tested:
editing one scene leaves every other scene's key unchanged, and changing a
global input (the profile, the brand, the FFmpeg build, the renderer version)
changes every key.

Entries are validated before reuse and written atomically, so an interrupted
render cannot leave a truncated file that a later run mistakes for a hit.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from polymation.config.channel import ChannelBrand
from polymation.errors import RenderError
from polymation.media.checksum import sha256_file
from polymation.rendering.profiles import RenderProfile
from polymation.schemas.assets import MediaAsset
from polymation.schemas.base import utc_now
from polymation.schemas.timeline import CompositionTimeline, TimelineScene

#: Bumped whenever this package's rendering behaviour changes in a way that
#: alters output for unchanged inputs. Part of every cache key, so a renderer
#: change invalidates every entry rather than serving stale frames.
RENDERER_VERSION = "m0.2.1"

#: Directory, relative to the project root, holding cached scene renders.
CACHE_DIRNAME = ".cache"

#: Sidecar suffix for an entry's metadata.
METADATA_SUFFIX = ".json"


@dataclass(frozen=True)
class CacheKey:
    """A scene's content address."""

    scene_id: str
    digest: str
    #: The exact payload that was hashed. Kept for diagnostics: when a cache
    #: miss is surprising, this shows which input changed.
    payload: dict[str, Any] = field(default_factory=dict, compare=False)

    @property
    def short(self) -> str:
        """First 12 hex characters, for logs and filenames."""
        return self.digest[:12]

    def filename(self, suffix: str) -> str:
        """Entry filename for this key."""
        return f"{self.scene_id}-{self.short}{suffix}"


@dataclass
class CacheEntry:
    """A validated cache entry on disk."""

    key: CacheKey
    path: Path
    size_bytes: int
    created_at: datetime

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable form."""
        return {
            "scene_id": self.key.scene_id,
            "digest": self.key.digest,
            "path": str(self.path),
            "size_bytes": self.size_bytes,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class CacheStatistics:
    """Counters for one render, reported to the operator."""

    hits: list[str] = field(default_factory=list)
    misses: list[str] = field(default_factory=list)
    rebuilt: list[str] = field(default_factory=list)
    corrupt: list[str] = field(default_factory=list)

    @property
    def hit_count(self) -> int:
        """Scenes served from cache."""
        return len(self.hits)

    @property
    def miss_count(self) -> int:
        """Scenes not found in cache."""
        return len(self.misses)

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable form."""
        return {
            "hits": list(self.hits),
            "misses": list(self.misses),
            "rebuilt": list(self.rebuilt),
            "corrupt_discarded": list(self.corrupt),
            "hit_count": self.hit_count,
            "miss_count": self.miss_count,
            "rebuilt_count": len(self.rebuilt),
        }


def _canonical(payload: Any) -> str:
    """Serialise a payload so equal content always yields equal bytes."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _font_identity(family: str) -> dict[str, Any]:
    """Identity and content hash of the font a family actually resolves to.

    Two machines with the same family name but different font *files* must not
    share cache entries: the glyphs differ, so the pixels differ.
    """
    from polymation.rendering.fixtures import resolve_font

    resolution = resolve_font(family)
    if resolution.path is None:
        return {"family": family, "resolved": None, "sha256": None, "fallback": True}

    try:
        digest = sha256_file(resolution.path)
    except OSError:  # pragma: no cover - unreadable font file
        digest = None

    return {
        "family": family,
        "resolved": resolution.path.name,
        "sha256": digest,
        "fallback": resolution.is_fallback,
    }


def compute_scene_key(
    scene: TimelineScene,
    timeline: CompositionTimeline,
    profile: RenderProfile,
    brand: ChannelBrand,
    *,
    assets: dict[str, MediaAsset],
    ffmpeg_version: str,
) -> CacheKey:
    """Compute the content address of one rendered scene.

    Raises:
        RenderError: if the scene references an asset that is not available,
            since its checksum could not then be part of the key.
    """
    asset_identity: dict[str, str] = {}
    for asset_id in sorted(set(scene.asset_ids())):
        asset = assets.get(asset_id)
        if asset is None:
            raise RenderError(
                f"scene {scene.scene_id!r} references asset {asset_id!r}, which is not "
                f"in the asset collection, so its cache key cannot be computed"
            )
        asset_identity[asset_id] = asset.sha256

    font_families = sorted(
        {overlay.style.font_family for overlay in scene.all_overlays() if overlay.is_text}
    )

    payload: dict[str, Any] = {
        # The scene's own content, exactly as serialised.
        "scene": scene.model_dump(mode="json"),
        # Asset identity by content, never by path or timestamp.
        "assets": asset_identity,
        # Fonts actually resolved, by content.
        "fonts": [_font_identity(family) for family in font_families],
        # Composition-relevant brand values. The whole brand is included
        # because any of it could reach a rendered pixel.
        "brand": brand.model_dump(mode="json"),
        # Geometry the timeline imposes on composition.
        "timeline": {
            "fps": timeline.fps,
            "width": timeline.width,
            "height": timeline.height,
            "safe_area_inset": timeline.safe_area_inset,
        },
        # Profile including every encoder setting.
        "profile": profile.cache_identity(),
        # Our own behaviour, and the encoder's.
        "renderer_version": RENDERER_VERSION,
        "ffmpeg_version": ffmpeg_version,
    }

    digest = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
    return CacheKey(scene_id=scene.scene_id, digest=digest, payload=payload)


class SceneCache:
    """Filesystem-backed cache of rendered scenes, scoped to one project."""

    def __init__(self, project_root: Path, profile: RenderProfile) -> None:
        self.project_root = project_root
        self.profile = profile
        # Preview and final entries never share a directory, so clearing one
        # profile's cache cannot disturb the other's.
        self.root = project_root / CACHE_DIRNAME / "scenes" / profile.name.value

    def path_for(self, key: CacheKey) -> Path:
        """Where ``key``'s video would live."""
        return self.root / key.filename(self.profile.container_suffix)

    def metadata_path_for(self, key: CacheKey) -> Path:
        """Where ``key``'s metadata sidecar would live."""
        return self.root / key.filename(METADATA_SUFFIX)

    def lookup(self, key: CacheKey) -> CacheEntry | None:
        """Return a *validated* entry for ``key``, or ``None``.

        Validation is why this does not simply test for existence: a render
        killed mid-write can leave a short file, and serving that as a hit would
        silently corrupt the output. A failed check discards the entry so the
        next step rebuilds it.
        """
        video = self.path_for(key)
        metadata = self.metadata_path_for(key)

        if not video.is_file() or not metadata.is_file():
            return None

        try:
            payload = json.loads(metadata.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.discard(key)
            return None

        if payload.get("digest") != key.digest:
            self.discard(key)
            return None

        recorded_size = payload.get("size_bytes")
        actual_size = video.stat().st_size
        if not isinstance(recorded_size, int) or recorded_size != actual_size:
            self.discard(key)
            return None

        if actual_size == 0:
            self.discard(key)
            return None

        recorded_checksum = payload.get("sha256")
        if isinstance(recorded_checksum, str):
            if sha256_file(video) != recorded_checksum:
                self.discard(key)
                return None

        try:
            created_at = datetime.fromisoformat(str(payload.get("created_at")))
        except ValueError:
            created_at = utc_now()

        return CacheEntry(key=key, path=video, size_bytes=actual_size, created_at=created_at)

    def store(self, key: CacheKey, produced: Path) -> CacheEntry:
        """Move a freshly rendered file into the cache, atomically.

        The video is placed with :func:`os.replace` and the metadata sidecar
        written only afterwards, so a crash between the two leaves an entry that
        ``lookup`` rejects for want of metadata rather than one it accepts as
        complete.
        """
        if not produced.is_file() or produced.stat().st_size == 0:
            raise RenderError(
                f"refusing to cache {produced}: it is missing or empty, so it is not a "
                f"complete render"
            )

        self.root.mkdir(parents=True, exist_ok=True)
        destination = self.path_for(key)

        if produced.resolve() != destination.resolve():
            os.replace(produced, destination)

        digest = sha256_file(destination)
        size = destination.stat().st_size
        created_at = utc_now()

        metadata = {
            "scene_id": key.scene_id,
            "digest": key.digest,
            "sha256": digest,
            "size_bytes": size,
            "created_at": created_at.isoformat(),
            "renderer_version": RENDERER_VERSION,
            "profile": self.profile.name.value,
        }
        _write_json_atomic(self.metadata_path_for(key), metadata)

        return CacheEntry(key=key, path=destination, size_bytes=size, created_at=created_at)

    def discard(self, key: CacheKey) -> None:
        """Remove an entry and its metadata, if present."""
        self.path_for(key).unlink(missing_ok=True)
        self.metadata_path_for(key).unlink(missing_ok=True)

    def entries(self) -> list[tuple[str, Path, int]]:
        """Every cached video in this profile's cache: ``(name, path, bytes)``."""
        if not self.root.is_dir():
            return []
        found: list[tuple[str, Path, int]] = []
        for path in sorted(self.root.iterdir()):
            if path.is_file() and path.suffix == self.profile.container_suffix:
                found.append((path.name, path, path.stat().st_size))
        return found

    def total_bytes(self) -> int:
        """Total size of this profile's cached videos."""
        return sum(size for _, _, size in self.entries())

    def clear(self) -> int:
        """Delete this profile's cache. Returns the number of files removed.

        Scoped to ``<project>/.cache/scenes/<profile>``. Source media lives in
        ``<project>/assets`` and is never touched by this.
        """
        if not self.root.is_dir():
            return 0
        removed = 0
        for path in list(self.root.iterdir()):
            if path.is_file():
                path.unlink()
                removed += 1
        return removed


def clear_project_cache(project_root: Path) -> int:
    """Delete every cached render for a project, across all profiles.

    Removes only ``<project>/.cache``. Imported source media and rendered
    outputs are left alone — a cache clear must never destroy user media.
    """
    cache_root = project_root / CACHE_DIRNAME
    if not cache_root.is_dir():
        return 0

    # Guard against a surprising root, since this deletes a tree.
    expected = (project_root / CACHE_DIRNAME).resolve()
    if cache_root.resolve() != expected:  # pragma: no cover - defensive
        raise RenderError(f"refusing to clear {cache_root}: unexpected location")

    removed = sum(1 for path in cache_root.rglob("*") if path.is_file())
    shutil.rmtree(cache_root)
    return removed


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON via a temporary file and ``os.replace``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    try:
        with handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, path)
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise
