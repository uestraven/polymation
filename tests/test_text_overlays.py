"""Safe text rendering and overlay placement.

The central claim under test: arbitrary text is safe because it becomes pixels
before FFmpeg is involved, not because it is escaped correctly.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from polymation.config.channel import ChannelConfig
from polymation.errors import ConfigurationError, RenderError
from polymation.rendering.colors import resolve_rgb, resolve_rgba, to_ffmpeg_color
from polymation.rendering.overlays import render_overlay, render_overlays
from polymation.rendering.profiles import FINAL_PROFILE, PREVIEW_PROFILE
from polymation.rendering.text import (
    normalise_linebreaks,
    render_color_panel,
    render_text_image,
    wrap_text,
)
from polymation.schemas.base import utc_now
from polymation.schemas.timeline import (
    Anchor,
    CompositionShot,
    CompositionTimeline,
    Overlay,
    OverlayKind,
    TextAlign,
    TextStyle,
    TimelineScene,
)
from polymation.schemas.timing import FrameSpan

NOW = utc_now()

#: Text that would need careful escaping in an FFmpeg `drawtext` filter. Each
#: character here breaks either the filter-graph parser, the filter's own
#: argument parser, or both.
HOSTILE_TEXT = (
    "Chrono Trigger's 'time': 100%: a \\ test; \"quoted\" "
    "[brackets] {braces} =equals, comma\nnewline\ttab"
)

#: Text exercising glyph coverage beyond ASCII.
UNICODE_TEXT = "éèê ÅØÆ ΩΔΣ Привет — 日本語"


def a_timeline() -> CompositionTimeline:
    return CompositionTimeline(
        project_id="p",
        scenes=[
            TimelineScene(
                scene_id="scene-01",
                order=0,
                shots=[
                    CompositionShot(
                        shot_id="shot-01",
                        asset_id="asset-0001",
                        span=FrameSpan(start_frame=0, duration_frames=60),
                    )
                ],
            )
        ],
        created_at=NOW,
        updated_at=NOW,
    )


# --- colours ------------------------------------------------------------------


def test_hex_colours_resolve(channel: ChannelConfig) -> None:
    assert resolve_rgb("#191D38", channel.brand) == (25, 29, 56)
    assert resolve_rgb("#FFFFFF", channel.brand) == (255, 255, 255)


def test_palette_references_resolve(channel: ChannelConfig) -> None:
    assert resolve_rgb("palette:deep_indigo", channel.brand) == (25, 29, 56)
    assert resolve_rgb("palette:vermilion", channel.brand) == (201, 68, 54)


def test_an_unknown_palette_entry_names_the_alternatives(channel: ChannelConfig) -> None:
    with pytest.raises(ConfigurationError, match="Available:"):
        resolve_rgb("palette:not_a_colour", channel.brand)


def test_a_malformed_colour_is_refused(channel: ChannelConfig) -> None:
    with pytest.raises(ConfigurationError, match="neither"):
        resolve_rgb("chartreuse", channel.brand)


def test_opacity_becomes_an_alpha_channel(channel: ChannelConfig) -> None:
    assert resolve_rgba("#000000", 0.0, channel.brand) == (0, 0, 0, 0)
    assert resolve_rgba("#000000", 1.0, channel.brand) == (0, 0, 0, 255)
    assert resolve_rgba("#000000", 0.5, channel.brand)[3] == 128


def test_out_of_range_opacity_is_refused(channel: ChannelConfig) -> None:
    for bad in (-0.1, 1.1):
        with pytest.raises(ValueError, match="opacity"):
            resolve_rgba("#000000", bad, channel.brand)


def test_ffmpeg_colour_format(channel: ChannelConfig) -> None:
    assert to_ffmpeg_color("palette:deep_indigo", channel.brand) == "0x191D38"
    assert to_ffmpeg_color("#c94436", channel.brand) == "0xC94436"


# --- wrapping -----------------------------------------------------------------


def test_linebreaks_are_normalised() -> None:
    assert normalise_linebreaks("a\r\nb\rc\nd") == ["a", "b", "c", "d"]
    assert normalise_linebreaks("a\n\nb") == ["a", "", "b"]


def test_wrapping_respects_explicit_breaks(channel: ChannelConfig, tmp_path: Path) -> None:
    rendered = render_text_image(
        "first line\nsecond line",
        TextStyle(font_size_px=32),
        frame_width=1920,
        frame_height=1080,
        destination=tmp_path / "t.png",
        brand=channel.brand,
    )
    assert rendered.lines == ("first line", "second line")


def test_wrapping_breaks_an_unspaced_run(channel: ChannelConfig, tmp_path: Path) -> None:
    """A long URL or CJK run must not overflow the frame."""
    rendered = render_text_image(
        "x" * 400,
        TextStyle(font_size_px=36, max_width_fraction=0.5),
        frame_width=1920,
        frame_height=1080,
        destination=tmp_path / "t.png",
        brand=channel.brand,
    )
    assert len(rendered.lines) > 1
    assert rendered.width <= int(1920 * 0.5)


def test_wrapping_rejects_a_non_positive_width() -> None:
    from polymation.rendering.fixtures import load_font, resolve_font

    font = load_font(resolve_font("DejaVuSans"), 20)
    with pytest.raises(ValueError, match="must be positive"):
        wrap_text("hello", font, 0)


def test_max_lines_truncates_and_reports_it(channel: ChannelConfig, tmp_path: Path) -> None:
    rendered = render_text_image(
        "one\ntwo\nthree\nfour\nfive",
        TextStyle(font_size_px=28),
        frame_width=1920,
        frame_height=1080,
        destination=tmp_path / "t.png",
        brand=channel.brand,
        max_lines=2,
    )
    assert len(rendered.lines) == 2
    assert rendered.truncated
    assert rendered.lines[-1].endswith("…")


def test_text_within_the_line_budget_is_not_marked_truncated(
    channel: ChannelConfig, tmp_path: Path
) -> None:
    rendered = render_text_image(
        "one\ntwo",
        TextStyle(font_size_px=28),
        frame_width=1920,
        frame_height=1080,
        destination=tmp_path / "t.png",
        brand=channel.brand,
        max_lines=3,
    )
    assert not rendered.truncated


# --- hostile text -------------------------------------------------------------


def test_hostile_text_renders_to_pixels_without_escaping(
    channel: ChannelConfig, tmp_path: Path
) -> None:
    destination = tmp_path / "hostile.png"
    rendered = render_text_image(
        HOSTILE_TEXT,
        TextStyle(font_size_px=36, background_color="#000000", background_opacity=0.7),
        frame_width=1920,
        frame_height=1080,
        destination=destination,
        brand=channel.brand,
    )

    assert destination.is_file()
    with Image.open(destination) as image:
        assert image.mode == "RGBA"
        assert image.size == (rendered.width, rendered.height)
        # Something was actually drawn.
        colours = image.convert("RGBA").getcolors(maxcolors=1 << 20)
        assert colours is not None
        assert len(colours) > 1

    # The text survived into the rendered line list unmodified, apart from
    # wrapping: nothing was stripped or escaped.
    joined = "\n".join(rendered.lines)
    for fragment in ("100%", "\\", '"quoted"', "'time'", "=equals", "[brackets]"):
        assert fragment in joined, fragment


@pytest.mark.parametrize(
    "text",
    [
        "'",
        '"',
        "\\",
        ":",
        "%",
        ",",
        ";",
        "[",
        "]",
        "=",
        "100%:'\\\",;[]=",
        "\n",
        "\t",
        "a\x0bb",
        UNICODE_TEXT,
        "   leading and trailing   ",
    ],
)
def test_every_awkward_character_renders(text: str, channel: ChannelConfig, tmp_path: Path) -> None:
    rendered = render_text_image(
        text if text.strip() else f"x{text}x",
        TextStyle(font_size_px=28),
        frame_width=1920,
        frame_height=1080,
        destination=tmp_path / "t.png",
        brand=channel.brand,
    )
    assert rendered.path.is_file()
    assert rendered.width > 0 and rendered.height > 0


def test_a_text_box_never_exceeds_the_frame(channel: ChannelConfig, tmp_path: Path) -> None:
    """The real invariant: a text overlay is always compositable."""
    rendered = render_text_image(
        "x" * 2000,
        TextStyle(font_size_px=200, padding_px=200, max_width_fraction=1.0),
        frame_width=1920,
        frame_height=1080,
        destination=tmp_path / "t.png",
        brand=channel.brand,
    )
    assert rendered.width <= 1920
    assert rendered.height <= 1080


def test_an_oversized_panel_is_refused_not_attempted(
    channel: ChannelConfig, tmp_path: Path
) -> None:
    """The pixel guard exists so a bad size fails fast instead of exhausting memory."""
    with pytest.raises(ValueError, match="pixel guard"):
        render_color_panel(
            width=40_000,
            height=40_000,
            fill_color="#000000",
            opacity=1.0,
            destination=tmp_path / "p.png",
            brand=channel.brand,
        )


def test_text_alignment_changes_the_pixels(channel: ChannelConfig, tmp_path: Path) -> None:
    from polymation.media.checksum import sha256_file

    digests = set()
    for align in (TextAlign.LEFT, TextAlign.CENTER, TextAlign.RIGHT):
        path = tmp_path / f"{align.value}.png"
        render_text_image(
            "short\na much longer line of text",
            TextStyle(font_size_px=32, align=align),
            frame_width=1920,
            frame_height=1080,
            destination=path,
            brand=channel.brand,
        )
        digests.add(sha256_file(path))
    assert len(digests) == 3


def test_text_rendering_is_deterministic(channel: ChannelConfig, tmp_path: Path) -> None:
    """Cache keys depend on this."""
    from polymation.media.checksum import sha256_file

    style = TextStyle(font_size_px=40, background_color="#101010", background_opacity=0.5)
    first = tmp_path / "a.png"
    second = tmp_path / "b.png"
    render_text_image(
        HOSTILE_TEXT,
        style,
        frame_width=1920,
        frame_height=1080,
        destination=first,
        brand=channel.brand,
    )
    render_text_image(
        HOSTILE_TEXT,
        style,
        frame_width=1920,
        frame_height=1080,
        destination=second,
        brand=channel.brand,
    )
    assert sha256_file(first) == sha256_file(second)


# --- colour panels ------------------------------------------------------------


def test_a_colour_panel_is_a_solid_fill(channel: ChannelConfig, tmp_path: Path) -> None:
    rendered = render_color_panel(
        width=120,
        height=60,
        fill_color="palette:vermilion",
        opacity=1.0,
        destination=tmp_path / "panel.png",
        brand=channel.brand,
    )
    assert (rendered.width, rendered.height) == (120, 60)
    with Image.open(rendered.path) as image:
        assert image.convert("RGBA").getpixel((60, 30)) == (201, 68, 54, 255)


def test_a_panel_with_bad_dimensions_is_refused(channel: ChannelConfig, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must be positive"):
        render_color_panel(
            width=0,
            height=10,
            fill_color="#000000",
            opacity=1.0,
            destination=tmp_path / "p.png",
            brand=channel.brand,
        )


# --- overlay placement --------------------------------------------------------


@pytest.mark.parametrize("anchor", list(Anchor))
def test_every_anchor_places_inside_the_frame(
    anchor: Anchor, channel: ChannelConfig, tmp_path: Path
) -> None:
    overlay = Overlay(
        overlay_id="o1",
        kind=OverlayKind.INFO_TEXT,
        span=FrameSpan(start_frame=0, duration_frames=30),
        anchor=anchor,
        text="placement",
        style=TextStyle(font_size_px=28),
    )
    placed = render_overlay(
        overlay,
        a_timeline(),
        FINAL_PROFILE,
        channel.brand,
        work_dir=tmp_path,
        image_paths={},
    )

    assert placed.x >= 0
    assert placed.y >= 0
    assert placed.x + placed.image.width <= FINAL_PROFILE.width
    assert placed.y + placed.image.height <= FINAL_PROFILE.height


def test_anchors_respect_the_safe_area(channel: ChannelConfig, tmp_path: Path) -> None:
    timeline = a_timeline()
    inset_x = int(timeline.safe_area_inset * FINAL_PROFILE.width)

    overlay = Overlay(
        overlay_id="o1",
        kind=OverlayKind.INFO_TEXT,
        span=FrameSpan(start_frame=0, duration_frames=30),
        anchor=Anchor.TOP_LEFT,
        text="safe",
        style=TextStyle(font_size_px=28),
    )
    placed = render_overlay(
        overlay, timeline, FINAL_PROFILE, channel.brand, work_dir=tmp_path, image_paths={}
    )
    assert placed.x == inset_x


def test_offsets_scale_between_profiles(channel: ChannelConfig, tmp_path: Path) -> None:
    """Preview must be a faithful stand-in, so placement scales with resolution."""
    timeline = a_timeline()
    overlay = Overlay(
        overlay_id="o1",
        kind=OverlayKind.LOWER_THIRD,
        span=FrameSpan(start_frame=0, duration_frames=30),
        anchor=Anchor.BOTTOM_LEFT,
        offset_y_px=-100,
        text="scaled",
        style=TextStyle(font_size_px=28),
    )

    final = render_overlay(
        overlay, timeline, FINAL_PROFILE, channel.brand, work_dir=tmp_path / "f", image_paths={}
    )
    preview = render_overlay(
        overlay,
        timeline,
        PREVIEW_PROFILE,
        channel.brand,
        work_dir=tmp_path / "p",
        image_paths={},
    )

    # The timeline is authored at 1920 wide; preview is half that.
    assert preview.y < final.y
    scale = PREVIEW_PROFILE.width / timeline.width
    assert scale == pytest.approx(0.5)


def test_overlay_timing_is_carried_through(channel: ChannelConfig, tmp_path: Path) -> None:
    overlay = Overlay(
        overlay_id="o1",
        kind=OverlayKind.TITLE,
        span=FrameSpan(start_frame=45, duration_frames=90),
        text="timed",
    )
    placed = render_overlay(
        overlay, a_timeline(), FINAL_PROFILE, channel.brand, work_dir=tmp_path, image_paths={}
    )
    assert placed.start_frame == 45
    assert placed.end_frame == 135


def test_an_image_overlay_is_resized_to_its_fraction(
    channel: ChannelConfig, tmp_path: Path
) -> None:
    source = tmp_path / "source.png"
    Image.new("RGB", (800, 600), (10, 20, 30)).save(source)

    overlay = Overlay(
        overlay_id="o1",
        kind=OverlayKind.IMAGE,
        span=FrameSpan(start_frame=0, duration_frames=30),
        image_asset_id="asset-0001",
        width_fraction=0.25,
    )
    placed = render_overlay(
        overlay,
        a_timeline(),
        FINAL_PROFILE,
        channel.brand,
        work_dir=tmp_path / "work",
        image_paths={"asset-0001": source},
    )

    assert placed.image.width == 480  # 0.25 * 1920
    # Aspect ratio preserved: 800x600 is 4:3, so 480 wide is 360 tall.
    assert placed.image.height == 360


def test_an_image_overlay_with_a_missing_asset_fails_loudly(
    channel: ChannelConfig, tmp_path: Path
) -> None:
    overlay = Overlay(
        overlay_id="o1",
        kind=OverlayKind.IMAGE,
        span=FrameSpan(start_frame=0, duration_frames=30),
        image_asset_id="asset-9999",
        width_fraction=0.2,
    )
    with pytest.raises(RenderError, match="not available on disk"):
        render_overlay(
            overlay,
            a_timeline(),
            FINAL_PROFILE,
            channel.brand,
            work_dir=tmp_path,
            image_paths={},
        )


def test_rendering_many_overlays_preserves_order(channel: ChannelConfig, tmp_path: Path) -> None:
    overlays = [
        Overlay(
            overlay_id=f"o{index}",
            kind=OverlayKind.INFO_TEXT,
            span=FrameSpan(start_frame=index, duration_frames=10),
            text=f"overlay {index}",
        )
        for index in range(4)
    ]
    placed = render_overlays(
        overlays,
        a_timeline(),
        FINAL_PROFILE,
        channel.brand,
        work_dir=tmp_path,
        image_paths={},
    )
    assert [item.overlay_id for item in placed] == ["o0", "o1", "o2", "o3"]


def test_a_colour_panel_overlay_uses_its_fractions(channel: ChannelConfig, tmp_path: Path) -> None:
    overlay = Overlay(
        overlay_id="panel",
        kind=OverlayKind.COLOR_PANEL,
        span=FrameSpan(start_frame=0, duration_frames=30),
        fill_color="palette:vermilion",
        width_fraction=0.5,
        height_fraction=0.25,
    )
    placed = render_overlay(
        overlay, a_timeline(), FINAL_PROFILE, channel.brand, work_dir=tmp_path, image_paths={}
    )
    assert placed.image.width == 960
    assert placed.image.height == 270
