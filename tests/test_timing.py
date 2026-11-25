"""Frame and sample timing: exactness, rounding, and span arithmetic."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from polymation.schemas.timing import (
    MAX_FRAMES,
    FrameSpan,
    Rounding,
    SampleSpan,
    frames_to_samples,
    frames_to_seconds,
    samples_per_frame,
    samples_to_seconds,
    seconds_to_frames,
    seconds_to_samples,
)

# --- the aligned profile is exact ---------------------------------------------


def test_thirty_fps_and_48khz_align_exactly() -> None:
    """The whole timing design rests on this: 48000/30 = 1600, no remainder."""
    assert samples_per_frame(30, 48_000) == 1600
    assert frames_to_samples(1, 30, 48_000) == 1600
    assert frames_to_samples(5400, 30, 48_000) == 8_640_000


def test_frames_to_samples_refuses_an_unaligned_profile() -> None:
    """Rounding per frame would accumulate audible drift, so it is refused.

    Note that 44.1 kHz at 30 fps *does* align (1470 samples per frame); the
    unaligned cases are rarer than they look, which is exactly why the check
    has to be explicit rather than assumed.
    """
    assert 44_100 % 30 == 0, "44.1kHz at 30fps aligns; pick a truly unaligned pair"

    for fps, rate in ((7, 48_000), (30, 11_025), (24, 48_001)):
        with pytest.raises(ValueError, match="not an exact multiple"):
            frames_to_samples(1, fps, rate)


def test_frames_to_samples_rejects_non_positive_rates() -> None:
    for fps, rate in ((0, 48_000), (-30, 48_000), (30, 0), (30, -1)):
        with pytest.raises(ValueError):
            frames_to_samples(1, fps, rate)


def test_no_drift_over_a_three_minute_timeline() -> None:
    """Summing per-scene integers must equal the whole, exactly.

    This is the property that float seconds would violate.
    """
    scene_frames = [780, 960, 1020, 900, 960, 780]
    assert sum(scene_frames) == 5400
    assert sum(frames_to_samples(n, 30, 48_000) for n in scene_frames) == frames_to_samples(
        5400, 30, 48_000
    )
    assert frames_to_seconds(5400, 30) == 180.0


# --- rounding is reported, never hidden ---------------------------------------


def test_an_exact_conversion_reports_no_rounding() -> None:
    frames, applied = seconds_to_frames(5.0, 30)
    assert frames == 150
    assert applied is None


def test_a_rounded_conversion_reports_what_it_did() -> None:
    # One frame at 24 fps is 1.25 frames at 30 fps.
    frames, applied = seconds_to_frames(1 / 24, 30, quantity="shot duration")
    assert frames == 1
    assert applied is not None
    assert applied.quantity == "shot duration"
    assert applied.requested == pytest.approx(1.25)
    assert applied.resolved == 1
    assert applied.error == pytest.approx(0.25)
    assert "rounding" in applied.to_dict()


@pytest.mark.parametrize(
    ("mode", "expected"),
    [(Rounding.DOWN, 1), (Rounding.UP, 2), (Rounding.NEAREST, 1)],
)
def test_rounding_modes(mode: Rounding, expected: int) -> None:
    frames, _ = seconds_to_frames(1 / 24, 30, rounding=mode)
    assert frames == expected


def test_nearest_rounds_a_half_away_from_zero() -> None:
    # 0.05s at 30 fps is exactly 1.5 frames.
    frames, _ = seconds_to_frames(0.05, 30, rounding=Rounding.NEAREST)
    assert frames == 2


def test_exact_rounding_refuses_a_fractional_result() -> None:
    with pytest.raises(ValueError, match="not a whole number"):
        seconds_to_frames(1 / 24, 30, rounding=Rounding.EXACT)


def test_exact_rounding_accepts_a_whole_result() -> None:
    frames, applied = seconds_to_frames(2.0, 30, rounding=Rounding.EXACT)
    assert (frames, applied) == (60, None)


def test_negative_durations_are_rejected() -> None:
    with pytest.raises(ValueError, match="must not be negative"):
        seconds_to_frames(-1.0, 30)
    with pytest.raises(ValueError, match="must not be negative"):
        seconds_to_samples(-1.0, 48_000)


def test_seconds_to_samples_round_trips() -> None:
    samples, applied = seconds_to_samples(1.5, 48_000)
    assert samples == 72_000
    assert applied is None
    assert samples_to_seconds(samples, 48_000) == 1.5


# --- FrameSpan ----------------------------------------------------------------


def test_span_is_half_open() -> None:
    span = FrameSpan(start_frame=10, duration_frames=5)
    assert span.end_frame == 15
    # Adjacent spans share a boundary without overlapping, which is what makes
    # sum(durations) == total hold.
    following = FrameSpan(start_frame=15, duration_frames=5)
    assert not span.overlaps(following)
    assert span.end_frame == following.start_frame


def test_overlap_detection() -> None:
    span = FrameSpan(start_frame=10, duration_frames=10)
    assert span.overlaps(FrameSpan(start_frame=19, duration_frames=1))
    assert not span.overlaps(FrameSpan(start_frame=20, duration_frames=1))
    assert span.overlaps(FrameSpan(start_frame=0, duration_frames=11))
    assert not span.overlaps(FrameSpan(start_frame=0, duration_frames=10))


def test_containment() -> None:
    outer = FrameSpan(start_frame=0, duration_frames=100)
    assert outer.contains(FrameSpan(start_frame=0, duration_frames=100))
    assert outer.contains(FrameSpan(start_frame=50, duration_frames=50))
    assert not outer.contains(FrameSpan(start_frame=50, duration_frames=51))


def test_a_zero_length_span_is_rejected() -> None:
    with pytest.raises(ValidationError):
        FrameSpan(start_frame=0, duration_frames=0)


def test_a_negative_start_is_rejected() -> None:
    with pytest.raises(ValidationError):
        FrameSpan(start_frame=-1, duration_frames=10)


def test_a_span_past_the_timeline_limit_is_rejected() -> None:
    with pytest.raises(ValidationError, match="exceeds the maximum"):
        FrameSpan(start_frame=MAX_FRAMES - 1, duration_frames=100)


def test_span_seconds_conversion() -> None:
    span = FrameSpan(start_frame=30, duration_frames=60)
    assert span.seconds(30) == (1.0, 3.0)
    assert span.duration_seconds(30) == 2.0


def test_shifting_a_span() -> None:
    span = FrameSpan(start_frame=10, duration_frames=5)
    assert span.shifted(20).start_frame == 30
    assert span.shifted(20).duration_frames == 5
    with pytest.raises(ValueError, match="before frame 0"):
        span.shifted(-11)


# --- SampleSpan ---------------------------------------------------------------


def test_sample_span_from_frames_is_exact() -> None:
    frames = FrameSpan(start_frame=90, duration_frames=150)
    samples = SampleSpan.from_frames(frames, 30, 48_000)
    assert samples.start_sample == 144_000
    assert samples.duration_samples == 240_000
    assert samples.end_sample == 384_000
    assert samples.seconds(48_000) == (3.0, 8.0)


def test_sample_span_overlap() -> None:
    span = SampleSpan(start_sample=0, duration_samples=48_000)
    assert span.overlaps(SampleSpan(start_sample=47_999, duration_samples=1))
    assert not span.overlaps(SampleSpan(start_sample=48_000, duration_samples=1))


def test_zero_length_sample_span_is_rejected() -> None:
    with pytest.raises(ValidationError):
        SampleSpan(start_sample=0, duration_samples=0)
