"""Finding/Report primitives shared by every GAUNTLET checker.

Extracted from validate.py so that the field registry (R-01) and lifecycle
machinery (R-02) can report findings without importing the validators —
checkers depend on findings, never on each other.

Every Finding carries the specification section it enforces. Sections are
cited as "BS x.y" (Benchmark Specification) or "CAS x.y" (Corpus Authoring
Specification); bare numbers predate the CAS and refer to the BS.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Severity(str, Enum):
    ERROR = "error"      # blocks release
    WARNING = "warning"  # requires a documented decision


@dataclass(frozen=True)
class Finding:
    severity: Severity
    section: str          # governing specification section
    code: str
    message: str
    sample_id: str | None = None
    location: str | None = None

    def __str__(self) -> str:
        where = f" [{self.sample_id or self.location}]" if (self.sample_id or self.location) else ""
        return f"{self.severity.value.upper():7} {self.code:26} §{self.section}{where}: {self.message}"


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    checked: int = 0

    def add(self, severity, section, code, message, sample_id=None, location=None) -> None:
        self.findings.append(Finding(severity, section, code, message, sample_id, location))

    def error(self, *a, **kw) -> None:
        self.add(Severity.ERROR, *a, **kw)

    def warn(self, *a, **kw) -> None:
        self.add(Severity.WARNING, *a, **kw)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.WARNING]

    @property
    def ok(self) -> bool:
        return not self.errors

    def by_code(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for f in self.findings:
            out[f.code] = out.get(f.code, 0) + 1
        return out

    def extend(self, other: "Report") -> None:
        self.findings.extend(other.findings)
        self.checked += other.checked
