import logging

import pytest
from tests.conftest import FakeClient
from tests.test_injector import FakeModelClient

from reviewbench.dataset import build_and_write, build_dataset, parse_repo
from reviewbench.github_client import GitHubError
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

    summary = build_and_write(client, ["owner/repo"], out, sources=["review_comment"])

    assert summary["total"] == 1
    assert summary["by_provenance"] == {"review_comment": 1}
    assert summary["by_label"] == {"defect": 1}
    assert len(list(read_jsonl(out))) == 1


def test_build_and_write_warns_below_minimum_cases(tmp_path, caplog):
    client = _client_with_one_comment()
    out = tmp_path / "out.jsonl"

    with caplog.at_level(logging.WARNING):
        build_and_write(client, ["owner/repo"], out, sources=["review_comment"])

    assert any("recommended minimum" in r.message for r in caplog.records)


# --- Multi-repo mining ---------------------------------------------------


def _comment(path="foo.py"):
    return {
        "id": 1, "path": path, "diff_hunk": "hunk",
        "body": "This is missing a null check on the response object.",
        "user": {"login": "human", "type": "User"},
        "html_url": "url1", "created_at": "2024-01-01T00:00:00Z",
    }


def _pr_for(num=5):
    return {
        "number": num, "merged_at": "2024-01-01T00:00:00Z",
        "html_url": f"https://github.com/x/y/pull/{num}",
        "base": {"sha": "b"}, "head": {"sha": "h"},
    }


def test_parse_repo_rejects_malformed_specs():
    assert parse_repo("psf/requests") == ("psf", "requests")
    for bad in ["requests", "psf/", "/requests", "a/b/c", ""]:
        with pytest.raises(ValueError, match="owner/name"):
            parse_repo(bad)


def test_multi_repo_merges_and_breaks_down_per_repo(tmp_path):
    client = FakeClient(
        paginated={
            "/repos/one/alpha/pulls": [_pr_for(5)],
            "/repos/one/alpha/pulls/5/comments": [_comment()],
            "/repos/one/alpha/pulls/5/files": [{"filename": "foo.py", "patch": "p"}],
            "/repos/two/beta/pulls": [_pr_for(9)],
            "/repos/two/beta/pulls/9/comments": [_comment("bar.py")],
            "/repos/two/beta/pulls/9/files": [{"filename": "bar.py", "patch": "p"}],
        }
    )
    out = tmp_path / "out.jsonl"

    summary = build_and_write(
        client, ["one/alpha", "two/beta"], out, sources=["review_comment"]
    )

    assert summary["total"] == 2
    assert summary["by_repo"]["one/alpha"]["total"] == 1
    assert summary["by_repo"]["two/beta"]["total"] == 1
    assert summary["by_repo"]["one/alpha"]["review_comment"] == 1
    assert {c.repo for c in read_jsonl(out)} == {"one/alpha", "two/beta"}


def test_one_failing_repo_does_not_lose_the_whole_run(tmp_path, caplog):
    # A 4-repo scan is tens of minutes of API calls; a single 404 must not
    # discard the repos that succeeded.
    class PartlyBroken(FakeClient):
        def get_paginated(self, path, params=None, *, per_page=100):
            if path.startswith("/repos/broken/"):
                raise GitHubError("404 Not Found")
            return super().get_paginated(path, params, per_page=per_page)

    client = PartlyBroken(
        paginated={
            "/repos/one/alpha/pulls": [_pr_for(5)],
            "/repos/one/alpha/pulls/5/comments": [_comment()],
            "/repos/one/alpha/pulls/5/files": [{"filename": "foo.py", "patch": "p"}],
        }
    )
    out = tmp_path / "out.jsonl"

    with caplog.at_level(logging.ERROR):
        summary = build_and_write(
            client, ["one/alpha", "broken/repo"], out, sources=["review_comment"]
        )

    assert summary["total"] == 1
    assert "broken/repo" in summary["failures"]
    assert any("continuing" in r.message for r in caplog.records)
