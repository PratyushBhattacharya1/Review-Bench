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
from reviewbench.paths import is_reviewable_code_path

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

# Structured severity/confidence headers, the signature of AI code-review
# output. Both halves are required — a bold ALL-CAPS severity token *and* a
# confidence score — because either alone appears in human writing
# ("**NOTE** this is fragile", "I have no confidence in this test").
# Together they are machine-generated in every instance observed.
#
# Tuned on real output found in psf/requests PR #7431:
#     ⚠️ **HIGH** — *test_coverage* **Confidence:** 80%
_MACHINE_SEVERITY_RE = re.compile(
    r"\*\*(CRITICAL|HIGH|MEDIUM|LOW|INFO|WARNING|BLOCKER|NIT)\*\*", re.MULTILINE
)
_MACHINE_CONFIDENCE_RE = re.compile(r"\*{0,2}Confidence:?\*{0,2}\s*:?\s*\d{1,3}\s*%", re.IGNORECASE)


def is_machine_authored(comment: dict) -> bool:
    """True if `comment` was written by a tool rather than a person.

    Ground truth that contains another code reviewer's output makes
    benchmarking a code reviewer against it circular: the score stops
    measuring "did it find the bug" and starts measuring "did it agree with
    the other tool". None of the prior art this project builds on
    (SWR-Bench, CodeReviewer, Qodo's benchmark) appears to control for it.

    Two independent checks, because one is not enough. GitHub's own
    `user.type == "Bot"` catches app-authored comments such as
    `github-advanced-security[bot]`'s CodeQL findings. It does **not** catch
    AI review tools that post through an ordinary user account — measured on
    psf/requests, `sdm0p` posts severity/confidence-formatted review output
    with `user.type == "User"`. The formatting heuristic covers that gap.

    A heuristic, and honest about it: it recognises the shapes observed in
    real data, not every shape that exists. `comment_author` is recorded on
    every case so a missed one stays auditable after the fact.
    """
    user = comment.get("user") or {}
    if (user.get("type") or "").lower() == "bot":
        return True

    body = comment.get("body") or ""
    return bool(_MACHINE_SEVERITY_RE.search(body) and _MACHINE_CONFIDENCE_RE.search(body))


def is_substantive_comment(body: str) -> bool:
    body = (body or "").strip()
    if len(body) < _MIN_SUBSTANTIVE_LEN:
        return False
    if _TRIVIAL_COMMENT_RE.match(body):
        return False
    return True


def is_thread_root(comment: dict) -> bool:
    """True if `comment` initiates a review thread rather than replying to one.

    GitHub's PR-comments endpoint returns every comment in a thread, so the
    back-and-forth replies ("good call", "thanks, pushed", "yes we could
    remove them") come back alongside the comment that actually raised the
    issue. Those replies clear the length + non-trivial filters and were
    landing as `defect` cases — a hand-verified run on psf/requests found
    roughly half the positive labels were thread chatter, not issues.

    A reply carries `in_reply_to_id`; a thread-initiating comment does not.
    Keeping only the roots is what makes "a comment marks a defect" a
    defensible approximation instead of "some human said something on this
    diff." It is still an approximation (see docs/DESIGN.md) — a root
    comment can be a question or a nit — but it removes the largest, most
    systematic source of label noise.
    """
    return comment.get("in_reply_to_id") is None


def mine_review_comments(
    client: GitHubClient,
    owner: str,
    repo: str,
    *,
    pr_scan_limit: int = 200,
    negatives_per_pr: int = 1,
    keep_machine_authored: bool = False,
) -> Iterator[Case]:
    """Scan up to `pr_scan_limit` most-recently-updated merged PRs. Yields
    one positive Case per substantive inline review comment, plus up to
    `negatives_per_pr` no_defect Cases per PR sampled from files that
    received no substantive comment.

    Comments written by tools are excluded from positives by default; see
    `is_machine_authored` for why that matters. `keep_machine_authored=True`
    retains them, which is how the contamination rate gets measured rather
    than silently discarded.
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

        yield from _cases_for_pr(
            client,
            owner,
            repo,
            pr,
            negatives_per_pr=negatives_per_pr,
            keep_machine_authored=keep_machine_authored,
        )


def _cases_for_pr(
    client: GitHubClient,
    owner: str,
    repo: str,
    pr: dict,
    *,
    negatives_per_pr: int,
    keep_machine_authored: bool = False,
) -> Iterator[Case]:
    number = pr["number"]
    base_sha = pr.get("base", {}).get("sha", "")
    head_sha = pr.get("head", {}).get("sha", "")

    try:
        comments = list(client.get_paginated(f"/repos/{owner}/{repo}/pulls/{number}/comments"))
    except GitHubError as e:
        logger.warning("Could not fetch review comments for PR #%s: %s", number, e)
        comments = []

    # A file that drew any substantive human attention — even in a reply —
    # is not a safe "clean" negative, so exclude it from the negative pool.
    # Positive cases, though, come only from thread roots (see is_thread_root).
    commented_paths: set[str] = {
        c["path"] for c in comments if is_substantive_comment(c.get("body", ""))
    }

    for c in comments:
        if not is_thread_root(c):
            continue
        if not is_substantive_comment(c.get("body", "")):
            continue
        if not is_reviewable_code_path(c["path"]):
            continue
        if not keep_machine_authored and is_machine_authored(c):
            logger.debug(
                "Skipping machine-authored comment on PR #%s by %s",
                number,
                (c.get("user") or {}).get("login"),
            )
            continue
        author = c.get("user") or {}
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
            comment_author=author.get("login"),
            comment_author_type=author.get("type"),
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

    uncommented = [
        f
        for f in files
        if f["filename"] not in commented_paths
        and f.get("patch")
        and is_reviewable_code_path(f["filename"])
    ]
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
