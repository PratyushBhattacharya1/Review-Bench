"""CLI entry point.

    python -m reviewbench.cli build-dataset --repo owner/name --out data.jsonl
    python -m reviewbench.cli run --dataset data.jsonl --reviewer anthropic --out preds.jsonl
    python -m reviewbench.cli score --dataset data.jsonl --predictions preds.jsonl
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from reviewbench.cache import DEFAULT_CACHE_DIR, ResponseCache
from reviewbench.dataset import build_and_write, parse_repo
from reviewbench.github_client import GitHubClient
from reviewbench.model_client import DEFAULT_MODEL, AnthropicClient
from reviewbench.models import read_jsonl
from reviewbench.runner import read_predictions, run_reviewer, write_predictions
from reviewbench.scoring import format_report, score


def _build_dataset_command(args: argparse.Namespace) -> int:
    repos = [r.strip() for r in args.repo.split(",") if r.strip()]
    if not repos:
        print("error: --repo requires at least one 'owner/name'", file=sys.stderr)
        return 2
    try:
        for spec in repos:
            parse_repo(spec)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    token = args.token or os.environ.get("GITHUB_TOKEN")
    if not token:
        print(
            "warning: no GitHub token found (--token or $GITHUB_TOKEN). "
            "Unauthenticated requests are rate-limited to 60/hour.",
            file=sys.stderr,
        )

    sources = [s.strip() for s in args.sources.split(",") if s.strip()]

    model_client = None
    cache = None
    if "synthetic" in sources:
        api_key = args.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")
        cache = ResponseCache(args.cache_dir, enabled=not args.no_cache)
        try:
            model_client = AnthropicClient(model=args.model, api_key=api_key, cache=cache)
        except ImportError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2

    client = GitHubClient(token=token)

    summary = build_and_write(
        client,
        repos,
        args.out,
        sources=sources,
        pr_scan_limit=args.limit,
        model_client=model_client,
        synthetic_limit=args.synthetic_limit,
        keep_machine_authored=args.keep_machine_authored,
    )

    print(f"Wrote {summary['total']} cases to {args.out}")
    print(f"  by provenance: {summary['by_provenance']}")
    print(f"  by label:      {summary['by_label']}")
    if len(summary["by_repo"]) > 1:
        print("  by repo:")
        for repo_name, counts in sorted(summary["by_repo"].items()):
            breakdown = ", ".join(
                f"{k}={v}" for k, v in sorted(counts.items()) if k != "total"
            )
            print(f"      {repo_name:28} {counts['total']:5}  ({breakdown})")
    for spec, err in summary.get("failures", {}).items():
        print(f"  WARNING: {spec} failed and was skipped: {err}", file=sys.stderr)
    if cache is not None:
        print(f"  model cache:   {cache.stats}")
    if summary["by_provenance"].get("synthetic"):
        print(
            "\nNote: synthetic cases are UNVALIDATED. Hand-verify a sample and report the\n"
            "error rate alongside any score computed from them (see docs/DESIGN.md)."
        )
    return 0



def _make_reviewer(args: argparse.Namespace):
    """Build the requested reviewer adapter.

    Import is deferred per-adapter so that missing an optional SDK only
    breaks the reviewer that needs it.
    """
    cache = ResponseCache(args.cache_dir, enabled=not args.no_cache)
    if args.reviewer == "anthropic":
        from reviewbench.reviewers.anthropic_reviewer import AnthropicReviewer

        model = args.model or DEFAULT_MODEL
        return AnthropicReviewer(
            model=model, api_key=os.environ.get("ANTHROPIC_API_KEY"), cache=cache
        )
    if args.reviewer == "openai":
        from reviewbench.reviewers.openai_reviewer import (
            DEFAULT_OPENAI_MODEL,
            OpenAIReviewer,
        )

        model = args.model or DEFAULT_OPENAI_MODEL
        return OpenAIReviewer(
            model=model, api_key=os.environ.get("OPENAI_API_KEY"), cache=cache
        )
    raise ValueError(f"Unknown reviewer: {args.reviewer}")


def _run_command(args: argparse.Namespace) -> int:
    cases = list(read_jsonl(args.dataset))
    if args.limit:
        cases = cases[: args.limit]
    if not cases:
        print(f"error: no cases in {args.dataset}", file=sys.stderr)
        return 2

    try:
        reviewer = _make_reviewer(args)
    except ImportError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    written = write_predictions(run_reviewer(reviewer, cases), args.out)
    print(f"Reviewed {written} cases with {reviewer.name}:{reviewer.model} -> {args.out}")

    predictions = list(read_predictions(args.out))
    errored = sum(1 for p in predictions if p.error)
    cached = sum(1 for p in predictions if p.cached)
    priced = [p.estimated_cost_usd for p in predictions if p.estimated_cost_usd is not None]
    if cached:
        print(f"  {cached} served from cache (no tokens spent)")
    if errored:
        print(f"  WARNING: {errored} reviews errored", file=sys.stderr)
    if priced:
        print(f"  estimated cost: ${sum(priced):.4f}")
    return 0


def _score_command(args: argparse.Namespace) -> int:
    cases = list(read_jsonl(args.dataset))
    predictions = list(read_predictions(args.predictions))
    scores = score(cases, predictions)

    if args.json:
        import json

        print(json.dumps({k: v.to_dict() for k, v in scores.items()}, indent=2, sort_keys=True))
        return 0

    print(format_report(scores))
    print()
    print(
        "Reminder: report the dataset's own label error rate alongside these "
        "numbers. A score computed on unverified labels is not a measurement "
        "(see docs/DESIGN.md)."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="reviewbench")
    parser.add_argument("-v", "--verbose", action="store_true", help="enable INFO-level logging")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build-dataset", help="mine a repo's PR history into a JSONL dataset")
    build.add_argument(
        "--repo",
        required=True,
        help="owner/name, or a comma-separated list: psf/requests,django/django",
    )
    build.add_argument("--out", required=True, help="output JSONL path")
    build.add_argument(
        "--sources",
        default="fixup,review_comment",
        help="comma-separated: fixup, review_comment, synthetic (default: fixup,review_comment)",
    )
    build.add_argument("--limit", type=int, default=300, help="max merged PRs to scan per miner (default: 300)")
    build.add_argument("--token", default=None, help="GitHub token (defaults to $GITHUB_TOKEN)")
    build.add_argument(
        "--keep-machine-authored",
        action="store_true",
        help="keep comments written by bots/AI reviewers as positives (default: excluded, "
             "since benchmarking a reviewer against another reviewer's output is circular)",
    )

    synth = build.add_argument_group("synthetic injection (only used with --sources synthetic)")
    synth.add_argument("--anthropic-api-key", default=None, help="defaults to $ANTHROPIC_API_KEY")
    synth.add_argument("--model", default=DEFAULT_MODEL, help=f"injection model (default: {DEFAULT_MODEL})")
    synth.add_argument("--synthetic-limit", type=int, default=50, help="max bugs to inject (default: 50)")
    synth.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR), help="model response cache directory")
    synth.add_argument("--no-cache", action="store_true", help="disable the model response cache")

    build.set_defaults(func=_build_dataset_command)

    run = subparsers.add_parser("run", help="run a reviewer over a dataset")
    run.add_argument("--dataset", required=True, help="JSONL dataset from build-dataset")
    run.add_argument("--out", required=True, help="output predictions JSONL")
    run.add_argument(
        "--reviewer", default="anthropic", choices=["anthropic", "openai"],
        help="which adapter to score (default: anthropic)",
    )
    run.add_argument("--model", default=None, help="model id (defaults per reviewer)")
    run.add_argument("--limit", type=int, default=None, help="only review the first N cases")
    run.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR), help="response cache directory")
    run.add_argument("--no-cache", action="store_true", help="disable the response cache")
    run.set_defaults(func=_run_command)

    scorer = subparsers.add_parser("score", help="score predictions against a dataset")
    scorer.add_argument("--dataset", required=True, help="JSONL dataset")
    scorer.add_argument("--predictions", required=True, help="JSONL predictions from run")
    scorer.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    scorer.set_defaults(func=_score_command)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
