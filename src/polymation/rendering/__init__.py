"""FFmpeg integration: profiles, fixtures, text, composition and inspection.

This package intentionally re-exports nothing. ``polymation.rendering.ffmpeg``
is imported by ``polymation.media.inspect`` (to run ffprobe), while
``polymation.rendering.compose`` imports the project validator, which in turn
reaches ``polymation.media``. Eager re-exports here would close that loop into
an import cycle. Import the submodules directly.
"""
