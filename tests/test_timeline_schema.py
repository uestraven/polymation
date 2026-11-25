"""Composition timeline schema: tiling, motion bounds, overlays, audio rules."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from polymation.schemas.base import utc_now
from polymation.schemas.timeline import (
    Anchor,
    AudioMix,
    AudioRole,
    AudioSegment,
    CompositionShot,
    CompositionTimeline,
    CropRect,
    Ducking,
    Motion,
    MotionKind,
    Overlay,
    OverlayKind,
    ShotFitMode,
    TextStyle,
    TimelineScene,
    Transition,
    TransitionKind,
    validate_color_spec,
)
from polymation.schemas.timing import FrameSpan, SampleSpan

NOW = utc_now()


def shot(
    shot_id: str = "shot-01",
    start: int = 0,
    frames: int = 60,
    **overrides: object,
) -> CompositionShot:
    """A minimal valid shot."""
    payload: dict[str, object] = {
        "shot_id": shot_id,
        "asset_id": "asset-0001",
        "span": FrameSpan(start_frame=start, duration_frames=frames),
    }
    payload.update(overrides)
    return CompositionShot(**payload)  # type: ignore[arg-type]


def scene(scene_id: str = "scene-01", order: int = 0, **overrides: object) -> TimelineScene:
    """A minimal valid scene."""
    payload: dict[str, object] = {
        "scene_id": scene_id,
        "order": order,
        "shots": [shot()],
    }
    payload.update(overrides)
    return TimelineScene(**payload)  # type: ignore[arg-type]


def timeline(**overrides: object) -> CompositionTimeline:
    """A minimal valid timeline."""
    payload: dict[str, object] = {
        "project_id": "p",
        "scenes": [scene()],
        "created_at": NOW,
        "updated_at": NOW,
    }
    payload.update(overrides)
    return CompositionTimeline(**payload)  # type: ignore[arg-type]


# --- colours ------------------------------------------------------------------


@pytest.mark.parametrize("value", ["#191D38", "#abcdef", "palette:deep_indigo", "palette:x1"])
def test_valid_colour_specs(value: str) -> None:
    assert validate_color_spec(value)


@pytest.mark.parametrize(
    "value",
    ["191D38", "#19D", "#1234567", "palette:", "palette:Upper", "red", "", "palette:a-b"],
)
def test_invalid_colour_specs(value: str) -> None:
    with pytest.raises(ValueError, match="must be"):
        validate_color_spec(value)


def test_hex_colours_are_normalised_to_uppercase() -> None:
    assert validate_color_spec("#abcdef") == "#ABCDEF"


# --- shot tiling --------------------------------------------------------------


def test_shots_must_start_at_frame_zero() -> None:
    with pytest.raises(ValidationError, match="must start at 0"):
        scene(shots=[shot(start=5, frames=60)])


def test_shots_must_tile_without_a_gap() -> None:
    with pytest.raises(ValidationError, match="without gaps"):
        scene(
            shots=[
                shot("shot-01", 0, 60),
                shot("shot-02", 70, 60),  # 10-frame gap
            ]
        )


def test_shots_must_not_overlap_without_a_crossfade() -> None:
    with pytest.raises(ValidationError, match="without gaps"):
        scene(shots=[shot("shot-01", 0, 60), shot("shot-02", 50, 60)])


def test_a_crossfade_accounts_for_its_overlap() -> None:
    built = scene(
        shots=[
            shot("shot-01", 0, 60),
            shot(
                "shot-02",
                50,
                60,
                transition_in=Transition(kind=TransitionKind.CROSSFADE, duration_frames=10),
            ),
        ]
    )
    # 60 + 60 - 10 overlap = 110.
    assert built.duration_frames == 110


def test_duplicate_shot_ids_within_a_scene_are_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate shot_id"):
        scene(shots=[shot("shot-01", 0, 60), shot("shot-01", 60, 60)])


def test_duplicate_shot_ids_across_scenes_are_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate shot_id"):
        timeline(
            scenes=[
                scene("scene-01", 0, shots=[shot("shot-01")]),
                scene("scene-02", 1, shots=[shot("shot-01")]),
            ]
        )


def test_a_scene_needs_at_least_one_shot() -> None:
    with pytest.raises(ValidationError):
        scene(shots=[])


def test_shots_are_returned_in_scene_relative_order() -> None:
    built = scene(shots=[shot("shot-02", 60, 30), shot("shot-01", 0, 60)])
    assert [item.shot_id for item in built.shots_in_order] == ["shot-01", "shot-02"]
    assert built.duration_frames == 90


# --- fit modes and crop -------------------------------------------------------


def test_crop_mode_requires_a_rectangle() -> None:
    with pytest.raises(ValidationError, match="supplies no crop rectangle"):
        shot(fit_mode=ShotFitMode.CROP)


def test_a_rectangle_requires_crop_mode() -> None:
    with pytest.raises(ValidationError, match="Set fit_mode to 'crop'"):
        shot(crop=CropRect(x=0.1, y=0.1, width=0.5, height=0.5))


def test_crop_must_stay_inside_the_source() -> None:
    with pytest.raises(ValidationError, match="past the right edge"):
        CropRect(x=0.6, y=0.0, width=0.5, height=0.5)
    with pytest.raises(ValidationError, match="past the bottom edge"):
        CropRect(x=0.0, y=0.6, width=0.5, height=0.5)


def test_a_full_frame_crop_is_valid() -> None:
    assert CropRect(x=0.0, y=0.0, width=1.0, height=1.0).width == 1.0


def test_a_zero_sized_crop_is_rejected() -> None:
    with pytest.raises(ValidationError):
        CropRect(x=0.0, y=0.0, width=0.0, height=0.5)


# --- motion -------------------------------------------------------------------


def test_static_motion_must_not_move_or_zoom() -> None:
    with pytest.raises(ValidationError, match=r"must leave both zoom values at 1\.0"):
        Motion(kind=MotionKind.STATIC, end_zoom=1.5)
    with pytest.raises(ValidationError, match="must not move the centre"):
        Motion(kind=MotionKind.STATIC, end_center_x=0.6)


def test_zoom_in_requires_increasing_zoom() -> None:
    with pytest.raises(ValidationError, match="requires end_zoom > start_zoom"):
        Motion(kind=MotionKind.ZOOM_IN, start_zoom=1.5, end_zoom=1.2)


def test_zoom_out_requires_decreasing_zoom() -> None:
    with pytest.raises(ValidationError, match="requires end_zoom < start_zoom"):
        Motion(kind=MotionKind.ZOOM_OUT, start_zoom=1.2, end_zoom=1.5)


def test_pan_requires_the_centre_to_move() -> None:
    with pytest.raises(ValidationError, match="requires the centre to move"):
        Motion(kind=MotionKind.PAN, start_zoom=1.5, end_zoom=1.5)


def test_zoom_below_one_is_rejected() -> None:
    """Zooming out past 1.0 would reveal undefined pixels outside the image."""
    with pytest.raises(ValidationError):
        Motion(kind=MotionKind.ZOOM_OUT, start_zoom=1.0, end_zoom=0.8)


def test_a_centre_that_would_show_past_an_edge_is_rejected() -> None:
    # At zoom 1.25 the window is 0.8 of the source, so the centre must lie in
    # [0.4, 0.6]. The message must name that interval.
    with pytest.raises(ValidationError, match=r"\[0\.4000, 0\.6000\]"):
        Motion(
            kind=MotionKind.PAN,
            start_zoom=1.25,
            end_zoom=1.25,
            start_center_x=0.32,
            end_center_x=0.68,
        )


@pytest.mark.parametrize("zoom", [1.0, 1.25, 2.0, 4.0])
def test_the_centre_bound_matches_the_zoom(zoom: float) -> None:
    """The permitted interval is exactly [0.5/z, 1 - 0.5/z]."""
    half = 0.5 / zoom
    # The extremes are accepted.
    Motion(
        kind=MotionKind.ZOOM_IN if zoom > 1.0 else MotionKind.STATIC,
        start_zoom=zoom,
        end_zoom=zoom * 1.1 if zoom > 1.0 else 1.0,
        start_center_x=half,
        start_center_y=half,
        end_center_x=half,
        end_center_y=half,
    )
    if zoom > 1.0:
        with pytest.raises(ValidationError, match="past the image edge"):
            Motion(
                kind=MotionKind.ZOOM_IN,
                start_zoom=zoom,
                end_zoom=zoom * 1.1,
                start_center_x=half - 0.01,
            )


def test_static_is_the_default() -> None:
    assert shot().motion.is_static


# --- transitions --------------------------------------------------------------


def test_a_cut_has_no_duration() -> None:
    with pytest.raises(ValidationError, match="must have duration_frames = 0"):
        Transition(kind=TransitionKind.CUT, duration_frames=5)


def test_a_fade_needs_a_duration() -> None:
    with pytest.raises(ValidationError, match="requires duration_frames >= 1"):
        Transition(kind=TransitionKind.FADE_TO_BLACK, duration_frames=0)


def test_a_crossfade_must_be_shorter_than_its_shot() -> None:
    with pytest.raises(ValidationError, match="not shorter than"):
        shot(
            frames=10,
            transition_in=Transition(kind=TransitionKind.CROSSFADE, duration_frames=10),
        )


def test_cross_scene_crossfade_is_rejected() -> None:
    """It would couple adjacent scenes and break per-scene caching."""
    with pytest.raises(ValidationError, match="may not be a crossfade"):
        scene(transition_in=Transition(kind=TransitionKind.CROSSFADE, duration_frames=10))


def test_a_scene_transition_cannot_exceed_the_scene() -> None:
    with pytest.raises(ValidationError, match="exceeds the scene"):
        scene(
            shots=[shot(frames=10)],
            transition_in=Transition(kind=TransitionKind.FADE_FROM_BLACK, duration_frames=20),
        )


def test_only_a_crossfade_consumes_timeline() -> None:
    assert Transition(kind=TransitionKind.CROSSFADE, duration_frames=5).consumes_timeline
    assert not Transition(kind=TransitionKind.FADE_TO_BLACK, duration_frames=5).consumes_timeline
    assert not Transition().consumes_timeline


# --- overlays -----------------------------------------------------------------


def test_a_text_overlay_requires_text() -> None:
    with pytest.raises(ValidationError, match="requires non-empty text"):
        Overlay(
            overlay_id="o1",
            kind=OverlayKind.TITLE,
            span=FrameSpan(start_frame=0, duration_frames=10),
        )


def test_a_text_overlay_rejects_whitespace_only_text() -> None:
    with pytest.raises(ValidationError, match="requires non-empty text"):
        Overlay(
            overlay_id="o1",
            kind=OverlayKind.TITLE,
            span=FrameSpan(start_frame=0, duration_frames=10),
            text="   ",
        )


def test_an_image_overlay_requires_an_asset() -> None:
    with pytest.raises(ValidationError, match="requires image_asset_id"):
        Overlay(
            overlay_id="o1",
            kind=OverlayKind.IMAGE,
            span=FrameSpan(start_frame=0, duration_frames=10),
        )


def test_a_text_overlay_rejects_an_image_asset() -> None:
    with pytest.raises(ValidationError, match="must not reference an image asset"):
        Overlay(
            overlay_id="o1",
            kind=OverlayKind.TITLE,
            span=FrameSpan(start_frame=0, duration_frames=10),
            text="hi",
            image_asset_id="asset-0001",
        )


def test_a_colour_panel_requires_fill_and_size() -> None:
    with pytest.raises(ValidationError, match="requires fill_color"):
        Overlay(
            overlay_id="o1",
            kind=OverlayKind.COLOR_PANEL,
            span=FrameSpan(start_frame=0, duration_frames=10),
        )
    with pytest.raises(ValidationError, match="width_fraction and height_fraction"):
        Overlay(
            overlay_id="o1",
            kind=OverlayKind.COLOR_PANEL,
            span=FrameSpan(start_frame=0, duration_frames=10),
            fill_color="#FF00FF",
        )


def test_overlay_fades_cannot_exceed_the_span() -> None:
    with pytest.raises(ValidationError, match="exceed its 10-frame span"):
        Overlay(
            overlay_id="o1",
            kind=OverlayKind.TITLE,
            span=FrameSpan(start_frame=0, duration_frames=10),
            text="hi",
            fade_in_frames=6,
            fade_out_frames=6,
        )


def test_a_shot_overlay_must_fit_its_shot() -> None:
    with pytest.raises(ValidationError, match="falls outside"):
        shot(
            frames=30,
            overlays=[
                Overlay(
                    overlay_id="o1",
                    kind=OverlayKind.TITLE,
                    span=FrameSpan(start_frame=20, duration_frames=20),
                    text="hi",
                )
            ],
        )


def test_a_scene_overlay_must_fit_its_scene() -> None:
    with pytest.raises(ValidationError, match="falls outside"):
        scene(
            shots=[shot(frames=30)],
            overlays=[
                Overlay(
                    overlay_id="o1",
                    kind=OverlayKind.TITLE,
                    span=FrameSpan(start_frame=0, duration_frames=40),
                    text="hi",
                )
            ],
        )


def test_shot_overlays_are_shifted_into_scene_time() -> None:
    built = scene(
        shots=[
            shot("shot-01", 0, 60),
            shot(
                "shot-02",
                60,
                60,
                overlays=[
                    Overlay(
                        overlay_id="o1",
                        kind=OverlayKind.TITLE,
                        span=FrameSpan(start_frame=10, duration_frames=20),
                        text="hi",
                    )
                ],
            ),
        ]
    )
    combined = built.all_overlays()
    assert len(combined) == 1
    # Shot-relative frame 10 in a shot starting at 60 is scene frame 70.
    assert combined[0].span.start_frame == 70


def test_a_text_style_background_needs_a_colour() -> None:
    with pytest.raises(ValidationError, match="no background_color"):
        TextStyle(background_opacity=0.5)


def test_overlay_counts_include_shot_and_scene_overlays() -> None:
    built = timeline(
        scenes=[
            scene(
                shots=[
                    shot(
                        overlays=[
                            Overlay(
                                overlay_id="o1",
                                kind=OverlayKind.TITLE,
                                span=FrameSpan(start_frame=0, duration_frames=10),
                                text="a",
                            )
                        ]
                    )
                ],
                overlays=[
                    Overlay(
                        overlay_id="o2",
                        kind=OverlayKind.SUBTITLE,
                        span=FrameSpan(start_frame=0, duration_frames=10),
                        text="b",
                    )
                ],
            )
        ]
    )
    assert built.overlay_count == 2


# --- timeline document --------------------------------------------------------


def test_total_frames_and_samples_agree() -> None:
    built = timeline(
        scenes=[
            scene("scene-01", 0, shots=[shot("shot-01", 0, 90)]),
            scene("scene-02", 1, shots=[shot("shot-02", 0, 150)]),
        ]
    )
    assert built.total_frames == 240
    assert built.total_samples == 240 * 1600
    assert built.duration_seconds == pytest.approx(8.0)


def test_scene_offsets_accumulate_in_order() -> None:
    built = timeline(
        scenes=[
            scene("scene-02", 1, shots=[shot("shot-02", 0, 60)]),
            scene("scene-01", 0, shots=[shot("shot-01", 0, 90)]),
        ]
    )
    assert built.scene_offset_frames("scene-01") == 0
    assert built.scene_offset_frames("scene-02") == 90
    with pytest.raises(KeyError):
        built.scene_offset_frames("scene-99")


def test_duplicate_scene_ids_and_orders_are_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate scene_id"):
        timeline(
            scenes=[
                scene("scene-01", 0, shots=[shot("shot-01")]),
                scene("scene-01", 1, shots=[shot("shot-02")]),
            ]
        )
    with pytest.raises(ValidationError, match="duplicate scene order"):
        timeline(
            scenes=[
                scene("scene-01", 0, shots=[shot("shot-01")]),
                scene("scene-02", 0, shots=[shot("shot-02")]),
            ]
        )


def test_an_unaligned_sample_rate_is_rejected() -> None:
    # 11025 / 30 = 367.5 samples per frame, so frame-to-sample conversion would
    # round every frame and accumulate drift.
    with pytest.raises(ValidationError, match="would drift"):
        timeline(audio_sample_rate=11_025)


def test_an_aligned_non_default_sample_rate_is_accepted() -> None:
    # 44100 / 30 = 1470 exactly, so this is fine even though it is not 48 kHz.
    assert timeline(audio_sample_rate=44_100).audio_sample_rate == 44_100


def test_the_timeline_round_trips_through_json() -> None:
    original = timeline()
    restored = CompositionTimeline.model_validate(original.model_dump(mode="json"))
    assert restored == original


def test_asset_ids_cover_shots_overlays_and_audio() -> None:
    built = timeline(
        scenes=[
            scene(
                shots=[shot(asset_id="asset-0001")],
                overlays=[
                    Overlay(
                        overlay_id="o1",
                        kind=OverlayKind.IMAGE,
                        span=FrameSpan(start_frame=0, duration_frames=10),
                        image_asset_id="asset-0002",
                        width_fraction=0.2,
                    )
                ],
            )
        ],
        audio=AudioMix(
            segments=[
                AudioSegment(
                    segment_id="narration-01",
                    role=AudioRole.NARRATION,
                    asset_id="asset-0003",
                    span=SampleSpan(start_sample=0, duration_samples=1600),
                )
            ]
        ),
    )
    assert built.asset_ids() == {"asset-0001", "asset-0002", "asset-0003"}


# --- audio --------------------------------------------------------------------


def test_overlapping_narration_is_rejected() -> None:
    """Two voices at once is not what this pipeline is for."""
    with pytest.raises(ValidationError, match="Overlapping narration is rejected"):
        AudioMix(
            segments=[
                AudioSegment(
                    segment_id="n1",
                    role=AudioRole.NARRATION,
                    asset_id="a",
                    span=SampleSpan(start_sample=0, duration_samples=48_000),
                ),
                AudioSegment(
                    segment_id="n2",
                    role=AudioRole.NARRATION,
                    asset_id="b",
                    span=SampleSpan(start_sample=47_000, duration_samples=48_000),
                ),
            ]
        )


def test_adjacent_narration_is_allowed() -> None:
    mix = AudioMix(
        segments=[
            AudioSegment(
                segment_id="n1",
                role=AudioRole.NARRATION,
                asset_id="a",
                span=SampleSpan(start_sample=0, duration_samples=48_000),
            ),
            AudioSegment(
                segment_id="n2",
                role=AudioRole.NARRATION,
                asset_id="b",
                span=SampleSpan(start_sample=48_000, duration_samples=48_000),
            ),
        ]
    )
    assert len(mix.segments_for(AudioRole.NARRATION)) == 2
    assert mix.end_sample == 96_000


def test_overlapping_music_is_allowed() -> None:
    """Only narration is restricted; layered music is a legitimate choice."""
    mix = AudioMix(
        segments=[
            AudioSegment(
                segment_id="m1",
                role=AudioRole.MUSIC,
                asset_id="a",
                span=SampleSpan(start_sample=0, duration_samples=48_000),
            ),
            AudioSegment(
                segment_id="m2",
                role=AudioRole.MUSIC,
                asset_id="b",
                span=SampleSpan(start_sample=1_000, duration_samples=48_000),
            ),
        ]
    )
    assert len(mix.segments_for(AudioRole.MUSIC)) == 2


def test_duplicate_segment_ids_are_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate segment_id"):
        AudioMix(
            segments=[
                AudioSegment(
                    segment_id="s1",
                    role=AudioRole.MUSIC,
                    asset_id="a",
                    span=SampleSpan(start_sample=0, duration_samples=10),
                ),
                AudioSegment(
                    segment_id="s1",
                    role=AudioRole.EFFECT,
                    asset_id="b",
                    span=SampleSpan(start_sample=0, duration_samples=10),
                ),
            ]
        )


def test_audio_fades_cannot_exceed_the_segment() -> None:
    with pytest.raises(ValidationError, match="exceed its 1000 samples"):
        AudioSegment(
            segment_id="s1",
            role=AudioRole.MUSIC,
            asset_id="a",
            span=SampleSpan(start_sample=0, duration_samples=1000),
            fade_in_samples=600,
            fade_out_samples=600,
        )


def test_audio_past_the_end_of_the_video_is_rejected() -> None:
    """Audio is never silently truncated to fit the visuals."""
    with pytest.raises(ValidationError, match="never silently truncated"):
        timeline(
            scenes=[scene(shots=[shot(frames=30)])],  # 30 frames = 48000 samples
            audio=AudioMix(
                segments=[
                    AudioSegment(
                        segment_id="n1",
                        role=AudioRole.NARRATION,
                        asset_id="a",
                        span=SampleSpan(start_sample=0, duration_samples=96_000),
                    )
                ]
            ),
        )


def test_ducking_defaults_are_sensible() -> None:
    ducking = Ducking()
    assert ducking.enabled
    assert ducking.attenuation_db < 0
    assert ducking.release_samples > ducking.attack_samples


def test_role_gain_is_bounded() -> None:
    with pytest.raises(ValidationError, match="outside"):
        AudioMix(role_gain_db={AudioRole.MUSIC: 40.0})


def test_anchor_covers_nine_positions() -> None:
    assert len(list(Anchor)) == 9
