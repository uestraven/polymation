"""Validation primitives: safe path handling and project validation.

This package intentionally re-exports nothing. ``polymation.validation.paths``
is imported by ``polymation.schemas``, while ``polymation.validation.validator``
imports ``polymation.schemas``; eager re-exports here would create an import
cycle. Import the submodules directly.
"""
