"""Project initialization, identifier stability and manifest persistence."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from polymation.config.paths import PROJECT_SUBDIRECTORIES, Workspace
from polymation.errors import (
    DocumentDecodeError,
    InvalidProjectIdError,
    ProjectExistsError,
    ProjectNotFoundError,
    UnsupportedSchemaVersionError,
)
from polymation.projects.ids import generate_project_id, slugify
from polymation.projects.store import ProjectBundle, ProjectStore, read_document
from polymation.schemas.project import (
    ContentFormat,
    ContentPillar,
    PrimarySubject,
    ProductionStage,
    VideoProject,
)

EXPECTED_DOCUMENTS = (
    "manifest.json",
    "brief.md",
    "script.json",
    "storyboard.json",
    "assets.json",
    "rights.json",
    "approvals.json",
)


# --- slugs and identifiers -----------------------------------------------------


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        (
            "Why Chrono Trigger Feels Like an Adventure",
            "why-chrono-trigger-feels-like-an-adventure",
        ),
        ("Final Fantasy VII: A Retrospective", "final-fantasy-vii-a-retrospective"),
        ("  Spaced   Out  ", "spaced-out"),
        ("Naïve Café Dérive", "naive-cafe-derive"),
        ("1990s!!!", "1990s"),
        ("A---B", "a-b"),
    ],
)
def test_slugify_produces_safe_slugs(title: str, expected: str) -> None:
    assert slugify(title) == expected


def test_slugify_truncates_long_titles_without_a_trailing_hyphen() -> None:
    slug = slugify("a" * 100)
    assert len(slug) <= 64
    assert not slug.endswith("-")


def test_identifier_slug_is_truncated_to_leave_room_for_date_and_suffix() -> None:
    identifier = generate_project_id("word " * 40, suffix="abc123")
    assert len(identifier) <= 64


def test_slugify_pads_a_very_short_slug() -> None:
    assert slugify("FF") == "ff-video"


def test_slugify_rejects_a_title_with_no_usable_characters() -> None:
    with pytest.raises(InvalidProjectIdError, match="no characters usable"):
        slugify("日本語")


def test_generated_identifier_embeds_the_date_and_slug() -> None:
    identifier = generate_project_id(
        "Chrono Trigger", now=datetime(2026, 3, 4, tzinfo=UTC), suffix="abc123"
    )
    assert identifier == "20260304-chrono-trigger-abc123"


def test_generated_identifiers_do_not_collide_for_the_same_title() -> None:
    identifiers = {generate_project_id("Same Title") for _ in range(50)}
    assert len(identifiers) == 50


# --- creation ------------------------------------------------------------------


def test_creation_scaffolds_every_required_directory(project: ProjectBundle) -> None:
    for subdirectory in PROJECT_SUBDIRECTORIES:
        assert (project.root / subdirectory).is_dir(), subdirectory


def test_creation_writes_every_required_document(project: ProjectBundle) -> None:
    for filename in EXPECTED_DOCUMENTS:
        assert (project.root / filename).is_file(), filename


def test_generated_documents_are_valid_against_their_schemas(
    store: ProjectStore, project: ProjectBundle
) -> None:
    reloaded = store.load(project.project_id)
    assert reloaded.manifest == project.manifest
    assert reloaded.script.project_id == project.project_id
    assert reloaded.storyboard.project_id == project.project_id
    assert reloaded.assets.project_id == project.project_id
    assert reloaded.rights.project_id == project.project_id
    assert reloaded.approvals.project_id == project.project_id


def test_manifest_is_readable_indented_json(project: ProjectBundle) -> None:
    raw = (project.root / "manifest.json").read_text(encoding="utf-8")
    assert raw.startswith("{\n")
    assert raw.endswith("\n")
    payload = json.loads(raw)
    assert payload["schema_version"] == "1.0"
    assert payload["project_id"] == project.project_id


def test_a_new_project_starts_as_a_draft(project: ProjectBundle) -> None:
    assert project.manifest.production_stage is ProductionStage.DRAFT
    assert project.storyboard.shot_count == 0
    assert project.assets.assets == []


def test_duplicate_project_creation_is_refused(store: ProjectStore) -> None:
    kwargs = {
        "title": "First",
        "project_id": "20260101-duplicate-test-aaa111",
        "channel_id": "polymation",
        "content_pillar": ContentPillar.ERA_SPOTLIGHT,
        "primary_subject": PrimarySubject.ANIME,
        "content_format": ContentFormat.STANDARD,
        "target_duration_seconds": 200,
        "slug": "duplicate-test",
    }
    store.create(**kwargs)  # type: ignore[arg-type]

    with pytest.raises(ProjectExistsError, match="will not be overwritten"):
        store.create(**{**kwargs, "title": "Second"})  # type: ignore[arg-type]


def test_duplicate_creation_leaves_the_original_untouched(store: ProjectStore) -> None:
    kwargs = {
        "title": "Original Title",
        "project_id": "20260101-overwrite-test-aaa111",
        "channel_id": "polymation",
        "content_pillar": ContentPillar.ERA_SPOTLIGHT,
        "primary_subject": PrimarySubject.ANIME,
        "content_format": ContentFormat.STANDARD,
        "target_duration_seconds": 200,
        "slug": "overwrite-test",
    }
    store.create(**kwargs)  # type: ignore[arg-type]

    with pytest.raises(ProjectExistsError):
        store.create(**{**kwargs, "title": "Replacement Title"})  # type: ignore[arg-type]

    assert store.load("20260101-overwrite-test-aaa111").manifest.title == "Original Title"


def test_creating_a_project_with_an_invalid_identifier_is_refused(store: ProjectStore) -> None:
    with pytest.raises(InvalidProjectIdError):
        store.create(
            title="Bad",
            project_id="../escape",
            channel_id="polymation",
            content_pillar=ContentPillar.ERA_SPOTLIGHT,
            primary_subject=PrimarySubject.ANIME,
            content_format=ContentFormat.STANDARD,
            target_duration_seconds=200,
            slug="bad",
        )


# --- identifier stability ------------------------------------------------------


def test_project_identifier_is_stable_when_the_title_changes(
    store: ProjectStore, project: ProjectBundle
) -> None:
    original_id = project.project_id
    original_root = project.root

    project.manifest.title = "An Entirely Different Title"
    project.manifest.slug = slugify("An Entirely Different Title")
    project.save()

    reloaded = store.load(original_id)
    assert reloaded.project_id == original_id
    assert reloaded.root == original_root
    assert reloaded.manifest.title == "An Entirely Different Title"
    assert reloaded.manifest.slug == "an-entirely-different-title"


# --- listing and loading -------------------------------------------------------


def test_listing_returns_only_directories_holding_a_manifest(
    store: ProjectStore, project: ProjectBundle, workspace: Workspace
) -> None:
    (workspace.projects_dir / "not-a-project").mkdir()
    (workspace.projects_dir / ".hidden").mkdir()
    (workspace.projects_dir / "stray-file.txt").write_text("x", encoding="utf-8")

    assert store.list_project_ids() == [project.project_id]


def test_listing_an_empty_workspace_returns_nothing(store: ProjectStore) -> None:
    assert store.list_project_ids() == []


def test_loading_a_missing_project_raises_not_found(store: ProjectStore) -> None:
    with pytest.raises(ProjectNotFoundError, match="polymation list"):
        store.load("20260101-does-not-exist-aaa111")


def test_loading_a_project_whose_manifest_claims_another_identifier_is_refused(
    store: ProjectStore, project: ProjectBundle
) -> None:
    manifest_path = project.root / "manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["project_id"] = "20260101-some-other-project-bbb222"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(DocumentDecodeError, match="Refusing to guess"):
        store.load(project.project_id)


def test_loading_a_future_schema_version_reports_the_version_problem(
    store: ProjectStore, project: ProjectBundle
) -> None:
    manifest_path = project.root / "manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["schema_version"] = "99.0"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(UnsupportedSchemaVersionError) as caught:
        store.load(project.project_id)

    assert "project manifest" in str(caught.value)
    assert "99.0" in str(caught.value)


def test_loading_malformed_json_reports_the_location(
    store: ProjectStore, project: ProjectBundle
) -> None:
    (project.root / "manifest.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(DocumentDecodeError, match="not valid JSON"):
        store.load(project.project_id)


def test_loading_a_missing_sibling_document_is_reported(
    store: ProjectStore, project: ProjectBundle
) -> None:
    (project.root / "storyboard.json").unlink()

    with pytest.raises(DocumentDecodeError, match="the file is missing"):
        store.load(project.project_id)


def test_reading_a_document_that_fails_schema_validation_is_reported(
    project: ProjectBundle,
) -> None:
    path = project.root / "manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["content_format"] = "vertical-reel"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(DocumentDecodeError, match="not a valid project manifest"):
        read_document(path, VideoProject)


# --- atomic writes -------------------------------------------------------------


def test_saving_leaves_no_temporary_files_behind(project: ProjectBundle) -> None:
    project.save()
    stray = [entry.name for entry in project.root.iterdir() if entry.name.endswith(".tmp")]
    assert stray == []


def test_workspace_discovery_prefers_the_environment_variable(
    workspace: Workspace, tmp_path: Path
) -> None:
    from polymation.config.paths import find_workspace_root

    assert find_workspace_root(tmp_path) == workspace.root
