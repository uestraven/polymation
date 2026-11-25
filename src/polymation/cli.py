"""The ``polymation`` command-line interface.

Structure: every command resolves the workspace, delegates to a library module,
and formats the result. No business logic lives here, so the same behaviour is
reachable from tests and from future callers without going through Typer.

Error contract: library code raises :class:`~polymation.errors.PolymationError`
subclasses carrying their own exit code; :func:`main` maps them to a message on
stderr and that exit code. See :class:`~polymation.errors.ExitCode`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated, Any, NoReturn

import typer

from polymation import __version__
from polymation.cli_render import (
    cache_app,
    inspect_command,
    reference_app,
    render_command,
    timeline_app,
)
from polymation.config.channel import DEFAULT_CHANNEL_ID, ChannelConfig, load_channel_config
from polymation.config.paths import Workspace
from polymation.doctor import run_diagnostics
from polymation.errors import ExitCode, PolymationError
from polymation.media.importer import import_asset
from polymation.projects.ids import generate_project_id, slugify
from polymation.projects.store import ProjectBundle, ProjectStore
from polymation.rendering.compose import inspect_output, render_project
from polymation.rendering.demo import (
    DEMO_PROJECT_ID,
    DEMO_TOTAL_DURATION_SECONDS,
    create_demo_project,
)
from polymation.rendering.fixtures import brand_font_report
from polymation.rights.report import build_rights_report
from polymation.schemas.assets import AssetCategory
from polymation.schemas.project import (
    FORMAT_DURATION_BOUNDS,
    ContentFormat,
    ContentPillar,
    PrimarySubject,
)
from polymation.schemas.rights import RightsBasis
from polymation.validation.validator import assess_render_readiness, validate_project

app = typer.Typer(
    name="polymation",
    help="Polymation Studio — internal media production platform (M0.1).",
    # Not no_args_is_help: Click >=8.2 makes that a usage error (exit 2). The
    # root callback shows help and exits 0 instead, which is stable across the
    # supported Click range.
    no_args_is_help=False,
    add_completion=False,
)

asset_app = typer.Typer(help="Import and inspect project media assets.", no_args_is_help=True)
rights_app = typer.Typer(help="Report on media rights and provenance.", no_args_is_help=True)
demo_app = typer.Typer(help="Build and render the technical demonstration.", no_args_is_help=True)

app.add_typer(asset_app, name="asset")
app.add_typer(rights_app, name="rights")
app.add_typer(demo_app, name="demo")

# M0.2 composition engine commands, defined in polymation.cli_render.
app.add_typer(timeline_app, name="timeline")
app.add_typer(cache_app, name="cache")
app.add_typer(reference_app, name="reference")
app.command("render")(render_command)
app.command("inspect")(inspect_command)

WorkspaceOption = Annotated[
    Path | None,
    typer.Option(
        "--workspace",
        help="Workspace root. Defaults to $POLYMATION_HOME, else the nearest "
        "ancestor directory containing channels/.",
        show_default=False,
    ),
]

JsonOption = Annotated[
    bool,
    typer.Option("--json", help="Emit machine-readable JSON instead of formatted text."),
]


def _echo_json(payload: dict[str, Any]) -> None:
    typer.echo(json.dumps(payload, indent=2, ensure_ascii=False, default=str))


def _resolve_workspace(explicit: Path | None) -> Workspace:
    if explicit is not None:
        return Workspace(root=explicit.expanduser().resolve())
    return Workspace.discover()


def _load_channel(workspace: Workspace, channel_id: str) -> ChannelConfig:
    return load_channel_config(workspace, channel_id)


def _load_bundle(workspace: Workspace, project_id: str) -> ProjectBundle:
    return ProjectStore(workspace).load(project_id)


def _fail(message: str, code: ExitCode) -> NoReturn:
    """Print ``message`` to stderr and terminate with ``code``.

    Raises ``SystemExit`` rather than ``typer.Exit``: this runs from
    :func:`main`, outside Click's invocation, where a ``typer.Exit`` would
    escape as an uncaught exception and print a traceback.
    """
    typer.secho(message, fg=typer.colors.RED, err=True)
    raise SystemExit(int(code))


@app.callback(invoke_without_command=True)
def _root(
    context: typer.Context,
    version: Annotated[
        bool, typer.Option("--version", help="Show the installed version and exit.")
    ] = False,
) -> None:
    """Polymation Studio command-line interface.

    ``invoke_without_command=True`` is required so that ``--version`` works
    without naming a subcommand; the help text is shown when neither a
    subcommand nor ``--version`` is given.
    """
    if version:
        typer.echo(f"polymation {__version__}")
        raise typer.Exit(int(ExitCode.OK))

    if context.invoked_subcommand is None:
        typer.echo(context.get_help())
        raise typer.Exit(int(ExitCode.OK))


@app.command()
def doctor(workspace: WorkspaceOption = None, as_json: JsonOption = False) -> None:
    """Report dependency availability, versions and readiness."""
    report = run_diagnostics(_resolve_workspace(workspace))

    if as_json:
        _echo_json(report.to_dict())
    else:
        typer.secho(f"Polymation Studio {__version__} — environment", bold=True)
        typer.echo(f"Workspace root: {report.workspace_root}")
        typer.echo("")
        for check in report.checks:
            if check.ok:
                marker, colour = "ok  ", typer.colors.GREEN
            elif check.required:
                marker, colour = "FAIL", typer.colors.RED
            else:
                marker, colour = "warn", typer.colors.YELLOW
            typer.secho(f"  [{marker}] {check.name}: {check.detail}", fg=colour)

        if report.remediation:
            typer.echo("")
            typer.secho("Remediation:", bold=True)
            for hint in report.remediation:
                for line in hint.splitlines():
                    typer.echo(f"  {line}")

        typer.echo("")
        if report.ready:
            typer.secho("All required dependencies are available.", fg=typer.colors.GREEN)
        else:
            missing = ", ".join(check.name for check in report.required_failures)
            typer.secho(f"Missing required dependencies: {missing}", fg=typer.colors.RED)

    raise typer.Exit(int(ExitCode.OK if report.ready else ExitCode.MISSING_DEPENDENCY))


@app.command()
def new(
    title: Annotated[str, typer.Argument(help="Human-readable video title.")],
    pillar: Annotated[
        ContentPillar, typer.Option("--pillar", help="Channel content pillar.")
    ] = ContentPillar.STORY_WORLD_DEEP_DIVE,
    subject: Annotated[
        PrimarySubject, typer.Option("--subject", help="Primary editorial subject.")
    ] = PrimarySubject.JRPG,
    content_format: Annotated[
        ContentFormat, typer.Option("--format", help="Output format.")
    ] = ContentFormat.STANDARD,
    duration: Annotated[
        int | None,
        typer.Option(
            "--duration",
            help="Target duration in seconds. Defaults to the midpoint of the "
            "format's editorial range.",
            show_default=False,
        ),
    ] = None,
    project_id: Annotated[
        str | None,
        typer.Option(
            "--project-id",
            help="Explicit project identifier. Generated from the title when omitted.",
            show_default=False,
        ),
    ] = None,
    channel: Annotated[str, typer.Option("--channel", help="Channel identifier.")] = (
        DEFAULT_CHANNEL_ID
    ),
    workspace: WorkspaceOption = None,
    as_json: JsonOption = False,
) -> None:
    """Initialize a new video project."""
    resolved_workspace = _resolve_workspace(workspace)
    channel_config = _load_channel(resolved_workspace, channel)

    minimum, maximum = FORMAT_DURATION_BOUNDS[content_format]
    target_duration = duration if duration is not None else (minimum + maximum) // 2

    identifier = project_id or generate_project_id(title)
    bundle = ProjectStore(resolved_workspace).create(
        title=title,
        project_id=identifier,
        channel_id=channel_config.channel_id,
        content_pillar=pillar,
        primary_subject=subject,
        content_format=content_format,
        target_duration_seconds=target_duration,
        slug=slugify(title),
        language=channel_config.brand.language,
    )

    if as_json:
        _echo_json(
            {
                "project_id": bundle.project_id,
                "title": bundle.manifest.title,
                "slug": bundle.manifest.slug,
                "path": str(bundle.root),
                "production_stage": bundle.manifest.production_stage.value,
                "target_duration_seconds": bundle.manifest.target_duration_seconds,
            }
        )
    else:
        typer.secho(f"Created project {bundle.project_id}", fg=typer.colors.GREEN, bold=True)
        typer.echo(f"  Title:    {bundle.manifest.title}")
        typer.echo(f"  Location: {bundle.root}")
        typer.echo(f"  Stage:    {bundle.manifest.production_stage.value}")
        typer.echo("")
        typer.echo("This is an empty draft. It is structurally valid but not production-ready.")
        typer.echo(f"Next: edit {bundle.root / bundle.manifest.paths.brief}")


@app.command(name="list")
def list_projects(workspace: WorkspaceOption = None, as_json: JsonOption = False) -> None:
    """List the projects in the workspace."""
    resolved_workspace = _resolve_workspace(workspace)
    store = ProjectStore(resolved_workspace)
    project_ids = store.list_project_ids()

    rows: list[dict[str, Any]] = []
    for identifier in project_ids:
        try:
            bundle = store.load(identifier)
        except PolymationError as exc:
            rows.append({"project_id": identifier, "error": str(exc)})
            continue
        rows.append(
            {
                "project_id": identifier,
                "title": bundle.manifest.title,
                "format": bundle.manifest.content_format.value,
                "stage": bundle.manifest.production_stage.value,
                "assets": len(bundle.assets.assets),
                "updated_at": bundle.manifest.updated_at.isoformat(),
            }
        )

    if as_json:
        _echo_json({"workspace_root": str(resolved_workspace.root), "projects": rows})
        return

    if not rows:
        typer.echo(f"No projects in {resolved_workspace.projects_dir}.")
        typer.echo('Create one with: polymation new "Your video title"')
        return

    typer.secho(f"{len(rows)} project(s) in {resolved_workspace.projects_dir}", bold=True)
    for row in rows:
        if "error" in row:
            typer.secho(f"  {row['project_id']}  [unreadable] {row['error']}", fg=typer.colors.RED)
            continue
        typer.echo(
            f"  {row['project_id']}  {row['stage']:<11} {row['assets']:>3} asset(s)  {row['title']}"
        )


@app.command()
def status(
    project_id: Annotated[str, typer.Argument(help="Project identifier.")],
    workspace: WorkspaceOption = None,
    as_json: JsonOption = False,
    verify_checksums: Annotated[
        bool,
        typer.Option(
            "--verify-checksums/--no-verify-checksums",
            help="Re-hash every asset file to detect modification after import.",
        ),
    ] = True,
) -> None:
    """Show project metadata, stage, asset counts and unresolved issues."""
    resolved_workspace = _resolve_workspace(workspace)
    bundle = _load_bundle(resolved_workspace, project_id)
    report = validate_project(bundle, verify_checksums=verify_checksums)
    readiness = assess_render_readiness(bundle, report)
    rights = build_rights_report(bundle)

    payload = {
        "project_id": bundle.project_id,
        "title": bundle.manifest.title,
        "slug": bundle.manifest.slug,
        "channel_id": bundle.manifest.channel_id,
        "content_pillar": bundle.manifest.content_pillar.value,
        "primary_subject": bundle.manifest.primary_subject.value,
        "content_format": bundle.manifest.content_format.value,
        "target_duration_seconds": bundle.manifest.target_duration_seconds,
        "production_stage": bundle.manifest.production_stage.value,
        "created_at": bundle.manifest.created_at.isoformat(),
        "updated_at": bundle.manifest.updated_at.isoformat(),
        "counts": {
            "assets": len(bundle.assets.assets),
            "assets_by_type": bundle.assets.counts_by_type(),
            "narration_scenes": len(bundle.script.scenes),
            "approved_narration_scenes": len(bundle.script.approved_scenes),
            "storyboard_scenes": len(bundle.storyboard.scenes),
            "storyboard_shots": bundle.storyboard.shot_count,
            "approvals": len(bundle.approvals.approvals),
        },
        "storyboard_duration_seconds": bundle.storyboard.duration_seconds,
        "rights_counts": bundle.rights.counts_by_status(),
        "structurally_valid": report.structurally_valid,
        "render_ready": readiness.render_ready,
        "publication_ready": None,
        "errors": [finding.to_dict() for finding in report.errors],
        "warnings": [finding.to_dict() for finding in report.warnings],
    }

    if as_json:
        _echo_json(payload)
        return

    manifest = bundle.manifest
    typer.secho(f"{manifest.title}", bold=True)
    typer.echo(f"  Project ID:  {manifest.project_id}")
    typer.echo(f"  Channel:     {manifest.channel_id}")
    typer.echo(f"  Pillar:      {manifest.content_pillar.value}")
    typer.echo(f"  Subject:     {manifest.primary_subject.value}")
    typer.echo(
        f"  Format:      {manifest.content_format.value} "
        f"(target {manifest.target_duration_seconds}s)"
    )
    typer.echo(f"  Stage:       {manifest.production_stage.value}")
    typer.echo(f"  Updated:     {manifest.updated_at.isoformat()}")
    typer.echo("")
    counts = bundle.assets.counts_by_type()
    typer.echo(
        f"  Assets:      {len(bundle.assets.assets)} "
        f"({', '.join(f'{name}={value}' for name, value in counts.items())})"
    )
    typer.echo(
        f"  Narration:   {len(bundle.script.scenes)} scene(s), "
        f"{len(bundle.script.approved_scenes)} approved"
    )
    typer.echo(
        f"  Storyboard:  {len(bundle.storyboard.scenes)} scene(s), "
        f"{bundle.storyboard.shot_count} shot(s), "
        f"{bundle.storyboard.duration_seconds:.1f}s"
    )
    typer.echo(
        "  Rights:      "
        + ", ".join(f"{name}={value}" for name, value in bundle.rights.counts_by_status().items())
    )
    typer.echo("")
    _echo_readiness(report.structurally_valid, readiness.render_ready)

    if report.errors:
        typer.echo("")
        typer.secho(f"{len(report.errors)} error(s):", fg=typer.colors.RED, bold=True)
        for finding in report.errors:
            typer.secho(
                f"  [{finding.code}] {finding.message}"
                + (f" ({finding.location})" if finding.location else ""),
                fg=typer.colors.RED,
            )

    if report.warnings:
        typer.echo("")
        typer.secho(f"{len(report.warnings)} warning(s):", fg=typer.colors.YELLOW, bold=True)
        for finding in report.warnings:
            typer.secho(
                f"  [{finding.code}] {finding.message}"
                + (f" ({finding.location})" if finding.location else ""),
                fg=typer.colors.YELLOW,
            )

    if rights.blocks_publication:
        typer.echo("")
        typer.secho(
            "Rights are not fully cleared. Publication readiness is not evaluated by "
            "M0.1 and publishing is out of scope.",
            fg=typer.colors.YELLOW,
        )


def _echo_readiness(structurally_valid: bool, render_ready: bool) -> None:
    typer.secho(
        f"  Structurally valid: {'yes' if structurally_valid else 'no'}",
        fg=typer.colors.GREEN if structurally_valid else typer.colors.RED,
    )
    typer.secho(
        f"  Render ready:       {'yes' if render_ready else 'no'}",
        fg=typer.colors.GREEN if render_ready else typer.colors.YELLOW,
    )
    typer.echo("  Publication ready:  not evaluated (out of scope for M0.1)")


@app.command()
def validate(
    project_id: Annotated[str, typer.Argument(help="Project identifier.")],
    workspace: WorkspaceOption = None,
    as_json: JsonOption = False,
    verify_checksums: Annotated[
        bool,
        typer.Option(
            "--verify-checksums/--no-verify-checksums",
            help="Re-hash every asset file to detect modification after import.",
        ),
    ] = True,
    strict: Annotated[bool, typer.Option("--strict", help="Treat warnings as failures.")] = False,
) -> None:
    """Validate a project's structure, references, media and rights."""
    resolved_workspace = _resolve_workspace(workspace)
    bundle = _load_bundle(resolved_workspace, project_id)
    report = validate_project(bundle, verify_checksums=verify_checksums)
    readiness = assess_render_readiness(bundle, report)

    if as_json:
        payload = report.to_dict()
        payload["render_readiness"] = readiness.to_dict()
        payload["strict"] = strict
        _echo_json(payload)
    else:
        typer.secho(f"Validating {bundle.project_id}", bold=True)
        if not report.findings:
            typer.secho("  No structural problems found.", fg=typer.colors.GREEN)
        for finding in report.errors:
            typer.secho(
                f"  ERROR   [{finding.code}] {finding.message}"
                + (f" ({finding.location})" if finding.location else ""),
                fg=typer.colors.RED,
            )
        for finding in report.warnings:
            typer.secho(
                f"  WARNING [{finding.code}] {finding.message}"
                + (f" ({finding.location})" if finding.location else ""),
                fg=typer.colors.YELLOW,
            )
        typer.echo("")
        _echo_readiness(report.structurally_valid, readiness.render_ready)
        if readiness.blockers and report.structurally_valid:
            typer.echo("")
            typer.secho("Render blockers:", fg=typer.colors.YELLOW, bold=True)
            for finding in readiness.blockers:
                typer.secho(f"  [{finding.code}] {finding.message}", fg=typer.colors.YELLOW)

    failed = bool(report.errors) or (strict and bool(report.warnings))
    raise typer.Exit(int(ExitCode.VALIDATION_FAILED if failed else ExitCode.OK))


@asset_app.command("import")
def asset_import(
    project_id: Annotated[str, typer.Argument(help="Project identifier.")],
    file: Annotated[Path, typer.Argument(help="Local media file to import.")],
    category: Annotated[
        AssetCategory | None,
        typer.Option(
            "--category",
            help="Destination category. Defaults to the detected media type's "
            "directory (images, footage or music).",
            show_default=False,
        ),
    ] = None,
    destination_name: Annotated[
        str | None,
        typer.Option(
            "--as",
            help="Destination filename. Must be a bare filename, not a path.",
            show_default=False,
        ),
    ] = None,
    source_note: Annotated[
        str | None,
        typer.Option("--source", help="Where the file came from.", show_default=False),
    ] = None,
    basis: Annotated[
        RightsBasis,
        typer.Option(
            "--basis",
            help="Documented basis for use. A basis is a provenance claim, never "
            "permission: the record is created unreviewed regardless.",
        ),
    ] = RightsBasis.USER_SUPPLIED,
    rights_holder: Annotated[
        str | None,
        typer.Option("--rights-holder", help="Creator or rights holder.", show_default=False),
    ] = None,
    license_identifier: Annotated[
        str | None,
        typer.Option("--license", help="Licence identifier, if licensed.", show_default=False),
    ] = None,
    intended_use: Annotated[
        str | None,
        typer.Option("--intended-use", help="Intended editorial use.", show_default=False),
    ] = None,
    ai_generated: Annotated[
        bool, typer.Option("--ai-generated", help="Mark the asset as AI-generated.")
    ] = False,
    allow_duplicate: Annotated[
        bool,
        typer.Option("--allow-duplicate", help="Import even if identical content exists."),
    ] = False,
    workspace: WorkspaceOption = None,
    as_json: JsonOption = False,
) -> None:
    """Import a local media file, recording metadata and an unreviewed rights record."""
    resolved_workspace = _resolve_workspace(workspace)
    bundle = _load_bundle(resolved_workspace, project_id)

    result = import_asset(
        bundle,
        file,
        category=category,
        destination_name=destination_name,
        original_source=source_note,
        rights_basis=basis,
        rights_holder=rights_holder,
        license_identifier=license_identifier,
        intended_editorial_use=intended_use,
        ai_generated=ai_generated,
        allow_duplicate=allow_duplicate,
    )
    bundle.save()

    if as_json:
        _echo_json(
            {
                "asset_id": result.asset.asset_id,
                "rights_record_id": result.rights.rights_record_id,
                "path": result.asset.path,
                "asset_type": result.asset.asset_type.value,
                "category": result.asset.category.value,
                "detected_format": result.asset.detected_format,
                "sha256": result.asset.sha256,
                "size_bytes": result.asset.size_bytes,
                "width": result.asset.width,
                "height": result.asset.height,
                "duration_seconds": result.asset.duration_seconds,
                "review_status": result.rights.review_status.value,
                "renamed_from": result.renamed_from,
            }
        )
        return

    typer.secho(f"Imported {result.asset.asset_id}", fg=typer.colors.GREEN, bold=True)
    typer.echo(f"  Path:     {result.asset.path}")
    typer.echo(f"  Detected: {result.asset.asset_type.value} / {result.asset.detected_format}")
    if result.asset.width and result.asset.height:
        typer.echo(f"  Size:     {result.asset.width}x{result.asset.height}")
    if result.asset.duration_seconds:
        typer.echo(f"  Duration: {result.asset.duration_seconds:.3f}s")
    typer.echo(f"  Bytes:    {result.asset.size_bytes}")
    typer.echo(f"  sha256:   {result.asset.sha256}")
    if result.was_renamed:
        typer.secho(
            f"  Renamed from {result.renamed_from!r} to avoid overwriting an existing file.",
            fg=typer.colors.YELLOW,
        )
    typer.echo("")
    typer.secho(
        f"  Rights record {result.rights.rights_record_id} created with status "
        f"'{result.rights.review_status.value}'. A human must review it before use.",
        fg=typer.colors.YELLOW,
    )


@asset_app.command("list")
def asset_list(
    project_id: Annotated[str, typer.Argument(help="Project identifier.")],
    workspace: WorkspaceOption = None,
    as_json: JsonOption = False,
) -> None:
    """List a project's imported assets."""
    resolved_workspace = _resolve_workspace(workspace)
    bundle = _load_bundle(resolved_workspace, project_id)

    rows = [
        {
            "asset_id": asset.asset_id,
            "asset_type": asset.asset_type.value,
            "category": asset.category.value,
            "path": asset.path,
            "detected_format": asset.detected_format,
            "size_bytes": asset.size_bytes,
            "width": asset.width,
            "height": asset.height,
            "duration_seconds": asset.duration_seconds,
            "sha256": asset.sha256,
            "rights_record_id": asset.rights_record_id,
            "review_status": (
                record.review_status.value
                if (record := bundle.rights.for_asset(asset.asset_id))
                else "missing_record"
            ),
            "file_present": bundle.asset_path(asset.path).is_file(),
        }
        for asset in bundle.assets.assets
    ]

    if as_json:
        _echo_json({"project_id": bundle.project_id, "assets": rows})
        return

    if not rows:
        typer.echo(f"No assets imported into {bundle.project_id}.")
        typer.echo(f"Import one with: polymation asset import {bundle.project_id} FILE")
        return

    typer.secho(f"{len(rows)} asset(s) in {bundle.project_id}", bold=True)
    for row in rows:
        dimensions = (
            f"{row['width']}x{row['height']}"
            if row["width"] and row["height"]
            else (f"{row['duration_seconds']:.1f}s" if row["duration_seconds"] else "-")
        )
        marker = "" if row["file_present"] else "  [FILE MISSING]"
        typer.echo(
            f"  {row['asset_id']}  {row['asset_type']:<5} {dimensions:<12} "
            f"{row['review_status']:<12} {row['path']}{marker}"
        )


@rights_app.command("report")
def rights_report(
    project_id: Annotated[str, typer.Argument(help="Project identifier.")],
    workspace: WorkspaceOption = None,
    as_json: JsonOption = False,
) -> None:
    """Report approved, restricted, rejected and unresolved asset rights."""
    resolved_workspace = _resolve_workspace(workspace)
    bundle = _load_bundle(resolved_workspace, project_id)
    report = build_rights_report(bundle)

    if as_json:
        _echo_json(report.to_dict())
        return

    typer.secho(f"Rights report — {bundle.project_id}", bold=True)
    typer.echo(f"  {report.total_assets} asset(s) covered")
    typer.echo("")

    groups = (
        ("Approved", report.approved, typer.colors.GREEN),
        ("Restricted", report.restricted, typer.colors.YELLOW),
        ("Rejected", report.rejected, typer.colors.RED),
        ("Unresolved", report.unresolved, typer.colors.YELLOW),
    )
    for label, entries, colour in groups:
        typer.secho(f"  {label}: {len(entries)}", fg=colour, bold=True)
        for entry in entries:
            attribution = (
                " [attribution required]"
                if (entry.record and entry.record.attribution_required)
                else ""
            )
            ai_marker = " [AI-generated]" if (entry.record and entry.record.ai_generated) else ""
            typer.echo(
                f"    {entry.asset.asset_id}  basis={entry.basis:<18} "
                f"{entry.asset.path}{attribution}{ai_marker}"
            )

    if report.orphan_records:
        typer.echo("")
        typer.secho(
            f"  {len(report.orphan_records)} rights record(s) cover no existing asset:",
            fg=typer.colors.RED,
        )
        for record in report.orphan_records:
            typer.echo(f"    {record.rights_record_id} -> {record.asset_id}")

    typer.echo("")
    if report.blocks_publication:
        typer.secho(
            "Not all material is cleared. M0.1 does not determine publication "
            "readiness, and publishing is out of scope.",
            fg=typer.colors.YELLOW,
        )
    else:
        typer.secho(
            "All assets carry an approved rights record. Publication remains a "
            "separate human decision.",
            fg=typer.colors.GREEN,
        )


@demo_app.command("create")
def demo_create(
    recreate: Annotated[
        bool,
        typer.Option("--recreate", help="Delete and rebuild the existing demo project."),
    ] = False,
    channel: Annotated[str, typer.Option("--channel", help="Channel identifier.")] = (
        DEFAULT_CHANNEL_ID
    ),
    workspace: WorkspaceOption = None,
    as_json: JsonOption = False,
) -> None:
    """Generate original synthetic fixtures and build the demonstration project."""
    resolved_workspace = _resolve_workspace(workspace)
    channel_config = _load_channel(resolved_workspace, channel)
    result = create_demo_project(resolved_workspace, channel_config, recreate=recreate)

    font_lines = [resolution.description for resolution in result.font_resolutions]

    if as_json:
        _echo_json(
            {
                "project_id": result.bundle.project_id,
                "path": str(result.bundle.root),
                "recreated": result.recreated,
                "fixtures": [str(path) for path in result.fixture_paths],
                "fonts": font_lines,
                "used_fallback_fonts": result.used_fallback_fonts,
                "scene_count": len(result.bundle.storyboard.scenes),
                "shot_count": result.bundle.storyboard.shot_count,
                "duration_seconds": result.bundle.storyboard.duration_seconds,
                "brand_provisional": channel_config.is_provisional,
            }
        )
        return

    typer.secho(
        f"Created demonstration project {result.bundle.project_id}",
        fg=typer.colors.GREEN,
        bold=True,
    )
    typer.echo(f"  Location: {result.bundle.root}")
    typer.echo(
        f"  Timeline: {len(result.bundle.storyboard.scenes)} scene(s), "
        f"{result.bundle.storyboard.shot_count} shot(s), "
        f"{result.bundle.storyboard.duration_seconds:.1f}s"
    )
    typer.echo("")
    typer.echo("  Original fixtures generated:")
    for path in result.fixture_paths:
        typer.echo(f"    {path}")
    typer.echo("")
    typer.echo("  Fonts:")
    for line in font_lines:
        typer.echo(f"    {line}")

    if result.used_fallback_fonts:
        typer.echo("")
        typer.secho(
            "  The proposed brand fonts are not installed. Text was drawn with a "
            "substitute, so this render does NOT represent brand typography.",
            fg=typer.colors.YELLOW,
        )
    if channel_config.is_provisional:
        typer.secho(
            "  Brand values are provisional, not approved production artwork.",
            fg=typer.colors.YELLOW,
        )

    typer.echo("")
    typer.echo("  Next: polymation demo render")


@demo_app.command("render")
def demo_render(
    project_id: Annotated[
        str, typer.Option("--project-id", help="Demonstration project identifier.")
    ] = DEMO_PROJECT_ID,
    output: Annotated[
        Path | None,
        typer.Option("--output", help="Output file path.", show_default=False),
    ] = None,
    overwrite: Annotated[
        bool, typer.Option("--overwrite", help="Replace an existing output file.")
    ] = False,
    channel: Annotated[str, typer.Option("--channel", help="Channel identifier.")] = (
        DEFAULT_CHANNEL_ID
    ),
    workspace: WorkspaceOption = None,
    as_json: JsonOption = False,
) -> None:
    """Render the demonstration with FFmpeg and verify the output with ffprobe."""
    resolved_workspace = _resolve_workspace(workspace)
    channel_config = _load_channel(resolved_workspace, channel)
    bundle = _load_bundle(resolved_workspace, project_id)

    result = render_project(
        bundle,
        channel_config.brand,
        output_path=output,
        overwrite=overwrite,
    )
    inspection = inspect_output(
        result.output_path,
        expected_duration_seconds=(
            DEMO_TOTAL_DURATION_SECONDS
            if project_id == DEMO_PROJECT_ID
            else bundle.storyboard.duration_seconds
        ),
    )

    if as_json:
        payload = result.to_dict()
        payload["inspection"] = inspection.to_dict()
        payload["ffmpeg_argv"] = list(result.ffmpeg_argv)
        _echo_json(payload)
    else:
        typer.secho(f"Rendered {result.output_path}", fg=typer.colors.GREEN, bold=True)
        typer.echo(f"  FFmpeg:       {result.ffmpeg_version}")
        typer.echo(f"  Render time:  {result.render_seconds:.2f}s")
        typer.echo(f"  File size:    {result.file_size_bytes} bytes")
        typer.echo("")
        typer.echo("  ffprobe verification:")
        typer.echo(f"    Resolution:   {inspection.width}x{inspection.height}")
        typer.echo(f"    Frame rate:   {inspection.frame_rate}")
        typer.echo(f"    Duration:     {inspection.duration_seconds}s")
        typer.echo(f"    Video codec:  {inspection.video_codec}")
        typer.echo(
            f"    Audio:        {inspection.audio_codec} @ "
            f"{inspection.audio_sample_rate} Hz, {inspection.audio_channels} ch"
        )
        typer.echo("")
        if inspection.conforms:
            typer.secho("  Output conforms to the M0.1 specification.", fg=typer.colors.GREEN)
        else:
            typer.secho("  Output deviates from the specification:", fg=typer.colors.RED)
            for deviation in inspection.deviations:
                typer.secho(f"    - {deviation}", fg=typer.colors.RED)

        for finding in result.readiness.cautions:
            typer.secho(f"  CAUTION [{finding.code}] {finding.message}", fg=typer.colors.YELLOW)

        typer.echo("")
        typer.echo(
            "  This is a technical fixture. It is not a channel intro, not a creative "
            "pilot, and not cleared for publication."
        )

    raise typer.Exit(int(ExitCode.OK if inspection.conforms else ExitCode.VALIDATION_FAILED))


@app.command()
def fonts(
    channel: Annotated[str, typer.Option("--channel", help="Channel identifier.")] = (
        DEFAULT_CHANNEL_ID
    ),
    workspace: WorkspaceOption = None,
    as_json: JsonOption = False,
) -> None:
    """Report whether the proposed brand fonts are installed locally."""
    resolved_workspace = _resolve_workspace(workspace)
    channel_config = _load_channel(resolved_workspace, channel)
    resolutions = brand_font_report(channel_config.brand)

    if as_json:
        _echo_json(
            {
                "channel_id": channel_config.channel_id,
                "brand_provisional": channel_config.is_provisional,
                "fonts": [
                    {
                        "family": resolution.requested_family,
                        "path": str(resolution.path) if resolution.path else None,
                        "available": not resolution.is_fallback,
                    }
                    for resolution in resolutions
                ],
            }
        )
        return

    typer.secho(f"Brand fonts — {channel_config.channel_id}", bold=True)
    for resolution in resolutions:
        colour = typer.colors.YELLOW if resolution.is_fallback else typer.colors.GREEN
        typer.secho(f"  {resolution.description}", fg=colour)

    if any(resolution.is_fallback for resolution in resolutions):
        typer.echo("")
        typer.secho(
            "Missing fonts are substituted at render time. Any render using a "
            "substitute does not represent brand typography.",
            fg=typer.colors.YELLOW,
        )


def main() -> None:
    """Console-script entry point. Maps library errors to messages and exit codes.

    ``standalone_mode=False`` stops Click from calling :func:`sys.exit` itself,
    which is what lets library exceptions reach the handlers below. In that mode
    Click *returns* the code from a ``typer.Exit`` instead of propagating it, so
    the return value is honoured here as well as the exception.
    """
    try:
        result = app(standalone_mode=False)
    except PolymationError as exc:
        _fail(f"error: {exc}", exc.exit_code)
    except typer.Exit as exc:  # pragma: no cover - Click normally returns the code
        raise SystemExit(exc.exit_code) from exc
    except click_exceptions() as exc:
        exc.show()
        raise SystemExit(exc.exit_code) from exc
    except KeyboardInterrupt:  # pragma: no cover - interactive only
        typer.secho("interrupted", fg=typer.colors.YELLOW, err=True)
        raise SystemExit(130) from None

    raise SystemExit(result if isinstance(result, int) else int(ExitCode.OK))


def click_exceptions() -> tuple[type[BaseException], ...]:
    """Click's user-facing exception types, resolved lazily.

    Typer re-exports Click, but the module path has moved between versions;
    resolving at call time keeps the CLI working across the supported range.
    """
    from click import exceptions

    return (exceptions.ClickException,)


if __name__ == "__main__":  # pragma: no cover
    main()
    sys.exit(0)
