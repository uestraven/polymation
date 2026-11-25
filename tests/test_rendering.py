"""Demonstration creation, FFmpeg rendering and output inspection.

Tests marked ``ffmpeg`` execute real FFmpeg and ffprobe commands. When the
toolchain is absent they skip explicitly, and M0.1's rendering acceptance
criteria (A16, A17) are then NOT satisfied.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from polymation.cli import app
from polymation.config.channel import ChannelConfig
from polymation.config.paths import Workspace
from polymation.errors import (
    ExitCode,
    MissingDependencyError,
    NotRenderReadyError,
    PolymationError,
    ProjectExistsError,
    RenderError,
)
from polymation.media.checksum import sha256_file
from polymation.projects.store import ProjectStore
from polymation.rendering.compose import (
    OUTPUT_AUDIO_SAMPLE_RATE,
    OUTPUT_FPS,
    OUTPUT_HEIGHT,
    OUTPUT_WIDTH,
    _build_filter_graph,
    _fit_filter,
    inspect_output,
    render_project,
)
from polymation.rendering.demo import (
    DEMO_PROJECT_ID,
    DEMO_TOTAL_DURATION_SECONDS,
    create_demo_project,
)
from polymation.rendering.ffmpeg import (
    find_ffmpeg,
    find_ffprobe,
    find_tool,
    run_tool,
    toolchain_available,
)
from polymation.schemas.assets import AssetType
from polymation.schemas.storyboard import FitMode
from polymation.validation.validator import assess_render_readiness, validate_project

#: Skip marker for tests that execute real FFmpeg commands.
#:
#: Defined in this module rather than in conftest.py: conftest is not importable
#: by module path, and `tests` is deliberately not a package. A skip here is an
#: honest report that rendering went unverified — it is never a pass.
ffmpeg_required = pytest.mark.skipif(
    not toolchain_available(),
    reason=(
        "FFmpeg and ffprobe are not installed. Rendering acceptance for M0.1 is "
        "NOT satisfied when this test is skipped."
    ),
)

# --- toolchain discovery -------------------------------------------------------


def test_a_missing_tool_is_reported_as_unavailable() -> None:
    info = find_tool("definitely-not-a-real-binary", "POLYMATION_NONEXISTENT")
    assert not info.available
    assert info.path is None
    assert info.version is None


def test_an_environment_override_is_honoured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = tmp_path / "not-executable"
    fake.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("POLYMATION_FFMPEG", str(fake))

    info = find_ffmpeg()
    assert info.path == fake
    # Present but not executable: reported unavailable rather than assumed working.
    assert not info.available


def test_toolchain_availability_matches_both_tools() -> None:
    assert toolchain_available() == (find_ffmpeg().available and find_ffprobe().available)


# --- filter construction (no FFmpeg needed) ------------------------------------


def test_contain_mode_pads_rather_than_stretching() -> None:
    chain = _fit_filter(FitMode.CONTAIN, "0x191D38")
    assert "force_original_aspect_ratio=decrease" in chain
    assert f"pad={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}" in chain
    assert "color=0x191D38" in chain


def test_cover_mode_crops_rather_than_stretching() -> None:
    chain = _fit_filter(FitMode.COVER, "0x191D38")
    assert "force_original_aspect_ratio=increase" in chain
    assert f"crop={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}" in chain


def test_neither_fit_mode_can_distort() -> None:
    """Any unconstrained `scale=W:H` would stretch the source."""
    for mode in FitMode:
        chain = _fit_filter(mode, "0x000000")
        assert "force_original_aspect_ratio" in chain


def test_the_filter_graph_concatenates_every_shot() -> None:
    from polymation.schemas.storyboard import VisualShot

    shots = [
        VisualShot(
            shot_id=f"shot-{index:02d}",
            scene_id=f"scene-{index:02d}",
            description="x",
            duration_seconds=5.0,
        )
        for index in range(3)
    ]
    graph = _build_filter_graph(shots, "0x191D38")

    assert "concat=n=3:v=1:a=0[vout]" in graph
    assert "[v0][v1][v2]concat" in graph
    assert graph.count(f"fps={OUTPUT_FPS}") == 3
    assert graph.count("setsar=1") == 3


# --- demo creation (no FFmpeg needed) ------------------------------------------


def test_demo_creation_builds_three_scenes_of_fifteen_seconds(
    workspace: Workspace, channel: ChannelConfig
) -> None:
    result = create_demo_project(workspace, channel)
    bundle = result.bundle

    assert bundle.project_id == DEMO_PROJECT_ID
    assert len(bundle.storyboard.scenes) == 3
    assert bundle.storyboard.shot_count == 3
    assert bundle.storyboard.duration_seconds == pytest.approx(DEMO_TOTAL_DURATION_SECONDS)
    assert DEMO_TOTAL_DURATION_SECONDS == 15.0


def test_demo_creation_generates_four_original_fixtures(
    workspace: Workspace, channel: ChannelConfig
) -> None:
    result = create_demo_project(workspace, channel)

    assert len(result.fixture_paths) == 4
    for path in result.fixture_paths:
        assert path.is_file()
        assert path.stat().st_size > 0


def test_demo_imports_fixtures_through_the_real_asset_pipeline(
    workspace: Workspace, channel: ChannelConfig
) -> None:
    """The demo must exercise import, checksums and rights like any other project."""
    bundle = create_demo_project(workspace, channel).bundle

    assert len(bundle.assets.assets) == 4
    assert len(bundle.rights.records) == 4

    types = {asset.asset_type for asset in bundle.assets.assets}
    assert types == {AssetType.IMAGE, AssetType.AUDIO}

    for asset in bundle.assets.assets:
        on_disk = bundle.asset_path(asset.path)
        assert on_disk.is_file()
        assert sha256_file(on_disk) == asset.sha256


def test_demo_rights_records_claim_original_work_but_remain_unreviewed(
    workspace: Workspace, channel: ChannelConfig
) -> None:
    bundle = create_demo_project(workspace, channel).bundle

    for record in bundle.rights.records:
        assert record.basis.value == "original_work"
        assert record.rights_holder == "Polymation Studio"
        assert record.ai_generated is False
        # Even our own work is not self-approved.
        assert record.review_status.value == "unreviewed"


def test_demo_narration_stays_draft(workspace: Workspace, channel: ChannelConfig) -> None:
    bundle = create_demo_project(workspace, channel).bundle

    assert len(bundle.script.scenes) == 3
    assert bundle.script.draft_scenes == bundle.script.scenes
    assert not bundle.script.is_fully_approved


def test_demo_scene_two_uses_the_four_by_three_source_in_contain_mode(
    workspace: Workspace, channel: ChannelConfig
) -> None:
    bundle = create_demo_project(workspace, channel).bundle
    shot = bundle.storyboard.shots[1]
    asset = bundle.assets.by_id(shot.asset_id or "")

    assert asset is not None
    assert (asset.width, asset.height) == (640, 480)
    assert shot.fit_mode is FitMode.CONTAIN


def test_demo_project_validates_and_is_render_ready(
    workspace: Workspace, channel: ChannelConfig
) -> None:
    bundle = create_demo_project(workspace, channel).bundle
    report = validate_project(bundle)

    assert report.structurally_valid, [finding.message for finding in report.errors]
    assert assess_render_readiness(bundle, report).render_ready


def test_demo_creation_refuses_to_clobber_an_existing_demo(
    workspace: Workspace, channel: ChannelConfig
) -> None:
    create_demo_project(workspace, channel)

    with pytest.raises(ProjectExistsError):
        create_demo_project(workspace, channel)


def test_demo_recreate_rebuilds_the_project(workspace: Workspace, channel: ChannelConfig) -> None:
    first = create_demo_project(workspace, channel)
    (first.bundle.root / "marker.txt").write_text("x", encoding="utf-8")

    second = create_demo_project(workspace, channel, recreate=True)

    assert second.recreated
    assert not (second.bundle.root / "marker.txt").exists()
    assert len(second.bundle.assets.assets) == 4


def test_demo_creation_reports_font_substitution_honestly(
    workspace: Workspace, channel: ChannelConfig
) -> None:
    result = create_demo_project(workspace, channel)

    assert len(result.font_resolutions) == 2
    for resolution in result.font_resolutions:
        if resolution.is_fallback:
            assert "NOT the brand font" in resolution.description


def test_a_failed_demo_build_leaves_no_partial_project(
    workspace: Workspace, channel: ChannelConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    import polymation.rendering.demo as demo_module

    def explode(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("simulated failure midway through the build")

    monkeypatch.setattr(demo_module, "_build_demo_project", explode)

    with pytest.raises(RuntimeError, match="simulated failure"):
        create_demo_project(workspace, channel)

    assert not workspace.project_dir(DEMO_PROJECT_ID).exists()


# --- render guard rails (no FFmpeg needed) -------------------------------------


def test_rendering_an_empty_project_is_refused(
    workspace: Workspace, channel: ChannelConfig, project: object
) -> None:
    bundle = ProjectStore(workspace).load(project.project_id)  # type: ignore[attr-defined]

    with pytest.raises(NotRenderReadyError, match="no_shots"):
        render_project(bundle, channel.brand)


def test_inspecting_a_missing_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(RenderError, match="does not exist"):
        inspect_output(tmp_path / "absent.mp4")


def test_render_requires_ffmpeg(
    workspace: Workspace, channel: ChannelConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without FFmpeg the render fails loudly; it never fakes a result."""
    bundle = create_demo_project(workspace, channel).bundle
    monkeypatch.setenv("PATH", str(workspace.root))
    monkeypatch.setenv("POLYMATION_FFMPEG", str(workspace.root / "no-such-ffmpeg"))

    with pytest.raises(MissingDependencyError, match="ffmpeg is required"):
        render_project(bundle, channel.brand)


def test_run_tool_uses_an_argument_vector_not_a_shell_string() -> None:
    """A filename containing shell metacharacters must not be interpreted."""
    import shutil

    echo = shutil.which("echo")
    if echo is None:  # pragma: no cover - echo exists on every supported platform
        pytest.skip("no echo binary available")

    hostile = "a; rm -rf /; $(whoami) `id` && echo pwned"
    result = run_tool(Path(echo), [hostile])

    assert result.ok
    assert result.stdout.strip() == hostile


# --- real FFmpeg integration ---------------------------------------------------


@ffmpeg_required
@pytest.mark.ffmpeg
def test_demo_renders_to_the_mandated_specification(
    workspace: Workspace, channel: ChannelConfig
) -> None:
    bundle = create_demo_project(workspace, channel).bundle
    result = render_project(bundle, channel.brand)

    assert result.output_path.is_file()
    assert result.file_size_bytes > 0
    assert result.render_seconds > 0

    inspection = inspect_output(
        result.output_path, expected_duration_seconds=DEMO_TOTAL_DURATION_SECONDS
    )

    assert inspection.width == OUTPUT_WIDTH
    assert inspection.height == OUTPUT_HEIGHT
    assert inspection.frame_rate == pytest.approx(OUTPUT_FPS, abs=0.01)
    assert inspection.video_codec == "h264"
    assert inspection.audio_codec == "aac"
    assert inspection.audio_sample_rate == OUTPUT_AUDIO_SAMPLE_RATE
    assert inspection.duration_seconds == pytest.approx(DEMO_TOTAL_DURATION_SECONDS, abs=0.5)
    assert inspection.conforms, inspection.deviations


@ffmpeg_required
@pytest.mark.ffmpeg
def test_render_refuses_to_overwrite_without_permission(
    workspace: Workspace, channel: ChannelConfig
) -> None:
    bundle = create_demo_project(workspace, channel).bundle
    first = render_project(bundle, channel.brand)
    original_bytes = first.output_path.read_bytes()

    with pytest.raises(RenderError, match="--overwrite"):
        render_project(bundle, channel.brand)

    assert first.output_path.read_bytes() == original_bytes


@ffmpeg_required
@pytest.mark.ffmpeg
def test_render_overwrites_when_asked(workspace: Workspace, channel: ChannelConfig) -> None:
    bundle = create_demo_project(workspace, channel).bundle
    first = render_project(bundle, channel.brand)
    second = render_project(bundle, channel.brand, overwrite=True)

    assert second.output_path == first.output_path
    assert second.output_path.is_file()


@ffmpeg_required
@pytest.mark.ffmpeg
def test_the_four_by_three_source_is_pillarboxed_not_stretched(
    workspace: Workspace, channel: ChannelConfig, tmp_path: Path
) -> None:
    """Extract a frame from scene 2 and check for indigo bars with no distortion."""
    from PIL import Image

    from polymation.rendering.ffmpeg import require_ffmpeg, run_tool

    bundle = create_demo_project(workspace, channel).bundle
    result = render_project(bundle, channel.brand)

    frame = tmp_path / "scene-02.png"
    ffmpeg = require_ffmpeg()
    assert ffmpeg.path is not None
    extract = run_tool(
        ffmpeg.path,
        [
            "-nostdin",
            "-hide_banner",
            "-y",
            "-ss",
            "7.5",
            "-i",
            str(result.output_path),
            "-frames:v",
            "1",
            str(frame),
        ],
    )
    assert extract.ok, extract.tail()

    with Image.open(frame) as image:
        rgb = image.convert("RGB")
        assert rgb.size == (OUTPUT_WIDTH, OUTPUT_HEIGHT)
        # A 4:3 source in a 16:9 frame leaves pillarbox bars at the left and
        # right edges, filled with the brand padding colour.
        indigo = channel.brand.palette.as_rgb("deep_indigo")
        for sample_x in (2, OUTPUT_WIDTH - 3):
            pixel = rgb.getpixel((sample_x, OUTPUT_HEIGHT // 2))
            assert all(
                abs(actual - expected) <= 12 for actual, expected in zip(pixel, indigo, strict=True)
            ), f"expected pillarbox near {indigo} at x={sample_x}, got {pixel}"

        # The pattern itself occupies the middle of the frame, so the centre
        # row must hold more than the single padding colour.
        centre_row = [rgb.getpixel((x, OUTPUT_HEIGHT // 2)) for x in range(0, OUTPUT_WIDTH, 8)]
        assert len(set(centre_row)) > 1, "the frame is a flat fill — no content was composed"

        # A 4:3 source letterboxed into 16:9 at height 1080 occupies 1440px of
        # width, leaving 240px of bar on each side. Content must not reach the
        # edge, which is what stretching would do.
        assert all(
            abs(actual - expected) <= 12
            for actual, expected in zip(
                rgb.getpixel((100, OUTPUT_HEIGHT // 2)), indigo, strict=True
            )
        ), "content reached x=100: the 4:3 source appears to have been stretched"


@ffmpeg_required
@pytest.mark.ffmpeg
def test_inspection_reports_deviations_from_the_specification(
    workspace: Workspace, channel: ChannelConfig, tmp_path: Path
) -> None:
    """A deliberately off-spec file must be reported as non-conforming."""
    from polymation.rendering.ffmpeg import require_ffmpeg, run_tool

    ffmpeg = require_ffmpeg()
    assert ffmpeg.path is not None

    off_spec = tmp_path / "off-spec.mp4"
    build = run_tool(
        ffmpeg.path,
        [
            "-nostdin",
            "-hide_banner",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=640x480:d=1:r=25",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(off_spec),
        ],
    )
    assert build.ok, build.tail()

    inspection = inspect_output(off_spec)
    assert not inspection.conforms
    assert any("resolution" in deviation for deviation in inspection.deviations)
    assert any("frame rate" in deviation for deviation in inspection.deviations)
    assert any("audio codec" in deviation for deviation in inspection.deviations)


@ffmpeg_required
@pytest.mark.ffmpeg
def test_demo_render_through_the_cli(
    runner: CliRunner, workspace: Workspace, channel: ChannelConfig
) -> None:
    create_demo_project(workspace, channel)

    result = runner.invoke(
        app,
        ["demo", "render", "--workspace", str(workspace.root), "--json"],
        catch_exceptions=True,
    )
    assert result.exit_code == int(ExitCode.OK), result.stdout

    payload = json.loads(result.stdout)
    inspection = payload["inspection"]
    assert inspection["conforms_to_specification"] is True
    assert inspection["width"] == OUTPUT_WIDTH
    assert inspection["audio_sample_rate"] == OUTPUT_AUDIO_SAMPLE_RATE
    assert Path(payload["output_path"]).is_file()
    # The exact FFmpeg command is reported, for reproducibility.
    assert payload["ffmpeg_argv"][0].endswith("ffmpeg")


@ffmpeg_required
@pytest.mark.ffmpeg
def test_ffprobe_identifies_a_rendered_video_asset(
    workspace: Workspace, channel: ChannelConfig
) -> None:
    """A rendered MP4 re-imported as an asset is detected as video by ffprobe."""
    from polymation.media.inspect import inspect_media

    bundle = create_demo_project(workspace, channel).bundle
    result = render_project(bundle, channel.brand)

    probe = inspect_media(result.output_path)
    assert probe.asset_type is AssetType.VIDEO
    assert (probe.width, probe.height) == (OUTPUT_WIDTH, OUTPUT_HEIGHT)
    assert probe.video_codec == "h264"
    assert probe.audio_codec == "aac"
    assert probe.duration_seconds == pytest.approx(DEMO_TOTAL_DURATION_SECONDS, abs=0.5)


# --- CLI demo create (no FFmpeg needed) ----------------------------------------


def test_demo_create_through_the_cli(runner: CliRunner, workspace: Workspace) -> None:
    result = runner.invoke(
        app,
        ["demo", "create", "--workspace", str(workspace.root), "--json"],
        catch_exceptions=True,
    )
    assert result.exit_code == 0, result.stdout

    payload = json.loads(result.stdout)
    assert payload["project_id"] == DEMO_PROJECT_ID
    assert payload["scene_count"] == 3
    assert payload["shot_count"] == 3
    assert payload["duration_seconds"] == pytest.approx(15.0)
    assert len(payload["fixtures"]) == 4
    assert payload["brand_provisional"] is True


def test_demo_create_twice_through_the_cli_is_a_conflict(
    runner: CliRunner, workspace: Workspace
) -> None:
    arguments = ["demo", "create", "--workspace", str(workspace.root)]
    assert runner.invoke(app, arguments, catch_exceptions=True).exit_code == 0

    result = runner.invoke(app, arguments, catch_exceptions=True)
    assert isinstance(result.exception, PolymationError)
    assert result.exception.exit_code == ExitCode.CONFLICT
