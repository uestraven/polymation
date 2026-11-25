"""Environment diagnostics behind ``polymation doctor``."""

from __future__ import annotations

import importlib.metadata
import platform
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from polymation.config.paths import Workspace
from polymation.rendering.ffmpeg import INSTALL_HINT, find_ffmpeg, find_ffprobe

#: Python version the project targets. Older supported versions are reported as
#: a caution rather than a failure.
PREFERRED_PYTHON = (3, 12)
MINIMUM_PYTHON = (3, 11)

#: Importable dependencies that must be present for the CLI to work at all.
REQUIRED_PACKAGES: tuple[str, ...] = ("typer", "pydantic", "pillow", "pyyaml")

#: Development-only tooling; absence does not break the product.
OPTIONAL_PACKAGES: tuple[str, ...] = ("pytest", "ruff")


@dataclass(frozen=True)
class Check:
    """One diagnostic result."""

    name: str
    ok: bool
    detail: str
    required: bool = True

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable form."""
        return {
            "name": self.name,
            "ok": self.ok,
            "required": self.required,
            "detail": self.detail,
        }


@dataclass
class DoctorReport:
    """The complete environment assessment."""

    checks: list[Check]
    workspace_root: Path
    remediation: list[str]

    @property
    def required_failures(self) -> list[Check]:
        """Failed checks that block normal operation."""
        return [check for check in self.checks if check.required and not check.ok]

    @property
    def optional_failures(self) -> list[Check]:
        """Failed checks that only limit optional capability."""
        return [check for check in self.checks if not check.required and not check.ok]

    @property
    def ready(self) -> bool:
        """Whether every required dependency is present."""
        return not self.required_failures

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable form, for machine-readable CLI output."""
        return {
            "ready": self.ready,
            "workspace_root": str(self.workspace_root),
            "checks": [check.to_dict() for check in self.checks],
            "remediation": list(self.remediation),
        }


def _package_version(distribution: str) -> str | None:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return None


def _disk_free_bytes(path: Path) -> int | None:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None


def run_diagnostics(workspace: Workspace | None = None) -> DoctorReport:
    """Assess the local environment and report what is and is not usable."""
    resolved_workspace = workspace or Workspace.discover()
    checks: list[Check] = []
    remediation: list[str] = []

    version_info = sys.version_info
    python_ok = (version_info.major, version_info.minor) >= MINIMUM_PYTHON
    python_detail = f"{platform.python_version()} at {sys.executable}"
    if python_ok and (version_info.major, version_info.minor) != PREFERRED_PYTHON:
        python_detail += (
            f" (project targets {PREFERRED_PYTHON[0]}.{PREFERRED_PYTHON[1]}; "
            f"this interpreter is supported but not the pinned version)"
        )
    checks.append(
        Check(
            name="python",
            ok=python_ok,
            detail=python_detail
            if python_ok
            else f"{platform.python_version()} is below the minimum "
            f"{MINIMUM_PYTHON[0]}.{MINIMUM_PYTHON[1]}",
        )
    )
    if not python_ok:
        remediation.append(
            f"Install Python {PREFERRED_PYTHON[0]}.{PREFERRED_PYTHON[1]} "
            f"(`uv python install {PREFERRED_PYTHON[0]}.{PREFERRED_PYTHON[1]}`)."
        )

    checks.append(
        Check(
            name="platform",
            ok=True,
            detail=f"{platform.system()} {platform.release()} ({platform.machine()})",
        )
    )

    for distribution in REQUIRED_PACKAGES:
        version = _package_version(distribution)
        checks.append(
            Check(
                name=f"python package: {distribution}",
                ok=version is not None,
                detail=version or "not installed",
            )
        )
        if version is None:
            remediation.append(f"Install the {distribution} package (`uv sync`).")

    for distribution in OPTIONAL_PACKAGES:
        version = _package_version(distribution)
        checks.append(
            Check(
                name=f"dev tool: {distribution}",
                ok=version is not None,
                detail=version or "not installed",
                required=False,
            )
        )

    ffmpeg = find_ffmpeg()
    ffprobe = find_ffprobe()
    for tool in (ffmpeg, ffprobe):
        checks.append(
            Check(
                name=tool.name,
                ok=tool.available,
                detail=(
                    f"{tool.version} at {tool.path}"
                    if tool.available
                    else (f"found at {tool.path} but not executable" if tool.path else "not found")
                ),
            )
        )
    if not (ffmpeg.available and ffprobe.available):
        remediation.append(INSTALL_HINT)

    channel_dir = resolved_workspace.channels_dir
    checks.append(
        Check(
            name="channel configuration",
            ok=channel_dir.is_dir(),
            detail=f"{channel_dir}" if channel_dir.is_dir() else f"{channel_dir} is missing",
        )
    )
    if not channel_dir.is_dir():
        remediation.append(
            f"Run the CLI from the repository root, or set POLYMATION_HOME to it "
            f"(expected a channels/ directory at {channel_dir})."
        )

    free_bytes = _disk_free_bytes(resolved_workspace.root)
    checks.append(
        Check(
            name="disk space",
            ok=free_bytes is not None and free_bytes > 1024**3,
            detail=(
                f"{free_bytes / 1024**3:.1f} GiB free at {resolved_workspace.root}"
                if free_bytes is not None
                else "could not be determined"
            ),
            required=False,
        )
    )

    return DoctorReport(
        checks=checks,
        workspace_root=resolved_workspace.root,
        remediation=remediation,
    )
