"""The M0.1 rendering proof of concept.

Scope boundary, stated plainly: this renderer turns a sequence of *still-image*
shots plus one optional audio bed into a single H.264/AAC MP4. It does not do
transitions, motion, narration synchronisation, captions, video-source shots or
per-scene caching. Those are M0.2 work and are deliberately absent rather than
stubbed.

Every FFmpeg invocation is an explicit argument vector. Filter graphs are built
from validated numeric values and palette colours, never from filenames.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from polymation.config.channel import ChannelBrand
from polymation.errors import NotRenderReadyError, RenderError
from polymation.projects.store import ProjectBundle
from polymation.rendering.ffmpeg import ToolInfo, ffprobe_json, require_ffmpeg, run_tool
from polymation.rendering.profiles import FINAL_PROFILE, RenderProfile
from polymation.schemas.assets import AssetType
from polymation.schemas.storyboard import FitMode, VisualShot
from polymation.validation.validator import RenderReadiness, assess_render_readiness

#: Output specification mandated for the M0.1 demonstration.
OUTPUT_WIDTH = 1920
OUTPUT_HEIGHT = 1080
OUTPUT_FPS = 30
OUTPUT_VIDEO_CODEC = "libx264"
OUTPUT_VIDEO_CODEC_NAME = "h264"
OUTPUT_AUDIO_CODEC = "aac"
OUTPUT_AUDIO_SAMPLE_RATE = 48_000
OUTPUT_AUDIO_CHANNELS = 2

#: Palette colour used to fill letterbox/pillarbox bars.
PADDING_COLOUR_NAME = "deep_indigo"


@dataclass(frozen=True)
class RenderResult:
    """Everything observed about a completed render."""

    output_path: Path
    ffmpeg_version: str
    ffmpeg_argv: tuple[str, ...]
    render_seconds: float
    file_size_bytes: int
    readiness: RenderReadiness

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable form."""
        return {
            "output_path": str(self.output_path),
            "ffmpeg_version": self.ffmpeg_version,
            "render_seconds": round(self.render_seconds, 3),
            "file_size_bytes": self.file_size_bytes,
            "cautions": [finding.to_dict() for finding in self.readiness.cautions],
        }


@dataclass(frozen=True)
class OutputInspection:
    """ffprobe's description of a rendered file, plus conformance checks."""

    width: int | None
    height: int | None
    frame_rate: float | None
    pixel_format: str | None
    duration_seconds: float | None
    video_codec: str | None
    audio_codec: str | None
    audio_sample_rate: int | None
    audio_channels: int | None
    file_size_bytes: int
    #: Specification deviations, empty when the output conforms.
    deviations: tuple[str, ...]

    @property
    def conforms(self) -> bool:
        """Whether the output matches the mandated M0.1 specification."""
        return not self.deviations

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable form."""
        return {
            "width": self.width,
            "height": self.height,
            "frame_rate": self.frame_rate,
            "pixel_format": self.pixel_format,
            "duration_seconds": self.duration_seconds,
            "video_codec": self.video_codec,
            "audio_codec": self.audio_codec,
            "audio_sample_rate": self.audio_sample_rate,
            "audio_channels": self.audio_channels,
            "file_size_bytes": self.file_size_bytes,
            "conforms_to_specification": self.conforms,
            "deviations": list(self.deviations),
        }


def _fit_filter(fit_mode: FitMode, pad_colour: str) -> str:
    """Return the scale/pad filter chain for ``fit_mode``.

    Neither mode distorts: ``contain`` scales down to fit and pads the remainder,
    ``cover`` scales up to fill and crops the overflow.
    """
    if fit_mode is FitMode.CONTAIN:
        return (
            f"scale={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:force_original_aspect_ratio=decrease,"
            f"pad={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:(ow-iw)/2:(oh-ih)/2:color={pad_colour}"
        )
    return (
        f"scale={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}"
    )


def _build_filter_graph(shots: list[VisualShot], pad_colour: str) -> str:
    chains: list[str] = []
    for index, shot in enumerate(shots):
        chains.append(
            f"[{index}:v]{_fit_filter(shot.fit_mode, pad_colour)},"
            f"setsar=1,fps={OUTPUT_FPS},format=yuv420p[v{index}]"
        )
    concat_inputs = "".join(f"[v{index}]" for index in range(len(shots)))
    chains.append(f"{concat_inputs}concat=n={len(shots)}:v=1:a=0[vout]")
    return ";".join(chains)


def render_project(
    bundle: ProjectBundle,
    brand: ChannelBrand,
    *,
    output_path: Path | None = None,
    audio_asset_id: str | None = None,
    overwrite: bool = False,
) -> RenderResult:
    """Render ``bundle``'s storyboard to an MP4.

    Args:
        bundle: a loaded project. Must be render-ready.
        brand: channel brand, used for the padding colour.
        output_path: destination. Defaults to ``<project>/output/<project_id>.mp4``.
        audio_asset_id: audio asset used as the bed. Defaults to the project's
            single audio asset, if there is exactly one.
        overwrite: permit replacing an existing output file.

    Raises:
        NotRenderReadyError: if validation found blockers.
        RenderError: if a shot references unusable media, or FFmpeg fails.
        MissingDependencyError: if ffmpeg is unavailable.
    """
    readiness = assess_render_readiness(bundle)
    if not readiness.render_ready:
        detail = "\n".join(f"  - [{item.code}] {item.message}" for item in readiness.blockers)
        raise NotRenderReadyError(f"Project {bundle.project_id} is not render-ready:\n{detail}")

    ffmpeg: ToolInfo = require_ffmpeg()
    if ffmpeg.path is None or ffmpeg.version is None:  # pragma: no cover - guarded above
        raise RenderError("ffmpeg reported as available but has no resolved path.")

    shots = bundle.storyboard.shots
    if not shots:  # pragma: no cover - guarded by readiness check
        raise NotRenderReadyError("The storyboard contains no shots.")

    inputs: list[str] = []
    for shot in shots:
        if shot.asset_id is None:  # pragma: no cover - guarded by readiness check
            raise RenderError(f"Shot {shot.shot_id!r} has no asset assigned.")
        asset = bundle.assets.by_id(shot.asset_id)
        if asset is None:  # pragma: no cover - guarded by validation
            raise RenderError(f"Shot {shot.shot_id!r} references unknown asset.")
        if asset.asset_type is not AssetType.IMAGE:
            raise RenderError(
                f"Shot {shot.shot_id!r} uses {asset.asset_type.value} asset "
                f"{asset.asset_id}. The M0.1 renderer composes still images only; "
                f"video and audio shots are M0.2 work."
            )
        source = bundle.asset_path(asset.path)
        if not source.is_file():
            raise RenderError(f"Asset file {asset.path!r} is missing on disk.")

        inputs += ["-loop", "1", "-t", f"{shot.duration_seconds:.6f}", "-i", str(source)]

    audio_asset = _select_audio_asset(bundle, audio_asset_id)
    audio_input_index = len(shots)
    if audio_asset is not None:
        audio_source = bundle.asset_path(audio_asset.path)
        if not audio_source.is_file():
            raise RenderError(f"Audio asset file {audio_asset.path!r} is missing on disk.")
        inputs += ["-i", str(audio_source)]

    destination = output_path or (
        bundle.root / bundle.manifest.paths.output_dir / f"{bundle.project_id}.mp4"
    )
    if destination.exists() and not overwrite:
        raise RenderError(
            f"{destination} already exists. Pass --overwrite to replace it; existing "
            f"renders are not silently discarded."
        )
    destination.parent.mkdir(parents=True, exist_ok=True)

    total_duration = sum(shot.duration_seconds for shot in shots)
    pad_colour = brand.palette.as_ffmpeg_color(PADDING_COLOUR_NAME)

    arguments: list[str] = [
        "-nostdin",
        "-hide_banner",
        "-y" if overwrite else "-n",
        *inputs,
        "-filter_complex",
        _build_filter_graph(shots, pad_colour),
        "-map",
        "[vout]",
    ]

    if audio_asset is not None:
        arguments += [
            "-map",
            f"{audio_input_index}:a:0",
            "-c:a",
            OUTPUT_AUDIO_CODEC,
            "-ar",
            str(OUTPUT_AUDIO_SAMPLE_RATE),
            "-ac",
            str(OUTPUT_AUDIO_CHANNELS),
            "-b:a",
            "128k",
            # Stop at the shorter of video and audio so a long bed cannot extend
            # the video past the storyboard.
            "-shortest",
        ]

    arguments += [
        "-c:v",
        OUTPUT_VIDEO_CODEC,
        "-profile:v",
        "high",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(OUTPUT_FPS),
        "-t",
        f"{total_duration:.6f}",
        "-movflags",
        "+faststart",
        str(destination),
    ]

    started = time.perf_counter()
    result = run_tool(ffmpeg.path, arguments)
    elapsed = time.perf_counter() - started

    if not result.ok:
        raise RenderError(
            f"FFmpeg exited with status {result.returncode}. Last lines of output:\n{result.tail()}"
        )

    if not destination.is_file():
        raise RenderError(f"FFmpeg reported success but {destination} was not created.")

    return RenderResult(
        output_path=destination,
        ffmpeg_version=ffmpeg.version,
        ffmpeg_argv=result.argv,
        render_seconds=elapsed,
        file_size_bytes=destination.stat().st_size,
        readiness=readiness,
    )


def _select_audio_asset(bundle: ProjectBundle, audio_asset_id: str | None) -> Any:
    """Pick the audio bed: the named asset, or the sole audio asset if unambiguous."""
    audio_assets = [asset for asset in bundle.assets.assets if asset.asset_type is AssetType.AUDIO]

    if audio_asset_id is not None:
        chosen = bundle.assets.by_id(audio_asset_id)
        if chosen is None:
            raise RenderError(f"No asset with identifier {audio_asset_id!r} exists.")
        if chosen.asset_type is not AssetType.AUDIO:
            raise RenderError(
                f"Asset {audio_asset_id!r} is {chosen.asset_type.value} media, not audio."
            )
        return chosen

    if len(audio_assets) == 1:
        return audio_assets[0]
    return None


def _as_float(raw: object) -> float | None:
    try:
        return float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _as_int(raw: object) -> int | None:
    try:
        return int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _parse_rate(raw: object) -> float | None:
    if not isinstance(raw, str) or "/" not in raw:
        return _as_float(raw)
    numerator, _, denominator = raw.partition("/")
    try:
        denominator_value = float(denominator)
    except ValueError:
        return None
    if denominator_value == 0:
        return None
    try:
        return float(numerator) / denominator_value
    except ValueError:
        return None


def inspect_output(
    path: Path,
    *,
    expected_duration_seconds: float | None = None,
    duration_tolerance_seconds: float = 0.5,
    require_audio: bool = True,
    profile: RenderProfile | None = None,
) -> OutputInspection:
    """Inspect a rendered file with ffprobe and check it against a profile.

    Args:
        path: the rendered file.
        expected_duration_seconds: when given, the duration is checked against
            it within ``duration_tolerance_seconds``.
        duration_tolerance_seconds: permitted duration drift.
        require_audio: check the audio stream. False for a deliberately silent
            render.
        profile: the profile to check against. Defaults to the final profile,
            which is what M0.1 produced, so existing callers keep their meaning.

    Raises:
        RenderError: if the file does not exist.
        MissingDependencyError: if ffprobe is unavailable.
        ValueError: if ffprobe cannot describe the file.
    """
    expected = profile or FINAL_PROFILE
    if not path.is_file():
        raise RenderError(f"{path} does not exist, so it cannot be inspected.")

    payload = ffprobe_json(path)
    streams = payload.get("streams")
    streams = streams if isinstance(streams, list) else []
    container = payload.get("format", {})
    container = container if isinstance(container, dict) else {}

    video = next(
        (
            stream
            for stream in streams
            if isinstance(stream, dict) and stream.get("codec_type") == "video"
        ),
        None,
    )
    audio = next(
        (
            stream
            for stream in streams
            if isinstance(stream, dict) and stream.get("codec_type") == "audio"
        ),
        None,
    )

    width = _as_int(video.get("width")) if video else None
    height = _as_int(video.get("height")) if video else None
    frame_rate = _parse_rate(video.get("r_frame_rate")) if video else None
    video_codec = str(video.get("codec_name")) if video else None
    pixel_format = str(video.get("pix_fmt")) if video and video.get("pix_fmt") else None
    audio_codec = str(audio.get("codec_name")) if audio else None
    audio_sample_rate = _as_int(audio.get("sample_rate")) if audio else None
    audio_channels = _as_int(audio.get("channels")) if audio else None
    duration = _as_float(container.get("duration"))

    deviations: list[str] = []
    if width != expected.width or height != expected.height:
        deviations.append(
            f"resolution is {width}x{height}, expected {expected.width}x{expected.height}"
        )
    if frame_rate is None or abs(frame_rate - expected.fps) > 0.01:
        deviations.append(f"frame rate is {frame_rate}, expected {expected.fps}")
    if video_codec != OUTPUT_VIDEO_CODEC_NAME:
        deviations.append(f"video codec is {video_codec!r}, expected {OUTPUT_VIDEO_CODEC_NAME!r}")
    if pixel_format is not None and pixel_format != expected.pixel_format:
        deviations.append(f"pixel format is {pixel_format!r}, expected {expected.pixel_format!r}")
    if require_audio:
        if audio_codec != expected.audio_codec:
            deviations.append(f"audio codec is {audio_codec!r}, expected {expected.audio_codec!r}")
        if audio_sample_rate != expected.audio_sample_rate:
            deviations.append(
                f"audio sample rate is {audio_sample_rate}, expected {expected.audio_sample_rate}"
            )
    if expected_duration_seconds is not None:
        drift = None if duration is None else abs(duration - expected_duration_seconds)
        if drift is None or drift > duration_tolerance_seconds:
            deviations.append(
                f"duration is {duration}s, expected about {expected_duration_seconds}s "
                f"(tolerance {duration_tolerance_seconds}s)"
            )

    return OutputInspection(
        width=width,
        height=height,
        frame_rate=frame_rate,
        pixel_format=pixel_format,
        duration_seconds=duration,
        video_codec=video_codec,
        audio_codec=audio_codec,
        audio_sample_rate=audio_sample_rate,
        audio_channels=audio_channels,
        file_size_bytes=path.stat().st_size,
        deviations=tuple(deviations),
    )
