"""Measuring loudness and true peak with FFmpeg's EBU R128 implementation.

Measurement is always reported, whether or not normalisation is applied. The
targets in :class:`~polymation.schemas.timeline.LoudnessTarget` are engineering
figures for this milestone (approximately -16 LUFS integrated, true peak no
higher than -1 dBTP), not a platform requirement, and the renderer states what
it actually achieved rather than asserting it hit them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from polymation.rendering.ffmpeg import require_ffmpeg, run_tool

#: `ebur128` summary lines, printed to stderr at the end of a pass.
_INTEGRATED_PATTERN = re.compile(r"^\s*I:\s*(-?\d+(?:\.\d+)?)\s*LUFS", re.MULTILINE)
_RANGE_PATTERN = re.compile(r"^\s*LRA:\s*(-?\d+(?:\.\d+)?)\s*LU", re.MULTILINE)
_TRUE_PEAK_PATTERN = re.compile(r"^\s*Peak:\s*(-?\d+(?:\.\d+)?|-inf)\s*dBFS", re.MULTILINE)

#: `volumedetect` reports sample peak, used as a cross-check.
_MAX_VOLUME_PATTERN = re.compile(r"max_volume:\s*(-?\d+(?:\.\d+)?)\s*dB")


@dataclass(frozen=True)
class LoudnessMeasurement:
    """What EBU R128 analysis found in a rendered file."""

    integrated_lufs: float | None
    loudness_range_lu: float | None
    true_peak_dbtp: float | None
    sample_peak_dbfs: float | None

    @property
    def is_silent(self) -> bool:
        """Whether analysis found no signal at all."""
        return self.integrated_lufs is None and (
            self.sample_peak_dbfs is None or self.sample_peak_dbfs <= -90.0
        )

    def within(
        self, *, integrated_lufs: float, true_peak_dbtp: float, tolerance_lu: float = 1.5
    ) -> bool:
        """Whether the measurement meets a target within ``tolerance_lu``."""
        if self.integrated_lufs is None or self.true_peak_dbtp is None:
            return False
        return (
            abs(self.integrated_lufs - integrated_lufs) <= tolerance_lu
            and self.true_peak_dbtp <= true_peak_dbtp + 0.1
        )

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable form."""
        return {
            "integrated_lufs": self.integrated_lufs,
            "loudness_range_lu": self.loudness_range_lu,
            "true_peak_dbtp": self.true_peak_dbtp,
            "sample_peak_dbfs": self.sample_peak_dbfs,
            "silent": self.is_silent,
        }


def _parse_float(match: re.Match[str] | None) -> float | None:
    if match is None:
        return None
    text = match.group(1)
    if text == "-inf":
        return None
    try:
        return float(text)
    except ValueError:  # pragma: no cover - the pattern guarantees a number
        return None


def measure_loudness(path: Path) -> LoudnessMeasurement:
    """Measure ``path``'s integrated loudness, range and true peak.

    Runs one analysis pass that decodes audio to null output. Returns ``None``
    fields rather than raising when a figure is unavailable, so a silent or
    audio-less file is reported as such instead of failing the render.

    Raises:
        MissingDependencyError: if ffmpeg is unavailable.
        FileNotFoundError: if ``path`` does not exist.
    """
    if not path.is_file():
        raise FileNotFoundError(f"{path} does not exist, so it cannot be measured")

    ffmpeg = require_ffmpeg()
    if ffmpeg.path is None:  # pragma: no cover - guaranteed by require_ffmpeg
        raise RuntimeError("ffmpeg reported available with no path")

    result = run_tool(
        ffmpeg.path,
        [
            "-nostdin",
            "-hide_banner",
            "-i",
            str(path),
            "-map",
            "0:a:0?",
            "-af",
            "ebur128=peak=true,volumedetect",
            "-f",
            "null",
            "-",
        ],
        timeout_seconds=600,
    )

    # A file with no audio stream exits non-zero; that is information, not a
    # failure to propagate.
    output = result.stderr

    return LoudnessMeasurement(
        integrated_lufs=_parse_float(_INTEGRATED_PATTERN.search(output)),
        loudness_range_lu=_parse_float(_RANGE_PATTERN.search(output)),
        true_peak_dbtp=_parse_float(_TRUE_PEAK_PATTERN.search(output)),
        sample_peak_dbfs=_parse_float(_MAX_VOLUME_PATTERN.search(output)),
    )


def loudnorm_filter(
    measurement: LoudnessMeasurement,
    *,
    integrated_lufs: float,
    true_peak_dbtp: float,
) -> str | None:
    """A second-pass ``loudnorm`` filter using a first-pass measurement.

    Returns ``None`` when there is nothing to normalise — a silent file, or one
    whose loudness could not be measured. Linear normalisation is requested so
    the result is a gain change rather than dynamic processing, which keeps the
    mix's own dynamics intact.
    """
    if measurement.integrated_lufs is None or measurement.is_silent:
        return None

    measured_peak = measurement.true_peak_dbtp if measurement.true_peak_dbtp is not None else -1.0
    measured_range = (
        measurement.loudness_range_lu if measurement.loudness_range_lu is not None else 7.0
    )

    return (
        f"loudnorm=I={integrated_lufs:.2f}:TP={true_peak_dbtp:.2f}:LRA=11"
        f":measured_I={measurement.integrated_lufs:.2f}"
        f":measured_TP={measured_peak:.2f}"
        f":measured_LRA={measured_range:.2f}"
        f":linear=true:print_format=summary"
    )
