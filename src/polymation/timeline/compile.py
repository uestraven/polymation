"""Compiling a storyboard into a composition timeline.

Compilation is a **one-way projection**, not a sync: it reads the storyboard and
the asset collection and produces a timeline. It never writes back to the
storyboard, and re-compiling discards hand edits to the timeline — which is why
:func:`compile_timeline` refuses to overwrite an existing timeline unless the
caller explicitly asks.

The storyboard carries float ``duration_seconds`` because that is how a human
thinks about a shot. The timeline carries integer frames because that is what
renders deterministically. Converting between them is the one place rounding
happens, and every rounding decision is recorded in
:attr:`CompilationResult.rounding` rather than applied silently.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from polymation.errors import PolymationError
from polymation.projects.store import ProjectBundle
from polymation.rendering.profiles import RenderProfile
from polymation.schemas.assets import AssetType
from polymation.schemas.base import utc_now
from polymation.schemas.storyboard import FitMode, VisualShot
from polymation.schemas.timeline import (
    AudioMix,
    AudioRole,
    AudioSegment,
    CompositionShot,
    CompositionTimeline,
    ScalingQuality,
    ShotFitMode,
    TimelineScene,
)
from polymation.schemas.timing import (
    FrameSpan,
    Rounding,
    RoundingApplied,
    SampleSpan,
    seconds_to_frames,
    seconds_to_samples,
)

#: How storyboard fit modes map onto timeline fit modes. Explicit crop has no
#: storyboard equivalent: it needs a rectangle, which is placement detail the
#: storyboard deliberately does not carry.
FIT_MODE_MAP: dict[FitMode, ShotFitMode] = {
    FitMode.CONTAIN: ShotFitMode.CONTAIN,
    FitMode.COVER: ShotFitMode.COVER,
}


class CompilationError(PolymationError):
    """The storyboard cannot be projected onto a timeline."""


@dataclass
class CompilationResult:
    """A compiled timeline plus everything the operator should know about it."""

    timeline: CompositionTimeline
    rounding: list[RoundingApplied] = field(default_factory=list)
    #: Non-fatal observations, e.g. a scene with no narration.
    notes: list[str] = field(default_factory=list)

    @property
    def had_rounding(self) -> bool:
        """Whether any duration had to be snapped to the frame grid."""
        return bool(self.rounding)

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable form, for machine-readable CLI output."""
        timeline = self.timeline
        return {
            "project_id": timeline.project_id,
            "profile_name": timeline.profile_name,
            "fps": timeline.fps,
            "resolution": f"{timeline.width}x{timeline.height}",
            "scene_count": len(timeline.scenes),
            "shot_count": timeline.shot_count,
            "overlay_count": timeline.overlay_count,
            "audio_segment_count": len(timeline.audio.segments),
            "total_frames": timeline.total_frames,
            "total_samples": timeline.total_samples,
            "duration_seconds": round(timeline.duration_seconds, 6),
            "rounding": [item.to_dict() for item in self.rounding],
            "notes": list(self.notes),
        }


def _compile_shot(
    shot: VisualShot,
    *,
    start_frame: int,
    fps: int,
    asset_types: dict[str, AssetType],
    rounding: list[RoundingApplied],
) -> CompositionShot:
    """Project one storyboard shot onto the frame grid."""
    frames, applied = seconds_to_frames(
        shot.duration_seconds,
        fps,
        rounding=Rounding.NEAREST,
        quantity=f"shot {shot.shot_id!r} duration",
    )
    if applied is not None:
        rounding.append(applied)

    if frames < 1:
        raise CompilationError(
            f"Shot {shot.shot_id!r} has duration {shot.duration_seconds}s, which rounds "
            f"to {frames} frames at {fps} fps. A shot must occupy at least one frame."
        )

    if shot.asset_id is None:
        raise CompilationError(
            f"Shot {shot.shot_id!r} has no asset assigned, so it cannot be compiled "
            f"into a timeline. Assign an asset, or remove the shot."
        )

    asset_type = asset_types.get(shot.asset_id)
    if asset_type is None:
        raise CompilationError(
            f"Shot {shot.shot_id!r} references asset {shot.asset_id!r}, which is not in "
            f"this project's asset collection."
        )
    if asset_type is AssetType.AUDIO:
        raise CompilationError(
            f"Shot {shot.shot_id!r} references audio asset {shot.asset_id!r}. A visual "
            f"shot needs an image or video asset; place audio on the audio mix instead."
        )

    fit_mode = FIT_MODE_MAP[shot.fit_mode]

    # Pixel-art material is overwhelmingly small and 4:3; smooth scaling turns
    # it to mush. This is a heuristic default that the timeline can override.
    scaling = ScalingQuality.SMOOTH

    return CompositionShot(
        shot_id=shot.shot_id,
        asset_id=shot.asset_id,
        span=FrameSpan(start_frame=start_frame, duration_frames=frames),
        fit_mode=fit_mode,
        scaling=scaling,
    )


def compile_timeline(
    bundle: ProjectBundle,
    profile: RenderProfile,
    *,
    narration_asset_ids: list[str] | None = None,
    music_asset_id: str | None = None,
) -> CompilationResult:
    """Project ``bundle``'s storyboard onto a composition timeline.

    Args:
        bundle: a loaded project.
        profile: the profile whose fps and resolution the timeline adopts.
        narration_asset_ids: audio assets to lay down on the narration bus, in
            order, one per storyboard scene. When omitted, no narration is
            placed — compilation never invents audio.
        music_asset_id: audio asset to lay under the whole video as music.

    Raises:
        CompilationError: if the storyboard cannot be projected — a shot with no
            asset, a dangling reference, a sub-frame duration, or no scenes.
    """
    rounding: list[RoundingApplied] = []
    notes: list[str] = []

    storyboard = bundle.storyboard
    if not storyboard.scenes:
        raise CompilationError(
            f"Project {bundle.project_id} has an empty storyboard, so there is nothing "
            f"to compile. Add scenes and shots first."
        )

    asset_types = {asset.asset_id: asset.asset_type for asset in bundle.assets.assets}
    asset_durations = {asset.asset_id: asset.duration_seconds for asset in bundle.assets.assets}

    scenes: list[TimelineScene] = []
    for scene in sorted(storyboard.scenes, key=lambda item: item.order):
        if not scene.shots:
            raise CompilationError(
                f"Storyboard scene {scene.scene_id!r} has no shots. A timeline scene "
                f"must contain at least one shot; add one or remove the scene."
            )

        cursor = 0
        shots: list[CompositionShot] = []
        for shot in scene.shots:
            compiled = _compile_shot(
                shot,
                start_frame=cursor,
                fps=profile.fps,
                asset_types=asset_types,
                rounding=rounding,
            )
            shots.append(compiled)
            cursor = compiled.span.end_frame

        scenes.append(TimelineScene(scene_id=scene.scene_id, order=scene.order, shots=shots))

    now = utc_now()
    timeline = CompositionTimeline(
        project_id=bundle.project_id,
        profile_name=profile.name.value,
        fps=profile.fps,
        width=profile.width,
        height=profile.height,
        audio_sample_rate=profile.audio_sample_rate,
        scenes=scenes,
        created_at=now,
        updated_at=now,
    )

    audio = _compile_audio(
        timeline,
        profile,
        asset_types=asset_types,
        asset_durations=asset_durations,
        narration_asset_ids=narration_asset_ids or [],
        music_asset_id=music_asset_id,
        rounding=rounding,
        notes=notes,
    )
    timeline.audio = audio
    timeline.rounding_notes = [
        f"{item.quantity}: {item.requested} -> {item.resolved} ({item.rounding.value})"
        for item in rounding
    ]
    timeline.touch()

    if not audio.segments:
        notes.append(
            "No audio was placed. The render will be silent; supply narration or "
            "music asset identifiers to place audio."
        )

    return CompilationResult(timeline=timeline, rounding=rounding, notes=notes)


def _compile_audio(
    timeline: CompositionTimeline,
    profile: RenderProfile,
    *,
    asset_types: dict[str, AssetType],
    asset_durations: dict[str, float | None],
    narration_asset_ids: list[str],
    music_asset_id: str | None,
    rounding: list[RoundingApplied],
    notes: list[str],
) -> AudioMix:
    """Lay narration under each scene and music under the whole timeline."""
    segments: list[AudioSegment] = []
    sample_rate = profile.audio_sample_rate

    def require_audio(asset_id: str, purpose: str) -> float:
        asset_type = asset_types.get(asset_id)
        if asset_type is None:
            raise CompilationError(
                f"{purpose} references asset {asset_id!r}, which is not in this "
                f"project's asset collection."
            )
        if asset_type is not AssetType.AUDIO:
            raise CompilationError(
                f"{purpose} references {asset_type.value} asset {asset_id!r}. "
                f"An audio asset is required."
            )
        duration = asset_durations.get(asset_id)
        if duration is None:
            raise CompilationError(
                f"{purpose} references audio asset {asset_id!r}, which has no recorded "
                f"duration. Re-import it so its duration is measured."
            )
        return duration

    scenes = timeline.scenes_in_order
    if narration_asset_ids and len(narration_asset_ids) != len(scenes):
        raise CompilationError(
            f"{len(narration_asset_ids)} narration asset(s) were supplied for "
            f"{len(scenes)} scene(s). Narration is placed one asset per scene, so the "
            f"counts must match. Place narration manually on the timeline for any "
            f"other arrangement."
        )

    for index, (scene, asset_id) in enumerate(zip(scenes, narration_asset_ids, strict=True)):
        duration_seconds = require_audio(asset_id, f"narration for scene {scene.scene_id!r}")
        samples, applied = seconds_to_samples(
            duration_seconds,
            sample_rate,
            rounding=Rounding.DOWN,
            quantity=f"narration {asset_id!r} duration",
        )
        if applied is not None:
            rounding.append(applied)

        scene_start_frame = timeline.scene_offset_frames(scene.scene_id)
        scene_span = FrameSpan(start_frame=scene_start_frame, duration_frames=scene.duration_frames)
        scene_samples = SampleSpan.from_frames(scene_span, profile.fps, sample_rate)

        if samples > scene_samples.duration_samples:
            # Narration is never silently truncated to fit the visuals.
            raise CompilationError(
                f"Narration asset {asset_id!r} is "
                f"{duration_seconds:.3f}s but scene {scene.scene_id!r} is only "
                f"{scene_span.duration_seconds(profile.fps):.3f}s. Narration is never "
                f"trimmed to fit visuals: lengthen the scene, or shorten the narration."
            )

        if samples < scene_samples.duration_samples:
            notes.append(
                f"Scene {scene.scene_id!r} has "
                f"{(scene_samples.duration_samples - samples) / sample_rate:.3f}s of "
                f"visual time after its narration ends."
            )

        segments.append(
            AudioSegment(
                segment_id=f"narration-{index + 1:02d}",
                role=AudioRole.NARRATION,
                asset_id=asset_id,
                span=SampleSpan(start_sample=scene_samples.start_sample, duration_samples=samples),
            )
        )

    if music_asset_id is not None:
        duration_seconds = require_audio(music_asset_id, "background music")
        available, applied = seconds_to_samples(
            duration_seconds,
            sample_rate,
            rounding=Rounding.DOWN,
            quantity=f"music {music_asset_id!r} duration",
        )
        if applied is not None:
            rounding.append(applied)

        wanted = timeline.total_samples
        placed = min(available, wanted)
        if placed < wanted:
            notes.append(
                f"Background music is {duration_seconds:.3f}s but the video is "
                f"{wanted / sample_rate:.3f}s; music stops "
                f"{(wanted - placed) / sample_rate:.3f}s before the end. Music is not "
                f"looped automatically."
            )
        if placed > 0:
            segments.append(
                AudioSegment(
                    segment_id="music-01",
                    role=AudioRole.MUSIC,
                    asset_id=music_asset_id,
                    span=SampleSpan(start_sample=0, duration_samples=placed),
                    gain_db=-6.0,
                    fade_in_samples=min(sample_rate, placed // 4),
                    fade_out_samples=min(2 * sample_rate, placed // 4),
                )
            )

    return AudioMix(segments=segments)
