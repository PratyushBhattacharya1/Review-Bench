"""Injector tests run against a fake StructuredModelClient — no API key, no
network, no cost. That the injector depends on the protocol rather than on
the Anthropic SDK is what makes this possible.
"""

from __future__ import annotations

from typing import Any

import pytest

from reviewbench.models import Case
from reviewbench.synthetic.injector import inject_synthetic_bugs

CLEAN_HUNK = "@@ -1,3 +1,5 @@\n def f(items):\n-    return items[0]\n+    if not items:\n+        return None\n+    return items[0]"


class FakeModelClient:
    """Returns queued responses in order; raises if a queued item is an Exception."""

    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def complete_json(self, *, system: str, prompt: str, schema: dict) -> dict:
        self.calls.append({"system": system, "prompt": prompt, "schema": schema})
        if not self.responses:
            raise AssertionError("FakeModelClient ran out of queued responses")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _clean_case(file_path="foo.py", hunk=CLEAN_HUNK) -> Case:
    return Case(
        id="clean-1",
        repo="owner/repo",
        pr_number=5,
        provenance="synthetic",
        label="no_defect",
        file_path=file_path,
        diff_hunk=hunk,
        base_commit_sha="base",
        head_commit_sha="head",
        source_urls=["https://github.com/owner/repo/pull/5"],
    )


def _good_response(hunk="@@ -1,3 +1,4 @@\n def f(items):\n+    return items[1]") -> dict:
    return {
        "injected_hunk": hunk,
        "defect_category": "off_by_one",
        "explanation": "Returns the second element instead of the first.",
        "confidence_subtle": True,
    }


def test_injection_yields_defect_and_paired_negative():
    client = FakeModelClient([_good_response()])

    cases = list(inject_synthetic_bugs(client, [_clean_case()]))

    assert len(cases) == 2
    defect, clean = cases
    assert defect.label == "defect"
    assert defect.provenance == "synthetic"
    assert defect.defect_category == "off_by_one"
    assert "UNVALIDATED" in defect.context
    assert clean.label == "no_defect"
    assert clean.diff_hunk == CLEAN_HUNK
    assert defect.id != clean.id


def test_paired_negatives_can_be_disabled():
    client = FakeModelClient([_good_response()])
    cases = list(inject_synthetic_bugs(client, [_clean_case()], emit_paired_negatives=False))
    assert len(cases) == 1
    assert cases[0].label == "defect"


def test_obvious_bugs_are_dropped_by_default():
    response = _good_response()
    response["confidence_subtle"] = False
    client = FakeModelClient([response])

    assert list(inject_synthetic_bugs(client, [_clean_case()])) == []


def test_obvious_bugs_kept_when_requested():
    response = _good_response()
    response["confidence_subtle"] = False
    client = FakeModelClient([response])

    cases = list(inject_synthetic_bugs(client, [_clean_case()], keep_obvious=True))
    assert len(cases) == 2


def test_unchanged_hunk_is_rejected():
    client = FakeModelClient([_good_response(hunk=CLEAN_HUNK)])
    assert list(inject_synthetic_bugs(client, [_clean_case()])) == []


def test_empty_hunk_is_rejected():
    client = FakeModelClient([_good_response(hunk="   ")])
    assert list(inject_synthetic_bugs(client, [_clean_case()])) == []


def test_model_failure_skips_case_without_aborting_run():
    client = FakeModelClient([RuntimeError("model refused"), _good_response()])

    cases = list(inject_synthetic_bugs(client, [_clean_case("a.py"), _clean_case("b.py")]))

    # First case failed, second succeeded — the run continued.
    assert len(cases) == 2
    assert all(c.file_path == "b.py" for c in cases)


def test_limit_caps_injections():
    client = FakeModelClient([_good_response(), _good_response()])

    cases = list(
        inject_synthetic_bugs(client, [_clean_case("a.py"), _clean_case("b.py")], limit=1)
    )

    assert len({c.file_path for c in cases}) == 1
    assert len(client.calls) == 1


def test_blank_input_hunk_is_skipped_without_calling_model():
    client = FakeModelClient([])
    assert list(inject_synthetic_bugs(client, [_clean_case(hunk="  \n ")])) == []
    assert client.calls == []


def test_prompt_includes_file_and_hunk():
    client = FakeModelClient([_good_response()])
    list(inject_synthetic_bugs(client, [_clean_case()]))

    prompt = client.calls[0]["prompt"]
    assert "foo.py" in prompt
    assert "owner/repo" in prompt
    assert CLEAN_HUNK in prompt
