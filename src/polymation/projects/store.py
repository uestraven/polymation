"""Reading and writing project documents on the local filesystem.

Versioned JSON manifests are the authoritative state of a video project. There
is no database in M0.1. Writes are atomic (temp file plus ``os.replace``) so an
interrupted write cannot leave a half-serialised manifest behind.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from pydantic import ValidationError

from polymation.config.paths import Workspace
from polymation.errors import (
    DocumentDecodeError,
    ProjectExistsError,
    ProjectNotFoundError,
)
from polymation.schemas.approvals import ApprovalLedger
from polymation.schemas.assets import AssetCollection
from polymation.schemas.base import TimestampedDocument, utc_now
from polymation.schemas.project import (
    ContentFormat,
    ContentPillar,
    ManifestPaths,
    PrimarySubject,
    ProductionStage,
    VideoProject,
    validate_project_id,
)
from polymation.schemas.rights import RightsLedger
from polymation.schemas.script import ScriptDocument
from polymation.schemas.storyboard import Storyboard
from polymation.validation.paths import resolve_within

DocumentT = TypeVar("DocumentT", bound=TimestampedDocument)

#: Written into brief.md for a newly created project.
BRIEF_TEMPLATE = """# {title}

> Draft brief. Nothing here has been researched, fact-checked or approved.

- **Project ID:** `{project_id}`
- **Content pillar:** {pillar}
- **Primary subject:** {subject}
- **Format:** {content_format}
- **Target duration:** {duration}s

## Editorial angle

_What specific question does this video answer, and why would a viewer care?_

## Key beats

1. _Hook._
2. _Development._
3. _Payoff._

## Open questions

- _What still needs research or a source?_

## Rights notes

- _Which visuals are needed, and where will original or cleared material come from?_
"""


def write_document(path: Path, document: TimestampedDocument) -> None:
    """Serialise ``document`` to ``path`` as readable JSON, atomically."""
    payload = document.model_dump(mode="json")
    rendered = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"

    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    try:
        with handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, path)
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise


def read_document(path: Path, model: type[DocumentT]) -> DocumentT:
    """Load and validate a JSON document of type ``model`` from ``path``.

    Raises:
        DocumentDecodeError: if the file is missing, not JSON, or fails schema
            validation.
        UnsupportedSchemaVersionError: if the document declares a schema version
            this build cannot read.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise DocumentDecodeError(
            f"Expected a {model.DOCUMENT_KIND} at {path}, but the file is missing."
        ) from exc
    except OSError as exc:
        raise DocumentDecodeError(f"Could not read {path}: {exc}") from exc

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DocumentDecodeError(
            f"{path} is not valid JSON (line {exc.lineno}, column {exc.colno}): {exc.msg}"
        ) from exc

    # Check the declared version before parsing the body, so a future document
    # produces a clear version message rather than confusing field errors.
    model.assert_readable_version(payload)

    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise DocumentDecodeError(f"{path} is not a valid {model.DOCUMENT_KIND}:\n{exc}") from exc


@dataclass
class ProjectBundle:
    """Every document belonging to one project, loaded together.

    Held as a unit because the documents are only meaningful against each other:
    a storyboard shot references an asset, which references a rights record.
    """

    workspace: Workspace
    root: Path
    manifest: VideoProject
    script: ScriptDocument
    storyboard: Storyboard
    assets: AssetCollection
    rights: RightsLedger
    approvals: ApprovalLedger

    @property
    def project_id(self) -> str:
        """The project's stable identifier."""
        return self.manifest.project_id

    def path_for(self, relative: str) -> Path:
        """Resolve a project-relative path inside this project, refusing escapes."""
        return resolve_within(self.root, relative)

    def asset_path(self, asset_path: str) -> Path:
        """Absolute location of an asset recorded at project-relative ``asset_path``."""
        return resolve_within(self.root, asset_path)

    def save(self) -> None:
        """Persist every document, refreshing the manifest's modification time."""
        self.manifest.touch()
        write_document(self.root / self.manifest.paths.script, self.script)
        write_document(self.root / self.manifest.paths.storyboard, self.storyboard)
        write_document(self.root / self.manifest.paths.assets, self.assets)
        write_document(self.root / self.manifest.paths.rights, self.rights)
        write_document(self.root / self.manifest.paths.approvals, self.approvals)
        write_document(self.root / "manifest.json", self.manifest)


class ProjectStore:
    """Filesystem-backed access to the projects in one workspace."""

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace

    def exists(self, project_id: str) -> bool:
        """Whether a project directory with a manifest exists for ``project_id``."""
        validate_project_id(project_id)
        return (self.workspace.project_dir(project_id) / "manifest.json").is_file()

    def list_project_ids(self) -> list[str]:
        """Identifiers of every project in the workspace, sorted.

        Directories without a ``manifest.json`` are ignored rather than treated
        as corrupt projects, so unrelated scratch directories do not break
        listing.
        """
        projects_dir = self.workspace.projects_dir
        if not projects_dir.is_dir():
            return []

        found: list[str] = []
        for entry in sorted(projects_dir.iterdir()):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            if (entry / "manifest.json").is_file():
                found.append(entry.name)
        return found

    def load(self, project_id: str) -> ProjectBundle:
        """Load every document for ``project_id``.

        Raises:
            ProjectNotFoundError: if no manifest exists for the identifier.
            DocumentDecodeError: if any document is unreadable or invalid.
        """
        validate_project_id(project_id)
        root = self.workspace.project_dir(project_id)

        if not (root / "manifest.json").is_file():
            raise ProjectNotFoundError(project_id)

        manifest = read_document(root / "manifest.json", VideoProject)

        if manifest.project_id != project_id:
            raise DocumentDecodeError(
                f"Project directory {root.name!r} holds a manifest claiming "
                f"project_id {manifest.project_id!r}. Refusing to guess which is "
                f"correct; rename the directory or correct the manifest."
            )

        return ProjectBundle(
            workspace=self.workspace,
            root=root,
            manifest=manifest,
            script=read_document(root / manifest.paths.script, ScriptDocument),
            storyboard=read_document(root / manifest.paths.storyboard, Storyboard),
            assets=read_document(root / manifest.paths.assets, AssetCollection),
            rights=read_document(root / manifest.paths.rights, RightsLedger),
            approvals=read_document(root / manifest.paths.approvals, ApprovalLedger),
        )

    def create(
        self,
        *,
        title: str,
        project_id: str,
        channel_id: str,
        content_pillar: ContentPillar,
        primary_subject: PrimarySubject,
        content_format: ContentFormat,
        target_duration_seconds: int,
        slug: str,
        language: str = "en",
    ) -> ProjectBundle:
        """Create a new project directory, scaffold and document set.

        Raises:
            ProjectExistsError: if a project directory already exists for the
                identifier. Existing projects are never overwritten.
        """
        validate_project_id(project_id)
        root = self.workspace.project_dir(project_id)

        if root.exists():
            raise ProjectExistsError(project_id)

        now = utc_now()
        manifest = VideoProject(
            project_id=project_id,
            title=title,
            slug=slug,
            channel_id=channel_id,
            content_pillar=content_pillar,
            primary_subject=primary_subject,
            content_format=content_format,
            language=language,
            target_duration_seconds=target_duration_seconds,
            production_stage=ProductionStage.DRAFT,
            paths=ManifestPaths(),
            created_at=now,
            updated_at=now,
        )

        self.workspace.ensure_project_scaffold(project_id)

        bundle = ProjectBundle(
            workspace=self.workspace,
            root=root,
            manifest=manifest,
            script=ScriptDocument(project_id=project_id, created_at=now, updated_at=now),
            storyboard=Storyboard(project_id=project_id, created_at=now, updated_at=now),
            assets=AssetCollection(project_id=project_id, created_at=now, updated_at=now),
            rights=RightsLedger(project_id=project_id, created_at=now, updated_at=now),
            approvals=ApprovalLedger(project_id=project_id, created_at=now, updated_at=now),
        )

        brief_path = root / manifest.paths.brief
        brief_path.write_text(
            BRIEF_TEMPLATE.format(
                title=title,
                project_id=project_id,
                pillar=content_pillar.value,
                subject=primary_subject.value,
                content_format=content_format.value,
                duration=target_duration_seconds,
            ),
            encoding="utf-8",
        )

        bundle.save()
        return bundle
