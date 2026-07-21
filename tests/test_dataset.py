import logging

import pytest
from tests.conftest import FakeClient
from tests.test_injector import FakeModelClient

from reviewbench.dataset import build_and_write, build_dataset
from reviewbench.models import read_jsonl


def _client_with_one_comment() -> FakeClient:
    pr = {
        "number": 5,
        "merged_at": "2024-01-01T00:00:00Z",
        "html_url": "https://github.com/owner/repo/pull/5",
        "base": {"sha": "base-sha"},
        "head": {"sha": "head-sha"},
    }
    return FakeClient(
        paginated={
            "/repos/owner/repo/pulls": [pr],
            "/repos/owner/repo/pulls/5/comments": [
                {
                    "id": 1,
                    "path": "foo.py",
                    "diff_hunk": "hunk",
                    "body": "This is missing a null check on the response object.",
                    "html_url": "url1",
                    "created_at": "2024-01-01T00:00:00Z",
                }
            ],
            "/repos/owner/repo/pulls/5/files": [{"filename": "foo.py", "patch": "patch-foo"}],
        }
    )


def test_build_dataset_raises_on_unknown_source():
    client = _client_with_one_comment()
    with pytest.raises(ValueError, match="Unknown source"):
        list(build_dataset(client, "owner", "repo", sources=["not_a_real_source"]))


def test_synthetic_without_model_client_raises():
    client = _client_with_one_comment()
    with pytest.raises(ValueError, match="requires a model client"):
        list(build_dataset(client, "owner", "repo", sources=["synthetic"]))


def test_synthetic_source_runs_injector_with_model_client():
    pr = {
        "number": 5,
        "merged_at": "2024-01-01T00:00:00Z",
        "title": "Add empty check",
        "html_url": "https://github.com/owner/repo/pull/5",
        "base": {"sha": "base-sha"},
        "head": {"sha": "head-sha"},
    }
    big_patch = "@@ -1,3 +1,5 @@\n def f(items):\n-    return items[0]\n+    if not items:\n+        return None\n+    return items[0]\n"
    client = FakeClient(
        paginated={
            "/repos/owner/repo/pulls": [pr],
            "/repos/owner/repo/pulls/5/comments": [],
            "/repos/owner/repo/pulls/5/files": [{"filename": "foo.py", "patch": big_patch}],
        }
    )
    model_client = FakeModelClient(
        [
            {
                "injected_hunk": "@@ -1,3 +1,4 @@\n def f(items):\n+    return items[1]",
                "defect_category": "off_by_one",
                "explanation": "Off by one.",
                "confidence_subtle": True,
            }
        ]
    )

    cases = list(
        build_dataset(client, "owner", "repo", sources=["synthetic"], model_client=model_client)
    )

    assert {c.label for c in cases} == {"defect", "no_defect"}
    assert all(c.provenance == "synthetic" for c in cases)


def test_build_and_write_writes_file_and_summary(tmp_path):
    client = _client_with_one_comment()
    out = tmp_path / "out.jsonl"

    summary = build_and_write(client, "owner", "repo", out, sources=["review_comment"])

    assert summary["total"] == 1
    assert summary["by_provenance"] == {"review_comment": 1}
    assert summary["by_label"] == {"defect": 1}
    assert len(list(read_jsonl(out))) == 1


def test_build_and_write_warns_below_minimum_cases(tmp_path, caplog):
    client = _client_with_one_comment()
    out = tmp_path / "out.jsonl"

    with caplog.at_level(logging.WARNING):
        build_and_write(client, "owner", "repo", out, sources=["review_comment"])

    assert any("recommended minimum" in r.message for r in caplog.records)
