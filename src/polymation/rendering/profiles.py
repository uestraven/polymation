"""Output render profiles.

Two profiles, both 30 fps with square pixels and identical composition timing,
so a preview is a faithful low-resolution stand-in for the final render and not
a different edit.

Encoder settings are recorded here as data, not buried in the renderer, because
they are part of every scene cache key: changing a CRF or a preset must
invalidate cached scenes (see :mod:`polymation.rendering.cache`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ProfileName(str, Enum):
    """The available render profiles."""

    PREVIEW = "preview"
    FINAL = "final"


@dataclass(frozen=True)
class RenderProfile:
    """A complete, immutable description of how to encode output."""

    name: ProfileName
    width: int
    height: int
    fps: int
    video_codec: str
    pixel_format: str
    crf: int
    preset: str
    video_profile: str
    audio_codec: str
    audio_sample_rate: int
    audio_channels: int
    audio_bitrate: str
    container_suffix: str
    #: Scaling filter FFmpeg should use for smooth (non-pixel-art) content.
    scale_flags: str
    #: Extra output arguments, e.g. faststart for the final profile.
    extra_output_args: tuple[str, ...] = field(default_factory=tuple)

    @property
    def resolution(self) -> tuple[int, int]:
        """``(width, height)``."""
        return (self.width, self.height)

    @property
    def aspect_ratio(self) -> float:
        """Output aspect ratio."""
        return self.width / self.height

    @property
    def samples_per_frame(self) -> int:
        """Audio samples in one video frame. Exact for both profiles."""
        if self.audio_sample_rate % self.fps != 0:
            raise ValueError(
                f"profile {self.name.value!r} has sample rate {self.audio_sample_rate} "
                f"which is not a multiple of {self.fps} fps"
            )
        return self.audio_sample_rate // self.fps

    def video_encode_args(self) -> list[str]:
        """FFmpeg arguments that encode video to this profile."""
        return [
            "-c:v",
            self.video_codec,
            "-profile:v",
            self.video_profile,
            "-preset",
            self.preset,
            "-crf",
            str(self.crf),
            "-pix_fmt",
            self.pixel_format,
            "-r",
            str(self.fps),
            # Square pixels, stated explicitly rather than relied upon.
            "-aspect",
            f"{self.width}:{self.height}",
        ]

    def audio_encode_args(self) -> list[str]:
        """FFmpeg arguments that encode audio to this profile."""
        return [
            "-c:a",
            self.audio_codec,
            "-ar",
            str(self.audio_sample_rate),
            "-ac",
            str(self.audio_channels),
            "-b:a",
            self.audio_bitrate,
        ]

    def cache_identity(self) -> dict[str, Any]:
        """Everything about this profile that can change rendered bytes.

        Fed into the scene cache key. A field omitted here is a field whose
        change would not invalidate the cache, so the list is deliberately
        complete rather than minimal.
        """
        return {
            "name": self.name.value,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "video_codec": self.video_codec,
            "pixel_format": self.pixel_format,
            "crf": self.crf,
            "preset": self.preset,
            "video_profile": self.video_profile,
            "audio_codec": self.audio_codec,
            "audio_sample_rate": self.audio_sample_rate,
            "audio_channels": self.audio_channels,
            "audio_bitrate": self.audio_bitrate,
            "scale_flags": self.scale_flags,
            "extra_output_args": list(self.extra_output_args),
        }


#: Fast, low-resolution output for internal creative review.
PREVIEW_PROFILE = RenderProfile(
    name=ProfileName.PREVIEW,
    width=960,
    height=540,
    fps=30,
    video_codec="libx264",
    pixel_format="yuv420p",
    crf=28,
    preset="veryfast",
    video_profile="main",
    audio_codec="aac",
    audio_sample_rate=48_000,
    audio_channels=2,
    audio_bitrate="96k",
    container_suffix=".mp4",
    scale_flags="bilinear",
)

#: Publication-quality output. This is the profile M0.1's demonstration used.
FINAL_PROFILE = RenderProfile(
    name=ProfileName.FINAL,
    width=1920,
    height=1080,
    fps=30,
    video_codec="libx264",
    pixel_format="yuv420p",
    crf=18,
    preset="medium",
    video_profile="high",
    audio_codec="aac",
    audio_sample_rate=48_000,
    audio_channels=2,
    audio_bitrate="192k",
    container_suffix=".mp4",
    scale_flags="lanczos",
    extra_output_args=("-movflags", "+faststart"),
)

PROFILES: dict[ProfileName, RenderProfile] = {
    ProfileName.PREVIEW: PREVIEW_PROFILE,
    ProfileName.FINAL: FINAL_PROFILE,
}


def get_profile(name: ProfileName | str) -> RenderProfile:
    """Look up a profile by name.

    Raises:
        KeyError: if the name is not a known profile.
    """
    key = ProfileName(name) if not isinstance(name, ProfileName) else name
    return PROFILES[key]
