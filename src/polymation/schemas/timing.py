"""Timing primitives for the composition timeline.

Design rule: **integer frames are the authoritative visual timing
representation, and integer samples the authoritative audio one.** Durations
are never accumulated as floating-point seconds, because summing ~5400 float
durations for a three-minute video accumulates drift that eventually shows up
as a dropped or duplicated frame and as audio/video desynchronisation.

Seconds appear only at the boundary where FFmpeg needs them, converted from
integers at the point of use.

For the standard 30 fps / 48 kHz profile the two grids align exactly:
48000 / 30 = 1600 samples per frame, with no remainder. Conversions therefore
need no rounding at all on that profile, and :func:`frames_to_samples` asserts
that alignment rather than assuming it.
"""

from __future__ import annotations

from enum import Enum
from fractions import Fraction
from typing import Any

from pydantic import Field, model_validator

from polymation.schemas.base import PolymationModel

#: Upper bound on a single span, as a guard against transposed units. 30 fps for
#: two hours — far beyond any Polymation video, but finite.
MAX_FRAMES = 216_000

#: Upper bound on a single audio span: two hours at 48 kHz.
MAX_SAMPLES = 345_600_000


class Rounding(str, Enum):
    """How a non-integral conversion should be resolved.

    Made explicit at every call site rather than defaulted, so a caller cannot
    accidentally truncate a duration and lose a frame.
    """

    #: Nearest integer, ties away from zero. Correct for a position in time.
    NEAREST = "nearest"
    #: Round down. Correct when a span must not exceed available material.
    DOWN = "down"
    #: Round up. Correct when a span must fully cover a region.
    UP = "up"
    #: Refuse to convert a non-integral value.
    EXACT = "exact"


class RoundingApplied(PolymationModel):
    """A record that a conversion was not exact.

    Surfaced in compilation and validation output so imported media whose frame
    rate or sample rate does not divide the profile's is visible to the
    operator, rather than being silently snapped to the grid.
    """

    quantity: str = Field(min_length=1, max_length=128)
    requested: float
    resolved: int
    rounding: Rounding
    #: Absolute error in the unit being converted to.
    error: float

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable form."""
        return {
            "quantity": self.quantity,
            "requested": self.requested,
            "resolved": self.resolved,
            "rounding": self.rounding.value,
            "error": round(self.error, 6),
        }


def _resolve(value: Fraction, rounding: Rounding, quantity: str) -> int:
    """Convert an exact rational to an integer under ``rounding``."""
    if value.denominator == 1:
        return int(value)

    if rounding is Rounding.EXACT:
        raise ValueError(
            f"{quantity} converts to {float(value)}, which is not a whole number, "
            f"and Rounding.EXACT was requested. Choose an explicit rounding mode, "
            f"or adjust the value so it lands on the grid."
        )
    if rounding is Rounding.DOWN:
        return value.numerator // value.denominator
    if rounding is Rounding.UP:
        return -((-value.numerator) // value.denominator)

    # NEAREST, ties away from zero.
    floor_value = value.numerator // value.denominator
    remainder = value - floor_value
    return floor_value + 1 if remainder >= Fraction(1, 2) else floor_value


def seconds_to_frames(
    seconds: float,
    fps: int,
    *,
    rounding: Rounding = Rounding.NEAREST,
    quantity: str = "duration",
) -> tuple[int, RoundingApplied | None]:
    """Convert ``seconds`` to whole frames at ``fps``.

    Returns the frame count and, when the conversion was not exact, a
    :class:`RoundingApplied` describing what was done. Callers are expected to
    propagate that record rather than discard it.
    """
    if fps <= 0:
        raise ValueError(f"fps must be positive, got {fps}")
    if seconds < 0:
        raise ValueError(f"{quantity} must not be negative, got {seconds}")

    exact = Fraction(seconds).limit_denominator(1_000_000) * fps
    resolved = _resolve(exact, rounding, quantity)

    if exact.denominator == 1:
        return resolved, None

    return resolved, RoundingApplied(
        quantity=quantity,
        requested=float(exact),
        resolved=resolved,
        rounding=rounding,
        error=abs(float(exact) - resolved),
    )


def frames_to_seconds(frames: int, fps: int) -> float:
    """Convert whole frames at ``fps`` to seconds."""
    if fps <= 0:
        raise ValueError(f"fps must be positive, got {fps}")
    return frames / fps


def frames_to_samples(frames: int, fps: int, sample_rate: int) -> int:
    """Convert whole frames to whole audio samples.

    Raises:
        ValueError: if the two grids do not align — that is, if
            ``sample_rate`` is not an exact multiple of ``fps``. Rather than
            rounding silently, this refuses, because a per-frame rounding error
            accumulates into audible drift over a three-minute video. The
            supported profiles (30 fps / 48 kHz) align exactly.
    """
    if fps <= 0 or sample_rate <= 0:
        raise ValueError(f"fps and sample_rate must be positive, got {fps} and {sample_rate}")
    if sample_rate % fps != 0:
        raise ValueError(
            f"sample rate {sample_rate} is not an exact multiple of {fps} fps "
            f"({sample_rate / fps} samples per frame). Frame-to-sample conversion "
            f"would accumulate drift, so it is refused. Use an aligned profile."
        )
    return frames * (sample_rate // fps)


def samples_per_frame(fps: int, sample_rate: int) -> int:
    """Samples in one frame, for an aligned profile."""
    return frames_to_samples(1, fps, sample_rate)


def seconds_to_samples(
    seconds: float,
    sample_rate: int,
    *,
    rounding: Rounding = Rounding.NEAREST,
    quantity: str = "audio duration",
) -> tuple[int, RoundingApplied | None]:
    """Convert ``seconds`` to whole audio samples at ``sample_rate``."""
    if sample_rate <= 0:
        raise ValueError(f"sample_rate must be positive, got {sample_rate}")
    if seconds < 0:
        raise ValueError(f"{quantity} must not be negative, got {seconds}")

    exact = Fraction(seconds).limit_denominator(1_000_000) * sample_rate
    resolved = _resolve(exact, rounding, quantity)

    if exact.denominator == 1:
        return resolved, None

    return resolved, RoundingApplied(
        quantity=quantity,
        requested=float(exact),
        resolved=resolved,
        rounding=rounding,
        error=abs(float(exact) - resolved),
    )


def samples_to_seconds(samples: int, sample_rate: int) -> float:
    """Convert whole samples to seconds."""
    if sample_rate <= 0:
        raise ValueError(f"sample_rate must be positive, got {sample_rate}")
    return samples / sample_rate


class FrameSpan(PolymationModel):
    """A half-open range of output frames: ``[start_frame, end_frame)``.

    Half-open so adjacent spans share a boundary without overlapping, which is
    what makes ``sum(durations) == total`` hold exactly.
    """

    start_frame: int = Field(ge=0, le=MAX_FRAMES)
    duration_frames: int = Field(ge=1, le=MAX_FRAMES)

    @model_validator(mode="after")
    def _check_end(self) -> FrameSpan:
        if self.start_frame + self.duration_frames > MAX_FRAMES:
            raise ValueError(
                f"span [{self.start_frame}, {self.end_frame}) exceeds the maximum "
                f"timeline length of {MAX_FRAMES} frames"
            )
        return self

    @property
    def end_frame(self) -> int:
        """First frame *after* this span."""
        return self.start_frame + self.duration_frames

    def overlaps(self, other: FrameSpan) -> bool:
        """Whether two spans share at least one frame."""
        return self.start_frame < other.end_frame and other.start_frame < self.end_frame

    def contains(self, other: FrameSpan) -> bool:
        """Whether ``other`` lies entirely within this span."""
        return self.start_frame <= other.start_frame and other.end_frame <= self.end_frame

    def seconds(self, fps: int) -> tuple[float, float]:
        """``(start, end)`` in seconds at ``fps``, for handing to FFmpeg."""
        return frames_to_seconds(self.start_frame, fps), frames_to_seconds(self.end_frame, fps)

    def duration_seconds(self, fps: int) -> float:
        """Length in seconds at ``fps``."""
        return frames_to_seconds(self.duration_frames, fps)

    def shifted(self, delta_frames: int) -> FrameSpan:
        """A copy moved by ``delta_frames``."""
        new_start = self.start_frame + delta_frames
        if new_start < 0:
            raise ValueError(
                f"shifting span [{self.start_frame}, {self.end_frame}) by "
                f"{delta_frames} would start before frame 0"
            )
        return FrameSpan(start_frame=new_start, duration_frames=self.duration_frames)


class SampleSpan(PolymationModel):
    """A half-open range of audio samples: ``[start_sample, end_sample)``."""

    start_sample: int = Field(ge=0, le=MAX_SAMPLES)
    duration_samples: int = Field(ge=1, le=MAX_SAMPLES)

    @model_validator(mode="after")
    def _check_end(self) -> SampleSpan:
        if self.start_sample + self.duration_samples > MAX_SAMPLES:
            raise ValueError(
                f"span [{self.start_sample}, {self.end_sample}) exceeds the maximum "
                f"of {MAX_SAMPLES} samples"
            )
        return self

    @property
    def end_sample(self) -> int:
        """First sample *after* this span."""
        return self.start_sample + self.duration_samples

    def overlaps(self, other: SampleSpan) -> bool:
        """Whether two spans share at least one sample."""
        return self.start_sample < other.end_sample and other.start_sample < self.end_sample

    def seconds(self, sample_rate: int) -> tuple[float, float]:
        """``(start, end)`` in seconds at ``sample_rate``."""
        return (
            samples_to_seconds(self.start_sample, sample_rate),
            samples_to_seconds(self.end_sample, sample_rate),
        )

    def duration_seconds(self, sample_rate: int) -> float:
        """Length in seconds at ``sample_rate``."""
        return samples_to_seconds(self.duration_samples, sample_rate)

    @classmethod
    def from_frames(cls, span: FrameSpan, fps: int, sample_rate: int) -> SampleSpan:
        """Convert a frame span to the sample span covering exactly the same time."""
        return cls(
            start_sample=frames_to_samples(span.start_frame, fps, sample_rate),
            duration_samples=frames_to_samples(span.duration_frames, fps, sample_rate),
        )
