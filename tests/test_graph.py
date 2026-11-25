"""Filter-graph construction: no distortion, no path leakage, exact timing.

These tests assert on the generated graph rather than running an encoder, so
they are fast and they pin the two safety invariants directly.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from polymation.config.channel import ChannelConfig
from polymation.rendering.graph import (
    FFmpegPlan,
    fit_filters,
    join_shot_chains,
    motion_canvas_size,
    motion_filter,
    overlay_chains,
    scale_flags_for,
    shot_input_arguments,
    shot_video_chain,
)
from polymation.rendering.profiles import FINAL_PROFILE, PREVIEW_PROFILE
from polymation.schemas.assets import AssetType, MediaAsset
from polymation.schemas.base import utc_now
from polymation.schemas.timeline import (
    CompositionShot,
    CropRect,
    Motion,
    MotionKind,
    ScalingQuality,
    ShortSourcePolicy,
    ShotFitMode,
    SourceTrim,
    Transition,
    TransitionKind,
)
from polymation.schemas.timing import FrameSpan

NOW = utc_now()


def make_shot(**overrides: object) -> CompositionShot:
    payload: dict[str, object] = {
        "shot_id": "shot-01",
        "asset_id": "asset-0001",
        "span": FrameSpan(start_frame=0, duration_frames=60),
    }
    payload.update(overrides)
    return CompositionShot(**payload)  # type: ignore[arg-type]


def make_asset(asset_type: AssetType = AssetType.IMAGE, **overrides: object) -> MediaAsset:
    payload: dict[str, object] = {
        "asset_id": "asset-0001",
        "asset_type": asset_type,
        "category": ("images" if asset_type is AssetType.IMAGE else "footage"),
        "path": "assets/images/a.png",
        "detected_format": "PNG",
        "size_bytes": 100,
        "sha256": "a" * 64,
        "original_source": "generated",
        "acquired_at": NOW,
        "rights_record_id": "rights-0001",
    }
    if asset_type is AssetType.IMAGE:
        payload.update({"width": 640, "height": 480})
    else:
        payload.update({"width": 640, "height": 480, "duration_seconds": 12.0})
    payload.update(overrides)
    return MediaAsset(**payload)  # type: ignore[arg-type]


# --- no distortion ------------------------------------------------------------


def unconstrained_scales(filters: list[str]) -> list[str]:
    """Scale filters that would stretch, i.e. lack an aspect-ratio constraint.

    Checked per filter token rather than with one regex over the whole chain:
    a lookahead on `scale=\\d+:\\d+` backtracks into the digits and silently
    passes, which is how an earlier version of this guard failed to guard.
    """
    return [
        item
        for item in filters
        if item.startswith("scale=") and "force_original_aspect_ratio" not in item
    ]


@pytest.mark.parametrize("mode", list(ShotFitMode))
def test_no_fit_mode_can_distort(mode: ShotFitMode, channel: ChannelConfig) -> None:
    crop = CropRect(x=0.1, y=0.1, width=0.8, height=0.8) if mode is ShotFitMode.CROP else None
    shot = make_shot(fit_mode=mode, crop=crop)
    filters = fit_filters(shot, FINAL_PROFILE, channel.brand)

    assert any("force_original_aspect_ratio" in item for item in filters)
    assert unconstrained_scales(filters) == [], filters
    assert "setsar=1" in filters


def test_the_distortion_guard_actually_detects_a_stretch() -> None:
    """A guard that cannot fail is not a guard."""
    assert unconstrained_scales(["scale=1920:1080", "setsar=1"]) == ["scale=1920:1080"]
    assert unconstrained_scales(["scale=1920:1080:force_original_aspect_ratio=decrease"]) == []


def test_contain_pads_with_the_background_colour(channel: ChannelConfig) -> None:
    shot = make_shot(fit_mode=ShotFitMode.CONTAIN, background_color="palette:deep_indigo")
    chain = ",".join(fit_filters(shot, FINAL_PROFILE, channel.brand))

    assert "force_original_aspect_ratio=decrease" in chain
    assert "pad=1920:1080:(ow-iw)/2:(oh-ih)/2" in chain
    assert "color=0x191D38" in chain
    assert "crop=" not in chain


def test_cover_crops_rather_than_padding(channel: ChannelConfig) -> None:
    chain = ",".join(
        fit_filters(make_shot(fit_mode=ShotFitMode.COVER), FINAL_PROFILE, channel.brand)
    )
    assert "force_original_aspect_ratio=increase" in chain
    assert "crop=1920:1080" in chain
    assert "pad=" not in chain


def test_explicit_crop_is_applied_in_source_coordinates(channel: ChannelConfig) -> None:
    shot = make_shot(
        fit_mode=ShotFitMode.CROP,
        crop=CropRect(x=0.25, y=0.1, width=0.5, height=0.8),
    )
    chain = ",".join(fit_filters(shot, FINAL_PROFILE, channel.brand))
    # Normalised, so the same rectangle survives a re-export at another size.
    assert "crop=iw*0.5:ih*0.8:iw*0.25:ih*0.1" in chain


def test_fit_targets_the_requested_box(channel: ChannelConfig) -> None:
    chain = ",".join(
        fit_filters(
            make_shot(), PREVIEW_PROFILE, channel.brand, target_width=480, target_height=270
        )
    )
    assert "scale=480:270" in chain
    assert "pad=480:270" in chain


# --- scaling quality ----------------------------------------------------------


def test_pixel_scaling_uses_nearest_neighbour() -> None:
    assert scale_flags_for(make_shot(scaling=ScalingQuality.PIXEL), FINAL_PROFILE) == "neighbor"


def test_smooth_scaling_uses_the_profile_kernel() -> None:
    assert scale_flags_for(make_shot(), FINAL_PROFILE) == FINAL_PROFILE.scale_flags
    assert scale_flags_for(make_shot(), PREVIEW_PROFILE) == PREVIEW_PROFILE.scale_flags


def test_the_two_profiles_use_different_kernels() -> None:
    """Preview trades quality for speed; final does the opposite."""
    assert FINAL_PROFILE.scale_flags != PREVIEW_PROFILE.scale_flags


# --- motion -------------------------------------------------------------------


def test_static_motion_emits_no_filter() -> None:
    assert motion_filter(make_shot(), FINAL_PROFILE, 60) is None


def test_zoom_motion_interpolates_over_the_shot() -> None:
    shot = make_shot(motion=Motion(kind=MotionKind.ZOOM_IN, start_zoom=1.0, end_zoom=2.0))
    chain = motion_filter(shot, FINAL_PROFILE, 61)
    assert chain is not None
    # 60 = frames - 1, so the final frame reaches exactly end_zoom.
    assert "z='1+(1)*on/60'" in chain
    assert "d=61" in chain
    assert "s=1920x1080" in chain
    assert "fps=30" in chain


def test_motion_expressions_contain_no_commas() -> None:
    """A comma inside a filter argument needs escaping; avoiding them avoids that."""
    shot = make_shot(
        motion=Motion(
            kind=MotionKind.PAN_ZOOM,
            start_zoom=1.2,
            end_zoom=1.6,
            start_center_x=0.45,
            start_center_y=0.45,
            end_center_x=0.6,
            end_center_y=0.55,
        )
    )
    chain = motion_filter(shot, FINAL_PROFILE, 90)
    assert chain is not None
    expressions = re.findall(r"'([^']*)'", chain)
    assert expressions
    for expression in expressions:
        assert "," not in expression, expression


def test_motion_canvas_is_scaled_by_the_maximum_zoom() -> None:
    shot = make_shot(motion=Motion(kind=MotionKind.ZOOM_IN, start_zoom=1.0, end_zoom=1.5))
    width, height = motion_canvas_size(shot, FINAL_PROFILE)
    assert (width, height) == (2880, 1620)
    # Even dimensions, which yuv420p chroma subsampling requires.
    assert width % 2 == 0 and height % 2 == 0


def test_motion_canvas_dimensions_are_always_even() -> None:
    for zoom in (1.0, 1.01, 1.33, 1.77, 2.5):
        shot = make_shot(
            motion=(
                Motion()
                if zoom == 1.0
                else Motion(kind=MotionKind.ZOOM_IN, start_zoom=1.0, end_zoom=zoom)
            )
        )
        width, height = motion_canvas_size(shot, FINAL_PROFILE)
        assert width % 2 == 0 and height % 2 == 0, zoom


def test_a_single_frame_shot_with_motion_does_not_divide_by_zero() -> None:
    shot = make_shot(
        span=FrameSpan(start_frame=0, duration_frames=1),
        motion=Motion(kind=MotionKind.ZOOM_IN, start_zoom=1.0, end_zoom=1.2),
    )
    chain = motion_filter(shot, FINAL_PROFILE, 1)
    assert chain is not None
    assert "/0" not in chain


# --- inputs -------------------------------------------------------------------


def test_a_static_image_input_loops_for_the_shot_duration() -> None:
    arguments = shot_input_arguments(
        make_shot(), make_asset(), Path("/tmp/a.png"), FINAL_PROFILE, 60
    )
    assert arguments[:2] == ["-loop", "1"]
    assert "-t" in arguments
    assert arguments[arguments.index("-t") + 1] == "2.000000"


def test_an_image_with_motion_is_fed_a_single_frame() -> None:
    """zoompan generates the frames, so looping the input would multiply them."""
    shot = make_shot(motion=Motion(kind=MotionKind.ZOOM_IN, start_zoom=1.0, end_zoom=1.5))
    arguments = shot_input_arguments(shot, make_asset(), Path("/tmp/a.png"), FINAL_PROFILE, 60)
    assert "-loop" not in arguments
    assert arguments == ["-i", "/tmp/a.png"]


def test_a_video_trim_becomes_a_seek_before_the_input() -> None:
    shot = make_shot(source_trim=SourceTrim(start_frame=90))
    arguments = shot_input_arguments(
        shot, make_asset(AssetType.VIDEO), Path("/tmp/a.mp4"), FINAL_PROFILE, 60
    )
    # -ss before -i is the accurate fast-seek form.
    assert arguments[0] == "-ss"
    assert arguments[1] == "3.000000"
    assert arguments.index("-ss") < arguments.index("-i")


def test_an_untrimmed_video_has_no_seek() -> None:
    arguments = shot_input_arguments(
        make_shot(), make_asset(AssetType.VIDEO), Path("/tmp/a.mp4"), FINAL_PROFILE, 60
    )
    assert "-ss" not in arguments


# --- shot chains --------------------------------------------------------------


def test_a_video_shot_normalises_the_frame_rate(channel: ChannelConfig) -> None:
    chain = shot_video_chain(
        make_shot(),
        make_asset(AssetType.VIDEO),
        FINAL_PROFILE,
        channel.brand,
        input_index=0,
        output_label="sv0",
        frames=60,
        source_frames_available=360,
    )
    assert "fps=30" in chain
    assert chain.startswith("[0:v]")
    assert chain.endswith("[sv0]")
    assert "format=yuv420p" in chain


def test_a_short_source_with_hold_policy_clones_the_last_frame(
    channel: ChannelConfig,
) -> None:
    chain = shot_video_chain(
        make_shot(short_source_policy=ShortSourcePolicy.HOLD_LAST_FRAME),
        make_asset(AssetType.VIDEO),
        FINAL_PROFILE,
        channel.brand,
        input_index=0,
        output_label="sv0",
        frames=60,
        source_frames_available=30,
    )
    assert "tpad=stop_mode=clone" in chain


def test_a_short_source_with_background_policy_pads_with_colour(
    channel: ChannelConfig,
) -> None:
    chain = shot_video_chain(
        make_shot(
            short_source_policy=ShortSourcePolicy.PAD_WITH_BACKGROUND,
            background_color="#000000",
        ),
        make_asset(AssetType.VIDEO),
        FINAL_PROFILE,
        channel.brand,
        input_index=0,
        output_label="sv0",
        frames=60,
        source_frames_available=30,
    )
    assert "tpad=stop_mode=add" in chain
    assert "color=0x000000" in chain


def test_a_short_source_with_the_default_policy_refuses_to_render(
    channel: ChannelConfig,
) -> None:
    from polymation.errors import RenderError

    with pytest.raises(RenderError, match="Validation should have rejected"):
        shot_video_chain(
            make_shot(),
            make_asset(AssetType.VIDEO),
            FINAL_PROFILE,
            channel.brand,
            input_index=0,
            output_label="sv0",
            frames=60,
            source_frames_available=30,
        )


def test_a_sufficient_source_adds_no_padding(channel: ChannelConfig) -> None:
    chain = shot_video_chain(
        make_shot(),
        make_asset(AssetType.VIDEO),
        FINAL_PROFILE,
        channel.brand,
        input_index=0,
        output_label="sv0",
        frames=60,
        source_frames_available=60,
    )
    assert "tpad" not in chain


# --- transitions --------------------------------------------------------------


def test_fade_from_black_fades_in_at_the_start(channel: ChannelConfig) -> None:
    chain = shot_video_chain(
        make_shot(
            transition_in=Transition(kind=TransitionKind.FADE_FROM_BLACK, duration_frames=15)
        ),
        make_asset(),
        FINAL_PROFILE,
        channel.brand,
        input_index=0,
        output_label="sv0",
        frames=60,
        source_frames_available=None,
    )
    assert "fade=t=in:st=0:d=0.500000" in chain


def test_fade_to_black_fades_out_at_the_end(channel: ChannelConfig) -> None:
    chain = shot_video_chain(
        make_shot(transition_in=Transition(kind=TransitionKind.FADE_TO_BLACK, duration_frames=30)),
        make_asset(),
        FINAL_PROFILE,
        channel.brand,
        input_index=0,
        output_label="sv0",
        frames=90,
        source_frames_available=None,
    )
    # Fade starts at (90 - 30)/30 = 2.0s and lasts 1.0s, so it ends exactly at
    # the shot's end: a fade never changes the timeline duration.
    assert "fade=t=out:st=2.000000:d=1.000000" in chain


def test_a_cut_adds_no_fade(channel: ChannelConfig) -> None:
    chain = shot_video_chain(
        make_shot(),
        make_asset(),
        FINAL_PROFILE,
        channel.brand,
        input_index=0,
        output_label="sv0",
        frames=60,
        source_frames_available=None,
    )
    assert "fade=" not in chain


# --- joining shots ------------------------------------------------------------


def test_a_single_shot_is_passed_through() -> None:
    chains = join_shot_chains(["sv0"], [make_shot()], FINAL_PROFILE, output_label="out")
    assert chains == ["[sv0]null[out]"]


def test_consecutive_shots_are_concatenated() -> None:
    shots = [
        make_shot(shot_id="shot-01", span=FrameSpan(start_frame=0, duration_frames=60)),
        make_shot(shot_id="shot-02", span=FrameSpan(start_frame=60, duration_frames=60)),
    ]
    chains = join_shot_chains(["sv0", "sv1"], shots, FINAL_PROFILE, output_label="out")
    assert any("concat=n=2:v=1:a=0[out]" in chain for chain in chains)
    assert all("xfade" not in chain for chain in chains)


def test_a_crossfade_uses_xfade_with_the_right_offset() -> None:
    shots = [
        make_shot(shot_id="shot-01", span=FrameSpan(start_frame=0, duration_frames=60)),
        make_shot(
            shot_id="shot-02",
            span=FrameSpan(start_frame=50, duration_frames=60),
            transition_in=Transition(kind=TransitionKind.CROSSFADE, duration_frames=10),
        ),
    ]
    chains = join_shot_chains(["sv0", "sv1"], shots, FINAL_PROFILE, output_label="out")
    joined = ";".join(chains)
    assert "xfade=transition=fade" in joined
    assert "duration=0.333333" in joined
    # The crossfade begins 10 frames before the first shot ends: 50/30s.
    assert "offset=1.666667" in joined


def test_three_shots_chain_through_intermediate_labels() -> None:
    shots = [
        make_shot(
            shot_id=f"shot-{index:02d}", span=FrameSpan(start_frame=index * 60, duration_frames=60)
        )
        for index in range(3)
    ]
    chains = join_shot_chains(["a", "b", "c"], shots, FINAL_PROFILE, output_label="out")
    assert len(chains) == 2
    assert chains[-1].endswith("[out]")


# --- overlays -----------------------------------------------------------------


def test_no_overlays_passes_the_base_through() -> None:
    assert overlay_chains([], FINAL_PROFILE, base_label="v", output_label="out") == ["[v]null[out]"]


def test_overlay_timing_uses_frame_numbers() -> None:
    chains = overlay_chains(
        [(1, 100, 200, 30, 90, 0, 1.0)],
        FINAL_PROFILE,
        base_label="v",
        output_label="out",
    )
    joined = ";".join(chains)
    assert "overlay=100:200" in joined
    # end_frame is exclusive, so `between` ends at end-1.
    assert "between(n\\,30\\,89)" in joined
    assert joined.endswith("[out]")


def test_a_translucent_overlay_adjusts_its_alpha() -> None:
    joined = ";".join(
        overlay_chains(
            [(1, 0, 0, 0, 10, 0, 0.5)], FINAL_PROFILE, base_label="v", output_label="out"
        )
    )
    assert "colorchannelmixer=aa=0.5" in joined


def test_an_opaque_overlay_skips_the_alpha_filter() -> None:
    joined = ";".join(
        overlay_chains(
            [(1, 0, 0, 0, 10, 0, 1.0)], FINAL_PROFILE, base_label="v", output_label="out"
        )
    )
    assert "colorchannelmixer" not in joined


def test_multiple_overlays_composite_in_order() -> None:
    chains = overlay_chains(
        [(1, 0, 0, 0, 10, 0, 1.0), (2, 5, 5, 10, 20, 0, 1.0)],
        FINAL_PROFILE,
        base_label="v",
        output_label="out",
    )
    assert chains[-1].endswith("[out]")
    assert len(chains) == 4  # two prepare chains plus two overlay chains


# --- the path-leakage invariant ----------------------------------------------


def test_no_input_path_appears_in_the_filter_string(channel: ChannelConfig) -> None:
    """Sources must reach FFmpeg only as -i arguments, never inside a filter."""
    plan = FFmpegPlan()
    hostile = Path("/tmp/a'b,c:d%e.png")
    index = plan.add_input(["-loop", "1", "-t", "2", "-i", str(hostile)])
    plan.chains.append(
        shot_video_chain(
            make_shot(),
            make_asset(),
            FINAL_PROFILE,
            channel.brand,
            input_index=index,
            output_label="sv0",
            frames=60,
            source_frames_available=None,
        )
    )
    overlay_index = plan.add_input(["-i", "/tmp/overlay'x,y.png"])
    plan.chains.extend(
        overlay_chains(
            [(overlay_index, 0, 0, 0, 10, 0, 1.0)],
            FINAL_PROFILE,
            base_label="sv0",
            output_label="out",
        )
    )

    assert plan.leaked_paths() == []


def test_the_leak_detector_actually_detects_a_leak() -> None:
    """A guard that cannot fail is not a guard."""
    plan = FFmpegPlan()
    plan.add_input(["-i", "/tmp/leaky.png"])
    plan.chains.append("[0:v]movie=/tmp/leaky.png[out]")
    assert plan.leaked_paths() == ["/tmp/leaky.png"]


def test_plan_renders_inputs_and_filters_separately() -> None:
    plan = FFmpegPlan()
    plan.add_input(["-i", "a.png"])
    plan.add_input(["-i", "b.png"])
    plan.chains.extend(["[0:v]null[x]", "[1:v]null[y]"])
    assert plan.input_args == ["-i", "a.png", "-i", "b.png"]
    assert plan.filter_complex == "[0:v]null[x];[1:v]null[y]"
