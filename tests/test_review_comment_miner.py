from tests.conftest import FakeClient

from reviewbench.miners.review_comment_miner import (
    is_machine_authored,
    is_substantive_comment,
    is_thread_root,
    mine_review_comments,
)


def test_is_substantive_comment_filters_trivial_acks():
    assert not is_substantive_comment("LGTM")
    assert not is_substantive_comment("  +1  ")
    assert not is_substantive_comment("nice")
    assert not is_substantive_comment("ok")  # too short
    assert not is_substantive_comment("")


def test_is_substantive_comment_keeps_real_feedback():
    assert is_substantive_comment("This will throw if `items` is empty — needs a length check.")


def test_is_thread_root_distinguishes_replies():
    assert is_thread_root({"id": 1})  # field absent → root
    assert is_thread_root({"id": 1, "in_reply_to_id": None})  # explicit null → root
    assert not is_thread_root({"id": 2, "in_reply_to_id": 1})  # reply to comment 1


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


def test_reply_comments_are_not_positive_cases():
    # A substantive root comment plus two substantive-looking replies —
    # the replies should be dropped, leaving exactly one positive.
    comments = [
        {
            "id": 1, "path": "foo.py", "diff_hunk": "hunk",
            "body": "This will throw if `items` is empty — add a length check.",
            "html_url": "url1", "created_at": "2024-01-01T00:00:00Z",
        },
        {
            "id": 2, "path": "foo.py", "diff_hunk": "hunk", "in_reply_to_id": 1,
            "body": "Yes, good call, I'll add that guard now.",
            "html_url": "url2", "created_at": "2024-01-01T01:00:00Z",
        },
        {
            "id": 3, "path": "foo.py", "diff_hunk": "hunk", "in_reply_to_id": 1,
            "body": "Thanks, pushed the fix in the latest commit.",
            "html_url": "url3", "created_at": "2024-01-01T02:00:00Z",
        },
    ]
    files = [{"filename": "foo.py", "patch": "full patch"}]

    cases = list(mine_review_comments(_client(comments, files), "owner", "repo"))

    positives = [c for c in cases if c.label == "defect"]
    assert len(positives) == 1
    assert positives[0].human_comment.startswith("This will throw")


def test_file_with_only_reply_attention_is_not_sampled_as_negative():
    # foo.py's root comment is trivial (LGTM) but a reply is substantive.
    # foo.py must not be emitted as a positive (no substantive root) and
    # must not be sampled as a clean negative (it drew substantive attention).
    comments = [
        {
            "id": 1, "path": "foo.py", "diff_hunk": "hunk", "body": "LGTM",
            "html_url": "url1", "created_at": "2024-01-01T00:00:00Z",
        },
        {
            "id": 2, "path": "foo.py", "diff_hunk": "hunk", "in_reply_to_id": 1,
            "body": "Actually wait, this drops the timeout on retries.",
            "html_url": "url2", "created_at": "2024-01-01T01:00:00Z",
        },
    ]
    files = [
        {"filename": "foo.py", "patch": "patch-foo"},
        {"filename": "bar.py", "patch": "patch-bar"},
    ]

    cases = list(mine_review_comments(_client(comments, files), "owner", "repo", negatives_per_pr=5))

    assert all(c.file_path != "foo.py" for c in cases)
    assert [c.file_path for c in cases if c.label == "no_defect"] == ["bar.py"]


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


# --- Machine-authored comment detection ---------------------------------
#
# Ground truth containing another reviewer's output makes benchmarking a
# reviewer against it circular. Both fixtures below are real shapes observed
# in psf/requests.


def test_bot_type_is_machine_authored():
    # github-advanced-security[bot] posting CodeQL findings.
    assert is_machine_authored(
        {
            "user": {"login": "github-advanced-security[bot]", "type": "Bot"},
            "body": "## Use of a broken or weak cryptographic hashing algorithm on sensitive data",
        }
    )


def test_ai_review_output_from_a_user_account_is_machine_authored():
    # The case user.type alone misses: PR #7431, author `sdm0p`, type "User".
    assert is_machine_authored(
        {
            "user": {"login": "sdm0p", "type": "User"},
            "body": "⚠️ **HIGH** — *test_coverage* **Confidence:** 80% New logic is not tested.",
        }
    )


def test_human_comment_is_not_machine_authored():
    assert not is_machine_authored(
        {
            "user": {"login": "nateprewitt", "type": "User"},
            "body": "This will throw if `items` is empty — needs a length check.",
        }
    )


def test_severity_word_alone_does_not_trigger():
    # Both halves are required; humans write bold emphasis and the word
    # "confidence" independently.
    assert not is_machine_authored(
        {"user": {"login": "x", "type": "User"}, "body": "**NOTE** this looks fragile to me"}
    )
    assert not is_machine_authored(
        {"user": {"login": "x", "type": "User"}, "body": "I have low confidence in this test"}
    )


def test_machine_authored_comments_excluded_from_positives():
    comments = [
        {
            "id": 1, "path": "foo.py", "diff_hunk": "hunk",
            "body": "⚠️ **HIGH** — *test_coverage* **Confidence:** 80% New logic is not tested.",
            "user": {"login": "sdm0p", "type": "User"},
            "html_url": "url1", "created_at": "2024-01-01T00:00:00Z",
        },
        {
            "id": 2, "path": "bar.py", "diff_hunk": "hunk",
            "body": "This will throw if `items` is empty — needs a length check.",
            "user": {"login": "nateprewitt", "type": "User"},
            "html_url": "url2", "created_at": "2024-01-01T00:00:00Z",
        },
    ]
    files = [{"filename": "foo.py", "patch": "p"}, {"filename": "bar.py", "patch": "p"}]

    cases = list(mine_review_comments(_client(comments, files), "owner", "repo", negatives_per_pr=0))

    assert [c.file_path for c in cases] == ["bar.py"]
    assert cases[0].comment_author == "nateprewitt"
    assert cases[0].comment_author_type == "User"


def test_keep_machine_authored_retains_them_for_measurement():
    comments = [
        {
            "id": 1, "path": "foo.py", "diff_hunk": "hunk",
            "body": "⚠️ **HIGH** — *test_coverage* **Confidence:** 80% New logic is not tested.",
            "user": {"login": "sdm0p", "type": "User"},
            "html_url": "url1", "created_at": "2024-01-01T00:00:00Z",
        },
    ]
    files = [{"filename": "foo.py", "patch": "p"}]

    cases = list(
        mine_review_comments(
            _client(comments, files), "owner", "repo",
            negatives_per_pr=0, keep_machine_authored=True,
        )
    )

    assert len(cases) == 1
    assert cases[0].comment_author == "sdm0p"


def test_machine_authored_file_still_excluded_from_negatives():
    # A file CodeQL flagged is not a clean negative, even though its comment
    # is excluded from positives.
    comments = [
        {
            "id": 1, "path": "foo.py", "diff_hunk": "hunk",
            "body": "## Use of a broken or weak cryptographic hashing algorithm on sensitive data",
            "user": {"login": "github-advanced-security[bot]", "type": "Bot"},
            "html_url": "url1", "created_at": "2024-01-01T00:00:00Z",
        },
    ]
    files = [{"filename": "foo.py", "patch": "p"}, {"filename": "bar.py", "patch": "p"}]

    cases = list(mine_review_comments(_client(comments, files), "owner", "repo", negatives_per_pr=5))

    assert [c.file_path for c in cases] == ["bar.py"]
