"""The storyboard: ordered visual shots grouped under ordered scenes.

Narrative scenes and visual shots are separate concepts. A storyboard scene
references a narration scene by identifier and holds one *or many* shots; a shot
is the unit that the renderer turns into frames.
"""

from __future__ import annotations

from enum import Enum
from typing import ClassVar

from pydantic import Field, field_validator, model_validator

from polymation.schemas.base import SCHEMA_VERSION, PolymationModel, TimestampedDocument
from polymation.schemas.script import SCENE_ID_PATTERN

#: Shot identifiers share the narration scene grammar.
SHOT_ID_PATTERN = SCENE_ID_PATTERN

#: Guard rails on shot length. A shot shorter than one frame at 30fps cannot be
#: rendered; the upper bound catches transposed-unit mistakes (minutes vs seconds).
MIN_SHOT_DURATION_SECONDS = 1 / 30
MAX_SHOT_DURATION_SECONDS = 600.0


class FitMode(str, Enum):
    """How a source asset is fitted into the output frame.

    ``CONTAIN`` letterboxes or pillarboxes to preserve the source aspect ratio —
    the correct default for archival 4:3 material in a 16:9 composition.
    ``COVER`` fills the frame and crops the overflow. Neither mode distorts.
    """

    CONTAIN = "contain"
    COVER = "cover"


class VisualShot(PolymationModel):
    """A single visual element on the timeline."""

    shot_id: str
    scene_id: str
    asset_id: str | None = None
    description: str = Field(min_length=1)
    duration_seconds: float = Field(ge=MIN_SHOT_DURATION_SECONDS, le=MAX_SHOT_DURATION_SECONDS)
    fit_mode: FitMode = FitMode.CONTAIN
    on_screen_text: str | None = None
    motion: str | None = None
    transition: str | None = None

    @field_validator("shot_id")
    @classmethod
    def _check_shot_id(cls, value: str) -> str:
        if not SHOT_ID_PATTERN.match(value):
            raise ValueError(
                f"shot_id {value!r} is invalid; expected lowercase letters, digits, "
                f"hyphens or underscores, starting with a letter or digit"
            )
        return value

    @field_validator("scene_id")
    @classmethod
    def _check_scene_id(cls, value: str) -> str:
        if not SCENE_ID_PATTERN.match(value):
            raise ValueError(f"scene_id {value!r} is invalid")
        return value


class StoryboardScene(PolymationModel):
    """An ordered group of shots covering one narration scene."""

    scene_id: str
    order: int = Field(ge=0)
    shots: list[VisualShot] = Field(default_factory=list)

    @field_validator("scene_id")
    @classmethod
    def _check_scene_id(cls, value: str) -> str:
        if not SCENE_ID_PATTERN.match(value):
            raise ValueError(f"scene_id {value!r} is invalid")
        return value

    @model_validator(mode="after")
    def _check_shots(self) -> StoryboardScene:
        seen: set[str] = set()
        for shot in self.shots:
            if shot.shot_id in seen:
                raise ValueError(f"duplicate shot_id {shot.shot_id!r} in scene {self.scene_id!r}")
            seen.add(shot.shot_id)
        return self

    @property
    def duration_seconds(self) -> float:
        """Total length of this scene's shots."""
        return sum(shot.duration_seconds for shot in self.shots)


class Storyboard(TimestampedDocument):
    """``projects/<project_id>/storyboard.json``."""

    DOCUMENT_KIND: ClassVar[str] = "storyboard"
    SUPPORTED_SCHEMA_VERSIONS: ClassVar[frozenset[str]] = frozenset({SCHEMA_VERSION})

    project_id: str
    scenes: list[StoryboardScene] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_unique_ids(self) -> Storyboard:
        scene_ids: set[str] = set()
        shot_ids: set[str] = set()

        for scene in self.scenes:
            if scene.scene_id in scene_ids:
                raise ValueError(f"duplicate storyboard scene_id {scene.scene_id!r}")
            scene_ids.add(scene.scene_id)

            for shot in scene.shots:
                if shot.shot_id in shot_ids:
                    raise ValueError(f"duplicate shot_id {shot.shot_id!r} in storyboard")
                shot_ids.add(shot.shot_id)
                if shot.scene_id != scene.scene_id:
                    raise ValueError(
                        f"shot {shot.shot_id!r} declares scene_id {shot.scene_id!r} but is "
                        f"nested under scene {scene.scene_id!r}"
                    )

        return self

    @property
    def shots(self) -> list[VisualShot]:
        """Every shot in timeline order (scene order, then shot order within scene)."""
        ordered_scenes = sorted(self.scenes, key=lambda scene: scene.order)
        return [shot for scene in ordered_scenes for shot in scene.shots]

    @property
    def shot_count(self) -> int:
        """Total number of shots across all scenes."""
        return sum(len(scene.shots) for scene in self.scenes)

    @property
    def duration_seconds(self) -> float:
        """Total storyboard length in seconds."""
        return sum(scene.duration_seconds for scene in self.scenes)
