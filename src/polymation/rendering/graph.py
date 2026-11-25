"""Building FFmpeg filter graphs for scene composition.

Two invariants hold throughout this module, and the tests check both:

1. **No filesystem path ever enters the filter string.** Sources and overlay
   images are supplied as ``-i`` arguments, which are separate elements of the
   argument vector. The filter string contains only labels, numbers, arithmetic
   and fixed keywords.
2. **No operator-supplied text ever enters the filter string.** Text has
   already become pixels in :mod:`polymation.rendering.text`. Colours are
   resolved to ``0xRRGGBB`` literals.

Together those mean the filter string is generated entirely from values this
module computes, so there is no escaping problem to get wrong. Commas are
avoided inside expressions for the same reason: ``max(a,b)`` would need
escaping in a filter argument, so bounds are computed in Python instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from polymation.config.channel import ChannelBrand
from polymation.errors import RenderError
from polymation.rendering.colors import to_ffmpeg_color
from polymation.rendering.profiles import RenderProfile
from polymation.schemas.assets import AssetType, MediaAsset
from polymation.schemas.timeline import (
    CompositionShot,
    ScalingQuality,
    ShortSourcePolicy,
    ShotFitMode,
    TransitionKind,
)

#: FFmpeg scaler flag for each scaling quality. ``neighbor`` keeps pixel art
#: crisp; the smooth option comes from the profile.
_PIXEL_SCALE_FLAGS = "neighbor"


@dataclass
class FFmpegPlan:
    """A complete, inspectable FFmpeg invocation.

    Kept as data rather than executed immediately so tests can assert on the
    graph without running an encoder, and so the plan can be hashed into a
    cache key.
    """

    #: Input arguments, in order. Each inner list is one input's arguments.
    inputs: list[list[str]] = field(default_factory=list)
    #: Filter chains, joined with ';' when rendered.
    chains: list[str] = field(default_factory=list)
    #: Output stream labels to map, e.g. ``["[vout]"]``.
    maps: list[str] = field(default_factory=list)

    def add_input(self, arguments: list[str]) -> int:
        """Register an input and return its index."""
        self.inputs.append(arguments)
        return len(self.inputs) - 1

    @property
    def input_args(self) -> list[str]:
        """All input arguments flattened for the command line."""
        return [argument for group in self.inputs for argument in group]

    @property
    def filter_complex(self) -> str:
        """The chains joined into a single ``-filter_complex`` value."""
        return ";".join(self.chains)

    def leaked_paths(self) -> list[str]:
        """Any input path that also appears inside the filter string.

        A standing guard on invariant 1 above: sources must reach FFmpeg only as
        ``-i`` arguments. Checking for a literal ``/`` would not work, because
        legitimate expressions divide (``(ow-iw)/2``, ``iw/zoom``); this checks
        for the actual path values instead, which is exact.
        """
        graph = self.filter_complex
        leaked: list[str] = []
        for group in self.inputs:
            for index, argument in enumerate(group):
                if index > 0 and group[index - 1] == "-i" and argument in graph:
                    leaked.append(argument)
        return leaked


def _number(value: float) -> str:
    """Format a number for a filter expression, without a comma or exponent."""
    if value == int(value):
        return str(int(value))
    return f"{value:.6f}".rstrip("0").rstrip(".")


def scale_flags_for(shot: CompositionShot, profile: RenderProfile) -> str:
    """Resampling kernel for ``shot``."""
    if shot.scaling is ScalingQuality.PIXEL:
        return _PIXEL_SCALE_FLAGS
    return profile.scale_flags


def fit_filters(
    shot: CompositionShot,
    profile: RenderProfile,
    brand: ChannelBrand,
    *,
    target_width: int | None = None,
    target_height: int | None = None,
) -> list[str]:
    """Filters that fit a source into the target box without distorting it.

    ``contain`` scales down to fit and pads the remainder with the shot's
    background colour; ``cover`` scales up to fill and crops the overflow;
    ``crop`` takes the shot's explicit rectangle first and then covers. No path
    through here uses an unconstrained ``scale=W:H``, which is the only way to
    stretch — a test asserts that.
    """
    width = target_width if target_width is not None else profile.width
    height = target_height if target_height is not None else profile.height
    flags = scale_flags_for(shot, profile)
    filters: list[str] = []

    if shot.fit_mode is ShotFitMode.CROP:
        if shot.crop is None:  # pragma: no cover - schema guarantees this
            raise RenderError(f"shot {shot.shot_id!r} is fit_mode 'crop' with no rectangle")
        crop = shot.crop
        # Crop in normalised source coordinates, so the same rectangle survives
        # the source being re-exported at another resolution.
        filters.append(
            f"crop=iw*{_number(crop.width)}:ih*{_number(crop.height)}"
            f":iw*{_number(crop.x)}:ih*{_number(crop.y)}"
        )
        filters.append(f"scale={width}:{height}:force_original_aspect_ratio=increase:flags={flags}")
        filters.append(f"crop={width}:{height}")
    elif shot.fit_mode is ShotFitMode.COVER:
        filters.append(f"scale={width}:{height}:force_original_aspect_ratio=increase:flags={flags}")
        filters.append(f"crop={width}:{height}")
    else:
        filters.append(f"scale={width}:{height}:force_original_aspect_ratio=decrease:flags={flags}")
        colour = to_ffmpeg_color(shot.background_color, brand)
        filters.append(f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color={colour}")

    filters.append("setsar=1")
    return filters


def motion_filter(shot: CompositionShot, profile: RenderProfile, frames: int) -> str | None:
    """A ``zoompan`` filter implementing the shot's motion, or ``None``.

    Motion operates on a canvas pre-scaled by the maximum zoom, so at full zoom
    the crop window is at native canvas resolution rather than an upscale of the
    output. The window is ``canvas / z``, and because ``z >= 1`` and the centre
    is schema-constrained to keep the window inside the canvas, motion cannot
    reveal undefined pixels.

    Expressions contain only numbers, arithmetic and the ``on``/``iw``/``ih``/
    ``zoom`` variables — no commas, so no filter-argument escaping is involved.
    """
    motion = shot.motion
    if motion.is_static:
        return None
    if frames < 1:  # pragma: no cover - schema guarantees this
        raise RenderError(f"shot {shot.shot_id!r} has no frames to animate")

    last = max(1, frames - 1)
    z0, z1 = motion.start_zoom, motion.end_zoom
    cx0, cx1 = motion.start_center_x, motion.end_center_x
    cy0, cy1 = motion.start_center_y, motion.end_center_y

    zoom_expression = _number(z0) if z0 == z1 else f"{_number(z0)}+({_number(z1 - z0)})*on/{last}"
    centre_x = _number(cx0) if cx0 == cx1 else f"({_number(cx0)}+({_number(cx1 - cx0)})*on/{last})"
    centre_y = _number(cy0) if cy0 == cy1 else f"({_number(cy0)}+({_number(cy1 - cy0)})*on/{last})"

    # zoompan's x/y are the top-left of the crop window in input coordinates.
    x_expression = f"{centre_x}*iw-(iw/zoom)/2"
    y_expression = f"{centre_y}*ih-(ih/zoom)/2"

    return (
        f"zoompan=z='{zoom_expression}':x='{x_expression}':y='{y_expression}'"
        f":d={frames}:s={profile.width}x{profile.height}:fps={profile.fps}"
    )


def motion_canvas_size(shot: CompositionShot, profile: RenderProfile) -> tuple[int, int]:
    """Canvas dimensions used when a shot has motion.

    Scaled by the maximum zoom and rounded to even numbers, which H.264's
    yuv420p chroma subsampling requires.
    """
    max_zoom = max(shot.motion.start_zoom, shot.motion.end_zoom)
    width = round(profile.width * max_zoom / 2) * 2
    height = round(profile.height * max_zoom / 2) * 2
    return width, height


def shot_input_arguments(
    shot: CompositionShot,
    asset: MediaAsset,
    source_path: Path,
    profile: RenderProfile,
    frames: int,
) -> list[str]:
    """Input arguments for one shot's source.

    The path is one element of the returned list and never reaches a filter
    string.
    """
    duration = frames / profile.fps

    if asset.asset_type is AssetType.IMAGE:
        if shot.motion.is_static:
            # A looped still gives the filter graph the frames it needs directly.
            return ["-loop", "1", "-t", f"{duration:.6f}", "-i", str(source_path)]
        # With motion, feed exactly one frame; zoompan generates `d` frames.
        return ["-i", str(source_path)]

    if asset.asset_type is AssetType.VIDEO:
        arguments: list[str] = []
        trim_start = shot.source_trim.start_frame if shot.source_trim else 0
        if trim_start:
            arguments += ["-ss", f"{trim_start / profile.fps:.6f}"]
        arguments += ["-t", f"{duration:.6f}", "-i", str(source_path)]
        return arguments

    raise RenderError(
        f"shot {shot.shot_id!r} references {asset.asset_type.value} asset "
        f"{asset.asset_id}; a visual shot needs an image or video"
    )


def shot_video_chain(
    shot: CompositionShot,
    asset: MediaAsset,
    profile: RenderProfile,
    brand: ChannelBrand,
    *,
    input_index: int,
    output_label: str,
    frames: int,
    source_frames_available: int | None,
) -> str:
    """The complete filter chain turning one input into a finished shot stream."""
    filters: list[str] = []
    has_motion = not shot.motion.is_static

    if has_motion:
        canvas_width, canvas_height = motion_canvas_size(shot, profile)
        filters += fit_filters(
            shot, profile, brand, target_width=canvas_width, target_height=canvas_height
        )
        motion = motion_filter(shot, profile, frames)
        if motion is not None:
            filters.append(motion)
    else:
        filters += fit_filters(shot, profile, brand)

    if asset.asset_type is AssetType.VIDEO:
        # Normalise an arbitrary source frame rate onto the output grid.
        filters.append(f"fps={profile.fps}")

        if source_frames_available is not None and source_frames_available < frames:
            filters.append(_short_source_filter(shot, profile, brand, frames))

    elif not has_motion:
        filters.append(f"fps={profile.fps}")

    filters += _transition_filters(shot, profile, frames)
    filters.append("format=yuv420p")

    chain = ",".join(filters)
    return f"[{input_index}:v]{chain}[{output_label}]"


def _short_source_filter(
    shot: CompositionShot,
    profile: RenderProfile,
    brand: ChannelBrand,
    frames: int,
) -> str:
    """Filter implementing the shot's explicit short-source policy."""
    duration = frames / profile.fps

    if shot.short_source_policy is ShortSourcePolicy.HOLD_LAST_FRAME:
        # clone repeats the final decoded frame; stop_duration bounds it.
        return f"tpad=stop_mode=clone:stop_duration={duration:.6f}"

    if shot.short_source_policy is ShortSourcePolicy.PAD_WITH_BACKGROUND:
        colour = to_ffmpeg_color(shot.background_color, brand)
        return f"tpad=stop_mode=add:stop_duration={duration:.6f}:color={colour}"

    raise RenderError(
        f"shot {shot.shot_id!r} has a source shorter than its {frames} frames and "
        f"policy {shot.short_source_policy.value!r}. Validation should have rejected "
        f"this before rendering."
    )


def _transition_filters(shot: CompositionShot, profile: RenderProfile, frames: int) -> list[str]:
    """Per-shot fade filters. Crossfades are handled when shots are joined."""
    transition = shot.transition_in
    if transition.kind in {TransitionKind.CUT, TransitionKind.CROSSFADE}:
        return []

    duration = transition.duration_frames / profile.fps
    if transition.kind is TransitionKind.FADE_FROM_BLACK:
        return [f"fade=t=in:st=0:d={duration:.6f}"]
    if transition.kind is TransitionKind.FADE_TO_BLACK:
        start = (frames - transition.duration_frames) / profile.fps
        return [f"fade=t=out:st={start:.6f}:d={duration:.6f}"]
    return []


def join_shot_chains(
    labels: list[str],
    shots: list[CompositionShot],
    profile: RenderProfile,
    *,
    output_label: str,
) -> list[str]:
    """Chains that join finished shot streams into one scene stream.

    Consecutive shots are concatenated, except where the incoming shot requests
    a crossfade, in which case ``xfade`` overlaps them. The overlap is already
    reflected in the shots' spans, so the resulting stream length equals the
    scene's declared duration — the schema enforces that tiling.
    """
    if not labels:  # pragma: no cover - callers guarantee at least one shot
        raise RenderError("cannot join zero shots")

    if len(labels) == 1:
        return [f"[{labels[0]}]null[{output_label}]"]

    chains: list[str] = []
    current = labels[0]
    # Timeline position of the end of the material accumulated so far.
    elapsed_frames = shots[0].span.duration_frames

    for index in range(1, len(labels)):
        shot = shots[index]
        incoming = labels[index]
        merged = output_label if index == len(labels) - 1 else f"{output_label}_j{index}"

        if shot.transition_in.kind is TransitionKind.CROSSFADE:
            overlap = shot.transition_in.duration_frames
            offset_frames = elapsed_frames - overlap
            chains.append(
                f"[{current}][{incoming}]xfade=transition=fade"
                f":duration={overlap / profile.fps:.6f}"
                f":offset={offset_frames / profile.fps:.6f}[{merged}]"
            )
            elapsed_frames = offset_frames + shot.span.duration_frames
        else:
            chains.append(f"[{current}][{incoming}]concat=n=2:v=1:a=0[{merged}]")
            elapsed_frames += shot.span.duration_frames

        current = merged

    return chains


def overlay_chains(
    overlay_inputs: list[tuple[int, int, int, int, int, int, float]],
    profile: RenderProfile,
    *,
    base_label: str,
    output_label: str,
) -> list[str]:
    """Chains compositing pre-rendered overlay images onto a base stream.

    Each tuple is ``(input_index, x, y, start_frame, end_frame, _reserved,
    opacity)``. Enabling uses frame numbers via ``between(n, a, b)``, so timing
    is exact on the frame grid rather than approximated in seconds.
    """
    if not overlay_inputs:
        return [f"[{base_label}]null[{output_label}]"]

    chains: list[str] = []
    current = base_label

    for position, (
        input_index,
        x_position,
        y_position,
        start_frame,
        end_frame,
        _reserved,
        opacity,
    ) in enumerate(overlay_inputs):
        source_label = f"{output_label}_ov{position}"
        if opacity < 1.0:
            chains.append(
                f"[{input_index}:v]format=rgba,"
                f"colorchannelmixer=aa={_number(opacity)}[{source_label}]"
            )
        else:
            chains.append(f"[{input_index}:v]format=rgba[{source_label}]")

        merged = (
            output_label if position == len(overlay_inputs) - 1 else f"{output_label}_m{position}"
        )
        chains.append(
            f"[{current}][{source_label}]overlay={x_position}:{y_position}"
            f":enable='between(n\\,{start_frame}\\,{end_frame - 1})'"
            f":format=auto[{merged}]"
        )
        current = merged

    return chains
