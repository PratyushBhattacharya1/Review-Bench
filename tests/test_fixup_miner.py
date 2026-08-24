from tests.conftest import FakeClient

from reviewbench.miners.fixup_miner import (
    MAX_FILES_PER_REVERTED_PR,
    _revert_cites_ticket,
    extract_revert_targets,
    mine_fixups,
)

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


# --- Localizability and revert-reason signal -----------------------------


def _origin_with_files(n_files: int, revert_title="Revert thing", revert_body="") -> FakeClient:
    revert_pr = {
        "number": 100, "title": revert_title, "body": revert_body or "Reverts #50",
        "html_url": "https://github.com/owner/repo/pull/100",
    }
    origin_pr = {
        "number": 50, "merged_at": "2025-01-01T00:00:00Z",
        "html_url": "https://github.com/owner/repo/pull/50",
        "base": {"sha": "b"}, "head": {"sha": "h"},
    }
    files = [{"filename": f"mod{i}.py", "patch": "@@ -1 +1 @@\n-a\n+b"} for i in range(n_files)]
    return FakeClient(
        paginated={"/repos/owner/repo/pulls/50/files": files},
        resources={"/repos/owner/repo/pulls/50": origin_pr},
        search_results=[revert_pr],
    )


def test_small_reverted_pr_yields_a_case_per_file():
    cases = list(mine_fixups(_origin_with_files(3), "owner", "repo"))
    assert len(cases) == 3


def test_oversized_reverted_pr_is_skipped_entirely():
    # django/django#8031 contributed 67 cases — 23% of a 289-case corpus —
    # all labelled defect though the bug lives in one or two files.
    client = _origin_with_files(MAX_FILES_PER_REVERTED_PR + 1)
    assert list(mine_fixups(client, "owner", "repo")) == []


def test_boundary_is_inclusive():
    client = _origin_with_files(MAX_FILES_PER_REVERTED_PR)
    assert len(list(mine_fixups(client, "owner", "repo"))) == MAX_FILES_PER_REVERTED_PR


def test_non_code_files_do_not_count_toward_the_limit():
    # 3 Python files plus a pile of changelog noise stays localizable.
    revert_pr = {"number": 100, "title": "Revert thing", "body": "Reverts #50",
                 "html_url": "u"}
    origin_pr = {"number": 50, "merged_at": "2025-01-01T00:00:00Z", "html_url": "u",
                 "base": {"sha": "b"}, "head": {"sha": "h"}}
    files = [{"filename": f"mod{i}.py", "patch": "p"} for i in range(3)]
    files += [{"filename": f"docs/page{i}.rst", "patch": "p"} for i in range(20)]
    client = FakeClient(
        paginated={"/repos/owner/repo/pulls/50/files": files},
        resources={"/repos/owner/repo/pulls/50": origin_pr},
        search_results=[revert_pr],
    )

    cases = list(mine_fixups(client, "owner", "repo"))

    assert len(cases) == 3
    assert all(c.file_path.endswith(".py") for c in cases)


def test_revert_citing_a_ticket_is_marked():
    # Django's convention: "Fixed #33159 -- Reverted ..." — the revert itself
    # closes a bug, strong evidence the reverted PR was defective.
    client = _origin_with_files(2, revert_title="Fixed #33159 -- Reverted \"Simplified middleware\"")
    cases = list(mine_fixups(client, "owner", "repo"))
    assert {c.defect_category for c in cases} == {"reverted_with_ticket"}


def test_revert_without_a_ticket_is_marked_differently():
    client = _origin_with_files(2, revert_title="Revert \"Add podman module\"")
    cases = list(mine_fixups(client, "owner", "repo"))
    assert {c.defect_category for c in cases} == {"reverted"}


def test_ticket_in_the_quoted_original_title_is_not_the_reverts_own_reason():
    # django/django#5303, found during multi-repo verification:
    #   Revert "Fixed #25417 -- Added a field check for invalid default values."
    # The ticket belongs to the PR being reverted. Counting it inverts the
    # signal — it says the original fixed a bug and this revert undid it.
    assert not _revert_cites_ticket(
        {"title": 'Revert "Fixed #25417 -- Added a field check"', "body": ""}
    )


def test_reverts_own_ticket_still_counts_alongside_a_quoted_one():
    # django/django#15510: the revert closes two tickets while quoting a
    # reverted title that also cites one.
    assert _revert_cites_ticket(
        {"title": 'Fixed #33955 -- Reverted "Fixed #32565 -- Moved URLResolver"', "body": ""}
    )


def test_ticket_cited_in_the_revert_body_counts():
    assert _revert_cites_ticket({"title": "Revert the thing", "body": "Fixes #4242 as well."})
