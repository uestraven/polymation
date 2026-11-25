"""Caption handling: importing, validating and exporting SRT.

M0.2 works with **externally supplied** captions only. There is no transcription
and no speech alignment — generating captions from audio is deferred, and
pretending otherwise would be a fake integration.
"""

from polymation.captions.srt import (
    CaptionCue,
    CaptionParseError,
    format_srt,
    parse_srt,
    validate_cues,
)

__all__ = [
    "CaptionCue",
    "CaptionParseError",
    "format_srt",
    "parse_srt",
    "validate_cues",
]
