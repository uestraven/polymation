"""Compiling and validating the composition timeline.

The storyboard records creative intent; the timeline records executable media
placement. This package turns the former into the latter and checks the result
against the project's assets and the output profile.
"""

from polymation.timeline.compile import CompilationResult, compile_timeline
from polymation.timeline.validate import validate_timeline

__all__ = ["CompilationResult", "compile_timeline", "validate_timeline"]
