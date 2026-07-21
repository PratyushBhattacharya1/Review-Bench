from tests.conftest import FakeClient

from reviewbench.miners.fixup_miner import mine_fixups

SHA = "a" * 40


def _client_with_revert(*, origin_merged=True, patch="@@ -1 +1 @@\n-old\n+new") -> FakeClient:
    revert_pr = {
        "number": 10,
        "merged_at": "2024-02-01T00:00:00Z",
        "title": 'Revert "Add feature X"',
        "body": f"This reverts commit {SHA}.",
        "html_url": "https://github.com/owner/repo/pull/10",
    }
    unrelated_pr = {
        "number": 11,
        "merged_at": "2024-02-02T00:00:00Z",
        "title": "Fix typo",
        "body": "Just a typo fix.",
        "html_url": "https://github.com/owner/repo/pull/11",
    }
    origin_pr = {
        "number": 7,
        "merged_at": "2024-01-01T00:00:00Z" if origin_merged else None,
        "html_url": "https://github.com/owner/repo/pull/7",
        "base": {"sha": "base-sha"},
        "head": {"sha": "head-sha"},
    }

    paginated = {
        "/repos/owner/repo/pulls": [revert_pr, unrelated_pr],
        "/repos/owner/repo/pulls/7/files": [
            {"filename": "foo.py", "patch": patch},
            {"filename": "binary.png", "patch": None},
        ],
    }
    resources = {
        f"/repos/owner/repo/commits/{SHA}/pulls": [origin_pr, revert_pr],
    }
    return FakeClient(paginated=paginated, resources=resources)


def test_mine_fixups_labels_origin_pr_as_defect():
    client = _client_with_revert()

    cases = list(mine_fixups(client, "owner", "repo", pr_scan_limit=10))

    assert len(cases) == 1
    case = cases[0]
    assert case.provenance == "fixup"
    assert case.label == "defect"
    assert case.pr_number == 7
    assert case.file_path == "foo.py"
    assert case.base_commit_sha == "base-sha"
    assert case.head_commit_sha == "head-sha"
    assert "https://github.com/owner/repo/pull/7" in case.source_urls
    assert "https://github.com/owner/repo/pull/10" in case.source_urls


def test_mine_fixups_skips_files_without_patch():
    client = _client_with_revert()
    cases = list(mine_fixups(client, "owner", "repo", pr_scan_limit=10))
    assert all(c.file_path != "binary.png" for c in cases)


def test_mine_fixups_skips_unresolved_origin():
    client = _client_with_revert(origin_merged=False)
    # origin PR has merged_at=None, so _find_origin_pr's merged filter excludes it
    cases = list(mine_fixups(client, "owner", "repo", pr_scan_limit=10))
    assert cases == []


def test_mine_fixups_ignores_non_revert_prs():
    revert_pr = {
        "number": 10,
        "merged_at": "2024-02-01T00:00:00Z",
        "title": "Fix typo",
        "body": "no revert footer here",
        "html_url": "https://github.com/owner/repo/pull/10",
    }
    client = FakeClient(paginated={"/repos/owner/repo/pulls": [revert_pr]})

    cases = list(mine_fixups(client, "owner", "repo", pr_scan_limit=10))

    assert cases == []


def test_mine_fixups_respects_scan_limit():
    client = _client_with_revert()
    cases = list(mine_fixups(client, "owner", "repo", pr_scan_limit=1))
    # Only the first PR (the revert itself) is scanned; still resolves fine
    # since scan limit counts PRs scanned, not cases found.
    assert len(cases) == 1
