"""Media rights records.

Design rule for this module: a *basis* is a claim about where material came
from, never proof that it may be used. Only an explicit human review can move a
record to ``APPROVED``. The code therefore refuses to mark user-supplied,
proposed-fair-use or unknown material as approved without a recorded human
approval, and treats unknown rights as permanently unresolved until a human
resolves them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import ClassVar

from pydantic import Field, field_serializer, field_validator, model_validator

from polymation.schemas.approvals import ApprovalRecord
from polymation.schemas.base import SCHEMA_VERSION, PolymationModel, TimestampedDocument


class ReviewStatus(str, Enum):
    """Human review state of a rights record."""

    UNREVIEWED = "unreviewed"
    APPROVED = "approved"
    REJECTED = "rejected"
    RESTRICTED = "restricted"


class RightsBasis(str, Enum):
    """The documented basis on which material is held.

    A basis is a factual claim about provenance. It is *not* permission. In
    particular ``PROPOSED_FAIR_USE`` records an argument that still requires
    human legal judgement, and ``UNKNOWN`` records an absence of information.
    """

    ORIGINAL_WORK = "original_work"
    LICENSED = "licensed"
    PUBLIC_DOMAIN = "public_domain"
    USER_SUPPLIED = "user_supplied"
    PROPOSED_FAIR_USE = "proposed_fair_use"
    UNKNOWN = "unknown"


#: Bases that may never reach APPROVED without a recorded human approval.
BASES_REQUIRING_HUMAN_APPROVAL: frozenset[RightsBasis] = frozenset(
    {
        RightsBasis.USER_SUPPLIED,
        RightsBasis.PROPOSED_FAIR_USE,
        RightsBasis.LICENSED,
    }
)

#: Bases that can never be approved at all by this milestone's logic: an unknown
#: basis is an unanswered question, and approving it would be a fabrication.
BASES_NEVER_AUTO_RESOLVABLE: frozenset[RightsBasis] = frozenset({RightsBasis.UNKNOWN})


class RightsRecord(PolymationModel):
    """Provenance and usage rights for exactly one media asset."""

    rights_record_id: str = Field(min_length=1, max_length=64)
    asset_id: str = Field(min_length=1, max_length=64)
    source: str = Field(min_length=1, max_length=1024)
    rights_holder: str | None = Field(default=None, max_length=512)
    basis: RightsBasis
    license_identifier: str | None = Field(default=None, max_length=256)
    attribution_required: bool = False
    attribution_text: str | None = Field(default=None, max_length=1024)
    usage_restrictions: list[str] = Field(default_factory=list)
    intended_editorial_use: str = Field(min_length=1, max_length=1024)
    ai_generated: bool = False
    review_status: ReviewStatus = ReviewStatus.UNREVIEWED
    human_approval: ApprovalRecord | None = None
    recorded_at: datetime
    review_notes: str | None = Field(default=None, max_length=4096)

    @field_validator("recorded_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("recorded_at must be timezone-aware (store UTC)")
        return value.astimezone(UTC)

    @field_serializer("recorded_at")
    def _serialize_recorded_at(self, value: datetime) -> str:
        return value.astimezone(UTC).isoformat()

    @model_validator(mode="after")
    def _enforce_approval_rules(self) -> RightsRecord:
        if self.review_status is ReviewStatus.APPROVED:
            if self.basis in BASES_NEVER_AUTO_RESOLVABLE:
                raise ValueError(
                    f"a rights record with basis {self.basis.value!r} cannot be approved: "
                    f"the rights are unknown and must be established first"
                )
            if self.basis in BASES_REQUIRING_HUMAN_APPROVAL and self.human_approval is None:
                raise ValueError(
                    f"basis {self.basis.value!r} requires an explicit human approval "
                    f"record before review_status may be 'approved'"
                )

        if self.basis is RightsBasis.LICENSED and not self.license_identifier:
            raise ValueError("basis 'licensed' requires a license_identifier")

        if self.attribution_required and not self.attribution_text:
            raise ValueError("attribution_required is set but no attribution_text was provided")

        return self

    @property
    def is_resolved(self) -> bool:
        """Whether a human has reached a decision about this asset.

        ``UNREVIEWED`` is unresolved. ``RESTRICTED`` counts as resolved — a
        decision was made — but restricted material still may not be used freely.
        """
        return self.review_status is not ReviewStatus.UNREVIEWED

    @property
    def is_cleared_for_use(self) -> bool:
        """Whether this asset may be used in a published video without further review."""
        return self.review_status is ReviewStatus.APPROVED

    @property
    def blocks_publication(self) -> bool:
        """Whether this record prevents publication of a video that uses the asset."""
        return self.review_status is not ReviewStatus.APPROVED


class RightsLedger(TimestampedDocument):
    """``projects/<project_id>/rights.json``."""

    DOCUMENT_KIND: ClassVar[str] = "rights ledger"
    SUPPORTED_SCHEMA_VERSIONS: ClassVar[frozenset[str]] = frozenset({SCHEMA_VERSION})

    project_id: str
    records: list[RightsRecord] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_unique(self) -> RightsLedger:
        record_ids: set[str] = set()
        for record in self.records:
            if record.rights_record_id in record_ids:
                raise ValueError(f"duplicate rights_record_id {record.rights_record_id!r}")
            record_ids.add(record.rights_record_id)
        return self

    def by_id(self, rights_record_id: str) -> RightsRecord | None:
        """Return the record with ``rights_record_id``, or ``None``."""
        for record in self.records:
            if record.rights_record_id == rights_record_id:
                return record
        return None

    def for_asset(self, asset_id: str) -> RightsRecord | None:
        """Return the record covering ``asset_id``, or ``None``."""
        for record in self.records:
            if record.asset_id == asset_id:
                return record
        return None

    def counts_by_status(self) -> dict[str, int]:
        """Number of records in each review status, including empty statuses."""
        counts = {status.value: 0 for status in ReviewStatus}
        for record in self.records:
            counts[record.review_status.value] += 1
        return counts
