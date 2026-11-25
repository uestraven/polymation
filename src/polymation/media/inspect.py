"""Media inspection: determine what a file actually is.

File extensions are never trusted as evidence of format. Images are opened and
verified with Pillow; audio and video are described by ffprobe. A file whose
extension disagrees with its content is reported by its detected content, and a
file that neither tool can read is rejected rather than guessed at.
"""

from __future__ import annotations

import wave
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from polymation.errors import MediaInspectionError, MissingDependencyError
from polymation.rendering.ffmpeg import ffprobe_json, find_ffprobe
from polymation.schemas.assets import AssetType

#: Pillow format name to MIME type, for the formats we accept as stills.
_IMAGE_MIME_TYPES: dict[str, str] = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "WEBP": "image/webp",
    "GIF": "image/gif",
    "BMP": "image/bmp",
    "TIFF": "image/tiff",
}

#: Container/codec hints to MIME types for time-based media.
_CONTAINER_MIME_TYPES: dict[str, str] = {
    "mov,mp4,m4a,3gp,3g2,mj2": "video/mp4",
    "matroska,webm": "video/webm",
    "avi": "video/x-msvideo",
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "flac": "audio/flac",
    "ogg": "audio/ogg",
    "aac": "audio/aac",
}

#: Pillow refuses very large images by default as a decompression-bomb guard.
#: Keep that guard; it is the correct behaviour for untrusted input.
MAX_IMAGE_PIXELS = Image.MAX_IMAGE_PIXELS


@dataclass(frozen=True)
class MediaProbe:
    """What inspection determined about one file."""

    asset_type: AssetType
    detected_format: str
    mime_type: str | None
    size_bytes: int
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    audio_sample_rate: int | None = None
    frame_rate: float | None = None


def _inspect_image(path: Path) -> MediaProbe | None:
    """Return an image probe, or ``None`` if the file is not a readable image."""
    try:
        with Image.open(path) as image:
            image.verify()
        # verify() consumes the file object, so reopen to read properties.
        with Image.open(path) as image:
            width, height = image.size
            image_format = (image.format or "UNKNOWN").upper()
    except (UnidentifiedImageError, SyntaxError):
        return None
    except Image.DecompressionBombError as exc:
        raise MediaInspectionError(
            f"{path.name} declares an image larger than Pillow's safety limit "
            f"({MAX_IMAGE_PIXELS} pixels) and was refused: {exc}"
        ) from exc
    except OSError as exc:
        # A truncated or corrupt image that Pillow recognised but cannot decode.
        raise MediaInspectionError(
            f"{path.name} looks like an image but could not be decoded: {exc}"
        ) from exc

    if width <= 0 or height <= 0:
        raise MediaInspectionError(f"{path.name} reports non-positive dimensions.")

    return MediaProbe(
        asset_type=AssetType.IMAGE,
        detected_format=image_format,
        mime_type=_IMAGE_MIME_TYPES.get(image_format),
        size_bytes=path.stat().st_size,
        width=width,
        height=height,
    )


def _inspect_wav(path: Path) -> MediaProbe | None:
    """Return a WAV probe via the standard library, or ``None`` if not a WAV.

    This parses the RIFF header rather than trusting the extension, so it is
    genuine content inspection. It exists so that uncompressed audio — including
    the fixtures this project generates — can be imported on a machine without
    FFmpeg, and so that the result is identical whether or not FFmpeg is present.
    """
    try:
        with wave.open(str(path), "rb") as handle:
            frame_count = handle.getnframes()
            sample_rate = handle.getframerate()
            channels = handle.getnchannels()
    except (wave.Error, EOFError, OSError):
        return None

    if sample_rate <= 0 or frame_count <= 0 or channels <= 0:
        return None

    return MediaProbe(
        asset_type=AssetType.AUDIO,
        detected_format="wav",
        mime_type="audio/wav",
        size_bytes=path.stat().st_size,
        duration_seconds=frame_count / sample_rate,
        audio_codec="pcm",
        audio_sample_rate=sample_rate,
    )


def _parse_frame_rate(raw: object) -> float | None:
    if not isinstance(raw, str) or "/" not in raw:
        return None
    numerator, _, denominator = raw.partition("/")
    try:
        denominator_value = float(denominator)
        if denominator_value == 0:
            return None
        return float(numerator) / denominator_value
    except ValueError:
        return None


def _positive_float(raw: object) -> float | None:
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _inspect_timebased(path: Path) -> MediaProbe:
    """Return a video or audio probe using ffprobe.

    Raises:
        MissingDependencyError: if ffprobe is unavailable.
        MediaInspectionError: if ffprobe cannot describe the file, or the file
            holds no usable media stream.
    """
    try:
        payload = ffprobe_json(path)
    except MissingDependencyError:
        raise
    except ValueError as exc:
        raise MediaInspectionError(
            f"{path.name} is not a readable image, and ffprobe could not describe it "
            f"either. The file is corrupt or of an unsupported format. ({exc})"
        ) from exc

    streams = payload.get("streams")
    container = payload.get("format", {})
    streams = streams if isinstance(streams, list) else []

    video_streams = [
        stream
        for stream in streams
        if isinstance(stream, dict)
        and stream.get("codec_type") == "video"
        # Cover art inside an audio file is carried as a still video stream.
        and stream.get("disposition", {}).get("attached_pic") != 1
    ]
    audio_streams = [
        stream
        for stream in streams
        if isinstance(stream, dict) and stream.get("codec_type") == "audio"
    ]

    if not video_streams and not audio_streams:
        raise MediaInspectionError(
            f"{path.name} contains no video or audio stream that ffprobe can use."
        )

    format_name = str(container.get("format_name") or "unknown")
    duration = _positive_float(container.get("duration"))

    audio_codec = None
    sample_rate = None
    if audio_streams:
        audio_codec = str(audio_streams[0].get("codec_name") or "") or None
        try:
            sample_rate = int(audio_streams[0].get("sample_rate"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            sample_rate = None

    if video_streams:
        video = video_streams[0]
        if duration is None:
            duration = _positive_float(video.get("duration"))
        width = video.get("width")
        height = video.get("height")
        if not isinstance(width, int) or not isinstance(height, int):
            raise MediaInspectionError(f"{path.name} has a video stream without usable dimensions.")
        if duration is None:
            raise MediaInspectionError(f"{path.name} has a video stream but no readable duration.")
        return MediaProbe(
            asset_type=AssetType.VIDEO,
            detected_format=format_name,
            mime_type=_CONTAINER_MIME_TYPES.get(format_name),
            size_bytes=path.stat().st_size,
            width=width,
            height=height,
            duration_seconds=duration,
            video_codec=str(video.get("codec_name") or "") or None,
            audio_codec=audio_codec,
            audio_sample_rate=sample_rate,
            frame_rate=_parse_frame_rate(video.get("r_frame_rate")),
        )

    if duration is None:
        duration = _positive_float(audio_streams[0].get("duration"))
    if duration is None:
        raise MediaInspectionError(f"{path.name} has an audio stream but no readable duration.")

    return MediaProbe(
        asset_type=AssetType.AUDIO,
        detected_format=format_name,
        mime_type=_CONTAINER_MIME_TYPES.get(format_name),
        size_bytes=path.stat().st_size,
        duration_seconds=duration,
        audio_codec=audio_codec,
        audio_sample_rate=sample_rate,
    )


def inspect_media(path: Path) -> MediaProbe:
    """Determine the type and properties of the media file at ``path``.

    Raises:
        MediaInspectionError: if the file is missing, empty, unreadable or of an
            unsupported format.
        MissingDependencyError: if the file is not an image and ffprobe, which
            would be needed to identify it, is unavailable.
    """
    if not path.exists():
        raise MediaInspectionError(f"{path} does not exist.")
    if path.is_dir():
        raise MediaInspectionError(f"{path} is a directory, not a media file.")
    if not path.is_file():
        raise MediaInspectionError(f"{path} is not a regular file.")
    if path.stat().st_size == 0:
        raise MediaInspectionError(f"{path.name} is empty (0 bytes).")

    image_probe = _inspect_image(path)
    if image_probe is not None:
        return image_probe

    wav_probe = _inspect_wav(path)
    if wav_probe is not None:
        return wav_probe

    if not find_ffprobe().available:
        raise MissingDependencyError(
            f"{path.name} is not a readable still image or WAV file, so identifying "
            f"it requires ffprobe, which is not installed. Install FFmpeg, or import "
            f"only PNG/JPEG/WebP/GIF/BMP/TIFF images and WAV audio."
        )

    return _inspect_timebased(path)
