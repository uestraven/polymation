"""Schema contracts: versioning, validation rules and the rights approval rules."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from polymation.errors import InvalidProjectIdError, UnsupportedSchemaVersionError
from polymation.schemas.approvals import (
    ApprovalDecision,
    ApprovalLedger,
    ApprovalRecord,
    ApprovalStage,
)
from polymation.schemas.assets import AssetCategory, AssetCollection, AssetType, MediaAsset
from polymation.schemas.base import SCHEMA_VERSION, utc_now
from polymation.schemas.project import (
    ContentFormat,
    ContentPillar,
    PrimarySubject,
    VideoProject,
    validate_project_id,
)
from polymation.schemas.rights import ReviewStatus, RightsBasis, RightsLedger, RightsRecord
from polymation.schemas.script import NarrationScene, NarrationStatus, ScriptDocument
from polymation.schemas.storyboard import FitMode, Storyboard, StoryboardScene, VisualShot

CHECKSUM = "a" * 64
NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _manifest(**overrides: object) -> VideoProject:
    payload: dict[str, object] = {
        "project_id": "20260101-test-project-abc123",
        "title": "Test Project",
        "slug": "test-project",
        "channel_id": "polymation",
        "content_pillar": ContentPillar.STORY_WORLD_DEEP_DIVE,
        "primary_subject": PrimarySubject.JRPG,
        "content_format": ContentFormat.STANDARD,
        "target_duration_seconds": 240,
        "created_at": NOW,
        "updated_at": NOW,
    }
    payload.update(overrides)
    return VideoProject(**payload)  # type: ignore[arg-type]


def _asset(**overrides: object) -> MediaAsset:
    payload: dict[str, object] = {
        "asset_id": "asset-0001",
        "asset_type": AssetType.IMAGE,
        "category": AssetCategory.IMAGES,
        "path": "assets/images/a.png",
        "detected_format": "PNG",
        "size_bytes": 100,
        "sha256": CHECKSUM,
        "width": 1920,
        "height": 1080,
        "original_source": "generated",
        "acquired_at": NOW,
        "rights_record_id": "rights-0001",
    }
    payload.update(overrides)
    return MediaAsset(**payload)  # type: ignore[arg-type]


def _rights(**overrides: object) -> RightsRecord:
    payload: dict[str, object] = {
        "rights_record_id": "rights-0001",
        "asset_id": "asset-0001",
        "source": "generated",
        "basis": RightsBasis.ORIGINAL_WORK,
        "intended_editorial_use": "Testing",
        "recorded_at": NOW,
    }
    payload.update(overrides)
    return RightsRecord(**payload)  # type: ignore[arg-type]


# --- schema versioning ---------------------------------------------------------


def test_documents_declare_the_current_schema_version() -> None:
    assert _manifest().schema_version == SCHEMA_VERSION


def test_unsupported_schema_version_is_rejected_with_an_actionable_message() -> None:
    payload = _manifest().model_dump(mode="json")
    payload["schema_version"] = "99.0"

    with pytest.raises(UnsupportedSchemaVersionError) as caught:
        VideoProject.assert_readable_version(payload)

    message = str(caught.value)
    assert "99.0" in message
    assert SCHEMA_VERSION in message
    assert "Upgrade Polymation Studio" in message


def test_unsupported_schema_version_also_fails_field_validation() -> None:
    with pytest.raises(ValidationError, match="unsupported schema_version"):
        _manifest(schema_version="99.0")


def test_supported_schema_version_passes_the_pre_check() -> None:
    VideoProject.assert_readable_version(_manifest().model_dump(mode="json"))


# --- manifest ------------------------------------------------------------------


def test_manifest_round_trips_through_json() -> None:
    original = _manifest()
    restored = VideoProject.model_validate(original.model_dump(mode="json"))
    assert restored == original


def test_unknown_fields_are_rejected_rather_than_silently_dropped() -> None:
    payload = _manifest().model_dump(mode="json")
    payload["unexpected_field"] = "surprise"

    with pytest.raises(ValidationError):
        VideoProject.model_validate(payload)


def test_naive_timestamps_are_rejected() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        _manifest(created_at=datetime(2026, 1, 1))


@pytest.mark.parametrize(
    "candidate",
    [
        "",
        " ",
        "UPPERCASE",
        "has spaces",
        "../traversal",
        "a",
        "ab",
        "-leading",
        "trailing-",
        "double--hyphen",
        "a" * 65,
        "has_underscore",
        "has.dot",
        "has/slash",
    ],
)
def test_invalid_project_identifiers_are_rejected(candidate: str) -> None:
    with pytest.raises(InvalidProjectIdError):
        validate_project_id(candidate)


def test_duration_bounds_are_reported_per_format() -> None:
    assert _manifest(content_format=ContentFormat.STANDARD).duration_bounds == (120, 300)
    assert _manifest(
        content_format=ContentFormat.SHORT, target_duration_seconds=45
    ).duration_bounds == (30, 60)


def test_duration_outside_format_bounds_is_detected_not_rejected() -> None:
    """Out-of-band durations are an editorial warning, not a schema error."""
    manifest = _manifest(target_duration_seconds=900)
    assert not manifest.duration_is_in_format_bounds()


# --- assets --------------------------------------------------------------------


def test_asset_rejects_a_malformed_checksum() -> None:
    with pytest.raises(ValidationError, match="hexadecimal digest"):
        _asset(sha256="not-a-checksum")


def test_asset_rejects_an_uppercase_checksum() -> None:
    with pytest.raises(ValidationError):
        _asset(sha256="A" * 64)


def test_asset_rejects_a_traversing_path() -> None:
    with pytest.raises(ValidationError):
        _asset(path="../../etc/passwd")


def test_image_asset_requires_dimensions() -> None:
    with pytest.raises(ValidationError, match="width and height"):
        _asset(width=None, height=None)


def test_audio_asset_must_not_carry_pixel_dimensions() -> None:
    with pytest.raises(ValidationError, match="must not record pixel dimensions"):
        _asset(
            asset_type=AssetType.AUDIO,
            category=AssetCategory.MUSIC,
            duration_seconds=5.0,
            width=100,
            height=100,
        )


def test_audio_asset_requires_a_duration() -> None:
    with pytest.raises(ValidationError, match="must record a duration"):
        _asset(
            asset_type=AssetType.AUDIO,
            category=AssetCategory.MUSIC,
            width=None,
            height=None,
            duration_seconds=None,
        )


def test_asset_category_must_suit_the_asset_type() -> None:
    with pytest.raises(ValidationError, match="not valid for a image asset"):
        _asset(category=AssetCategory.MUSIC)


def test_asset_collection_rejects_duplicate_identifiers() -> None:
    with pytest.raises(ValidationError, match="duplicate asset_id"):
        AssetCollection(
            project_id="p",
            created_at=NOW,
            updated_at=NOW,
            assets=[_asset(), _asset(path="assets/images/b.png")],
        )


def test_asset_collection_rejects_two_assets_at_the_same_path() -> None:
    with pytest.raises(ValidationError, match="same path"):
        AssetCollection(
            project_id="p",
            created_at=NOW,
            updated_at=NOW,
            assets=[_asset(), _asset(asset_id="asset-0002", sha256="b" * 64)],
        )


def test_asset_lookup_by_checksum_finds_identical_content() -> None:
    collection = AssetCollection(project_id="p", created_at=NOW, updated_at=NOW, assets=[_asset()])
    assert collection.by_checksum(CHECKSUM) is not None
    assert collection.by_checksum("b" * 64) is None


# --- rights --------------------------------------------------------------------


def test_unknown_rights_can_never_be_approved() -> None:
    with pytest.raises(ValidationError, match="rights are unknown"):
        _rights(basis=RightsBasis.UNKNOWN, review_status=ReviewStatus.APPROVED)


@pytest.mark.parametrize(
    "basis",
    [RightsBasis.USER_SUPPLIED, RightsBasis.PROPOSED_FAIR_USE],
)
def test_user_supplied_and_fair_use_require_a_human_approval_record(
    basis: RightsBasis,
) -> None:
    with pytest.raises(ValidationError, match="requires an explicit human approval"):
        _rights(basis=basis, review_status=ReviewStatus.APPROVED)


def test_proposed_fair_use_can_be_approved_only_with_a_human_approval_record() -> None:
    approval = ApprovalRecord(
        approval_id="approval-0001",
        stage=ApprovalStage.ASSET_RIGHTS,
        artifact_reference="assets/images/a.png",
        artifact_checksum=CHECKSUM,
        approver="project-owner",
        approved_at=NOW,
    )
    record = _rights(
        basis=RightsBasis.PROPOSED_FAIR_USE,
        review_status=ReviewStatus.APPROVED,
        human_approval=approval,
    )
    assert record.is_cleared_for_use


def test_licensed_material_requires_a_licence_identifier() -> None:
    with pytest.raises(ValidationError, match="requires a license_identifier"):
        _rights(basis=RightsBasis.LICENSED)


def test_attribution_required_demands_attribution_text() -> None:
    with pytest.raises(ValidationError, match="no attribution_text"):
        _rights(attribution_required=True)


def test_unreviewed_rights_are_unresolved_and_block_publication() -> None:
    record = _rights(review_status=ReviewStatus.UNREVIEWED)
    assert not record.is_resolved
    assert not record.is_cleared_for_use
    assert record.blocks_publication


def test_restricted_rights_are_resolved_but_still_block_publication() -> None:
    record = _rights(review_status=ReviewStatus.RESTRICTED, usage_restrictions=["no thumbnails"])
    assert record.is_resolved
    assert not record.is_cleared_for_use
    assert record.blocks_publication


def test_original_work_may_be_approved_without_a_separate_approval_record() -> None:
    record = _rights(basis=RightsBasis.ORIGINAL_WORK, review_status=ReviewStatus.APPROVED)
    assert record.is_cleared_for_use


def test_rights_ledger_rejects_duplicate_record_identifiers() -> None:
    with pytest.raises(ValidationError, match="duplicate rights_record_id"):
        RightsLedger(
            project_id="p",
            created_at=NOW,
            updated_at=NOW,
            records=[_rights(), _rights(asset_id="asset-0002")],
        )


# --- script and storyboard -----------------------------------------------------


def test_narration_scenes_and_visual_shots_are_separate_concepts() -> None:
    """One narration scene can carry several visual shots."""
    script = ScriptDocument(
        project_id="p",
        created_at=NOW,
        updated_at=NOW,
        scenes=[NarrationScene(scene_id="scene-01", order=0, narration="One beat.")],
    )
    storyboard = Storyboard(
        project_id="p",
        created_at=NOW,
        updated_at=NOW,
        scenes=[
            StoryboardScene(
                scene_id="scene-01",
                order=0,
                shots=[
                    VisualShot(
                        shot_id=f"shot-{index:02d}",
                        scene_id="scene-01",
                        description=f"Shot {index}",
                        duration_seconds=2.0,
                    )
                    for index in range(1, 4)
                ],
            )
        ],
    )

    assert len(script.scenes) == 1
    assert storyboard.shot_count == 3
    assert storyboard.duration_seconds == pytest.approx(6.0)


def test_draft_and_approved_narration_are_distinguished() -> None:
    script = ScriptDocument(
        project_id="p",
        created_at=NOW,
        updated_at=NOW,
        scenes=[
            NarrationScene(scene_id="scene-01", order=0, narration="Draft."),
            NarrationScene(
                scene_id="scene-02",
                order=1,
                narration="Approved.",
                status=NarrationStatus.APPROVED,
            ),
        ],
    )

    assert [scene.scene_id for scene in script.draft_scenes] == ["scene-01"]
    assert [scene.scene_id for scene in script.approved_scenes] == ["scene-02"]
    assert not script.is_fully_approved


def test_an_empty_script_is_never_fully_approved() -> None:
    script = ScriptDocument(project_id="p", created_at=NOW, updated_at=NOW)
    assert not script.is_fully_approved


def test_script_rejects_duplicate_scene_identifiers() -> None:
    with pytest.raises(ValidationError, match="duplicate narration scene_id"):
        ScriptDocument(
            project_id="p",
            created_at=NOW,
            updated_at=NOW,
            scenes=[
                NarrationScene(scene_id="scene-01", order=0, narration="A"),
                NarrationScene(scene_id="scene-01", order=1, narration="B"),
            ],
        )


@pytest.mark.parametrize("duration", [0, -1.0, 0.001, 1000.0])
def test_invalid_shot_durations_are_rejected(duration: float) -> None:
    with pytest.raises(ValidationError):
        VisualShot(
            shot_id="shot-01",
            scene_id="scene-01",
            description="Test",
            duration_seconds=duration,
        )


def test_shot_nested_under_a_mismatched_scene_is_rejected() -> None:
    with pytest.raises(ValidationError, match="declares scene_id"):
        Storyboard(
            project_id="p",
            created_at=NOW,
            updated_at=NOW,
            scenes=[
                StoryboardScene(
                    scene_id="scene-01",
                    order=0,
                    shots=[
                        VisualShot(
                            shot_id="shot-01",
                            scene_id="scene-99",
                            description="Mismatched",
                            duration_seconds=2.0,
                        )
                    ],
                )
            ],
        )


def test_shots_are_returned_in_scene_order() -> None:
    storyboard = Storyboard(
        project_id="p",
        created_at=NOW,
        updated_at=NOW,
        scenes=[
            StoryboardScene(
                scene_id="scene-02",
                order=1,
                shots=[
                    VisualShot(
                        shot_id="shot-02",
                        scene_id="scene-02",
                        description="Second",
                        duration_seconds=1.0,
                        fit_mode=FitMode.COVER,
                    )
                ],
            ),
            StoryboardScene(
                scene_id="scene-01",
                order=0,
                shots=[
                    VisualShot(
                        shot_id="shot-01",
                        scene_id="scene-01",
                        description="First",
                        duration_seconds=1.0,
                    )
                ],
            ),
        ],
    )

    assert [shot.shot_id for shot in storyboard.shots] == ["shot-01", "shot-02"]


# --- approvals -----------------------------------------------------------------


def test_an_approval_covers_only_the_bytes_it_was_recorded_against() -> None:
    approval = ApprovalRecord(
        approval_id="approval-0001",
        stage=ApprovalStage.SCRIPT,
        artifact_reference="script.json",
        artifact_checksum=CHECKSUM,
        approver="project-owner",
        approved_at=NOW,
    )

    assert approval.covers(artifact_reference="script.json", artifact_checksum=CHECKSUM)
    # Content changed -> the approval no longer applies.
    assert not approval.covers(artifact_reference="script.json", artifact_checksum="b" * 64)
    # Different artefact -> not covered either.
    assert not approval.covers(artifact_reference="storyboard.json", artifact_checksum=CHECKSUM)


def test_a_rejection_never_counts_as_coverage() -> None:
    approval = ApprovalRecord(
        approval_id="approval-0001",
        stage=ApprovalStage.SCRIPT,
        artifact_reference="script.json",
        artifact_checksum=CHECKSUM,
        decision=ApprovalDecision.REJECTED,
        approver="project-owner",
        approved_at=NOW,
    )
    assert not approval.covers(artifact_reference="script.json", artifact_checksum=CHECKSUM)


def test_ledger_reports_the_latest_approval_for_a_stage() -> None:
    earlier = ApprovalRecord(
        approval_id="approval-0001",
        stage=ApprovalStage.SCRIPT,
        artifact_reference="script.json",
        artifact_checksum="b" * 64,
        approver="project-owner",
        approved_at=NOW,
    )
    later = ApprovalRecord(
        approval_id="approval-0002",
        stage=ApprovalStage.SCRIPT,
        artifact_reference="script.json",
        artifact_checksum=CHECKSUM,
        approver="project-owner",
        approved_at=NOW + timedelta(days=1),
    )
    ledger = ApprovalLedger(
        project_id="p", created_at=NOW, updated_at=NOW, approvals=[earlier, later]
    )

    assert ledger.latest_for_stage(ApprovalStage.SCRIPT) is later
    assert ledger.latest_for_stage(ApprovalStage.RENDER) is None
    assert ledger.has_valid_approval(
        stage=ApprovalStage.SCRIPT,
        artifact_reference="script.json",
        artifact_checksum=CHECKSUM,
    )
    assert not ledger.has_valid_approval(
        stage=ApprovalStage.SCRIPT,
        artifact_reference="script.json",
        artifact_checksum="c" * 64,
    )


def test_touch_advances_the_modification_timestamp() -> None:
    manifest = _manifest()
    before = manifest.updated_at
    manifest.touch()
    assert manifest.updated_at > before
    assert manifest.created_at == NOW
    assert manifest.updated_at <= utc_now()
