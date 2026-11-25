"""Validation result types.

Deliberately dependency-free — this module imports nothing from the rest of the
package. Several layers produce findings (project validation, timeline
validation, caption parsing, render preflight), and if the result types lived
alongside the project validator then a pure text parser would have to import
the filesystem and checksum machinery to report a warning. Keeping them here
means any layer can produce a finding without inheriting the validator's
dependencies, and without creating an import cycle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Severity(str, Enum):
    """Whether a finding blocks progress or merely warrants attention."""

    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class Finding:
    """One validation result."""

    severity: Severity
    code: str
    message: str
    location: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable form, for machine-readable CLI output."""
        return {
            "severity": self.severity.value,
            "code": self.code,
            "message": self.message,
            "location": self.location,
        }


@dataclass
class ValidationReport:
    """The complete outcome of validating one project or timeline."""

    project_id: str
    findings: list[Finding] = field(default_factory=list)

    def add(self, severity: Severity, code: str, message: str, location: str | None = None) -> None:
        """Record a finding."""
        self.findings.append(
            Finding(severity=severity, code=code, message=message, location=location)
        )

    def error(self, code: str, message: str, location: str | None = None) -> None:
        """Record a blocking problem."""
        self.add(Severity.ERROR, code, message, location)

    def warning(self, code: str, message: str, location: str | None = None) -> None:
        """Record a non-blocking concern."""
        self.add(Severity.WARNING, code, message, location)

    def extend(self, findings: list[Finding]) -> None:
        """Absorb findings produced by another layer."""
        self.findings.extend(findings)

    @property
    def errors(self) -> list[Finding]:
        """Only the blocking findings."""
        return [item for item in self.findings if item.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[Finding]:
        """Only the non-blocking findings."""
        return [item for item in self.findings if item.severity is Severity.WARNING]

    @property
    def structurally_valid(self) -> bool:
        """Whether the subject's documents are internally consistent."""
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable form, for machine-readable CLI output."""
        return {
            "project_id": self.project_id,
            "structurally_valid": self.structurally_valid,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "findings": [finding.to_dict() for finding in self.findings],
        }


@dataclass
class RenderReadiness:
    """Whether a project can be rendered, and why not if it cannot."""

    render_ready: bool
    blockers: list[Finding] = field(default_factory=list)
    #: Concerns that do not stop a render but must not be ignored.
    cautions: list[Finding] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable form, for machine-readable CLI output."""
        return {
            "render_ready": self.render_ready,
            "blockers": [finding.to_dict() for finding in self.blockers],
            "cautions": [finding.to_dict() for finding in self.cautions],
            "publication_ready": None,
            "publication_note": (
                "Publication readiness is not evaluated. Publishing is out of scope "
                "and requires cleared rights plus recorded human approvals."
            ),
        }
