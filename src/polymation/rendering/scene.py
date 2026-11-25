"""Rendering one scene to a cached intermediate file.

A scene is the unit of caching, so it must render **independently**: nothing
about scene N's output may depend on scene N-1. That constraint is why
cross-scene crossfades are not supported and why audio is mixed at final
assembly rather than per scene — audio deliberately crosses scene boundaries.

Scene intermediates are video-only, encoded with the output profile's exact
settings so final assembly can concatenate them by stream copy instead of
re-encoding. That is what makes a warm render fast.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from polymation.config.channel import ChannelBrand
from polymation.errors import RenderError
from polymation.rendering.ffmpeg import ToolInfo, run_tool
from polymation.rendering.graph import (
    FFmpegPlan,
    join_shot_chains,
    overlay_chains,
    shot_input_arguments,
    shot_video_chain,
)
from polymation.rendering.overlays import PlacedOverlay, render_overlays
from polymation.rendering.profiles import RenderProfile
from polymation.schemas.assets import AssetType, MediaAsset
from polymation.schemas.timeline import CompositionTimeline, TimelineScene


@dataclass
class SceneRenderResult:
    """A rendered scene intermediate."""

    scene_id: str
    path: Path
    frames: int
    argv: tuple[str, ...]
    overlays: list[PlacedOverlay]

    @property
    def used_fallback_fonts(self) -> bool:
        """Whether any overlay text was drawn with a substitute typeface."""
        return any(overlay.used_fallback_font for overlay in self.overlays)

    @property
    def truncated_overlays(self) -> list[str]:
        """Overlays whose text had to be cut to fit its line budget."""
        return [overlay.overlay_id for overlay in self.overlays if overlay.truncated]


def build_scene_plan(
    scene: TimelineScene,
    timeline: CompositionTimeline,
    profile: RenderProfile,
    brand: ChannelBrand,
    *,
    asset_paths: dict[str, Path],
    assets: dict[str, MediaAsset],
    placed_overlays: list[PlacedOverlay],
) -> FFmpegPlan:
    """Build the FFmpeg plan that renders ``scene``.

    Separated from execution so the graph can be asserted on in tests without
    invoking an encoder, and so the plan is inspectable when a render fails.
    """
    plan = FFmpegPlan()
    shots = scene.shots_in_order
    shot_labels: list[str] = []

    for position, shot in enumerate(shots):
        asset = assets.get(shot.asset_id)
        if asset is None:
            raise RenderError(
                f"scene {scene.scene_id!r} shot {shot.shot_id!r} references unknown "
                f"asset {shot.asset_id!r}"
            )
        path = asset_paths.get(shot.asset_id)
        if path is None or not path.is_file():
            raise RenderError(
                f"scene {scene.scene_id!r} shot {shot.shot_id!r} needs asset "
                f"{shot.asset_id!r}, which is not available on disk"
            )

        frames = shot.span.duration_frames
        index = plan.add_input(shot_input_arguments(shot, asset, path, profile, frames))

        available: int | None = None
        if asset.asset_type is AssetType.VIDEO and asset.duration_seconds is not None:
            trim = shot.source_trim.start_frame if shot.source_trim else 0
            available = int(asset.duration_seconds * profile.fps) - trim

        label = f"sv{position}"
        plan.chains.append(
            shot_video_chain(
                shot,
                asset,
                profile,
                brand,
                input_index=index,
                output_label=label,
                frames=frames,
                source_frames_available=available,
            )
        )
        shot_labels.append(label)

    joined_label = "scene_joined"
    plan.chains.extend(join_shot_chains(shot_labels, shots, profile, output_label=joined_label))

    overlay_inputs: list[tuple[int, int, int, int, int, int, float]] = []
    for overlay in placed_overlays:
        index = plan.add_input(["-i", str(overlay.image.path)])
        overlay_inputs.append(
            (
                index,
                overlay.x,
                overlay.y,
                overlay.start_frame,
                overlay.end_frame,
                0,
                overlay.opacity,
            )
        )

    final_label = "scene_out"
    plan.chains.extend(
        overlay_chains(overlay_inputs, profile, base_label=joined_label, output_label=final_label)
    )
    plan.maps = [f"[{final_label}]"]

    return plan


def render_scene(
    scene: TimelineScene,
    timeline: CompositionTimeline,
    profile: RenderProfile,
    brand: ChannelBrand,
    ffmpeg: ToolInfo,
    *,
    asset_paths: dict[str, Path],
    assets: dict[str, MediaAsset],
    destination: Path,
    work_dir: Path,
) -> SceneRenderResult:
    """Render ``scene`` to ``destination``.

    The file is produced in a temporary location and moved into place only on
    success, so an interrupted render never leaves a partial file where a
    complete one is expected.

    Raises:
        RenderError: if an asset is unavailable or FFmpeg fails.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    image_paths = {
        asset_id: path
        for asset_id, path in asset_paths.items()
        if assets.get(asset_id) is not None and assets[asset_id].asset_type is AssetType.IMAGE
    }

    placed = render_overlays(
        scene.all_overlays(),
        timeline,
        profile,
        brand,
        work_dir=work_dir,
        image_paths=image_paths,
    )

    plan = build_scene_plan(
        scene,
        timeline,
        profile,
        brand,
        asset_paths=asset_paths,
        assets=assets,
        placed_overlays=placed,
    )

    leaked = plan.leaked_paths()
    if leaked:  # pragma: no cover - guarded by a dedicated test
        raise RenderError(
            f"internal error: source path(s) {leaked} leaked into the filter graph "
            f"for scene {scene.scene_id!r}. Paths must only ever be -i arguments."
        )

    frames = scene.duration_frames
    if ffmpeg.path is None:  # pragma: no cover - callers resolve ffmpeg first
        raise RenderError("ffmpeg has no resolved path")

    destination.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        dir=destination.parent,
        prefix=f".{destination.stem}.",
        suffix=profile.container_suffix,
        delete=False,
    )
    handle.close()
    temporary = Path(handle.name)

    arguments: list[str] = [
        "-nostdin",
        "-hide_banner",
        "-y",
        *plan.input_args,
        "-filter_complex",
        plan.filter_complex,
        "-map",
        plan.maps[0],
        # No audio in a scene intermediate: audio crosses scene boundaries and
        # is mixed once, at assembly.
        "-an",
        *profile.video_encode_args(),
        # Exactly the scene's frame count, so concatenation is frame-exact.
        "-frames:v",
        str(frames),
        str(temporary),
    ]

    try:
        result = run_tool(ffmpeg.path, arguments)
        if not result.ok:
            raise RenderError(
                f"FFmpeg failed rendering scene {scene.scene_id!r} (status "
                f"{result.returncode}):\n{result.tail()}"
            )
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise RenderError(
                f"FFmpeg reported success for scene {scene.scene_id!r} but produced no output."
            )
        temporary.replace(destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise

    return SceneRenderResult(
        scene_id=scene.scene_id,
        path=destination,
        frames=frames,
        argv=result.argv,
        overlays=placed,
    )
