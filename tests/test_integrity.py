"""Project write protection and recovery from interrupted writes."""

from __future__ import annotations

import json
import os
import socket
import time
from pathlib import Path

import pytest

from polymation.errors import UnsafePathError
from polymation.projects.journal import (
    JOURNAL_DIRNAME,
    MANIFEST_FILENAME,
    JournalError,
    JournalledWrite,
    recover_project,
)
from polymation.projects.locking import (
    LOCK_FILENAME,
    STALE_AFTER_SECONDS,
    LockInfo,
    ProjectLock,
    ProjectLockedError,
    is_stale,
    read_lock,
)
from polymation.projects.store import ProjectBundle, ProjectStore

# --- locking ------------------------------------------------------------------


def test_a_lock_is_created_and_released(tmp_path: Path) -> None:
    lock = ProjectLock(tmp_path, operation="test")
    assert read_lock(tmp_path) is None

    with lock:
        assert lock.held
        info = read_lock(tmp_path)
        assert info is not None
        assert info.pid == os.getpid()
        assert info.hostname == socket.gethostname()
        assert info.operation == "test"

    assert not lock.held
    assert read_lock(tmp_path) is None


def test_a_second_live_lock_is_refused(tmp_path: Path) -> None:
    with ProjectLock(tmp_path, operation="first"):
        with pytest.raises(ProjectLockedError, match="is locked by 'first'"):
            ProjectLock(tmp_path, operation="second").acquire()


def test_the_refusal_message_names_the_holder(tmp_path: Path) -> None:
    with ProjectLock(tmp_path, operation="render final"):
        with pytest.raises(ProjectLockedError) as caught:
            ProjectLock(tmp_path, operation="other").acquire()
    message = str(caught.value)
    assert "render final" in message
    assert str(os.getpid()) in message
    assert LOCK_FILENAME in message


def test_the_lock_is_released_even_when_the_body_raises(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError), ProjectLock(tmp_path, operation="boom"):
        raise RuntimeError("boom")
    assert read_lock(tmp_path) is None


def test_a_lock_held_by_a_dead_process_is_stale(tmp_path: Path) -> None:
    info = LockInfo(
        pid=999_999_999,  # not a live pid
        hostname=socket.gethostname(),
        operation="abandoned",
        acquired_at=time.time(),
    )
    stale, reason = is_stale(info)
    assert stale
    assert "no longer running" in reason


def test_a_lock_held_by_a_live_process_is_not_stale(tmp_path: Path) -> None:
    info = LockInfo(
        pid=os.getpid(),
        hostname=socket.gethostname(),
        operation="mine",
        acquired_at=time.time(),
    )
    stale, _ = is_stale(info)
    assert not stale


def test_an_old_lock_from_another_host_is_stale() -> None:
    """A different host's pid cannot be checked, so age is the only evidence."""
    info = LockInfo(
        pid=1,
        hostname="some-other-machine",
        operation="old",
        acquired_at=time.time() - STALE_AFTER_SECONDS - 60,
    )
    stale, reason = is_stale(info)
    assert stale
    assert "staleness limit" in reason


def test_a_recent_lock_from_another_host_is_respected() -> None:
    info = LockInfo(
        pid=1,
        hostname="some-other-machine",
        operation="active",
        acquired_at=time.time(),
    )
    stale, _ = is_stale(info)
    assert not stale


def test_a_stale_lock_is_broken_and_reported(tmp_path: Path) -> None:
    (tmp_path / LOCK_FILENAME).write_text(
        json.dumps(
            {
                "pid": 999_999_999,
                "hostname": socket.gethostname(),
                "operation": "abandoned",
                "acquired_at": time.time(),
            }
        ),
        encoding="utf-8",
    )

    lock = ProjectLock(tmp_path, operation="new")
    with lock:
        assert lock.held
        # Breaking a stale lock is never silent.
        assert lock.broke_stale_lock is not None
        assert "no longer running" in lock.broke_stale_lock


def test_a_corrupt_lock_file_is_broken(tmp_path: Path) -> None:
    (tmp_path / LOCK_FILENAME).write_text("{not json", encoding="utf-8")
    lock = ProjectLock(tmp_path, operation="new")
    with lock:
        assert lock.held
        assert lock.broke_stale_lock is not None


def test_stale_breaking_can_be_refused(tmp_path: Path) -> None:
    (tmp_path / LOCK_FILENAME).write_text("{not json", encoding="utf-8")
    with pytest.raises(ProjectLockedError, match="unreadable lock file"):
        ProjectLock(tmp_path, operation="new", break_stale=False).acquire()


def test_a_normal_lock_reports_no_break(tmp_path: Path) -> None:
    lock = ProjectLock(tmp_path, operation="clean")
    with lock:
        assert lock.broke_stale_lock is None


# --- journalled writes --------------------------------------------------------


def test_a_committed_batch_writes_every_file(tmp_path: Path) -> None:
    with JournalledWrite(tmp_path, operation="test") as batch:
        batch.stage("a.json", b'{"a": 1}')
        batch.stage("nested/b.json", b'{"b": 2}')
        batch.commit()

    assert (tmp_path / "a.json").read_bytes() == b'{"a": 1}'
    assert (tmp_path / "nested" / "b.json").read_bytes() == b'{"b": 2}'
    # The journal cleans up after itself.
    assert not (tmp_path / JOURNAL_DIRNAME / batch.entry_id).exists()


def test_committing_nothing_is_an_error(tmp_path: Path) -> None:
    batch = JournalledWrite(tmp_path, operation="test")
    with pytest.raises(JournalError, match="nothing was staged"):
        batch.commit()


def test_committing_twice_is_an_error(tmp_path: Path) -> None:
    batch = JournalledWrite(tmp_path, operation="test")
    batch.stage("a.json", b"{}")
    batch.commit()
    with pytest.raises(JournalError, match="already been committed"):
        batch.commit()


def test_aborting_leaves_the_project_untouched(tmp_path: Path) -> None:
    batch = JournalledWrite(tmp_path, operation="test")
    batch.stage("a.json", b"{}")
    batch.abort()

    assert not (tmp_path / "a.json").exists()
    assert not (tmp_path / JOURNAL_DIRNAME / batch.entry_id).exists()


def test_an_exception_in_the_context_aborts(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError):
        with JournalledWrite(tmp_path, operation="test") as batch:
            batch.stage("a.json", b"{}")
            raise RuntimeError("boom")

    assert not (tmp_path / "a.json").exists()


def test_a_journal_cannot_write_outside_the_project(tmp_path: Path) -> None:
    batch = JournalledWrite(tmp_path, operation="test")
    for hostile in ("../escape.json", "/etc/passwd", "a/../../escape.json"):
        with pytest.raises(UnsafePathError):
            batch.stage(hostile, b"{}")


def test_the_journal_manifest_cannot_collide_with_a_staged_file(tmp_path: Path) -> None:
    """A project's own manifest.json must not be overwritten by bookkeeping.

    This was a real bug: the journal's manifest was named manifest.json, so it
    clobbered the staged project manifest and then moved itself into place.
    """
    assert MANIFEST_FILENAME != "manifest.json"

    with JournalledWrite(tmp_path, operation="test") as batch:
        batch.stage("manifest.json", b'{"project": "real"}')
        batch.commit()

    assert json.loads((tmp_path / "manifest.json").read_text()) == {"project": "real"}


# --- recovery -----------------------------------------------------------------


def test_recovery_on_a_clean_project_does_nothing(tmp_path: Path) -> None:
    outcome = recover_project(tmp_path)
    assert not outcome.acted
    assert outcome.rolled_forward == []
    assert outcome.discarded == []


def test_an_interrupted_batch_rolls_forward(tmp_path: Path) -> None:
    """Once the manifest is durable, the new state is the intended one."""
    batch = JournalledWrite(tmp_path, operation="interrupted")
    batch.stage("a.json", b'{"a": 1}')
    batch.stage("b.json", b'{"b": 2}')

    # Simulate a crash after the manifest is written but before any move, by
    # writing the manifest ourselves and not calling commit.
    manifest = {
        "journal_version": 1,
        "entry_id": batch.entry_id,
        "operation": "interrupted",
        "created_at": "2026-01-01T00:00:00+00:00",
        "files": [
            {"target": target, "staged": staged.name, "sha256": digest}
            for target, staged, digest in batch._staged
        ],
    }
    (batch.entry_dir / MANIFEST_FILENAME).write_text(json.dumps(manifest), encoding="utf-8")

    assert not (tmp_path / "a.json").exists()

    outcome = recover_project(tmp_path)

    assert outcome.acted
    assert batch.entry_id in outcome.rolled_forward
    assert sorted(outcome.files_restored) == ["a.json", "b.json"]
    assert (tmp_path / "a.json").read_bytes() == b'{"a": 1}'
    assert (tmp_path / "b.json").read_bytes() == b'{"b": 2}'


def test_a_partially_applied_batch_completes(tmp_path: Path) -> None:
    """Files already moved stay put; the rest are moved."""
    batch = JournalledWrite(tmp_path, operation="partial")
    batch.stage("a.json", b'{"a": 1}')
    batch.stage("b.json", b'{"b": 2}')

    manifest = {
        "journal_version": 1,
        "entry_id": batch.entry_id,
        "operation": "partial",
        "created_at": "2026-01-01T00:00:00+00:00",
        "files": [
            {"target": target, "staged": staged.name, "sha256": digest}
            for target, staged, digest in batch._staged
        ],
    }
    (batch.entry_dir / MANIFEST_FILENAME).write_text(json.dumps(manifest), encoding="utf-8")

    # Apply the first move by hand, as an interrupted commit would have.
    first_target, first_staged, _ = batch._staged[0]
    os.replace(first_staged, tmp_path / first_target)

    outcome = recover_project(tmp_path)

    assert outcome.files_restored == ["b.json"]
    assert (tmp_path / "a.json").read_bytes() == b'{"a": 1}'
    assert (tmp_path / "b.json").read_bytes() == b'{"b": 2}'


def test_a_batch_without_a_manifest_is_discarded(tmp_path: Path) -> None:
    """No durable manifest means the write never reached the point of intent."""
    batch = JournalledWrite(tmp_path, operation="never-committed")
    batch.stage("a.json", b'{"a": 1}')

    outcome = recover_project(tmp_path)

    assert batch.entry_id in outcome.discarded
    assert not (tmp_path / "a.json").exists()


def test_a_batch_with_a_corrupt_manifest_is_discarded(tmp_path: Path) -> None:
    batch = JournalledWrite(tmp_path, operation="corrupt")
    batch.stage("a.json", b"{}")
    (batch.entry_dir / MANIFEST_FILENAME).write_text("{not json", encoding="utf-8")

    outcome = recover_project(tmp_path)

    assert batch.entry_id in outcome.discarded
    assert not (tmp_path / "a.json").exists()


def test_a_future_journal_version_is_discarded_not_misapplied(tmp_path: Path) -> None:
    batch = JournalledWrite(tmp_path, operation="future")
    batch.stage("a.json", b"{}")
    (batch.entry_dir / MANIFEST_FILENAME).write_text(
        json.dumps({"journal_version": 99, "files": []}), encoding="utf-8"
    )

    outcome = recover_project(tmp_path)

    assert batch.entry_id in outcome.discarded
    assert not (tmp_path / "a.json").exists()


def test_recovery_removes_the_empty_journal_directory(tmp_path: Path) -> None:
    batch = JournalledWrite(tmp_path, operation="x")
    batch.stage("a.json", b"{}")
    recover_project(tmp_path)
    assert not (tmp_path / JOURNAL_DIRNAME).exists()


# --- integration with the project store ---------------------------------------


def test_saving_a_project_uses_the_journal_and_leaves_no_residue(
    project: ProjectBundle,
) -> None:
    project.save()
    journal_root = project.root / JOURNAL_DIRNAME
    assert not journal_root.exists() or not any(journal_root.iterdir())


def test_loading_a_project_recovers_an_interrupted_write(
    store: ProjectStore, project: ProjectBundle
) -> None:
    """No caller should ever observe a half-applied batch."""
    batch = JournalledWrite(project.root, operation="interrupted")
    batch.stage("research/notes.md", b"recovered")
    manifest = {
        "journal_version": 1,
        "entry_id": batch.entry_id,
        "operation": "interrupted",
        "created_at": "2026-01-01T00:00:00+00:00",
        "files": [
            {"target": target, "staged": staged.name, "sha256": digest}
            for target, staged, digest in batch._staged
        ],
    }
    (batch.entry_dir / MANIFEST_FILENAME).write_text(json.dumps(manifest), encoding="utf-8")

    reloaded = store.load(project.project_id)

    assert (project.root / "research" / "notes.md").read_bytes() == b"recovered"
    assert reloaded.recovery is not None
    assert reloaded.recovery.acted


def test_a_clean_load_reports_no_recovery(store: ProjectStore, project: ProjectBundle) -> None:
    assert store.load(project.project_id).recovery is None


def test_project_documents_survive_a_save_reload_cycle(
    store: ProjectStore, project: ProjectBundle
) -> None:
    project.manifest.title = "Renamed Through The Journal"
    project.save()

    reloaded = store.load(project.project_id)
    assert reloaded.manifest.title == "Renamed Through The Journal"
    assert reloaded.manifest.project_id == project.project_id
