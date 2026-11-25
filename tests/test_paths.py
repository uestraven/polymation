"""Path safety: traversal, absolute paths, symlink escape, filename validation."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest

from polymation.errors import UnsafePathError
from polymation.validation.paths import (
    resolve_within,
    to_project_relative,
    validate_filename,
    validate_project_relative_path,
)


@pytest.mark.parametrize(
    "candidate",
    [
        "../secrets.txt",
        "assets/../../secrets.txt",
        "..",
        "../",
        "assets/images/../../../etc/passwd",
        "/etc/passwd",
        "/absolute/path.png",
        "C:/Windows/system32",
        "C:data",
        r"\\server\share\file.png",
        "assets\\images\\file.png",
        "",
        "   ",
        " assets/a.png",
        "assets/a.png ",
        "assets/./a.png",
        "./a.png",
        "assets//a.png",
        "assets/a\x00.png",
        "assets/a\nb.png",
    ],
)
def test_unsafe_project_relative_paths_are_rejected(candidate: str) -> None:
    with pytest.raises(UnsafePathError):
        validate_project_relative_path(candidate)


@pytest.mark.parametrize(
    "candidate",
    [
        "manifest.json",
        "assets/images/shot-01.png",
        "assets/music/tone.wav",
        "output/demo.mp4",
        "a.png",
        "deeply/nested/but/safe/file.png",
        "file with spaces.png",
        "file-with-unicode-naïve.png",
    ],
)
def test_safe_project_relative_paths_are_accepted(candidate: str) -> None:
    assert validate_project_relative_path(candidate) == PurePosixPath(candidate)


@pytest.mark.parametrize(
    "candidate",
    ["../evil.png", "a/b.png", "a\\b.png", "", "  ", ".", "..", "x\x00.png"],
)
def test_unsafe_filenames_are_rejected(candidate: str) -> None:
    with pytest.raises(UnsafePathError):
        validate_filename(candidate)


def test_safe_filename_is_returned_unchanged() -> None:
    assert validate_filename("title-card.png") == "title-card.png"


def test_resolve_within_accepts_paths_inside_the_root(tmp_path: Path) -> None:
    target = tmp_path / "assets" / "images" / "a.png"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"x")

    assert resolve_within(tmp_path, "assets/images/a.png") == target.resolve()


def test_resolve_within_rejects_traversal(tmp_path: Path) -> None:
    with pytest.raises(UnsafePathError):
        resolve_within(tmp_path, "../outside.png")


def test_resolve_within_rejects_symlink_escape(tmp_path: Path) -> None:
    """A symlink inside the project pointing outside it must not be followed."""
    outside = tmp_path.parent / "outside-target"
    outside.mkdir(exist_ok=True)
    (outside / "secret.png").write_bytes(b"secret")

    project_root = tmp_path / "project"
    (project_root / "assets").mkdir(parents=True)
    (project_root / "assets" / "escape").symlink_to(outside, target_is_directory=True)

    with pytest.raises(UnsafePathError, match="outside the project directory"):
        resolve_within(project_root, "assets/escape/secret.png")


def test_resolve_within_allows_the_root_itself(tmp_path: Path) -> None:
    # A single safe component that happens to be the project's own directory
    # name must still resolve inside it.
    (tmp_path / "output").mkdir()
    assert resolve_within(tmp_path, "output") == (tmp_path / "output").resolve()


def test_to_project_relative_round_trips(tmp_path: Path) -> None:
    target = tmp_path / "assets" / "images" / "a.png"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"x")

    assert to_project_relative(tmp_path, target) == PurePosixPath("assets/images/a.png")


def test_to_project_relative_rejects_outside_paths(tmp_path: Path) -> None:
    outside = tmp_path.parent / "elsewhere.png"
    outside.write_bytes(b"x")

    with pytest.raises(UnsafePathError):
        to_project_relative(tmp_path, outside)
