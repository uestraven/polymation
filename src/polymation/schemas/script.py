"""The script document: narration scenes, factual claims and editorial notes.

Script *generation* is out of M0.1 scope. This module defines only the data
contract, so narration authored by a human (or, later, reviewed AI output) has a
stable place to live.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import ClassVar

from pydantic import Field, field_validator, model_validator

from polymation.schemas.base import SCHEMA_VERSION, PolymationModel, TimestampedDocument

#: Scene identifiers are referenced from the storyboard, so they are constrained
#: to a short, stable, human-typable grammar.
SCENE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")


class NarrationStatus(str, Enum):
    """Whether a single narration scene has been approved for production use."""

    DRAFT = "draft"
    APPROVED = "approved"


class ScriptApprovalStatus(str, Enum):
    """Whether the script as a whole has been approved."""

    DRAFT = "draft"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    REJECTED = "rejected"


class ClaimVerification(str, Enum):
    """Verification state of a factual claim made by the narration."""

    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    DISPUTED = "disputed"


class NarrationScene(PolymationModel):
    """One narrative beat of the script.

    A narration scene is *not* a visual shot. One scene may be covered by any
    number of shots; see :mod:`polymation.schemas.storyboard`.
    """

    scene_id: str
    order: int = Field(ge=0)
    narration: str = Field(min_length=1)
    status: NarrationStatus = NarrationStatus.DRAFT
    estimated_duration_seconds: float | None = Field(default=None, gt=0, le=3600)
    notes: str | None = None

    @field_validator("scene_id")
    @classmethod
    def _check_scene_id(cls, value: str) -> str:
        if not SCENE_ID_PATTERN.match(value):
            raise ValueError(
                f"scene_id {value!r} is invalid; expected lowercase letters, digits, "
                f"hyphens or underscores, starting with a letter or digit"
            )
        return value

    @property
    def is_approved(self) -> bool:
        """Whether this scene's narration carries explicit approval."""
        return self.status is NarrationStatus.APPROVED


class FactualClaim(PolymationModel):
    """A factual assertion made by the narration, with its supporting source.

    Automated research and automated verification are out of M0.1 scope: the
    verification field records a human judgement.
    """

    claim_id: str
    scene_id: str | None = None
    statement: str = Field(min_length=1)
    source: str | None = None
    verification: ClaimVerification = ClaimVerification.UNVERIFIED
    notes: str | None = None


class PronunciationNote(PolymationModel):
    """How to pronounce a term, for narration recording or synthesis."""

    term: str = Field(min_length=1)
    guidance: str = Field(min_length=1)
    scene_ids: list[str] = Field(default_factory=list)


class ScriptDocument(TimestampedDocument):
    """``projects/<project_id>/script.json``."""

    DOCUMENT_KIND: ClassVar[str] = "script document"
    SUPPORTED_SCHEMA_VERSIONS: ClassVar[frozenset[str]] = frozenset({SCHEMA_VERSION})

    project_id: str
    script_version: int = Field(default=1, ge=1)
    approval_status: ScriptApprovalStatus = ScriptApprovalStatus.DRAFT
    scenes: list[NarrationScene] = Field(default_factory=list)
    claims: list[FactualClaim] = Field(default_factory=list)
    pronunciation_notes: list[PronunciationNote] = Field(default_factory=list)
    editorial_notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_unique_scene_ids(self) -> ScriptDocument:
        seen: set[str] = set()
        for scene in self.scenes:
            if scene.scene_id in seen:
                raise ValueError(f"duplicate narration scene_id {scene.scene_id!r}")
            seen.add(scene.scene_id)

        claim_ids: set[str] = set()
        for claim in self.claims:
            if claim.claim_id in claim_ids:
                raise ValueError(f"duplicate claim_id {claim.claim_id!r}")
            claim_ids.add(claim.claim_id)

        return self

    @property
    def scene_ids(self) -> set[str]:
        """Identifiers of every narration scene in this script."""
        return {scene.scene_id for scene in self.scenes}

    @property
    def approved_scenes(self) -> list[NarrationScene]:
        """Only those scenes whose narration is explicitly approved."""
        return [scene for scene in self.scenes if scene.is_approved]

    @property
    def draft_scenes(self) -> list[NarrationScene]:
        """Scenes whose narration is still a draft."""
        return [scene for scene in self.scenes if not scene.is_approved]

    @property
    def is_fully_approved(self) -> bool:
        """Whether the document and every one of its scenes are approved.

        An empty script is never 'fully approved': there is nothing to approve.
        """
        return (
            self.approval_status is ScriptApprovalStatus.APPROVED
            and bool(self.scenes)
            and all(scene.is_approved for scene in self.scenes)
        )
