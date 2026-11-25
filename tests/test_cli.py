"""CLI behaviour: output, JSON mode and exit codes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from polymation import __version__
from polymation.cli import app
from polymation.config.paths import Workspace
from polymation.errors import ExitCode, PolymationError
from polymation.media.importer import import_asset
from polymation.projects.store import ProjectBundle


def invoke(runner: CliRunner, arguments: list[str]) -> Result:
    """Run the CLI, letting library errors surface for assertion."""
    return runner.invoke(app, arguments, catch_exceptions=True)


def payload_of(result: Result) -> dict:
    """Parse a ``--json`` command's stdout."""
    return json.loads(result.stdout)


# --- top level -----------------------------------------------------------------


def test_version_is_reported(runner: CliRunner) -> None:
    result = invoke(runner, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_bare_invocation_shows_help_and_succeeds(runner: CliRunner) -> None:
    result = invoke(runner, [])
    assert result.exit_code == 0
    assert "Polymation Studio" in result.stdout
    for command in ("doctor", "new", "list", "status", "validate", "asset", "rights", "demo"):
        assert command in result.stdout


# --- doctor --------------------------------------------------------------------


def test_doctor_reports_every_dependency(runner: CliRunner, workspace: Workspace) -> None:
    result = invoke(runner, ["doctor", "--workspace", str(workspace.root), "--json"])
    payload = payload_of(result)

    names = {check["name"] for check in payload["checks"]}
    assert {"python", "platform", "ffmpeg", "ffprobe", "channel configuration"} <= names
    assert "python package: pydantic" in names
    assert payload["workspace_root"] == str(workspace.root)


def test_doctor_exit_code_reflects_readiness(runner: CliRunner, workspace: Workspace) -> None:
    result = invoke(runner, ["doctor", "--workspace", str(workspace.root), "--json"])
    expected = ExitCode.OK if payload_of(result)["ready"] else ExitCode.MISSING_DEPENDENCY
    assert result.exit_code == int(expected)


def test_doctor_explains_how_to_install_a_missing_dependency(
    runner: CliRunner, workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(workspace.root))  # hide ffmpeg/ffprobe
    monkeypatch.delenv("POLYMATION_FFMPEG", raising=False)
    monkeypatch.delenv("POLYMATION_FFPROBE", raising=False)

    result = invoke(runner, ["doctor", "--workspace", str(workspace.root), "--json"])
    payload = payload_of(result)

    assert payload["ready"] is False
    assert any("ffmpeg" in hint for hint in payload["remediation"])
    assert result.exit_code == int(ExitCode.MISSING_DEPENDENCY)


# --- new / list / status -------------------------------------------------------


def test_new_creates_a_project(runner: CliRunner, workspace: Workspace) -> None:
    result = invoke(
        runner,
        [
            "new",
            "Why Chrono Trigger Feels Like an Adventure",
            "--workspace",
            str(workspace.root),
            "--pillar",
            "story_world_deep_dive",
            "--subject",
            "jrpg",
            "--format",
            "standard",
            "--duration",
            "240",
            "--json",
        ],
    )
    assert result.exit_code == 0

    payload = payload_of(result)
    assert payload["title"] == "Why Chrono Trigger Feels Like an Adventure"
    assert payload["target_duration_seconds"] == 240
    assert payload["production_stage"] == "draft"
    assert (Path(payload["path"]) / "manifest.json").is_file()


def test_new_defaults_duration_to_the_format_midpoint(
    runner: CliRunner, workspace: Workspace
) -> None:
    result = invoke(
        runner,
        ["new", "Shorts Test", "--format", "short", "--workspace", str(workspace.root), "--json"],
    )
    assert payload_of(result)["target_duration_seconds"] == 45


def test_new_refuses_to_overwrite_an_existing_project(
    runner: CliRunner, workspace: Workspace
) -> None:
    arguments = [
        "new",
        "First",
        "--project-id",
        "20260101-cli-conflict-aaa111",
        "--workspace",
        str(workspace.root),
    ]
    assert invoke(runner, arguments).exit_code == 0

    result = invoke(
        runner,
        [
            *arguments[:2],
            "--project-id",
            "20260101-cli-conflict-aaa111",
            "--workspace",
            str(workspace.root),
        ],
    )
    assert isinstance(result.exception, PolymationError)
    assert result.exception.exit_code == ExitCode.CONFLICT


def test_new_rejects_an_unsafe_project_identifier(runner: CliRunner, workspace: Workspace) -> None:
    result = invoke(
        runner,
        ["new", "Bad", "--project-id", "../escape", "--workspace", str(workspace.root)],
    )
    assert isinstance(result.exception, PolymationError)
    assert result.exception.exit_code == ExitCode.UNSAFE_INPUT


def test_new_rejects_an_unknown_pillar(runner: CliRunner, workspace: Workspace) -> None:
    result = invoke(
        runner, ["new", "Bad", "--pillar", "not-a-pillar", "--workspace", str(workspace.root)]
    )
    assert result.exit_code == 2


def test_list_reports_an_empty_workspace_helpfully(runner: CliRunner, workspace: Workspace) -> None:
    result = invoke(runner, ["list", "--workspace", str(workspace.root)])
    assert result.exit_code == 0
    assert "No projects" in result.stdout
    assert "polymation new" in result.stdout


def test_list_shows_created_projects(
    runner: CliRunner, workspace: Workspace, project: ProjectBundle
) -> None:
    result = invoke(runner, ["list", "--workspace", str(workspace.root), "--json"])
    identifiers = [row["project_id"] for row in payload_of(result)["projects"]]
    assert project.project_id in identifiers


def test_status_reports_metadata_counts_and_readiness(
    runner: CliRunner, workspace: Workspace, project: ProjectBundle
) -> None:
    result = invoke(
        runner, ["status", project.project_id, "--workspace", str(workspace.root), "--json"]
    )
    payload = payload_of(result)

    assert payload["project_id"] == project.project_id
    assert payload["counts"]["assets"] == 0
    assert payload["structurally_valid"] is True
    assert payload["render_ready"] is False
    assert payload["publication_ready"] is None


def test_status_of_a_missing_project_exits_not_found(
    runner: CliRunner, workspace: Workspace
) -> None:
    result = invoke(
        runner, ["status", "20260101-missing-project-aaa111", "--workspace", str(workspace.root)]
    )
    assert isinstance(result.exception, PolymationError)
    assert result.exception.exit_code == ExitCode.NOT_FOUND


# --- validate ------------------------------------------------------------------


def test_validate_passes_for_an_empty_draft(
    runner: CliRunner, workspace: Workspace, project: ProjectBundle
) -> None:
    result = invoke(runner, ["validate", project.project_id, "--workspace", str(workspace.root)])
    assert result.exit_code == int(ExitCode.OK)


def test_validate_emits_machine_readable_output(
    runner: CliRunner, workspace: Workspace, project: ProjectBundle, png_file: Path
) -> None:
    import_asset(project, png_file)
    project.save()

    result = invoke(
        runner, ["validate", project.project_id, "--workspace", str(workspace.root), "--json"]
    )
    payload = payload_of(result)

    assert payload["structurally_valid"] is True
    assert payload["warning_count"] >= 1
    assert "render_readiness" in payload
    assert payload["render_readiness"]["publication_ready"] is None


def test_validate_fails_when_an_asset_file_is_missing(
    runner: CliRunner, workspace: Workspace, project: ProjectBundle, png_file: Path
) -> None:
    result = import_asset(project, png_file)
    project.save()
    result.destination.unlink()

    outcome = invoke(runner, ["validate", project.project_id, "--workspace", str(workspace.root)])
    assert outcome.exit_code == int(ExitCode.VALIDATION_FAILED)
    assert "missing_asset_file" in outcome.stdout


def test_strict_mode_turns_warnings_into_a_failure(
    runner: CliRunner, workspace: Workspace, project: ProjectBundle, png_file: Path
) -> None:
    import_asset(project, png_file)
    project.save()

    lenient = invoke(runner, ["validate", project.project_id, "--workspace", str(workspace.root)])
    strict = invoke(
        runner, ["validate", project.project_id, "--workspace", str(workspace.root), "--strict"]
    )

    assert lenient.exit_code == int(ExitCode.OK)
    assert strict.exit_code == int(ExitCode.VALIDATION_FAILED)


# --- asset commands ------------------------------------------------------------


def test_asset_import_through_the_cli(
    runner: CliRunner, workspace: Workspace, project: ProjectBundle, png_file: Path
) -> None:
    result = invoke(
        runner,
        [
            "asset",
            "import",
            project.project_id,
            str(png_file),
            "--workspace",
            str(workspace.root),
            "--basis",
            "original_work",
            "--rights-holder",
            "A Person",
            "--json",
        ],
    )
    assert result.exit_code == 0

    payload = payload_of(result)
    assert payload["asset_id"] == "asset-0001"
    assert payload["review_status"] == "unreviewed"
    assert payload["width"] == 64
    assert len(payload["sha256"]) == 64


def test_asset_import_warns_that_the_rights_record_needs_review(
    runner: CliRunner, workspace: Workspace, project: ProjectBundle, png_file: Path
) -> None:
    result = invoke(
        runner,
        ["asset", "import", project.project_id, str(png_file), "--workspace", str(workspace.root)],
    )
    assert "unreviewed" in result.stdout
    assert "human must review" in result.stdout


def test_asset_import_rejects_an_unsafe_destination_name(
    runner: CliRunner, workspace: Workspace, project: ProjectBundle, png_file: Path
) -> None:
    result = invoke(
        runner,
        [
            "asset",
            "import",
            project.project_id,
            str(png_file),
            "--as",
            "../../escaped.png",
            "--workspace",
            str(workspace.root),
        ],
    )
    assert isinstance(result.exception, PolymationError)
    assert result.exception.exit_code == ExitCode.UNSAFE_INPUT


def test_asset_import_refuses_a_duplicate(
    runner: CliRunner, workspace: Workspace, project: ProjectBundle, png_file: Path
) -> None:
    arguments = [
        "asset",
        "import",
        project.project_id,
        str(png_file),
        "--workspace",
        str(workspace.root),
    ]
    assert invoke(runner, arguments).exit_code == 0

    result = invoke(runner, arguments)
    assert isinstance(result.exception, PolymationError)
    assert result.exception.exit_code == ExitCode.CONFLICT


def test_asset_list_reports_imported_assets(
    runner: CliRunner, workspace: Workspace, project: ProjectBundle, png_file: Path
) -> None:
    import_asset(project, png_file)
    project.save()

    result = invoke(
        runner, ["asset", "list", project.project_id, "--workspace", str(workspace.root), "--json"]
    )
    assets = payload_of(result)["assets"]
    assert len(assets) == 1
    assert assets[0]["file_present"] is True
    assert assets[0]["review_status"] == "unreviewed"


def test_asset_list_flags_a_missing_file(
    runner: CliRunner, workspace: Workspace, project: ProjectBundle, png_file: Path
) -> None:
    result = import_asset(project, png_file)
    project.save()
    result.destination.unlink()

    outcome = invoke(
        runner, ["asset", "list", project.project_id, "--workspace", str(workspace.root)]
    )
    assert "FILE MISSING" in outcome.stdout


def test_asset_list_is_helpful_when_empty(
    runner: CliRunner, workspace: Workspace, project: ProjectBundle
) -> None:
    result = invoke(
        runner, ["asset", "list", project.project_id, "--workspace", str(workspace.root)]
    )
    assert "No assets" in result.stdout


# --- rights --------------------------------------------------------------------


def test_rights_report_lists_unresolved_assets(
    runner: CliRunner, workspace: Workspace, project: ProjectBundle, png_file: Path
) -> None:
    import_asset(project, png_file)
    project.save()

    result = invoke(
        runner,
        ["rights", "report", project.project_id, "--workspace", str(workspace.root), "--json"],
    )
    payload = payload_of(result)

    assert payload["counts"]["unresolved"] == 1
    assert payload["counts"]["approved"] == 0
    assert payload["blocks_publication"] is True


def test_rights_report_text_output_shows_all_four_statuses(
    runner: CliRunner, workspace: Workspace, project: ProjectBundle, png_file: Path
) -> None:
    import_asset(project, png_file)
    project.save()

    result = invoke(
        runner, ["rights", "report", project.project_id, "--workspace", str(workspace.root)]
    )
    for label in ("Approved", "Restricted", "Rejected", "Unresolved"):
        assert label in result.stdout


# --- fonts ---------------------------------------------------------------------


def test_fonts_reports_availability_without_claiming_fidelity(
    runner: CliRunner, workspace: Workspace
) -> None:
    result = invoke(runner, ["fonts", "--workspace", str(workspace.root), "--json"])
    payload = payload_of(result)

    families = {entry["family"] for entry in payload["fonts"]}
    assert {"Space Grotesk", "IBM Plex Serif", "Silkscreen"} == families
    assert payload["brand_provisional"] is True
    for entry in payload["fonts"]:
        assert isinstance(entry["available"], bool)
