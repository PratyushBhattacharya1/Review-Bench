"""Sources clean merged-PR diffs, used as input to synthetic injection.

"Clean" here means: merged, not itself a revert, and carrying no
substantive review comments. That is a weaker claim than "defect-free" —
reviewers miss things, and this miner inherits exactly the limitation
docs/DESIGN.md records for source 2. It is good enough as a starting point
for injection (where the label comes from the injected bug, not from the
original being provably clean), and it is *not* good enough to emit these
as no_defect cases on their own — which is why this module produces
candidates for the injector rather than dataset rows.
"""

from __future__ import annotations

import logging
from typing import Iterator

from reviewbench.github_client import GitHubClient, GitHubError
from reviewbench.miners.review_comment_miner import is_substantive_comment
from reviewbench.models import Case, make_case_id
from reviewbench.paths import is_reviewable_code_path

logger = logging.getLogger(__name__)

_MIN_HUNK_CHARS = 80


def mine_clean_hunks(
    client: GitHubClient,
    owner: str,
    repo: str,
    *,
    pr_scan_limit: int = 100,
    files_per_pr: int = 1,
) -> Iterator[Case]:
    """Yield clean diff hunks as provisional `no_defect` Cases.

    These are injection *inputs*, not dataset rows — `inject_synthetic_bugs`
    consumes them and emits the actual synthetic cases.
    """
    scanned = 0
    for pr in client.get_paginated(
        f"/repos/{owner}/{repo}/pulls", params={"state": "closed", "sort": "updated", "direction": "desc"}
    ):
        if scanned >= pr_scan_limit:
            break
        if not pr.get("merged_at"):
            continue
        if (pr.get("title") or "").lower().startswith("revert"):
            continue
        scanned += 1

        number = pr["number"]
        try:
            comments = list(client.get_paginated(f"/repos/{owner}/{repo}/pulls/{number}/comments"))
        except GitHubError as e:
            logger.warning("Could not fetch comments for PR #%s: %s", number, e)
            continue

        commented_paths = {c["path"] for c in comments if is_substantive_comment(c.get("body", ""))}

        try:
            files = list(client.get_paginated(f"/repos/{owner}/{repo}/pulls/{number}/files"))
        except GitHubError as e:
            logger.warning("Could not fetch files for PR #%s: %s", number, e)
            continue

        emitted = 0
        for f in files:
            if emitted >= files_per_pr:
                break
            patch = f.get("patch")
            if not patch or len(patch) < _MIN_HUNK_CHARS:
                # Too small to hide a meaningful bug in.
                continue
            if f["filename"] in commented_paths:
                continue
            if not is_reviewable_code_path(f["filename"]):
                # Injecting a bug into a changelog or a CI config would
                # produce a case that is out of scope before the model is
                # even called — and would still cost an API request.
                continue

            emitted += 1
            yield Case(
                id=make_case_id("clean", owner, repo, str(number), f["filename"]),
                repo=f"{owner}/{repo}",
                pr_number=number,
                provenance="synthetic",
                label="no_defect",
                file_path=f["filename"],
                diff_hunk=patch,
                base_commit_sha=pr.get("base", {}).get("sha", ""),
                head_commit_sha=pr.get("head", {}).get("sha", ""),
                defect_category=None,
                context="Clean merged hunk, sourced as an injection candidate.",
                human_comment=None,
                source_urls=[pr.get("html_url", "")],
                created_at=pr.get("merged_at"),
            )
