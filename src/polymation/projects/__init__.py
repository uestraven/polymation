"""Project lifecycle: identifier generation and manifest-backed storage."""

from polymation.projects.ids import generate_project_id, slugify
from polymation.projects.store import (
    ProjectBundle,
    ProjectStore,
    read_document,
    write_document,
)

__all__ = [
    "ProjectBundle",
    "ProjectStore",
    "generate_project_id",
    "read_document",
    "slugify",
    "write_document",
]
