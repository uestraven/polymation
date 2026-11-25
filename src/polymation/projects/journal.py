"""Recoverable multi-document writes.

M0.1 wrote each document atomically but had no transaction across the set:
``ProjectBundle.save()`` writes six files in sequence, and a crash between two
of them could leave the project internally inconsistent — an asset recorded in
``assets.json`` with no matching record in ``rights.json``, say.

This adds a write-ahead journal. Every document in a batch is staged inside
``.journal/<id>/``, a manifest listing the intended moves is written and
fsynced, and only then are the files moved into place. The crash windows are:

* **before the manifest is complete** — nothing has moved; the staged files are
  orphaned and discarded on the next run.
* **after the manifest, partway through the moves** — the manifest says what
  was intended, so the next run *completes* the remaining moves (roll forward)
  rather than leaving a half-applied batch.

Roll-forward rather than rollback is deliberate: by the time the manifest is
durable, the new state is the intended one, and some files may already be live.
Rolling back would require keeping the old bytes too, for no benefit here.

This is not a general transaction system, and it is not a database — it is the
smallest mechanism that makes an interrupted batch recoverable.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from polymation.errors import PolymationError
from polymation.media.checksum import sha256_bytes
from polymation.schemas.base import utc_now

#: Journal directory inside a project.
JOURNAL_DIRNAME = ".journal"

#: Manifest filename inside a journal entry.
#:
#: Underscore-prefixed so it can never collide with a staged document. A plain
#: "manifest.json" collided with the project's own manifest.json: the journal
#: overwrote the staged copy and then moved its own bookkeeping into place as
#: the project manifest.
MANIFEST_FILENAME = "_journal.json"

#: Manifest schema version, so a future format change is detectable.
JOURNAL_VERSION = 1


class JournalError(PolymationError):
    """A journalled write could not be prepared or applied."""


@dataclass
class RecoveryOutcome:
    """What recovery found and did."""

    #: Journal entries whose manifests were complete and therefore applied.
    rolled_forward: list[str]
    #: Entries discarded because their manifest was absent or unreadable.
    discarded: list[str]
    #: Files actually moved into place during roll-forward.
    files_restored: list[str]

    @property
    def acted(self) -> bool:
        """Whether recovery changed anything."""
        return bool(self.rolled_forward or self.discarded)

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable form."""
        return {
            "rolled_forward": list(self.rolled_forward),
            "discarded": list(self.discarded),
            "files_restored": list(self.files_restored),
            "acted": self.acted,
        }


def _fsync_directory(path: Path) -> None:
    """Flush a directory entry, so a rename is durable.

    Without this, the rename can be in the page cache while the manifest is on
    disk, which is the one ordering the recovery logic must not see.
    """
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:  # pragma: no cover - platform without directory fsync
        return
    try:
        os.fsync(descriptor)
    except OSError:  # pragma: no cover - not supported on some filesystems
        pass
    finally:
        os.close(descriptor)


class JournalledWrite:
    """Stage a batch of document writes, then apply them recoverably.

    Usage::

        batch = JournalledWrite(project_root, operation="asset import")
        batch.stage("assets.json", payload_bytes)
        batch.stage("rights.json", other_bytes)
        batch.commit()
    """

    def __init__(self, project_root: Path, *, operation: str) -> None:
        self.project_root = project_root
        self.operation = operation
        self.entry_id = f"{utc_now().strftime('%Y%m%dT%H%M%S%f')}-{os.getpid()}"
        self.entry_dir = project_root / JOURNAL_DIRNAME / self.entry_id
        self._staged: list[tuple[str, Path, str]] = []
        self._committed = False

    def stage(self, relative_path: str, content: bytes) -> Path:
        """Stage ``content`` to be written at ``relative_path``.

        The path is validated against the project root, so a journal cannot be
        used to write outside the project.
        """
        from polymation.validation.paths import resolve_within

        # Raises UnsafePathError for traversal, absolute paths and the rest.
        resolve_within(self.project_root, relative_path)

        self.entry_dir.mkdir(parents=True, exist_ok=True)
        # The index prefix keeps staged names unique even if two targets
        # flatten to the same string, and records the intended order.
        staged_name = f"{len(self._staged):03d}-{relative_path.replace('/', '__')}"
        staged_path = self.entry_dir / staged_name

        handle = tempfile.NamedTemporaryFile(
            mode="wb", dir=self.entry_dir, prefix=".stage.", delete=False
        )
        try:
            with handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(handle.name, staged_path)
        except BaseException:
            Path(handle.name).unlink(missing_ok=True)
            raise

        self._staged.append((relative_path, staged_path, sha256_bytes(content)))
        return staged_path

    def commit(self) -> None:
        """Make the manifest durable, then move every staged file into place.

        Raises:
            JournalError: if nothing was staged, or a move fails.
        """
        if self._committed:
            raise JournalError("this journalled write has already been committed")
        if not self._staged:
            raise JournalError("nothing was staged, so there is nothing to commit")

        manifest = {
            "journal_version": JOURNAL_VERSION,
            "entry_id": self.entry_id,
            "operation": self.operation,
            "created_at": utc_now().isoformat(),
            "files": [
                {
                    "target": relative_path,
                    "staged": staged.name,
                    "sha256": digest,
                }
                for relative_path, staged, digest in self._staged
            ],
        }

        # The manifest must be durable *before* any file moves, because its
        # presence is what tells recovery the batch was meant to be applied.
        manifest_path = self.entry_dir / MANIFEST_FILENAME
        _write_bytes_durable(manifest_path, json.dumps(manifest, indent=2).encode("utf-8"))
        _fsync_directory(self.entry_dir)

        try:
            for relative_path, staged, _ in self._staged:
                target = self.project_root / relative_path
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staged, target)
                _fsync_directory(target.parent)
        except OSError as exc:
            raise JournalError(
                f"Failed partway through applying journalled write {self.entry_id!r} "
                f"({self.operation}): {exc}. The journal manifest is intact, so the "
                f"next command will complete the remaining moves."
            ) from exc

        self._committed = True
        shutil.rmtree(self.entry_dir, ignore_errors=True)

    def abort(self) -> None:
        """Discard staged files without touching the project."""
        if not self._committed:
            shutil.rmtree(self.entry_dir, ignore_errors=True)

    def __enter__(self) -> JournalledWrite:
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        if exc_type is not None:
            self.abort()


def _write_bytes_durable(path: Path, content: bytes) -> None:
    """Write bytes and fsync them."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False
    )
    try:
        with handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, path)
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise


def recover_project(project_root: Path) -> RecoveryOutcome:
    """Complete or discard any interrupted journalled writes.

    Called before any operation that reads or writes a project, so an
    interrupted batch is resolved before anything observes the inconsistency.

    An entry with a complete manifest is rolled forward: each file still staged
    is moved into place. An entry with no readable manifest never reached the
    point of intent and is discarded.
    """
    outcome = RecoveryOutcome(rolled_forward=[], discarded=[], files_restored=[])
    journal_root = project_root / JOURNAL_DIRNAME

    if not journal_root.is_dir():
        return outcome

    for entry_dir in sorted(journal_root.iterdir()):
        if not entry_dir.is_dir():
            continue

        manifest_path = entry_dir / MANIFEST_FILENAME
        if not manifest_path.is_file():
            outcome.discarded.append(entry_dir.name)
            shutil.rmtree(entry_dir, ignore_errors=True)
            continue

        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            files = manifest["files"]
            if manifest.get("journal_version") != JOURNAL_VERSION:
                raise ValueError(f"unsupported journal_version {manifest.get('journal_version')!r}")
        except (OSError, KeyError, ValueError, json.JSONDecodeError):
            outcome.discarded.append(entry_dir.name)
            shutil.rmtree(entry_dir, ignore_errors=True)
            continue

        for record in files:
            staged = entry_dir / str(record["staged"])
            if not staged.is_file():
                # Already moved before the interruption.
                continue
            target = project_root / str(record["target"])
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged, target)
            _fsync_directory(target.parent)
            outcome.files_restored.append(str(record["target"]))

        outcome.rolled_forward.append(entry_dir.name)
        shutil.rmtree(entry_dir, ignore_errors=True)

    # Leave no empty journal directory behind.
    try:
        if journal_root.is_dir() and not any(journal_root.iterdir()):
            journal_root.rmdir()
    except OSError:  # pragma: no cover - raced with another process
        pass

    return outcome
