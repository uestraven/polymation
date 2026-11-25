"""Content hashing. A file's sha256 is its identity throughout the platform."""

from __future__ import annotations

import hashlib
from pathlib import Path

#: Read size for streaming hashes; large media must never be loaded whole.
CHUNK_SIZE = 1024 * 1024


def sha256_file(path: Path) -> str:
    """Return the lowercase hex sha256 digest of the file at ``path``.

    Raises:
        FileNotFoundError: if ``path`` does not exist.
        IsADirectoryError: if ``path`` is a directory.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(payload: bytes) -> str:
    """Return the lowercase hex sha256 digest of ``payload``."""
    return hashlib.sha256(payload).hexdigest()


def sha256_text(payload: str) -> str:
    """Return the lowercase hex sha256 digest of ``payload`` encoded as UTF-8."""
    return sha256_bytes(payload.encode("utf-8"))
