"""Original fixture generation: images, audio and honest font reporting."""

from __future__ import annotations

import wave
from pathlib import Path

import pytest
from PIL import Image

from polymation.config.channel import ChannelConfig
from polymation.rendering.fixtures import (
    DEMO_AUDIO_CHANNELS,
    DEMO_AUDIO_SAMPLE_RATE,
    DEMO_HEIGHT,
    DEMO_WIDTH,
    TEST_IMAGE_HEIGHT,
    TEST_IMAGE_WIDTH,
    FontResolution,
    brand_font_report,
    find_font_file,
    generate_closing_card,
    generate_test_pattern,
    generate_title_card,
    generate_tone,
    resolve_font,
)

# --- fonts ---------------------------------------------------------------------


def test_a_missing_font_family_resolves_to_a_declared_fallback() -> None:
    resolution = resolve_font("ThisFontDefinitelyDoesNotExistAnywhere")
    assert resolution.path is None
    assert resolution.is_fallback
    assert "NOT the brand font" in resolution.description


def test_an_absent_font_is_reported_as_absent() -> None:
    assert find_font_file("ThisFontDefinitelyDoesNotExistAnywhere") is None


def test_an_empty_family_name_resolves_to_nothing() -> None:
    assert find_font_file("") is None


def test_brand_font_report_covers_every_proposed_font(channel: ChannelConfig) -> None:
    report = brand_font_report(channel.brand)
    families = {resolution.requested_family for resolution in report}
    assert families == {"Space Grotesk", "IBM Plex Serif", "Silkscreen"}


def test_a_resolved_font_does_not_claim_to_be_a_fallback(tmp_path: Path) -> None:
    resolution = FontResolution(
        requested_family="Example", path=tmp_path / "Example.ttf", is_fallback=False
    )
    assert "NOT the brand font" not in resolution.description


# --- images --------------------------------------------------------------------


def test_title_card_is_full_hd_and_uses_brand_colours(
    tmp_path: Path, channel: ChannelConfig
) -> None:
    path = generate_title_card(
        tmp_path / "title.png",
        channel.brand,
        primary_font=resolve_font("Space Grotesk"),
        accent_font=resolve_font("IBM Plex Serif"),
    )

    with Image.open(path) as image:
        assert image.size == (DEMO_WIDTH, DEMO_HEIGHT)
        assert image.format == "PNG"
        # Top-left corner is background: deep indigo from the brand palette.
        assert image.convert("RGB").getpixel((5, 5)) == channel.brand.palette.as_rgb("deep_indigo")


def test_test_pattern_is_four_by_three(tmp_path: Path, channel: ChannelConfig) -> None:
    """Scene 2 must not be 16:9 — that is the whole point of the fit test."""
    path = generate_test_pattern(tmp_path / "pattern.png", channel.brand)

    with Image.open(path) as image:
        assert image.size == (TEST_IMAGE_WIDTH, TEST_IMAGE_HEIGHT)
        assert image.width / image.height == pytest.approx(4 / 3)


def test_test_pattern_uses_only_palette_colours(tmp_path: Path, channel: ChannelConfig) -> None:
    """No colour may appear that is not a declared brand colour."""
    path = generate_test_pattern(tmp_path / "pattern.png", channel.brand)
    palette = {
        channel.brand.palette.as_rgb(name)
        for name in ("crt_amber", "deep_indigo", "parchment_cream", "vermilion")
    }

    with Image.open(path) as image:
        colours = {colour for _, colour in image.convert("RGB").getcolors(maxcolors=1 << 16)}

    assert colours <= palette


def test_closing_card_is_full_hd_with_a_vermilion_accent(
    tmp_path: Path, channel: ChannelConfig
) -> None:
    path = generate_closing_card(
        tmp_path / "closing.png", channel.brand, primary_font=resolve_font("Space Grotesk")
    )

    with Image.open(path) as image:
        assert image.size == (DEMO_WIDTH, DEMO_HEIGHT)
        # The left accent bar is vermilion.
        assert image.convert("RGB").getpixel((5, DEMO_HEIGHT // 2)) == (
            channel.brand.palette.as_rgb("vermilion")
        )


def test_generated_images_are_not_blank(tmp_path: Path, channel: ChannelConfig) -> None:
    path = generate_title_card(
        tmp_path / "title.png",
        channel.brand,
        primary_font=resolve_font("Space Grotesk"),
        accent_font=resolve_font("IBM Plex Serif"),
    )
    with Image.open(path) as image:
        colours = image.convert("RGB").getcolors(maxcolors=1 << 16)
    assert colours is not None
    assert len(colours) > 1, "the title card rendered as a solid fill — no text was drawn"


def test_fixture_generation_creates_missing_directories(
    tmp_path: Path, channel: ChannelConfig
) -> None:
    target = tmp_path / "deep" / "nested" / "pattern.png"
    assert generate_test_pattern(target, channel.brand).is_file()


# --- audio ---------------------------------------------------------------------


def test_tone_is_48khz_stereo_16_bit(tmp_path: Path) -> None:
    path = generate_tone(tmp_path / "tone.wav", duration_seconds=1.0)

    with wave.open(str(path), "rb") as handle:
        assert handle.getframerate() == DEMO_AUDIO_SAMPLE_RATE
        assert handle.getnchannels() == DEMO_AUDIO_CHANNELS
        assert handle.getsampwidth() == 2
        assert handle.getnframes() == DEMO_AUDIO_SAMPLE_RATE


def test_tone_duration_matches_the_request(tmp_path: Path) -> None:
    path = generate_tone(tmp_path / "tone.wav", duration_seconds=15.0)

    with wave.open(str(path), "rb") as handle:
        duration = handle.getnframes() / handle.getframerate()
    assert duration == pytest.approx(15.0, abs=0.01)


def test_tone_is_audible_but_quiet(tmp_path: Path) -> None:
    """A silent file would make the audio half of the demonstration meaningless."""
    import struct

    path = generate_tone(tmp_path / "tone.wav", duration_seconds=1.0)
    with wave.open(str(path), "rb") as handle:
        frames = handle.readframes(handle.getnframes())

    samples = struct.unpack(f"<{len(frames) // 2}h", frames)
    peak = max(abs(sample) for sample in samples)

    assert peak > 0, "the tone is silent"
    # Comfortably below full scale (32767), matching TONE_PEAK_AMPLITUDE of 0.08.
    assert peak < 0.15 * 32767


def test_tone_starts_and_ends_near_silence(tmp_path: Path) -> None:
    """The fade envelope must prevent a click at each join."""
    import struct

    path = generate_tone(tmp_path / "tone.wav", duration_seconds=3.0)
    with wave.open(str(path), "rb") as handle:
        frames = handle.readframes(handle.getnframes())

    samples = struct.unpack(f"<{len(frames) // 2}h", frames)
    assert abs(samples[0]) < 10
    assert abs(samples[-1]) < 10


@pytest.mark.parametrize("duration", [0, -1.0])
def test_tone_rejects_a_non_positive_duration(tmp_path: Path, duration: float) -> None:
    with pytest.raises(ValueError, match="must be positive"):
        generate_tone(tmp_path / "tone.wav", duration_seconds=duration)


@pytest.mark.parametrize("amplitude", [0, -0.1, 1.5])
def test_tone_rejects_an_out_of_range_amplitude(tmp_path: Path, amplitude: float) -> None:
    with pytest.raises(ValueError, match="peak_amplitude"):
        generate_tone(tmp_path / "tone.wav", duration_seconds=1.0, peak_amplitude=amplitude)


def test_tone_rejects_an_empty_frequency_list(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="at least one frequency"):
        generate_tone(tmp_path / "tone.wav", duration_seconds=1.0, frequencies_hz=())


def test_fixture_generation_is_deterministic(tmp_path: Path, channel: ChannelConfig) -> None:
    """Identical inputs must produce identical bytes, so checksums are stable."""
    from polymation.media.checksum import sha256_file

    first = generate_test_pattern(tmp_path / "a.png", channel.brand)
    second = generate_test_pattern(tmp_path / "b.png", channel.brand)
    assert sha256_file(first) == sha256_file(second)

    tone_a = generate_tone(tmp_path / "a.wav", duration_seconds=1.0)
    tone_b = generate_tone(tmp_path / "b.wav", duration_seconds=1.0)
    assert sha256_file(tone_a) == sha256_file(tone_b)
