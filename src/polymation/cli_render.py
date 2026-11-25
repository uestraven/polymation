"""M0.2 CLI commands: timeline, render, cache and inspect.

Kept in its own module so :mod:`polymation.cli` stays navigable; the Typer
sub-apps defined here are registered by that module. Conventions follow M0.1
exactly: resolve the workspace, delegate to a library function, format the
result, and support ``--json`` everywhere.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer

from polymation.config.channel import DEFAULT_CHANNEL_ID, load_channel_config
from polymation.config.paths import Workspace
from polymation.errors import ExitCode, PolymationError, RenderError
from polymation.projects.locking import read_lock
from polymation.projects.store import ProjectStore
from polymation.rendering.cache import (
    AssemblyCache,
    SceneCache,
    clear_project_cache,
    compute_scene_key,
)
from polymation.rendering.compose import inspect_output
from polymation.rendering.engine import preflight, render
from polymation.rendering.ffmpeg import find_ffmpeg
from polymation.rendering.profiles import PROFILES, ProfileName, get_profile
from polymation.rendering.reference import (
    REFERENCE_PROJECT_ID,
    build_reference_fixture,
    marker_times,
)
from polymation.timeline.compile import compile_timeline

timeline_app = typer.Typer(
    help="Compile and validate the composition timeline.", no_args_is_help=True
)
cache_app = typer.Typer(help="Inspect and clear render caches.", no_args_is_help=True)
reference_app = typer.Typer(
    help="Build and render the engineering reference fixture.", no_args_is_help=True
)

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


def _echo_findings(report: Any) -> None:
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


# --- timeline ------------------------------------------------------------------


@timeline_app.command("compile")
def timeline_compile(
    project_id: Annotated[str, typer.Argument(help="Project identifier.")],
    profile_name: Annotated[
        ProfileName, typer.Option("--profile", help="Profile whose geometry to adopt.")
    ] = ProfileName.FINAL,
    narration: Annotated[
        list[str] | None,
        typer.Option(
            "--narration",
            help="Audio asset id to place on the narration bus, one per scene, "
            "repeatable and in scene order.",
            show_default=False,
        ),
    ] = None,
    music: Annotated[
        str | None,
        typer.Option("--music", help="Audio asset id for the music bed.", show_default=False),
    ] = None,
    overwrite: Annotated[
        bool,
        typer.Option(
            "--overwrite",
            help="Replace an existing timeline. Compilation discards hand edits, so "
            "this is required when a timeline already exists.",
        ),
    ] = False,
    workspace: WorkspaceOption = None,
    as_json: JsonOption = False,
) -> None:
    """Compile the storyboard into a composition timeline."""
    resolved = _resolve_workspace(workspace)
    store = ProjectStore(resolved)
    bundle = store.load(project_id)
    profile = get_profile(profile_name)

    if bundle.timeline is not None and not overwrite:
        raise RenderError(
            f"Project {project_id} already has a timeline. Compilation projects the "
            f"storyboard afresh and discards hand edits to timeline.json, so pass "
            f"--overwrite to confirm."
        )

    result = compile_timeline(bundle, profile, narration_asset_ids=narration, music_asset_id=music)
    bundle.timeline = result.timeline
    bundle.save(operation="timeline compile")

    if as_json:
        _echo_json(result.to_dict())
        return

    timeline = result.timeline
    typer.secho(f"Compiled timeline for {project_id}", fg=typer.colors.GREEN, bold=True)
    typer.echo(f"  Profile:   {timeline.profile_name} ({timeline.width}x{timeline.height})")
    typer.echo(
        f"  Timeline:  {len(timeline.scenes)} scene(s), {timeline.shot_count} shot(s), "
        f"{timeline.total_frames} frames, {timeline.duration_seconds:.3f}s"
    )
    typer.echo(f"  Audio:     {len(timeline.audio.segments)} segment(s)")

    if result.rounding:
        typer.echo("")
        typer.secho(
            f"  {len(result.rounding)} duration(s) were snapped to the frame grid:",
            fg=typer.colors.YELLOW,
        )
        for item in result.rounding:
            typer.echo(
                f"    {item.quantity}: {item.requested:.4f} -> {item.resolved} "
                f"({item.rounding.value}, error {item.error:.4f})"
            )

    for note in result.notes:
        typer.secho(f"  note: {note}", fg=typer.colors.YELLOW)


@timeline_app.command("validate")
def timeline_validate(
    project_id: Annotated[str, typer.Argument(help="Project identifier.")],
    profile_name: Annotated[
        ProfileName, typer.Option("--profile", help="Profile to validate against.")
    ] = ProfileName.FINAL,
    verify_checksums: Annotated[
        bool,
        typer.Option(
            "--verify-checksums/--no-verify-checksums",
            help="Re-hash referenced assets to detect modification after import.",
        ),
    ] = True,
    strict: Annotated[bool, typer.Option("--strict", help="Treat warnings as failures.")] = False,
    workspace: WorkspaceOption = None,
    as_json: JsonOption = False,
) -> None:
    """Validate a project's timeline against its assets and a profile."""
    resolved = _resolve_workspace(workspace)
    bundle = ProjectStore(resolved).load(project_id)
    profile = get_profile(profile_name)
    report = preflight(bundle, profile, verify_checksums=verify_checksums)

    if as_json:
        payload = report.to_dict()
        payload["profile"] = profile.name.value
        payload["strict"] = strict
        payload["renderable"] = report.structurally_valid
        _echo_json(payload)
    else:
        typer.secho(
            f"Validating timeline for {project_id} against profile {profile.name.value!r}",
            bold=True,
        )
        if not report.findings:
            typer.secho("  No problems found.", fg=typer.colors.GREEN)
        _echo_findings(report)
        typer.echo("")
        typer.secho(
            f"  Renderable: {'yes' if report.structurally_valid else 'no'}",
            fg=typer.colors.GREEN if report.structurally_valid else typer.colors.RED,
        )
        typer.echo("  Publication ready: not evaluated (out of scope)")

    failed = bool(report.errors) or (strict and bool(report.warnings))
    raise typer.Exit(int(ExitCode.VALIDATION_FAILED if failed else ExitCode.OK))


# --- render --------------------------------------------------------------------


def render_command(
    project_id: Annotated[str, typer.Argument(help="Project identifier.")],
    preview: Annotated[
        bool, typer.Option("--preview", help="Render the 960x540 review profile.")
    ] = False,
    final: Annotated[
        bool, typer.Option("--final", help="Render the 1920x1080 publication profile.")
    ] = False,
    scene: Annotated[
        list[str] | None,
        typer.Option(
            "--scene",
            help="Render only this scene, repeatable. Final assembly is skipped, so "
            "the assembled output on disk stays stale.",
            show_default=False,
        ),
    ] = None,
    force: Annotated[
        bool, typer.Option("--force", help="Ignore cache hits and re-render.")
    ] = False,
    overwrite: Annotated[
        bool, typer.Option("--overwrite", help="Replace an existing output file.")
    ] = False,
    output: Annotated[
        Path | None,
        typer.Option("--output", help="Output file path.", show_default=False),
    ] = None,
    channel: Annotated[str, typer.Option("--channel", help="Channel identifier.")] = (
        DEFAULT_CHANNEL_ID
    ),
    verify_checksums: Annotated[
        bool,
        typer.Option(
            "--verify-checksums/--no-verify-checksums",
            help="Re-hash referenced assets during preflight.",
        ),
    ] = True,
    workspace: WorkspaceOption = None,
    as_json: JsonOption = False,
) -> None:
    """Render a project's timeline."""
    if preview and final:
        raise RenderError(
            "Pass either --preview or --final, not both. The two profiles have "
            "separate caches and separate outputs; render them one at a time."
        )
    profile = get_profile(ProfileName.PREVIEW if preview else ProfileName.FINAL)

    resolved = _resolve_workspace(workspace)
    channel_config = load_channel_config(resolved, channel)
    bundle = ProjectStore(resolved).load(project_id)

    outcome = render(
        bundle,
        channel_config,
        profile,
        scene_ids=scene,
        force=force,
        overwrite=overwrite,
        output_path=output,
        verify_checksums=verify_checksums,
    )

    if as_json:
        _echo_json(outcome.to_dict())
    else:
        _echo_render_outcome(outcome)

    conforms = outcome.inspection is None or outcome.inspection.conforms
    raise typer.Exit(int(ExitCode.OK if conforms else ExitCode.VALIDATION_FAILED))


def _echo_render_outcome(outcome: Any) -> None:
    """Human-readable render report."""
    profile = outcome.profile

    if outcome.scene_only:
        typer.secho(
            f"Rendered {len(outcome.scenes)} scene(s) for profile {profile.name.value!r}",
            fg=typer.colors.GREEN,
            bold=True,
        )
        typer.echo("")
        typer.secho(
            "  Final assembly was NOT updated: this was a scene-only render.",
            fg=typer.colors.YELLOW,
            bold=True,
        )
        if outcome.stale_output_path is not None:
            typer.secho(
                f"  The existing output at {outcome.stale_output_path} is now STALE "
                f"and does not reflect these scenes.",
                fg=typer.colors.YELLOW,
            )
        else:
            typer.echo("  No assembled output exists yet.")
    else:
        typer.secho(f"Rendered {outcome.output_path}", fg=typer.colors.GREEN, bold=True)
        typer.echo("  Final assembly: updated")

    typer.echo("")
    typer.echo(f"  Profile:      {profile.name.value} ({profile.width}x{profile.height})")
    typer.echo(f"  Total time:   {outcome.total_seconds:.2f}s")
    typer.echo(f"  Assembly:     {outcome.assembly_seconds:.2f}s")
    if outcome.assembly_cached:
        typer.echo("  Assembly:     served from cache")

    typer.echo("")
    typer.echo(
        f"  Scenes:       {outcome.cache.hit_count} cached, {len(outcome.cache.rebuilt)} rebuilt"
    )
    if outcome.cache.rebuilt:
        typer.echo(f"    rebuilt: {', '.join(outcome.cache.rebuilt)}")
    if outcome.cache.hits:
        typer.echo(f"    reused:  {', '.join(outcome.cache.hits)}")
    if outcome.cache.corrupt:
        typer.secho(
            f"    discarded as corrupt and rebuilt: {', '.join(outcome.cache.corrupt)}",
            fg=typer.colors.YELLOW,
        )
    typer.echo(f"  Cache size:   {outcome.cache_bytes} bytes")

    if outcome.inspection is not None:
        inspection = outcome.inspection
        typer.echo("")
        typer.echo("  ffprobe verification:")
        typer.echo(f"    Resolution:  {inspection.width}x{inspection.height}")
        typer.echo(f"    Frame rate:  {inspection.frame_rate}")
        typer.echo(f"    Duration:    {inspection.duration_seconds}s")
        typer.echo(f"    Video:       {inspection.video_codec} / {inspection.pixel_format}")
        typer.echo(
            f"    Audio:       {inspection.audio_codec} @ "
            f"{inspection.audio_sample_rate} Hz, {inspection.audio_channels} ch"
        )
        if inspection.conforms:
            typer.secho("    Conforms to the profile.", fg=typer.colors.GREEN)
        else:
            typer.secho("    Deviates from the profile:", fg=typer.colors.RED)
            for deviation in inspection.deviations:
                typer.secho(f"      - {deviation}", fg=typer.colors.RED)

    if outcome.loudness is not None:
        loudness = outcome.loudness
        typer.echo("")
        typer.echo("  Loudness (EBU R128, measured):")
        typer.echo(f"    Integrated:  {loudness.integrated_lufs} LUFS")
        typer.echo(f"    Range:       {loudness.loudness_range_lu} LU")
        typer.echo(f"    True peak:   {loudness.true_peak_dbtp} dBTP")

    if outcome.sidecar_srt_path is not None:
        typer.echo("")
        typer.echo(f"  Captions:     {outcome.sidecar_srt_path}")
    if outcome.stem_paths:
        typer.echo(f"  Stems:        {', '.join(sorted(outcome.stem_paths))}")

    if outcome.broke_stale_lock:
        typer.echo("")
        typer.secho(
            f"  Broke a stale project lock: {outcome.broke_stale_lock}",
            fg=typer.colors.YELLOW,
        )

    if outcome.used_fallback_fonts:
        typer.echo("")
        typer.secho(
            "  Overlay text used a substitute typeface, so this render does NOT "
            "represent brand typography.",
            fg=typer.colors.YELLOW,
        )

    if outcome.cautions:
        typer.echo("")
        typer.secho(f"  {len(outcome.cautions)} caution(s):", fg=typer.colors.YELLOW)
        for finding in outcome.cautions[:12]:
            typer.secho(f"    [{finding.code}] {finding.message}", fg=typer.colors.YELLOW)
        if len(outcome.cautions) > 12:
            typer.secho(
                f"    ... and {len(outcome.cautions) - 12} more (use --json for all)",
                fg=typer.colors.YELLOW,
            )

    typer.echo("")
    typer.echo(
        "  Publication readiness is not evaluated. A technically correct render is "
        "not an editorial or rights approval."
    )


# --- cache ---------------------------------------------------------------------


@cache_app.command("status")
def cache_status(
    project_id: Annotated[str, typer.Argument(help="Project identifier.")],
    workspace: WorkspaceOption = None,
    as_json: JsonOption = False,
) -> None:
    """Report cached scenes, their freshness and total size."""
    resolved = _resolve_workspace(workspace)
    bundle = ProjectStore(resolved).load(project_id)
    channel_config = load_channel_config(resolved, bundle.manifest.channel_id)
    ffmpeg_version = find_ffmpeg().version or "unknown"
    assets = {asset.asset_id: asset for asset in bundle.assets.assets}

    payload: dict[str, Any] = {
        "project_id": project_id,
        "has_timeline": bundle.timeline is not None,
        "ffmpeg_version": ffmpeg_version,
        "profiles": {},
        "lock": None,
    }

    lock = read_lock(bundle.root)
    if lock is not None:
        payload["lock"] = lock.to_dict()

    for name in PROFILES:
        profile = get_profile(name)
        scene_cache = SceneCache(bundle.root, profile)
        assembly_cache = AssemblyCache(bundle.root, profile)

        scenes: list[dict[str, Any]] = []
        if bundle.timeline is not None:
            for scene in bundle.timeline.scenes_in_order:
                try:
                    key = compute_scene_key(
                        scene,
                        bundle.timeline,
                        profile,
                        channel_config.brand,
                        assets=assets,
                        ffmpeg_version=ffmpeg_version,
                    )
                except PolymationError as exc:
                    scenes.append(
                        {"scene_id": scene.scene_id, "state": "unknown", "detail": str(exc)}
                    )
                    continue

                entry = scene_cache.lookup(key)
                scenes.append(
                    {
                        "scene_id": scene.scene_id,
                        "digest": key.short,
                        "state": "fresh" if entry else "stale",
                        "size_bytes": entry.size_bytes if entry else 0,
                        "frames": scene.duration_frames,
                    }
                )

        payload["profiles"][profile.name.value] = {
            "scene_cache_dir": str(scene_cache.root),
            "scene_entries": len(scene_cache.entries()),
            "scene_bytes": scene_cache.total_bytes(),
            "assembly_entries": len(assembly_cache.entries()),
            "assembly_bytes": assembly_cache.total_bytes(),
            "scenes": scenes,
        }

    if as_json:
        _echo_json(payload)
        return

    typer.secho(f"Cache status — {project_id}", bold=True)
    if lock is not None:
        typer.secho(
            f"  Project is locked by {lock.operation!r} (pid {lock.pid} on "
            f"{lock.hostname}, {lock.age_seconds:.0f}s ago)",
            fg=typer.colors.YELLOW,
        )
    if bundle.timeline is None:
        typer.secho(
            "  No compiled timeline, so scene freshness is unknown.",
            fg=typer.colors.YELLOW,
        )

    for name, data in payload["profiles"].items():
        typer.echo("")
        typer.secho(f"  {name}", bold=True)
        typer.echo(
            f"    scenes:   {data['scene_entries']} entr(y/ies), {data['scene_bytes']} bytes"
        )
        typer.echo(
            f"    assembly: {data['assembly_entries']} entr(y/ies), {data['assembly_bytes']} bytes"
        )
        for scene in data["scenes"]:
            state = scene.get("state")
            colour = (
                typer.colors.GREEN
                if state == "fresh"
                else typer.colors.YELLOW
                if state == "stale"
                else typer.colors.RED
            )
            typer.secho(
                f"      {scene['scene_id']:<12} {state:<7} "
                f"{scene.get('digest', '-'):<14} {scene.get('size_bytes', 0)} bytes",
                fg=colour,
            )


@cache_app.command("clear")
def cache_clear(
    project_id: Annotated[str, typer.Argument(help="Project identifier.")],
    profile_name: Annotated[
        ProfileName | None,
        typer.Option(
            "--profile",
            help="Clear only this profile's cache. Omit to clear both.",
            show_default=False,
        ),
    ] = None,
    workspace: WorkspaceOption = None,
    as_json: JsonOption = False,
) -> None:
    """Clear a project's render caches.

    Scoped to the named project's ``.cache`` directory. Imported source media
    and rendered outputs are never touched.
    """
    resolved = _resolve_workspace(workspace)
    bundle = ProjectStore(resolved).load(project_id)

    if profile_name is None:
        removed = clear_project_cache(bundle.root)
        scope = "all profiles"
    else:
        profile = get_profile(profile_name)
        removed = SceneCache(bundle.root, profile).clear()
        removed += AssemblyCache(bundle.root, profile).clear()
        scope = profile.name.value

    if as_json:
        _echo_json(
            {
                "project_id": project_id,
                "scope": scope,
                "files_removed": removed,
                "source_media_touched": False,
            }
        )
        return

    typer.secho(
        f"Cleared {removed} cache file(s) for {project_id} ({scope}).",
        fg=typer.colors.GREEN,
    )
    typer.echo("  Imported source media and rendered outputs were not touched.")


# --- inspect -------------------------------------------------------------------


def inspect_command(
    video_path: Annotated[Path, typer.Argument(help="Video file to inspect.")],
    profile_name: Annotated[
        ProfileName, typer.Option("--profile", help="Profile to check against.")
    ] = ProfileName.FINAL,
    expect_duration: Annotated[
        float | None,
        typer.Option(
            "--expect-duration",
            help="Expected duration in seconds, checked within --tolerance.",
            show_default=False,
        ),
    ] = None,
    tolerance: Annotated[
        float, typer.Option("--tolerance", help="Duration tolerance in seconds.")
    ] = 0.2,
    require_audio: Annotated[
        bool,
        typer.Option("--require-audio/--allow-silent", help="Check the audio stream."),
    ] = True,
    measure_loudness_too: Annotated[
        bool, typer.Option("--loudness/--no-loudness", help="Also measure EBU R128 loudness.")
    ] = True,
    as_json: JsonOption = False,
) -> None:
    """Inspect any video file's properties against a render profile."""
    profile = get_profile(profile_name)
    inspection = inspect_output(
        video_path,
        expected_duration_seconds=expect_duration,
        duration_tolerance_seconds=tolerance,
        require_audio=require_audio,
        profile=profile,
    )

    loudness = None
    if measure_loudness_too:
        from polymation.rendering.loudness import measure_loudness

        loudness = measure_loudness(video_path)

    if as_json:
        payload = inspection.to_dict()
        payload["path"] = str(video_path)
        payload["profile"] = profile.name.value
        payload["loudness"] = loudness.to_dict() if loudness else None
        _echo_json(payload)
    else:
        typer.secho(f"{video_path}", bold=True)
        typer.echo(f"  Checked against profile: {profile.name.value}")
        typer.echo(f"  Resolution:  {inspection.width}x{inspection.height}")
        typer.echo(f"  Frame rate:  {inspection.frame_rate}")
        typer.echo(f"  Duration:    {inspection.duration_seconds}s")
        typer.echo(f"  Video:       {inspection.video_codec} / {inspection.pixel_format}")
        typer.echo(
            f"  Audio:       {inspection.audio_codec} @ "
            f"{inspection.audio_sample_rate} Hz, {inspection.audio_channels} ch"
        )
        typer.echo(f"  File size:   {inspection.file_size_bytes} bytes")
        if loudness is not None:
            typer.echo(
                f"  Loudness:    {loudness.integrated_lufs} LUFS integrated, "
                f"{loudness.true_peak_dbtp} dBTP true peak"
            )
        typer.echo("")
        if inspection.conforms:
            typer.secho("  Conforms to the profile.", fg=typer.colors.GREEN)
        else:
            typer.secho("  Deviates from the profile:", fg=typer.colors.RED)
            for deviation in inspection.deviations:
                typer.secho(f"    - {deviation}", fg=typer.colors.RED)

    raise typer.Exit(int(ExitCode.OK if inspection.conforms else ExitCode.VALIDATION_FAILED))


# --- reference fixture ---------------------------------------------------------


@reference_app.command("build")
def reference_build(
    recreate: Annotated[
        bool, typer.Option("--recreate", help="Delete and rebuild the existing fixture.")
    ] = False,
    channel: Annotated[str, typer.Option("--channel", help="Channel identifier.")] = (
        DEFAULT_CHANNEL_ID
    ),
    workspace: WorkspaceOption = None,
    as_json: JsonOption = False,
) -> None:
    """Build the ~180-second engineering reference fixture from original assets."""
    resolved = _resolve_workspace(workspace)
    channel_config = load_channel_config(resolved, channel)
    build = build_reference_fixture(resolved, channel_config, recreate=recreate)

    if as_json:
        _echo_json(build.to_dict())
        return

    timeline = build.bundle.timeline
    assert timeline is not None  # noqa: S101 - build always writes a timeline
    typer.secho(
        f"Built reference fixture {build.bundle.project_id}",
        fg=typer.colors.GREEN,
        bold=True,
    )
    typer.echo(f"  Location:  {build.bundle.root}")
    typer.echo(
        f"  Timeline:  {len(timeline.scenes)} scene(s), {timeline.shot_count} shot(s), "
        f"{timeline.overlay_count} overlay(s)"
    )
    typer.echo(
        f"  Duration:  {timeline.total_frames} frames = "
        f"{timeline.duration_seconds:.3f}s at {timeline.fps} fps"
    )
    typer.echo(f"  Audio:     {len(timeline.audio.segments)} segment(s)")
    typer.echo(f"  Assets:    {len(build.generated_assets)} generated")
    typer.echo(
        f"  Markers:   {len(marker_times())} at "
        f"{', '.join(f'{value:.1f}s' for value in marker_times())}"
    )
    typer.echo("")
    typer.secho(
        "  This is an engineering benchmark, not an episode. Narration is a "
        "synthetic tone, not speech, and rights are unresolved.",
        fg=typer.colors.YELLOW,
    )
    typer.echo("")
    typer.echo(f"  Next: polymation render {REFERENCE_PROJECT_ID} --preview")
