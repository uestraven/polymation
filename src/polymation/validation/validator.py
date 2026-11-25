"""Cross-document project validation.

Three distinct questions, deliberately kept apart:

* **structurally valid** — the documents parse and refer only to things that
  exist. An empty draft project is structurally valid.
* **render ready** — there is enough material to produce frames, and every
  referenced file is present and unmodified.
* **publication ready** — not evaluated by M0.1. Publication requires cleared
  rights and human approvals; this build reports the inputs to that decision and
  refuses to make it.

Schema-shape errors are raised by the schema layer at load time. This module
finds the problems that only appear when documents are read against each other
and against the filesystem.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from polymation.errors import UnsafePathError
from polymation.media.checksum import sha256_file
from polymation.projects.store import ProjectBundle
from polymation.schemas.rights import ReviewStatus
from polymation.schemas.storyboard import MIN_SHOT_DURATION_SECONDS
from polymation.validation.paths import resolve_within


class Severity(str, Enum):
    """Whether a finding blocks progress or merely warrants attention."""

    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class Finding:
    """One validation result."""

    severity: Severity
    code: str
    message: str
    location: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable form, for machine-readable CLI output."""
        return {
            "severity": self.severity.value,
            "code": self.code,
            "message": self.message,
            "location": self.location,
        }


@dataclass
class ValidationReport:
    """The complete outcome of validating one project."""

    project_id: str
    findings: list[Finding] = field(default_factory=list)

    def add(self, severity: Severity, code: str, message: str, location: str | None = None) -> None:
        """Record a finding."""
        self.findings.append(
            Finding(severity=severity, code=code, message=message, location=location)
        )

    def error(self, code: str, message: str, location: str | None = None) -> None:
        """Record a blocking problem."""
        self.add(Severity.ERROR, code, message, location)

    def warning(self, code: str, message: str, location: str | None = None) -> None:
        """Record a non-blocking concern."""
        self.add(Severity.WARNING, code, message, location)

    @property
    def errors(self) -> list[Finding]:
        """Only the blocking findings."""
        return [item for item in self.findings if item.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[Finding]:
        """Only the non-blocking findings."""
        return [item for item in self.findings if item.severity is Severity.WARNING]

    @property
    def structurally_valid(self) -> bool:
        """Whether the project's documents are internally consistent."""
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable form, for machine-readable CLI output."""
        return {
            "project_id": self.project_id,
            "structurally_valid": self.structurally_valid,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "findings": [finding.to_dict() for finding in self.findings],
        }


@dataclass
class RenderReadiness:
    """Whether a project can be rendered, and why not if it cannot."""

    render_ready: bool
    blockers: list[Finding] = field(default_factory=list)
    #: Concerns that do not stop a render but must not be ignored.
    cautions: list[Finding] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable form, for machine-readable CLI output."""
        return {
            "render_ready": self.render_ready,
            "blockers": [finding.to_dict() for finding in self.blockers],
            "cautions": [finding.to_dict() for finding in self.cautions],
            "publication_ready": None,
            "publication_note": (
                "Publication readiness is not evaluated by M0.1. Publishing is out "
                "of scope and requires cleared rights plus recorded human approvals."
            ),
        }


def validate_project(bundle: ProjectBundle, *, verify_checksums: bool = True) -> ValidationReport:
    """Validate ``bundle`` and return every finding.

    Args:
        bundle: the loaded project.
        verify_checksums: re-hash every asset file. Correct but proportional to
            total media size; the CLI exposes a flag to skip it.
    """
    report = ValidationReport(project_id=bundle.project_id)

    _check_document_ownership(bundle, report)
    _check_manifest(bundle, report)
    _check_declared_paths(bundle, report)
    _check_assets(bundle, report, verify_checksums=verify_checksums)
    _check_rights(bundle, report)
    _check_storyboard(bundle, report)
    _check_script(bundle, report)
    _check_approvals(bundle, report)

    return report


def _check_document_ownership(bundle: ProjectBundle, report: ValidationReport) -> None:
    """Every sibling document must claim the same project."""
    expected = bundle.manifest.project_id
    documents = {
        "script.json": bundle.script.project_id,
        "storyboard.json": bundle.storyboard.project_id,
        "assets.json": bundle.assets.project_id,
        "rights.json": bundle.rights.project_id,
        "approvals.json": bundle.approvals.project_id,
    }
    for filename, declared in documents.items():
        if declared != expected:
            report.error(
                "document_project_mismatch",
                f"{filename} declares project_id {declared!r} but belongs to the "
                f"project {expected!r}. A document from another project must not be "
                f"mixed in.",
                location=filename,
            )


def _check_manifest(bundle: ProjectBundle, report: ValidationReport) -> None:
    manifest = bundle.manifest

    if not manifest.duration_is_in_format_bounds():
        minimum, maximum = manifest.duration_bounds
        report.warning(
            "duration_outside_format_bounds",
            f"Target duration {manifest.target_duration_seconds}s is outside the "
            f"editorial range for format {manifest.content_format.value!r} "
            f"({minimum}-{maximum}s).",
            location="manifest.json",
        )

    channel_dir = bundle.workspace.channel_dir(manifest.channel_id)
    if not channel_dir.is_dir():
        report.error(
            "unknown_channel",
            f"Manifest references channel {manifest.channel_id!r}, but no "
            f"configuration exists at {channel_dir}.",
            location="manifest.json",
        )

    if manifest.updated_at < manifest.created_at:
        report.error(
            "timestamps_inconsistent",
            f"updated_at ({manifest.updated_at.isoformat()}) precedes created_at "
            f"({manifest.created_at.isoformat()}).",
            location="manifest.json",
        )


def _check_declared_paths(bundle: ProjectBundle, report: ValidationReport) -> None:
    """Every path the manifest declares must be safe and, for documents, present."""
    paths = bundle.manifest.paths
    required_documents = {
        "brief": paths.brief,
        "script": paths.script,
        "storyboard": paths.storyboard,
        "assets": paths.assets,
        "rights": paths.rights,
        "approvals": paths.approvals,
    }

    for name, relative in required_documents.items():
        try:
            resolved = resolve_within(bundle.root, relative)
        except UnsafePathError as exc:
            report.error(
                "unsafe_manifest_path",
                f"Manifest path {name}={relative!r} is not a safe project-relative path: {exc}",
                location="manifest.json",
            )
            continue

        if not resolved.is_file():
            report.error(
                "missing_project_document",
                f"Manifest declares {name} at {relative!r}, but no file exists there.",
                location=relative,
            )

    for name, relative in {"assets_dir": paths.assets_dir, "output_dir": paths.output_dir}.items():
        try:
            resolved = resolve_within(bundle.root, relative)
        except UnsafePathError as exc:
            report.error(
                "unsafe_manifest_path",
                f"Manifest path {name}={relative!r} is not a safe project-relative path: {exc}",
                location="manifest.json",
            )
            continue

        if resolved.exists() and not resolved.is_dir():
            report.error(
                "expected_directory",
                f"Manifest declares {name} at {relative!r}, but that path is not a directory.",
                location=relative,
            )


def _check_assets(
    bundle: ProjectBundle, report: ValidationReport, *, verify_checksums: bool
) -> None:
    for asset in bundle.assets.assets:
        location = f"assets.json:{asset.asset_id}"

        try:
            resolved = resolve_within(bundle.root, asset.path)
        except UnsafePathError as exc:
            report.error(
                "unsafe_asset_path",
                f"Asset {asset.asset_id} stores path {asset.path!r}, which is not a "
                f"safe project-relative path: {exc}",
                location=location,
            )
            continue

        if not resolved.exists():
            report.error(
                "missing_asset_file",
                f"Asset {asset.asset_id} references {asset.path!r}, but no file exists there.",
                location=location,
            )
            continue

        if not resolved.is_file():
            report.error(
                "asset_not_a_file",
                f"Asset {asset.asset_id} references {asset.path!r}, which is not a regular file.",
                location=location,
            )
            continue

        actual_size = resolved.stat().st_size
        if actual_size != asset.size_bytes:
            report.error(
                "asset_size_mismatch",
                f"Asset {asset.asset_id} records {asset.size_bytes} bytes but the "
                f"file on disk is {actual_size} bytes. The file changed after import.",
                location=location,
            )
            continue

        if verify_checksums:
            actual = sha256_file(resolved)
            if actual != asset.sha256:
                report.error(
                    "asset_checksum_mismatch",
                    f"Asset {asset.asset_id} records sha256 {asset.sha256} but the "
                    f"file on disk hashes to {actual}. The file was modified or "
                    f"replaced after import; any approval of it is void.",
                    location=location,
                )

        if bundle.rights.by_id(asset.rights_record_id) is None:
            report.error(
                "missing_rights_record",
                f"Asset {asset.asset_id} references rights record "
                f"{asset.rights_record_id!r}, which does not exist in rights.json.",
                location=location,
            )


def _check_rights(bundle: ProjectBundle, report: ValidationReport) -> None:
    asset_ids = bundle.assets.asset_ids

    for record in bundle.rights.records:
        location = f"rights.json:{record.rights_record_id}"

        if record.asset_id not in asset_ids:
            report.error(
                "orphan_rights_record",
                f"Rights record {record.rights_record_id} covers asset "
                f"{record.asset_id!r}, which does not exist in assets.json.",
                location=location,
            )
            continue

        if record.review_status is ReviewStatus.UNREVIEWED:
            report.warning(
                "unresolved_asset_rights",
                f"Asset {record.asset_id} has unresolved rights (basis "
                f"{record.basis.value!r}). A human must review it before the video "
                f"can be published.",
                location=location,
            )
        elif record.review_status is ReviewStatus.REJECTED:
            report.error(
                "rejected_asset_in_project",
                f"Asset {record.asset_id} has rights status 'rejected' but is still "
                f"present in the project. Remove the asset or revise the decision.",
                location=location,
            )
        elif record.review_status is ReviewStatus.RESTRICTED:
            report.warning(
                "restricted_asset_rights",
                f"Asset {record.asset_id} is restricted"
                + (
                    f" ({'; '.join(record.usage_restrictions)})"
                    if record.usage_restrictions
                    else ""
                )
                + ". Confirm the restriction is honoured before use.",
                location=location,
            )

    for asset in bundle.assets.assets:
        if bundle.rights.for_asset(asset.asset_id) is None:
            report.error(
                "asset_without_rights_record",
                f"Asset {asset.asset_id} has no rights record covering it. Every "
                f"asset must carry provenance.",
                location=f"assets.json:{asset.asset_id}",
            )


def _check_storyboard(bundle: ProjectBundle, report: ValidationReport) -> None:
    asset_ids = bundle.assets.asset_ids
    narration_scene_ids = bundle.script.scene_ids

    for scene in bundle.storyboard.scenes:
        if narration_scene_ids and scene.scene_id not in narration_scene_ids:
            report.error(
                "invalid_scene_reference",
                f"Storyboard scene {scene.scene_id!r} does not correspond to any "
                f"narration scene in script.json.",
                location=f"storyboard.json:{scene.scene_id}",
            )

        if not scene.shots:
            report.warning(
                "scene_without_shots",
                f"Storyboard scene {scene.scene_id!r} contains no visual shots.",
                location=f"storyboard.json:{scene.scene_id}",
            )

        for shot in scene.shots:
            location = f"storyboard.json:{shot.shot_id}"

            if shot.duration_seconds < MIN_SHOT_DURATION_SECONDS:
                report.error(
                    "invalid_shot_duration",
                    f"Shot {shot.shot_id!r} has duration {shot.duration_seconds}s, "
                    f"which is shorter than a single frame at 30fps.",
                    location=location,
                )

            if shot.asset_id is None:
                report.warning(
                    "shot_without_asset",
                    f"Shot {shot.shot_id!r} has no asset assigned, so it cannot be rendered yet.",
                    location=location,
                )
            elif shot.asset_id not in asset_ids:
                report.error(
                    "invalid_asset_reference",
                    f"Shot {shot.shot_id!r} references asset {shot.asset_id!r}, "
                    f"which does not exist in assets.json.",
                    location=location,
                )


def _check_script(bundle: ProjectBundle, report: ValidationReport) -> None:
    scene_ids = bundle.script.scene_ids

    for claim in bundle.script.claims:
        if claim.scene_id is not None and claim.scene_id not in scene_ids:
            report.error(
                "invalid_scene_reference",
                f"Factual claim {claim.claim_id!r} references narration scene "
                f"{claim.scene_id!r}, which does not exist.",
                location=f"script.json:{claim.claim_id}",
            )
        if claim.source is None:
            report.warning(
                "claim_without_source",
                f"Factual claim {claim.claim_id!r} has no source recorded.",
                location=f"script.json:{claim.claim_id}",
            )

    for note in bundle.script.pronunciation_notes:
        for scene_id in note.scene_ids:
            if scene_id not in scene_ids:
                report.error(
                    "invalid_scene_reference",
                    f"Pronunciation note for {note.term!r} references narration scene "
                    f"{scene_id!r}, which does not exist.",
                    location="script.json",
                )

    if bundle.script.scenes and bundle.script.draft_scenes:
        report.warning(
            "draft_narration_present",
            f"{len(bundle.script.draft_scenes)} of {len(bundle.script.scenes)} "
            f"narration scenes are still drafts and are not approved for production.",
            location="script.json",
        )


def _check_approvals(bundle: ProjectBundle, report: ValidationReport) -> None:
    for approval in bundle.approvals.approvals:
        location = f"approvals.json:{approval.approval_id}"
        try:
            resolved = resolve_within(bundle.root, approval.artifact_reference)
        except UnsafePathError:
            # An approval may reference a logical artefact rather than a file
            # (for example a rights record identifier). That is not an error.
            continue

        if not resolved.is_file():
            report.warning(
                "approval_artifact_missing",
                f"Approval {approval.approval_id} references artefact "
                f"{approval.artifact_reference!r}, which no longer exists. The "
                f"approval cannot be verified.",
                location=location,
            )
            continue

        actual = sha256_file(resolved)
        if actual != approval.artifact_checksum:
            report.warning(
                "approval_invalidated",
                f"Approval {approval.approval_id} was recorded against checksum "
                f"{approval.artifact_checksum} but {approval.artifact_reference!r} "
                f"now hashes to {actual}. The approval no longer applies.",
                location=location,
            )


def assess_render_readiness(
    bundle: ProjectBundle, report: ValidationReport | None = None
) -> RenderReadiness:
    """Decide whether ``bundle`` can be rendered.

    A structurally valid project is not necessarily renderable, and a renderable
    project is not necessarily publishable. Unresolved rights are a *caution*
    here — they do not stop a technical render — and they are reported as such
    rather than being silently dropped.
    """
    report = report or validate_project(bundle)

    blockers: list[Finding] = list(report.errors)
    cautions: list[Finding] = []

    if bundle.storyboard.shot_count == 0:
        blockers.append(
            Finding(
                severity=Severity.ERROR,
                code="no_shots",
                message=("The storyboard contains no shots, so there is nothing to render."),
                location="storyboard.json",
            )
        )

    for shot in bundle.storyboard.shots:
        if shot.asset_id is None:
            blockers.append(
                Finding(
                    severity=Severity.ERROR,
                    code="shot_without_asset",
                    message=f"Shot {shot.shot_id!r} has no asset assigned.",
                    location=f"storyboard.json:{shot.shot_id}",
                )
            )
            continue

        asset = bundle.assets.by_id(shot.asset_id)
        if asset is None:
            # Already reported as an error by validate_project; do not duplicate.
            continue

        record = bundle.rights.for_asset(asset.asset_id)
        if record is None or not record.is_resolved:
            cautions.append(
                Finding(
                    severity=Severity.WARNING,
                    code="unresolved_rights_in_render",
                    message=(
                        f"Shot {shot.shot_id!r} uses asset {asset.asset_id} whose "
                        f"rights are unresolved. The render is a technical artefact "
                        f"only and must not be published."
                    ),
                    location=f"rights.json:{asset.rights_record_id}",
                )
            )

    if bundle.script.scenes and not bundle.script.is_fully_approved:
        cautions.append(
            Finding(
                severity=Severity.WARNING,
                code="script_not_approved",
                message=(
                    "The script is not fully approved. Rendering draft narration "
                    "produces a preview, not a publishable video."
                ),
                location="script.json",
            )
        )

    return RenderReadiness(render_ready=not blockers, blockers=blockers, cautions=cautions)
