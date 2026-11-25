"""Generating original synthetic video and marker audio with FFmpeg.

Everything here is drawn frame by frame with Pillow and encoded by FFmpeg, or
synthesised arithmetically. No third-party media, no FFmpeg pattern generator
whose provenance would need explaining, and no network access.

The **marker** helpers exist so audio/video synchronisation can be measured
rather than eyeballed: a visual marker is a bright block in a known corner on an
exactly known frame, and an audio marker is a short click at the exactly
corresponding sample. A test can then extract the frame at a marker time, assert
the block is there, measure the audio envelope around the same time, and assert
the click is there — which together detect drift in either direction.
"""

from __future__ import annotations

import math
import struct
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw

from polymation.errors import RenderError
from polymation.rendering.ffmpeg import require_ffmpeg, run_tool

#: Size of the visual sync marker, in pixels, at 1920x1080. Scaled for other
#: resolutions so it stays detectable after downscaling to preview.
MARKER_SIZE_FRACTION = 0.06

#: Colour of the visual sync marker: saturated, and not used elsewhere in the
#: fixture palette, so a test can detect it by colour distance alone.
MARKER_RGB = (255, 0, 255)

#: Peak amplitude of an audio marker click, as a fraction of full scale.
MARKER_CLICK_AMPLITUDE = 0.5

#: Click length in milliseconds. Long enough to survive AAC encoding, short
#: enough to localise in time.
MARKER_CLICK_MS = 40


@dataclass(frozen=True)
class SyntheticVideo:
    """A generated video fixture and its properties."""

    path: Path
    width: int
    height: int
    fps: int
    frames: int
    has_audio: bool

    @property
    def duration_seconds(self) -> float:
        """Length in seconds."""
        return self.frames / self.fps

    @property
    def aspect_ratio(self) -> float:
        """Source aspect ratio."""
        return self.width / self.height


def marker_box(width: int, height: int) -> tuple[int, int, int, int]:
    """Bounding box of the visual sync marker for a given frame size.

    Top-left corner, so it survives both ``contain`` padding (which adds bars at
    left/right or top/bottom but keeps the image centred) and a test that knows
    where the image landed.
    """
    size = max(8, int(min(width, height) * MARKER_SIZE_FRACTION))
    return (0, 0, size, size)


def draw_synthetic_frame(
    width: int,
    height: int,
    frame_index: int,
    total_frames: int,
    *,
    palette: tuple[tuple[int, int, int], ...],
    marker: bool = False,
) -> Image.Image:
    """Draw one frame of an original synthetic clip.

    Content: a moving bar whose position encodes the frame index, a static
    quadrant grid for aspect checks, and a progress strip. Entirely geometric.
    """
    background, accent, light, warn = palette
    image = Image.new("RGB", (width, height), background)
    draw = ImageDraw.Draw(image)

    # Quadrant guides: a square at the centre stays square only if the aspect
    # ratio survives the pipeline.
    centre_x, centre_y = width // 2, height // 2
    half = max(8, int(min(width, height) * 0.18))
    draw.rectangle(
        [(centre_x - half, centre_y - half), (centre_x + half, centre_y + half)],
        outline=light,
        width=max(2, height // 270),
    )
    draw.line([(centre_x, 0), (centre_x, height)], fill=accent, width=max(1, height // 540))
    draw.line([(0, centre_y), (width, centre_y)], fill=accent, width=max(1, height // 540))

    # Moving bar: horizontal position is a linear function of the frame index,
    # so a test can tell which frame it is looking at.
    span = max(1, total_frames - 1)
    position = int((width - width // 12) * frame_index / span)
    draw.rectangle(
        [(position, int(height * 0.78)), (position + width // 12, int(height * 0.9))],
        fill=warn,
    )

    # Progress strip along the bottom edge.
    draw.rectangle(
        [(0, height - max(4, height // 108)), (int(width * frame_index / span), height)],
        fill=accent,
    )

    if marker:
        draw.rectangle(marker_box(width, height), fill=MARKER_RGB)

    return image


def generate_synthetic_video(
    destination: Path,
    *,
    width: int,
    height: int,
    fps: int,
    frames: int,
    palette: tuple[tuple[int, int, int], ...],
    with_audio: bool = False,
    marker_frames: tuple[int, ...] = (),
    audio_sample_rate: int = 48_000,
) -> SyntheticVideo:
    """Render an original synthetic video fixture.

    Frames are drawn with Pillow into a temporary directory and encoded by
    FFmpeg from a numbered sequence, so the source is unambiguously ours.

    Args:
        destination: output path. The container is inferred from the suffix.
        width, height: source geometry. Use a non-16:9 value to exercise
            aspect-ratio preservation.
        fps: source frame rate. Use something other than 30 to exercise frame
            rate normalisation.
        frames: number of frames to generate.
        palette: four RGB tuples (background, accent, light, warn).
        with_audio: mux a synthetic tone, with clicks on ``marker_frames``.
        marker_frames: frames carrying a visual marker (and audio click).

    Raises:
        RenderError: if encoding fails.
        MissingDependencyError: if ffmpeg is unavailable.
    """
    if frames < 1:
        raise ValueError(f"frames must be positive, got {frames}")
    if width % 2 or height % 2:
        raise ValueError(f"H.264 yuv420p needs even dimensions; got {width}x{height}")

    ffmpeg = require_ffmpeg()
    if ffmpeg.path is None:  # pragma: no cover - require_ffmpeg guarantees this
        raise RenderError("ffmpeg has no resolved path")

    destination.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="polymation-synth-") as scratch:
        scratch_dir = Path(scratch)
        for index in range(frames):
            frame = draw_synthetic_frame(
                width,
                height,
                index,
                frames,
                palette=palette,
                marker=index in marker_frames,
            )
            frame.save(scratch_dir / f"frame-{index:05d}.png", format="PNG")

        arguments: list[str] = [
            "-nostdin",
            "-hide_banner",
            "-y",
            "-framerate",
            str(fps),
            "-i",
            str(scratch_dir / "frame-%05d.png"),
        ]

        if with_audio:
            audio_path = scratch_dir / "audio.wav"
            marker_samples = tuple(int(index * audio_sample_rate / fps) for index in marker_frames)
            generate_marker_audio(
                audio_path,
                duration_seconds=frames / fps,
                sample_rate=audio_sample_rate,
                marker_samples=marker_samples,
            )
            arguments += ["-i", str(audio_path)]

        arguments += [
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(fps),
        ]
        if with_audio:
            arguments += ["-c:a", "aac", "-ar", str(audio_sample_rate), "-ac", "2", "-shortest"]
        arguments.append(str(destination))

        result = run_tool(ffmpeg.path, arguments)
        if not result.ok:
            raise RenderError(
                f"Encoding the synthetic fixture failed (status {result.returncode}):\n"
                f"{result.tail()}"
            )

    if not destination.is_file() or destination.stat().st_size == 0:
        raise RenderError(f"synthetic fixture {destination} was not produced")

    return SyntheticVideo(
        path=destination,
        width=width,
        height=height,
        fps=fps,
        frames=frames,
        has_audio=with_audio,
    )


def generate_marker_audio(
    destination: Path,
    *,
    duration_seconds: float,
    sample_rate: int = 48_000,
    marker_samples: tuple[int, ...] = (),
    base_frequency: float = 220.0,
    base_amplitude: float = 0.05,
    channels: int = 2,
) -> Path:
    """Synthesise a quiet tone with loud, short clicks at known sample offsets.

    The clicks are the audio half of the synchronisation measurement. They are
    much louder than the bed so an envelope measurement localises them
    unambiguously, and short enough that their position is precise.
    """
    if duration_seconds <= 0:
        raise ValueError(f"duration_seconds must be positive, got {duration_seconds}")
    if not 0 < base_amplitude <= 1:
        raise ValueError(f"base_amplitude must be in (0, 1], got {base_amplitude}")

    total = round(duration_seconds * sample_rate)
    click_length = max(1, int(sample_rate * MARKER_CLICK_MS / 1000))
    full_scale = 2**15 - 1

    click_starts = set(marker_samples)
    click_ranges = [(start, start + click_length) for start in sorted(click_starts)]

    samples = bytearray()
    for index in range(total):
        value = math.sin(2 * math.pi * base_frequency * index / sample_rate)
        amplitude = base_amplitude

        for start, end in click_ranges:
            if start <= index < end:
                # A short decaying burst at a different frequency, so it is
                # distinguishable from the bed by ear as well as by envelope.
                progress = (index - start) / click_length
                amplitude = MARKER_CLICK_AMPLITUDE * (1.0 - progress)
                value = math.sin(2 * math.pi * 1760.0 * index / sample_rate)
                break

        scaled = int(max(-1.0, min(1.0, value * amplitude)) * full_scale)
        samples += struct.pack("<h", scaled) * channels

    destination.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(destination), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(bytes(samples))

    return destination


def detect_marker(image: Image.Image, *, tolerance: int = 60) -> bool:
    """Whether the visual sync marker is present in a frame's top-left corner.

    Samples the centre of the expected marker box and compares against
    :data:`MARKER_RGB` with a tolerance, because lossy encoding and chroma
    subsampling shift the exact value.
    """
    rgb = image.convert("RGB")
    left, top, right, bottom = marker_box(rgb.width, rgb.height)
    sample_x = (left + right) // 2
    sample_y = (top + bottom) // 2
    pixel = rgb.getpixel((sample_x, sample_y))
    return all(
        abs(actual - expected) <= tolerance
        for actual, expected in zip(pixel, MARKER_RGB, strict=True)
    )


def audio_envelope(path: Path, *, sample_rate: int = 48_000, window_ms: int = 20) -> list[float]:
    """Per-window peak amplitude of a WAV file, normalised to ``[0, 1]``.

    Used to locate audio markers. Reads with the standard library, so it does
    not depend on FFmpeg and cannot be confounded by a decode difference.
    """
    with wave.open(str(path), "rb") as handle:
        if handle.getsampwidth() != 2:
            raise ValueError(
                f"{path.name} is {handle.getsampwidth() * 8}-bit; this helper reads 16-bit PCM"
            )
        channels = handle.getnchannels()
        actual_rate = handle.getframerate()
        frames = handle.getnframes()
        raw = handle.readframes(frames)

    if actual_rate != sample_rate:
        raise ValueError(f"{path.name} is {actual_rate} Hz but {sample_rate} Hz was expected")

    values = struct.unpack(f"<{len(raw) // 2}h", raw)
    window = max(1, int(sample_rate * window_ms / 1000)) * channels
    full_scale = 2**15 - 1

    envelope: list[float] = []
    for start in range(0, len(values), window):
        chunk = values[start : start + window]
        if not chunk:
            break
        envelope.append(max(abs(sample) for sample in chunk) / full_scale)
    return envelope
