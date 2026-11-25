"""The composition timeline — the executable counterpart to the storyboard.

The storyboard records **creative intent**: this scene shows that subject, for
about this long. The timeline records **executable media placement**: this asset
occupies frames 450-600, fitted this way, with this overlay from frame 465, and
narration occupying samples 720 000-1 040 000.

They are separate documents with separate schema versions because they change
for different reasons and at different rates. Compiling a storyboard produces a
timeline; editing a timeline never edits the storyboard.

All visual timing is integer frames and all audio timing integer samples; see
:mod:`polymation.schemas.timing` for why.
"""

from __future__ import annotations

import re
from enum import Enum
from itertools import pairwise
from typing import ClassVar

from pydantic import Field, field_validator, model_validator

from polymation.schemas.base import SCHEMA_VERSION, PolymationModel, TimestampedDocument
from polymation.schemas.script import SCENE_ID_PATTERN
from polymation.schemas.timing import FrameSpan, SampleSpan

#: Identifier grammar shared by scenes, shots, overlays and audio segments.
ELEMENT_ID_PATTERN = SCENE_ID_PATTERN

#: A colour is either an explicit 6-digit hex value or a reference into the
#: channel's brand palette. Palette references keep brand values out of the
#: timeline, so rebranding does not require rewriting every project.
HEX_COLOR_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}$")
PALETTE_REFERENCE_PATTERN = re.compile(r"^palette:[a-z0-9_]{1,64}$")


def validate_color_spec(value: str) -> str:
    """Validate a colour as ``#RRGGBB`` or ``palette:<name>``."""
    if HEX_COLOR_PATTERN.match(value):
        return value.upper()
    if PALETTE_REFERENCE_PATTERN.match(value):
        return value
    raise ValueError(
        f"colour {value!r} must be '#RRGGBB' or 'palette:<name>' "
        f"(for example '#191D38' or 'palette:deep_indigo')"
    )


# --- enums ---------------------------------------------------------------------


class ShotFitMode(str, Enum):
    """How a source is fitted into the output frame.

    None of these distorts: ``CONTAIN`` letterboxes or pillarboxes, ``COVER``
    fills and crops the overflow, ``CROP`` takes an explicit rectangle from the
    source. Archival 4:3 material defaults to ``CONTAIN``.
    """

    CONTAIN = "contain"
    COVER = "cover"
    CROP = "crop"


class ScalingQuality(str, Enum):
    """Which resampling kernel to use.

    ``PIXEL`` uses nearest-neighbour, which is the correct choice for
    pixel-art-era game graphics: bilinear scaling of a 256x224 sprite sheet
    turns crisp pixels into mush. ``SMOOTH`` uses the profile's high-quality
    kernel for photographic and vector-ish material.
    """

    SMOOTH = "smooth"
    PIXEL = "pixel"


class MotionKind(str, Enum):
    """The deterministic image motion operations supported.

    Deliberately a closed set. A general animation system is out of scope.
    """

    STATIC = "static"
    PAN = "pan"
    ZOOM_IN = "zoom_in"
    ZOOM_OUT = "zoom_out"
    PAN_ZOOM = "pan_zoom"


class ShortSourcePolicy(str, Enum):
    """What to do when a video source is shorter than the shot needs.

    The default is to fail. The alternatives exist so that holding or trimming
    is an explicit, recorded decision rather than something the renderer does
    quietly behind the operator's back.
    """

    ERROR = "error"
    HOLD_LAST_FRAME = "hold_last_frame"
    TRIM_SHOT = "trim_shot"


class TransitionKind(str, Enum):
    """The supported transition set.

    All of these are *within-scene* or *at a scene boundary against black*, so
    every scene still renders independently and the scene cache stays valid.
    A cross-scene crossfade would couple adjacent scenes and is deliberately
    not offered — see docs/DECISIONS.md D-018.
    """

    CUT = "cut"
    FADE_FROM_BLACK = "fade_from_black"
    FADE_TO_BLACK = "fade_to_black"
    CROSSFADE = "crossfade"


class OverlayKind(str, Enum):
    """Composition primitives available as overlays."""

    TITLE = "title"
    SUBTITLE = "subtitle"
    LOWER_THIRD = "lower_third"
    INFO_TEXT = "info_text"
    IMAGE = "image"
    COLOR_PANEL = "color_panel"
    COMPARISON_LABEL = "comparison_label"


class Anchor(str, Enum):
    """Nine-point anchor for overlay placement, within the safe area."""

    TOP_LEFT = "top_left"
    TOP_CENTER = "top_center"
    TOP_RIGHT = "top_right"
    MIDDLE_LEFT = "middle_left"
    MIDDLE_CENTER = "middle_center"
    MIDDLE_RIGHT = "middle_right"
    BOTTOM_LEFT = "bottom_left"
    BOTTOM_CENTER = "bottom_center"
    BOTTOM_RIGHT = "bottom_right"


class TextAlign(str, Enum):
    """Horizontal text alignment within an overlay box."""

    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"


class AudioRole(str, Enum):
    """Which mix bus an audio segment belongs to.

    Separate roles exist so narration can duck music without the renderer
    guessing which input is which, and so stems can be exported per role.
    """

    NARRATION = "narration"
    MUSIC = "music"
    SOURCE = "source"
    EFFECT = "effect"


# --- geometry and styling ------------------------------------------------------


class CropRect(PolymationModel):
    """An explicit source rectangle, in normalised source coordinates.

    Normalised rather than pixels so the same crop survives a source being
    re-exported at a different resolution.
    """

    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    width: float = Field(gt=0.0, le=1.0)
    height: float = Field(gt=0.0, le=1.0)

    @model_validator(mode="after")
    def _check_bounds(self) -> CropRect:
        if self.x + self.width > 1.0 + 1e-9:
            raise ValueError(
                f"crop extends past the right edge: x={self.x} + width={self.width} > 1.0"
            )
        if self.y + self.height > 1.0 + 1e-9:
            raise ValueError(
                f"crop extends past the bottom edge: y={self.y} + height={self.height} > 1.0"
            )
        return self


class Motion(PolymationModel):
    """A deterministic pan and/or zoom over a still image.

    Zoom is constrained to ``>= 1.0`` and the visible window is constrained to
    stay inside the source, so motion can never expose undefined image
    boundaries. A centre that would show past an edge is rejected with a message
    naming the permitted range, rather than silently clamped.
    """

    kind: MotionKind = MotionKind.STATIC
    start_zoom: float = Field(default=1.0, ge=1.0, le=8.0)
    end_zoom: float = Field(default=1.0, ge=1.0, le=8.0)
    start_center_x: float = Field(default=0.5, ge=0.0, le=1.0)
    start_center_y: float = Field(default=0.5, ge=0.0, le=1.0)
    end_center_x: float = Field(default=0.5, ge=0.0, le=1.0)
    end_center_y: float = Field(default=0.5, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _check_consistency(self) -> Motion:
        if self.kind is MotionKind.STATIC:
            if (self.start_zoom, self.end_zoom) != (1.0, 1.0):
                raise ValueError("motion kind 'static' must leave both zoom values at 1.0")
            if (self.start_center_x, self.start_center_y) != (
                self.end_center_x,
                self.end_center_y,
            ):
                raise ValueError("motion kind 'static' must not move the centre")

        if self.kind is MotionKind.ZOOM_IN and self.end_zoom <= self.start_zoom:
            raise ValueError(
                f"motion kind 'zoom_in' requires end_zoom > start_zoom, got "
                f"{self.start_zoom} -> {self.end_zoom}"
            )
        if self.kind is MotionKind.ZOOM_OUT and self.end_zoom >= self.start_zoom:
            raise ValueError(
                f"motion kind 'zoom_out' requires end_zoom < start_zoom, got "
                f"{self.start_zoom} -> {self.end_zoom}"
            )
        if self.kind is MotionKind.PAN and (
            self.start_center_x,
            self.start_center_y,
        ) == (self.end_center_x, self.end_center_y):
            raise ValueError("motion kind 'pan' requires the centre to move")

        for label, zoom, centre_x, centre_y in (
            ("start", self.start_zoom, self.start_center_x, self.start_center_y),
            ("end", self.end_zoom, self.end_center_x, self.end_center_y),
        ):
            half = 0.5 / zoom
            low, high = half, 1.0 - half
            for axis, value in (("x", centre_x), ("y", centre_y)):
                if not (low - 1e-9 <= value <= high + 1e-9):
                    raise ValueError(
                        f"{label}_center_{axis}={value} at zoom {zoom} would show past "
                        f"the image edge; at this zoom the centre must be within "
                        f"[{low:.4f}, {high:.4f}]"
                    )

        return self

    @property
    def is_static(self) -> bool:
        """Whether this describes no movement at all."""
        return self.kind is MotionKind.STATIC


class TextStyle(PolymationModel):
    """Typography and box styling for a text overlay.

    ``font_family`` is a *reference*; availability is resolved at render time
    and a substitution is reported rather than hidden. Final brand typography is
    M0.3 work.
    """

    font_family: str = Field(default="Space Grotesk", min_length=1, max_length=128)
    font_size_px: int = Field(default=48, ge=8, le=400)
    color: str = Field(default="palette:parchment_cream")
    background_color: str | None = None
    background_opacity: float = Field(default=0.0, ge=0.0, le=1.0)
    padding_px: int = Field(default=24, ge=0, le=400)
    line_spacing_px: int = Field(default=8, ge=0, le=200)
    #: Maximum text box width as a fraction of the frame, used for wrapping.
    max_width_fraction: float = Field(default=0.8, gt=0.0, le=1.0)
    align: TextAlign = TextAlign.LEFT

    @field_validator("color", "background_color")
    @classmethod
    def _check_colors(cls, value: str | None) -> str | None:
        return None if value is None else validate_color_spec(value)

    @model_validator(mode="after")
    def _check_background(self) -> TextStyle:
        if self.background_opacity > 0 and self.background_color is None:
            raise ValueError(
                "background_opacity is greater than zero but no background_color was given"
            )
        return self


class Overlay(PolymationModel):
    """A timed graphic or text element composited over a shot or scene.

    Text is rendered to pixels by Pillow and composited as an image. It is never
    interpolated into an FFmpeg filter expression, so arbitrary punctuation,
    quotes, colons, percent signs and Unicode are safe by construction rather
    than by escaping. See docs/DECISIONS.md D-016.
    """

    overlay_id: str
    kind: OverlayKind
    span: FrameSpan
    anchor: Anchor = Anchor.BOTTOM_LEFT
    offset_x_px: int = Field(default=0, ge=-4000, le=4000)
    offset_y_px: int = Field(default=0, ge=-4000, le=4000)
    text: str | None = Field(default=None, max_length=2000)
    image_asset_id: str | None = None
    #: Rendered width as a fraction of the frame, for image and panel overlays.
    width_fraction: float | None = Field(default=None, gt=0.0, le=1.0)
    height_fraction: float | None = Field(default=None, gt=0.0, le=1.0)
    fill_color: str | None = None
    opacity: float = Field(default=1.0, ge=0.0, le=1.0)
    style: TextStyle = Field(default_factory=TextStyle)
    fade_in_frames: int = Field(default=0, ge=0, le=300)
    fade_out_frames: int = Field(default=0, ge=0, le=300)

    @field_validator("overlay_id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        if not ELEMENT_ID_PATTERN.match(value):
            raise ValueError(f"overlay_id {value!r} is invalid")
        return value

    @field_validator("fill_color")
    @classmethod
    def _check_fill(cls, value: str | None) -> str | None:
        return None if value is None else validate_color_spec(value)

    @model_validator(mode="after")
    def _check_payload(self) -> Overlay:
        text_kinds = {
            OverlayKind.TITLE,
            OverlayKind.SUBTITLE,
            OverlayKind.LOWER_THIRD,
            OverlayKind.INFO_TEXT,
            OverlayKind.COMPARISON_LABEL,
        }

        if self.kind in text_kinds:
            if not self.text or not self.text.strip():
                raise ValueError(f"overlay kind {self.kind.value!r} requires non-empty text")
            if self.image_asset_id is not None:
                raise ValueError(
                    f"overlay kind {self.kind.value!r} is a text overlay and must not "
                    f"reference an image asset"
                )
        elif self.kind is OverlayKind.IMAGE:
            if not self.image_asset_id:
                raise ValueError("overlay kind 'image' requires image_asset_id")
            if self.text is not None:
                raise ValueError("overlay kind 'image' must not carry text")
        elif self.kind is OverlayKind.COLOR_PANEL:
            if self.fill_color is None:
                raise ValueError("overlay kind 'color_panel' requires fill_color")
            if self.width_fraction is None or self.height_fraction is None:
                raise ValueError(
                    "overlay kind 'color_panel' requires width_fraction and height_fraction"
                )
            if self.text is not None or self.image_asset_id is not None:
                raise ValueError("overlay kind 'color_panel' carries neither text nor an image")

        if self.fade_in_frames + self.fade_out_frames > self.span.duration_frames:
            raise ValueError(
                f"overlay {self.overlay_id!r} fades ({self.fade_in_frames} in + "
                f"{self.fade_out_frames} out) exceed its {self.span.duration_frames}-frame span"
            )

        return self

    @property
    def is_text(self) -> bool:
        """Whether this overlay renders text."""
        return self.text is not None


class Transition(PolymationModel):
    """A transition applied at a shot or scene boundary.

    ``duration_frames`` is the length of the effect. For fades to and from
    black the effect happens *inside* the owning span, so the timeline duration
    is unchanged. For a crossfade the incoming shot overlaps the outgoing one by
    this many frames, which shortens the scene — accounted for explicitly in
    :meth:`TimelineScene.duration_frames` rather than left implicit.
    """

    kind: TransitionKind = TransitionKind.CUT
    duration_frames: int = Field(default=0, ge=0, le=300)

    @model_validator(mode="after")
    def _check_duration(self) -> Transition:
        if self.kind is TransitionKind.CUT:
            if self.duration_frames != 0:
                raise ValueError("transition kind 'cut' must have duration_frames = 0")
        elif self.duration_frames < 1:
            raise ValueError(f"transition kind {self.kind.value!r} requires duration_frames >= 1")
        return self

    @property
    def consumes_timeline(self) -> bool:
        """Whether this transition shortens the timeline by overlapping material."""
        return self.kind is TransitionKind.CROSSFADE


# --- shots and scenes ----------------------------------------------------------


class SourceTrim(PolymationModel):
    """Where in a video source the shot begins.

    Only a start offset: M0.2 renders at 1:1 speed, so the consumed source
    length always equals the shot's timeline length. Retiming is out of scope,
    and modelling a separate source duration would imply support that does not
    exist.
    """

    start_frame: int = Field(default=0, ge=0, le=216_000)


class CompositionShot(PolymationModel):
    """One piece of source material placed on the timeline.

    ``span.start_frame`` is relative to the **start of the owning scene**, not
    the project. Scene-relative timing is what lets a scene be rendered and
    cached in isolation; the assembler offsets scenes when it concatenates them.
    """

    shot_id: str
    asset_id: str
    span: FrameSpan
    fit_mode: ShotFitMode = ShotFitMode.CONTAIN
    crop: CropRect | None = None
    scaling: ScalingQuality = ScalingQuality.SMOOTH
    background_color: str = "palette:deep_indigo"
    source_trim: SourceTrim | None = None
    short_source_policy: ShortSourcePolicy = ShortSourcePolicy.ERROR
    motion: Motion = Field(default_factory=Motion)
    #: Include the source's own audio on the SOURCE bus. Muted by default:
    #: archival clips usually carry audio we have no right to publish.
    include_source_audio: bool = False
    overlays: list[Overlay] = Field(default_factory=list)
    transition_in: Transition = Field(default_factory=Transition)

    @field_validator("shot_id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        if not ELEMENT_ID_PATTERN.match(value):
            raise ValueError(f"shot_id {value!r} is invalid")
        return value

    @field_validator("background_color")
    @classmethod
    def _check_background(cls, value: str) -> str:
        return validate_color_spec(value)

    @model_validator(mode="after")
    def _check_combination(self) -> CompositionShot:
        if self.fit_mode is ShotFitMode.CROP and self.crop is None:
            raise ValueError(
                f"shot {self.shot_id!r} uses fit_mode 'crop' but supplies no crop "
                f"rectangle. Provide `crop`, or use 'contain'/'cover'."
            )
        if self.fit_mode is not ShotFitMode.CROP and self.crop is not None:
            raise ValueError(
                f"shot {self.shot_id!r} supplies a crop rectangle but fit_mode is "
                f"{self.fit_mode.value!r}. Set fit_mode to 'crop', or remove `crop`."
            )

        overlay_ids: set[str] = set()
        for overlay in self.overlays:
            if overlay.overlay_id in overlay_ids:
                raise ValueError(f"duplicate overlay_id {overlay.overlay_id!r} in shot")
            overlay_ids.add(overlay.overlay_id)

            # Shot overlays are timed relative to the shot.
            shot_local = FrameSpan(start_frame=0, duration_frames=self.span.duration_frames)
            if not shot_local.contains(overlay.span):
                raise ValueError(
                    f"overlay {overlay.overlay_id!r} span "
                    f"[{overlay.span.start_frame}, {overlay.span.end_frame}) falls outside "
                    f"its shot's {self.span.duration_frames} frames"
                )

        if self.transition_in.kind is TransitionKind.CROSSFADE and (
            self.transition_in.duration_frames >= self.span.duration_frames
        ):
            raise ValueError(
                f"shot {self.shot_id!r} crossfade of {self.transition_in.duration_frames} "
                f"frames is not shorter than the shot's {self.span.duration_frames} frames"
            )

        return self


class TimelineScene(PolymationModel):
    """An independently renderable unit: the granularity of the render cache.

    Shot spans are scene-relative and must tile the scene without gaps or
    overlaps (apart from deliberate crossfade overlap), so a scene's length is
    exactly determined by its shots.
    """

    scene_id: str
    order: int = Field(ge=0)
    shots: list[CompositionShot] = Field(min_length=1)
    #: Overlays spanning the whole scene rather than one shot.
    overlays: list[Overlay] = Field(default_factory=list)
    transition_in: Transition = Field(default_factory=Transition)
    transition_out: Transition = Field(default_factory=Transition)

    @field_validator("scene_id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        if not SCENE_ID_PATTERN.match(value):
            raise ValueError(f"scene_id {value!r} is invalid")
        return value

    @model_validator(mode="after")
    def _check_layout(self) -> TimelineScene:
        shot_ids: set[str] = set()
        for shot in self.shots:
            if shot.shot_id in shot_ids:
                raise ValueError(f"duplicate shot_id {shot.shot_id!r} in scene {self.scene_id!r}")
            shot_ids.add(shot.shot_id)

        ordered = sorted(self.shots, key=lambda shot: shot.span.start_frame)
        if ordered[0].span.start_frame != 0:
            raise ValueError(
                f"scene {self.scene_id!r} first shot starts at frame "
                f"{ordered[0].span.start_frame}; scene-relative shots must start at 0"
            )

        cursor = 0
        for shot in ordered:
            overlap = (
                shot.transition_in.duration_frames
                if shot.transition_in.kind is TransitionKind.CROSSFADE
                else 0
            )
            expected = cursor - overlap
            if shot.span.start_frame != expected:
                raise ValueError(
                    f"scene {self.scene_id!r} shot {shot.shot_id!r} starts at frame "
                    f"{shot.span.start_frame} but the previous material ends at {cursor} "
                    f"with a {overlap}-frame crossfade, so it must start at {expected}. "
                    f"Shots must tile the scene without gaps."
                )
            cursor = shot.span.end_frame

        total = cursor
        overlay_ids: set[str] = set()
        scene_span = FrameSpan(start_frame=0, duration_frames=total)
        for overlay in self.overlays:
            if overlay.overlay_id in overlay_ids:
                raise ValueError(f"duplicate overlay_id {overlay.overlay_id!r} in scene")
            overlay_ids.add(overlay.overlay_id)
            if not scene_span.contains(overlay.span):
                raise ValueError(
                    f"scene overlay {overlay.overlay_id!r} span "
                    f"[{overlay.span.start_frame}, {overlay.span.end_frame}) falls outside "
                    f"the scene's {total} frames"
                )

        for label, transition in (
            ("transition_in", self.transition_in),
            ("transition_out", self.transition_out),
        ):
            if transition.kind is TransitionKind.CROSSFADE:
                raise ValueError(
                    f"scene {self.scene_id!r} {label} may not be a crossfade: cross-scene "
                    f"crossfades would couple adjacent scenes and break cache isolation. "
                    f"Use a crossfade on a shot's transition_in instead."
                )
            if transition.duration_frames > total:
                raise ValueError(
                    f"scene {self.scene_id!r} {label} of {transition.duration_frames} "
                    f"frames exceeds the scene's {total} frames"
                )

        return self

    @property
    def duration_frames(self) -> int:
        """Scene length in frames, with crossfade overlaps subtracted."""
        return max(shot.span.end_frame for shot in self.shots)

    @property
    def shots_in_order(self) -> list[CompositionShot]:
        """Shots sorted by scene-relative start frame."""
        return sorted(self.shots, key=lambda shot: shot.span.start_frame)

    def all_overlays(self) -> list[Overlay]:
        """Scene overlays plus every shot overlay, the latter shifted to scene time."""
        combined = list(self.overlays)
        for shot in self.shots:
            for overlay in shot.overlays:
                combined.append(
                    overlay.model_copy(update={"span": overlay.span.shifted(shot.span.start_frame)})
                )
        return combined

    def asset_ids(self) -> list[str]:
        """Every asset this scene references, shots and image overlays alike."""
        found: list[str] = [shot.asset_id for shot in self.shots]
        for overlay in self.all_overlays():
            if overlay.image_asset_id:
                found.append(overlay.image_asset_id)
        return found


# --- audio and captions --------------------------------------------------------


class AudioSegment(PolymationModel):
    """One piece of audio placed on the project-wide timeline.

    Audio spans are **project-absolute** in samples, unlike shot spans, because
    narration and music deliberately cross scene boundaries. That is also why
    audio is mixed at final assembly rather than per scene.
    """

    segment_id: str
    role: AudioRole
    asset_id: str
    span: SampleSpan
    #: Offset into the source, in samples.
    source_start_sample: int = Field(default=0, ge=0, le=345_600_000)
    gain_db: float = Field(default=0.0, ge=-60.0, le=12.0)
    fade_in_samples: int = Field(default=0, ge=0, le=48_000 * 10)
    fade_out_samples: int = Field(default=0, ge=0, le=48_000 * 10)
    #: Pad with silence rather than failing if the source runs out. Must be
    #: requested explicitly — narration is never silently extended.
    allow_silence_padding: bool = False

    @field_validator("segment_id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        if not ELEMENT_ID_PATTERN.match(value):
            raise ValueError(f"segment_id {value!r} is invalid")
        return value

    @model_validator(mode="after")
    def _check_fades(self) -> AudioSegment:
        if self.fade_in_samples + self.fade_out_samples > self.span.duration_samples:
            raise ValueError(
                f"audio segment {self.segment_id!r} fades ({self.fade_in_samples} + "
                f"{self.fade_out_samples}) exceed its {self.span.duration_samples} samples"
            )
        return self


class Ducking(PolymationModel):
    """How music is attenuated while narration plays.

    Implemented as deterministic gain automation derived from the narration
    segments' known sample positions, not as a dynamics processor keyed off the
    audio itself. That makes the result reproducible and directly testable: the
    attenuation windows are computed from the timeline, so a test can assert
    exactly where music is quiet. See docs/DECISIONS.md D-017.
    """

    enabled: bool = True
    #: Attenuation applied to music under narration.
    attenuation_db: float = Field(default=-12.0, ge=-60.0, le=0.0)
    #: Ramp into and out of the attenuation, in samples.
    attack_samples: int = Field(default=4_800, ge=0, le=48_000 * 5)
    release_samples: int = Field(default=14_400, ge=0, le=48_000 * 5)
    #: Extra attenuated time before and after each narration segment.
    lead_in_samples: int = Field(default=0, ge=0, le=48_000 * 5)
    lead_out_samples: int = Field(default=0, ge=0, le=48_000 * 5)


class LoudnessTarget(PolymationModel):
    """Loudness and peak targets for the final mix.

    Engineering targets for this milestone, not a platform requirement. The
    renderer measures what it actually achieved and reports both.
    """

    integrated_lufs: float = Field(default=-16.0, ge=-40.0, le=0.0)
    true_peak_dbtp: float = Field(default=-1.0, ge=-12.0, le=0.0)
    #: Measure and report without applying normalisation.
    measure_only: bool = False


class AudioMix(PolymationModel):
    """The project's complete audio composition."""

    segments: list[AudioSegment] = Field(default_factory=list)
    ducking: Ducking = Field(default_factory=Ducking)
    loudness: LoudnessTarget = Field(default_factory=LoudnessTarget)
    #: Per-role trim applied before mixing, for balancing stems.
    role_gain_db: dict[AudioRole, float] = Field(default_factory=dict)
    #: Keep per-role stems alongside the mix.
    export_stems: bool = False

    @model_validator(mode="after")
    def _check_segments(self) -> AudioMix:
        segment_ids: set[str] = set()
        for segment in self.segments:
            if segment.segment_id in segment_ids:
                raise ValueError(f"duplicate segment_id {segment.segment_id!r}")
            segment_ids.add(segment.segment_id)

        # Overlapping narration is almost always an authoring mistake: two
        # voices at once is not something this pipeline is for.
        narration = sorted(
            (s for s in self.segments if s.role is AudioRole.NARRATION),
            key=lambda s: s.span.start_sample,
        )
        for earlier, later in pairwise(narration):
            if earlier.span.overlaps(later.span):
                raise ValueError(
                    f"narration segments {earlier.segment_id!r} and {later.segment_id!r} "
                    f"overlap ([{earlier.span.start_sample}, {earlier.span.end_sample}) and "
                    f"[{later.span.start_sample}, {later.span.end_sample})). Overlapping "
                    f"narration is rejected rather than mixed."
                )

        for role, gain in self.role_gain_db.items():
            if not -60.0 <= gain <= 12.0:
                raise ValueError(f"role_gain_db[{role.value}]={gain} is outside [-60, 12]")

        return self

    def segments_for(self, role: AudioRole) -> list[AudioSegment]:
        """Segments on one bus, in timeline order."""
        return sorted(
            (s for s in self.segments if s.role is role),
            key=lambda s: s.span.start_sample,
        )

    @property
    def end_sample(self) -> int:
        """First sample after all audio, or 0 when there is none."""
        return max((s.span.end_sample for s in self.segments), default=0)


class CaptionPlacement(PolymationModel):
    """Where and how burned-in captions are drawn."""

    anchor: Anchor = Anchor.BOTTOM_CENTER
    offset_y_px: int = Field(default=-80, ge=-2000, le=2000)
    style: TextStyle = Field(
        default_factory=lambda: TextStyle(
            font_size_px=42,
            color="#FFFFFF",
            background_color="#000000",
            background_opacity=0.65,
            padding_px=18,
            align=TextAlign.CENTER,
            max_width_fraction=0.82,
        )
    )
    #: Maximum lines drawn per cue; a longer cue is a validation warning.
    max_lines: int = Field(default=3, ge=1, le=6)


class CaptionSettings(PolymationModel):
    """How a project's captions participate in rendering."""

    #: Project-relative path to an externally supplied SRT file.
    srt_path: str | None = Field(default=None, max_length=1024)
    #: Composite captions into the video.
    burn_in: bool = False
    #: Write a sidecar .srt next to the rendered video.
    export_sidecar: bool = True
    placement: CaptionPlacement = Field(default_factory=CaptionPlacement)


# --- the timeline document -----------------------------------------------------


class CompositionTimeline(TimestampedDocument):
    """``projects/<project_id>/timeline.json``.

    The authoritative, executable description of a video. Compiled from the
    storyboard by :mod:`polymation.timeline.compile`, then editable directly.
    """

    DOCUMENT_KIND: ClassVar[str] = "composition timeline"
    SUPPORTED_SCHEMA_VERSIONS: ClassVar[frozenset[str]] = frozenset({SCHEMA_VERSION})

    project_id: str
    #: Which profile this timeline was authored against. Timing is identical
    #: across profiles, so this records intent rather than constraining output.
    profile_name: str = "final"
    fps: int = Field(default=30, ge=1, le=120)
    width: int = Field(default=1920, ge=16, le=7680)
    height: int = Field(default=1080, ge=16, le=4320)
    audio_sample_rate: int = Field(default=48_000, ge=8_000, le=192_000)
    #: Fraction of the frame inset on each edge that overlays must stay within.
    safe_area_inset: float = Field(default=0.05, ge=0.0, le=0.25)
    scenes: list[TimelineScene] = Field(default_factory=list)
    audio: AudioMix = Field(default_factory=AudioMix)
    captions: CaptionSettings = Field(default_factory=CaptionSettings)
    #: Rounding that compilation had to apply, carried for operator visibility.
    rounding_notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_timeline(self) -> CompositionTimeline:
        if self.audio_sample_rate % self.fps != 0:
            raise ValueError(
                f"audio_sample_rate {self.audio_sample_rate} is not an exact multiple of "
                f"{self.fps} fps; frame-to-sample conversion would drift"
            )

        scene_ids: set[str] = set()
        orders: set[int] = set()
        shot_ids: set[str] = set()
        for scene in self.scenes:
            if scene.scene_id in scene_ids:
                raise ValueError(f"duplicate scene_id {scene.scene_id!r} in timeline")
            scene_ids.add(scene.scene_id)
            if scene.order in orders:
                raise ValueError(f"duplicate scene order {scene.order} in timeline")
            orders.add(scene.order)
            for shot in scene.shots:
                if shot.shot_id in shot_ids:
                    raise ValueError(f"duplicate shot_id {shot.shot_id!r} across timeline")
                shot_ids.add(shot.shot_id)

        total_samples = self.total_samples
        for segment in self.audio.segments:
            if total_samples and segment.span.end_sample > total_samples:
                raise ValueError(
                    f"audio segment {segment.segment_id!r} ends at sample "
                    f"{segment.span.end_sample}, past the timeline's {total_samples} "
                    f"samples. Audio is never silently truncated to fit, so extend the "
                    f"visuals or shorten the audio deliberately."
                )

        return self

    @property
    def scenes_in_order(self) -> list[TimelineScene]:
        """Scenes sorted by their declared order."""
        return sorted(self.scenes, key=lambda scene: scene.order)

    @property
    def total_frames(self) -> int:
        """Total video length in frames."""
        return sum(scene.duration_frames for scene in self.scenes)

    @property
    def total_samples(self) -> int:
        """Total audio length in samples, matching the video exactly."""
        if self.audio_sample_rate % self.fps != 0:
            return 0
        return self.total_frames * (self.audio_sample_rate // self.fps)

    @property
    def duration_seconds(self) -> float:
        """Total length in seconds."""
        return self.total_frames / self.fps

    @property
    def shot_count(self) -> int:
        """Total shots across all scenes."""
        return sum(len(scene.shots) for scene in self.scenes)

    @property
    def overlay_count(self) -> int:
        """Total overlays, scene-level and shot-level."""
        return sum(len(scene.all_overlays()) for scene in self.scenes)

    def scene_offset_frames(self, scene_id: str) -> int:
        """Absolute start frame of ``scene_id`` on the project timeline.

        Raises:
            KeyError: if no such scene exists.
        """
        offset = 0
        for scene in self.scenes_in_order:
            if scene.scene_id == scene_id:
                return offset
            offset += scene.duration_frames
        raise KeyError(f"no scene {scene_id!r} in this timeline")

    def scene_by_id(self, scene_id: str) -> TimelineScene | None:
        """Look up a scene, or ``None``."""
        for scene in self.scenes:
            if scene.scene_id == scene_id:
                return scene
        return None

    def asset_ids(self) -> set[str]:
        """Every asset the timeline references, visual and audio."""
        found: set[str] = set()
        for scene in self.scenes:
            found.update(scene.asset_ids())
        found.update(segment.asset_id for segment in self.audio.segments)
        return found
