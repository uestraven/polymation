"""Timeline compilation and validation against a project's real assets."""

from __future__ import annotations

import wave
from pathlib import Path

import pytest
from PIL import Image

from polymation.media.importer import import_asset
from polymation.projects.store import ProjectBundle, ProjectStore
from polymation.rendering.profiles import FINAL_PROFILE, PREVIEW_PROFILE
from polymation.schemas.assets import AssetCategory
from polymation.schemas.base import utc_now
from polymation.schemas.rights import RightsBasis
from polymation.schemas.script import NarrationScene
from polymation.schemas.storyboard import FitMode, StoryboardScene, VisualShot
from polymation.schemas.timeline import (
    AudioMix,
    AudioRole,
    AudioSegment,
    CaptionSettings,
    CompositionShot,
    CompositionTimeline,
    Motion,
    MotionKind,
    Overlay,
    OverlayKind,
    ShotFitMode,
    SourceTrim,
    TimelineScene,
    Transition,
    TransitionKind,
)
from polymation.schemas.timing import FrameSpan, SampleSpan
from polymation.timeline.compile import CompilationError, compile_timeline
from polymation.timeline.validate import validate_timeline

NOW = utc_now()


def codes(findings: list) -> set[str]:
    return {finding.code for finding in findings}


@pytest.fixture
def still(tmp_path: Path) -> Path:
    """An original 4:3 still."""
    path = tmp_path / "sources" / "still-4x3.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (640, 480), (25, 29, 56)).save(path)
    return path


@pytest.fixture
def wide_still(tmp_path: Path) -> Path:
    """An original 16:9 still, large enough not to need upscaling."""
    path = tmp_path / "sources" / "still-wide.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (3840, 2160), (40, 40, 40)).save(path)
    return path


@pytest.fixture
def short_wav(tmp_path: Path) -> Path:
    """One second of silence."""
    path = tmp_path / "sources" / "one-second.wav"
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(48_000)
        handle.writeframes(b"\x00\x00\x00\x00" * 48_000)
    return path


def import_all(project: ProjectBundle, *paths: Path) -> list[str]:
    """Import each path and return the asset ids, in order."""
    ids: list[str] = []
    for path in paths:
        category = AssetCategory.MUSIC if path.suffix == ".wav" else AssetCategory.GRAPHICS
        result = import_asset(
            project,
            path,
            category=category,
            rights_basis=RightsBasis.ORIGINAL_WORK,
            rights_holder="Polymation Studio",
            intended_editorial_use="test",
        )
        ids.append(result.asset.asset_id)
    project.save()
    return ids


def storyboard_from(project: ProjectBundle, asset_ids: list[str], seconds: float) -> None:
    """Give the project one storyboard scene per asset."""
    project.script.scenes = [
        NarrationScene(scene_id=f"scene-{index + 1:02d}", order=index, narration="x")
        for index in range(len(asset_ids))
    ]
    project.storyboard.scenes = [
        StoryboardScene(
            scene_id=f"scene-{index + 1:02d}",
            order=index,
            shots=[
                VisualShot(
                    shot_id=f"shot-{index + 1:02d}",
                    scene_id=f"scene-{index + 1:02d}",
                    asset_id=asset_id,
                    description="x",
                    duration_seconds=seconds,
                    fit_mode=FitMode.CONTAIN,
                )
            ],
        )
        for index, asset_id in enumerate(asset_ids)
    ]
    project.save()


# --- compilation --------------------------------------------------------------


def test_compiling_an_empty_storyboard_is_refused(project: ProjectBundle) -> None:
    with pytest.raises(CompilationError, match="empty storyboard"):
        compile_timeline(project, FINAL_PROFILE)


def test_compilation_projects_durations_onto_the_frame_grid(
    project: ProjectBundle, still: Path
) -> None:
    ids = import_all(project, still)
    storyboard_from(project, ids, 5.0)

    result = compile_timeline(project, FINAL_PROFILE)

    assert result.timeline.total_frames == 150
    assert result.timeline.duration_seconds == pytest.approx(5.0)
    assert not result.had_rounding


def test_compilation_reports_rounding(project: ProjectBundle, still: Path) -> None:
    ids = import_all(project, still)
    # 1/24s is 1.25 frames at 30fps.
    storyboard_from(project, ids, 1 / 24)

    result = compile_timeline(project, FINAL_PROFILE)

    assert result.had_rounding
    assert len(result.rounding) == 1
    assert result.rounding[0].resolved == 1
    assert result.timeline.rounding_notes


def test_a_duration_that_rounds_to_zero_frames_is_refused(
    project: ProjectBundle, still: Path
) -> None:
    """A shot must occupy at least one frame on the output grid.

    The storyboard schema already enforces a one-frame floor at 30 fps, so this
    is only reachable at a lower profile frame rate — which is exactly when a
    duration can round away to nothing.
    """
    import dataclasses

    slow = dataclasses.replace(FINAL_PROFILE, fps=10)
    ids = import_all(project, still)
    # 1/30s is the storyboard minimum; at 10 fps that is 0.33 frames.
    storyboard_from(project, ids, 1 / 30)

    with pytest.raises(CompilationError, match="at least one frame"):
        compile_timeline(project, slow)


def test_a_shot_without_an_asset_is_refused(project: ProjectBundle) -> None:
    project.script.scenes = [NarrationScene(scene_id="scene-01", order=0, narration="x")]
    project.storyboard.scenes = [
        StoryboardScene(
            scene_id="scene-01",
            order=0,
            shots=[
                VisualShot(
                    shot_id="shot-01",
                    scene_id="scene-01",
                    description="no asset",
                    duration_seconds=2.0,
                )
            ],
        )
    ]
    project.save()

    with pytest.raises(CompilationError, match="no asset assigned"):
        compile_timeline(project, FINAL_PROFILE)


def test_a_dangling_asset_reference_is_refused(project: ProjectBundle) -> None:
    project.script.scenes = [NarrationScene(scene_id="scene-01", order=0, narration="x")]
    project.storyboard.scenes = [
        StoryboardScene(
            scene_id="scene-01",
            order=0,
            shots=[
                VisualShot(
                    shot_id="shot-01",
                    scene_id="scene-01",
                    asset_id="asset-9999",
                    description="x",
                    duration_seconds=2.0,
                )
            ],
        )
    ]
    project.save()

    with pytest.raises(CompilationError, match="not in this project's asset collection"):
        compile_timeline(project, FINAL_PROFILE)


def test_an_audio_asset_in_a_visual_shot_is_refused(
    project: ProjectBundle, short_wav: Path
) -> None:
    ids = import_all(project, short_wav)
    storyboard_from(project, ids, 1.0)

    with pytest.raises(CompilationError, match="place audio on the audio mix"):
        compile_timeline(project, FINAL_PROFILE)


def test_narration_longer_than_its_scene_is_refused(
    project: ProjectBundle, still: Path, short_wav: Path
) -> None:
    """Narration is never trimmed to fit visuals."""
    still_id, wav_id = import_all(project, still, short_wav)
    storyboard_from(project, [still_id], 0.5)  # scene shorter than the 1s narration

    with pytest.raises(CompilationError, match="never trimmed to fit visuals"):
        compile_timeline(project, FINAL_PROFILE, narration_asset_ids=[wav_id])


def test_narration_shorter_than_its_scene_is_noted_not_padded(
    project: ProjectBundle, still: Path, short_wav: Path
) -> None:
    still_id, wav_id = import_all(project, still, short_wav)
    storyboard_from(project, [still_id], 4.0)

    result = compile_timeline(project, FINAL_PROFILE, narration_asset_ids=[wav_id])

    assert any("visual time after its narration ends" in note for note in result.notes)
    narration = result.timeline.audio.segments_for(AudioRole.NARRATION)
    assert len(narration) == 1
    assert narration[0].span.duration_samples == 48_000


def test_narration_count_must_match_the_scene_count(
    project: ProjectBundle, still: Path, short_wav: Path
) -> None:
    still_id, wav_id = import_all(project, still, short_wav)
    storyboard_from(project, [still_id], 4.0)

    with pytest.raises(CompilationError, match="the counts must match"):
        compile_timeline(project, FINAL_PROFILE, narration_asset_ids=[wav_id, wav_id])


def test_music_shorter_than_the_video_is_noted_not_looped(
    project: ProjectBundle, still: Path, short_wav: Path
) -> None:
    still_id, wav_id = import_all(project, still, short_wav)
    storyboard_from(project, [still_id], 5.0)

    result = compile_timeline(project, FINAL_PROFILE, music_asset_id=wav_id)

    assert any("not looped automatically" in note for note in result.notes)
    music = result.timeline.audio.segments_for(AudioRole.MUSIC)
    assert music[0].span.duration_samples == 48_000


def test_a_silent_compilation_says_so(project: ProjectBundle, still: Path) -> None:
    ids = import_all(project, still)
    storyboard_from(project, ids, 2.0)

    result = compile_timeline(project, FINAL_PROFILE)

    assert result.timeline.audio.segments == []
    assert any("render will be silent" in note for note in result.notes)


def test_compilation_does_not_modify_the_storyboard(project: ProjectBundle, still: Path) -> None:
    """Compilation is a one-way projection."""
    ids = import_all(project, still)
    storyboard_from(project, ids, 3.0)
    before = project.storyboard.model_dump(mode="json")

    compile_timeline(project, FINAL_PROFILE)

    assert project.storyboard.model_dump(mode="json") == before


def test_the_compiled_timeline_adopts_the_profile_geometry(
    project: ProjectBundle, still: Path
) -> None:
    ids = import_all(project, still)
    storyboard_from(project, ids, 2.0)

    preview = compile_timeline(project, PREVIEW_PROFILE).timeline
    assert (preview.width, preview.height) == PREVIEW_PROFILE.resolution
    assert preview.profile_name == "preview"


def test_a_compiled_timeline_round_trips_through_the_store(
    store: ProjectStore, project: ProjectBundle, still: Path
) -> None:
    ids = import_all(project, still)
    storyboard_from(project, ids, 2.0)
    result = compile_timeline(project, FINAL_PROFILE)

    project.timeline = result.timeline
    project.save()

    reloaded = store.load(project.project_id)
    assert reloaded.timeline is not None
    assert reloaded.timeline.total_frames == result.timeline.total_frames


def test_the_compilation_result_serialises(project: ProjectBundle, still: Path) -> None:
    import json

    ids = import_all(project, still)
    storyboard_from(project, ids, 2.0)
    payload = compile_timeline(project, FINAL_PROFILE).to_dict()
    json.dumps(payload)
    assert payload["total_frames"] == 60


# --- M0.1 backward compatibility ---------------------------------------------


def test_a_project_without_a_timeline_still_loads(
    store: ProjectStore, project: ProjectBundle
) -> None:
    """Every project M0.1 created is in this state."""
    assert not (project.root / "timeline.json").exists()
    reloaded = store.load(project.project_id)
    assert reloaded.timeline is None


def test_an_m01_manifest_without_the_new_path_fields_loads(
    store: ProjectStore, project: ProjectBundle
) -> None:
    """ManifestPaths gained fields in M0.2; every field has a default."""
    import json

    manifest_path = project.root / "manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    # Reduce `paths` to exactly the M0.1 key set.
    payload["paths"] = {
        key: value
        for key, value in payload["paths"].items()
        if key not in {"timeline", "cache_dir"}
    }
    manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    reloaded = store.load(project.project_id)

    assert reloaded.manifest.paths.timeline == "timeline.json"
    assert reloaded.manifest.paths.cache_dir == ".cache"


def test_an_m01_project_can_be_compiled_and_rendered_into(
    store: ProjectStore, project: ProjectBundle, still: Path
) -> None:
    """The upgrade path: an M0.1 project gains a timeline without migration."""
    ids = import_all(project, still)
    storyboard_from(project, ids, 2.0)

    result = compile_timeline(project, FINAL_PROFILE)
    project.timeline = result.timeline
    project.save()

    reloaded = store.load(project.project_id)
    report = validate_timeline(reloaded, reloaded.timeline, FINAL_PROFILE)  # type: ignore[arg-type]
    assert report.structurally_valid, [item.message for item in report.errors]


# --- validation ---------------------------------------------------------------


def a_timeline(
    project: ProjectBundle, scenes: list[TimelineScene], **kw: object
) -> CompositionTimeline:
    payload: dict[str, object] = {
        "project_id": project.project_id,
        "scenes": scenes,
        "created_at": NOW,
        "updated_at": NOW,
    }
    payload.update(kw)
    return CompositionTimeline(**payload)  # type: ignore[arg-type]


def one_scene(asset_id: str, **shot_kw: object) -> TimelineScene:
    payload: dict[str, object] = {
        "shot_id": "shot-01",
        "asset_id": asset_id,
        "span": FrameSpan(start_frame=0, duration_frames=60),
    }
    payload.update(shot_kw)
    return TimelineScene(
        scene_id="scene-01",
        order=0,
        shots=[CompositionShot(**payload)],  # type: ignore[arg-type]
    )


def test_a_valid_timeline_passes(project: ProjectBundle, still: Path) -> None:
    ids = import_all(project, still)
    project.script.scenes = [NarrationScene(scene_id="scene-01", order=0, narration="x")]
    project.storyboard.scenes = [StoryboardScene(scene_id="scene-01", order=0, shots=[])]
    project.save()

    report = validate_timeline(project, a_timeline(project, [one_scene(ids[0])]), FINAL_PROFILE)
    assert report.structurally_valid, [item.message for item in report.errors]


def test_a_timeline_from_another_project_is_rejected(project: ProjectBundle, still: Path) -> None:
    ids = import_all(project, still)
    foreign = a_timeline(project, [one_scene(ids[0])]).model_copy(
        update={"project_id": "20260101-other-project-bbb222"}
    )
    report = validate_timeline(project, foreign, FINAL_PROFILE)
    assert "timeline_project_mismatch" in codes(report.errors)


def test_a_dangling_asset_reference_is_an_error(project: ProjectBundle) -> None:
    report = validate_timeline(
        project, a_timeline(project, [one_scene("asset-9999")]), FINAL_PROFILE
    )
    assert "invalid_asset_reference" in codes(report.errors)


def test_a_missing_asset_file_is_an_error(project: ProjectBundle, still: Path) -> None:
    ids = import_all(project, still)
    project.asset_path(project.assets.assets[0].path).unlink()

    report = validate_timeline(project, a_timeline(project, [one_scene(ids[0])]), FINAL_PROFILE)
    assert "missing_asset_file" in codes(report.errors)


def test_a_modified_asset_is_detected_when_checksums_are_verified(
    project: ProjectBundle, still: Path
) -> None:
    ids = import_all(project, still)
    on_disk = project.asset_path(project.assets.assets[0].path)
    data = bytearray(on_disk.read_bytes())
    data[-1] ^= 0xFF
    on_disk.write_bytes(bytes(data))

    timeline = a_timeline(project, [one_scene(ids[0])])
    assert "asset_checksum_mismatch" in codes(
        validate_timeline(project, timeline, FINAL_PROFILE, verify_checksums=True).errors
    )
    assert "asset_checksum_mismatch" not in codes(
        validate_timeline(project, timeline, FINAL_PROFILE, verify_checksums=False).errors
    )


def test_trimming_a_still_image_is_an_error(project: ProjectBundle, still: Path) -> None:
    ids = import_all(project, still)
    scene = one_scene(ids[0], source_trim=SourceTrim(start_frame=30))
    report = validate_timeline(project, a_timeline(project, [scene]), FINAL_PROFILE)
    assert "trim_on_still_image" in codes(report.errors)


def test_requesting_source_audio_from_a_still_is_an_error(
    project: ProjectBundle, still: Path
) -> None:
    ids = import_all(project, still)
    scene = one_scene(ids[0], include_source_audio=True)
    report = validate_timeline(project, a_timeline(project, [scene]), FINAL_PROFILE)
    assert "source_audio_from_image" in codes(report.errors)


def test_heavy_upscaling_is_a_warning(project: ProjectBundle, still: Path) -> None:
    """A 640x480 source filling 1920x1080 is a 3x magnification."""
    ids = import_all(project, still)
    scene = one_scene(ids[0], fit_mode=ShotFitMode.COVER)
    report = validate_timeline(project, a_timeline(project, [scene]), FINAL_PROFILE)
    assert "heavy_upscale" in codes(report.warnings)


def test_a_large_source_does_not_warn_about_upscaling(
    project: ProjectBundle, wide_still: Path
) -> None:
    ids = import_all(project, wide_still)
    scene = one_scene(ids[0], fit_mode=ShotFitMode.COVER)
    report = validate_timeline(project, a_timeline(project, [scene]), FINAL_PROFILE)
    assert "heavy_upscale" not in codes(report.warnings)


def test_a_profile_fps_mismatch_is_an_error(project: ProjectBundle, still: Path) -> None:
    ids = import_all(project, still)
    timeline = a_timeline(project, [one_scene(ids[0])], fps=25, audio_sample_rate=50_000)
    report = validate_timeline(project, timeline, FINAL_PROFILE)
    assert "timeline_fps_mismatch" in codes(report.errors)


def test_a_different_resolution_at_the_same_aspect_is_accepted(
    project: ProjectBundle, still: Path
) -> None:
    """That is exactly the preview/final relationship."""
    ids = import_all(project, still)
    timeline = a_timeline(project, [one_scene(ids[0])], width=1920, height=1080)
    report = validate_timeline(project, timeline, PREVIEW_PROFILE)
    assert "timeline_aspect_mismatch" not in codes(report.errors)


def test_a_different_aspect_ratio_is_an_error(project: ProjectBundle, still: Path) -> None:
    ids = import_all(project, still)
    timeline = a_timeline(project, [one_scene(ids[0])], width=1080, height=1920)
    report = validate_timeline(project, timeline, FINAL_PROFILE)
    assert "timeline_aspect_mismatch" in codes(report.errors)


def test_an_empty_timeline_is_an_error(project: ProjectBundle) -> None:
    report = validate_timeline(project, a_timeline(project, []), FINAL_PROFILE)
    assert "timeline_empty" in codes(report.errors)


def test_an_overlay_referencing_a_missing_asset_is_an_error(
    project: ProjectBundle, still: Path
) -> None:
    ids = import_all(project, still)
    scene = TimelineScene(
        scene_id="scene-01",
        order=0,
        shots=[
            CompositionShot(
                shot_id="shot-01",
                asset_id=ids[0],
                span=FrameSpan(start_frame=0, duration_frames=60),
            )
        ],
        overlays=[
            Overlay(
                overlay_id="o1",
                kind=OverlayKind.IMAGE,
                span=FrameSpan(start_frame=0, duration_frames=30),
                image_asset_id="asset-9999",
                width_fraction=0.2,
            )
        ],
    )
    report = validate_timeline(project, a_timeline(project, [scene]), FINAL_PROFILE)
    assert "overlay_asset_missing" in codes(report.errors)


def test_a_timeline_scene_absent_from_the_storyboard_is_a_warning(
    project: ProjectBundle, still: Path
) -> None:
    ids = import_all(project, still)
    project.storyboard.scenes = [StoryboardScene(scene_id="scene-99", order=0, shots=[])]
    project.script.scenes = [NarrationScene(scene_id="scene-99", order=0, narration="x")]
    project.save()

    report = validate_timeline(project, a_timeline(project, [one_scene(ids[0])]), FINAL_PROFILE)
    assert "timeline_scene_not_in_storyboard" in codes(report.warnings)


def test_audio_longer_than_its_source_is_an_error(
    project: ProjectBundle, still: Path, short_wav: Path
) -> None:
    still_id, wav_id = import_all(project, still, short_wav)
    scene = one_scene(still_id)
    timeline = a_timeline(
        project,
        [scene],
        audio=AudioMix(
            segments=[
                AudioSegment(
                    segment_id="m1",
                    role=AudioRole.MUSIC,
                    asset_id=wav_id,
                    span=SampleSpan(start_sample=0, duration_samples=96_000),
                )
            ]
        ),
    )
    report = validate_timeline(project, timeline, FINAL_PROFILE)
    assert "audio_source_too_short" in codes(report.errors)


def test_silence_padding_turns_that_error_into_a_warning(
    project: ProjectBundle, still: Path, short_wav: Path
) -> None:
    still_id, wav_id = import_all(project, still, short_wav)
    timeline = a_timeline(
        project,
        [one_scene(still_id)],
        audio=AudioMix(
            segments=[
                AudioSegment(
                    segment_id="m1",
                    role=AudioRole.MUSIC,
                    asset_id=wav_id,
                    span=SampleSpan(start_sample=0, duration_samples=96_000),
                    allow_silence_padding=True,
                )
            ]
        ),
    )
    report = validate_timeline(project, timeline, FINAL_PROFILE)
    assert "audio_source_too_short" not in codes(report.errors)
    assert "audio_padded_with_silence" in codes(report.warnings)


def test_ducking_without_narration_is_a_warning(
    project: ProjectBundle, still: Path, short_wav: Path
) -> None:
    still_id, wav_id = import_all(project, still, short_wav)
    timeline = a_timeline(
        project,
        [one_scene(still_id)],
        audio=AudioMix(
            segments=[
                AudioSegment(
                    segment_id="m1",
                    role=AudioRole.MUSIC,
                    asset_id=wav_id,
                    span=SampleSpan(start_sample=0, duration_samples=48_000),
                )
            ]
        ),
    )
    report = validate_timeline(project, timeline, FINAL_PROFILE)
    assert "ducking_without_narration" in codes(report.warnings)


def test_caption_burn_in_without_a_source_is_an_error(project: ProjectBundle, still: Path) -> None:
    ids = import_all(project, still)
    timeline = a_timeline(project, [one_scene(ids[0])], captions=CaptionSettings(burn_in=True))
    report = validate_timeline(project, timeline, FINAL_PROFILE)
    assert "captions_burn_in_without_source" in codes(report.errors)


def test_a_missing_caption_file_is_an_error(project: ProjectBundle, still: Path) -> None:
    ids = import_all(project, still)
    timeline = a_timeline(
        project,
        [one_scene(ids[0])],
        captions=CaptionSettings(srt_path="captions/absent.srt"),
    )
    report = validate_timeline(project, timeline, FINAL_PROFILE)
    assert "missing_caption_file" in codes(report.errors)


def test_an_unsafe_caption_path_is_an_error(project: ProjectBundle, still: Path) -> None:
    ids = import_all(project, still)
    timeline = a_timeline(
        project,
        [one_scene(ids[0])],
        captions=CaptionSettings(srt_path="../../../etc/passwd"),
    )
    report = validate_timeline(project, timeline, FINAL_PROFILE)
    assert "unsafe_caption_path" in codes(report.errors)


def test_a_malformed_caption_file_is_an_error(project: ProjectBundle, still: Path) -> None:
    ids = import_all(project, still)
    captions = project.root / "captions" / "bad.srt"
    captions.parent.mkdir(parents=True, exist_ok=True)
    captions.write_text("this is not srt at all\n", encoding="utf-8")

    timeline = a_timeline(
        project, [one_scene(ids[0])], captions=CaptionSettings(srt_path="captions/bad.srt")
    )
    report = validate_timeline(project, timeline, FINAL_PROFILE)
    assert "caption_parse_failed" in codes(report.errors)


def test_captions_past_the_video_end_are_an_error(project: ProjectBundle, still: Path) -> None:
    ids = import_all(project, still)
    captions = project.root / "captions" / "long.srt"
    captions.parent.mkdir(parents=True, exist_ok=True)
    # The scene is 60 frames = 2s; this cue ends at 10s.
    captions.write_text("1\n00:00:08,000 --> 00:00:10,000\ntoo late\n", encoding="utf-8")

    timeline = a_timeline(
        project, [one_scene(ids[0])], captions=CaptionSettings(srt_path="captions/long.srt")
    )
    report = validate_timeline(project, timeline, FINAL_PROFILE)
    assert "caption_exceeds_video_duration" in codes(report.errors)


def test_a_cross_scene_crossfade_is_rejected_by_validation(
    project: ProjectBundle, still: Path
) -> None:
    """Defence in depth: the schema rejects this, and so does the validator.

    Both layers are checked because a timeline can also arrive from a
    hand-edited file, and `model_construct` is the only way to reach the
    validator with a document the schema would have refused.
    """
    ids = import_all(project, still)
    valid = a_timeline(project, [one_scene(ids[0])])

    bad_scene = valid.scenes[0].model_construct(
        **{
            **valid.scenes[0].__dict__,
            "transition_out": Transition.model_construct(
                kind=TransitionKind.CROSSFADE, duration_frames=10
            ),
        }
    )
    bad_timeline = valid.model_construct(**{**valid.__dict__, "scenes": [bad_scene]})

    report = validate_timeline(project, bad_timeline, FINAL_PROFILE)
    assert "cross_scene_crossfade" in codes(report.errors)


def test_motion_on_a_still_is_accepted(project: ProjectBundle, wide_still: Path) -> None:
    ids = import_all(project, wide_still)
    scene = one_scene(ids[0], motion=Motion(kind=MotionKind.ZOOM_IN, start_zoom=1.0, end_zoom=1.3))
    report = validate_timeline(project, a_timeline(project, [scene]), FINAL_PROFILE)
    assert "motion_on_video" not in codes(report.errors)
