"""Programmatic generation of original fixture media.

Everything produced here is generated from code in this repository: solid
fills, geometric primitives, text drawn with locally installed fonts, and a
synthesised sine tone. No third-party artwork, game footage, anime footage,
commercial music or downloaded asset is used or required, and nothing here
touches the network.
"""

from __future__ import annotations

import math
import struct
import wave
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from polymation.config.channel import ChannelBrand

#: Directories searched for installed TrueType/OpenType fonts.
FONT_SEARCH_DIRECTORIES: tuple[Path, ...] = (
    Path("/usr/share/fonts"),
    Path("/usr/local/share/fonts"),
    Path("/usr/share/fonts/truetype"),
    Path.home() / ".fonts",
    Path.home() / ".local/share/fonts",
    Path("/Library/Fonts"),
    Path("/System/Library/Fonts"),
)

FONT_EXTENSIONS = frozenset({".ttf", ".otf", ".ttc"})

#: Output specification for the technical demonstration.
DEMO_WIDTH = 1920
DEMO_HEIGHT = 1080
DEMO_FPS = 30
DEMO_AUDIO_SAMPLE_RATE = 48_000
DEMO_AUDIO_CHANNELS = 2
DEMO_AUDIO_SAMPLE_WIDTH_BYTES = 2

#: The 4:3 test image is deliberately *not* 16:9, to prove the renderer fits
#: archival aspect ratios into the output frame without distorting them.
TEST_IMAGE_WIDTH = 640
TEST_IMAGE_HEIGHT = 480

#: Peak amplitude of the synthetic tone as a fraction of full scale. Kept low so
#: the fixture is audible but quiet.
TONE_PEAK_AMPLITUDE = 0.08

#: Frequencies of the two-note original test signal, in hertz.
TONE_FREQUENCIES_HZ: tuple[float, ...] = (196.0, 261.63, 196.0)


@dataclass(frozen=True)
class FontResolution:
    """Which font was actually used for a piece of text, and whether it is the
    brand font or a fallback.

    The distinction is recorded explicitly so no render can claim brand fidelity
    while silently substituting a different typeface.
    """

    requested_family: str
    path: Path | None
    is_fallback: bool

    @property
    def description(self) -> str:
        """Human-readable summary for CLI output and render reports."""
        if self.path is None:
            return f"{self.requested_family} -> Pillow built-in fallback (NOT the brand font)"
        if self.is_fallback:
            return f"{self.requested_family} -> {self.path.name} (fallback, NOT the brand font)"
        return f"{self.requested_family} -> {self.path}"


def _normalise(name: str) -> str:
    return "".join(character for character in name.lower() if character.isalnum())


def find_font_file(family: str) -> Path | None:
    """Return the path of an installed font file matching ``family``, or ``None``."""
    target = _normalise(family)
    if not target:
        return None

    for directory in FONT_SEARCH_DIRECTORIES:
        if not directory.is_dir():
            continue
        try:
            candidates = sorted(directory.rglob("*"))
        except OSError:
            continue
        for candidate in candidates:
            if candidate.suffix.lower() not in FONT_EXTENSIONS:
                continue
            if target in _normalise(candidate.stem):
                return candidate
    return None


def resolve_font(family: str) -> FontResolution:
    """Resolve ``family`` to an installed font file, reporting fallback honestly."""
    found = find_font_file(family)
    return FontResolution(requested_family=family, path=found, is_fallback=found is None)


def load_font(resolution: FontResolution, size: int) -> ImageFont.FreeTypeFont:
    """Load a Pillow font at ``size`` for ``resolution``.

    Falls back to Pillow's bundled font when the requested family is absent. The
    fallback is recorded in ``resolution``, never hidden.
    """
    if resolution.path is not None:
        try:
            return ImageFont.truetype(str(resolution.path), size=size)
        except OSError:
            pass
    return ImageFont.load_default(size=size)


def brand_font_report(brand: ChannelBrand) -> list[FontResolution]:
    """Resolve every font the brand proposes, for diagnostics."""
    return [resolve_font(font.family) for font in brand.typography.proposed_fonts]


def _draw_centred_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    *,
    centre_x: int,
    centre_y: int,
    fill: tuple[int, int, int],
) -> None:
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    width = right - left
    height = bottom - top
    draw.text(
        (centre_x - width / 2 - left, centre_y - height / 2 - top),
        text,
        font=font,
        fill=fill,
    )


def generate_title_card(
    path: Path,
    brand: ChannelBrand,
    *,
    primary_font: FontResolution,
    accent_font: FontResolution,
) -> Path:
    """Draw the Polymation title card: indigo field, parchment and amber type."""
    background = brand.palette.as_rgb("deep_indigo")
    title_colour = brand.palette.as_rgb("parchment_cream")
    accent_colour = brand.palette.as_rgb("crt_amber")

    image = Image.new("RGB", (DEMO_WIDTH, DEMO_HEIGHT), background)
    draw = ImageDraw.Draw(image)

    title = load_font(primary_font, 150)
    tagline = load_font(accent_font, 54)

    _draw_centred_text(
        draw,
        brand.name.upper(),
        title,
        centre_x=DEMO_WIDTH // 2,
        centre_y=int(DEMO_HEIGHT * 0.42),
        fill=title_colour,
    )

    # Thin amber rule beneath the wordmark.
    rule_half_width = int(DEMO_WIDTH * 0.19)
    rule_y = int(DEMO_HEIGHT * 0.545)
    draw.rectangle(
        [
            (DEMO_WIDTH // 2 - rule_half_width, rule_y),
            (DEMO_WIDTH // 2 + rule_half_width, rule_y + 4),
        ],
        fill=accent_colour,
    )

    _draw_centred_text(
        draw,
        brand.tagline,
        tagline,
        centre_x=DEMO_WIDTH // 2,
        centre_y=int(DEMO_HEIGHT * 0.62),
        fill=accent_colour,
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG")
    return path


def generate_test_pattern(path: Path, brand: ChannelBrand) -> Path:
    """Draw an original 4:3 geometric test pattern.

    Pixel-art-inspired construction: a coarse checkerboard, palette colour bars,
    concentric frames and centre crosshairs. All geometry, no imagery.
    """
    indigo = brand.palette.as_rgb("deep_indigo")
    amber = brand.palette.as_rgb("crt_amber")
    cream = brand.palette.as_rgb("parchment_cream")
    vermilion = brand.palette.as_rgb("vermilion")

    image = Image.new("RGB", (TEST_IMAGE_WIDTH, TEST_IMAGE_HEIGHT), indigo)
    draw = ImageDraw.Draw(image)

    centre_x = TEST_IMAGE_WIDTH // 2
    centre_y = TEST_IMAGE_HEIGHT // 2

    # Band 1 — coarse 32px checkerboard across the top, reads as pixel blocks.
    cell = 32
    checker_bottom = cell * 4
    for row in range(0, checker_bottom, cell):
        for column in range(0, TEST_IMAGE_WIDTH, cell):
            if ((row // cell) + (column // cell)) % 2 == 0:
                draw.rectangle([(column, row), (column + cell - 1, row + cell - 1)], fill=amber)

    # Band 3 — palette colour bars across the bottom.
    bar_height = 96
    bar_top = TEST_IMAGE_HEIGHT - bar_height
    bars = (amber, cream, vermilion, indigo)
    bar_width = TEST_IMAGE_WIDTH // len(bars)
    for index, colour in enumerate(bars):
        right = (index + 1) * bar_width - 1 if index < len(bars) - 1 else TEST_IMAGE_WIDTH - 1
        draw.rectangle([(index * bar_width, bar_top), (right, TEST_IMAGE_HEIGHT - 1)], fill=colour)
    draw.line([(0, bar_top), (TEST_IMAGE_WIDTH, bar_top)], fill=cream, width=2)

    # Band 2, centre — concentric *squares*. Squares are the useful geometry
    # here: any horizontal or vertical stretch anywhere in the pipeline shows up
    # immediately as a rectangle.
    for index, half in enumerate((40, 68, 96)):
        draw.rectangle(
            [(centre_x - half, centre_y - half), (centre_x + half, centre_y + half)],
            outline=cream if index % 2 == 0 else amber,
            width=3,
        )

    # Centre crosshair, confined to the middle band so it does not cut the bars.
    crosshair_half = 120
    draw.line(
        [(centre_x, centre_y - crosshair_half), (centre_x, centre_y + crosshair_half)],
        fill=vermilion,
        width=2,
    )
    draw.line(
        [(centre_x - crosshair_half, centre_y), (centre_x + crosshair_half, centre_y)],
        fill=vermilion,
        width=2,
    )

    # Corner markers: equal-sided brackets, so cropping or padding is obvious.
    bracket = 40
    for corner_x, corner_y, step_x, step_y in (
        (0, 0, 1, 1),
        (TEST_IMAGE_WIDTH - 1, 0, -1, 1),
        (0, TEST_IMAGE_HEIGHT - 1, 1, -1),
        (TEST_IMAGE_WIDTH - 1, TEST_IMAGE_HEIGHT - 1, -1, -1),
    ):
        draw.line(
            [(corner_x, corner_y), (corner_x + step_x * bracket, corner_y)],
            fill=vermilion,
            width=4,
        )
        draw.line(
            [(corner_x, corner_y), (corner_x, corner_y + step_y * bracket)],
            fill=vermilion,
            width=4,
        )

    # Single outer frame, one pixel in from each edge.
    draw.rectangle([(1, 1), (TEST_IMAGE_WIDTH - 2, TEST_IMAGE_HEIGHT - 2)], outline=cream, width=2)

    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG")
    return path


def generate_closing_card(
    path: Path,
    brand: ChannelBrand,
    *,
    primary_font: FontResolution,
    headline: str = "TECHNICAL DEMONSTRATION",
    # ASCII only: Pillow's fallback font has no glyph for an em dash, and a
    # missing glyph renders as a tofu box.
    subline: str = "Original synthetic fixtures - not a channel intro",
) -> Path:
    """Draw the closing graphic: simple type on indigo with a vermilion accent."""
    background = brand.palette.as_rgb("deep_indigo")
    vermilion = brand.palette.as_rgb("vermilion")
    cream = brand.palette.as_rgb("parchment_cream")

    image = Image.new("RGB", (DEMO_WIDTH, DEMO_HEIGHT), background)
    draw = ImageDraw.Draw(image)

    # Vermilion accent block down the left edge.
    draw.rectangle([(0, 0), (int(DEMO_WIDTH * 0.035), DEMO_HEIGHT)], fill=vermilion)

    headline_font = load_font(primary_font, 88)
    subline_font = load_font(primary_font, 40)

    _draw_centred_text(
        draw,
        headline,
        headline_font,
        centre_x=DEMO_WIDTH // 2,
        centre_y=int(DEMO_HEIGHT * 0.46),
        fill=cream,
    )
    _draw_centred_text(
        draw,
        subline,
        subline_font,
        centre_x=DEMO_WIDTH // 2,
        centre_y=int(DEMO_HEIGHT * 0.58),
        fill=vermilion,
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG")
    return path


def generate_tone(
    path: Path,
    *,
    duration_seconds: float,
    sample_rate: int = DEMO_AUDIO_SAMPLE_RATE,
    peak_amplitude: float = TONE_PEAK_AMPLITUDE,
    frequencies_hz: tuple[float, ...] = TONE_FREQUENCIES_HZ,
) -> Path:
    """Synthesise an original, quiet audio test signal as a 16-bit stereo WAV.

    One sine note per entry in ``frequencies_hz``, each with a short raised-cosine
    fade so there are no clicks at the joins. Generated entirely from arithmetic:
    no sample library, no recording, no downloaded audio.
    """
    if duration_seconds <= 0:
        raise ValueError("duration_seconds must be positive")
    if not 0 < peak_amplitude <= 1:
        raise ValueError("peak_amplitude must be in (0, 1]")
    if not frequencies_hz:
        raise ValueError("at least one frequency is required")

    total_frames = round(duration_seconds * sample_rate)
    note_frames = total_frames // len(frequencies_hz)
    fade_frames = max(1, int(sample_rate * 0.05))
    full_scale = 2 ** (8 * DEMO_AUDIO_SAMPLE_WIDTH_BYTES - 1) - 1

    samples = bytearray()
    for index in range(total_frames):
        note_index = min(index // note_frames, len(frequencies_hz) - 1)
        frequency = frequencies_hz[note_index]
        position_in_note = index - note_index * note_frames

        envelope = 1.0
        if position_in_note < fade_frames:
            envelope = 0.5 * (1 - math.cos(math.pi * position_in_note / fade_frames))
        remaining = note_frames - position_in_note
        if remaining < fade_frames:
            envelope = min(envelope, 0.5 * (1 - math.cos(math.pi * remaining / fade_frames)))

        value = math.sin(2 * math.pi * frequency * index / sample_rate)
        scaled = int(value * envelope * peak_amplitude * full_scale)
        samples += struct.pack("<hh", scaled, scaled)

    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(DEMO_AUDIO_CHANNELS)
        handle.setsampwidth(DEMO_AUDIO_SAMPLE_WIDTH_BYTES)
        handle.setframerate(sample_rate)
        handle.writeframes(bytes(samples))

    return path
