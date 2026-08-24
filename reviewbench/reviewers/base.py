"""The reviewer-side contract: what a code reviewer is, from the harness's
point of view.

`Reviewer` is a Protocol rather than a base class, matching
`StructuredModelClient` in `reviewbench.model_client`. That is what lets a
raw model call, a hosted product like PR-Agent, and a test fake all satisfy
the same interface without inheriting from anything, and it is what keeps
the runner and the scorer from knowing which is which.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol, runtime_checkable

from reviewbench.models import Case


@dataclass(frozen=True)
class Finding:
    """One issue a reviewer claims to have found.

    `line` is optional because reviewers differ: some cite a line, some only
    name a file. Scoring treats a missing line as "file-level only" rather
    than guessing, since inventing a line would manufacture localization
    accuracy the reviewer never demonstrated.
    """

    file_path: str
    message: str
    line: int | None = None
    severity: str | None = None
    category: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReviewResult:
    """A reviewer's output for one case, plus what it cost to get it.

    Usage rides along with the findings because cost per case is a
    first-class metric here, not an afterthought — it is the number that
    decides whether a reviewer is worth running on every PR, and almost no
    published benchmark reports it.
    """

    findings: list[Finding] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cached: bool = False
    error: str | None = None


@runtime_checkable
class Reviewer(Protocol):
    """Anything that can review a diff hunk and report findings."""

    name: str
    model: str

    def review(self, case: Case) -> ReviewResult:
        ...


REVIEW_SYSTEM_PROMPT = """You are reviewing a single diff hunk from a pull request.

Report only defects: bugs, correctness errors, security issues, resource \
leaks, race conditions, and missing error handling that would cause \
incorrect behaviour. Do not report style preferences, naming, formatting, \
or suggestions that do not change behaviour.

If the hunk contains no defect, return an empty findings list. Returning \
findings on clean code is a scored error, not a safe default — a reviewer \
that flags something on every hunk is worse than useless."""

FINDINGS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "line": {
                        "type": "integer",
                        "description": "Line number in the new version of the file, if identifiable.",
                    },
                    "message": {
                        "type": "string",
                        "description": "What the defect is and how it manifests.",
                    },
                    "severity": {
                        "type": "string",
                        "enum": ["critical", "high", "medium", "low"],
                    },
                    "category": {
                        "type": "string",
                        "enum": [
                            "correctness",
                            "security",
                            "resource_leak",
                            "concurrency",
                            "error_handling",
                            "performance",
                            "other",
                        ],
                    },
                },
                "required": ["message", "severity", "category"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["findings"],
    "additionalProperties": False,
}


def build_review_prompt(case: Case) -> str:
    """Render one case as a review request.

    Deliberately withholds provenance and label. Telling the reviewer this
    hunk came from a reverted PR would leak the answer.
    """
    return (
        f"Repository: {case.repo}\n"
        f"File: {case.file_path}\n\n"
        f"Diff hunk:\n```diff\n{case.diff_hunk}\n```"
    )


def parse_findings(payload: dict[str, Any], *, file_path: str) -> list[Finding]:
    """Convert a schema-conforming response into `Finding` objects."""
    findings: list[Finding] = []
    for raw in payload.get("findings") or []:
        message = (raw.get("message") or "").strip()
        if not message:
            continue
        line = raw.get("line")
        findings.append(
            Finding(
                file_path=file_path,
                message=message,
                line=int(line) if isinstance(line, int) else None,
                severity=raw.get("severity"),
                category=raw.get("category"),
            )
        )
    return findings
