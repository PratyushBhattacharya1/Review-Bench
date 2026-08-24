"""Orchestrates the miners into a single JSONL dataset across one or more repos."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Iterator

from reviewbench.github_client import GitHubClient, GitHubError
from reviewbench.miners.clean_pr_miner import mine_clean_hunks
from reviewbench.miners.fixup_miner import mine_fixups
from reviewbench.miners.review_comment_miner import mine_review_comments
from reviewbench.model_client import StructuredModelClient
from reviewbench.models import Case, write_jsonl
from reviewbench.synthetic.injector import inject_synthetic_bugs

logger = logging.getLogger(__name__)

MIN_RECOMMENDED_CASES = 200

_GITHUB_MINERS = {
    "fixup": mine_fixups,
    "review_comment": mine_review_comments,
}
KNOWN_SOURCES = set(_GITHUB_MINERS) | {"synthetic"}


def build_dataset(
    client: GitHubClient,
    owner: str,
    repo: str,
    *,
    sources: list[str],
    pr_scan_limit: int = 300,
    model_client: StructuredModelClient | None = None,
    synthetic_limit: int | None = None,
    keep_machine_authored: bool = False,
) -> Iterator[Case]:
    """Run the requested miners and yield every Case produced. Callers are
    responsible for persisting (see `write_jsonl`); this stays a generator
    so a caller can also do streaming analysis without buffering.

    The `synthetic` source needs a `model_client` — it is the only source
    that costs money per case, so it stays opt-in and explicit rather than
    running by default.
    """
    unknown = set(sources) - KNOWN_SOURCES
    if unknown:
        raise ValueError(f"Unknown source(s): {sorted(unknown)}. Known: {sorted(KNOWN_SOURCES)}")

    for source in sources:
        if source == "synthetic":
            if model_client is None:
                raise ValueError(
                    "Source 'synthetic' requires a model client. Pass --anthropic-api-key "
                    "or set $ANTHROPIC_API_KEY, or drop 'synthetic' from --sources."
                )
            logger.info("Running miner: synthetic (LLM injection)")
            clean = mine_clean_hunks(client, owner, repo, pr_scan_limit=pr_scan_limit)
            yield from inject_synthetic_bugs(model_client, clean, limit=synthetic_limit)
            continue

        miner = _GITHUB_MINERS[source]
        logger.info("Running miner: %s", source)
        # Only the comment miner has an author to filter on.
        extra = {"keep_machine_authored": keep_machine_authored} if source == "review_comment" else {}
        yield from miner(client, owner, repo, pr_scan_limit=pr_scan_limit, **extra)


def parse_repo(spec: str) -> tuple[str, str]:
    """Split an "owner/name" spec, raising a clear error if it is malformed."""
    if spec.count("/") != 1 or not all(spec.split("/")):
        raise ValueError(f"--repo must be 'owner/name', got {spec!r}")
    owner, name = spec.split("/")
    return owner, name


def build_and_write(
    client: GitHubClient,
    repos: list[str],
    out_path: str | Path,
    *,
    sources: list[str],
    pr_scan_limit: int = 300,
    model_client: StructuredModelClient | None = None,
    synthetic_limit: int | None = None,
    keep_machine_authored: bool = False,
) -> dict[str, Any]:
    """Mine every repo in `repos` into one dataset at `out_path`.

    Returns a summary broken down by provenance, label, and repo. The
    per-repo breakdown matters because label quality is not repo-invariant:
    a repo that reverts to unblock CI produces different `fixup` precision
    than one that reverts only for defects, and pooling would hide that.

    One repo failing does not end the run. A 4-repo scan is tens of minutes
    of API calls, and losing all of it because the third repo 404s would be
    the same failure mode as the dropped connection that GitHubClient now
    retries.
    """
    cases: list[Case] = []
    failures: dict[str, str] = {}

    for spec in repos:
        owner, name = parse_repo(spec)
        logger.info("Mining %s/%s", owner, name)
        try:
            cases.extend(
                build_dataset(
                    client,
                    owner,
                    name,
                    sources=sources,
                    pr_scan_limit=pr_scan_limit,
                    model_client=model_client,
                    synthetic_limit=synthetic_limit,
                    keep_machine_authored=keep_machine_authored,
                )
            )
        except GitHubError as e:
            logger.error("Mining %s/%s failed, continuing: %s", owner, name, e)
            failures[spec] = str(e)

    written = write_jsonl(cases, out_path)

    by_provenance: dict[str, int] = {}
    by_label: dict[str, int] = {}
    by_repo: dict[str, dict[str, int]] = {}
    for case in cases:
        by_provenance[case.provenance] = by_provenance.get(case.provenance, 0) + 1
        by_label[case.label] = by_label.get(case.label, 0) + 1
        repo_counts = by_repo.setdefault(case.repo, {})
        repo_counts["total"] = repo_counts.get("total", 0) + 1
        repo_counts[case.provenance] = repo_counts.get(case.provenance, 0) + 1

    if written < MIN_RECOMMENDED_CASES:
        logger.warning(
            "Only %d cases written (< %d recommended minimum). "
            "Confidence intervals on scores computed from this dataset will not be meaningful — see docs/DESIGN.md.",
            written,
            MIN_RECOMMENDED_CASES,
        )

    return {
        "total": written,
        "by_provenance": by_provenance,
        "by_label": by_label,
        "by_repo": by_repo,
        "failures": failures,
    }
