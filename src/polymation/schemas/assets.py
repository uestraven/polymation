"""Media asset records: what a file is, where it came from, and how it hashes."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import Enum
from typing import ClassVar

from pydantic import Field, field_serializer, field_validator, model_validator

from polymation.schemas.base import SCHEMA_VERSION, PolymationModel, TimestampedDocument
from polymation.validation.paths import validate_project_relative_path

#: Asset identifiers are generated, not user-supplied, but are validated anyway
#: because they are read back from disk.
ASSET_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")

#: Lowercase hex sha256.
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class AssetType(str, Enum):
    """The kind of media an asset holds, as detected by inspection."""

    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"


class AssetCategory(str, Enum):
    """Editorial role of an asset, which decides its directory inside the project."""

    IMAGES = "images"
    FOOTAGE = "footage"
    NARRATION = "narration"
    GRAPHICS = "graphics"
    MUSIC = "music"


#: Default category for each detected asset type. The operator may override the
#: category at import time (narration audio vs music, graphics vs images).
DEFAULT_CATEGORY_FOR_TYPE: dict[AssetType, AssetCategory] = {
    AssetType.IMAGE: AssetCategory.IMAGES,
    AssetType.VIDEO: AssetCategory.FOOTAGE,
    AssetType.AUDIO: AssetCategory.MUSIC,
}

#: Categories that may legitimately hold each asset type.
ALLOWED_CATEGORIES_FOR_TYPE: dict[AssetType, frozenset[AssetCategory]] = {
    AssetType.IMAGE: frozenset({AssetCategory.IMAGES, AssetCategory.GRAPHICS}),
    AssetType.VIDEO: frozenset({AssetCategory.FOOTAGE}),
    AssetType.AUDIO: frozenset({AssetCategory.NARRATION, AssetCategory.MUSIC}),
}


class MediaAsset(PolymationModel):
    """One imported media file.

    ``path`` is always project-relative with POSIX separators, so a project
    directory can be moved or archived without rewriting its manifests. The
    checksum is the identity of the bytes: validation re-hashes the file and
    reports a mismatch rather than trusting the manifest.
    """

    asset_id: str
    asset_type: AssetType
    category: AssetCategory
    path: str
    detected_format: str = Field(min_length=1, max_length=64)
    mime_type: str | None = None
    size_bytes: int = Field(ge=0)
    sha256: str
    width: int | None = Field(default=None, gt=0, le=100_000)
    height: int | None = Field(default=None, gt=0, le=100_000)
    duration_seconds: float | None = Field(default=None, gt=0, le=86_400)
    original_source: str = Field(min_length=1, max_length=1024)
    acquired_at: datetime
    rights_record_id: str

    @field_validator("asset_id", "rights_record_id")
    @classmethod
    def _check_identifier(cls, value: str) -> str:
        if not ASSET_ID_PATTERN.match(value):
            raise ValueError(f"identifier {value!r} is invalid")
        return value

    @field_validator("sha256")
    @classmethod
    def _check_checksum(cls, value: str) -> str:
        if not SHA256_PATTERN.match(value):
            raise ValueError(f"sha256 {value!r} is not a 64-character lowercase hexadecimal digest")
        return value

    @field_validator("path")
    @classmethod
    def _check_path(cls, value: str) -> str:
        return str(validate_project_relative_path(value))

    @field_validator("acquired_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("acquired_at must be timezone-aware (store UTC)")
        return value.astimezone(UTC)

    @field_serializer("acquired_at")
    def _serialize_acquired_at(self, value: datetime) -> str:
        return value.astimezone(UTC).isoformat()

    @model_validator(mode="after")
    def _check_type_consistency(self) -> MediaAsset:
        allowed = ALLOWED_CATEGORIES_FOR_TYPE[self.asset_type]
        if self.category not in allowed:
            permitted = ", ".join(sorted(category.value for category in allowed))
            raise ValueError(
                f"category {self.category.value!r} is not valid for a "
                f"{self.asset_type.value} asset; permitted: {permitted}"
            )

        if self.asset_type is AssetType.IMAGE and (self.width is None or self.height is None):
            raise ValueError("image assets must record width and height")

        if self.asset_type is AssetType.AUDIO and (
            self.width is not None or self.height is not None
        ):
            raise ValueError("audio assets must not record pixel dimensions")

        if self.asset_type in {AssetType.VIDEO, AssetType.AUDIO} and self.duration_seconds is None:
            raise ValueError(f"{self.asset_type.value} assets must record a duration")

        return self


class AssetCollection(TimestampedDocument):
    """``projects/<project_id>/assets.json``."""

    DOCUMENT_KIND: ClassVar[str] = "asset collection"
    SUPPORTED_SCHEMA_VERSIONS: ClassVar[frozenset[str]] = frozenset({SCHEMA_VERSION})

    project_id: str
    assets: list[MediaAsset] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_unique(self) -> AssetCollection:
        asset_ids: set[str] = set()
        paths: set[str] = set()
        for asset in self.assets:
            if asset.asset_id in asset_ids:
                raise ValueError(f"duplicate asset_id {asset.asset_id!r}")
            asset_ids.add(asset.asset_id)
            if asset.path in paths:
                raise ValueError(f"two assets claim the same path {asset.path!r}")
            paths.add(asset.path)
        return self

    def by_id(self, asset_id: str) -> MediaAsset | None:
        """Return the asset with ``asset_id``, or ``None``."""
        for asset in self.assets:
            if asset.asset_id == asset_id:
                return asset
        return None

    def by_checksum(self, sha256: str) -> MediaAsset | None:
        """Return the first asset whose content hash is ``sha256``, or ``None``."""
        for asset in self.assets:
            if asset.sha256 == sha256:
                return asset
        return None

    @property
    def asset_ids(self) -> set[str]:
        """Identifiers of every asset in the collection."""
        return {asset.asset_id for asset in self.assets}

    def counts_by_type(self) -> dict[str, int]:
        """Number of assets of each type, including types with no assets."""
        counts = {asset_type.value: 0 for asset_type in AssetType}
        for asset in self.assets:
            counts[asset.asset_type.value] += 1
        return counts
