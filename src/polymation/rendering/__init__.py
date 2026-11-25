"""FFmpeg integration: fixture generation, composition and output inspection.

The M0.1 renderer is a proof of concept with a deliberately narrow contract —
still-image shots plus one audio bed, composed to a single H.264/AAC MP4. See
:mod:`polymation.rendering.compose` for the stated boundary.
"""

from polymation.rendering.compose import (
    OUTPUT_AUDIO_SAMPLE_RATE,
    OUTPUT_FPS,
    OUTPUT_HEIGHT,
    OUTPUT_WIDTH,
    OutputInspection,
    RenderResult,
    inspect_output,
    render_project,
)
from polymation.rendering.ffmpeg import (
    ToolInfo,
    ffprobe_json,
    find_ffmpeg,
    find_ffprobe,
    toolchain_available,
)

__all__ = [
    "OUTPUT_AUDIO_SAMPLE_RATE",
    "OUTPUT_FPS",
    "OUTPUT_HEIGHT",
    "OUTPUT_WIDTH",
    "OutputInspection",
    "RenderResult",
    "ToolInfo",
    "ffprobe_json",
    "find_ffmpeg",
    "find_ffprobe",
    "inspect_output",
    "render_project",
    "toolchain_available",
]
