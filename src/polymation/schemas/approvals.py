"""Production approval records.

An approval binds a human decision to the *exact bytes* that were reviewed, via
``artifact_checksum``. That is what makes later invalidation possible: when an
artefact changes, its checksum changes, and :meth:`ApprovalRecord.covers`
returns ``False``. M0.1 stores and compares these records; it deliberately does
not implement an approval workflow engine, notifications or state machines.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import Enum
from typing import ClassVar

from pydantic import Field, field_serializer, field_validator, model_validator

from polymation.schemas.base import SCHEMA_VERSION, PolymationModel, TimestampedDocument

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class ApprovalStage(str, Enum):
    """The production stage an approval applies to."""

    BRIEF = "brief"
    RESEARCH = "research"
    SCRIPT = "script"
    STORYBOARD = "storyboard"
    ASSET_RIGHTS = "asset_rights"
    RENDER = "render"
    PUBLICATION = "publication"


class ApprovalDecision(str, Enum):
    """The decision a human recorded."""

    APPROVED = "approved"
    REJECTED = "rejected"


class ApprovalRecord(PolymationModel):
    """One human decision about one versioned artefact."""

    approval_id: str = Field(min_length=1, max_length=64)
    stage: ApprovalStage
    artifact_reference: str = Field(min_length=1, max_length=1024)
    artifact_checksum: str
    decision: ApprovalDecision = ApprovalDecision.APPROVED
    approver: str = Field(min_length=1, max_length=256)
    approved_at: datetime
    review_notes: str | None = Field(default=None, max_length=4096)

    @field_validator("artifact_checksum")
    @classmethod
    def _check_checksum(cls, value: str) -> str:
        if not SHA256_PATTERN.match(value):
            raise ValueError(
                f"artifact_checksum {value!r} is not a 64-character lowercase "
                f"hexadecimal sha256 digest"
            )
        return value

    @field_validator("approved_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("approved_at must be timezone-aware (store UTC)")
        return value.astimezone(UTC)

    @field_serializer("approved_at")
    def _serialize_approved_at(self, value: datetime) -> str:
        return value.astimezone(UTC).isoformat()

    def covers(self, *, artifact_reference: str, artifact_checksum: str) -> bool:
        """Whether this approval still applies to the given artefact.

        An approval is valid only for the bytes that were reviewed. If the
        artefact's content hash has changed, the approval no longer covers it.
        """
        return (
            self.decision is ApprovalDecision.APPROVED
            and self.artifact_reference == artifact_reference
            and self.artifact_checksum == artifact_checksum
        )


class ApprovalLedger(TimestampedDocument):
    """``projects/<project_id>/approvals.json``."""

    DOCUMENT_KIND: ClassVar[str] = "approval ledger"
    SUPPORTED_SCHEMA_VERSIONS: ClassVar[frozenset[str]] = frozenset({SCHEMA_VERSION})

    project_id: str
    approvals: list[ApprovalRecord] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_unique(self) -> ApprovalLedger:
        approval_ids: set[str] = set()
        for approval in self.approvals:
            if approval.approval_id in approval_ids:
                raise ValueError(f"duplicate approval_id {approval.approval_id!r}")
            approval_ids.add(approval.approval_id)
        return self

    def latest_for_stage(self, stage: ApprovalStage) -> ApprovalRecord | None:
        """Return the most recent approval recorded for ``stage``, or ``None``."""
        candidates = [approval for approval in self.approvals if approval.stage is stage]
        if not candidates:
            return None
        return max(candidates, key=lambda approval: approval.approved_at)

    def has_valid_approval(
        self, *, stage: ApprovalStage, artifact_reference: str, artifact_checksum: str
    ) -> bool:
        """Whether an unexpired approval exists for exactly these artefact bytes."""
        latest = self.latest_for_stage(stage)
        if latest is None:
            return False
        return latest.covers(
            artifact_reference=artifact_reference, artifact_checksum=artifact_checksum
        )
