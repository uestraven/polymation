"""The render engine: preflight, per-scene rendering, assembly, verification.

Order of operations, and why:

1. **Recover and lock.** Resolve any interrupted write, then take the project
   lock, so two renders cannot interleave.
2. **Validate.** A render that would fail is refused before any encoding, so
   the operator does not wait minutes to learn a reference is dangling.
3. **Render scenes**, serving cache hits where inputs are unchanged.
4. **Assemble**: concatenate scene intermediates by stream copy, mix audio
   once, optionally burn in captions, encode the final file.
5. **Verify** with ffprobe and measure loudness, then report both.

A failed render never replaces a valid existing output: the final file is
produced under a temporary name and moved into place only after it has been
verified.
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from polymation.captions.srt import CaptionCue, format_srt, parse_srt
from polymation.config.channel import ChannelConfig
from polymation.errors import NotRenderReadyError, RenderError
from polymation.projects.locking import ProjectLock
from polymation.projects.store import ProjectBundle
from polymation.rendering.audio import AudioPlan, build_audio_plan
from polymation.rendering.cache import (
    AssemblyCache,
    CacheKey,
    CacheStatistics,
    SceneCache,
    compute_assembly_key,
    compute_scene_key,
)
from polymation.rendering.compose import OutputInspection, inspect_output
from polymation.rendering.ffmpeg import ToolInfo, require_ffmpeg, run_tool
from polymation.rendering.graph import overlay_chains
from polymation.rendering.loudness import (
    LoudnessMeasurement,
    loudnorm_filter,
    measure_loudness,
)
from polymation.rendering.overlays import PlacedOverlay, render_overlay
from polymation.rendering.profiles import RenderProfile
from polymation.rendering.scene import render_scene
from polymation.schemas.assets import MediaAsset
from polymation.schemas.timeline import (
    CompositionTimeline,
    Overlay,
    OverlayKind,
    TimelineScene,
)
from polymation.schemas.timing import FrameSpan
from polymation.timeline.validate import validate_timeline
from polymation.validation.findings import Finding, Severity, ValidationReport


@dataclass
class SceneOutcome:
    """What happened to one scene during a render."""

    scene_id: str
    cached: bool
    path: Path
    frames: int
    seconds: float
    #: The scene's content address, used to key the assembly cache.
    digest: str = ""

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable form."""
        return {
            "scene_id": self.scene_id,
            "cached": self.cached,
            "frames": self.frames,
            "render_seconds": round(self.seconds, 3),
            "path": str(self.path),
        }


@dataclass
class RenderOutcome:
    """The complete result of a render."""

    profile: RenderProfile
    output_path: Path | None
    scenes: list[SceneOutcome] = field(default_factory=list)
    cache: CacheStatistics = field(default_factory=CacheStatistics)
    inspection: OutputInspection | None = None
    loudness: LoudnessMeasurement | None = None
    total_seconds: float = 0.0
    assembly_seconds: float = 0.0
    cautions: list[Finding] = field(default_factory=list)
    #: True when only some scenes were rendered, so the assembled output on
    #: disk does not reflect the whole timeline.
    scene_only: bool = False
    #: True when the fully assembled output came from the assembly cache.
    assembly_cached: bool = False
    #: Path to the stale final output when ``scene_only`` left it untouched.
    stale_output_path: Path | None = None
    sidecar_srt_path: Path | None = None
    stem_paths: dict[str, Path] = field(default_factory=dict)
    used_fallback_fonts: bool = False
    broke_stale_lock: str | None = None

    @property
    def cache_bytes(self) -> int:
        """Total size of the scene intermediates used."""
        return sum(scene.path.stat().st_size for scene in self.scenes if scene.path.is_file())

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable form, for machine-readable CLI output."""
        return {
            "profile": self.profile.name.value,
            "output_path": str(self.output_path) if self.output_path else None,
            "scene_only": self.scene_only,
            "assembly_cached": self.assembly_cached,
            "final_assembly_updated": not self.scene_only,
            "stale_output_path": (str(self.stale_output_path) if self.stale_output_path else None),
            "scenes": [scene.to_dict() for scene in self.scenes],
            "cache": self.cache.to_dict(),
            "cache_bytes": self.cache_bytes,
            "inspection": self.inspection.to_dict() if self.inspection else None,
            "loudness": self.loudness.to_dict() if self.loudness else None,
            "total_seconds": round(self.total_seconds, 3),
            "assembly_seconds": round(self.assembly_seconds, 3),
            "cautions": [finding.to_dict() for finding in self.cautions],
            "sidecar_srt_path": (str(self.sidecar_srt_path) if self.sidecar_srt_path else None),
            "stems": {role: str(path) for role, path in self.stem_paths.items()},
            "used_fallback_fonts": self.used_fallback_fonts,
            "broke_stale_lock": self.broke_stale_lock,
            "publication_ready": None,
            "publication_note": (
                "Publication readiness is not evaluated. A technically correct render "
                "is not an editorial or rights approval."
            ),
        }


def _asset_maps(bundle: ProjectBundle) -> tuple[dict[str, MediaAsset], dict[str, Path]]:
    """Asset records and resolved on-disk paths, keyed by asset id."""
    assets = {asset.asset_id: asset for asset in bundle.assets.assets}
    paths = {asset_id: bundle.asset_path(asset.path) for asset_id, asset in assets.items()}
    return assets, paths


def _rights_cautions(bundle: ProjectBundle, timeline: CompositionTimeline) -> list[Finding]:
    """Unresolved-rights cautions for every asset the timeline uses.

    Rendering is permitted with unresolved rights — an internal technical
    preview is exactly what this is for — but it is never silent about it, and
    never described as publication approval.
    """
    cautions: list[Finding] = []
    for asset_id in sorted(timeline.asset_ids()):
        record = bundle.rights.for_asset(asset_id)
        if record is None or not record.is_resolved:
            cautions.append(
                Finding(
                    severity=Severity.WARNING,
                    code="unresolved_rights_in_render",
                    message=(
                        f"Asset {asset_id} has unresolved rights. This render is an "
                        f"internal technical artefact and is not approved for "
                        f"publication."
                    ),
                    location=f"rights.json:{asset_id}",
                )
            )
    return cautions


def render(
    bundle: ProjectBundle,
    channel: ChannelConfig,
    profile: RenderProfile,
    *,
    scene_ids: list[str] | None = None,
    force: bool = False,
    overwrite: bool = False,
    output_path: Path | None = None,
    verify_checksums: bool = True,
) -> RenderOutcome:
    """Render ``bundle``'s timeline with ``profile``.

    Args:
        bundle: a loaded project. Must carry a compiled timeline.
        channel: channel configuration, for brand colours and fonts.
        profile: preview or final.
        scene_ids: render only these scenes. Final assembly is then skipped and
            the outcome says so, because the output on disk no longer reflects
            the whole timeline.
        force: ignore cache hits and re-render every requested scene.
        overwrite: permit replacing an existing final output.
        output_path: destination. Defaults to
            ``<project>/output/<project_id>-<profile>.mp4``.
        verify_checksums: re-hash referenced assets during preflight.

    Raises:
        NotRenderReadyError: if validation found blocking problems.
        RenderError: if rendering or assembly fails.
        ProjectLockedError: if another command holds the project lock.
        MissingDependencyError: if FFmpeg is unavailable.
    """
    started = time.perf_counter()

    timeline = bundle.timeline
    if timeline is None:
        raise NotRenderReadyError(
            f"Project {bundle.project_id} has no compiled timeline. Run "
            f"`polymation timeline compile {bundle.project_id}` first."
        )

    ffmpeg: ToolInfo = require_ffmpeg()
    if ffmpeg.path is None or ffmpeg.version is None:  # pragma: no cover
        raise RenderError("ffmpeg reported as available but has no resolved path")

    operation = f"render {profile.name.value}"
    if scene_ids:
        operation += f" scene(s) {','.join(scene_ids)}"

    with ProjectLock(bundle.root, operation=operation) as lock:
        outcome = RenderOutcome(profile=profile, output_path=None)
        outcome.broke_stale_lock = lock.broke_stale_lock

        report = validate_timeline(bundle, timeline, profile, verify_checksums=verify_checksums)
        if report.errors:
            detail = "\n".join(f"  - [{item.code}] {item.message}" for item in report.errors)
            raise NotRenderReadyError(
                f"Project {bundle.project_id} cannot be rendered with profile "
                f"{profile.name.value!r}:\n{detail}"
            )

        outcome.cautions = report.warnings + _rights_cautions(bundle, timeline)

        assets, asset_paths = _asset_maps(bundle)
        cache = SceneCache(bundle.root, profile)

        requested = _select_scenes(timeline, scene_ids)
        outcome.scene_only = scene_ids is not None

        work_root = bundle.root / timeline_cache_work_dir(profile)
        work_root.mkdir(parents=True, exist_ok=True)

        try:
            for scene in requested:
                outcome.scenes.append(
                    _render_or_reuse(
                        scene,
                        timeline,
                        profile,
                        channel,
                        ffmpeg,
                        assets=assets,
                        asset_paths=asset_paths,
                        cache=cache,
                        statistics=outcome.cache,
                        work_root=work_root,
                        force=force,
                    )
                )

            outcome.used_fallback_fonts = _any_fallback_fonts(timeline)

            if outcome.scene_only:
                destination = _default_output_path(bundle, profile, output_path)
                outcome.stale_output_path = destination if destination.is_file() else None
            else:
                assembly_started = time.perf_counter()
                outcome.output_path = _assemble(
                    bundle,
                    timeline,
                    profile,
                    channel,
                    ffmpeg,
                    assets=assets,
                    asset_paths=asset_paths,
                    scene_outcomes=outcome.scenes,
                    work_root=work_root,
                    output_path=output_path,
                    overwrite=overwrite,
                    outcome=outcome,
                )
                outcome.assembly_seconds = time.perf_counter() - assembly_started

                outcome.inspection = inspect_output(
                    outcome.output_path,
                    expected_duration_seconds=timeline.duration_seconds,
                    duration_tolerance_seconds=0.2,
                    require_audio=bool(timeline.audio.segments),
                    profile=profile,
                )
                outcome.loudness = measure_loudness(outcome.output_path)
        finally:
            shutil.rmtree(work_root, ignore_errors=True)

        outcome.total_seconds = time.perf_counter() - started
        return outcome


def timeline_cache_work_dir(profile: RenderProfile) -> str:
    """Project-relative scratch directory for a render of ``profile``."""
    return f".cache/work/{profile.name.value}"


def _select_scenes(
    timeline: CompositionTimeline, scene_ids: list[str] | None
) -> list[TimelineScene]:
    """Scenes to render, in timeline order."""
    ordered = timeline.scenes_in_order
    if scene_ids is None:
        return ordered

    wanted = set(scene_ids)
    known = {scene.scene_id for scene in ordered}
    unknown = sorted(wanted - known)
    if unknown:
        raise RenderError(
            f"No scene(s) {', '.join(repr(item) for item in unknown)} in the timeline. "
            f"Available: {', '.join(sorted(known))}."
        )
    return [scene for scene in ordered if scene.scene_id in wanted]


def _render_or_reuse(
    scene: TimelineScene,
    timeline: CompositionTimeline,
    profile: RenderProfile,
    channel: ChannelConfig,
    ffmpeg: ToolInfo,
    *,
    assets: dict[str, MediaAsset],
    asset_paths: dict[str, Path],
    cache: SceneCache,
    statistics: CacheStatistics,
    work_root: Path,
    force: bool,
) -> SceneOutcome:
    """Serve a scene from cache, or render and cache it."""
    key: CacheKey = compute_scene_key(
        scene,
        timeline,
        profile,
        channel.brand,
        assets=assets,
        ffmpeg_version=ffmpeg.version or "unknown",
    )

    if not force:
        # Note whether an entry existed *before* looking it up: lookup discards
        # an entry that fails validation, so checking afterwards would always
        # find nothing and a corrupt entry would be reported as a plain miss.
        existed = cache.path_for(key).exists() or cache.metadata_path_for(key).exists()
        entry = cache.lookup(key)
        if entry is not None:
            statistics.hits.append(scene.scene_id)
            return SceneOutcome(
                scene_id=scene.scene_id,
                cached=True,
                path=entry.path,
                frames=scene.duration_frames,
                seconds=0.0,
                digest=key.digest,
            )
        if existed:
            statistics.corrupt.append(scene.scene_id)

    statistics.misses.append(scene.scene_id)

    started = time.perf_counter()
    produced = work_root / f"scene-{scene.scene_id}{profile.container_suffix}"
    render_scene(
        scene,
        timeline,
        profile,
        channel.brand,
        ffmpeg,
        asset_paths=asset_paths,
        assets=assets,
        destination=produced,
        work_dir=work_root / f"overlays-{scene.scene_id}",
    )
    elapsed = time.perf_counter() - started

    entry = cache.store(key, produced)
    statistics.rebuilt.append(scene.scene_id)

    return SceneOutcome(
        scene_id=scene.scene_id,
        cached=False,
        path=entry.path,
        frames=scene.duration_frames,
        seconds=elapsed,
        digest=key.digest,
    )


def _any_fallback_fonts(timeline: CompositionTimeline) -> bool:
    """Whether any overlay's font family resolves to a substitute."""
    from polymation.rendering.fixtures import resolve_font

    families = {
        overlay.style.font_family
        for scene in timeline.scenes
        for overlay in scene.all_overlays()
        if overlay.is_text
    }
    if timeline.captions.burn_in:
        families.add(timeline.captions.placement.style.font_family)
    return any(resolve_font(family).is_fallback for family in families)


def _default_output_path(
    bundle: ProjectBundle, profile: RenderProfile, output_path: Path | None
) -> Path:
    """Where the assembled output goes."""
    if output_path is not None:
        return output_path
    name = f"{bundle.project_id}-{profile.name.value}{profile.container_suffix}"
    return bundle.root / bundle.manifest.paths.output_dir / name


def _assemble(
    bundle: ProjectBundle,
    timeline: CompositionTimeline,
    profile: RenderProfile,
    channel: ChannelConfig,
    ffmpeg: ToolInfo,
    *,
    assets: dict[str, MediaAsset],
    asset_paths: dict[str, Path],
    scene_outcomes: list[SceneOutcome],
    work_root: Path,
    output_path: Path | None,
    overwrite: bool,
    outcome: RenderOutcome,
) -> Path:
    """Concatenate scenes, mix audio, optionally burn captions, encode output."""
    destination = _default_output_path(bundle, profile, output_path)

    if destination.exists() and not overwrite:
        raise RenderError(
            f"{destination} already exists. Pass --overwrite to replace it; an existing "
            f"render is never silently discarded."
        )

    destination.parent.mkdir(parents=True, exist_ok=True)

    cues = _load_cues(bundle, timeline, profile)
    caption_bytes = format_srt(cues, fps=profile.fps).encode("utf-8") if cues else None

    # Assembly has its own cache dependencies: the scene digests plus the audio
    # mix, loudness target and caption text. Without this, burning in captions
    # re-encodes the whole video every render — captions change pixels, which
    # defeats the stream-copy path that makes a warm render cheap.
    assembly_cache = AssemblyCache(bundle.root, profile)
    assembly_key = compute_assembly_key(
        [scene.digest for scene in scene_outcomes],
        timeline,
        profile,
        caption_bytes=caption_bytes,
        ffmpeg_version=ffmpeg.version or "unknown",
    )
    cached_assembly = assembly_cache.lookup(assembly_key)
    if cached_assembly is not None:
        shutil.copy2(cached_assembly.path, destination)
        outcome.assembly_cached = True
        if timeline.captions.export_sidecar and cues:
            sidecar = destination.with_suffix(".srt")
            sidecar.write_text(format_srt(cues, fps=profile.fps), encoding="utf-8")
            outcome.sidecar_srt_path = sidecar
        return destination

    # Concatenate scene intermediates without re-encoding. All scenes share the
    # profile's encoder settings, so a stream copy is exact and fast.
    concat_list = work_root / "scenes.txt"
    concat_list.write_text(
        "".join(f"file '{scene.path.as_posix()}'\n" for scene in scene_outcomes),
        encoding="utf-8",
    )

    video_only = work_root / f"video{profile.container_suffix}"
    concat_result = run_tool(
        ffmpeg.path,  # type: ignore[arg-type]
        [
            "-nostdin",
            "-hide_banner",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-c",
            "copy",
            str(video_only),
        ],
    )
    if not concat_result.ok:
        raise RenderError(
            f"Concatenating scenes failed (status {concat_result.returncode}):\n"
            f"{concat_result.tail()}"
        )

    # `cues` was already loaded above, to key the assembly cache.
    if timeline.captions.export_sidecar and cues:
        sidecar = destination.with_suffix(".srt")
        sidecar.write_text(format_srt(cues, fps=profile.fps), encoding="utf-8")
        outcome.sidecar_srt_path = sidecar

    # Caption overlays become extra video inputs, which shifts where the audio
    # inputs land. Render them first so the offset is known before the audio
    # plan is built.
    caption_overlays = _caption_overlays(cues, timeline, profile) if cues else []
    placed_captions = [
        render_overlay(
            overlay,
            timeline,
            profile,
            channel.brand,
            work_dir=work_root / "captions",
            image_paths={},
            max_lines=timeline.captions.placement.max_lines,
        )
        for overlay in caption_overlays
    ]

    measurement: LoudnessMeasurement | None = None
    if timeline.audio.segments and not timeline.audio.loudness.measure_only:
        # Two-pass loudness: a single pass cannot know integrated loudness in
        # advance. The probe has only the video at input 0, so audio starts at 1.
        probe_plan = build_audio_plan(
            timeline,
            profile,
            asset_paths=asset_paths,
            assets=assets,
            first_input_index=1,
        )
        if not probe_plan.is_silent:
            probe_mix = work_root / "mix-probe.wav"
            _encode_mix_probe(ffmpeg, probe_plan, video_only, probe_mix, profile)
            measurement = measure_loudness(probe_mix)

    # Input 0 is the video; then one input per caption image; then audio.
    audio_plan = build_audio_plan(
        timeline,
        profile,
        asset_paths=asset_paths,
        assets=assets,
        first_input_index=1 + len(placed_captions),
    )

    final_temp = work_root / f"final{profile.container_suffix}"
    _encode_final(
        ffmpeg,
        video_only=video_only,
        audio_plan=audio_plan,
        placed_captions=placed_captions,
        timeline=timeline,
        profile=profile,
        measurement=measurement,
        destination=final_temp,
    )

    if not final_temp.is_file() or final_temp.stat().st_size == 0:
        raise RenderError("final assembly produced no output")

    # Verify before replacing, so a bad render cannot destroy a good one.
    verification = inspect_output(
        final_temp,
        expected_duration_seconds=timeline.duration_seconds,
        duration_tolerance_seconds=0.2,
        require_audio=bool(timeline.audio.segments),
        profile=profile,
    )
    if not verification.conforms:
        raise RenderError(
            "The assembled output does not conform to profile "
            f"{profile.name.value!r}:\n"
            + "\n".join(f"  - {item}" for item in verification.deviations)
            + f"\nThe existing output at {destination} was left untouched."
        )

    # Cache a copy before moving, so a later identical render is a file copy.
    cache_copy = work_root / f"assembly-cache{profile.container_suffix}"
    shutil.copy2(final_temp, cache_copy)
    assembly_cache.store(assembly_key, cache_copy)

    shutil.move(str(final_temp), str(destination))

    if timeline.audio.export_stems and timeline.audio.segments:
        outcome.stem_paths = _export_stems(
            ffmpeg,
            timeline,
            profile,
            asset_paths=asset_paths,
            assets=assets,
            video_only=video_only,
            destination=destination,
            outcome=outcome,
        )

    return destination


def _load_cues(
    bundle: ProjectBundle, timeline: CompositionTimeline, profile: RenderProfile
) -> list[CaptionCue]:
    """Parse the project's caption file, if it has one."""
    if timeline.captions.srt_path is None:
        return []
    path = bundle.path_for(timeline.captions.srt_path)
    if not path.is_file():
        return []
    return parse_srt(path.read_text(encoding="utf-8"), fps=profile.fps)


def _caption_overlays(
    cues: list[CaptionCue], timeline: CompositionTimeline, profile: RenderProfile
) -> list[Overlay]:
    """Build one overlay per cue, for burn-in."""
    if not timeline.captions.burn_in:
        return []

    placement = timeline.captions.placement
    return [
        Overlay(
            overlay_id=f"caption-{position + 1:04d}",
            kind=OverlayKind.SUBTITLE,
            span=cue.span,
            anchor=placement.anchor,
            offset_y_px=placement.offset_y_px,
            text=cue.text,
            style=placement.style,
        )
        for position, cue in enumerate(cues)
    ]


def _encode_mix_probe(
    ffmpeg: ToolInfo,
    audio_plan: AudioPlan,
    video_only: Path,
    destination: Path,
    profile: RenderProfile,
) -> None:
    """Render the audio mix alone to a WAV, for loudness measurement."""
    arguments = [
        "-nostdin",
        "-hide_banner",
        "-y",
        "-i",
        str(video_only),
        *[argument for group in audio_plan.inputs for argument in group],
        "-filter_complex",
        ";".join(audio_plan.chains),
        "-map",
        f"[{audio_plan.mix_label}]",
        "-c:a",
        "pcm_s16le",
        "-ar",
        str(profile.audio_sample_rate),
        "-ac",
        str(profile.audio_channels),
        str(destination),
    ]
    result = run_tool(ffmpeg.path, arguments)  # type: ignore[arg-type]
    if not result.ok:
        raise RenderError(
            f"Building the audio mix for measurement failed (status "
            f"{result.returncode}):\n{result.tail()}"
        )


def _encode_final(
    ffmpeg: ToolInfo,
    *,
    video_only: Path,
    audio_plan: AudioPlan,
    placed_captions: list[PlacedOverlay],
    timeline: CompositionTimeline,
    profile: RenderProfile,
    measurement: LoudnessMeasurement | None,
    destination: Path,
) -> None:
    """Encode the final file: video (copied or captioned) plus the mixed audio.

    When there are no burned-in captions the concatenated video already matches
    the profile exactly, so it is stream-copied rather than re-encoded — the
    scenes were encoded with these settings. Captions force a re-encode, which
    is unavoidable: pixels change.
    """
    arguments: list[str] = ["-nostdin", "-hide_banner", "-y", "-i", str(video_only)]
    chains: list[str] = []
    video_map = "0:v:0"

    if placed_captions:
        overlay_inputs: list[tuple[int, int, int, int, int, int, float]] = []
        for position, placed in enumerate(placed_captions):
            index = 1 + position
            arguments += ["-i", str(placed.image.path)]
            overlay_inputs.append(
                (
                    index,
                    placed.x,
                    placed.y,
                    placed.start_frame,
                    placed.end_frame,
                    0,
                    placed.opacity,
                )
            )
        chains.extend(
            overlay_chains(overlay_inputs, profile, base_label="0:v", output_label="vcap")
        )
        video_map = "[vcap]"

    audio_map: str | None = None
    if not audio_plan.is_silent:
        for group in audio_plan.inputs:
            arguments += group
        chains.extend(audio_plan.chains)
        audio_map = f"[{audio_plan.mix_label}]"

        if measurement is not None:
            normalisation = loudnorm_filter(
                measurement,
                integrated_lufs=timeline.audio.loudness.integrated_lufs,
                true_peak_dbtp=timeline.audio.loudness.true_peak_dbtp,
            )
            if normalisation is not None:
                chains.append(
                    f"[{audio_plan.mix_label}]{normalisation},"
                    f"aresample={profile.audio_sample_rate}[anorm]"
                )
                audio_map = "[anorm]"

    if chains:
        arguments += ["-filter_complex", ";".join(chains)]

    arguments += ["-map", video_map]
    if audio_map is not None:
        arguments += ["-map", audio_map]

    if video_map == "0:v:0":
        arguments += ["-c:v", "copy"]
    else:
        arguments += profile.video_encode_args()

    if audio_map is not None:
        arguments += profile.audio_encode_args()
    else:
        arguments += ["-an"]

    arguments += [
        "-t",
        f"{timeline.duration_seconds:.6f}",
        *profile.extra_output_args,
        str(destination),
    ]

    result = run_tool(ffmpeg.path, arguments)  # type: ignore[arg-type]
    if not result.ok:
        raise RenderError(f"Final assembly failed (status {result.returncode}):\n{result.tail()}")


def _export_stems(
    ffmpeg: ToolInfo,
    timeline: CompositionTimeline,
    profile: RenderProfile,
    *,
    asset_paths: dict[str, Path],
    assets: dict[str, MediaAsset],
    video_only: Path,
    destination: Path,
    outcome: RenderOutcome,
) -> dict[str, Path]:
    """Write one WAV per audio role alongside the final output.

    Each stem is rendered from its own single-role graph rather than by mapping
    a bus label out of the full mix: an FFmpeg filter output may be consumed
    only once, and the full mix already feeds every bus into ``amix``. Tapping
    it produced no stems at all, silently, until this was fixed.

    A stem that fails to render is reported as a caution rather than dropped,
    because a missing stem with no explanation is worse than a loud one.
    """
    stems: dict[str, Path] = {}
    stem_dir = destination.parent / f"{destination.stem}-stems"
    stem_dir.mkdir(parents=True, exist_ok=True)

    roles = {segment.role for segment in timeline.audio.segments}

    for role in sorted(roles, key=lambda item: item.value):
        plan = build_audio_plan(
            timeline,
            profile,
            asset_paths=asset_paths,
            assets=assets,
            first_input_index=1,
            only_roles=frozenset({role}),
        )
        if plan.is_silent:
            continue

        stem_path = stem_dir / f"{role.value}.wav"
        arguments = [
            "-nostdin",
            "-hide_banner",
            "-y",
            "-i",
            str(video_only),
            *[argument for group in plan.inputs for argument in group],
            "-filter_complex",
            ";".join(plan.chains),
            "-map",
            f"[{plan.mix_label}]",
            "-c:a",
            "pcm_s16le",
            "-ar",
            str(profile.audio_sample_rate),
            "-ac",
            str(profile.audio_channels),
            str(stem_path),
        ]
        result = run_tool(ffmpeg.path, arguments)  # type: ignore[arg-type]
        if result.ok and stem_path.is_file() and stem_path.stat().st_size > 0:
            stems[role.value] = stem_path
        else:
            outcome.cautions.append(
                Finding(
                    severity=Severity.WARNING,
                    code="stem_export_failed",
                    message=(
                        f"The {role.value} stem could not be written "
                        f"(FFmpeg status {result.returncode}). The mixed output is "
                        f"unaffected. Last FFmpeg output: {result.tail(4)}"
                    ),
                    location=str(stem_path),
                )
            )

    return stems


def preflight(
    bundle: ProjectBundle,
    profile: RenderProfile,
    *,
    verify_checksums: bool = True,
) -> ValidationReport:
    """Validate a project's timeline without rendering anything."""
    timeline = bundle.timeline
    if timeline is None:
        report = ValidationReport(project_id=bundle.project_id)
        report.error(
            "no_timeline",
            "The project has no compiled timeline. Run `polymation timeline compile` "
            "to create one from the storyboard.",
            location="timeline.json",
        )
        return report
    return validate_timeline(bundle, timeline, profile, verify_checksums=verify_checksums)


__all__ = [
    "FrameSpan",
    "RenderOutcome",
    "SceneOutcome",
    "preflight",
    "render",
    "timeline_cache_work_dir",
]
