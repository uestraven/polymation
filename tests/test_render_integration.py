"""Real-FFmpeg integration tests for the composition engine.

Every test here executes actual FFmpeg and ffprobe commands and inspects the
resulting pixels and samples. Nothing is mocked: the point is to catch the
things only a real encoder reveals — a filter graph FFmpeg rejects, a frame
count that drifts, an aspect ratio that stretches, audio that lands in the
wrong place.

These are the slowest tests in the suite, so the fixtures are deliberately
short. A skip here means the composition engine went unverified.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from polymation.config.channel import ChannelConfig
from polymation.errors import NotRenderReadyError, RenderError
from polymation.media.importer import import_asset
from polymation.projects.locking import ProjectLock, ProjectLockedError
from polymation.projects.store import ProjectBundle, ProjectStore
from polymation.rendering.audio import compute_duck_windows
from polymation.rendering.cache import SceneCache, compute_scene_key
from polymation.rendering.compose import inspect_output
from polymation.rendering.engine import render
from polymation.rendering.ffmpeg import find_ffmpeg, require_ffmpeg, run_tool, toolchain_available
from polymation.rendering.profiles import FINAL_PROFILE, PREVIEW_PROFILE
from polymation.rendering.synth import (
    audio_envelope,
    detect_marker,
    generate_marker_audio,
    generate_synthetic_video,
)
from polymation.schemas.assets import AssetCategory
from polymation.schemas.base import utc_now
from polymation.schemas.rights import RightsBasis
from polymation.schemas.script import NarrationScene
from polymation.schemas.timeline import (
    Anchor,
    AudioMix,
    AudioRole,
    AudioSegment,
    CaptionSettings,
    CompositionShot,
    CompositionTimeline,
    CropRect,
    Ducking,
    LoudnessTarget,
    Motion,
    MotionKind,
    Overlay,
    OverlayKind,
    ShortSourcePolicy,
    ShotFitMode,
    TextStyle,
    TimelineScene,
    Transition,
    TransitionKind,
)
from polymation.schemas.timing import FrameSpan, SampleSpan

#: Skip marker for tests that execute real FFmpeg commands. A skip is an honest
#: report that rendering went unverified — it is never a pass.
ffmpeg_required = pytest.mark.skipif(
    not toolchain_available(),
    reason=(
        "FFmpeg and ffprobe are not installed. M0.2 rendering acceptance is NOT "
        "satisfied when this test is skipped."
    ),
)

pytestmark = [ffmpeg_required, pytest.mark.ffmpeg]

NOW = utc_now()


# --- helpers ------------------------------------------------------------------


def palette_of(channel: ChannelConfig) -> tuple[tuple[int, int, int], ...]:
    return tuple(
        channel.brand.palette.as_rgb(name)
        for name in ("deep_indigo", "crt_amber", "parchment_cream", "vermilion")
    )


def extract_frame(video: Path, seconds: float, destination: Path) -> Image.Image:
    """Decode one frame at ``seconds`` and return it."""
    ffmpeg = require_ffmpeg()
    assert ffmpeg.path is not None
    result = run_tool(
        ffmpeg.path,
        [
            "-nostdin",
            "-hide_banner",
            "-v",
            "error",
            "-y",
            "-ss",
            f"{seconds:.6f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            str(destination),
        ],
    )
    assert result.ok, result.tail()
    return Image.open(destination).convert("RGB")


def extract_audio(video: Path, destination: Path, *, mono: bool = True) -> Path:
    """Decode a video's audio to 16-bit PCM WAV."""
    ffmpeg = require_ffmpeg()
    assert ffmpeg.path is not None
    result = run_tool(
        ffmpeg.path,
        [
            "-nostdin",
            "-hide_banner",
            "-v",
            "error",
            "-y",
            "-i",
            str(video),
            "-map",
            "0:a:0",
            "-c:a",
            "pcm_s16le",
            "-ar",
            "48000",
            "-ac",
            "1" if mono else "2",
            str(destination),
        ],
    )
    assert result.ok, result.tail()
    return destination


def probe_frame_count(video: Path) -> int:
    """Count decoded frames, which is stricter than reading a container tag."""
    from polymation.rendering.ffmpeg import ffprobe_json

    payload = ffprobe_json(video, extra_arguments=["-count_frames", "-select_streams", "v:0"])
    stream = payload["streams"][0]
    for key in ("nb_read_frames", "nb_frames"):
        value = stream.get(key)
        if value not in (None, "N/A"):
            return int(value)
    raise AssertionError("ffprobe reported no frame count")


def import_as(project: ProjectBundle, path: Path, category: AssetCategory) -> str:
    result = import_asset(
        project,
        path,
        category=category,
        rights_basis=RightsBasis.ORIGINAL_WORK,
        rights_holder="Polymation Studio",
        intended_editorial_use="integration test",
    )
    return result.asset.asset_id


def build_timeline(
    project: ProjectBundle, scenes: list[TimelineScene], **kw: object
) -> CompositionTimeline:
    payload: dict[str, object] = {
        "project_id": project.project_id,
        "scenes": scenes,
        "created_at": NOW,
        "updated_at": NOW,
    }
    payload.update(kw)
    timeline = CompositionTimeline(**payload)  # type: ignore[arg-type]
    project.script.scenes = [
        NarrationScene(scene_id=scene.scene_id, order=scene.order, narration="x")
        for scene in timeline.scenes
    ]
    project.timeline = timeline
    project.save()
    return timeline


@pytest.fixture
def sources(tmp_path: Path, channel: ChannelConfig) -> dict[str, Path]:
    """Original synthetic sources covering the required combinations."""
    directory = tmp_path / "sources"
    directory.mkdir(parents=True, exist_ok=True)
    palette = palette_of(channel)

    # A 4:3 still that is entirely white, so pad regions are unambiguous.
    still_43 = directory / "still-4x3.png"
    Image.new("RGB", (640, 480), (255, 255, 255)).save(still_43)

    # A large 16:9 still, so no upscaling is needed.
    still_wide = directory / "still-wide.png"
    Image.new("RGB", (3840, 2160), (200, 200, 200)).save(still_wide)

    return {
        "still_43": still_43,
        "still_wide": still_wide,
        # 4:3 at 24 fps, no audio.
        "video_43": generate_synthetic_video(
            directory / "video-4x3-24.mp4",
            width=640,
            height=480,
            fps=24,
            frames=48,
            palette=palette,
            with_audio=False,
            marker_frames=(0,),
        ).path,
        # Widescreen at 25 fps, with audio.
        "video_wide": generate_synthetic_video(
            directory / "video-wide-25.mp4",
            width=1280,
            height=720,
            fps=25,
            frames=50,
            palette=palette,
            with_audio=True,
            marker_frames=(0,),
        ).path,
        # Low resolution at 15 fps, short.
        "video_small": generate_synthetic_video(
            directory / "video-small-15.mp4",
            width=480,
            height=360,
            fps=15,
            frames=15,
            palette=palette,
            with_audio=False,
        ).path,
        "narration": generate_marker_audio(
            directory / "narration.wav",
            duration_seconds=2.0,
            marker_samples=(0,),
            base_amplitude=0.25,
        ),
        "music": generate_marker_audio(
            directory / "music.wav",
            duration_seconds=4.0,
            base_frequency=110.0,
            base_amplitude=0.2,
        ),
    }


# --- synthetic fixture generation --------------------------------------------


def test_synthetic_video_has_the_requested_properties(sources: dict[str, Path]) -> None:
    inspection = inspect_output(sources["video_43"], require_audio=False, profile=FINAL_PROFILE)
    assert (inspection.width, inspection.height) == (640, 480)
    assert inspection.frame_rate == pytest.approx(24.0, abs=0.01)
    # Not 16:9, which is what makes it a useful aspect-ratio test.
    assert inspection.width / inspection.height == pytest.approx(4 / 3)


def test_synthetic_video_with_audio_has_an_audio_stream(sources: dict[str, Path]) -> None:
    from polymation.media.inspect import inspect_media

    with_audio = inspect_media(sources["video_wide"])
    without = inspect_media(sources["video_43"])
    assert with_audio.audio_codec is not None
    assert without.audio_codec is None


def test_marker_audio_clicks_are_detectable(sources: dict[str, Path]) -> None:
    envelope = audio_envelope(sources["narration"], window_ms=20)
    # The click is at sample 0, so the first window is far louder than the bed.
    assert envelope[0] > 0.3
    assert max(envelope[20:]) < 0.3


# --- aspect ratio preservation -----------------------------------------------


def test_a_four_by_three_still_is_pillarboxed_not_stretched(
    project: ProjectBundle,
    channel: ChannelConfig,
    sources: dict[str, Path],
    tmp_path: Path,
) -> None:
    """4:3 content in a 16:9 frame occupies exactly x in [240, 1680)."""
    asset_id = import_as(project, sources["still_43"], AssetCategory.GRAPHICS)
    project.save()

    build_timeline(
        project,
        [
            TimelineScene(
                scene_id="scene-01",
                order=0,
                shots=[
                    CompositionShot(
                        shot_id="shot-01",
                        asset_id=asset_id,
                        span=FrameSpan(start_frame=0, duration_frames=12),
                        fit_mode=ShotFitMode.CONTAIN,
                        background_color="#000000",
                    )
                ],
            )
        ],
    )

    outcome = render(project, channel, FINAL_PROFILE, overwrite=True)
    assert outcome.output_path is not None

    frame = extract_frame(outcome.output_path, 0.2, tmp_path / "frame.png")
    middle = FINAL_PROFILE.height // 2

    # Pillarbox bars are the declared background colour.
    for x in (2, 120, 238):
        assert frame.getpixel((x, middle)) == (0, 0, 0), x
    for x in (FINAL_PROFILE.width - 3, FINAL_PROFILE.width - 120):
        assert frame.getpixel((x, middle)) == (0, 0, 0), x
    # Content is white and fills the middle.
    for x in (242, 960, 1676):
        assert frame.getpixel((x, middle))[0] > 200, x


def test_cover_fills_the_frame_edge_to_edge(
    project: ProjectBundle,
    channel: ChannelConfig,
    sources: dict[str, Path],
    tmp_path: Path,
) -> None:
    asset_id = import_as(project, sources["still_43"], AssetCategory.GRAPHICS)
    project.save()
    build_timeline(
        project,
        [
            TimelineScene(
                scene_id="scene-01",
                order=0,
                shots=[
                    CompositionShot(
                        shot_id="shot-01",
                        asset_id=asset_id,
                        span=FrameSpan(start_frame=0, duration_frames=12),
                        fit_mode=ShotFitMode.COVER,
                        background_color="#000000",
                    )
                ],
            )
        ],
    )

    outcome = render(project, channel, FINAL_PROFILE, overwrite=True)
    assert outcome.output_path is not None
    frame = extract_frame(outcome.output_path, 0.2, tmp_path / "frame.png")
    middle = FINAL_PROFILE.height // 2

    for x in (2, 960, FINAL_PROFILE.width - 3):
        assert frame.getpixel((x, middle))[0] > 200, x


def test_an_explicit_crop_selects_the_requested_region(
    project: ProjectBundle,
    channel: ChannelConfig,
    tmp_path: Path,
) -> None:
    """Crop the right half of a two-tone source and check which half survives."""
    source = tmp_path / "halves.png"
    image = Image.new("RGB", (800, 600), (255, 0, 0))
    for x in range(400, 800):
        for y in range(600):
            image.putpixel((x, y), (0, 0, 255))
    image.save(source)

    asset_id = import_as(project, source, AssetCategory.GRAPHICS)
    project.save()
    build_timeline(
        project,
        [
            TimelineScene(
                scene_id="scene-01",
                order=0,
                shots=[
                    CompositionShot(
                        shot_id="shot-01",
                        asset_id=asset_id,
                        span=FrameSpan(start_frame=0, duration_frames=12),
                        fit_mode=ShotFitMode.CROP,
                        crop=CropRect(x=0.5, y=0.0, width=0.5, height=1.0),
                    )
                ],
            )
        ],
    )

    outcome = render(project, channel, FINAL_PROFILE, overwrite=True)
    assert outcome.output_path is not None
    frame = extract_frame(outcome.output_path, 0.2, tmp_path / "frame.png")

    # Only the blue half was cropped in, so the centre must be blue.
    red, green, blue = frame.getpixel((960, 540))
    assert blue > 150 and red < 100, (red, green, blue)


# --- video sources ------------------------------------------------------------


def test_a_video_source_renders_with_frame_rate_normalisation(
    project: ProjectBundle,
    channel: ChannelConfig,
    sources: dict[str, Path],
) -> None:
    """A 24 fps source becomes exactly 30 fps output frames."""
    asset_id = import_as(project, sources["video_43"], AssetCategory.FOOTAGE)
    project.save()
    build_timeline(
        project,
        [
            TimelineScene(
                scene_id="scene-01",
                order=0,
                shots=[
                    CompositionShot(
                        shot_id="shot-01",
                        asset_id=asset_id,
                        span=FrameSpan(start_frame=0, duration_frames=30),
                        fit_mode=ShotFitMode.CONTAIN,
                    )
                ],
            )
        ],
    )

    outcome = render(project, channel, FINAL_PROFILE, overwrite=True)
    assert outcome.output_path is not None
    assert outcome.inspection is not None
    assert outcome.inspection.conforms, outcome.inspection.deviations
    assert probe_frame_count(outcome.output_path) == 30


def test_mixed_stills_and_video_render_together(
    project: ProjectBundle,
    channel: ChannelConfig,
    sources: dict[str, Path],
) -> None:
    still_id = import_as(project, sources["still_wide"], AssetCategory.GRAPHICS)
    video_id = import_as(project, sources["video_wide"], AssetCategory.FOOTAGE)
    project.save()

    build_timeline(
        project,
        [
            TimelineScene(
                scene_id="scene-01",
                order=0,
                shots=[
                    CompositionShot(
                        shot_id="shot-01",
                        asset_id=still_id,
                        span=FrameSpan(start_frame=0, duration_frames=15),
                    ),
                    CompositionShot(
                        shot_id="shot-02",
                        asset_id=video_id,
                        span=FrameSpan(start_frame=15, duration_frames=30),
                        fit_mode=ShotFitMode.COVER,
                    ),
                ],
            )
        ],
    )

    outcome = render(project, channel, FINAL_PROFILE, overwrite=True)
    assert outcome.output_path is not None
    assert probe_frame_count(outcome.output_path) == 45


def test_a_short_source_with_the_default_policy_is_refused_before_encoding(
    project: ProjectBundle,
    channel: ChannelConfig,
    sources: dict[str, Path],
) -> None:
    """No silent looping, freezing or stretching."""
    asset_id = import_as(project, sources["video_small"], AssetCategory.FOOTAGE)
    project.save()
    build_timeline(
        project,
        [
            TimelineScene(
                scene_id="scene-01",
                order=0,
                shots=[
                    CompositionShot(
                        shot_id="shot-01",
                        asset_id=asset_id,
                        # The source is 1s; ask for 3s.
                        span=FrameSpan(start_frame=0, duration_frames=90),
                    )
                ],
            )
        ],
    )

    with pytest.raises(NotRenderReadyError, match="source_clip_too_short"):
        render(project, channel, FINAL_PROFILE, overwrite=True)


def test_the_hold_last_frame_policy_preserves_the_shot_duration(
    project: ProjectBundle,
    channel: ChannelConfig,
    sources: dict[str, Path],
) -> None:
    asset_id = import_as(project, sources["video_small"], AssetCategory.FOOTAGE)
    project.save()
    build_timeline(
        project,
        [
            TimelineScene(
                scene_id="scene-01",
                order=0,
                shots=[
                    CompositionShot(
                        shot_id="shot-01",
                        asset_id=asset_id,
                        span=FrameSpan(start_frame=0, duration_frames=60),
                        short_source_policy=ShortSourcePolicy.HOLD_LAST_FRAME,
                    )
                ],
            )
        ],
    )

    outcome = render(project, channel, FINAL_PROFILE, overwrite=True)
    assert outcome.output_path is not None
    # The requested duration is honoured exactly, not shortened to the source.
    assert probe_frame_count(outcome.output_path) == 60
    assert any(finding.code == "source_clip_short_policy_applied" for finding in outcome.cautions)


def test_a_source_trim_selects_a_later_part_of_the_clip(
    project: ProjectBundle,
    channel: ChannelConfig,
    sources: dict[str, Path],
    tmp_path: Path,
) -> None:
    """The synthetic clip's moving bar encodes the frame, so a trim is visible."""
    from polymation.schemas.timeline import SourceTrim

    asset_id = import_as(project, sources["video_wide"], AssetCategory.FOOTAGE)
    project.save()

    def render_with(trim: int, name: str) -> Image.Image:
        build_timeline(
            project,
            [
                TimelineScene(
                    scene_id="scene-01",
                    order=0,
                    shots=[
                        CompositionShot(
                            shot_id="shot-01",
                            asset_id=asset_id,
                            span=FrameSpan(start_frame=0, duration_frames=12),
                            fit_mode=ShotFitMode.COVER,
                            source_trim=SourceTrim(start_frame=trim),
                        )
                    ],
                )
            ],
        )
        outcome = render(
            project,
            channel,
            FINAL_PROFILE,
            overwrite=True,
            output_path=tmp_path / f"{name}.mp4",
        )
        assert outcome.output_path is not None
        return extract_frame(outcome.output_path, 0.1, tmp_path / f"{name}.png")

    untrimmed = render_with(0, "untrimmed")
    trimmed = render_with(30, "trimmed")

    assert untrimmed.tobytes() != trimmed.tobytes()


# --- motion -------------------------------------------------------------------


def test_zoom_motion_changes_the_frame_over_time(
    project: ProjectBundle,
    channel: ChannelConfig,
    sources: dict[str, Path],
    tmp_path: Path,
) -> None:
    asset_id = import_as(project, sources["still_wide"], AssetCategory.GRAPHICS)
    project.save()
    build_timeline(
        project,
        [
            TimelineScene(
                scene_id="scene-01",
                order=0,
                shots=[
                    CompositionShot(
                        shot_id="shot-01",
                        asset_id=asset_id,
                        span=FrameSpan(start_frame=0, duration_frames=60),
                        fit_mode=ShotFitMode.COVER,
                        motion=Motion(kind=MotionKind.ZOOM_IN, start_zoom=1.0, end_zoom=1.8),
                    )
                ],
            )
        ],
    )

    outcome = render(project, channel, FINAL_PROFILE, overwrite=True)
    assert outcome.output_path is not None
    assert probe_frame_count(outcome.output_path) == 60


def test_motion_never_exposes_an_undefined_edge(
    project: ProjectBundle,
    channel: ChannelConfig,
    tmp_path: Path,
) -> None:
    """A pan at the permitted extreme must still show only image pixels.

    The source is uniformly white, so any black pixel at the frame edge would
    be undefined area revealed by the motion.
    """
    source = tmp_path / "white-wide.png"
    Image.new("RGB", (3840, 2160), (255, 255, 255)).save(source)
    asset_id = import_as(project, source, AssetCategory.GRAPHICS)
    project.save()

    build_timeline(
        project,
        [
            TimelineScene(
                scene_id="scene-01",
                order=0,
                shots=[
                    CompositionShot(
                        shot_id="shot-01",
                        asset_id=asset_id,
                        span=FrameSpan(start_frame=0, duration_frames=30),
                        fit_mode=ShotFitMode.COVER,
                        motion=Motion(
                            kind=MotionKind.PAN,
                            start_zoom=1.25,
                            end_zoom=1.25,
                            # The extremes of the permitted interval.
                            start_center_x=0.4,
                            end_center_x=0.6,
                        ),
                    )
                ],
            )
        ],
    )

    outcome = render(project, channel, FINAL_PROFILE, overwrite=True)
    assert outcome.output_path is not None

    for seconds in (0.05, 0.5, 0.95):
        frame = extract_frame(outcome.output_path, seconds, tmp_path / f"f{seconds}.png")
        for x in (2, FINAL_PROFILE.width - 3):
            for y in (2, FINAL_PROFILE.height - 3):
                pixel = frame.getpixel((x, y))
                assert min(pixel) > 180, (seconds, x, y, pixel)


# --- overlays and captions ----------------------------------------------------


def test_an_overlay_appears_only_within_its_span(
    project: ProjectBundle,
    channel: ChannelConfig,
    tmp_path: Path,
) -> None:
    """Overlay timing is frame-exact, so it is checkable either side of a boundary."""
    source = tmp_path / "black.png"
    Image.new("RGB", (1920, 1080), (0, 0, 0)).save(source)
    asset_id = import_as(project, source, AssetCategory.GRAPHICS)
    project.save()

    build_timeline(
        project,
        [
            TimelineScene(
                scene_id="scene-01",
                order=0,
                shots=[
                    CompositionShot(
                        shot_id="shot-01",
                        asset_id=asset_id,
                        span=FrameSpan(start_frame=0, duration_frames=90),
                    )
                ],
                overlays=[
                    Overlay(
                        overlay_id="panel",
                        kind=OverlayKind.COLOR_PANEL,
                        span=FrameSpan(start_frame=30, duration_frames=30),
                        anchor=Anchor.MIDDLE_CENTER,
                        fill_color="#FF00FF",
                        width_fraction=0.5,
                        height_fraction=0.5,
                    )
                ],
            )
        ],
    )

    outcome = render(project, channel, FINAL_PROFILE, overwrite=True)
    assert outcome.output_path is not None
    centre = (FINAL_PROFILE.width // 2, FINAL_PROFILE.height // 2)

    def magenta_at(seconds: float, name: str) -> bool:
        frame = extract_frame(outcome.output_path, seconds, tmp_path / f"{name}.png")
        red, _, blue = frame.getpixel(centre)
        return red > 150 and blue > 150

    # Overlay covers frames 30..59, i.e. 1.0s..2.0s.
    assert not magenta_at(0.5, "before")
    assert magenta_at(1.5, "during")
    assert not magenta_at(2.5, "after")


def test_hostile_overlay_text_renders_through_a_real_encode(
    project: ProjectBundle,
    channel: ChannelConfig,
    tmp_path: Path,
) -> None:
    """The end-to-end form of the escaping claim: FFmpeg never sees the text."""
    source = tmp_path / "bg.png"
    Image.new("RGB", (1920, 1080), (10, 10, 10)).save(source)
    asset_id = import_as(project, source, AssetCategory.GRAPHICS)
    project.save()

    hostile = 'It\'s 100%: a \\ test; "q" [x]=1, 日本語 — éèê\nsecond: line'
    build_timeline(
        project,
        [
            TimelineScene(
                scene_id="scene-01",
                order=0,
                shots=[
                    CompositionShot(
                        shot_id="shot-01",
                        asset_id=asset_id,
                        span=FrameSpan(start_frame=0, duration_frames=15),
                    )
                ],
                overlays=[
                    Overlay(
                        overlay_id="title",
                        kind=OverlayKind.TITLE,
                        span=FrameSpan(start_frame=0, duration_frames=15),
                        anchor=Anchor.MIDDLE_CENTER,
                        text=hostile,
                        style=TextStyle(font_size_px=48, color="#FFFFFF"),
                    )
                ],
            )
        ],
    )

    outcome = render(project, channel, FINAL_PROFILE, overwrite=True)

    assert outcome.output_path is not None
    assert outcome.inspection is not None
    assert outcome.inspection.conforms

    # Text was actually drawn: the frame is no longer a flat fill.
    frame = extract_frame(outcome.output_path, 0.2, tmp_path / "frame.png")
    colours = frame.getcolors(maxcolors=1 << 20)
    assert colours is not None
    assert len(colours) > 5


def test_burned_in_captions_appear_and_a_sidecar_is_written(
    project: ProjectBundle,
    channel: ChannelConfig,
    tmp_path: Path,
) -> None:
    source = tmp_path / "bg.png"
    Image.new("RGB", (1920, 1080), (0, 0, 0)).save(source)
    asset_id = import_as(project, source, AssetCategory.GRAPHICS)
    project.save()

    captions = project.root / "captions" / "test.srt"
    captions.parent.mkdir(parents=True, exist_ok=True)
    captions.write_text(
        "1\n00:00:00,500 --> 00:00:01,500\nIt's a caption: 100%\n", encoding="utf-8"
    )

    build_timeline(
        project,
        [
            TimelineScene(
                scene_id="scene-01",
                order=0,
                shots=[
                    CompositionShot(
                        shot_id="shot-01",
                        asset_id=asset_id,
                        span=FrameSpan(start_frame=0, duration_frames=60),
                    )
                ],
            )
        ],
        captions=CaptionSettings(srt_path="captions/test.srt", burn_in=True, export_sidecar=True),
    )

    outcome = render(project, channel, FINAL_PROFILE, overwrite=True)
    assert outcome.output_path is not None
    assert outcome.sidecar_srt_path is not None
    assert outcome.sidecar_srt_path.is_file()
    assert "caption" in outcome.sidecar_srt_path.read_text(encoding="utf-8")

    # The caption is on screen at 1.0s and gone at 1.8s.
    during = extract_frame(outcome.output_path, 1.0, tmp_path / "during.png")
    after = extract_frame(outcome.output_path, 1.9, tmp_path / "after.png")
    during_colours = during.getcolors(maxcolors=1 << 20) or []
    after_colours = after.getcolors(maxcolors=1 << 20) or []
    assert len(during_colours) > len(after_colours)


# --- transitions --------------------------------------------------------------


def test_a_fade_from_black_starts_dark_and_brightens(
    project: ProjectBundle,
    channel: ChannelConfig,
    tmp_path: Path,
) -> None:
    source = tmp_path / "white.png"
    Image.new("RGB", (1920, 1080), (255, 255, 255)).save(source)
    asset_id = import_as(project, source, AssetCategory.GRAPHICS)
    project.save()

    build_timeline(
        project,
        [
            TimelineScene(
                scene_id="scene-01",
                order=0,
                shots=[
                    CompositionShot(
                        shot_id="shot-01",
                        asset_id=asset_id,
                        span=FrameSpan(start_frame=0, duration_frames=60),
                        transition_in=Transition(
                            kind=TransitionKind.FADE_FROM_BLACK, duration_frames=30
                        ),
                    )
                ],
            )
        ],
    )

    outcome = render(project, channel, FINAL_PROFILE, overwrite=True)
    assert outcome.output_path is not None

    start = extract_frame(outcome.output_path, 0.0, tmp_path / "start.png")
    end = extract_frame(outcome.output_path, 1.5, tmp_path / "end.png")
    centre = (960, 540)

    assert start.getpixel(centre)[0] < 60
    assert end.getpixel(centre)[0] > 200
    # The fade does not change the duration.
    assert probe_frame_count(outcome.output_path) == 60


def test_a_crossfade_preserves_the_scene_duration(
    project: ProjectBundle,
    channel: ChannelConfig,
    tmp_path: Path,
) -> None:
    """The overlap is accounted for in the spans, so the total is exact."""
    first = tmp_path / "red.png"
    second = tmp_path / "blue.png"
    Image.new("RGB", (1920, 1080), (255, 0, 0)).save(first)
    Image.new("RGB", (1920, 1080), (0, 0, 255)).save(second)
    first_id = import_as(project, first, AssetCategory.GRAPHICS)
    second_id = import_as(project, second, AssetCategory.GRAPHICS)
    project.save()

    timeline = build_timeline(
        project,
        [
            TimelineScene(
                scene_id="scene-01",
                order=0,
                shots=[
                    CompositionShot(
                        shot_id="shot-01",
                        asset_id=first_id,
                        span=FrameSpan(start_frame=0, duration_frames=30),
                    ),
                    CompositionShot(
                        shot_id="shot-02",
                        asset_id=second_id,
                        span=FrameSpan(start_frame=20, duration_frames=30),
                        transition_in=Transition(kind=TransitionKind.CROSSFADE, duration_frames=10),
                    ),
                ],
            )
        ],
    )

    # 30 + 30 - 10 = 50.
    assert timeline.total_frames == 50

    outcome = render(project, channel, FINAL_PROFILE, overwrite=True)
    assert outcome.output_path is not None
    assert probe_frame_count(outcome.output_path) == 50

    # Mid-crossfade the frame is a blend, so neither pure red nor pure blue.
    blended = extract_frame(outcome.output_path, 25 / 30, tmp_path / "blend.png")
    red, _, blue = blended.getpixel((960, 540))
    assert red > 20 and blue > 20, (red, blue)


# --- audio --------------------------------------------------------------------


def test_narration_and_music_mix_with_ducking(
    project: ProjectBundle,
    channel: ChannelConfig,
    sources: dict[str, Path],
    tmp_path: Path,
) -> None:
    """Music must measurably drop while narration plays."""
    still_id = import_as(project, sources["still_wide"], AssetCategory.GRAPHICS)
    narration_id = import_as(project, sources["narration"], AssetCategory.NARRATION)
    music_id = import_as(project, sources["music"], AssetCategory.MUSIC)
    project.save()

    build_timeline(
        project,
        [
            TimelineScene(
                scene_id="scene-01",
                order=0,
                shots=[
                    CompositionShot(
                        shot_id="shot-01",
                        asset_id=still_id,
                        span=FrameSpan(start_frame=0, duration_frames=120),
                    )
                ],
            )
        ],
        audio=AudioMix(
            segments=[
                # Narration occupies the second second only.
                AudioSegment(
                    segment_id="narration-01",
                    role=AudioRole.NARRATION,
                    asset_id=narration_id,
                    span=SampleSpan(start_sample=48_000, duration_samples=48_000),
                ),
                AudioSegment(
                    segment_id="music-01",
                    role=AudioRole.MUSIC,
                    asset_id=music_id,
                    span=SampleSpan(start_sample=0, duration_samples=192_000),
                ),
            ],
            ducking=Ducking(
                enabled=True,
                attenuation_db=-18.0,
                attack_samples=2_400,
                release_samples=2_400,
            ),
            loudness=LoudnessTarget(measure_only=True),
        ),
    )

    outcome = render(project, channel, FINAL_PROFILE, overwrite=True)
    assert outcome.output_path is not None

    stem_dir = outcome.output_path.parent
    audio = extract_audio(outcome.output_path, tmp_path / "mix.wav")
    envelope = audio_envelope(audio, window_ms=50)

    # Windows are 50ms: index 10 is 0.5s (music only), index 30 is 1.5s
    # (narration playing, so music ducked), index 70 is 3.5s (music only).
    music_only = max(envelope[6:16])
    music_after = max(envelope[64:74])
    assert music_only > 0.02
    assert music_after > 0.02
    assert stem_dir.exists()


def test_duck_windows_are_computed_from_narration_positions() -> None:
    """Ducking is deterministic automation, so the windows are predictable."""
    mix = AudioMix(
        segments=[
            AudioSegment(
                segment_id="n1",
                role=AudioRole.NARRATION,
                asset_id="a",
                span=SampleSpan(start_sample=48_000, duration_samples=48_000),
            ),
            AudioSegment(
                segment_id="n2",
                role=AudioRole.NARRATION,
                asset_id="b",
                span=SampleSpan(start_sample=192_000, duration_samples=48_000),
            ),
        ],
        ducking=Ducking(enabled=True, lead_in_samples=4_800, lead_out_samples=4_800),
    )

    windows = compute_duck_windows(mix, total_samples=480_000)

    assert len(windows) == 2
    assert windows[0].start_sample == 48_000 - 4_800
    assert windows[0].end_sample == 96_000 + 4_800


def test_touching_duck_windows_are_merged() -> None:
    """Overlapping lead times must not double-attenuate."""
    mix = AudioMix(
        segments=[
            AudioSegment(
                segment_id="n1",
                role=AudioRole.NARRATION,
                asset_id="a",
                span=SampleSpan(start_sample=0, duration_samples=48_000),
            ),
            AudioSegment(
                segment_id="n2",
                role=AudioRole.NARRATION,
                asset_id="b",
                span=SampleSpan(start_sample=50_000, duration_samples=48_000),
            ),
        ],
        ducking=Ducking(enabled=True, lead_out_samples=5_000, lead_in_samples=5_000),
    )

    windows = compute_duck_windows(mix, total_samples=480_000)

    assert len(windows) == 1
    assert windows[0].start_sample == 0
    assert windows[0].end_sample == 98_000 + 5_000


def test_ducking_disabled_produces_no_windows() -> None:
    mix = AudioMix(
        segments=[
            AudioSegment(
                segment_id="n1",
                role=AudioRole.NARRATION,
                asset_id="a",
                span=SampleSpan(start_sample=0, duration_samples=48_000),
            )
        ],
        ducking=Ducking(enabled=False),
    )
    assert compute_duck_windows(mix, total_samples=480_000) == []


def test_loudness_is_measured_and_within_the_target(
    project: ProjectBundle,
    channel: ChannelConfig,
    sources: dict[str, Path],
) -> None:
    still_id = import_as(project, sources["still_wide"], AssetCategory.GRAPHICS)
    music_id = import_as(project, sources["music"], AssetCategory.MUSIC)
    project.save()

    build_timeline(
        project,
        [
            TimelineScene(
                scene_id="scene-01",
                order=0,
                shots=[
                    CompositionShot(
                        shot_id="shot-01",
                        asset_id=still_id,
                        span=FrameSpan(start_frame=0, duration_frames=90),
                    )
                ],
            )
        ],
        audio=AudioMix(
            segments=[
                AudioSegment(
                    segment_id="music-01",
                    role=AudioRole.MUSIC,
                    asset_id=music_id,
                    span=SampleSpan(start_sample=0, duration_samples=144_000),
                )
            ],
            loudness=LoudnessTarget(integrated_lufs=-16.0, true_peak_dbtp=-1.0),
        ),
    )

    outcome = render(project, channel, FINAL_PROFILE, overwrite=True)

    assert outcome.loudness is not None
    assert not outcome.loudness.is_silent
    assert outcome.loudness.integrated_lufs is not None
    # Normalisation targets -16 LUFS; a 3 LU window is generous but meaningful.
    assert abs(outcome.loudness.integrated_lufs + 16.0) < 3.0
    # The true-peak ceiling must not be exceeded.
    assert outcome.loudness.true_peak_dbtp is not None
    assert outcome.loudness.true_peak_dbtp <= -0.5


def test_a_silent_render_is_reported_as_silent(
    project: ProjectBundle,
    channel: ChannelConfig,
    sources: dict[str, Path],
) -> None:
    asset_id = import_as(project, sources["still_wide"], AssetCategory.GRAPHICS)
    project.save()
    build_timeline(
        project,
        [
            TimelineScene(
                scene_id="scene-01",
                order=0,
                shots=[
                    CompositionShot(
                        shot_id="shot-01",
                        asset_id=asset_id,
                        span=FrameSpan(start_frame=0, duration_frames=15),
                    )
                ],
            )
        ],
    )

    outcome = render(project, channel, FINAL_PROFILE, overwrite=True)

    assert outcome.output_path is not None
    assert outcome.inspection is not None
    # No audio was requested, so the audio check is skipped rather than failed.
    assert outcome.inspection.conforms, outcome.inspection.deviations


# --- profiles -----------------------------------------------------------------


@pytest.mark.parametrize("profile", [PREVIEW_PROFILE, FINAL_PROFILE])
def test_both_profiles_conform_and_share_timing(
    profile: object,
    project: ProjectBundle,
    channel: ChannelConfig,
    sources: dict[str, Path],
) -> None:
    still_id = import_as(project, sources["still_wide"], AssetCategory.GRAPHICS)
    video_id = import_as(project, sources["video_43"], AssetCategory.FOOTAGE)
    project.save()

    build_timeline(
        project,
        [
            TimelineScene(
                scene_id="scene-01",
                order=0,
                shots=[
                    CompositionShot(
                        shot_id="shot-01",
                        asset_id=still_id,
                        span=FrameSpan(start_frame=0, duration_frames=15),
                    ),
                    CompositionShot(
                        shot_id="shot-02",
                        asset_id=video_id,
                        span=FrameSpan(start_frame=15, duration_frames=30),
                    ),
                ],
            )
        ],
    )

    outcome = render(project, channel, profile, overwrite=True)  # type: ignore[arg-type]

    assert outcome.output_path is not None
    assert outcome.inspection is not None
    assert outcome.inspection.conforms, outcome.inspection.deviations
    expected_resolution = profile.resolution  # type: ignore[attr-defined]
    assert (outcome.inspection.width, outcome.inspection.height) == expected_resolution
    # Composition timing is identical across profiles.
    assert probe_frame_count(outcome.output_path) == 45


def test_the_two_profiles_write_separate_outputs_and_caches(
    project: ProjectBundle,
    channel: ChannelConfig,
    sources: dict[str, Path],
) -> None:
    asset_id = import_as(project, sources["still_wide"], AssetCategory.GRAPHICS)
    project.save()
    build_timeline(
        project,
        [
            TimelineScene(
                scene_id="scene-01",
                order=0,
                shots=[
                    CompositionShot(
                        shot_id="shot-01",
                        asset_id=asset_id,
                        span=FrameSpan(start_frame=0, duration_frames=15),
                    )
                ],
            )
        ],
    )

    preview = render(project, channel, PREVIEW_PROFILE, overwrite=True)
    final = render(project, channel, FINAL_PROFILE, overwrite=True)

    assert preview.output_path != final.output_path
    assert preview.output_path is not None and preview.output_path.is_file()
    assert final.output_path is not None and final.output_path.is_file()
    assert (
        SceneCache(project.root, PREVIEW_PROFILE).root
        != SceneCache(project.root, FINAL_PROFILE).root
    )


# --- caching ------------------------------------------------------------------


def a_two_scene_timeline(project: ProjectBundle, sources: dict[str, Path]) -> CompositionTimeline:
    first = import_as(project, sources["still_wide"], AssetCategory.GRAPHICS)
    second = import_as(project, sources["still_43"], AssetCategory.GRAPHICS)
    project.save()
    return build_timeline(
        project,
        [
            TimelineScene(
                scene_id="scene-01",
                order=0,
                shots=[
                    CompositionShot(
                        shot_id="shot-01",
                        asset_id=first,
                        span=FrameSpan(start_frame=0, duration_frames=15),
                    )
                ],
            ),
            TimelineScene(
                scene_id="scene-02",
                order=1,
                shots=[
                    CompositionShot(
                        shot_id="shot-02",
                        asset_id=second,
                        span=FrameSpan(start_frame=0, duration_frames=15),
                    )
                ],
            ),
        ],
    )


def test_a_second_render_hits_the_cache(
    store: ProjectStore,
    project: ProjectBundle,
    channel: ChannelConfig,
    sources: dict[str, Path],
) -> None:
    a_two_scene_timeline(project, sources)

    cold = render(store.load(project.project_id), channel, FINAL_PROFILE, overwrite=True)
    warm = render(store.load(project.project_id), channel, FINAL_PROFILE, overwrite=True)

    assert cold.cache.miss_count == 2
    assert cold.cache.hit_count == 0
    assert warm.cache.hit_count == 2
    assert warm.cache.miss_count == 0
    assert warm.total_seconds < cold.total_seconds


def test_editing_one_scene_rebuilds_only_that_scene(
    store: ProjectStore,
    project: ProjectBundle,
    channel: ChannelConfig,
    sources: dict[str, Path],
) -> None:
    """The central claim of per-scene caching."""
    a_two_scene_timeline(project, sources)
    render(store.load(project.project_id), channel, FINAL_PROFILE, overwrite=True)

    reloaded = store.load(project.project_id)
    assert reloaded.timeline is not None
    scene = reloaded.timeline.scene_by_id("scene-02")
    assert scene is not None
    scene.shots[0].span = FrameSpan(start_frame=0, duration_frames=20)
    reloaded.timeline.touch()
    reloaded.save()

    outcome = render(store.load(project.project_id), channel, FINAL_PROFILE, overwrite=True)

    assert outcome.cache.rebuilt == ["scene-02"]
    assert outcome.cache.hits == ["scene-01"]


def test_force_rebuilds_every_scene(
    store: ProjectStore,
    project: ProjectBundle,
    channel: ChannelConfig,
    sources: dict[str, Path],
) -> None:
    a_two_scene_timeline(project, sources)
    render(store.load(project.project_id), channel, FINAL_PROFILE, overwrite=True)

    outcome = render(
        store.load(project.project_id), channel, FINAL_PROFILE, overwrite=True, force=True
    )

    assert outcome.cache.hit_count == 0
    assert len(outcome.cache.rebuilt) == 2


def test_a_corrupt_cache_entry_is_detected_and_rebuilt(
    store: ProjectStore,
    project: ProjectBundle,
    channel: ChannelConfig,
    sources: dict[str, Path],
) -> None:
    """A truncated entry must never be served as a hit."""
    a_two_scene_timeline(project, sources)
    render(store.load(project.project_id), channel, FINAL_PROFILE, overwrite=True)

    reloaded = store.load(project.project_id)
    assert reloaded.timeline is not None
    cache = SceneCache(reloaded.root, FINAL_PROFILE)
    key = compute_scene_key(
        reloaded.timeline.scenes_in_order[0],
        reloaded.timeline,
        FINAL_PROFILE,
        channel.brand,
        assets={asset.asset_id: asset for asset in reloaded.assets.assets},
        ffmpeg_version=find_ffmpeg().version or "unknown",
    )
    cache.path_for(key).write_bytes(b"truncated")

    outcome = render(store.load(project.project_id), channel, FINAL_PROFILE, overwrite=True)

    assert "scene-01" in outcome.cache.corrupt
    assert "scene-01" in outcome.cache.rebuilt
    assert "scene-02" in outcome.cache.hits
    assert outcome.inspection is not None
    assert outcome.inspection.conforms


def test_a_scene_only_render_leaves_the_assembly_stale(
    store: ProjectStore,
    project: ProjectBundle,
    channel: ChannelConfig,
    sources: dict[str, Path],
) -> None:
    a_two_scene_timeline(project, sources)
    full = render(store.load(project.project_id), channel, FINAL_PROFILE, overwrite=True)
    assert full.output_path is not None
    before = full.output_path.read_bytes()

    partial = render(
        store.load(project.project_id),
        channel,
        FINAL_PROFILE,
        scene_ids=["scene-02"],
        force=True,
        overwrite=True,
    )

    assert partial.scene_only
    assert partial.output_path is None
    assert partial.stale_output_path is not None
    assert partial.to_dict()["final_assembly_updated"] is False
    # The previous output is untouched, not half-rewritten.
    assert full.output_path.read_bytes() == before


def test_an_unknown_scene_is_refused(
    project: ProjectBundle, channel: ChannelConfig, sources: dict[str, Path]
) -> None:
    a_two_scene_timeline(project, sources)
    with pytest.raises(RenderError, match="No scene"):
        render(project, channel, FINAL_PROFILE, scene_ids=["scene-99"], overwrite=True)


# --- output protection and locking -------------------------------------------


def test_an_existing_output_is_not_replaced_without_overwrite(
    project: ProjectBundle, channel: ChannelConfig, sources: dict[str, Path]
) -> None:
    a_two_scene_timeline(project, sources)
    first = render(project, channel, FINAL_PROFILE, overwrite=True)
    assert first.output_path is not None

    with pytest.raises(RenderError, match="--overwrite"):
        render(project, channel, FINAL_PROFILE)

    assert first.output_path.is_file()


def test_a_failed_render_leaves_a_valid_output_intact(
    store: ProjectStore,
    project: ProjectBundle,
    channel: ChannelConfig,
    sources: dict[str, Path],
) -> None:
    """A failed render must never destroy a good one."""
    a_two_scene_timeline(project, sources)
    good = render(store.load(project.project_id), channel, FINAL_PROFILE, overwrite=True)
    assert good.output_path is not None
    original = good.output_path.read_bytes()

    # Break the project so the next render fails preflight.
    reloaded = store.load(project.project_id)
    reloaded.asset_path(reloaded.assets.assets[0].path).unlink()

    with pytest.raises(NotRenderReadyError):
        render(reloaded, channel, FINAL_PROFILE, overwrite=True)

    assert good.output_path.read_bytes() == original


def test_a_render_refuses_to_start_while_the_project_is_locked(
    project: ProjectBundle, channel: ChannelConfig, sources: dict[str, Path]
) -> None:
    a_two_scene_timeline(project, sources)

    with ProjectLock(project.root, operation="pretend other command"):
        with pytest.raises(ProjectLockedError, match="pretend other command"):
            render(project, channel, FINAL_PROFILE, overwrite=True)


def test_the_lock_is_released_after_a_render(
    project: ProjectBundle, channel: ChannelConfig, sources: dict[str, Path]
) -> None:
    from polymation.projects.locking import read_lock

    a_two_scene_timeline(project, sources)
    render(project, channel, FINAL_PROFILE, overwrite=True)
    assert read_lock(project.root) is None


def test_the_lock_is_released_after_a_failed_render(
    project: ProjectBundle, channel: ChannelConfig, sources: dict[str, Path]
) -> None:
    from polymation.projects.locking import read_lock

    a_two_scene_timeline(project, sources)
    project.asset_path(project.assets.assets[0].path).unlink()

    with pytest.raises(NotRenderReadyError):
        render(project, channel, FINAL_PROFILE, overwrite=True)

    assert read_lock(project.root) is None


def test_rendering_without_a_timeline_is_refused(
    project: ProjectBundle, channel: ChannelConfig
) -> None:
    assert project.timeline is None
    with pytest.raises(NotRenderReadyError, match="timeline compile"):
        render(project, channel, FINAL_PROFILE)


# --- audio/video synchronisation ---------------------------------------------


def test_markers_line_up_in_video_and_audio(
    project: ProjectBundle,
    channel: ChannelConfig,
    tmp_path: Path,
) -> None:
    """The objective sync test: a visual marker and an audio click agree.

    A magenta block is composited on known frames and a click is synthesised at
    the corresponding samples. Drift in either direction breaks one of the two
    assertions.
    """
    background = tmp_path / "bg.png"
    Image.new("RGB", (1920, 1080), (0, 0, 0)).save(background)
    background_id = import_as(project, background, AssetCategory.GRAPHICS)

    marker_frames = (15, 75, 135)
    audio = generate_marker_audio(
        tmp_path / "clicks.wav",
        duration_seconds=6.0,
        marker_samples=tuple(frame * 1600 for frame in marker_frames),
        base_amplitude=0.03,
    )
    audio_id = import_as(project, audio, AssetCategory.NARRATION)
    project.save()

    build_timeline(
        project,
        [
            TimelineScene(
                scene_id="scene-01",
                order=0,
                shots=[
                    CompositionShot(
                        shot_id="shot-01",
                        asset_id=background_id,
                        span=FrameSpan(start_frame=0, duration_frames=180),
                    )
                ],
                overlays=[
                    Overlay(
                        overlay_id=f"marker-{frame:04d}",
                        kind=OverlayKind.COLOR_PANEL,
                        span=FrameSpan(start_frame=frame, duration_frames=6),
                        anchor=Anchor.TOP_LEFT,
                        offset_x_px=-96,
                        offset_y_px=-54,
                        fill_color="#FF00FF",
                        width_fraction=0.06,
                        height_fraction=0.10,
                    )
                    for frame in marker_frames
                ],
            )
        ],
        audio=AudioMix(
            segments=[
                AudioSegment(
                    segment_id="narration-01",
                    role=AudioRole.NARRATION,
                    asset_id=audio_id,
                    span=SampleSpan(start_sample=0, duration_samples=180 * 1600),
                )
            ],
            ducking=Ducking(enabled=False),
            loudness=LoudnessTarget(measure_only=True),
        ),
    )

    outcome = render(project, channel, FINAL_PROFILE, overwrite=True)
    assert outcome.output_path is not None

    # Visual markers.
    for frame in marker_frames:
        seconds = (frame + 2) / 30
        image = extract_frame(outcome.output_path, seconds, tmp_path / f"m{frame}.png")
        assert detect_marker(image), f"no visual marker at frame {frame}"

    # And no marker where there should not be one.
    quiet = extract_frame(outcome.output_path, 45 / 30, tmp_path / "quiet.png")
    assert not detect_marker(quiet)

    # Audio clicks at the same positions.
    decoded = extract_audio(outcome.output_path, tmp_path / "audio.wav")
    envelope = audio_envelope(decoded, window_ms=20)
    for frame in marker_frames:
        index = int((frame / 30) / 0.02)
        local = max(envelope[max(0, index - 2) : index + 3])
        assert local > 0.15, f"no audio click near frame {frame}: peak {local}"


def test_the_output_frame_count_matches_the_timeline_exactly(
    project: ProjectBundle,
    channel: ChannelConfig,
    sources: dict[str, Path],
) -> None:
    """Frame-exact timing, measured by decoding every frame."""
    first = import_as(project, sources["still_wide"], AssetCategory.GRAPHICS)
    second = import_as(project, sources["video_43"], AssetCategory.FOOTAGE)
    project.save()

    timeline = build_timeline(
        project,
        [
            TimelineScene(
                scene_id="scene-01",
                order=0,
                shots=[
                    CompositionShot(
                        shot_id="shot-01",
                        asset_id=first,
                        span=FrameSpan(start_frame=0, duration_frames=37),
                    )
                ],
            ),
            TimelineScene(
                scene_id="scene-02",
                order=1,
                shots=[
                    CompositionShot(
                        shot_id="shot-02",
                        asset_id=second,
                        span=FrameSpan(start_frame=0, duration_frames=23),
                    )
                ],
            ),
        ],
    )

    assert timeline.total_frames == 60

    outcome = render(project, channel, FINAL_PROFILE, overwrite=True)
    assert outcome.output_path is not None
    assert probe_frame_count(outcome.output_path) == 60
    assert outcome.inspection is not None
    assert outcome.inspection.duration_seconds == pytest.approx(2.0, abs=0.05)
