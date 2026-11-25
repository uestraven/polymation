#!/usr/bin/env python3
"""Benchmark the composition engine on the ~180-second reference fixture.

Methodology, stated so the numbers mean something:

* A **fresh temporary workspace** per run, so no cache state leaks in from a
  previous run. This matters: an earlier hand-run of these measurements
  reported a warm render as slow because the assembly cache did not yet exist,
  and reported a "modified scene" rebuild as a cache hit because the previous
  run had already applied the same edit.
* The fixture is built once; build time is reported separately from render time
  because generating 15 original assets is not rendering.
* Cold measurements run with an empty cache. Warm measurements run immediately
  after, with every input unchanged.
* The single-scene-edit measurement applies a **unique** edit each run, so it
  cannot accidentally reproduce a cached state.
* Peak memory is the process maximum resident set size, which covers this
  process but **not** the FFmpeg child processes — FFmpeg does the heavy
  lifting, so treat it as a floor, not the true peak.
* Wall-clock times include FFmpeg process startup and are specific to the
  host's CPU and FFmpeg build, both of which are reported.

Usage:
    scripts/benchmark.py [--json out.json] [--keep]
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import resource
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Measurement:
    """One timed render."""

    label: str
    seconds: float
    scenes_rebuilt: list[str] = field(default_factory=list)
    scenes_reused: list[str] = field(default_factory=list)
    assembly_seconds: float = 0.0
    assembly_cached: bool = False
    output_bytes: int = 0
    cache_bytes: int = 0

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable form."""
        return {
            "label": self.label,
            "seconds": round(self.seconds, 3),
            "scenes_rebuilt": self.scenes_rebuilt,
            "scenes_reused": self.scenes_reused,
            "rebuilt_count": len(self.scenes_rebuilt),
            "reused_count": len(self.scenes_reused),
            "assembly_seconds": round(self.assembly_seconds, 3),
            "assembly_cached": self.assembly_cached,
            "output_bytes": self.output_bytes,
            "cache_bytes": self.cache_bytes,
        }


def tool_version(binary: str, *arguments: str) -> str:
    """First line of a tool's version output, or a marker when absent."""
    path = shutil.which(binary)
    if path is None:
        return "not installed"
    try:
        completed = subprocess.run(  # noqa: S603 - resolved binary, fixed argv
            [path, *arguments],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unavailable"
    return (completed.stdout or completed.stderr).splitlines()[0].strip()


def host_conditions() -> dict[str, Any]:
    """Hardware and software conditions the numbers are specific to."""
    try:
        cpu_count = os.cpu_count() or 0
    except NotImplementedError:  # pragma: no cover
        cpu_count = 0

    model = "unknown"
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.is_file():
        for line in cpuinfo.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.lower().startswith("model name"):
                model = line.split(":", 1)[1].strip()
                break

    total_memory_gib: float | None = None
    meminfo = Path("/proc/meminfo")
    if meminfo.is_file():
        for line in meminfo.read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                total_memory_gib = int(line.split()[1]) / 1024 / 1024
                break

    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "cpu_model": model,
        "cpu_count": cpu_count,
        "total_memory_gib": round(total_memory_gib, 1) if total_memory_gib else None,
        "ffmpeg": tool_version("ffmpeg", "-version"),
        "ffprobe": tool_version("ffprobe", "-version"),
    }


def peak_memory_mib() -> float:
    """Peak RSS of this process in MiB. Excludes FFmpeg children."""
    usage = resource.getrusage(resource.RUSAGE_SELF)
    # ru_maxrss is KiB on Linux, bytes on macOS.
    divisor = 1024 if sys.platform != "darwin" else 1024 * 1024
    return usage.ru_maxrss / divisor


def main(argv: list[str]) -> int:
    """Run the benchmark. Returns a process exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path, help="Write the results as JSON.")
    parser.add_argument(
        "--keep", action="store_true", help="Keep the temporary workspace for inspection."
    )
    arguments = parser.parse_args(argv)

    workspace_root = Path(tempfile.mkdtemp(prefix="polymation-benchmark-"))
    studio = workspace_root / "studio"
    (studio / "projects").mkdir(parents=True)
    (studio / "fixtures").mkdir()
    shutil.copytree(REPOSITORY_ROOT / "channels", studio / "channels")
    os.environ["POLYMATION_HOME"] = str(studio)

    # Imported after POLYMATION_HOME is set, so discovery cannot pick up the
    # repository's own workspace.
    from polymation.config import Workspace, load_channel_config
    from polymation.projects.store import ProjectStore
    from polymation.rendering.cache import AssemblyCache, SceneCache
    from polymation.rendering.engine import render
    from polymation.rendering.profiles import FINAL_PROFILE, PREVIEW_PROFILE
    from polymation.rendering.reference import (
        REFERENCE_PROJECT_ID,
        build_reference_fixture,
        marker_times,
    )
    from polymation.rendering.synth import audio_envelope, detect_marker

    conditions = host_conditions()
    print("Host conditions")
    for key, value in conditions.items():
        print(f"  {key:18} {value}")
    if conditions["ffmpeg"] == "not installed":
        print("\nerror: FFmpeg is required to benchmark rendering.", file=sys.stderr)
        return 1

    workspace = Workspace(root=studio)
    channel = load_channel_config(workspace, "polymation")
    store = ProjectStore(workspace)

    print("\nBuilding the reference fixture")
    started = time.perf_counter()
    build = build_reference_fixture(workspace, channel, recreate=True)
    build_seconds = time.perf_counter() - started
    timeline = build.bundle.timeline
    assert timeline is not None

    print(f"  build time        {build_seconds:.1f}s")
    print(f"  assets generated  {len(build.generated_assets)}")
    print(
        f"  timeline          {len(timeline.scenes)} scenes, {timeline.shot_count} shots, "
        f"{timeline.overlay_count} overlays, {len(timeline.audio.segments)} audio segments"
    )
    print(
        f"  duration          {timeline.total_frames} frames = "
        f"{timeline.duration_seconds:.3f}s at {timeline.fps} fps"
    )

    measurements: list[Measurement] = []

    def measure(label: str, **kwargs: Any) -> Any:
        profile = kwargs.pop("profile", FINAL_PROFILE)
        started_at = time.perf_counter()
        outcome = render(
            store.load(REFERENCE_PROJECT_ID), channel, profile, overwrite=True, **kwargs
        )
        elapsed = time.perf_counter() - started_at
        cache = SceneCache(build.bundle.root, profile)
        assembly = AssemblyCache(build.bundle.root, profile)
        measurements.append(
            Measurement(
                label=label,
                seconds=elapsed,
                scenes_rebuilt=list(outcome.cache.rebuilt),
                scenes_reused=list(outcome.cache.hits),
                assembly_seconds=outcome.assembly_seconds,
                assembly_cached=outcome.assembly_cached,
                output_bytes=(
                    outcome.output_path.stat().st_size
                    if outcome.output_path and outcome.output_path.is_file()
                    else 0
                ),
                cache_bytes=cache.total_bytes() + assembly.total_bytes(),
            )
        )
        print(
            f"  {label:34} {elapsed:7.1f}s  rebuilt={len(outcome.cache.rebuilt)} "
            f"reused={outcome.cache.hit_count} assembly={outcome.assembly_seconds:.1f}s"
            + ("  (assembly cached)" if outcome.assembly_cached else "")
        )
        return outcome

    print("\nRendering")
    cold_preview = measure("1. cold preview", profile=PREVIEW_PROFILE)
    cold_final = measure("2. cold final", profile=FINAL_PROFILE)
    warm_final = measure("3. warm final (unchanged)", profile=FINAL_PROFILE)

    # A unique edit, so this can never accidentally reproduce a cached state.
    edited = store.load(REFERENCE_PROJECT_ID)
    assert edited.timeline is not None
    scene = edited.timeline.scene_by_id("scene-04")
    assert scene is not None
    marker = f"benchmark edit {time.time_ns()}"
    for index, overlay in enumerate(scene.overlays):
        if overlay.is_text:
            scene.overlays[index] = overlay.model_copy(update={"text": marker})
            break
    edited.timeline.touch()
    edited.save(operation="benchmark edit")

    one_scene = measure("4. final after one scene edit", profile=FINAL_PROFILE)

    print("\nVerifying the final output")
    output = cold_final.output_path
    assert output is not None and output.is_file()

    inspection = warm_final.inspection or cold_final.inspection
    assert inspection is not None
    loudness = warm_final.loudness or cold_final.loudness

    from polymation.rendering.ffmpeg import ffprobe_json, require_ffmpeg, run_tool

    payload = ffprobe_json(output, extra_arguments=["-count_frames", "-select_streams", "v:0"])
    stream = payload["streams"][0]
    counted = stream.get("nb_read_frames") or stream.get("nb_frames")
    frame_count = int(counted) if counted not in (None, "N/A") else -1

    print(f"  resolution        {inspection.width}x{inspection.height}")
    print(f"  frame rate        {inspection.frame_rate}")
    print(f"  frames decoded    {frame_count} (expected {timeline.total_frames})")
    print(f"  duration          {inspection.duration_seconds}s")
    print(f"  video / audio     {inspection.video_codec} / {inspection.audio_codec}")
    print(f"  conforms          {inspection.conforms}")
    if loudness is not None:
        print(
            f"  loudness          {loudness.integrated_lufs} LUFS, "
            f"{loudness.true_peak_dbtp} dBTP, range {loudness.loudness_range_lu} LU"
        )

    print("\nAudio/video synchronisation")
    ffmpeg = require_ffmpeg()
    assert ffmpeg.path is not None
    scratch = workspace_root / "sync"
    scratch.mkdir(parents=True, exist_ok=True)

    from PIL import Image

    visual_hits = 0
    for seconds in marker_times():
        frame_path = scratch / f"frame-{seconds:.3f}.png"
        result = run_tool(
            ffmpeg.path,
            [
                "-nostdin",
                "-hide_banner",
                "-v",
                "error",
                "-y",
                "-ss",
                f"{seconds + 0.05:.6f}",
                "-i",
                str(output),
                "-frames:v",
                "1",
                str(frame_path),
            ],
        )
        if result.ok and frame_path.is_file():
            with Image.open(frame_path) as image:
                visual_hits += detect_marker(image)

    audio_path = scratch / "audio.wav"
    run_tool(
        ffmpeg.path,
        [
            "-nostdin",
            "-hide_banner",
            "-v",
            "error",
            "-y",
            "-i",
            str(output),
            "-map",
            "0:a:0",
            "-c:a",
            "pcm_s16le",
            "-ar",
            "48000",
            "-ac",
            "1",
            str(audio_path),
        ],
    )
    audio_hits = 0
    window_seconds = 0.02
    if audio_path.is_file():
        envelope = audio_envelope(audio_path, window_ms=int(window_seconds * 1000))
        for seconds in marker_times():
            index = int(seconds / window_seconds)
            local = envelope[max(0, index - 3) : index + 4]
            if local and max(local) > 0.15:
                audio_hits += 1

    expected_markers = len(marker_times())
    print(f"  markers expected  {expected_markers}")
    print(f"  visual detected   {visual_hits}")
    print(f"  audio detected    {audio_hits}")
    discrepancies = expected_markers - min(visual_hits, audio_hits)
    print(f"  discrepancies     {discrepancies}")

    print(f"\nPeak RSS (this process, excludes FFmpeg children): {peak_memory_mib():.1f} MiB")

    results: dict[str, Any] = {
        "host": conditions,
        "fixture": {
            "project_id": REFERENCE_PROJECT_ID,
            "build_seconds": round(build_seconds, 3),
            "generated_assets": len(build.generated_assets),
            "scenes": len(timeline.scenes),
            "shots": timeline.shot_count,
            "overlays": timeline.overlay_count,
            "audio_segments": len(timeline.audio.segments),
            "total_frames": timeline.total_frames,
            "duration_seconds": timeline.duration_seconds,
        },
        "measurements": [item.to_dict() for item in measurements],
        "output": {
            "path": str(output),
            "resolution": f"{inspection.width}x{inspection.height}",
            "frame_rate": inspection.frame_rate,
            "frames_decoded": frame_count,
            "frames_expected": timeline.total_frames,
            "duration_seconds": inspection.duration_seconds,
            "video_codec": inspection.video_codec,
            "audio_codec": inspection.audio_codec,
            "audio_sample_rate": inspection.audio_sample_rate,
            "pixel_format": inspection.pixel_format,
            "file_size_bytes": inspection.file_size_bytes,
            "conforms": inspection.conforms,
            "deviations": list(inspection.deviations),
        },
        "loudness": loudness.to_dict() if loudness else None,
        "synchronisation": {
            "markers_expected": expected_markers,
            "visual_markers_detected": visual_hits,
            "audio_markers_detected": audio_hits,
            "discrepancies": discrepancies,
            "marker_seconds": [round(value, 4) for value in marker_times()],
        },
        "peak_rss_mib_this_process": round(peak_memory_mib(), 1),
        "preview_output_bytes": next(
            (item.output_bytes for item in measurements if "preview" in item.label), 0
        ),
    }

    if arguments.json:
        arguments.json.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"\nWrote {arguments.json}")

    if arguments.keep:
        print(f"\nWorkspace kept at {studio}")
    else:
        shutil.rmtree(workspace_root, ignore_errors=True)

    unused = (cold_preview, one_scene)
    del unused

    ok = inspection.conforms and frame_count == timeline.total_frames and discrepancies == 0
    print(f"\nBenchmark {'PASSED' if ok else 'FAILED'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
