"""Media inspection, checksums and asset import."""

from __future__ import annotations

import wave
from pathlib import Path

import pytest
from PIL import Image

from polymation.errors import (
    DuplicateAssetError,
    MediaInspectionError,
    UnsafePathError,
)
from polymation.media.checksum import sha256_bytes, sha256_file, sha256_text
from polymation.media.importer import import_asset
from polymation.media.inspect import inspect_media
from polymation.projects.store import ProjectBundle, ProjectStore
from polymation.schemas.assets import AssetCategory, AssetType
from polymation.schemas.rights import ReviewStatus, RightsBasis

#: sha256 of the empty string, from the FIPS 180-4 test vectors.
EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

#: sha256("abc"), also from the FIPS 180-4 test vectors.
ABC_SHA256 = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


# --- checksums -----------------------------------------------------------------


def test_checksum_matches_published_test_vectors() -> None:
    assert sha256_bytes(b"") == EMPTY_SHA256
    assert sha256_bytes(b"abc") == ABC_SHA256
    assert sha256_text("abc") == ABC_SHA256


def test_file_checksum_matches_the_byte_checksum(tmp_path: Path) -> None:
    path = tmp_path / "data.bin"
    payload = b"abc"
    path.write_bytes(payload)
    assert sha256_file(path) == ABC_SHA256


def test_file_checksum_handles_content_larger_than_one_chunk(tmp_path: Path) -> None:
    from polymation.media.checksum import CHUNK_SIZE

    payload = b"polymation" * (CHUNK_SIZE // 5)
    path = tmp_path / "large.bin"
    path.write_bytes(payload)
    assert sha256_file(path) == sha256_bytes(payload)


def test_checksum_changes_when_content_changes(tmp_path: Path) -> None:
    path = tmp_path / "data.bin"
    path.write_bytes(b"before")
    before = sha256_file(path)
    path.write_bytes(b"after")
    assert sha256_file(path) != before


# --- inspection ----------------------------------------------------------------


def test_png_is_detected_with_dimensions(png_file: Path) -> None:
    probe = inspect_media(png_file)
    assert probe.asset_type is AssetType.IMAGE
    assert probe.detected_format == "PNG"
    assert probe.mime_type == "image/png"
    assert (probe.width, probe.height) == (64, 48)
    assert probe.size_bytes == png_file.stat().st_size


def test_wav_is_detected_with_duration_and_sample_rate(wav_file: Path) -> None:
    probe = inspect_media(wav_file)
    assert probe.asset_type is AssetType.AUDIO
    assert probe.detected_format == "wav"
    assert probe.audio_sample_rate == 48_000
    assert probe.duration_seconds == pytest.approx(0.1)
    assert probe.width is None and probe.height is None


def test_a_misleading_extension_does_not_decide_the_format(tmp_path: Path) -> None:
    """A PNG named .wav is reported as an image, from its content."""
    path = tmp_path / "actually-a-png.wav"
    Image.new("RGB", (10, 10), (0, 0, 0)).save(path, format="PNG")

    probe = inspect_media(path)
    assert probe.asset_type is AssetType.IMAGE
    assert probe.detected_format == "PNG"


def test_a_wav_named_png_is_reported_as_audio(tmp_path: Path) -> None:
    path = tmp_path / "actually-a-wav.png"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(44_100)
        handle.writeframes(b"\x00\x00" * 4_410)

    probe = inspect_media(path)
    assert probe.asset_type is AssetType.AUDIO
    assert probe.detected_format == "wav"


def test_an_empty_file_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "empty.png"
    path.write_bytes(b"")

    with pytest.raises(MediaInspectionError, match="empty"):
        inspect_media(path)


def test_a_missing_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(MediaInspectionError, match="does not exist"):
        inspect_media(tmp_path / "nope.png")


def test_a_directory_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(MediaInspectionError, match="is a directory"):
        inspect_media(tmp_path)


def test_a_truncated_png_is_rejected(tmp_path: Path) -> None:
    """Corrupt media must be refused, not imported with guessed metadata."""
    source = tmp_path / "good.png"
    Image.new("RGB", (64, 64), (1, 2, 3)).save(source, format="PNG")

    truncated = tmp_path / "truncated.png"
    truncated.write_bytes(source.read_bytes()[: len(source.read_bytes()) // 2])

    with pytest.raises(MediaInspectionError):
        inspect_media(truncated)


def test_unrecognisable_content_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "random.bin"
    path.write_bytes(b"this is not media at all, by any reading of the bytes")

    # Without ffprobe this raises MissingDependencyError; with it, the file is
    # still refused. Either way it is never silently accepted.
    with pytest.raises((MediaInspectionError, Exception)) as caught:
        inspect_media(path)
    assert "random.bin" in str(caught.value)


# --- import --------------------------------------------------------------------


def test_import_copies_the_file_into_the_project(project: ProjectBundle, png_file: Path) -> None:
    result = import_asset(project, png_file)

    assert result.destination.is_file()
    assert result.destination.parent == project.root / "assets" / "images"
    assert result.asset.path == "assets/images/swatch.png"
    # Source is untouched.
    assert png_file.is_file()


def test_import_records_checksum_size_and_dimensions(
    project: ProjectBundle, png_file: Path
) -> None:
    result = import_asset(project, png_file)

    assert result.asset.sha256 == sha256_file(png_file)
    assert result.asset.size_bytes == png_file.stat().st_size
    assert (result.asset.width, result.asset.height) == (64, 48)
    assert result.asset.detected_format == "PNG"


def test_import_creates_an_unreviewed_rights_record(project: ProjectBundle, png_file: Path) -> None:
    result = import_asset(project, png_file)

    assert result.rights.review_status is ReviewStatus.UNREVIEWED
    assert result.rights.asset_id == result.asset.asset_id
    assert result.asset.rights_record_id == result.rights.rights_record_id
    assert not result.rights.is_cleared_for_use


def test_user_supplied_material_is_never_auto_approved(
    project: ProjectBundle, png_file: Path
) -> None:
    result = import_asset(project, png_file, rights_basis=RightsBasis.USER_SUPPLIED)
    assert result.rights.basis is RightsBasis.USER_SUPPLIED
    assert result.rights.review_status is ReviewStatus.UNREVIEWED


def test_claiming_original_work_still_requires_review(
    project: ProjectBundle, png_file: Path
) -> None:
    """A basis is a provenance claim, not clearance: the record stays unreviewed."""
    result = import_asset(project, png_file, rights_basis=RightsBasis.ORIGINAL_WORK)
    assert result.rights.review_status is ReviewStatus.UNREVIEWED


def test_duplicate_content_is_refused_by_checksum(
    project: ProjectBundle, png_file: Path, tmp_path: Path
) -> None:
    import_asset(project, png_file)

    # Same bytes, different filename: still a duplicate.
    copy = tmp_path / "source" / "renamed.png"
    copy.write_bytes(png_file.read_bytes())

    with pytest.raises(DuplicateAssetError) as caught:
        import_asset(project, copy)

    assert "asset-0001" in str(caught.value)
    assert sha256_file(png_file) in str(caught.value)
    assert len(project.assets.assets) == 1


def test_duplicate_content_can_be_imported_deliberately(
    project: ProjectBundle, png_file: Path, tmp_path: Path
) -> None:
    import_asset(project, png_file)
    copy = tmp_path / "source" / "renamed.png"
    copy.write_bytes(png_file.read_bytes())

    result = import_asset(project, copy, allow_duplicate=True)

    assert len(project.assets.assets) == 2
    assert result.asset.asset_id == "asset-0002"
    assert result.asset.sha256 == project.assets.assets[0].sha256


def test_a_filename_collision_renames_rather_than_overwrites(
    project: ProjectBundle, png_file: Path, tmp_path: Path
) -> None:
    first = import_asset(project, png_file)
    first_bytes = first.destination.read_bytes()

    # Different content, same filename.
    other = tmp_path / "other" / "swatch.png"
    other.parent.mkdir(parents=True)
    Image.new("RGB", (32, 32), (200, 100, 50)).save(other, format="PNG")

    second = import_asset(project, other)

    assert second.destination.name == "swatch-1.png"
    assert second.was_renamed
    assert second.renamed_from == "swatch.png"
    # The first file was not touched.
    assert first.destination.read_bytes() == first_bytes


@pytest.mark.parametrize(
    "destination_name",
    ["../escape.png", "../../escape.png", "a/b.png", "/absolute.png", "..", "."],
)
def test_import_rejects_unsafe_destination_names(
    project: ProjectBundle, png_file: Path, destination_name: str
) -> None:
    with pytest.raises(UnsafePathError):
        import_asset(project, png_file, destination_name=destination_name)

    assert project.assets.assets == []


def test_import_rejects_a_destination_name_that_escapes_via_traversal(
    project: ProjectBundle, png_file: Path, tmp_path: Path
) -> None:
    """Nothing may be written outside the project's asset directory."""
    with pytest.raises(UnsafePathError):
        import_asset(project, png_file, destination_name="../../../../../../tmp/pwned.png")

    assert not (Path("/tmp") / "pwned.png").exists()


def test_audio_import_defaults_to_the_music_directory(
    project: ProjectBundle, wav_file: Path
) -> None:
    result = import_asset(project, wav_file)
    assert result.asset.category is AssetCategory.MUSIC
    assert result.asset.path == "assets/music/tone.wav"
    assert result.asset.duration_seconds == pytest.approx(0.1)


def test_audio_can_be_filed_as_narration(project: ProjectBundle, wav_file: Path) -> None:
    result = import_asset(project, wav_file, category=AssetCategory.NARRATION)
    assert result.asset.path == "assets/narration/tone.wav"


def test_an_image_cannot_be_filed_under_an_audio_category(
    project: ProjectBundle, png_file: Path
) -> None:
    with pytest.raises(MediaInspectionError, match="cannot be filed under"):
        import_asset(project, png_file, category=AssetCategory.MUSIC)


def test_identifiers_increment_across_imports(
    project: ProjectBundle, png_file: Path, wav_file: Path
) -> None:
    first = import_asset(project, png_file)
    second = import_asset(project, wav_file)

    assert first.asset.asset_id == "asset-0001"
    assert second.asset.asset_id == "asset-0002"
    assert first.rights.rights_record_id == "rights-0001"
    assert second.rights.rights_record_id == "rights-0002"


def test_imported_assets_survive_a_save_and_reload(
    store: ProjectStore, project: ProjectBundle, png_file: Path
) -> None:
    result = import_asset(project, png_file)
    project.save()

    reloaded = store.load(project.project_id)
    assert len(reloaded.assets.assets) == 1
    assert reloaded.assets.assets[0] == result.asset
    assert reloaded.rights.records[0] == result.rights


def test_import_records_provenance_fields(project: ProjectBundle, png_file: Path) -> None:
    result = import_asset(
        project,
        png_file,
        original_source="Drawn by hand in 2026",
        rights_holder="A Person",
        intended_editorial_use="Scene 2 b-roll",
        ai_generated=True,
    )

    assert result.asset.original_source == "Drawn by hand in 2026"
    assert result.rights.rights_holder == "A Person"
    assert result.rights.intended_editorial_use == "Scene 2 b-roll"
    assert result.rights.ai_generated is True
