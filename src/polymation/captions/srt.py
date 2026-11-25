"""SRT parsing, formatting and timing validation.

Cue timing is stored as integer frames, like everything else on the timeline, so
caption positions and shot positions are directly comparable without converting
through floats. SRT's own ``HH:MM:SS,mmm`` is a millisecond grid, which does not
divide evenly into 30 fps frames; the conversion is therefore explicit and its
rounding is reported rather than hidden.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any

from polymation.errors import PolymationError
from polymation.schemas.timing import FrameSpan
from polymation.validation.findings import Finding, Severity

#: `HH:MM:SS,mmm --> HH:MM:SS,mmm`, tolerating a dot as the decimal separator
#: (which some tools emit) and extra positioning data after the timestamps
#: (which SRT permits and we ignore).
_TIMING_PATTERN = re.compile(
    r"^\s*(?P<sh>\d{1,3}):(?P<sm>\d{2}):(?P<ss>\d{2})[,.](?P<sms>\d{1,3})\s*-->\s*"
    r"(?P<eh>\d{1,3}):(?P<em>\d{2}):(?P<es>\d{2})[,.](?P<ems>\d{1,3})\s*(?P<rest>.*)$"
)

#: Readability guidance. Exceeded cues are warnings, never errors: the operator
#: supplied the captions and may have reason.
MAX_CHARS_PER_LINE = 42
MIN_CUE_FRAMES = 15  # half a second at 30 fps
MAX_CUE_FRAMES = 210  # seven seconds at 30 fps


class CaptionParseError(PolymationError):
    """An SRT document could not be parsed."""


@dataclass(frozen=True)
class CaptionCue:
    """One caption, timed in whole output frames."""

    index: int
    span: FrameSpan
    lines: tuple[str, ...]

    @property
    def text(self) -> str:
        """The cue's lines joined with newlines."""
        return "\n".join(self.lines)

    @property
    def character_count(self) -> int:
        """Total visible characters, excluding line breaks."""
        return sum(len(line) for line in self.lines)

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable form."""
        return {
            "index": self.index,
            "start_frame": self.span.start_frame,
            "end_frame": self.span.end_frame,
            "duration_frames": self.span.duration_frames,
            "lines": list(self.lines),
        }


def _timestamp_to_frames(
    hours: str, minutes: str, seconds: str, milliseconds: str, fps: int
) -> int:
    """Convert an SRT timestamp to the nearest whole frame."""
    total_ms = (
        int(hours) * 3_600_000
        + int(minutes) * 60_000
        + int(seconds) * 1_000
        # SRT milliseconds are 3 digits; "5" means 500ms, not 5ms.
        + int(milliseconds.ljust(3, "0"))
    )
    # Nearest frame: a cue boundary is a position in time, so nearest is right.
    return (total_ms * fps + 500) // 1000


def frames_to_timestamp(frames: int, fps: int) -> str:
    """Format whole frames as an SRT ``HH:MM:SS,mmm`` timestamp."""
    if frames < 0:
        raise ValueError(f"frames must not be negative, got {frames}")
    total_ms = round(frames * 1000 / fps)
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def parse_srt(text: str, *, fps: int) -> list[CaptionCue]:
    """Parse an SRT document into frame-timed cues.

    Tolerant of the things real SRT files contain: a UTF-8 BOM, CRLF line
    endings, blank lines between blocks, a missing or non-sequential index, and
    dots instead of commas in timestamps. Intolerant of anything ambiguous — a
    cue whose end precedes its start is an error, not something to reinterpret.

    Raises:
        CaptionParseError: if the document is malformed.
    """
    if fps <= 0:
        raise ValueError(f"fps must be positive, got {fps}")

    cleaned = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    blocks = [block for block in re.split(r"\n\s*\n", cleaned) if block.strip()]

    cues: list[CaptionCue] = []
    for block_number, block in enumerate(blocks, start=1):
        lines = [line for line in block.split("\n")]

        # An index line is optional in practice; detect by looking for the
        # timing arrow on the first or second line.
        timing_line_index = None
        for candidate in (0, 1):
            if candidate < len(lines) and "-->" in lines[candidate]:
                timing_line_index = candidate
                break

        if timing_line_index is None:
            raise CaptionParseError(
                f"Caption block {block_number} has no '-->' timing line. Block begins: "
                f"{lines[0][:60]!r}"
            )

        declared_index = block_number
        if timing_line_index == 1:
            index_text = lines[0].strip()
            if index_text:
                try:
                    declared_index = int(index_text)
                except ValueError as exc:
                    raise CaptionParseError(
                        f"Caption block {block_number} has a non-numeric index {index_text!r}."
                    ) from exc

        match = _TIMING_PATTERN.match(lines[timing_line_index])
        if match is None:
            raise CaptionParseError(
                f"Caption block {block_number} has a malformed timing line: "
                f"{lines[timing_line_index].strip()!r}. Expected "
                f"'HH:MM:SS,mmm --> HH:MM:SS,mmm'."
            )

        start_frame = _timestamp_to_frames(
            match.group("sh"), match.group("sm"), match.group("ss"), match.group("sms"), fps
        )
        end_frame = _timestamp_to_frames(
            match.group("eh"), match.group("em"), match.group("es"), match.group("ems"), fps
        )

        if end_frame <= start_frame:
            raise CaptionParseError(
                f"Caption {declared_index} ends at frame {end_frame}, which is not after "
                f"its start at frame {start_frame}. A zero-length or reversed cue is "
                f"rejected rather than guessed at."
            )

        body = [line.rstrip() for line in lines[timing_line_index + 1 :]]
        while body and not body[-1]:
            body.pop()

        if not body or not any(line.strip() for line in body):
            raise CaptionParseError(
                f"Caption {declared_index} has timing but no text. An empty cue is "
                f"rejected; remove it or give it text."
            )

        cues.append(
            CaptionCue(
                index=declared_index,
                span=FrameSpan(start_frame=start_frame, duration_frames=end_frame - start_frame),
                lines=tuple(body),
            )
        )

    return cues


def format_srt(cues: list[CaptionCue], *, fps: int) -> str:
    """Serialise cues as an SRT document.

    Indices are renumbered from 1 in timeline order, which is what every player
    expects and what makes a round trip stable.
    """
    if fps <= 0:
        raise ValueError(f"fps must be positive, got {fps}")

    ordered = sorted(cues, key=lambda cue: (cue.span.start_frame, cue.span.end_frame))
    blocks: list[str] = []
    for number, cue in enumerate(ordered, start=1):
        start = frames_to_timestamp(cue.span.start_frame, fps)
        end = frames_to_timestamp(cue.span.end_frame, fps)
        body = "\n".join(cue.lines)
        blocks.append(f"{number}\n{start} --> {end}\n{body}")

    # SRT conventionally ends with a trailing blank line.
    return "\n\n".join(blocks) + "\n" if blocks else ""


@dataclass
class CueValidation:
    """Result of checking a cue list. Mostly a convenience for tests."""

    findings: list[Finding] = field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        """Blocking findings."""
        return [item for item in self.findings if item.severity is Severity.ERROR]


def validate_cues(
    cues: list[CaptionCue],
    *,
    total_frames: int,
    fps: int,
    max_lines: int = 3,
) -> list[Finding]:
    """Check caption timing and readability against the rendered video.

    Errors: a cue running past the end of the video, or overlapping cues.
    Warnings: very short or very long cues, over-long lines, too many lines.
    """
    findings: list[Finding] = []

    if not cues:
        findings.append(
            Finding(
                severity=Severity.WARNING,
                code="captions_empty",
                message="The caption file parsed successfully but contains no cues.",
            )
        )
        return findings

    ordered = sorted(cues, key=lambda cue: cue.span.start_frame)

    for cue in ordered:
        where = f"cue {cue.index}"

        if total_frames and cue.span.end_frame > total_frames:
            overrun = (cue.span.end_frame - total_frames) / fps
            findings.append(
                Finding(
                    severity=Severity.ERROR,
                    code="caption_exceeds_video_duration",
                    message=(
                        f"Caption {cue.index} ends at frame {cue.span.end_frame}, "
                        f"{overrun:.3f}s past the end of the {total_frames}-frame video. "
                        f"Captions are never silently clipped to fit."
                    ),
                    location=where,
                )
            )

        if cue.span.duration_frames < MIN_CUE_FRAMES:
            findings.append(
                Finding(
                    severity=Severity.WARNING,
                    code="caption_too_brief",
                    message=(
                        f"Caption {cue.index} is on screen for only "
                        f"{cue.span.duration_frames / fps:.2f}s, below the "
                        f"{MIN_CUE_FRAMES / fps:.2f}s readability floor."
                    ),
                    location=where,
                )
            )
        elif cue.span.duration_frames > MAX_CUE_FRAMES:
            findings.append(
                Finding(
                    severity=Severity.WARNING,
                    code="caption_too_long_on_screen",
                    message=(
                        f"Caption {cue.index} is on screen for "
                        f"{cue.span.duration_frames / fps:.2f}s, above the "
                        f"{MAX_CUE_FRAMES / fps:.2f}s guideline. Consider splitting it."
                    ),
                    location=where,
                )
            )

        if len(cue.lines) > max_lines:
            findings.append(
                Finding(
                    severity=Severity.WARNING,
                    code="caption_too_many_lines",
                    message=(
                        f"Caption {cue.index} has {len(cue.lines)} lines but burn-in is "
                        f"configured for at most {max_lines}; the surplus will be "
                        f"truncated with an ellipsis."
                    ),
                    location=where,
                )
            )

        for line_number, line in enumerate(cue.lines, start=1):
            if len(line) > MAX_CHARS_PER_LINE:
                findings.append(
                    Finding(
                        severity=Severity.WARNING,
                        code="caption_line_too_long",
                        message=(
                            f"Caption {cue.index} line {line_number} is {len(line)} "
                            f"characters, above the {MAX_CHARS_PER_LINE}-character "
                            f"readability guideline. It will wrap."
                        ),
                        location=where,
                    )
                )

    for earlier, later in pairwise(ordered):
        if earlier.span.overlaps(later.span):
            findings.append(
                Finding(
                    severity=Severity.ERROR,
                    code="captions_overlap",
                    message=(
                        f"Captions {earlier.index} and {later.index} overlap "
                        f"(frames [{earlier.span.start_frame}, {earlier.span.end_frame}) "
                        f"and [{later.span.start_frame}, {later.span.end_frame})). Two "
                        f"captions on screen at once is rejected rather than stacked."
                    ),
                    location=f"cue {later.index}",
                )
            )

    return findings
