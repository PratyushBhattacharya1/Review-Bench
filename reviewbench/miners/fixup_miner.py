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
from typing import Iterator

from reviewbench.github_client import GitHubClient, GitHubError
from reviewbench.models import Case, make_case_id

logger = logging.getLogger(__name__)

_REVERT_COMMIT_RE = re.compile(r"This reverts commit ([0-9a-f]{40})", re.IGNORECASE)
_REVERT_TITLE_RE = re.compile(r"^revert\b", re.IGNORECASE)


def _is_revert_pr(pr: dict) -> str | None:
    """Return the reverted commit SHA if `pr` looks like a GitHub-generated
    revert PR, else None.
    """
    body = pr.get("body") or ""
    match = _REVERT_COMMIT_RE.search(body)
    if match:
        return match.group(1)
    if _REVERT_TITLE_RE.match(pr.get("title") or ""):
        # Title says "Revert ..." but body doesn't carry the standard
        # GitHub-generated footer (e.g. manual revert). Not enough signal
        # to resolve a commit SHA, so skip rather than guess.
        logger.debug("PR #%s looks like a manual revert; skipping (no resolvable commit SHA)", pr.get("number"))
    return None


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


def mine_fixups(client: GitHubClient, owner: str, repo: str, *, pr_scan_limit: int = 300) -> Iterator[Case]:
    """Scan up to `pr_scan_limit` most-recently-updated merged PRs for
    reverts, and yield one Case per file changed in the reverted (origin)
    PR.
    """
    scanned = 0
    for pr in client.get_paginated(
        f"/repos/{owner}/{repo}/pulls", params={"state": "closed", "sort": "updated", "direction": "desc"}
    ):
        if scanned >= pr_scan_limit:
            break
        scanned += 1

        if not pr.get("merged_at"):
            continue

        sha = _is_revert_pr(pr)
        if not sha:
            continue

        origin_pr = _find_origin_pr(client, owner, repo, sha, revert_pr_number=pr["number"])
        if origin_pr is None:
            logger.info("Revert PR #%s found but origin PR could not be resolved; skipping", pr["number"])
            continue

        yield from _cases_for_origin_pr(client, owner, repo, origin_pr, revert_pr=pr)


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
