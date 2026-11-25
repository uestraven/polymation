"""Discovery and invocation of the FFmpeg toolchain.

Every call goes through :func:`run_tool`, which uses an explicit ``list[str]``
argument vector and never a shell string. No caller builds a command line by
string interpolation, so filenames containing spaces, quotes or shell
metacharacters cannot alter the command that runs.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from polymation.errors import MissingDependencyError

#: Environment variables that override binary discovery.
FFMPEG_ENV_VAR = "POLYMATION_FFMPEG"
FFPROBE_ENV_VAR = "POLYMATION_FFPROBE"

#: Default wall-clock ceiling for a single tool invocation, in seconds.
DEFAULT_TIMEOUT_SECONDS = 600

_VERSION_PATTERN = re.compile(r"version\s+(\S+)")

#: Installation guidance surfaced when the toolchain is missing.
INSTALL_HINT = (
    "Install FFmpeg (which provides both ffmpeg and ffprobe):\n"
    "  Debian/Ubuntu : sudo apt-get install -y ffmpeg\n"
    "  Amazon Linux 2: sudo amazon-linux-extras install -y epel && sudo yum install -y ffmpeg\n"
    "  Homebrew      : brew install ffmpeg\n"
    "  macOS/other   : see https://ffmpeg.org/download.html\n"
    f"Or point {FFMPEG_ENV_VAR} and {FFPROBE_ENV_VAR} at existing binaries."
)


@dataclass(frozen=True)
class ToolInfo:
    """Where a tool was found and which version it reports."""

    name: str
    path: Path | None
    version: str | None

    @property
    def available(self) -> bool:
        """Whether the tool was found and could be executed."""
        return self.path is not None and self.version is not None


def _probe_version(path: Path) -> str | None:
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, resolved binary, no shell
            [str(path), "-version"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if completed.returncode != 0:
        return None

    first_line = completed.stdout.splitlines()[0] if completed.stdout else ""
    match = _VERSION_PATTERN.search(first_line)
    return match.group(1) if match else (first_line.strip() or None)


def find_tool(name: str, env_var: str) -> ToolInfo:
    """Locate ``name`` on PATH or via ``env_var``, and read its version."""
    override = os.environ.get(env_var)
    candidate: str | None = override or shutil.which(name)

    if not candidate:
        return ToolInfo(name=name, path=None, version=None)

    path = Path(candidate)
    if not path.is_file() or not os.access(path, os.X_OK):
        return ToolInfo(name=name, path=path, version=None)

    return ToolInfo(name=name, path=path, version=_probe_version(path))


def find_ffmpeg() -> ToolInfo:
    """Locate the ``ffmpeg`` binary."""
    return find_tool("ffmpeg", FFMPEG_ENV_VAR)


def find_ffprobe() -> ToolInfo:
    """Locate the ``ffprobe`` binary."""
    return find_tool("ffprobe", FFPROBE_ENV_VAR)


def require_ffmpeg() -> ToolInfo:
    """Return a usable ``ffmpeg``, or explain how to install it.

    Raises:
        MissingDependencyError: if ffmpeg is unavailable.
    """
    info = find_ffmpeg()
    if not info.available:
        raise MissingDependencyError(f"ffmpeg is required but was not found.\n{INSTALL_HINT}")
    return info


def require_ffprobe() -> ToolInfo:
    """Return a usable ``ffprobe``, or explain how to install it.

    Raises:
        MissingDependencyError: if ffprobe is unavailable.
    """
    info = find_ffprobe()
    if not info.available:
        raise MissingDependencyError(f"ffprobe is required but was not found.\n{INSTALL_HINT}")
    return info


def toolchain_available() -> bool:
    """Whether both ffmpeg and ffprobe are usable right now."""
    return find_ffmpeg().available and find_ffprobe().available


@dataclass(frozen=True)
class ToolResult:
    """Outcome of one tool invocation."""

    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        """Whether the tool exited successfully."""
        return self.returncode == 0

    def tail(self, lines: int = 20) -> str:
        """Last ``lines`` lines of stderr, where FFmpeg writes its diagnostics."""
        return "\n".join(self.stderr.strip().splitlines()[-lines:])


def run_tool(
    binary: Path,
    arguments: list[str],
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> ToolResult:
    """Run ``binary`` with ``arguments`` as an explicit argument vector.

    ``shell=False`` is the default and is never overridden, so no argument can
    be reinterpreted by a shell.
    """
    argv = [str(binary), *arguments]
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, resolved binary, no shell
            argv,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise MissingDependencyError(
            f"{binary.name} did not finish within {timeout_seconds}s and was terminated."
        ) from exc
    except OSError as exc:
        raise MissingDependencyError(f"Could not execute {binary}: {exc}") from exc

    return ToolResult(
        argv=tuple(argv),
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )


def ffprobe_json(target: Path, *, extra_arguments: list[str] | None = None) -> dict[str, Any]:
    """Return ffprobe's JSON description of ``target``.

    Raises:
        MissingDependencyError: if ffprobe is unavailable.
        ValueError: if ffprobe fails or returns output that is not a JSON object.
    """
    tool = require_ffprobe()
    assert tool.path is not None  # noqa: S101 - guaranteed by require_ffprobe

    arguments = [
        "-v",
        "error",
        "-hide_banner",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        *(extra_arguments or []),
        str(target),
    ]

    result = run_tool(tool.path, arguments, timeout_seconds=120)
    if not result.ok:
        raise ValueError(f"ffprobe failed for {target}: {result.tail()}")

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"ffprobe returned output that is not valid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError("ffprobe returned a JSON value that is not an object.")

    return payload
