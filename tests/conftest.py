"""Shared fixtures. Every test runs against a temporary workspace.

No test reads or writes the repository's own `projects/` directory, and no test
touches the network.
"""

from __future__ import annotations

import shutil
import wave
from pathlib import Path

import pytest
from typer.testing import CliRunner

from polymation.config.channel import ChannelConfig, load_channel_config
from polymation.config.paths import Workspace
from polymation.projects.store import ProjectBundle, ProjectStore
from polymation.schemas.project import ContentFormat, ContentPillar, PrimarySubject

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Workspace:
    """A temporary workspace with the real channel configuration copied in."""
    root = tmp_path / "studio"
    (root / "projects").mkdir(parents=True)
    (root / "fixtures").mkdir(parents=True)
    shutil.copytree(REPOSITORY_ROOT / "channels", root / "channels")

    monkeypatch.setenv("POLYMATION_HOME", str(root))
    return Workspace(root=root)


@pytest.fixture
def channel(workspace: Workspace) -> ChannelConfig:
    """The polymation channel configuration, loaded from the temporary workspace."""
    return load_channel_config(workspace, "polymation")


@pytest.fixture
def store(workspace: Workspace) -> ProjectStore:
    """A project store over the temporary workspace."""
    return ProjectStore(workspace)


@pytest.fixture
def project(store: ProjectStore) -> ProjectBundle:
    """A freshly created, empty draft project."""
    return store.create(
        title="Why Chrono Trigger Feels Like an Adventure",
        project_id="20260101-chrono-trigger-adventure-abc123",
        channel_id="polymation",
        content_pillar=ContentPillar.STORY_WORLD_DEEP_DIVE,
        primary_subject=PrimarySubject.JRPG,
        content_format=ContentFormat.STANDARD,
        target_duration_seconds=240,
        slug="why-chrono-trigger-feels-like-an-adventure",
    )


@pytest.fixture
def runner() -> CliRunner:
    """A Typer CLI runner."""
    return CliRunner()


@pytest.fixture
def png_file(tmp_path: Path) -> Path:
    """A small original PNG, generated in-process."""
    from PIL import Image

    path = tmp_path / "source" / "swatch.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 48), (25, 29, 56)).save(path, format="PNG")
    return path


@pytest.fixture
def wav_file(tmp_path: Path) -> Path:
    """A short original WAV, generated in-process."""
    path = tmp_path / "source" / "tone.wav"
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(48_000)
        handle.writeframes(b"\x00\x00" * 4_800)  # 0.1s of silence
    return path
