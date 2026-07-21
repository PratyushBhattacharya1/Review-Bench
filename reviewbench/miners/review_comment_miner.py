"""Source 2: human review comments as approximate defect labels, in the
tradition of the CodeReviewer dataset.

A comment on a diff hunk in a merged PR is treated as "a human reviewer
found something worth flagging here." This is an approximation, not
ground truth — absence of a comment is not proof of absence of a defect
(reviewers miss things; that gap is exactly what this whole project
exists to measure). See docs/DESIGN.md for the full caveat.

To make noise-rate scoring possible (see docs/DESIGN.md on why that
metric matters), this miner also samples uncommented hunks from the same
PRs as `no_defect` negatives.
"""

from __future__ import annotations

import logging
import re
from typing import Iterator

from reviewbench.github_client import GitHubClient, GitHubError
from reviewbench.models import Case, make_case_id

logger = logging.getLogger(__name__)

# Deliberately conservative: only strip comments that are almost certainly
# not about a defect. Anything ambiguous is kept, which biases toward
# false positives in the label (see docs/DESIGN.md) rather than silently
# discarding real signal.
_TRIVIAL_COMMENT_RE = re.compile(
    r"^\s*(lgtm|\+1|nice|nice one|thanks|thank you|good catch|done|fixed|ack|approved|👍|:\+1:)\s*[!.]*\s*$",
    re.IGNORECASE,
)
_MIN_SUBSTANTIVE_LEN = 15


def is_substantive_comment(body: str) -> bool:
    body = (body or "").strip()
    if len(body) < _MIN_SUBSTANTIVE_LEN:
        return False
    if _TRIVIAL_COMMENT_RE.match(body):
        return False
    return True


def mine_review_comments(
    client: GitHubClient,
    owner: str,
    repo: str,
    *,
    pr_scan_limit: int = 200,
    negatives_per_pr: int = 1,
) -> Iterator[Case]:
    """Scan up to `pr_scan_limit` most-recently-updated merged PRs. Yields
    one positive Case per substantive inline review comment, plus up to
    `negatives_per_pr` no_defect Cases per PR sampled from files that
    received no substantive comment.
    """
    scanned = 0
    for pr in client.get_paginated(
        f"/repos/{owner}/{repo}/pulls", params={"state": "closed", "sort": "updated", "direction": "desc"}
    ):
        if scanned >= pr_scan_limit:
            break
        if not pr.get("merged_at"):
            continue
        scanned += 1

        yield from _cases_for_pr(client, owner, repo, pr, negatives_per_pr=negatives_per_pr)


def _cases_for_pr(client: GitHubClient, owner: str, repo: str, pr: dict, *, negatives_per_pr: int) -> Iterator[Case]:
    number = pr["number"]
    base_sha = pr.get("base", {}).get("sha", "")
    head_sha = pr.get("head", {}).get("sha", "")

    try:
        comments = list(client.get_paginated(f"/repos/{owner}/{repo}/pulls/{number}/comments"))
    except GitHubError as e:
        logger.warning("Could not fetch review comments for PR #%s: %s", number, e)
        comments = []

    commented_paths: set[str] = set()
    for c in comments:
        if not is_substantive_comment(c.get("body", "")):
            continue
        commented_paths.add(c["path"])
        yield Case(
            id=make_case_id("review_comment", owner, repo, str(number), c["path"], str(c["id"])),
            repo=f"{owner}/{repo}",
            pr_number=number,
            provenance="review_comment",
            label="defect",
            file_path=c["path"],
            diff_hunk=c.get("diff_hunk", ""),
            base_commit_sha=base_sha,
            head_commit_sha=head_sha,
            defect_category=None,
            context=None,
            human_comment=c.get("body"),
            source_urls=[c.get("html_url", ""), pr.get("html_url", "")],
            created_at=c.get("created_at"),
        )

    if negatives_per_pr <= 0:
        return

    try:
        files = list(client.get_paginated(f"/repos/{owner}/{repo}/pulls/{number}/files"))
    except GitHubError as e:
        logger.warning("Could not fetch files for PR #%s: %s", number, e)
        return

    uncommented = [f for f in files if f["filename"] not in commented_paths and f.get("patch")]
    for f in uncommented[:negatives_per_pr]:
        yield Case(
            id=make_case_id("review_comment_neg", owner, repo, str(number), f["filename"]),
            repo=f"{owner}/{repo}",
            pr_number=number,
            provenance="review_comment",
            label="no_defect",
            file_path=f["filename"],
            diff_hunk=f["patch"],
            base_commit_sha=base_sha,
            head_commit_sha=head_sha,
            defect_category=None,
            context="Sampled as a negative: merged with no substantive review comment on this file.",
            human_comment=None,
            source_urls=[pr.get("html_url", "")],
            created_at=pr.get("merged_at"),
        )
