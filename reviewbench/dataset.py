"""Orchestrates the miners into a single JSONL dataset for one repo."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterator

from reviewbench.github_client import GitHubClient
from reviewbench.miners.fixup_miner import mine_fixups
from reviewbench.miners.review_comment_miner import mine_review_comments
from reviewbench.models import Case, write_jsonl

logger = logging.getLogger(__name__)

MIN_RECOMMENDED_CASES = 200

_SOURCE_MINERS = {
    "fixup": mine_fixups,
    "review_comment": mine_review_comments,
}


def build_dataset(
    client: GitHubClient,
    owner: str,
    repo: str,
    *,
    sources: list[str],
    pr_scan_limit: int = 300,
) -> Iterator[Case]:
    """Run the requested miners and yield every Case produced. Callers are
    responsible for persisting (see `write_jsonl`); this stays a generator
    so a caller can also do streaming analysis without buffering.
    """
    unknown = set(sources) - set(_SOURCE_MINERS) - {"synthetic"}
    if unknown:
        raise ValueError(f"Unknown source(s): {sorted(unknown)}. Known: {sorted(_SOURCE_MINERS)} + 'synthetic'")

    for source in sources:
        if source == "synthetic":
            logger.warning("Source 'synthetic' requested but not yet implemented (see docs/DESIGN.md) — skipping")
            continue
        miner = _SOURCE_MINERS[source]
        logger.info("Running miner: %s", source)
        yield from miner(client, owner, repo, pr_scan_limit=pr_scan_limit)


def build_and_write(
    client: GitHubClient,
    owner: str,
    repo: str,
    out_path: str | Path,
    *,
    sources: list[str],
    pr_scan_limit: int = 300,
) -> dict[str, int]:
    """Build the dataset and write it to `out_path`. Returns a summary
    dict with total case count and a per-provenance/per-label breakdown,
    which the CLI prints (including the low-N warning from docs/DESIGN.md).
    """
    cases = list(build_dataset(client, owner, repo, sources=sources, pr_scan_limit=pr_scan_limit))
    written = write_jsonl(cases, out_path)

    by_provenance: dict[str, int] = {}
    by_label: dict[str, int] = {}
    for case in cases:
        by_provenance[case.provenance] = by_provenance.get(case.provenance, 0) + 1
        by_label[case.label] = by_label.get(case.label, 0) + 1

    if written < MIN_RECOMMENDED_CASES:
        logger.warning(
            "Only %d cases written (< %d recommended minimum). "
            "Confidence intervals on scores computed from this dataset will not be meaningful — see docs/DESIGN.md.",
            written,
            MIN_RECOMMENDED_CASES,
        )

    return {"total": written, "by_provenance": by_provenance, "by_label": by_label}
