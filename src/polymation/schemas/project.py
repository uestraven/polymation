"""The video project manifest: the authoritative record of a single video."""

from __future__ import annotations

import re
from enum import Enum
from typing import ClassVar

from pydantic import Field, field_validator

from polymation.errors import InvalidProjectIdError
from polymation.schemas.base import SCHEMA_VERSION, PolymationModel, TimestampedDocument

#: Project identifiers are filesystem-safe by construction: they become directory
#: names, so the grammar is deliberately narrow.
PROJECT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")

#: Slugs follow the same grammar as identifiers but are derived from the title
#: and may change when the title changes.
SLUG_PATTERN = PROJECT_ID_PATTERN


class ContentFormat(str, Enum):
    """Supported output formats."""

    STANDARD = "standard"
    SHORT = "short"


class PrimarySubject(str, Enum):
    """Supported editorial subjects."""

    JRPG = "jrpg"
    ANIME = "anime"
    CROSSOVER = "crossover"
    INDUSTRY_HISTORY = "industry_history"


class ContentPillar(str, Enum):
    """Channel content pillars, mirroring channels/polymation/editorial.yaml."""

    STORY_WORLD_DEEP_DIVE = "story_world_deep_dive"
    DESIGN_CRAFT_BREAKDOWN = "design_craft_breakdown"
    ERA_SPOTLIGHT = "era_spotlight"
    SHORT_FORM_HOOK = "short_form_hook"


class ProductionStage(str, Enum):
    """Where a project currently sits in the production process.

    The stage is descriptive bookkeeping. It is never treated as proof that the
    work of a stage was actually done or approved — validation and approval
    records carry that meaning.
    """

    DRAFT = "draft"
    RESEARCH = "research"
    SCRIPT = "script"
    STORYBOARD = "storyboard"
    ASSETS = "assets"
    RENDER = "render"
    REVIEW = "review"
    COMPLETE = "complete"


#: Duration bounds enforced per format. The authoritative editorial values live
#: in channels/polymation/editorial.yaml; these are the schema-level guard rails.
FORMAT_DURATION_BOUNDS: dict[ContentFormat, tuple[int, int]] = {
    ContentFormat.STANDARD: (120, 300),
    ContentFormat.SHORT: (30, 60),
}


def validate_project_id(raw: str) -> str:
    """Return ``raw`` if it is a well-formed project identifier.

    Raises:
        InvalidProjectIdError: if the identifier could not safely be used as a
            directory name.
    """
    if not raw or not raw.strip():
        raise InvalidProjectIdError("A project identifier may not be empty.")
    if raw != raw.strip():
        raise InvalidProjectIdError(
            f"Project identifier {raw!r} has leading or trailing whitespace."
        )
    if not PROJECT_ID_PATTERN.match(raw):
        raise InvalidProjectIdError(
            f"Project identifier {raw!r} is invalid. Use 3-64 characters of "
            f"lowercase letters, digits and single hyphens, starting and ending "
            f"with a letter or digit."
        )
    if "--" in raw:
        raise InvalidProjectIdError(
            f"Project identifier {raw!r} contains a double hyphen. Use single hyphens."
        )
    return raw


class ManifestPaths(PolymationModel):
    """Project-relative locations of the documents owned by a project.

    Stored explicitly so the layout can evolve without a schema migration for
    every consumer that needs to find a sibling document.
    """

    brief: str = "brief.md"
    script: str = "script.json"
    storyboard: str = "storyboard.json"
    assets: str = "assets.json"
    rights: str = "rights.json"
    approvals: str = "approvals.json"
    output_dir: str = "output"
    assets_dir: str = "assets"


class VideoProject(TimestampedDocument):
    """The project manifest — ``projects/<project_id>/manifest.json``.

    ``project_id`` is assigned once at creation and never derived from the
    current title, so renaming a video does not move or rename its project.
    """

    DOCUMENT_KIND: ClassVar[str] = "project manifest"
    SUPPORTED_SCHEMA_VERSIONS: ClassVar[frozenset[str]] = frozenset({SCHEMA_VERSION})

    project_id: str
    title: str = Field(min_length=1, max_length=300)
    slug: str
    channel_id: str = Field(min_length=1, max_length=64)
    content_pillar: ContentPillar
    primary_subject: PrimarySubject
    content_format: ContentFormat
    language: str = Field(default="en", min_length=2, max_length=16)
    target_duration_seconds: int = Field(gt=0, le=7200)
    production_stage: ProductionStage = ProductionStage.DRAFT
    paths: ManifestPaths = Field(default_factory=ManifestPaths)

    @field_validator("project_id")
    @classmethod
    def _check_project_id(cls, value: str) -> str:
        return validate_project_id(value)

    @field_validator("slug")
    @classmethod
    def _check_slug(cls, value: str) -> str:
        if not SLUG_PATTERN.match(value) or "--" in value:
            raise ValueError(
                f"slug {value!r} is invalid; expected 3-64 characters of lowercase "
                f"letters, digits and single hyphens"
            )
        return value

    @property
    def duration_bounds(self) -> tuple[int, int]:
        """Return the (minimum, maximum) duration permitted for this format."""
        return FORMAT_DURATION_BOUNDS[self.content_format]

    def duration_is_in_format_bounds(self) -> bool:
        """Whether the target duration falls inside this format's editorial bounds."""
        minimum, maximum = self.duration_bounds
        return minimum <= self.target_duration_seconds <= maximum
