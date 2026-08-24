"""The dataset record schema.

One `Case` is one diff hunk with a ground-truth label. This is the unit
that both the dataset builder (this phase) and, later, the scorer operate
on. Keeping it a plain dataclass with a stable JSONL representation means
the schema is the contract between phases, not an implementation detail
buried in the miners.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Literal

Provenance = Literal["fixup", "review_comment", "synthetic"]
Label = Literal["defect", "no_defect"]


def make_case_id(*parts: str) -> str:
    """Stable id for a case, derived from its identifying fields so the
    same underlying hunk always maps to the same id across runs (this is
    what makes `write_jsonl`'s dedup meaningful).
    """
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()
    return digest[:16]


@dataclass(frozen=True)
class Case:
    """One evaluable unit: a diff hunk plus everything needed to judge a
    reviewer's output against it, and to trace the label back to its source.
    """

    id: str
    repo: str
    pr_number: int
    provenance: Provenance
    label: Label

    file_path: str
    diff_hunk: str
    base_commit_sha: str
    head_commit_sha: str

    defect_category: str | None = None
    context: str | None = None
    human_comment: str | None = None
    # Who wrote `human_comment`. Recorded because ground truth that contains
    # another code reviewer's output — CodeQL, or an AI review bot — makes
    # benchmarking a reviewer against it circular. `comment_author_type` is
    # GitHub's own "User"/"Bot" classification, which is necessary but not
    # sufficient: AI review tools post from user accounts too (see
    # `is_machine_authored`).
    comment_author: str | None = None
    comment_author_type: str | None = None
    source_urls: list[str] = field(default_factory=list)
    created_at: str | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @staticmethod
    def from_json(line: str) -> "Case":
        return Case(**json.loads(line))


def write_jsonl(cases: Iterable[Case], path: str | Path) -> int:
    """Write cases to `path` as JSONL, deduplicated by `id`. Returns the
    count of cases actually written.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    seen: set[str] = set()
    count = 0
    with path.open("w", encoding="utf-8") as f:
        for case in cases:
            if case.id in seen:
                continue
            seen.add(case.id)
            f.write(case.to_json() + "\n")
            count += 1
    return count


def read_jsonl(path: str | Path) -> Iterator[Case]:
    path = Path(path)
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield Case.from_json(line)
