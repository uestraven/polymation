"""Advisory project locks, to stop two commands writing one project at once.

M0.1 had no protection: two concurrent commands could interleave writes and
leave documents inconsistent. This is a lock file created with ``O_EXCL``,
which is atomic on POSIX, carrying the holder's pid, hostname and start time.

Deliberately *advisory and local*. It prevents the realistic accident — two
commands in two terminals on one machine — and does not pretend to coordinate
across NFS or between hosts. A lock that overstates its guarantees is worse than
one that states a modest guarantee plainly.

Stale locks are the hard part. A process killed with SIGKILL leaves its lock
behind, and a tool that then refuses to run forever is unusable. So a lock is
considered stale when its holder is gone (same host, pid no longer alive) or
when it is older than :data:`STALE_AFTER_SECONDS`. Breaking a stale lock is
logged, never silent.
"""

from __future__ import annotations

import json
import os
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any

from polymation.errors import ExitCode, PolymationError

#: Lock filename inside the project directory.
LOCK_FILENAME = ".polymation.lock"

#: A lock older than this is assumed abandoned, even if we cannot check the pid
#: (a different host). Generous, because a long final render legitimately holds
#: a lock for minutes.
STALE_AFTER_SECONDS = 3 * 60 * 60


class ProjectLockedError(PolymationError):
    """Another process holds the project lock."""

    exit_code = ExitCode.CONFLICT


@dataclass(frozen=True)
class LockInfo:
    """Who holds a lock, and since when."""

    pid: int
    hostname: str
    operation: str
    acquired_at: float

    @property
    def age_seconds(self) -> float:
        """How long the lock has been held."""
        return max(0.0, time.time() - self.acquired_at)

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable form."""
        return {
            "pid": self.pid,
            "hostname": self.hostname,
            "operation": self.operation,
            "acquired_at": self.acquired_at,
            "age_seconds": round(self.age_seconds, 3),
        }


def _process_alive(pid: int) -> bool:
    """Whether ``pid`` exists on this host."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists but belongs to another user.
        return True
    except OSError:  # pragma: no cover - unexpected errno
        return True
    return True


def read_lock(project_root: Path) -> LockInfo | None:
    """Read the project's lock, or ``None`` if unlocked or unreadable."""
    path = project_root / LOCK_FILENAME
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return LockInfo(
            pid=int(payload["pid"]),
            hostname=str(payload["hostname"]),
            operation=str(payload.get("operation", "unknown")),
            acquired_at=float(payload["acquired_at"]),
        )
    except (OSError, KeyError, ValueError, TypeError, json.JSONDecodeError):
        # A corrupt lock conveys nothing; treat it as stale rather than as a
        # permanent barrier.
        return None


def is_stale(info: LockInfo) -> tuple[bool, str]:
    """Whether a lock may be broken, and why."""
    if info.hostname == socket.gethostname() and not _process_alive(info.pid):
        return True, f"holder pid {info.pid} is no longer running on this host"
    if info.age_seconds > STALE_AFTER_SECONDS:
        return True, (
            f"lock is {info.age_seconds / 3600:.1f}h old, beyond the "
            f"{STALE_AFTER_SECONDS / 3600:.1f}h staleness limit"
        )
    return False, ""


class ProjectLock:
    """Context manager holding an exclusive advisory lock on one project.

    Usage::

        with ProjectLock(project_root, operation="render final") as lock:
            ...

    The lock is released on exit, including on exception, so a failed render
    does not leave the project locked.
    """

    def __init__(
        self,
        project_root: Path,
        *,
        operation: str,
        break_stale: bool = True,
    ) -> None:
        self.project_root = project_root
        self.path = project_root / LOCK_FILENAME
        self.operation = operation
        self.break_stale = break_stale
        self._acquired = False
        #: Set when a stale lock was broken, so the caller can report it.
        self.broke_stale_lock: str | None = None

    def acquire(self) -> ProjectLock:
        """Take the lock.

        Raises:
            ProjectLockedError: if a live lock is held by another process.
        """
        self.project_root.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "pid": os.getpid(),
                "hostname": socket.gethostname(),
                "operation": self.operation,
                "acquired_at": time.time(),
            },
            indent=2,
        )

        for attempt in range(2):
            try:
                # O_EXCL makes creation atomic: exactly one caller wins.
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                existing = read_lock(self.project_root)

                if existing is None:
                    if attempt == 0 and self.break_stale:
                        self.broke_stale_lock = "previous lock file was unreadable"
                        self.path.unlink(missing_ok=True)
                        continue
                    raise ProjectLockedError(
                        f"Project {self.project_root.name} has an unreadable lock file at "
                        f"{self.path}. Remove it if no other command is running."
                    ) from None

                stale, reason = is_stale(existing)
                if stale and self.break_stale and attempt == 0:
                    self.broke_stale_lock = reason
                    self.path.unlink(missing_ok=True)
                    continue

                raise ProjectLockedError(
                    f"Project {self.project_root.name} is locked by "
                    f"{existing.operation!r} (pid {existing.pid} on "
                    f"{existing.hostname}, held for {existing.age_seconds:.0f}s). "
                    f"Wait for it to finish, or remove {self.path} if you are certain "
                    f"nothing is running."
                ) from None
            else:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                self._acquired = True
                return self

        raise ProjectLockedError(  # pragma: no cover - loop always returns or raises
            f"Could not acquire the lock for {self.project_root.name}."
        )

    def release(self) -> None:
        """Release the lock if this instance holds it."""
        if self._acquired:
            self.path.unlink(missing_ok=True)
            self._acquired = False

    @property
    def held(self) -> bool:
        """Whether this instance currently holds the lock."""
        return self._acquired

    def __enter__(self) -> ProjectLock:
        return self.acquire()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()
