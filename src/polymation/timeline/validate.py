"""Validating a composition timeline against the project and the profile.

The timeline schema already guarantees internal shape — spans tile their scene,
motion stays inside the image, overlays fit their span. This module checks the
things only visible with the project and filesystem in hand: do referenced
assets exist, is a video source long enough for the shot that trims it, do
overlays stay inside the safe area, do captions fit the video.

Findings reuse :class:`~polymation.validation.validator.Finding`, so timeline
problems appear in the same error/warning report as everything else.
"""

from __future__ import annotations

from polymation.media.checksum import sha256_file
from polymation.projects.store import ProjectBundle
from polymation.rendering.profiles import RenderProfile
from polymation.schemas.assets import AssetType, MediaAsset
from polymation.schemas.timeline import (
    AudioRole,
    CompositionShot,
    CompositionTimeline,
    OverlayKind,
    ShortSourcePolicy,
    ShotFitMode,
    TimelineScene,
    TransitionKind,
)
from polymation.validation.findings import ValidationReport

#: Overlay area below this fraction of the frame is probably a mistake.
MIN_SENSIBLE_OVERLAY_FRACTION = 0.0005


def validate_timeline(
    bundle: ProjectBundle,
    timeline: CompositionTimeline,
    profile: RenderProfile,
    *,
    verify_checksums: bool = False,
) -> ValidationReport:
    """Validate ``timeline`` against ``bundle`` and ``profile``.

    Args:
        bundle: the project the timeline belongs to.
        timeline: the timeline to check.
        profile: the profile the timeline will be rendered with.
        verify_checksums: re-hash referenced asset files. Off by default here
            because ``polymation validate`` already does it for the whole
            project; on for render preflight, where stale bytes matter.
    """
    report = ValidationReport(project_id=timeline.project_id)

    if timeline.project_id != bundle.project_id:
        report.error(
            "timeline_project_mismatch",
            f"timeline.json declares project_id {timeline.project_id!r} but belongs to "
            f"project {bundle.project_id!r}.",
            location="timeline.json",
        )
        return report

    _check_profile_agreement(timeline, profile, report)

    assets = {asset.asset_id: asset for asset in bundle.assets.assets}

    if not timeline.scenes:
        report.error(
            "timeline_empty",
            "The timeline contains no scenes, so there is nothing to render.",
            location="timeline.json",
        )

    for scene in timeline.scenes_in_order:
        _check_scene(bundle, timeline, scene, profile, assets, report, verify_checksums)

    _check_audio(bundle, timeline, profile, assets, report)
    _check_captions(bundle, timeline, profile, report)

    return report


def _check_profile_agreement(
    timeline: CompositionTimeline, profile: RenderProfile, report: ValidationReport
) -> None:
    """A timeline authored for one geometry rendered at another is a warning."""
    if timeline.fps != profile.fps:
        report.error(
            "timeline_fps_mismatch",
            f"The timeline is authored at {timeline.fps} fps but profile "
            f"{profile.name.value!r} renders at {profile.fps} fps. Frame positions would "
            f"not mean the same thing; recompile the timeline for this profile.",
            location="timeline.json",
        )

    if (timeline.width, timeline.height) != profile.resolution:
        timeline_aspect = timeline.width / timeline.height
        if abs(timeline_aspect - profile.aspect_ratio) > 1e-6:
            report.error(
                "timeline_aspect_mismatch",
                f"The timeline is authored at {timeline.width}x{timeline.height} "
                f"(aspect {timeline_aspect:.4f}) but profile {profile.name.value!r} is "
                f"{profile.width}x{profile.height} (aspect {profile.aspect_ratio:.4f}). "
                f"Overlay placement would not scale faithfully.",
                location="timeline.json",
            )
        # Same aspect, different resolution is fine and expected: that is
        # exactly the preview/final relationship.

    if timeline.audio_sample_rate != profile.audio_sample_rate:
        report.error(
            "timeline_sample_rate_mismatch",
            f"The timeline is authored at {timeline.audio_sample_rate} Hz but profile "
            f"{profile.name.value!r} outputs {profile.audio_sample_rate} Hz. Audio "
            f"sample positions would not align with frames.",
            location="timeline.json",
        )


def _check_scene(
    bundle: ProjectBundle,
    timeline: CompositionTimeline,
    scene: TimelineScene,
    profile: RenderProfile,
    assets: dict[str, MediaAsset],
    report: ValidationReport,
    verify_checksums: bool,
) -> None:
    location = f"timeline.json:{scene.scene_id}"

    # A timeline scene need not exist in the storyboard — the timeline is
    # independent — but a mismatch is worth surfacing.
    storyboard_scene_ids = {item.scene_id for item in bundle.storyboard.scenes}
    if storyboard_scene_ids and scene.scene_id not in storyboard_scene_ids:
        report.warning(
            "timeline_scene_not_in_storyboard",
            f"Timeline scene {scene.scene_id!r} has no matching storyboard scene. The "
            f"timeline is allowed to diverge from creative intent, but check this is "
            f"deliberate.",
            location=location,
        )

    for shot in scene.shots_in_order:
        _check_shot(bundle, shot, profile, assets, report, verify_checksums)

    for overlay in scene.all_overlays():
        _check_overlay_placement(timeline, overlay.overlay_id, overlay, profile, report)
        if overlay.kind is OverlayKind.IMAGE and overlay.image_asset_id is not None:
            asset = assets.get(overlay.image_asset_id)
            if asset is None:
                report.error(
                    "overlay_asset_missing",
                    f"Overlay {overlay.overlay_id!r} references asset "
                    f"{overlay.image_asset_id!r}, which is not in the asset collection.",
                    location=location,
                )
            elif asset.asset_type is not AssetType.IMAGE:
                report.error(
                    "overlay_asset_not_an_image",
                    f"Overlay {overlay.overlay_id!r} references "
                    f"{asset.asset_type.value} asset {asset.asset_id}; an image overlay "
                    f"needs an image.",
                    location=location,
                )

    for label, transition in (
        ("transition_in", scene.transition_in),
        ("transition_out", scene.transition_out),
    ):
        if transition.kind is TransitionKind.CROSSFADE:
            report.error(
                "cross_scene_crossfade",
                f"Scene {scene.scene_id!r} {label} is a crossfade. Cross-scene "
                f"crossfades are not supported: they would couple adjacent scenes and "
                f"break per-scene caching. Use a shot-level crossfade inside a scene.",
                location=location,
            )


def _check_shot(
    bundle: ProjectBundle,
    shot: CompositionShot,
    profile: RenderProfile,
    assets: dict[str, MediaAsset],
    report: ValidationReport,
    verify_checksums: bool,
) -> None:
    location = f"timeline.json:{shot.shot_id}"
    asset = assets.get(shot.asset_id)

    if asset is None:
        report.error(
            "invalid_asset_reference",
            f"Shot {shot.shot_id!r} references asset {shot.asset_id!r}, which is not in "
            f"the asset collection.",
            location=location,
        )
        return

    if asset.asset_type is AssetType.AUDIO:
        report.error(
            "shot_references_audio",
            f"Shot {shot.shot_id!r} references audio asset {asset.asset_id}. A visual "
            f"shot needs an image or video.",
            location=location,
        )
        return

    path = bundle.asset_path(asset.path)
    if not path.is_file():
        report.error(
            "missing_asset_file",
            f"Shot {shot.shot_id!r} needs {asset.path!r}, but no file exists there.",
            location=location,
        )
        return

    if verify_checksums:
        actual = sha256_file(path)
        if actual != asset.sha256:
            report.error(
                "asset_checksum_mismatch",
                f"Asset {asset.asset_id} used by shot {shot.shot_id!r} hashes to "
                f"{actual} but the manifest records {asset.sha256}. The file changed "
                f"after import; any cached render of it is void.",
                location=location,
            )

    if asset.asset_type is AssetType.IMAGE:
        if shot.source_trim is not None and shot.source_trim.start_frame != 0:
            report.error(
                "trim_on_still_image",
                f"Shot {shot.shot_id!r} trims into still image {asset.asset_id}. A still "
                f"has no timeline to trim; remove source_trim.",
                location=location,
            )
        if shot.include_source_audio:
            report.error(
                "source_audio_from_image",
                f"Shot {shot.shot_id!r} requests source audio from still image "
                f"{asset.asset_id}, which has none.",
                location=location,
            )
    else:
        _check_video_source_length(shot, asset, profile, report, location)

    if asset.asset_type is AssetType.VIDEO and not shot.motion.is_static:
        report.error(
            "motion_on_video",
            f"Shot {shot.shot_id!r} applies {shot.motion.kind.value!r} motion to video "
            f"asset {asset.asset_id}. Image motion applies to stills only; moving an "
            f"already-moving source is out of scope for M0.2.",
            location=location,
        )

    if asset.asset_type is AssetType.IMAGE and asset.width is not None and asset.height is not None:
        _check_upscale(shot, asset, profile, report, location)


def _check_video_source_length(
    shot: CompositionShot,
    asset: MediaAsset,
    profile: RenderProfile,
    report: ValidationReport,
    location: str,
) -> None:
    """A video source must cover the shot, unless a policy says otherwise."""
    if asset.duration_seconds is None:
        report.error(
            "video_duration_unknown",
            f"Video asset {asset.asset_id} has no recorded duration, so shot "
            f"{shot.shot_id!r} cannot be checked for length. Re-import the asset.",
            location=location,
        )
        return

    trim_start = shot.source_trim.start_frame if shot.source_trim else 0
    available_frames = int(asset.duration_seconds * profile.fps) - trim_start
    needed = shot.span.duration_frames

    if available_frames >= needed:
        return

    shortfall = needed - available_frames
    detail = (
        f"Shot {shot.shot_id!r} needs {needed} frames from video asset "
        f"{asset.asset_id} starting at source frame {trim_start}, but only "
        f"{max(0, available_frames)} frames are available "
        f"({shortfall} short, {shortfall / profile.fps:.3f}s)."
    )

    if shot.short_source_policy is ShortSourcePolicy.ERROR:
        report.error(
            "source_clip_too_short",
            f"{detail} The source is never silently looped, frozen or stretched. "
            f"Shorten the shot, reduce the trim, or set short_source_policy to "
            f"'hold_last_frame' or 'trim_shot' to make the behaviour explicit.",
            location=location,
        )
    else:
        report.warning(
            "source_clip_short_policy_applied",
            f"{detail} Policy {shot.short_source_policy.value!r} will be applied.",
            location=location,
        )


def _check_upscale(
    shot: CompositionShot,
    asset: MediaAsset,
    profile: RenderProfile,
    report: ValidationReport,
    location: str,
) -> None:
    """Warn when motion or fit would magnify a still beyond its pixels."""
    if asset.width is None or asset.height is None:
        return

    max_zoom = max(shot.motion.start_zoom, shot.motion.end_zoom)

    # `contain` scales to fit, so the binding constraint is the smaller ratio;
    # `cover` and `crop` fill the frame, so it is the larger one.
    ratios = (profile.width / asset.width, profile.height / asset.height)
    base_scale = min(ratios) if shot.fit_mode is ShotFitMode.CONTAIN else max(ratios)
    scale = base_scale * max_zoom
    if scale > 2.0:
        report.warning(
            "heavy_upscale",
            f"Shot {shot.shot_id!r} magnifies {asset.width}x{asset.height} asset "
            f"{asset.asset_id} by about {scale:.1f}x to fill "
            f"{profile.width}x{profile.height}. Expect visible softness; consider a "
            f"larger source or less zoom.",
            location=location,
        )


def _check_overlay_placement(
    timeline: CompositionTimeline,
    overlay_id: str,
    overlay: object,
    profile: RenderProfile,
    report: ValidationReport,
) -> None:
    """Warn when an overlay's nominal box sits outside the safe area."""
    width_fraction = getattr(overlay, "width_fraction", None)
    height_fraction = getattr(overlay, "height_fraction", None)

    if width_fraction is not None and height_fraction is not None:
        area = width_fraction * height_fraction
        if area < MIN_SENSIBLE_OVERLAY_FRACTION:
            report.warning(
                "overlay_vanishingly_small",
                f"Overlay {overlay_id!r} covers {area * 100:.3f}% of the frame and will "
                f"be effectively invisible at {profile.width}x{profile.height}.",
                location="timeline.json",
            )

    inset_px_x = int(timeline.safe_area_inset * profile.width)
    inset_px_y = int(timeline.safe_area_inset * profile.height)
    offset_x = getattr(overlay, "offset_x_px", 0)
    offset_y = getattr(overlay, "offset_y_px", 0)

    if abs(offset_x) > profile.width // 2 - inset_px_x:
        report.warning(
            "overlay_outside_safe_area",
            f"Overlay {overlay_id!r} has a horizontal offset of {offset_x}px, which can "
            f"push it outside the {timeline.safe_area_inset:.0%} safe area.",
            location="timeline.json",
        )
    if abs(offset_y) > profile.height // 2 - inset_px_y:
        report.warning(
            "overlay_outside_safe_area",
            f"Overlay {overlay_id!r} has a vertical offset of {offset_y}px, which can "
            f"push it outside the {timeline.safe_area_inset:.0%} safe area.",
            location="timeline.json",
        )


def _check_audio(
    bundle: ProjectBundle,
    timeline: CompositionTimeline,
    profile: RenderProfile,
    assets: dict[str, MediaAsset],
    report: ValidationReport,
) -> None:
    sample_rate = profile.audio_sample_rate

    for segment in timeline.audio.segments:
        location = f"timeline.json:{segment.segment_id}"
        asset = assets.get(segment.asset_id)

        if asset is None:
            report.error(
                "invalid_audio_asset_reference",
                f"Audio segment {segment.segment_id!r} references asset "
                f"{segment.asset_id!r}, which is not in the asset collection.",
                location=location,
            )
            continue

        if asset.asset_type is not AssetType.AUDIO:
            if not (asset.asset_type is AssetType.VIDEO and segment.role is AudioRole.SOURCE):
                report.error(
                    "audio_segment_not_audio",
                    f"Audio segment {segment.segment_id!r} references "
                    f"{asset.asset_type.value} asset {asset.asset_id}.",
                    location=location,
                )
                continue

        if not bundle.asset_path(asset.path).is_file():
            report.error(
                "missing_asset_file",
                f"Audio segment {segment.segment_id!r} needs {asset.path!r}, but no "
                f"file exists there.",
                location=location,
            )
            continue

        if asset.duration_seconds is None:
            report.error(
                "audio_duration_unknown",
                f"Audio asset {asset.asset_id} has no recorded duration, so segment "
                f"{segment.segment_id!r} cannot be checked.",
                location=location,
            )
            continue

        available = int(asset.duration_seconds * sample_rate) - segment.source_start_sample
        needed = segment.span.duration_samples
        if available < needed:
            shortfall_seconds = (needed - available) / sample_rate
            if segment.allow_silence_padding:
                report.warning(
                    "audio_padded_with_silence",
                    f"Audio segment {segment.segment_id!r} is {shortfall_seconds:.3f}s "
                    f"longer than the material available from {asset.asset_id}; silence "
                    f"padding was explicitly permitted.",
                    location=location,
                )
            else:
                report.error(
                    "audio_source_too_short",
                    f"Audio segment {segment.segment_id!r} needs {needed} samples from "
                    f"{asset.asset_id} but only {max(0, available)} are available "
                    f"({shortfall_seconds:.3f}s short). Audio is never silently "
                    f"extended; shorten the segment or set allow_silence_padding.",
                    location=location,
                )

    total_samples = timeline.total_samples
    narration = timeline.audio.segments_for(AudioRole.NARRATION)
    if narration and total_samples:
        covered = sum(segment.span.duration_samples for segment in narration)
        if covered > total_samples:
            report.error(
                "narration_exceeds_video",
                f"Narration totals {covered / sample_rate:.3f}s but the video is only "
                f"{total_samples / sample_rate:.3f}s.",
                location="timeline.json",
            )

    if timeline.audio.ducking.enabled and not narration:
        report.warning(
            "ducking_without_narration",
            "Music ducking is enabled but the timeline has no narration segments, so "
            "nothing will duck.",
            location="timeline.json",
        )


def _check_captions(
    bundle: ProjectBundle,
    timeline: CompositionTimeline,
    profile: RenderProfile,
    report: ValidationReport,
) -> None:
    settings = timeline.captions
    if settings.srt_path is None:
        if settings.burn_in:
            report.error(
                "captions_burn_in_without_source",
                "Caption burn-in is enabled but no srt_path is set, so there are no "
                "captions to burn in.",
                location="timeline.json",
            )
        return

    try:
        path = bundle.path_for(settings.srt_path)
    except Exception as exc:  # reported as a finding, never swallowed
        report.error(
            "unsafe_caption_path",
            f"Caption path {settings.srt_path!r} is not a safe project-relative path: {exc}",
            location="timeline.json",
        )
        return

    if not path.is_file():
        report.error(
            "missing_caption_file",
            f"The timeline references captions at {settings.srt_path!r}, but no file exists there.",
            location="timeline.json",
        )
        return

    # Parsing and timing checks live in the captions module; import locally to
    # keep this module's import graph shallow.
    from polymation.captions.srt import CaptionParseError, parse_srt, validate_cues

    try:
        cues = parse_srt(path.read_text(encoding="utf-8"), fps=profile.fps)
    except CaptionParseError as exc:
        report.error(
            "caption_parse_failed",
            f"{settings.srt_path!r} is not valid SRT: {exc}",
            location=settings.srt_path,
        )
        return

    for finding in validate_cues(
        cues,
        total_frames=timeline.total_frames,
        fps=profile.fps,
        max_lines=settings.placement.max_lines,
    ):
        report.add(
            finding.severity,
            finding.code,
            finding.message,
            location=finding.location or settings.srt_path,
        )
