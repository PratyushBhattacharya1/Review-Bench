"""Runs a reviewer over a dataset and records what it found, and what it cost.

Kept separate from both the reviewers and the scorer so the three can vary
independently: a new adapter needs no runner change, and a new metric needs
no re-run. Predictions persist to JSONL for the same reason the dataset does
— scoring should be re-runnable without paying for inference twice.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

from reviewbench.models import Case
from reviewbench.pricing import estimate_cost_usd
from reviewbench.reviewers.base import Finding, Reviewer

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Prediction:
    """One reviewer's output for one case, with its cost and latency."""

    case_id: str
    reviewer: str
    model: str
    findings: list[dict[str, Any]] = field(default_factory=list)
    latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    # None means "no price on file for this model", which is different from
    # zero. Zero would read as free.
    estimated_cost_usd: float | None = None
    cached: bool = False
    error: str | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @staticmethod
    def from_json(line: str) -> "Prediction":
        return Prediction(**json.loads(line))

    @property
    def finding_objects(self) -> list[Finding]:
        return [Finding(**f) for f in self.findings]


def run_reviewer(reviewer: Reviewer, cases: Iterable[Case]) -> Iterator[Prediction]:
    """Review every case, yielding one Prediction each.

    Latency is wall-clock around the adapter call. Cache hits are timed and
    flagged too — a cached run's latency is not a claim about the model, and
    `cached` is what lets scoring exclude those from timing statistics.
    """
    for case in cases:
        started = time.perf_counter()
        result = reviewer.review(case)
        elapsed_ms = (time.perf_counter() - started) * 1000

        yield Prediction(
            case_id=case.id,
            reviewer=reviewer.name,
            model=reviewer.model,
            findings=[f.to_dict() for f in result.findings],
            latency_ms=round(elapsed_ms, 2),
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            estimated_cost_usd=estimate_cost_usd(
                reviewer.model, result.input_tokens, result.output_tokens
            ),
            cached=result.cached,
            error=result.error,
        )


def write_predictions(predictions: Iterable[Prediction], path: str | Path) -> int:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as f:
        for prediction in predictions:
            f.write(prediction.to_json() + "\n")
            count += 1
    return count


def read_predictions(path: str | Path) -> Iterator[Prediction]:
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield Prediction.from_json(line)
