"""CLI entry point.

    python -m reviewbench.cli build-dataset --repo owner/name --out data.jsonl
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from reviewbench.dataset import build_and_write
from reviewbench.github_client import GitHubClient


def _build_dataset_command(args: argparse.Namespace) -> int:
    if "/" not in args.repo:
        print(f"error: --repo must be 'owner/name', got {args.repo!r}", file=sys.stderr)
        return 2
    owner, repo = args.repo.split("/", 1)

    token = args.token or os.environ.get("GITHUB_TOKEN")
    if not token:
        print(
            "warning: no GitHub token found (--token or $GITHUB_TOKEN). "
            "Unauthenticated requests are rate-limited to 60/hour.",
            file=sys.stderr,
        )

    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    client = GitHubClient(token=token)

    summary = build_and_write(
        client, owner, repo, args.out, sources=sources, pr_scan_limit=args.limit
    )

    print(f"Wrote {summary['total']} cases to {args.out}")
    print(f"  by provenance: {summary['by_provenance']}")
    print(f"  by label:      {summary['by_label']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="reviewbench")
    parser.add_argument("-v", "--verbose", action="store_true", help="enable INFO-level logging")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build-dataset", help="mine a repo's PR history into a JSONL dataset")
    build.add_argument("--repo", required=True, help="owner/name, e.g. psf/requests")
    build.add_argument("--out", required=True, help="output JSONL path")
    build.add_argument(
        "--sources",
        default="fixup,review_comment",
        help="comma-separated: fixup, review_comment, synthetic (default: fixup,review_comment)",
    )
    build.add_argument("--limit", type=int, default=300, help="max merged PRs to scan per miner (default: 300)")
    build.add_argument("--token", default=None, help="GitHub token (defaults to $GITHUB_TOKEN)")
    build.set_defaults(func=_build_dataset_command)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
