"""Safe handling of project-relative paths.

Every path stored in a project manifest is relative to the project directory and
is written with POSIX separators so manifests stay portable. Nothing stored in a
manifest may escape its project directory, so all such paths pass through
:func:`validate_project_relative_path` on the way in and
:func:`resolve_within` on the way out.
"""

from __future__ import annotations

import ntpath
import posixpath
from pathlib import Path, PurePosixPath

from polymation.errors import UnsafePathError

#: Characters rejected in stored paths and generated filenames. NUL terminates
#: C strings; the rest break either POSIX or Windows filesystems.
_FORBIDDEN_CHARACTERS = frozenset('\x00\n\r\t<>:"|?*\\')


def validate_project_relative_path(raw: str | PurePosixPath) -> PurePosixPath:
    """Return ``raw`` as a safe, normalised project-relative POSIX path.

    Raises:
        UnsafePathError: if the path is absolute, empty, contains a parent
            reference, uses a Windows drive or UNC prefix, or contains a
            character that is unsafe in a filesystem path.
    """
    text = str(raw)

    if not text or not text.strip():
        raise UnsafePathError("A project-relative path may not be empty.")

    if text != text.strip():
        raise UnsafePathError(f"Project-relative path {text!r} has leading or trailing whitespace.")

    bad = sorted(set(text) & _FORBIDDEN_CHARACTERS)
    if bad:
        rendered = ", ".join(repr(character) for character in bad)
        raise UnsafePathError(
            f"Project-relative path {text!r} contains unsupported character(s): {rendered}."
        )

    # Reject absolute paths under both POSIX and Windows rules, plus Windows
    # drive-relative paths such as "C:data" that ntpath does not call absolute.
    if posixpath.isabs(text) or ntpath.isabs(text) or ntpath.splitdrive(text)[0]:
        raise UnsafePathError(
            f"Project-relative path {text!r} is absolute. Manifests must store paths "
            f"relative to the project directory so projects remain portable."
        )

    # Split the raw string rather than inspecting PurePosixPath.parts: the latter
    # silently normalises away "." components and duplicate separators, which
    # would let an unnormalised path through unnoticed.
    components = text.split("/")

    for component in components:
        if component == "..":
            raise UnsafePathError(
                f"Project-relative path {text!r} contains a '..' component. "
                f"Parent-directory references are rejected: a stored path may never "
                f"escape its project directory."
            )
        if component == "":
            raise UnsafePathError(
                f"Project-relative path {text!r} has an empty component (a leading, "
                f"trailing or doubled '/'). Store the normalised path instead."
            )
        if component == ".":
            raise UnsafePathError(
                f"Project-relative path {text!r} contains a redundant '.' component. "
                f"Store the normalised path instead."
            )

    return PurePosixPath(text)


def validate_filename(raw: str) -> str:
    """Return ``raw`` as a safe single-segment filename.

    Used for the destination filename of an imported asset, where the caller may
    supply an arbitrary name.

    Raises:
        UnsafePathError: if the name is empty, is a reserved directory entry, or
            contains a path separator or unsafe character.
    """
    if not raw or not raw.strip():
        raise UnsafePathError("A filename may not be empty.")

    if raw in {".", ".."}:
        raise UnsafePathError(f"{raw!r} is not a usable filename.")

    if "/" in raw or "\\" in raw:
        raise UnsafePathError(
            f"Filename {raw!r} contains a path separator. Supply a bare filename; "
            f"the destination directory is chosen by the asset's detected type."
        )

    validated = validate_project_relative_path(raw)
    if len(validated.parts) != 1:
        raise UnsafePathError(f"Filename {raw!r} must be a single path segment.")

    return validated.parts[0]


def resolve_within(root: Path, relative: str | PurePosixPath) -> Path:
    """Resolve ``relative`` against ``root``, refusing anything that escapes it.

    The check is performed on fully resolved paths, so a symlink inside the
    project that points outside it is rejected too.

    Raises:
        UnsafePathError: if ``relative`` is unsafe, or resolves outside ``root``.
    """
    validated = validate_project_relative_path(relative)
    resolved_root = root.resolve()
    candidate = (resolved_root / Path(*validated.parts)).resolve()

    if candidate != resolved_root and resolved_root not in candidate.parents:
        raise UnsafePathError(
            f"Path {str(validated)!r} resolves to {candidate} which is outside the "
            f"project directory {resolved_root}. Refusing to read or write it."
        )

    return candidate


def to_project_relative(root: Path, target: Path) -> PurePosixPath:
    """Express ``target`` as a validated project-relative POSIX path.

    Raises:
        UnsafePathError: if ``target`` is not inside ``root``.
    """
    resolved_root = root.resolve()
    resolved_target = target.resolve()

    try:
        relative = resolved_target.relative_to(resolved_root)
    except ValueError as exc:
        raise UnsafePathError(
            f"{resolved_target} is not inside the project directory {resolved_root}."
        ) from exc

    return validate_project_relative_path(relative.as_posix())
