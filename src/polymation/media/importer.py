"""Importing local media files into a project.

Guarantees provided here:

* the destination path is always inside the project's asset directory;
* an existing file is never overwritten — a colliding name is suffixed;
* byte-identical content is detected by checksum and refused by default;
* every imported asset gets an ``unreviewed`` rights record, because an import
  is an act of acquisition, not an act of clearance.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from polymation.errors import (
    DuplicateAssetError,
    MediaInspectionError,
    UnsafePathError,
)
from polymation.media.checksum import sha256_file
from polymation.media.inspect import MediaProbe, inspect_media
from polymation.projects.store import ProjectBundle
from polymation.schemas.assets import (
    ALLOWED_CATEGORIES_FOR_TYPE,
    DEFAULT_CATEGORY_FOR_TYPE,
    AssetCategory,
    AssetType,
    MediaAsset,
)
from polymation.schemas.base import utc_now
from polymation.schemas.rights import ReviewStatus, RightsBasis, RightsRecord
from polymation.validation.paths import to_project_relative, validate_filename

#: Maximum suffixes tried when resolving a filename collision.
MAX_COLLISION_ATTEMPTS = 1000

#: Default editorial-use note recorded for an import with no explicit note.
DEFAULT_INTENDED_USE = "Unspecified — recorded at import; requires editorial review."


@dataclass(frozen=True)
class ImportResult:
    """What an import produced."""

    asset: MediaAsset
    rights: RightsRecord
    probe: MediaProbe
    destination: Path
    renamed_from: str | None

    @property
    def was_renamed(self) -> bool:
        """Whether the destination filename differed from the requested one."""
        return self.renamed_from is not None


def _next_identifier(prefix: str, used: set[str]) -> str:
    index = 1
    while True:
        candidate = f"{prefix}-{index:04d}"
        if candidate not in used:
            return candidate
        index += 1


def _resolve_destination(directory: Path, filename: str) -> tuple[Path, str | None]:
    """Return a non-existing destination path, suffixing on collision."""
    candidate = directory / filename
    if not candidate.exists():
        return candidate, None

    stem = Path(filename).stem
    suffix = Path(filename).suffix

    for index in range(1, MAX_COLLISION_ATTEMPTS + 1):
        alternative = directory / f"{stem}-{index}{suffix}"
        if not alternative.exists():
            return alternative, filename

    raise UnsafePathError(
        f"Could not find a free filename for {filename!r} in {directory} after "
        f"{MAX_COLLISION_ATTEMPTS} attempts. Clean up the directory first."
    )


def import_asset(
    bundle: ProjectBundle,
    source: Path,
    *,
    category: AssetCategory | None = None,
    destination_name: str | None = None,
    original_source: str | None = None,
    rights_basis: RightsBasis = RightsBasis.USER_SUPPLIED,
    rights_holder: str | None = None,
    license_identifier: str | None = None,
    intended_editorial_use: str | None = None,
    ai_generated: bool = False,
    allow_duplicate: bool = False,
) -> ImportResult:
    """Copy ``source`` into ``bundle``'s asset directory and record it.

    The bundle is mutated in memory; the caller is responsible for calling
    :meth:`ProjectBundle.save`.

    Raises:
        MediaInspectionError: if the file is unreadable or an unsupported format.
        DuplicateAssetError: if identical content is already imported and
            ``allow_duplicate`` is false.
        UnsafePathError: if ``destination_name`` is not a safe bare filename.
        MissingDependencyError: if identifying the file would require ffprobe
            and it is unavailable.
    """
    source_path = Path(source).expanduser()

    probe = inspect_media(source_path)
    checksum = sha256_file(source_path)

    existing = bundle.assets.by_checksum(checksum)
    if existing is not None and not allow_duplicate:
        raise DuplicateAssetError(
            checksum=checksum,
            existing_asset_id=existing.asset_id,
            existing_path=existing.path,
        )

    resolved_category = category or DEFAULT_CATEGORY_FOR_TYPE[probe.asset_type]
    allowed = ALLOWED_CATEGORIES_FOR_TYPE[probe.asset_type]
    if resolved_category not in allowed:
        permitted = ", ".join(sorted(item.value for item in allowed))
        raise MediaInspectionError(
            f"{source_path.name} was detected as {probe.asset_type.value} media, which "
            f"cannot be filed under {resolved_category.value!r}. Permitted: {permitted}."
        )

    requested_name = validate_filename(destination_name or source_path.name)

    category_directory = bundle.path_for(
        f"{bundle.manifest.paths.assets_dir}/{resolved_category.value}"
    )
    category_directory.mkdir(parents=True, exist_ok=True)

    destination, renamed_from = _resolve_destination(category_directory, requested_name)

    # copy2 preserves mtime, which keeps the acquisition record honest about the
    # source file rather than the copy.
    shutil.copy2(source_path, destination)

    copied_checksum = sha256_file(destination)
    if copied_checksum != checksum:
        destination.unlink(missing_ok=True)
        raise MediaInspectionError(
            f"The copy of {source_path.name} did not match the source checksum "
            f"(source {checksum}, copy {copied_checksum}). The copy was removed; "
            f"check the source file and the destination filesystem."
        )

    relative_path = to_project_relative(bundle.root, destination)

    asset_id = _next_identifier("asset", bundle.assets.asset_ids)
    rights_record_id = _next_identifier(
        "rights", {record.rights_record_id for record in bundle.rights.records}
    )
    now = utc_now()

    asset = MediaAsset(
        asset_id=asset_id,
        asset_type=probe.asset_type,
        category=resolved_category,
        path=str(relative_path),
        detected_format=probe.detected_format,
        mime_type=probe.mime_type,
        size_bytes=probe.size_bytes,
        sha256=checksum,
        width=probe.width,
        height=probe.height,
        duration_seconds=probe.duration_seconds,
        original_source=original_source or str(source_path),
        acquired_at=now,
        rights_record_id=rights_record_id,
    )

    # Imports are never auto-approved. A local file of unknown provenance is
    # user-supplied material awaiting human review.
    rights = RightsRecord(
        rights_record_id=rights_record_id,
        asset_id=asset_id,
        source=original_source or str(source_path),
        rights_holder=rights_holder,
        basis=rights_basis,
        license_identifier=license_identifier,
        intended_editorial_use=intended_editorial_use or DEFAULT_INTENDED_USE,
        ai_generated=ai_generated,
        review_status=ReviewStatus.UNREVIEWED,
        human_approval=None,
        recorded_at=now,
    )

    bundle.assets.assets = [*bundle.assets.assets, asset]
    bundle.assets.touch()
    bundle.rights.records = [*bundle.rights.records, rights]
    bundle.rights.touch()

    return ImportResult(
        asset=asset,
        rights=rights,
        probe=probe,
        destination=destination,
        renamed_from=renamed_from,
    )


def asset_type_summary(bundle: ProjectBundle) -> dict[str, int]:
    """Count of assets by type for ``bundle``, including types with none."""
    counts = {asset_type.value: 0 for asset_type in AssetType}
    for asset in bundle.assets.assets:
        counts[asset.asset_type.value] += 1
    return counts
