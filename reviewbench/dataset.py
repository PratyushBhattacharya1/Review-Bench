"""Orchestrates the miners into a single JSONL dataset for one repo."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterator

from reviewbench.github_client import GitHubClient
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
        yield from miner(client, owner, repo, pr_scan_limit=pr_scan_limit)


def build_and_write(
    client: GitHubClient,
    owner: str,
    repo: str,
    out_path: str | Path,
    *,
    sources: list[str],
    pr_scan_limit: int = 300,
    model_client: StructuredModelClient | None = None,
    synthetic_limit: int | None = None,
) -> dict[str, int]:
    """Build the dataset and write it to `out_path`. Returns a summary
    dict with total case count and a per-provenance/per-label breakdown,
    which the CLI prints (including the low-N warning from docs/DESIGN.md).
    """
    cases = list(
        build_dataset(
            client,
            owner,
            repo,
            sources=sources,
            pr_scan_limit=pr_scan_limit,
            model_client=model_client,
            synthetic_limit=synthetic_limit,
        )
    )
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
