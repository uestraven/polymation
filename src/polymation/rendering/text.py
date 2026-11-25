"""Rendering text to pixels with Pillow.

**This module is the reason arbitrary text is safe in this pipeline.** Text
never reaches FFmpeg. It is wrapped, measured and drawn into an RGBA image
here, and FFmpeg only ever receives that image's *path* as one element of an
argument vector. Apostrophes, colons, commas, percent signs, backslashes,
newlines, emoji and right-to-left scripts are pixels by the time FFmpeg sees
anything, so there is no filter-expression escaping to get wrong.

Compare the alternative: FFmpeg's ``drawtext`` requires escaping ``:`` ``'``
``\\`` ``%`` and more, with rules that differ between the filter-graph parser
and the filter itself. Getting that wrong on operator-supplied text is a
command-injection-shaped bug. Not doing it at all removes the class of bug.

See docs/DECISIONS.md D-016.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from polymation.config.channel import ChannelBrand
from polymation.rendering.colors import resolve_rgb, resolve_rgba
from polymation.rendering.fixtures import FontResolution, load_font, resolve_font
from polymation.schemas.timeline import TextAlign, TextStyle

#: Hard cap on a rendered text image, as a guard against a pathological style.
MAX_TEXT_IMAGE_PIXELS = 8192 * 8192

#: Characters treated as explicit line breaks in overlay text.
_LINE_BREAKS = ("\r\n", "\r", "\n")


@dataclass(frozen=True)
class RenderedText:
    """A text overlay rendered to an RGBA image on disk."""

    path: Path
    width: int
    height: int
    #: The lines actually drawn, after wrapping. Useful for assertions and for
    #: reporting when a caption was longer than its permitted line count.
    lines: tuple[str, ...]
    font: FontResolution
    #: Whether `max_lines` forced lines to be dropped. Reported so a caption
    #: that did not fit is visible rather than quietly shortened.
    truncated: bool = False

    @property
    def used_fallback_font(self) -> bool:
        """Whether the requested family was unavailable and substituted."""
        return self.font.is_fallback


def normalise_linebreaks(text: str) -> list[str]:
    """Split ``text`` on any line-break convention, preserving blank lines."""
    normalised = text
    for sequence in _LINE_BREAKS:
        normalised = normalised.replace(sequence, "\n")
    return normalised.split("\n")


def wrap_text(
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width_px: int,
) -> list[str]:
    """Wrap ``text`` to ``max_width_px``, respecting explicit line breaks.

    Words longer than the available width are broken mid-word rather than
    allowed to overflow the frame — a long URL or an unspaced CJK run must still
    stay inside the safe area.
    """
    if max_width_px <= 0:
        raise ValueError(f"max_width_px must be positive, got {max_width_px}")

    def measure(candidate: str) -> int:
        left, _, right, _ = font.getbbox(candidate)
        return right - left

    lines: list[str] = []
    for paragraph in normalise_linebreaks(text):
        if not paragraph.strip():
            lines.append("")
            continue

        current = ""
        for word in paragraph.split(" "):
            attempt = word if not current else f"{current} {word}"
            if measure(attempt) <= max_width_px:
                current = attempt
                continue

            if current:
                lines.append(current)
                current = ""

            # The word alone may still be too wide; break it by character.
            if measure(word) <= max_width_px:
                current = word
                continue

            chunk = ""
            for character in word:
                if measure(chunk + character) <= max_width_px or not chunk:
                    chunk += character
                else:
                    lines.append(chunk)
                    chunk = character
            current = chunk

        if current:
            lines.append(current)

    return lines or [""]


def render_text_image(
    text: str,
    style: TextStyle,
    *,
    frame_width: int,
    frame_height: int,
    destination: Path,
    brand: ChannelBrand | None = None,
    max_lines: int | None = None,
) -> RenderedText:
    """Render ``text`` to an RGBA PNG at ``destination``.

    The image is sized to its content plus padding, never to the whole frame, so
    compositing cost is proportional to the text rather than the canvas.

    Args:
        text: arbitrary text. No escaping is required or performed.
        style: typography and box styling.
        frame_width: output frame width, used to resolve ``max_width_fraction``.
        frame_height: output frame height, used only to bound the result.
        destination: where to write the PNG.
        brand: channel brand, for resolving ``palette:`` colours.
        max_lines: truncate to this many lines, appending an ellipsis. ``None``
            keeps every line.

    Raises:
        ValueError: if the resolved image would exceed the pixel guard.
    """
    resolution = resolve_font(style.font_family)
    font = load_font(resolution, style.font_size_px)

    available_width = max(1, int(frame_width * style.max_width_fraction) - 2 * style.padding_px)
    lines = wrap_text(text, font, available_width)

    truncated = max_lines is not None and len(lines) > max_lines
    if truncated:
        lines = lines[:max_lines]
        if lines:
            lines[-1] = lines[-1].rstrip() + "\u2026"

    ascent, descent = font.getmetrics()
    line_height = ascent + descent
    text_width = 0
    for line in lines:
        if not line:
            continue
        left, _, right, _ = font.getbbox(line)
        text_width = max(text_width, right - left)

    text_height = len(lines) * line_height + max(0, len(lines) - 1) * style.line_spacing_px

    box_width = min(frame_width, text_width + 2 * style.padding_px)
    box_height = min(frame_height, text_height + 2 * style.padding_px)
    box_width = max(1, box_width)
    box_height = max(1, box_height)

    if box_width * box_height > MAX_TEXT_IMAGE_PIXELS:
        raise ValueError(
            f"text overlay would render at {box_width}x{box_height}, beyond the "
            f"{MAX_TEXT_IMAGE_PIXELS}-pixel guard. Reduce font_size_px or "
            f"max_width_fraction."
        )

    background = (
        resolve_rgba(style.background_color, style.background_opacity, brand)
        if style.background_color is not None
        else (0, 0, 0, 0)
    )
    image = Image.new("RGBA", (box_width, box_height), background)
    draw = ImageDraw.Draw(image)
    fill = (*resolve_rgb(style.color, brand), 255)

    cursor_y = style.padding_px
    for line in lines:
        if line:
            left, _, right, _ = font.getbbox(line)
            line_width = right - left
            if style.align is TextAlign.CENTER:
                cursor_x = (box_width - line_width) // 2 - left
            elif style.align is TextAlign.RIGHT:
                cursor_x = box_width - style.padding_px - line_width - left
            else:
                cursor_x = style.padding_px - left
            draw.text((cursor_x, cursor_y), line, font=font, fill=fill)
        cursor_y += line_height + style.line_spacing_px

    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, format="PNG")

    return RenderedText(
        path=destination,
        width=box_width,
        height=box_height,
        lines=tuple(lines),
        font=resolution,
        truncated=truncated,
    )


def render_color_panel(
    *,
    width: int,
    height: int,
    fill_color: str,
    opacity: float,
    destination: Path,
    brand: ChannelBrand | None = None,
) -> RenderedText:
    """Render a solid colour panel as an RGBA PNG.

    Returned as a :class:`RenderedText` with no lines, so panels and text
    overlays share one compositing path.
    """
    if width <= 0 or height <= 0:
        raise ValueError(f"panel size must be positive, got {width}x{height}")
    if width * height > MAX_TEXT_IMAGE_PIXELS:
        raise ValueError(f"panel {width}x{height} exceeds the pixel guard")

    image = Image.new("RGBA", (width, height), resolve_rgba(fill_color, opacity, brand))
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, format="PNG")

    return RenderedText(
        path=destination,
        width=width,
        height=height,
        lines=(),
        font=FontResolution(requested_family="(none)", path=None, is_fallback=False),
    )
