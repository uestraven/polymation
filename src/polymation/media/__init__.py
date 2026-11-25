"""Media handling: content hashing, format inspection and project import.

This package intentionally re-exports nothing. ``polymation.media.checksum`` is
imported by the validator, while ``polymation.media.inspect`` imports
``polymation.rendering.ffmpeg``; eager re-exports here would turn that into an
import cycle, because importing any submodule runs this file. Import the
submodules directly.
"""
