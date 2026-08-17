"""Source 1: fixup/revert mining — the highest-signal, hardest-to-fake
ground truth source.

A human explicitly undid a merged PR. We resolve the reverted commit back
to the PR that introduced it (via GitHub's "list pull requests associated
with a commit" endpoint) and label *that* PR's diff as a positive
(defect) case. See docs/DESIGN.md for the known weakness: reverts happen
for non-bug reasons too (scope cuts, CI unblocking), so every case keeps
both PRs' URLs for a human to spot-check during verification.
"""

from __future__ import annotations

import logging
import re
from typing import Iterator, NamedTuple

from reviewbench.github_client import GitHubClient, GitHubError
from reviewbench.models import Case, make_case_id

logger = logging.getLogger(__name__)

_REVERT_COMMIT_RE = re.compile(r"This reverts commit ([0-9a-f]{40})", re.IGNORECASE)
_REVERT_TITLE_RE = re.compile(r"\brevert(s|ed|ing)?\b", re.IGNORECASE)

# A PR number adjacent to a revert verb: "Reverts owner/repo#2442",
# "will revert #3362", "reverts the changes from #6667".
#
# The adjacency requirement is the whole point. A revert PR body routinely
# cites issues it also addresses ("This addresses #3481 and will revert
# #3362") — taking every `#N` in the body would label the wrong PR as
# defective. Requiring the reference to follow a revert verb keeps #3362
# and correctly ignores #3481.
_REVERT_PR_RE = re.compile(
    r"\brevert(?:s|ed|ing)?\b[^\n#]{0,40}?(?:[\w.-]+/[\w.-]+)?#(\d+)",
    re.IGNORECASE,
)


class RevertTarget(NamedTuple):
    """What a revert PR says it is undoing.

    `kind` is "sha" (GitHub's auto-generated footer) or "pr" (a human
    writing "Reverts #2442"). Both resolve to an origin PR, by different
    routes.
    """

    kind: str
    value: str


def extract_revert_targets(pr: dict) -> list[RevertTarget]:
    """Return everything `pr` claims to revert, or [] if it isn't a revert.

    Two reference styles, because real repos use both. GitHub's "Revert"
    button writes `This reverts commit <sha>`; humans writing a revert by
    hand overwhelmingly cite the PR number instead. Measured on
    psf/requests: of 8 merged revert PRs, 1 used the SHA footer and 5 used
    a PR reference — so recognizing only the footer, as this miner
    originally did, finds almost nothing.
    """
    body = pr.get("body") or ""
    title = pr.get("title") or ""

    targets: list[RevertTarget] = []
    seen: set[tuple[str, str]] = set()

    for sha in _REVERT_COMMIT_RE.findall(body):
        key = ("sha", sha.lower())
        if key not in seen:
            seen.add(key)
            targets.append(RevertTarget("sha", sha.lower()))

    # PR references are only trusted when the title also signals a revert.
    # Without that gate, any PR whose body happens to say "we should revert
    # #123 someday" would be treated as a revert of #123.
    if _REVERT_TITLE_RE.search(title):
        for number in _REVERT_PR_RE.findall(f"{title}\n{body}"):
            key = ("pr", number)
            if key not in seen:
                seen.add(key)
                targets.append(RevertTarget("pr", number))

    if not targets and _REVERT_TITLE_RE.search(title):
        logger.debug(
            "PR #%s looks like a revert but names no resolvable target; skipping",
            pr.get("number"),
        )
    return targets


def _find_origin_pr(client: GitHubClient, owner: str, repo: str, sha: str, revert_pr_number: int) -> dict | None:
    """Resolve a commit SHA back to the (merged) PR that introduced it."""
    try:
        prs = client.get(f"/repos/{owner}/{repo}/commits/{sha}/pulls")
    except GitHubError as e:
        logger.warning("Could not resolve commit %s to a PR: %s", sha, e)
        return None

    candidates = [p for p in prs if p.get("number") != revert_pr_number and p.get("merged_at")]
    if not candidates:
        return None
    # Prefer the PR that was merged most recently before the revert — the
    # commit could in principle appear on more than one merged PR's history.
    candidates.sort(key=lambda p: p.get("merged_at") or "", reverse=True)
    return candidates[0]


def _fetch_origin_pr_by_number(
    client: GitHubClient, owner: str, repo: str, number: str, revert_pr_number: int
) -> dict | None:
    """Resolve a `Reverts #N` reference to PR N, if it is a merged PR."""
    if int(number) == revert_pr_number:
        return None
    try:
        pr = client.get(f"/repos/{owner}/{repo}/pulls/{number}")
    except GitHubError as e:
        # A `#N` reference can point at an *issue* rather than a PR, in
        # which case the pulls endpoint 404s. That's expected, not an error.
        logger.debug("Reference #%s did not resolve to a PR: %s", number, e)
        return None

    if not pr.get("merged_at"):
        logger.debug("Referenced PR #%s was never merged; not a defect case", number)
        return None
    return pr


def _resolve_targets(
    client: GitHubClient, owner: str, repo: str, revert_pr: dict, targets: list[RevertTarget]
) -> Iterator[dict]:
    """Yield the merged origin PR for each resolvable revert target."""
    revert_number = revert_pr["number"]
    seen: set[int] = set()
    for target in targets:
        if target.kind == "sha":
            origin = _find_origin_pr(client, owner, repo, target.value, revert_number)
        else:
            origin = _fetch_origin_pr_by_number(client, owner, repo, target.value, revert_number)
        if origin is None:
            continue
        if origin["number"] in seen:
            continue
        seen.add(origin["number"])
        yield origin


def _iter_revert_candidates(
    client: GitHubClient, owner: str, repo: str, *, pr_scan_limit: int, use_search: bool
) -> Iterator[dict]:
    """Yield merged PRs that might be reverts.

    Two discovery strategies. Search asks GitHub for revert-titled PRs
    across the repo's whole history in one query; the recency scan walks
    the most-recently-updated closed PRs.

    Search is the default because reverts are rare and old. Every one of
    psf/requests' 11 revert PRs predates its 300 most recent — a recency
    scan there returns nothing no matter how good the pattern matching is,
    which is exactly what the first full run showed.
    """
    if use_search:
        query = f"repo:{owner}/{repo} type:pr is:merged revert in:title"
        try:
            yield from client.search_issues(query, max_results=pr_scan_limit)
            return
        except GitHubError as e:
            # Search can be unavailable (permissions, secondary rate limit).
            # Falling back beats failing the whole run.
            logger.warning("Revert search failed (%s); falling back to recency scan", e)

    scanned = 0
    for pr in client.get_paginated(
        f"/repos/{owner}/{repo}/pulls", params={"state": "closed", "sort": "updated", "direction": "desc"}
    ):
        if scanned >= pr_scan_limit:
            break
        scanned += 1
        if pr.get("merged_at"):
            yield pr


def mine_fixups(
    client: GitHubClient,
    owner: str,
    repo: str,
    *,
    pr_scan_limit: int = 300,
    use_search: bool = True,
) -> Iterator[Case]:
    """Find revert PRs, resolve what they reverted, and yield one Case per
    file changed in the reverted (origin) PR.

    Set `use_search=False` to walk recent PRs instead of querying search —
    useful when search is unavailable, or to restrict the scan to recent
    history.
    """
    for pr in _iter_revert_candidates(
        client, owner, repo, pr_scan_limit=pr_scan_limit, use_search=use_search
    ):
        targets = extract_revert_targets(pr)
        if not targets:
            continue

        resolved_any = False
        for origin_pr in _resolve_targets(client, owner, repo, pr, targets):
            resolved_any = True
            yield from _cases_for_origin_pr(client, owner, repo, origin_pr, revert_pr=pr)

        if not resolved_any:
            logger.info(
                "Revert PR #%s named %d target(s) but none resolved to a merged PR",
                pr["number"],
                len(targets),
            )


def _cases_for_origin_pr(client: GitHubClient, owner: str, repo: str, origin_pr: dict, *, revert_pr: dict) -> Iterator[Case]:
    number = origin_pr["number"]
    try:
        files = list(client.get_paginated(f"/repos/{owner}/{repo}/pulls/{number}/files"))
    except GitHubError as e:
        logger.warning("Could not fetch files for origin PR #%s: %s", number, e)
        return

    base_sha = origin_pr.get("base", {}).get("sha", "")
    head_sha = origin_pr.get("head", {}).get("sha", "")

    for f in files:
        patch = f.get("patch")
        if not patch:
            # Binary file or diff too large for GitHub to include a patch;
            # nothing to score a reviewer's line-level output against.
            continue
        yield Case(
            id=make_case_id("fixup", owner, repo, str(number), f["filename"]),
            repo=f"{owner}/{repo}",
            pr_number=number,
            provenance="fixup",
            label="defect",
            file_path=f["filename"],
            diff_hunk=patch,
            base_commit_sha=base_sha,
            head_commit_sha=head_sha,
            defect_category=None,
            context=f"Reverted by PR #{revert_pr['number']}: {revert_pr.get('title', '')}",
            human_comment=None,
            source_urls=[origin_pr.get("html_url", ""), revert_pr.get("html_url", "")],
            created_at=origin_pr.get("merged_at"),
        )
