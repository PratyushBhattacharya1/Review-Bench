"""Scores predictions against a dataset.

Metrics follow docs/DESIGN.md: detection, localization, noise rate, and
cost — always broken out by provenance, never pooled. Pooling is the thing
this project exists not to do, because the three sources have genuinely
different label quality and a blended number hides exactly the gap that is
interesting.
"""

from __future__ import annotations

import logging
import re
import statistics
from dataclasses import asdict, dataclass, field
from typing import Iterable

from reviewbench.models import Case
from reviewbench.runner import Prediction

logger = logging.getLogger(__name__)

# "@@ -12,7 +12,9 @@" -> the new-file range starts at line 12 and spans 9.
_HUNK_HEADER_RE = re.compile(r"^@@\s+-\d+(?:,\d+)?\s+\+(\d+)(?:,(\d+))?\s+@@", re.MULTILINE)


def hunk_line_range(diff_hunk: str) -> tuple[int, int] | None:
    """New-file line range covered by `diff_hunk`, or None if unparseable.

    Spans every header in the hunk, since GitHub's `diff_hunk` field can
    carry more than one.
    """
    starts: list[int] = []
    ends: list[int] = []
    for match in _HUNK_HEADER_RE.finditer(diff_hunk or ""):
        start = int(match.group(1))
        span = int(match.group(2)) if match.group(2) else 1
        starts.append(start)
        ends.append(start + max(span, 1) - 1)
    if not starts:
        return None
    return min(starts), max(ends)


@dataclass
class ProvenanceScore:
    """Scores for one provenance.

    Reports raw counts alongside the ratios on purpose: a precision of 1.0
    over two cases is not the same claim as a precision of 1.0 over two
    hundred, and only the counts show that.
    """

    provenance: str
    n_defect: int = 0
    n_no_defect: int = 0
    true_positives: int = 0
    false_negatives: int = 0
    false_positives: int = 0
    true_negatives: int = 0
    findings_on_clean: int = 0
    findings_with_line: int = 0
    findings_inside_hunk: int = 0
    errors: int = 0
    latencies_ms: list[float] = field(default_factory=list)
    total_cost_usd: float = 0.0
    cost_unavailable: int = 0

    @property
    def precision(self) -> float | None:
        denom = self.true_positives + self.false_positives
        return self.true_positives / denom if denom else None

    @property
    def recall(self) -> float | None:
        denom = self.true_positives + self.false_negatives
        return self.true_positives / denom if denom else None

    @property
    def f1(self) -> float | None:
        p, r = self.precision, self.recall
        if p is None or r is None or (p + r) == 0:
            return None
        return 2 * p * r / (p + r)

    @property
    def noise_rate(self) -> float | None:
        """Mean findings emitted per clean case.

        The metric users care about most and vendors report least. A
        reviewer that flags something on every hunk scores well on recall
        and is unusable; this is what catches it.
        """
        return self.findings_on_clean / self.n_no_defect if self.n_no_defect else None

    @property
    def localization_rate(self) -> float | None:
        """Share of line-bearing findings landing inside the case's hunk.

        A weak proxy, and labelled as one. It measures "pointed somewhere in
        the right region", not "identified the right bug" — `fixup` ground
        truth knows the file but not the offending line, so nothing stronger
        is computable from it. Semantic matching needs the LLM judge.
        """
        if not self.findings_with_line:
            return None
        return self.findings_inside_hunk / self.findings_with_line

    @property
    def mean_latency_ms(self) -> float | None:
        return statistics.mean(self.latencies_ms) if self.latencies_ms else None

    @property
    def caveats(self) -> list[str]:
        """Reasons this row's ratios are not interpretable as they look.

        A provenance holding only one label still produces numbers, and they
        are the most misleading kind: `fixup` emits positives only, so its
        precision is 100% by construction — there are no clean cases it
        could have been wrong about — and its noise rate is uncomputable.
        Printing that beside a genuine precision invites a false comparison,
        so it gets flagged in the report rather than silently rendered.
        """
        notes: list[str] = []
        if self.n_no_defect == 0 and self.n_defect:
            notes.append(
                "no clean cases in this provenance — precision is 100% by construction "
                "and noise rate is uncomputable"
            )
        if self.n_defect == 0 and self.n_no_defect:
            notes.append(
                "no defect cases in this provenance — recall and F1 are undefined, and "
                "precision can only ever be 0%"
            )
        return notes

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("latencies_ms")
        d.update(
            precision=self.precision,
            recall=self.recall,
            f1=self.f1,
            noise_rate=self.noise_rate,
            localization_rate=self.localization_rate,
            mean_latency_ms=self.mean_latency_ms,
        )
        return d


def score(cases: Iterable[Case], predictions: Iterable[Prediction]) -> dict[str, ProvenanceScore]:
    """Score predictions against cases, keyed by provenance.

    Cases without a prediction are skipped rather than counted as misses — a
    reviewer cannot be marked wrong for a case it was never shown.
    """
    by_id = {c.id: c for c in cases}
    scores: dict[str, ProvenanceScore] = {}

    for prediction in predictions:
        case = by_id.get(prediction.case_id)
        if case is None:
            logger.warning("Prediction for unknown case %s; skipping", prediction.case_id)
            continue

        s = scores.setdefault(case.provenance, ProvenanceScore(provenance=case.provenance))

        if prediction.error:
            # An errored review is not evidence either way. Counted so it
            # stays visible, excluded from the ratios so it cannot flatter
            # them.
            s.errors += 1
            continue

        if not prediction.cached:
            s.latencies_ms.append(prediction.latency_ms)
        if prediction.estimated_cost_usd is None:
            s.cost_unavailable += 1
        else:
            s.total_cost_usd += prediction.estimated_cost_usd

        findings = prediction.finding_objects
        flagged = len(findings) > 0

        if case.label == "defect":
            s.n_defect += 1
            if flagged:
                s.true_positives += 1
            else:
                s.false_negatives += 1

            hunk_range = hunk_line_range(case.diff_hunk)
            for finding in findings:
                if finding.line is None:
                    continue
                s.findings_with_line += 1
                if hunk_range and hunk_range[0] <= finding.line <= hunk_range[1]:
                    s.findings_inside_hunk += 1
        else:
            s.n_no_defect += 1
            s.findings_on_clean += len(findings)
            if flagged:
                s.false_positives += 1
            else:
                s.true_negatives += 1

    return scores


def format_report(scores: dict[str, ProvenanceScore]) -> str:
    """Human-readable per-provenance table."""
    if not scores:
        return "No scored predictions."

    def pct(value: float | None) -> str:
        return f"{value:.1%}" if value is not None else "n/a"

    header = (
        f"{'provenance':<18}{'n(+/-)':>10}{'prec':>8}{'recall':>8}"
        f"{'F1':>8}{'noise':>8}{'loc':>8}{'cost$':>10}{'ms':>8}"
    )
    lines = [header, "-" * len(header)]

    for name in sorted(scores):
        s = scores[name]
        counts = f"{s.n_defect}/{s.n_no_defect}"
        noise = f"{s.noise_rate:.2f}" if s.noise_rate is not None else "n/a"
        latency = f"{s.mean_latency_ms:.0f}" if s.mean_latency_ms is not None else "n/a"
        lines.append(
            f"{name:<18}{counts:>10}{pct(s.precision):>8}{pct(s.recall):>8}"
            f"{pct(s.f1):>8}{noise:>8}{pct(s.localization_rate):>8}"
            f"{s.total_cost_usd:>10.4f}{latency:>8}"
        )
        for caveat in s.caveats:
            lines.append(f"{'':<18}! {caveat}")
        if s.errors:
            lines.append(f"{'':<18}({s.errors} errored reviews, excluded from ratios)")
        if s.cost_unavailable:
            lines.append(f"{'':<18}({s.cost_unavailable} predictions had no price on file)")

    lines.append("")
    lines.append("noise = mean findings per clean case (lower is better).")
    lines.append(
        "loc   = line-bearing findings landing inside the case hunk. A weak proxy for"
    )
    lines.append(
        "        localization, not semantic matching — see docs/DESIGN.md."
    )
    return "\n".join(lines)
