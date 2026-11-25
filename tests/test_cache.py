"""Scene cache: key composition, selective invalidation, corruption recovery."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from polymation.config.channel import ChannelConfig
from polymation.errors import RenderError
from polymation.projects.store import ProjectBundle
from polymation.rendering.cache import (
    CACHE_DIRNAME,
    RENDERER_VERSION,
    AssemblyCache,
    SceneCache,
    clear_project_cache,
    compute_assembly_key,
    compute_scene_key,
)
from polymation.rendering.profiles import FINAL_PROFILE, PREVIEW_PROFILE
from polymation.schemas.assets import AssetType, MediaAsset
from polymation.schemas.base import utc_now
from polymation.schemas.timeline import (
    CompositionShot,
    CompositionTimeline,
    Overlay,
    OverlayKind,
    TextStyle,
    TimelineScene,
)
from polymation.schemas.timing import FrameSpan

NOW = utc_now()


def make_asset(asset_id: str, checksum: str) -> MediaAsset:
    return MediaAsset(
        asset_id=asset_id,
        asset_type=AssetType.IMAGE,
        category="images",
        path=f"assets/images/{asset_id}.png",
        detected_format="PNG",
        size_bytes=100,
        sha256=checksum,
        width=640,
        height=480,
        original_source="generated",
        acquired_at=NOW,
        rights_record_id="rights-0001",
    )


def make_scene(
    scene_id: str = "scene-01", asset_id: str = "asset-0001", **kw: object
) -> TimelineScene:
    payload: dict[str, object] = {
        "scene_id": scene_id,
        "order": 0,
        "shots": [
            CompositionShot(
                shot_id=f"{scene_id}-shot",
                asset_id=asset_id,
                span=FrameSpan(start_frame=0, duration_frames=60),
            )
        ],
    }
    payload.update(kw)
    return TimelineScene(**payload)  # type: ignore[arg-type]


def make_timeline(scenes: list[TimelineScene]) -> CompositionTimeline:
    return CompositionTimeline(
        project_id="p",
        scenes=[scene.model_copy(update={"order": index}) for index, scene in enumerate(scenes)],
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.fixture
def key_inputs(channel: ChannelConfig) -> dict[str, object]:
    """Everything compute_scene_key needs, with one asset."""
    scene = make_scene()
    return {
        "scene": scene,
        "timeline": make_timeline([scene]),
        "profile": FINAL_PROFILE,
        "brand": channel.brand,
        "assets": {"asset-0001": make_asset("asset-0001", "a" * 64)},
        "ffmpeg_version": "7.1",
    }


def key_for(**overrides: object) -> str:
    """Compute a digest with the given inputs overridden."""
    arguments = dict(overrides)
    return compute_scene_key(
        arguments.pop("scene"),  # type: ignore[arg-type]
        arguments.pop("timeline"),  # type: ignore[arg-type]
        arguments.pop("profile"),  # type: ignore[arg-type]
        arguments.pop("brand"),  # type: ignore[arg-type]
        assets=arguments.pop("assets"),  # type: ignore[arg-type]
        ffmpeg_version=arguments.pop("ffmpeg_version"),  # type: ignore[arg-type]
    ).digest


# --- key composition ----------------------------------------------------------


def test_the_same_inputs_give_the_same_key(key_inputs: dict[str, object]) -> None:
    assert key_for(**key_inputs) == key_for(**key_inputs)


def test_changing_the_scene_changes_the_key(key_inputs: dict[str, object]) -> None:
    base = key_for(**key_inputs)
    longer = make_scene()
    longer.shots[0].span = FrameSpan(start_frame=0, duration_frames=90)
    assert key_for(**{**key_inputs, "scene": longer}) != base


def test_changing_an_asset_checksum_changes_the_key(key_inputs: dict[str, object]) -> None:
    """Asset identity is content, so re-importing different bytes must invalidate."""
    base = key_for(**key_inputs)
    changed = {"asset-0001": make_asset("asset-0001", "b" * 64)}
    assert key_for(**{**key_inputs, "assets": changed}) != base


def test_changing_overlay_text_changes_the_key(key_inputs: dict[str, object]) -> None:
    scene = make_scene(
        overlays=[
            Overlay(
                overlay_id="o1",
                kind=OverlayKind.TITLE,
                span=FrameSpan(start_frame=0, duration_frames=30),
                text="before",
            )
        ]
    )
    before = key_for(**{**key_inputs, "scene": scene, "timeline": make_timeline([scene])})

    after_scene = make_scene(
        overlays=[
            Overlay(
                overlay_id="o1",
                kind=OverlayKind.TITLE,
                span=FrameSpan(start_frame=0, duration_frames=30),
                text="after",
            )
        ]
    )
    after = key_for(
        **{**key_inputs, "scene": after_scene, "timeline": make_timeline([after_scene])}
    )
    assert before != after


def test_changing_the_font_family_changes_the_key(key_inputs: dict[str, object]) -> None:
    def scene_with(family: str) -> TimelineScene:
        return make_scene(
            overlays=[
                Overlay(
                    overlay_id="o1",
                    kind=OverlayKind.TITLE,
                    span=FrameSpan(start_frame=0, duration_frames=30),
                    text="x",
                    style=TextStyle(font_family=family),
                )
            ]
        )

    first = scene_with("Space Grotesk")
    second = scene_with("IBM Plex Serif")
    assert key_for(**{**key_inputs, "scene": first, "timeline": make_timeline([first])}) != key_for(
        **{**key_inputs, "scene": second, "timeline": make_timeline([second])}
    )


def test_changing_the_profile_changes_the_key(key_inputs: dict[str, object]) -> None:
    assert key_for(**{**key_inputs, "profile": PREVIEW_PROFILE}) != key_for(**key_inputs)


def test_changing_an_encoder_setting_changes_the_key(key_inputs: dict[str, object]) -> None:
    """A CRF change alters bytes, so it must invalidate the cache."""
    import dataclasses

    tweaked = dataclasses.replace(FINAL_PROFILE, crf=FINAL_PROFILE.crf + 1)
    assert key_for(**{**key_inputs, "profile": tweaked}) != key_for(**key_inputs)


def test_changing_the_ffmpeg_version_changes_the_key(key_inputs: dict[str, object]) -> None:
    assert key_for(**{**key_inputs, "ffmpeg_version": "9.1"}) != key_for(**key_inputs)


def test_changing_the_brand_changes_the_key(
    key_inputs: dict[str, object], channel: ChannelConfig
) -> None:
    recoloured = channel.brand.model_copy(
        update={"palette": channel.brand.palette.model_copy(update={"deep_indigo": "#000001"})}
    )
    assert key_for(**{**key_inputs, "brand": recoloured}) != key_for(**key_inputs)


def test_changing_the_safe_area_changes_the_key(key_inputs: dict[str, object]) -> None:
    timeline = key_inputs["timeline"]
    assert isinstance(timeline, CompositionTimeline)
    moved = timeline.model_copy(update={"safe_area_inset": 0.1})
    assert key_for(**{**key_inputs, "timeline": moved}) != key_for(**key_inputs)


def test_a_missing_asset_makes_the_key_uncomputable(key_inputs: dict[str, object]) -> None:
    with pytest.raises(RenderError, match="cache key cannot be computed"):
        key_for(**{**key_inputs, "assets": {}})


def test_the_renderer_version_is_part_of_the_key(key_inputs: dict[str, object]) -> None:
    key = compute_scene_key(
        key_inputs["scene"],  # type: ignore[arg-type]
        key_inputs["timeline"],  # type: ignore[arg-type]
        FINAL_PROFILE,
        key_inputs["brand"],  # type: ignore[arg-type]
        assets=key_inputs["assets"],  # type: ignore[arg-type]
        ffmpeg_version="7.1",
    )
    assert key.payload["renderer_version"] == RENDERER_VERSION


def test_a_timestamp_is_not_part_of_the_key(key_inputs: dict[str, object]) -> None:
    """A timestamp is not evidence about content, so it must not appear."""
    key = compute_scene_key(
        key_inputs["scene"],  # type: ignore[arg-type]
        key_inputs["timeline"],  # type: ignore[arg-type]
        FINAL_PROFILE,
        key_inputs["brand"],  # type: ignore[arg-type]
        assets=key_inputs["assets"],  # type: ignore[arg-type]
        ffmpeg_version="7.1",
    )
    serialised = json.dumps(key.payload, default=str)
    for forbidden in ("mtime", "modified_at", "st_mtime", "updated_at", "created_at"):
        assert forbidden not in serialised, forbidden


# --- selective invalidation ---------------------------------------------------


def test_editing_one_scene_leaves_other_scene_keys_unchanged(
    channel: ChannelConfig,
) -> None:
    """The whole point of per-scene caching."""
    first = make_scene("scene-01", "asset-0001")
    second = make_scene("scene-02", "asset-0002")
    assets = {
        "asset-0001": make_asset("asset-0001", "a" * 64),
        "asset-0002": make_asset("asset-0002", "b" * 64),
    }
    before = make_timeline([first, second])

    def digest(timeline: CompositionTimeline, scene: TimelineScene) -> str:
        return compute_scene_key(
            scene,
            timeline,
            FINAL_PROFILE,
            channel.brand,
            assets=assets,
            ffmpeg_version="7.1",
        ).digest

    first_before = digest(before, before.scenes_in_order[0])
    second_before = digest(before, before.scenes_in_order[1])

    edited_second = make_scene("scene-02", "asset-0002")
    edited_second.shots[0].span = FrameSpan(start_frame=0, duration_frames=120)
    after = make_timeline([first, edited_second])

    assert digest(after, after.scenes_in_order[0]) == first_before
    assert digest(after, after.scenes_in_order[1]) != second_before


# --- store, lookup, validate --------------------------------------------------


def test_a_stored_entry_is_found_again(
    project: ProjectBundle, key_inputs: dict[str, object], tmp_path: Path
) -> None:
    cache = SceneCache(project.root, FINAL_PROFILE)
    key = compute_scene_key(
        key_inputs["scene"],  # type: ignore[arg-type]
        key_inputs["timeline"],  # type: ignore[arg-type]
        FINAL_PROFILE,
        key_inputs["brand"],  # type: ignore[arg-type]
        assets=key_inputs["assets"],  # type: ignore[arg-type]
        ffmpeg_version="7.1",
    )

    assert cache.lookup(key) is None

    produced = tmp_path / "scene.mp4"
    produced.write_bytes(b"x" * 2048)
    entry = cache.store(key, produced)

    assert entry.size_bytes == 2048
    found = cache.lookup(key)
    assert found is not None
    assert found.path == entry.path


def test_an_empty_render_is_never_cached(
    project: ProjectBundle, key_inputs: dict[str, object], tmp_path: Path
) -> None:
    cache = SceneCache(project.root, FINAL_PROFILE)
    key = compute_scene_key(
        key_inputs["scene"],  # type: ignore[arg-type]
        key_inputs["timeline"],  # type: ignore[arg-type]
        FINAL_PROFILE,
        key_inputs["brand"],  # type: ignore[arg-type]
        assets=key_inputs["assets"],  # type: ignore[arg-type]
        ffmpeg_version="7.1",
    )
    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")

    with pytest.raises(RenderError, match="not a complete render"):
        cache.store(key, empty)


def _store_entry(cache: SceneCache, key_inputs: dict[str, object], tmp_path: Path):
    key = compute_scene_key(
        key_inputs["scene"],  # type: ignore[arg-type]
        key_inputs["timeline"],  # type: ignore[arg-type]
        cache.profile,
        key_inputs["brand"],  # type: ignore[arg-type]
        assets=key_inputs["assets"],  # type: ignore[arg-type]
        ffmpeg_version="7.1",
    )
    produced = tmp_path / "scene.mp4"
    produced.write_bytes(b"x" * 4096)
    cache.store(key, produced)
    return key


def test_a_truncated_entry_is_rejected_and_discarded(
    project: ProjectBundle, key_inputs: dict[str, object], tmp_path: Path
) -> None:
    """A render killed mid-write must never be served as a hit."""
    cache = SceneCache(project.root, FINAL_PROFILE)
    key = _store_entry(cache, key_inputs, tmp_path)

    video = cache.path_for(key)
    video.write_bytes(b"x" * 10)

    assert cache.lookup(key) is None
    assert not video.exists()
    assert not cache.metadata_path_for(key).exists()


def test_an_entry_with_altered_content_is_rejected(
    project: ProjectBundle, key_inputs: dict[str, object], tmp_path: Path
) -> None:
    """Same length, different bytes: only the content hash catches this."""
    cache = SceneCache(project.root, FINAL_PROFILE)
    key = _store_entry(cache, key_inputs, tmp_path)

    video = cache.path_for(key)
    video.write_bytes(b"y" * 4096)

    assert cache.lookup(key) is None


def test_an_entry_with_missing_metadata_is_rejected(
    project: ProjectBundle, key_inputs: dict[str, object], tmp_path: Path
) -> None:
    cache = SceneCache(project.root, FINAL_PROFILE)
    key = _store_entry(cache, key_inputs, tmp_path)
    cache.metadata_path_for(key).unlink()

    assert cache.lookup(key) is None


def test_an_entry_with_corrupt_metadata_is_rejected(
    project: ProjectBundle, key_inputs: dict[str, object], tmp_path: Path
) -> None:
    cache = SceneCache(project.root, FINAL_PROFILE)
    key = _store_entry(cache, key_inputs, tmp_path)
    cache.metadata_path_for(key).write_text("{not json", encoding="utf-8")

    assert cache.lookup(key) is None
    assert not cache.path_for(key).exists()


def test_an_entry_with_a_mismatched_digest_is_rejected(
    project: ProjectBundle, key_inputs: dict[str, object], tmp_path: Path
) -> None:
    cache = SceneCache(project.root, FINAL_PROFILE)
    key = _store_entry(cache, key_inputs, tmp_path)

    metadata_path = cache.metadata_path_for(key)
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    payload["digest"] = "0" * 64
    metadata_path.write_text(json.dumps(payload), encoding="utf-8")

    assert cache.lookup(key) is None


# --- profile isolation and clearing -------------------------------------------


def test_preview_and_final_caches_are_separate(project: ProjectBundle) -> None:
    preview = SceneCache(project.root, PREVIEW_PROFILE)
    final = SceneCache(project.root, FINAL_PROFILE)
    assert preview.root != final.root
    assert preview.root.name == "preview"
    assert final.root.name == "final"


def test_assembly_and_scene_caches_are_separate(project: ProjectBundle) -> None:
    assert (
        AssemblyCache(project.root, FINAL_PROFILE).root
        != SceneCache(project.root, FINAL_PROFILE).root
    )


def test_clearing_one_profile_leaves_the_other(
    project: ProjectBundle, key_inputs: dict[str, object], tmp_path: Path
) -> None:
    preview = SceneCache(project.root, PREVIEW_PROFILE)
    final = SceneCache(project.root, FINAL_PROFILE)
    _store_entry(preview, key_inputs, tmp_path)
    _store_entry(final, key_inputs, tmp_path)

    assert preview.clear() > 0
    assert preview.entries() == []
    assert len(final.entries()) == 1


def test_clearing_the_project_cache_never_touches_source_media(
    project: ProjectBundle, key_inputs: dict[str, object], tmp_path: Path, png_file: Path
) -> None:
    from polymation.media.importer import import_asset

    imported = import_asset(project, png_file)
    project.save()
    cache = SceneCache(project.root, FINAL_PROFILE)
    _store_entry(cache, key_inputs, tmp_path)

    removed = clear_project_cache(project.root)

    assert removed > 0
    assert not (project.root / CACHE_DIRNAME).exists()
    # The imported media and the project documents survive.
    assert imported.destination.is_file()
    assert (project.root / "manifest.json").is_file()
    assert (project.root / "assets.json").is_file()


def test_clearing_an_absent_cache_is_harmless(project: ProjectBundle) -> None:
    assert clear_project_cache(project.root) == 0


def test_total_bytes_sums_entries(
    project: ProjectBundle, key_inputs: dict[str, object], tmp_path: Path
) -> None:
    cache = SceneCache(project.root, FINAL_PROFILE)
    assert cache.total_bytes() == 0
    _store_entry(cache, key_inputs, tmp_path)
    assert cache.total_bytes() == 4096


# --- assembly key -------------------------------------------------------------


def test_the_assembly_key_depends_on_scene_digests(key_inputs: dict[str, object]) -> None:
    timeline = key_inputs["timeline"]
    assert isinstance(timeline, CompositionTimeline)

    first = compute_assembly_key(
        ["aaa", "bbb"], timeline, FINAL_PROFILE, caption_bytes=None, ffmpeg_version="7.1"
    )
    second = compute_assembly_key(
        ["aaa", "ccc"], timeline, FINAL_PROFILE, caption_bytes=None, ffmpeg_version="7.1"
    )
    assert first.digest != second.digest


def test_the_assembly_key_depends_on_caption_content(
    key_inputs: dict[str, object],
) -> None:
    timeline = key_inputs["timeline"]
    assert isinstance(timeline, CompositionTimeline)

    first = compute_assembly_key(
        ["aaa"], timeline, FINAL_PROFILE, caption_bytes=b"one", ffmpeg_version="7.1"
    )
    second = compute_assembly_key(
        ["aaa"], timeline, FINAL_PROFILE, caption_bytes=b"two", ffmpeg_version="7.1"
    )
    assert first.digest != second.digest


def test_the_assembly_key_depends_on_the_audio_mix(key_inputs: dict[str, object]) -> None:
    from polymation.schemas.timeline import AudioMix, Ducking

    timeline = key_inputs["timeline"]
    assert isinstance(timeline, CompositionTimeline)
    quieter = timeline.model_copy(update={"audio": AudioMix(ducking=Ducking(attenuation_db=-20.0))})

    first = compute_assembly_key(
        ["aaa"], timeline, FINAL_PROFILE, caption_bytes=None, ffmpeg_version="7.1"
    )
    second = compute_assembly_key(
        ["aaa"], quieter, FINAL_PROFILE, caption_bytes=None, ffmpeg_version="7.1"
    )
    assert first.digest != second.digest


def test_identical_assembly_inputs_give_one_key(key_inputs: dict[str, object]) -> None:
    timeline = key_inputs["timeline"]
    assert isinstance(timeline, CompositionTimeline)
    arguments = {
        "timeline": timeline,
        "profile": FINAL_PROFILE,
        "caption_bytes": b"x",
        "ffmpeg_version": "7.1",
    }
    assert (
        compute_assembly_key(["a", "b"], **arguments).digest  # type: ignore[arg-type]
        == compute_assembly_key(["a", "b"], **arguments).digest  # type: ignore[arg-type]
    )
