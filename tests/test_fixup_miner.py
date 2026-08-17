from tests.conftest import FakeClient

from reviewbench.miners.fixup_miner import extract_revert_targets, mine_fixups

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

    cases = list(mine_fixups(client, "owner", "repo", pr_scan_limit=10, use_search=False))

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
    cases = list(mine_fixups(client, "owner", "repo", pr_scan_limit=10, use_search=False))
    assert all(c.file_path != "binary.png" for c in cases)


def test_mine_fixups_skips_unresolved_origin():
    client = _client_with_revert(origin_merged=False)
    # origin PR has merged_at=None, so _find_origin_pr's merged filter excludes it
    cases = list(mine_fixups(client, "owner", "repo", pr_scan_limit=10, use_search=False))
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

    cases = list(mine_fixups(client, "owner", "repo", pr_scan_limit=10, use_search=False))

    assert cases == []


def test_mine_fixups_respects_scan_limit():
    client = _client_with_revert()
    cases = list(mine_fixups(client, "owner", "repo", pr_scan_limit=1, use_search=False))
    # Only the first PR (the revert itself) is scanned; still resolves fine
    # since scan limit counts PRs scanned, not cases found.
    assert len(cases) == 1


# --- Revert-target extraction -------------------------------------------
#
# Cases below are drawn from real psf/requests revert PRs. Before the fix,
# only the SHA-footer form was recognized, so the miner found ~1 in 8 of
# these against live data.


def test_extracts_github_generated_sha_footer():
    pr = {"number": 1, "title": 'Revert "Add X"', "body": f"This reverts commit {SHA}."}
    assert extract_revert_targets(pr) == [("sha", SHA)]


def test_extracts_owner_repo_pr_reference():
    # psf/requests#2458: "Reverts kennethreitz/requests#2442"
    pr = {"number": 1, "title": 'Revert "Update certificate bundle."',
          "body": "Reverts kennethreitz/requests#2442"}
    assert extract_revert_targets(pr) == [("pr", "2442")]


def test_extracts_bare_pr_reference():
    # psf/requests#6767: "This PR reverts the changes from #6667 ..."
    pr = {"number": 1, "title": "Revert caching a default SSLContext",
          "body": "This PR reverts the changes from #6667 to the previous behavior."}
    assert extract_revert_targets(pr) == [("pr", "6667")]


def test_ignores_pr_refs_not_adjacent_to_a_revert_verb():
    # psf/requests#3486: "This addresses #3481 and will revert #3362 back ..."
    # #3481 is an issue being addressed, not the revert target.
    pr = {"number": 1, "title": "reverting 3362",
          "body": "This addresses #3481 and will revert #3362 back to its prior state."}
    assert extract_revert_targets(pr) == [("pr", "3362")]


def test_pr_refs_require_a_revert_title():
    # Body mentions reverting, but the PR isn't a revert — don't label #123.
    pr = {"number": 1, "title": "Add retry backoff",
          "body": "If this misbehaves we should revert #123 someday."}
    assert extract_revert_targets(pr) == []


def test_non_revert_pr_yields_no_targets():
    assert extract_revert_targets({"number": 1, "title": "Fix typo", "body": "a typo"}) == []


def test_both_reference_styles_are_deduped_not_dropped():
    pr = {"number": 1, "title": "Revert thing",
          "body": f"Reverts #42\n\nThis reverts commit {SHA}.\nAlso reverts #42 again."}
    assert extract_revert_targets(pr) == [("sha", SHA), ("pr", "42")]


# --- Search-based discovery ---------------------------------------------


def _search_client() -> FakeClient:
    revert_pr = {
        "number": 6767,
        "title": "Revert caching a default SSLContext",
        "body": "This PR reverts the changes from #6667 to the previous behavior.",
        "html_url": "https://github.com/owner/repo/pull/6767",
    }
    origin_pr = {
        "number": 6667,
        "merged_at": "2025-01-01T00:00:00Z",
        "html_url": "https://github.com/owner/repo/pull/6667",
        "base": {"sha": "base-sha"},
        "head": {"sha": "head-sha"},
    }
    return FakeClient(
        paginated={"/repos/owner/repo/pulls/6667/files": [{"filename": "adapters.py", "patch": "@@ -1 +1 @@\n-a\n+b"}]},
        resources={"/repos/owner/repo/pulls/6667": origin_pr},
        search_results=[revert_pr],
    )


def test_search_path_resolves_pr_reference_to_origin():
    client = _search_client()

    cases = list(mine_fixups(client, "owner", "repo"))

    assert len(cases) == 1
    assert cases[0].pr_number == 6667
    assert cases[0].label == "defect"
    assert cases[0].file_path == "adapters.py"
    assert "Reverted by PR #6767" in cases[0].context
    assert "repo:owner/repo" in client.queries[0]
    assert "is:merged" in client.queries[0]


def test_unmerged_referenced_pr_is_not_a_defect_case():
    client = _search_client()
    client.resources["/repos/owner/repo/pulls/6667"]["merged_at"] = None

    assert list(mine_fixups(client, "owner", "repo")) == []


def test_self_reference_does_not_resolve():
    client = _search_client()
    client.search_results[0]["body"] = "Reverts #6767"  # points at itself

    assert list(mine_fixups(client, "owner", "repo")) == []


def test_falls_back_to_recency_scan_when_search_unavailable():
    # search_results=None makes FakeClient.search_issues raise GitHubError.
    client = _client_with_revert()

    cases = list(mine_fixups(client, "owner", "repo", pr_scan_limit=10))

    assert len(cases) == 1
    assert cases[0].pr_number == 7
