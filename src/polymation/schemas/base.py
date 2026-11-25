"""Shared base classes for every persisted Polymation document.

Each document kind carries an explicit ``schema_version``. Readers check the
declared version *before* attempting to parse the body, so an unreadable future
document produces an actionable message instead of a field-level type error.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, field_serializer, field_validator

from polymation.errors import UnsupportedSchemaVersionError

#: The schema version written by this build for every document kind.
SCHEMA_VERSION = "1.0"


def utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC timestamp."""
    return datetime.now(UTC)


class PolymationModel(BaseModel):
    """Base model: strict, so an unexpected field is an error, not silent data loss."""

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        str_strip_whitespace=True,
        use_enum_values=False,
    )


class VersionedDocument(PolymationModel):
    """A model persisted as its own JSON file, with an explicit schema version."""

    #: Human-readable name used in error messages.
    DOCUMENT_KIND: ClassVar[str] = "document"
    #: Versions this build can read.
    SUPPORTED_SCHEMA_VERSIONS: ClassVar[frozenset[str]] = frozenset({SCHEMA_VERSION})

    schema_version: str = SCHEMA_VERSION

    @field_validator("schema_version")
    @classmethod
    def _check_schema_version(cls, value: str) -> str:
        if value not in cls.SUPPORTED_SCHEMA_VERSIONS:
            raise ValueError(
                f"unsupported schema_version {value!r} for {cls.DOCUMENT_KIND}; "
                f"supported: {', '.join(sorted(cls.SUPPORTED_SCHEMA_VERSIONS))}"
            )
        return value

    @classmethod
    def assert_readable_version(cls, payload: Any) -> None:
        """Check a raw decoded payload's declared version before full parsing.

        Raises:
            UnsupportedSchemaVersionError: if the payload declares a version this
                build cannot read.
        """
        if not isinstance(payload, dict):
            return
        declared = payload.get("schema_version")
        if isinstance(declared, str) and declared not in cls.SUPPORTED_SCHEMA_VERSIONS:
            raise UnsupportedSchemaVersionError(
                document_kind=cls.DOCUMENT_KIND,
                found=declared,
                supported=cls.SUPPORTED_SCHEMA_VERSIONS,
            )


class TimestampedDocument(VersionedDocument):
    """A versioned document that records its own creation and modification times."""

    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware (store UTC)")
        return value.astimezone(UTC)

    @field_serializer("created_at", "updated_at")
    def _serialize_timestamp(self, value: datetime) -> str:
        return value.astimezone(UTC).isoformat()

    def touch(self) -> None:
        """Record that the document has just been modified."""
        self.updated_at = utc_now()
