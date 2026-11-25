"""Workspace layout: where the studio root, channel configs and projects live."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from polymation.errors import ConfigurationError

#: Environment variable that pins the workspace root explicitly.
WORKSPACE_ENV_VAR = "POLYMATION_HOME"

#: Marker directory that identifies a Polymation Studio workspace root.
WORKSPACE_MARKER = "channels"

#: Subdirectories created inside every project directory.
PROJECT_SUBDIRECTORIES: tuple[str, ...] = (
    "assets/images",
    "assets/footage",
    "assets/narration",
    "assets/graphics",
    "assets/music",
    "research",
    "captions",
    "previews",
    "thumbnails",
    "output",
)


def find_workspace_root(start: Path | None = None) -> Path:
    """Locate the workspace root.

    Resolution order: ``$POLYMATION_HOME``, then the nearest ancestor of
    ``start`` containing a ``channels`` directory, then ``start`` itself.
    """
    override = os.environ.get(WORKSPACE_ENV_VAR)
    if override:
        root = Path(override).expanduser()
        if not root.is_dir():
            raise ConfigurationError(
                f"{WORKSPACE_ENV_VAR} points at {root}, which is not a directory. "
                f"Unset it, or point it at a Polymation Studio workspace root."
            )
        return root.resolve()

    origin = (start or Path.cwd()).resolve()
    for candidate in (origin, *origin.parents):
        if (candidate / WORKSPACE_MARKER).is_dir():
            return candidate

    return origin


@dataclass(frozen=True)
class Workspace:
    """Resolved locations of everything the studio reads and writes."""

    root: Path

    @classmethod
    def discover(cls, start: Path | None = None) -> Workspace:
        """Build a workspace from the resolved root directory."""
        return cls(root=find_workspace_root(start))

    @property
    def channels_dir(self) -> Path:
        """Directory holding one subdirectory per channel configuration."""
        return self.root / "channels"

    @property
    def projects_dir(self) -> Path:
        """Directory holding one subdirectory per video project."""
        return self.root / "projects"

    @property
    def fixtures_dir(self) -> Path:
        """Directory holding generated, original fixture media."""
        return self.root / "fixtures"

    def channel_dir(self, channel_id: str) -> Path:
        """Directory holding ``channel_id``'s configuration files."""
        if not channel_id or "/" in channel_id or "\\" in channel_id or channel_id.startswith("."):
            raise ConfigurationError(f"Channel identifier {channel_id!r} is not usable.")
        return self.channels_dir / channel_id

    def project_dir(self, project_id: str) -> Path:
        """Directory holding ``project_id``'s manifests and media.

        The caller is responsible for having validated ``project_id`` with
        :func:`polymation.schemas.project.validate_project_id`.
        """
        return self.projects_dir / project_id

    def ensure_project_scaffold(self, project_id: str) -> Path:
        """Create the full directory scaffold for a project and return its root."""
        project_root = self.project_dir(project_id)
        for subdirectory in PROJECT_SUBDIRECTORIES:
            (project_root / subdirectory).mkdir(parents=True, exist_ok=True)
        return project_root
