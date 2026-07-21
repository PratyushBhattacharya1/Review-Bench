from tests.conftest import FakeClient

from reviewbench.miners.review_comment_miner import is_substantive_comment, mine_review_comments


def test_is_substantive_comment_filters_trivial_acks():
    assert not is_substantive_comment("LGTM")
    assert not is_substantive_comment("  +1  ")
    assert not is_substantive_comment("nice")
    assert not is_substantive_comment("ok")  # too short
    assert not is_substantive_comment("")


def test_is_substantive_comment_keeps_real_feedback():
    assert is_substantive_comment("This will throw if `items` is empty — needs a length check.")


def _pr() -> dict:
    return {
        "number": 5,
        "merged_at": "2024-01-01T00:00:00Z",
        "html_url": "https://github.com/owner/repo/pull/5",
        "base": {"sha": "base-sha"},
        "head": {"sha": "head-sha"},
    }


def _client(comments, files) -> FakeClient:
    return FakeClient(
        paginated={
            "/repos/owner/repo/pulls": [_pr()],
            "/repos/owner/repo/pulls/5/comments": comments,
            "/repos/owner/repo/pulls/5/files": files,
        }
    )


def test_substantive_comment_becomes_positive_case():
    comments = [
        {
            "id": 1,
            "path": "foo.py",
            "diff_hunk": "@@ -1 +1 @@\n-old\n+new",
            "body": "This will throw if `items` is empty.",
            "html_url": "https://github.com/owner/repo/pull/5#discussion_r1",
            "created_at": "2024-01-01T00:00:00Z",
        }
    ]
    files = [{"filename": "foo.py", "patch": "full patch"}]

    cases = list(mine_review_comments(FakeClient(paginated={
        "/repos/owner/repo/pulls": [_pr()],
        "/repos/owner/repo/pulls/5/comments": comments,
        "/repos/owner/repo/pulls/5/files": files,
    }), "owner", "repo"))

    positives = [c for c in cases if c.label == "defect"]
    assert len(positives) == 1
    assert positives[0].human_comment == "This will throw if `items` is empty."
    assert positives[0].file_path == "foo.py"
    assert positives[0].provenance == "review_comment"


def test_trivial_comment_does_not_become_a_case():
    comments = [
        {
            "id": 2,
            "path": "foo.py",
            "diff_hunk": "@@ -1 +1 @@\n-old\n+new",
            "body": "LGTM",
            "html_url": "https://github.com/owner/repo/pull/5#discussion_r2",
            "created_at": "2024-01-01T00:00:00Z",
        }
    ]
    files = [{"filename": "foo.py", "patch": "full patch"}]

    cases = list(mine_review_comments(_client(comments, files), "owner", "repo"))

    assert all(c.human_comment != "LGTM" for c in cases)


def test_uncommented_file_sampled_as_negative():
    comments = [
        {
            "id": 1,
            "path": "foo.py",
            "diff_hunk": "hunk",
            "body": "This will throw if `items` is empty.",
            "html_url": "url1",
            "created_at": "2024-01-01T00:00:00Z",
        }
    ]
    files = [
        {"filename": "foo.py", "patch": "patch-foo"},
        {"filename": "bar.py", "patch": "patch-bar"},
    ]

    cases = list(mine_review_comments(_client(comments, files), "owner", "repo", negatives_per_pr=1))

    negatives = [c for c in cases if c.label == "no_defect"]
    assert len(negatives) == 1
    assert negatives[0].file_path == "bar.py"
    assert negatives[0].human_comment is None


def test_negatives_per_pr_zero_disables_sampling():
    comments = []
    files = [{"filename": "bar.py", "patch": "patch-bar"}]

    cases = list(mine_review_comments(_client(comments, files), "owner", "repo", negatives_per_pr=0))

    assert cases == []
