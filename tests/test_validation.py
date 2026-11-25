"""Project validation: errors vs warnings, and structural vs render readiness."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from polymation.media.checksum import sha256_file
from polymation.media.importer import import_asset
from polymation.projects.store import ProjectBundle, ProjectStore
from polymation.rights.report import build_rights_report
from polymation.schemas.base import utc_now
from polymation.schemas.rights import ReviewStatus, RightsBasis, RightsRecord
from polymation.schemas.script import NarrationScene, NarrationStatus, ScriptApprovalStatus
from polymation.schemas.storyboard import StoryboardScene, VisualShot
from polymation.validation.validator import (
    Severity,
    assess_render_readiness,
    validate_project,
)


def _codes(findings: list) -> set[str]:
    return {finding.code for finding in findings}


def _storyboard_from_assets(bundle: ProjectBundle, asset_ids: list[str]) -> None:
    """Give ``bundle`` one narration scene and one shot per asset."""
    bundle.script.scenes = [
        NarrationScene(scene_id=f"scene-{index:02d}", order=index, narration=f"Beat {index}.")
        for index in range(len(asset_ids))
    ]
    bundle.storyboard.scenes = [
        StoryboardScene(
            scene_id=f"scene-{index:02d}",
            order=index,
            shots=[
                VisualShot(
                    shot_id=f"shot-{index:02d}",
                    scene_id=f"scene-{index:02d}",
                    asset_id=asset_id,
                    description=f"Shot {index}",
                    duration_seconds=2.0,
                )
            ],
        )
        for index, asset_id in enumerate(asset_ids)
    ]


# --- structural validity vs render readiness -----------------------------------


def test_an_empty_draft_project_is_structurally_valid(project: ProjectBundle) -> None:
    report = validate_project(project)
    assert report.structurally_valid
    assert report.errors == []


def test_an_empty_draft_project_is_not_render_ready(project: ProjectBundle) -> None:
    readiness = assess_render_readiness(project)
    assert not readiness.render_ready
    assert "no_shots" in _codes(readiness.blockers)


def test_a_render_ready_project_is_still_not_declared_publishable(
    project: ProjectBundle, png_file: Path
) -> None:
    """Rendering a project must never imply it may be published."""
    result = import_asset(project, png_file)
    _storyboard_from_assets(project, [result.asset.asset_id])
    project.save()

    readiness = assess_render_readiness(project)
    assert readiness.render_ready
    assert readiness.to_dict()["publication_ready"] is None
    # Unresolved rights are surfaced as a caution, not dropped.
    assert "unresolved_rights_in_render" in _codes(readiness.cautions)


def test_unresolved_rights_do_not_block_a_technical_render(
    project: ProjectBundle, png_file: Path
) -> None:
    result = import_asset(project, png_file)
    _storyboard_from_assets(project, [result.asset.asset_id])
    project.save()

    readiness = assess_render_readiness(project)
    assert readiness.render_ready
    assert readiness.blockers == []


def test_draft_narration_is_a_caution_for_rendering(project: ProjectBundle, png_file: Path) -> None:
    result = import_asset(project, png_file)
    _storyboard_from_assets(project, [result.asset.asset_id])
    project.save()

    readiness = assess_render_readiness(project)
    assert "script_not_approved" in _codes(readiness.cautions)


def test_fully_approved_script_removes_the_script_caution(
    project: ProjectBundle, png_file: Path
) -> None:
    result = import_asset(project, png_file)
    _storyboard_from_assets(project, [result.asset.asset_id])
    project.script.scenes = [
        scene.model_copy(update={"status": NarrationStatus.APPROVED})
        for scene in project.script.scenes
    ]
    project.script.approval_status = ScriptApprovalStatus.APPROVED
    project.save()

    readiness = assess_render_readiness(project)
    assert "script_not_approved" not in _codes(readiness.cautions)


# --- errors: missing and modified media ----------------------------------------


def test_a_missing_asset_file_is_an_error(project: ProjectBundle, png_file: Path) -> None:
    result = import_asset(project, png_file)
    project.save()
    result.destination.unlink()

    report = validate_project(project)
    assert "missing_asset_file" in _codes(report.errors)
    assert not report.structurally_valid


def test_a_modified_asset_file_is_a_checksum_error(project: ProjectBundle, png_file: Path) -> None:
    result = import_asset(project, png_file)
    project.save()

    # Replace the content with different bytes of the same length, so only the
    # checksum reveals the change.
    original = result.destination.read_bytes()
    tampered = bytearray(original)
    tampered[-1] ^= 0xFF
    result.destination.write_bytes(bytes(tampered))

    report = validate_project(project)
    assert "asset_checksum_mismatch" in _codes(report.errors)


def test_a_resized_asset_file_is_a_size_error(project: ProjectBundle, png_file: Path) -> None:
    result = import_asset(project, png_file)
    project.save()
    result.destination.write_bytes(result.destination.read_bytes() + b"extra")

    report = validate_project(project)
    assert "asset_size_mismatch" in _codes(report.errors)


def test_checksum_verification_can_be_skipped(project: ProjectBundle, png_file: Path) -> None:
    result = import_asset(project, png_file)
    project.save()

    original = result.destination.read_bytes()
    tampered = bytearray(original)
    tampered[-1] ^= 0xFF
    result.destination.write_bytes(bytes(tampered))

    report = validate_project(project, verify_checksums=False)
    assert "asset_checksum_mismatch" not in _codes(report.errors)


# --- errors: invalid references ------------------------------------------------


def test_a_shot_referencing_an_unknown_asset_is_an_error(
    project: ProjectBundle, png_file: Path
) -> None:
    import_asset(project, png_file)
    project.script.scenes = [NarrationScene(scene_id="scene-00", order=0, narration="A")]
    project.storyboard.scenes = [
        StoryboardScene(
            scene_id="scene-00",
            order=0,
            shots=[
                VisualShot(
                    shot_id="shot-00",
                    scene_id="scene-00",
                    asset_id="asset-9999",
                    description="Dangling",
                    duration_seconds=2.0,
                )
            ],
        )
    ]
    project.save()

    report = validate_project(project)
    assert "invalid_asset_reference" in _codes(report.errors)


def test_a_storyboard_scene_with_no_narration_scene_is_an_error(
    project: ProjectBundle, png_file: Path
) -> None:
    result = import_asset(project, png_file)
    project.script.scenes = [NarrationScene(scene_id="scene-00", order=0, narration="A")]
    project.storyboard.scenes = [
        StoryboardScene(
            scene_id="scene-99",
            order=0,
            shots=[
                VisualShot(
                    shot_id="shot-00",
                    scene_id="scene-99",
                    asset_id=result.asset.asset_id,
                    description="Orphan scene",
                    duration_seconds=2.0,
                )
            ],
        )
    ]
    project.save()

    report = validate_project(project)
    assert "invalid_scene_reference" in _codes(report.errors)


def test_an_asset_pointing_at_a_missing_rights_record_is_an_error(
    project: ProjectBundle, png_file: Path
) -> None:
    import_asset(project, png_file)
    project.rights.records = []
    project.save()

    report = validate_project(project)
    assert "missing_rights_record" in _codes(report.errors)
    assert "asset_without_rights_record" in _codes(report.errors)


def test_a_rights_record_covering_no_asset_is_an_error(project: ProjectBundle) -> None:
    project.rights.records = [
        RightsRecord(
            rights_record_id="rights-0001",
            asset_id="asset-0001",
            source="nowhere",
            basis=RightsBasis.UNKNOWN,
            intended_editorial_use="Testing",
            recorded_at=utc_now(),
        )
    ]
    project.save()

    report = validate_project(project)
    assert "orphan_rights_record" in _codes(report.errors)


def test_a_rejected_asset_still_in_the_project_is_an_error(
    project: ProjectBundle, png_file: Path
) -> None:
    import_asset(project, png_file)
    project.rights.records[0].review_status = ReviewStatus.REJECTED
    project.save()

    report = validate_project(project)
    assert "rejected_asset_in_project" in _codes(report.errors)


def test_a_document_belonging_to_another_project_is_an_error(
    store: ProjectStore, project: ProjectBundle
) -> None:
    path = project.root / "script.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["project_id"] = "20260101-some-other-project-bbb222"
    path.write_text(json.dumps(payload), encoding="utf-8")

    report = validate_project(store.load(project.project_id))
    assert "document_project_mismatch" in _codes(report.errors)


def test_a_missing_project_document_is_an_error(project: ProjectBundle) -> None:
    (project.root / "brief.md").unlink()

    report = validate_project(project)
    assert "missing_project_document" in _codes(report.errors)


def test_an_unknown_channel_is_an_error(project: ProjectBundle) -> None:
    project.manifest.channel_id = "no-such-channel"
    project.save()

    report = validate_project(project)
    assert "unknown_channel" in _codes(report.errors)


def test_an_unsafe_stored_asset_path_is_an_error(project: ProjectBundle, png_file: Path) -> None:
    """A manifest hand-edited to traverse out of the project must be rejected."""
    result = import_asset(project, png_file)
    project.save()

    path = project.root / "assets.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["assets"][0]["path"] = "../../../../etc/passwd"
    path.write_text(json.dumps(payload), encoding="utf-8")

    # The schema layer rejects it on load, before any filesystem access.
    from polymation.errors import DocumentDecodeError

    store = ProjectStore(project.workspace)
    with pytest.raises(DocumentDecodeError):
        store.load(project.project_id)

    assert result.destination.is_file()


# --- warnings ------------------------------------------------------------------


def test_unresolved_rights_are_a_warning_not_an_error(
    project: ProjectBundle, png_file: Path
) -> None:
    import_asset(project, png_file)
    project.save()

    report = validate_project(project)
    assert "unresolved_asset_rights" in _codes(report.warnings)
    assert report.structurally_valid


def test_restricted_rights_are_a_warning(project: ProjectBundle, png_file: Path) -> None:
    import_asset(project, png_file)
    project.rights.records[0].review_status = ReviewStatus.RESTRICTED
    project.rights.records[0].usage_restrictions = ["editorial use only"]
    project.save()

    report = validate_project(project)
    warnings = [finding for finding in report.warnings if finding.code == "restricted_asset_rights"]
    assert warnings
    assert "editorial use only" in warnings[0].message


def test_a_duration_outside_the_format_band_is_a_warning(project: ProjectBundle) -> None:
    project.manifest.target_duration_seconds = 1200
    project.save()

    report = validate_project(project)
    assert "duration_outside_format_bounds" in _codes(report.warnings)
    assert report.structurally_valid


def test_a_scene_with_no_shots_is_a_warning(project: ProjectBundle) -> None:
    project.script.scenes = [NarrationScene(scene_id="scene-00", order=0, narration="A")]
    project.storyboard.scenes = [StoryboardScene(scene_id="scene-00", order=0, shots=[])]
    project.save()

    report = validate_project(project)
    assert "scene_without_shots" in _codes(report.warnings)
    assert report.structurally_valid


def test_an_invalidated_approval_is_a_warning(project: ProjectBundle) -> None:
    """When an approved artefact's bytes change, the approval stops applying."""
    from polymation.schemas.approvals import ApprovalRecord, ApprovalStage

    brief = project.root / "brief.md"
    project.approvals.approvals = [
        ApprovalRecord(
            approval_id="approval-0001",
            stage=ApprovalStage.BRIEF,
            artifact_reference="brief.md",
            artifact_checksum=sha256_file(brief),
            approver="project-owner",
            approved_at=utc_now(),
        )
    ]
    project.save()

    assert "approval_invalidated" not in _codes(validate_project(project).warnings)

    brief.write_text("# Rewritten brief\n", encoding="utf-8")
    assert "approval_invalidated" in _codes(validate_project(project).warnings)


def test_a_claim_without_a_source_is_a_warning(project: ProjectBundle) -> None:
    from polymation.schemas.script import FactualClaim

    project.script.claims = [FactualClaim(claim_id="claim-0001", statement="Something factual.")]
    project.save()

    report = validate_project(project)
    assert "claim_without_source" in _codes(report.warnings)


def test_a_claim_referencing_an_unknown_scene_is_an_error(project: ProjectBundle) -> None:
    from polymation.schemas.script import FactualClaim

    project.script.claims = [
        FactualClaim(claim_id="claim-0001", statement="X", scene_id="scene-99", source="A book")
    ]
    project.save()

    report = validate_project(project)
    assert "invalid_scene_reference" in _codes(report.errors)


# --- report shape --------------------------------------------------------------


def test_findings_are_separated_into_errors_and_warnings(
    project: ProjectBundle, png_file: Path
) -> None:
    result = import_asset(project, png_file)
    project.save()
    result.destination.unlink()

    report = validate_project(project)
    assert all(finding.severity is Severity.ERROR for finding in report.errors)
    assert all(finding.severity is Severity.WARNING for finding in report.warnings)
    assert len(report.findings) == len(report.errors) + len(report.warnings)


def test_the_report_serialises_to_json(project: ProjectBundle, png_file: Path) -> None:
    import_asset(project, png_file)
    project.save()

    payload = validate_project(project).to_dict()
    json.dumps(payload)  # must not raise
    assert payload["project_id"] == project.project_id
    assert payload["structurally_valid"] is True
    assert payload["warning_count"] == len(payload["findings"])


# --- rights reporting ----------------------------------------------------------


def test_rights_report_groups_assets_by_status(
    project: ProjectBundle, png_file: Path, tmp_path: Path
) -> None:
    statuses = (
        ReviewStatus.APPROVED,
        ReviewStatus.RESTRICTED,
        ReviewStatus.REJECTED,
        ReviewStatus.UNREVIEWED,
    )
    for index, status in enumerate(statuses):
        source = tmp_path / f"asset-{index}.png"
        Image.new("RGB", (8 + index, 8), (index, index, index)).save(source, format="PNG")
        import_asset(project, source, rights_basis=RightsBasis.ORIGINAL_WORK)
        project.rights.records[index].review_status = status
    project.save()

    report = build_rights_report(project)
    assert len(report.approved) == 1
    assert len(report.restricted) == 1
    assert len(report.rejected) == 1
    assert len(report.unresolved) == 1
    assert report.total_assets == 4
    assert report.blocks_publication


def test_an_asset_with_no_rights_record_is_reported_unresolved(
    project: ProjectBundle, png_file: Path
) -> None:
    """A missing record is an absence of information, never a clearance."""
    import_asset(project, png_file)
    project.rights.records = []
    project.save()

    report = build_rights_report(project)
    assert len(report.unresolved) == 1
    assert report.unresolved[0].status == "missing_record"
    assert report.unresolved[0].basis == "unknown"
    assert report.blocks_publication


def test_a_fully_approved_project_does_not_block_publication(
    project: ProjectBundle, png_file: Path
) -> None:
    import_asset(project, png_file, rights_basis=RightsBasis.ORIGINAL_WORK)
    project.rights.records[0].review_status = ReviewStatus.APPROVED
    project.save()

    report = build_rights_report(project)
    assert not report.blocks_publication
    assert len(report.approved) == 1


def test_the_rights_report_serialises_to_json(project: ProjectBundle, png_file: Path) -> None:
    import_asset(project, png_file)
    project.save()

    payload = build_rights_report(project).to_dict()
    json.dumps(payload)
    assert payload["counts"]["unresolved"] == 1
    assert "publication_note" in payload
