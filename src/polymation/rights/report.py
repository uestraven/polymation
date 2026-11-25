"""Rights reporting: what is cleared, what is restricted, what is unresolved."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from polymation.projects.store import ProjectBundle
from polymation.schemas.assets import MediaAsset
from polymation.schemas.rights import ReviewStatus, RightsRecord


@dataclass(frozen=True)
class RightsEntry:
    """One asset paired with the rights record covering it."""

    asset: MediaAsset
    record: RightsRecord | None

    @property
    def status(self) -> str:
        """Review status, or ``'missing_record'`` when no record covers the asset."""
        return self.record.review_status.value if self.record else "missing_record"

    @property
    def basis(self) -> str:
        """Rights basis, or ``'unknown'`` when no record covers the asset."""
        return self.record.basis.value if self.record else "unknown"

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable form."""
        return {
            "asset_id": self.asset.asset_id,
            "path": self.asset.path,
            "asset_type": self.asset.asset_type.value,
            "status": self.status,
            "basis": self.basis,
            "rights_holder": self.record.rights_holder if self.record else None,
            "license_identifier": self.record.license_identifier if self.record else None,
            "ai_generated": self.record.ai_generated if self.record else None,
            "attribution_required": self.record.attribution_required if self.record else None,
            "usage_restrictions": list(self.record.usage_restrictions) if self.record else [],
            "has_human_approval": bool(self.record and self.record.human_approval),
        }


@dataclass
class RightsReport:
    """Rights state for an entire project, grouped by review status."""

    project_id: str
    approved: list[RightsEntry] = field(default_factory=list)
    restricted: list[RightsEntry] = field(default_factory=list)
    rejected: list[RightsEntry] = field(default_factory=list)
    unresolved: list[RightsEntry] = field(default_factory=list)
    #: Rights records that cover no existing asset.
    orphan_records: list[RightsRecord] = field(default_factory=list)

    @property
    def total_assets(self) -> int:
        """Number of assets covered by this report."""
        return len(self.approved) + len(self.restricted) + len(self.rejected) + len(self.unresolved)

    @property
    def blocks_publication(self) -> bool:
        """Whether any asset would prevent publication.

        Only ``approved`` material may be published. Restricted material is a
        human judgement call and is counted as blocking here, because M0.1 does
        not model per-restriction conditions.
        """
        return bool(self.restricted or self.rejected or self.unresolved)

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable form, for machine-readable CLI output."""
        return {
            "project_id": self.project_id,
            "total_assets": self.total_assets,
            "counts": {
                "approved": len(self.approved),
                "restricted": len(self.restricted),
                "rejected": len(self.rejected),
                "unresolved": len(self.unresolved),
                "orphan_records": len(self.orphan_records),
            },
            "approved": [entry.to_dict() for entry in self.approved],
            "restricted": [entry.to_dict() for entry in self.restricted],
            "rejected": [entry.to_dict() for entry in self.rejected],
            "unresolved": [entry.to_dict() for entry in self.unresolved],
            "orphan_records": [
                {
                    "rights_record_id": record.rights_record_id,
                    "asset_id": record.asset_id,
                }
                for record in self.orphan_records
            ],
            "blocks_publication": self.blocks_publication,
            "publication_note": (
                "Publishing is out of scope for M0.1. This report is an input to a "
                "human rights decision, not a clearance determination."
            ),
        }


def build_rights_report(bundle: ProjectBundle) -> RightsReport:
    """Group every asset in ``bundle`` by the review status of its rights record.

    An asset with no rights record at all is reported as unresolved, never as
    cleared: a missing record is an absence of information.
    """
    report = RightsReport(project_id=bundle.project_id)

    for asset in bundle.assets.assets:
        record = bundle.rights.for_asset(asset.asset_id)
        entry = RightsEntry(asset=asset, record=record)

        if record is None:
            report.unresolved.append(entry)
        elif record.review_status is ReviewStatus.APPROVED:
            report.approved.append(entry)
        elif record.review_status is ReviewStatus.RESTRICTED:
            report.restricted.append(entry)
        elif record.review_status is ReviewStatus.REJECTED:
            report.rejected.append(entry)
        else:
            report.unresolved.append(entry)

    known_asset_ids = bundle.assets.asset_ids
    report.orphan_records = [
        record for record in bundle.rights.records if record.asset_id not in known_asset_ids
    ]

    return report
