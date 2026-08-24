"""Runner and scorer tests.

A `FakeReviewer` stands in for a real adapter, which is the payoff of
`Reviewer` being a Protocol: the whole run/score pipeline is exercisable
with no API key and no network.
"""

from __future__ import annotations

import pytest

from reviewbench.models import Case
from reviewbench.reviewers.base import Finding, ReviewResult
from reviewbench.runner import Prediction, read_predictions, run_reviewer, write_predictions
from reviewbench.scoring import format_report, hunk_line_range, score

HUNK = "@@ -10,3 +10,5 @@\n def f(items):\n-    return items[0]\n+    return items[1]"


def _case(case_id, label, provenance="fixup", hunk=HUNK) -> Case:
    return Case(
        id=case_id,
        repo="owner/repo",
        pr_number=1,
        provenance=provenance,
        label=label,
        file_path="mod.py",
        diff_hunk=hunk,
        base_commit_sha="b",
        head_commit_sha="h",
    )


class FakeReviewer:
    """Returns a queued ReviewResult per case, in order."""

    name = "fake"
    model = "claude-opus-4-8"

    def __init__(self, results):
        self.results = list(results)
        self.seen = []

    def review(self, case):
        self.seen.append(case.id)
        return self.results.pop(0) if self.results else ReviewResult()


def _found(line=11, message="Off-by-one: returns the second element."):
    return ReviewResult(
        findings=[Finding(file_path="mod.py", message=message, line=line, severity="high")],
        input_tokens=1000,
        output_tokens=200,
    )


# --- hunk parsing --------------------------------------------------------


@pytest.mark.parametrize(
    "hunk,expected",
    [
        ("@@ -1,3 +12,9 @@\n ctx", (12, 20)),
        ("@@ -1 +5 @@", (5, 5)),
        ("@@ -1,2 +5,3 @@\n x\n@@ -9,2 +20,4 @@\n y", (5, 23)),
        ("no header at all", None),
        ("", None),
    ],
)
def test_hunk_line_range(hunk, expected):
    assert hunk_line_range(hunk) == expected


# --- runner --------------------------------------------------------------


def test_runner_records_findings_cost_and_latency():
    reviewer = FakeReviewer([_found()])
    predictions = list(run_reviewer(reviewer, [_case("a", "defect")]))

    assert len(predictions) == 1
    p = predictions[0]
    assert p.case_id == "a"
    assert p.reviewer == "fake"
    assert len(p.findings) == 1
    assert p.input_tokens == 1000
    # claude-opus-4-8 is $5/$25 per MTok: 1000 in + 200 out.
    assert p.estimated_cost_usd == pytest.approx(0.005 + 0.005)
    assert p.latency_ms >= 0


def test_unknown_model_reports_no_cost_rather_than_zero():
    class OddModel(FakeReviewer):
        model = "some-unlisted-model"

    predictions = list(run_reviewer(OddModel([_found()]), [_case("a", "defect")]))
    # None means "no price on file"; zero would read as free.
    assert predictions[0].estimated_cost_usd is None


def test_predictions_round_trip_through_jsonl(tmp_path):
    out = tmp_path / "preds.jsonl"
    written = write_predictions(run_reviewer(FakeReviewer([_found()]), [_case("a", "defect")]), out)

    assert written == 1
    restored = list(read_predictions(out))
    assert restored[0].case_id == "a"
    assert restored[0].finding_objects[0].line == 11


# --- scoring -------------------------------------------------------------


def test_perfect_reviewer_scores_perfectly():
    cases = [_case("a", "defect"), _case("b", "no_defect")]
    reviewer = FakeReviewer([_found(), ReviewResult()])  # flags the bug, ignores clean

    scores = score(cases, run_reviewer(reviewer, cases))["fixup"]

    assert scores.precision == 1.0
    assert scores.recall == 1.0
    assert scores.f1 == 1.0
    assert scores.noise_rate == 0.0


def test_reviewer_that_flags_everything_has_recall_but_terrible_noise():
    # The failure mode docs/DESIGN.md is written against: perfect recall,
    # useless in practice. Noise rate is what exposes it.
    cases = [_case("a", "defect"), _case("b", "no_defect"), _case("c", "no_defect")]
    reviewer = FakeReviewer([_found(), _found(), _found()])

    s = score(cases, run_reviewer(reviewer, cases))["fixup"]

    assert s.recall == 1.0
    assert s.precision == pytest.approx(1 / 3)
    assert s.noise_rate == 1.0
    assert s.false_positives == 2


def test_silent_reviewer_has_no_noise_and_no_recall():
    cases = [_case("a", "defect"), _case("b", "no_defect")]
    s = score(cases, run_reviewer(FakeReviewer([]), cases))["fixup"]

    assert s.recall == 0.0
    assert s.noise_rate == 0.0
    assert s.precision is None  # never flagged anything, so undefined


def test_scores_are_broken_out_by_provenance_never_pooled():
    cases = [
        _case("a", "defect", provenance="fixup"),
        _case("b", "defect", provenance="synthetic"),
    ]
    reviewer = FakeReviewer([_found(), ReviewResult()])

    scores = score(cases, run_reviewer(reviewer, cases))

    assert set(scores) == {"fixup", "synthetic"}
    assert scores["fixup"].recall == 1.0
    assert scores["synthetic"].recall == 0.0


def test_localization_counts_only_line_bearing_findings():
    # Hunk covers new-file lines 10..14.
    cases = [_case("a", "defect"), _case("b", "defect")]
    reviewer = FakeReviewer([_found(line=11), _found(line=999)])

    s = score(cases, run_reviewer(reviewer, cases))["fixup"]

    assert s.findings_with_line == 2
    assert s.findings_inside_hunk == 1
    assert s.localization_rate == 0.5


def test_findings_without_a_line_are_not_counted_as_localized():
    cases = [_case("a", "defect")]
    reviewer = FakeReviewer(
        [ReviewResult(findings=[Finding(file_path="mod.py", message="something", line=None)])]
    )

    s = score(cases, run_reviewer(reviewer, cases))["fixup"]

    assert s.true_positives == 1
    assert s.findings_with_line == 0
    assert s.localization_rate is None  # undefined, not zero


def test_errored_reviews_are_counted_but_excluded_from_ratios():
    cases = [_case("a", "defect"), _case("b", "defect")]
    reviewer = FakeReviewer([ReviewResult(error="rate limited"), _found()])

    s = score(cases, run_reviewer(reviewer, cases))["fixup"]

    assert s.errors == 1
    assert s.n_defect == 1  # the errored case is not scored either way
    assert s.recall == 1.0


def test_predictions_for_unknown_cases_are_skipped():
    scores = score([_case("a", "defect")], [Prediction(case_id="ghost", reviewer="f", model="m")])
    assert scores == {}


def test_format_report_renders_without_crashing_on_partial_data():
    cases = [_case("a", "defect")]
    report = format_report(score(cases, run_reviewer(FakeReviewer([_found()]), cases)))

    assert "fixup" in report
    assert "noise" in report


def test_positives_only_provenance_is_flagged_as_uninterpretable():
    # fixup emits positives only, so its precision is 100% by construction.
    # The number is real and the interpretation is not; the report says so.
    cases = [_case("a", "defect"), _case("b", "defect")]
    s = score(cases, run_reviewer(FakeReviewer([_found(), _found()]), cases))["fixup"]

    assert s.precision == 1.0
    assert s.noise_rate is None
    assert any("no clean cases" in c for c in s.caveats)
    assert "no clean cases" in format_report({"fixup": s})


def test_negatives_only_provenance_is_flagged_too():
    cases = [_case("a", "no_defect"), _case("b", "no_defect")]
    s = score(cases, run_reviewer(FakeReviewer([_found(), ReviewResult()]), cases))["fixup"]

    assert any("no defect cases" in c for c in s.caveats)


def test_balanced_provenance_has_no_caveats():
    cases = [_case("a", "defect"), _case("b", "no_defect")]
    s = score(cases, run_reviewer(FakeReviewer([_found(), ReviewResult()]), cases))["fixup"]

    assert s.caveats == []
