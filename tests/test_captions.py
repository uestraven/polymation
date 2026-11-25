"""SRT import, export, timing validation and readability checks."""

from __future__ import annotations

import pytest

from polymation.captions.srt import (
    MAX_CHARS_PER_LINE,
    MAX_CUE_FRAMES,
    MIN_CUE_FRAMES,
    CaptionCue,
    CaptionParseError,
    format_srt,
    frames_to_timestamp,
    parse_srt,
    validate_cues,
)
from polymation.schemas.timing import FrameSpan
from polymation.validation.findings import Severity

SIMPLE = """1
00:00:00,000 --> 00:00:02,000
First caption

2
00:00:02,000 --> 00:00:04,000
Second caption
on two lines
"""


def codes(findings: list) -> list[str]:
    return [finding.code for finding in findings]


# --- parsing ------------------------------------------------------------------


def test_a_simple_document_parses() -> None:
    cues = parse_srt(SIMPLE, fps=30)
    assert len(cues) == 2
    assert cues[0].span == FrameSpan(start_frame=0, duration_frames=60)
    assert cues[1].span == FrameSpan(start_frame=60, duration_frames=60)
    assert cues[1].lines == ("Second caption", "on two lines")


def test_timestamps_convert_to_the_nearest_frame() -> None:
    # 0.033s is one frame at 30fps (0.0333...); 0.050s is 1.5 frames -> 2.
    cues = parse_srt("1\n00:00:00,033 --> 00:00:00,050\nx\n", fps=30)
    assert cues[0].span.start_frame == 1
    assert cues[0].span.end_frame == 2


def test_hours_are_handled() -> None:
    cues = parse_srt("1\n01:02:03,500 --> 01:02:04,000\nx\n", fps=30)
    expected = (1 * 3600 + 2 * 60 + 3.5) * 30
    assert cues[0].span.start_frame == round(expected)


def test_a_utf8_bom_is_tolerated() -> None:
    assert len(parse_srt("﻿" + SIMPLE, fps=30)) == 2


def test_crlf_line_endings_are_tolerated() -> None:
    assert len(parse_srt(SIMPLE.replace("\n", "\r\n"), fps=30)) == 2


def test_a_dotted_decimal_separator_is_tolerated() -> None:
    cues = parse_srt("1\n00:00:00.000 --> 00:00:01.000\nx\n", fps=30)
    assert cues[0].span.duration_frames == 30


def test_a_missing_index_is_tolerated() -> None:
    cues = parse_srt("00:00:00,000 --> 00:00:01,000\nx\n", fps=30)
    assert len(cues) == 1
    assert cues[0].index == 1


def test_non_sequential_indices_are_preserved() -> None:
    cues = parse_srt(
        "7\n00:00:00,000 --> 00:00:01,000\na\n\n9\n00:00:01,000 --> 00:00:02,000\nb\n",
        fps=30,
    )
    assert [cue.index for cue in cues] == [7, 9]


def test_extra_blank_lines_between_blocks_are_tolerated() -> None:
    assert len(parse_srt(SIMPLE.replace("\n\n", "\n\n\n\n"), fps=30)) == 2


def test_trailing_positioning_data_is_ignored() -> None:
    cues = parse_srt("1\n00:00:00,000 --> 00:00:01,000 X1:40 X2:600 Y1:20 Y2:50\nx\n", fps=30)
    assert cues[0].span.duration_frames == 30


def test_unicode_text_survives_parsing() -> None:
    cues = parse_srt("1\n00:00:00,000 --> 00:00:01,000\n日本語 éèê — 100%\n", fps=30)
    assert cues[0].lines == ("日本語 éèê — 100%",)


# --- parsing refuses the ambiguous --------------------------------------------


def test_a_block_without_timing_is_refused() -> None:
    with pytest.raises(CaptionParseError, match="no '-->' timing line"):
        parse_srt("just some text\n", fps=30)


def test_a_malformed_timing_line_is_refused() -> None:
    with pytest.raises(CaptionParseError, match="malformed timing line"):
        parse_srt("1\n00:00 --> 00:01\nx\n", fps=30)


def test_a_reversed_cue_is_refused() -> None:
    with pytest.raises(CaptionParseError, match="not after its start"):
        parse_srt("1\n00:00:05,000 --> 00:00:02,000\nx\n", fps=30)


def test_a_zero_length_cue_is_refused() -> None:
    with pytest.raises(CaptionParseError, match="not after its start"):
        parse_srt("1\n00:00:01,000 --> 00:00:01,000\nx\n", fps=30)


def test_a_cue_with_no_text_is_refused() -> None:
    with pytest.raises(CaptionParseError, match="timing but no text"):
        parse_srt("1\n00:00:00,000 --> 00:00:01,000\n", fps=30)


def test_a_cue_with_only_whitespace_is_refused() -> None:
    with pytest.raises(CaptionParseError, match="timing but no text"):
        parse_srt("1\n00:00:00,000 --> 00:00:01,000\n   \n", fps=30)


def test_a_non_numeric_index_is_refused() -> None:
    with pytest.raises(CaptionParseError, match="non-numeric index"):
        parse_srt("abc\n00:00:00,000 --> 00:00:01,000\nx\n", fps=30)


def test_an_invalid_fps_is_refused() -> None:
    with pytest.raises(ValueError, match="fps must be positive"):
        parse_srt(SIMPLE, fps=0)


def test_an_empty_document_yields_no_cues() -> None:
    assert parse_srt("", fps=30) == []
    assert parse_srt("   \n\n  \n", fps=30) == []


# --- formatting and round trip ------------------------------------------------


def test_timestamp_formatting() -> None:
    assert frames_to_timestamp(0, 30) == "00:00:00,000"
    assert frames_to_timestamp(30, 30) == "00:00:01,000"
    assert frames_to_timestamp(45, 30) == "00:00:01,500"
    assert frames_to_timestamp(5400, 30) == "00:03:00,000"
    assert frames_to_timestamp(30 * 3661, 30) == "01:01:01,000"


def test_a_negative_frame_cannot_be_formatted() -> None:
    with pytest.raises(ValueError, match="must not be negative"):
        frames_to_timestamp(-1, 30)


def test_the_round_trip_is_stable() -> None:
    cues = parse_srt(SIMPLE, fps=30)
    again = parse_srt(format_srt(cues, fps=30), fps=30)
    assert [cue.span for cue in again] == [cue.span for cue in cues]
    assert [cue.lines for cue in again] == [cue.lines for cue in cues]


def test_a_second_round_trip_is_a_fixed_point() -> None:
    once = format_srt(parse_srt(SIMPLE, fps=30), fps=30)
    twice = format_srt(parse_srt(once, fps=30), fps=30)
    assert once == twice


def test_formatting_renumbers_from_one_in_timeline_order() -> None:
    cues = [
        CaptionCue(index=7, span=FrameSpan(start_frame=60, duration_frames=30), lines=("b",)),
        CaptionCue(index=3, span=FrameSpan(start_frame=0, duration_frames=30), lines=("a",)),
    ]
    rendered = format_srt(cues, fps=30)
    assert rendered.startswith("1\n")
    assert rendered.index("a") < rendered.index("b")
    assert parse_srt(rendered, fps=30)[0].index == 1


def test_formatting_an_empty_list_gives_an_empty_document() -> None:
    assert format_srt([], fps=30) == ""


def test_hostile_text_survives_the_round_trip() -> None:
    hostile = 'It\'s 100%: a \\ test; "quoted" — éèê'
    cues = [
        CaptionCue(index=1, span=FrameSpan(start_frame=0, duration_frames=60), lines=(hostile,))
    ]
    again = parse_srt(format_srt(cues, fps=30), fps=30)
    assert again[0].lines == (hostile,)


# --- timing validation --------------------------------------------------------


def test_cues_within_the_video_pass() -> None:
    cues = parse_srt(SIMPLE, fps=30)
    assert validate_cues(cues, total_frames=200, fps=30) == []


def test_a_cue_past_the_end_of_the_video_is_an_error() -> None:
    cues = parse_srt(SIMPLE, fps=30)
    findings = validate_cues(cues, total_frames=100, fps=30)
    errors = [item for item in findings if item.severity is Severity.ERROR]
    assert [item.code for item in errors] == ["caption_exceeds_video_duration"]
    assert "never silently clipped" in errors[0].message


def test_overlapping_cues_are_an_error() -> None:
    document = "1\n00:00:00,000 --> 00:00:03,000\na\n\n2\n00:00:02,000 --> 00:00:04,000\nb\n"
    findings = validate_cues(parse_srt(document, fps=30), total_frames=200, fps=30)
    assert "captions_overlap" in codes(findings)


def test_adjacent_cues_do_not_overlap() -> None:
    findings = validate_cues(parse_srt(SIMPLE, fps=30), total_frames=200, fps=30)
    assert "captions_overlap" not in codes(findings)


def test_a_very_brief_cue_is_a_warning() -> None:
    document = f"1\n00:00:00,000 --> 00:00:00,{int(MIN_CUE_FRAMES / 30 * 1000) - 100:03d}\nx\n"
    findings = validate_cues(parse_srt(document, fps=30), total_frames=200, fps=30)
    assert "caption_too_brief" in codes(findings)
    assert all(item.severity is Severity.WARNING for item in findings)


def test_a_very_long_cue_is_a_warning() -> None:
    seconds = MAX_CUE_FRAMES / 30 + 2
    document = f"1\n00:00:00,000 --> 00:00:{int(seconds):02d},000\nx\n"
    findings = validate_cues(parse_srt(document, fps=30), total_frames=30 * 60, fps=30)
    assert "caption_too_long_on_screen" in codes(findings)


def test_an_over_long_line_is_a_warning() -> None:
    long_line = "x" * (MAX_CHARS_PER_LINE + 10)
    document = f"1\n00:00:00,000 --> 00:00:02,000\n{long_line}\n"
    findings = validate_cues(parse_srt(document, fps=30), total_frames=200, fps=30)
    assert "caption_line_too_long" in codes(findings)


def test_too_many_lines_is_a_warning() -> None:
    document = "1\n00:00:00,000 --> 00:00:02,000\na\nb\nc\nd\n"
    findings = validate_cues(parse_srt(document, fps=30), total_frames=200, fps=30, max_lines=3)
    assert "caption_too_many_lines" in codes(findings)


def test_an_empty_cue_list_is_a_warning_not_an_error() -> None:
    findings = validate_cues([], total_frames=200, fps=30)
    assert codes(findings) == ["captions_empty"]
    assert findings[0].severity is Severity.WARNING


def test_cue_helpers() -> None:
    cue = CaptionCue(
        index=1, span=FrameSpan(start_frame=0, duration_frames=30), lines=("ab", "cde")
    )
    assert cue.text == "ab\ncde"
    assert cue.character_count == 5
    assert cue.to_dict()["duration_frames"] == 30
