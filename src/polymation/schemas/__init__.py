"""Versioned Pydantic data contracts for every document Polymation Studio stores.

Every document type is persisted as readable JSON with an explicit
``schema_version``. The schema layer owns validation of *shape*; cross-document
consistency (dangling references, missing files, checksum drift) is the job of
:mod:`polymation.validation.validator`.
"""

from polymation.schemas.approvals import (
    ApprovalDecision,
    ApprovalLedger,
    ApprovalRecord,
    ApprovalStage,
)
from polymation.schemas.assets import (
    ALLOWED_CATEGORIES_FOR_TYPE,
    DEFAULT_CATEGORY_FOR_TYPE,
    AssetCategory,
    AssetCollection,
    AssetType,
    MediaAsset,
)
from polymation.schemas.base import (
    SCHEMA_VERSION,
    PolymationModel,
    TimestampedDocument,
    VersionedDocument,
    utc_now,
)
from polymation.schemas.project import (
    FORMAT_DURATION_BOUNDS,
    ContentFormat,
    ContentPillar,
    ManifestPaths,
    PrimarySubject,
    ProductionStage,
    VideoProject,
    validate_project_id,
)
from polymation.schemas.rights import (
    ReviewStatus,
    RightsBasis,
    RightsLedger,
    RightsRecord,
)
from polymation.schemas.script import (
    ClaimVerification,
    FactualClaim,
    NarrationScene,
    NarrationStatus,
    PronunciationNote,
    ScriptApprovalStatus,
    ScriptDocument,
)
from polymation.schemas.storyboard import (
    MAX_SHOT_DURATION_SECONDS,
    MIN_SHOT_DURATION_SECONDS,
    FitMode,
    Storyboard,
    StoryboardScene,
    VisualShot,
)

__all__ = [
    "ALLOWED_CATEGORIES_FOR_TYPE",
    "DEFAULT_CATEGORY_FOR_TYPE",
    "FORMAT_DURATION_BOUNDS",
    "MAX_SHOT_DURATION_SECONDS",
    "MIN_SHOT_DURATION_SECONDS",
    "SCHEMA_VERSION",
    "ApprovalDecision",
    "ApprovalLedger",
    "ApprovalRecord",
    "ApprovalStage",
    "AssetCategory",
    "AssetCollection",
    "AssetType",
    "ClaimVerification",
    "ContentFormat",
    "ContentPillar",
    "FactualClaim",
    "FitMode",
    "ManifestPaths",
    "MediaAsset",
    "NarrationScene",
    "NarrationStatus",
    "PolymationModel",
    "PrimarySubject",
    "ProductionStage",
    "PronunciationNote",
    "ReviewStatus",
    "RightsBasis",
    "RightsLedger",
    "RightsRecord",
    "ScriptApprovalStatus",
    "ScriptDocument",
    "Storyboard",
    "StoryboardScene",
    "TimestampedDocument",
    "VersionedDocument",
    "VideoProject",
    "VisualShot",
    "utc_now",
    "validate_project_id",
]
