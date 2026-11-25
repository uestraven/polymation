"""Exception hierarchy and process exit codes shared by every CLI command."""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    """Process exit codes used consistently across all CLI commands.

    Code 2 is deliberately skipped: Typer/Click reserves it for CLI usage
    errors (unknown option, missing argument).
    """

    OK = 0
    ERROR = 1
    VALIDATION_FAILED = 3
    MISSING_DEPENDENCY = 4
    NOT_FOUND = 5
    CONFLICT = 6
    UNSAFE_INPUT = 7


class PolymationError(Exception):
    """Base class for all errors raised by Polymation Studio."""

    exit_code: ExitCode = ExitCode.ERROR


class ConfigurationError(PolymationError):
    """Channel or workspace configuration is missing or malformed."""


class UnsupportedSchemaVersionError(PolymationError):
    """A stored document declares a schema version this build cannot read."""

    exit_code = ExitCode.VALIDATION_FAILED

    def __init__(self, *, document_kind: str, found: str, supported: frozenset[str]) -> None:
        supported_list = ", ".join(sorted(supported))
        super().__init__(
            f"{document_kind} declares schema_version {found!r}, which this build of "
            f"Polymation Studio cannot read. Supported versions: {supported_list}. "
            f"Upgrade Polymation Studio, or migrate the document to a supported version."
        )
        self.document_kind = document_kind
        self.found = found
        self.supported = supported


class DocumentDecodeError(PolymationError):
    """A stored JSON document is unreadable or fails schema validation."""

    exit_code = ExitCode.VALIDATION_FAILED


class ProjectNotFoundError(PolymationError):
    """The requested project identifier does not exist in the workspace."""

    exit_code = ExitCode.NOT_FOUND

    def __init__(self, project_id: str) -> None:
        super().__init__(
            f"No project with identifier {project_id!r} was found. "
            f"Run `polymation list` to see the available projects."
        )
        self.project_id = project_id


class ProjectExistsError(PolymationError):
    """Refused to overwrite an existing project directory."""

    exit_code = ExitCode.CONFLICT

    def __init__(self, project_id: str) -> None:
        super().__init__(
            f"A project with identifier {project_id!r} already exists and will not be "
            f"overwritten. Choose a different identifier, or delete the existing "
            f"project directory deliberately."
        )
        self.project_id = project_id


class InvalidProjectIdError(PolymationError):
    """A project identifier is not a safe, well-formed identifier."""

    exit_code = ExitCode.UNSAFE_INPUT


class UnsafePathError(PolymationError, ValueError):
    """A path escapes its permitted root, or is otherwise unusable as a reference.

    Also a ``ValueError`` so that Pydantic field validators which reject a stored
    path surface it as a normal ``ValidationError`` alongside other field
    problems, rather than escaping the model-validation boundary.
    """

    exit_code = ExitCode.UNSAFE_INPUT


class MediaInspectionError(PolymationError):
    """A media file could not be inspected, or is an unsupported format."""


class DuplicateAssetError(PolymationError):
    """An asset with identical content has already been imported."""

    exit_code = ExitCode.CONFLICT

    def __init__(self, *, checksum: str, existing_asset_id: str, existing_path: str) -> None:
        super().__init__(
            f"This file is byte-identical to already-imported asset {existing_asset_id} "
            f"({existing_path}); sha256={checksum}. Pass --allow-duplicate to import a "
            f"second copy anyway."
        )
        self.checksum = checksum
        self.existing_asset_id = existing_asset_id
        self.existing_path = existing_path


class MissingDependencyError(PolymationError):
    """A required external tool (FFmpeg, ffprobe) is unavailable."""

    exit_code = ExitCode.MISSING_DEPENDENCY


class RenderError(PolymationError):
    """Rendering failed, or the project is not render-ready."""


class NotRenderReadyError(RenderError):
    """A project is structurally valid but cannot be rendered."""

    exit_code = ExitCode.VALIDATION_FAILED
