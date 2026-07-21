# reviewbench

Vendor benchmarks tell you which AI code reviewer is best on average, across
whatever fixed corpus the vendor picked. **reviewbench mines your own
repository's merged PR history to build a bespoke ground-truth set, then
scores any code-review tool — a raw model call or a real product like
PR-Agent — against it.** The output is a repo-specific answer to "is this
reviewer any good on my codebase," not a leaderboard position on someone
else's.

This is not a novel benchmark format. [SWR-Bench](docs/DESIGN.md#prior-art),
the [CodeReviewer dataset](docs/DESIGN.md#prior-art), and Qodo's published
benchmark all exist and are read before writing a line of this. The delta
here is narrower and more useful for a specific team: automatic,
repo-specific dataset generation, plus a tool-agnostic adapter protocol, so
the benchmark travels with the codebase instead of living as a static
academic artifact.

## Status

This project is being built in public phases. Current state:

| Phase | Scope | Status |
|---|---|---|
| 0 | Scoping, README, design doc | done |
| 1 | Dataset builder (mining ground truth from GitHub) | in progress — see below |
| 2 | Runner + adapters (raw model APIs, PR-Agent) | not started |
| 3 | Scoring (detection, localization, noise rate, LLM-judge) | not started |
| 4 | Ship (Docker, GitHub Action, results site) | not started |
| 5 | Write-up | not started |

Ground truth comes from three sources, of ascending difficulty to mine. See
[docs/DESIGN.md](docs/DESIGN.md) for the full rationale, including how this
project tries to avoid the known failure modes documented in the SWE-Bench+
and SWR-Bench papers.

1. **Fixup/revert mining** (`reviewbench/miners/fixup_miner.py`) —
   implemented. Finds merged PRs whose title/body is a GitHub-generated
   revert (`This reverts commit <sha>`), resolves the reverted commit back
   to the PR that introduced it via the GitHub "list pull requests
   associated with a commit" endpoint, and labels that original PR's diff
   as a positive (defect) case.
2. **Human review comments as labels**
   (`reviewbench/miners/review_comment_miner.py`) — implemented. Pulls
   inline review comments on merged PRs as positive (defect) cases, and
   samples uncommented hunks from the same PRs as negative (no-defect)
   cases. Filters low-signal comments (LGTM/praise-only) with a documented
   heuristic — see the design doc for its known limitations.
3. **Synthetic bug injection** — not yet implemented
   (`reviewbench/synthetic/injector.py` is a stub). Planned for later in
   Phase 1: inject bugs into clean merged-PR diffs with an LLM, in the style
   of DebugBench, and manually validate a sample.

Every emitted case is tagged with its `provenance` (`fixup`,
`review_comment`, or `synthetic`) so results can — and will — be reported
broken out by source rather than pooled. Synthetic bugs are easier to catch
than real ones; pooling them would inflate every reviewer's score.

## Usage

```bash
pip install -e ".[dev]"
export GITHUB_TOKEN=ghp_...

python -m reviewbench.cli build-dataset \
  --repo psf/requests \
  --sources fixup,review_comment \
  --limit 300 \
  --out data/requests.jsonl
```

Each line of the output is one `Case` (see [reviewbench/models.py](reviewbench/models.py)):
diff hunk, surrounding file context, base/head commit SHAs, label
(`defect`/`no_defect`), defect category (when known), provenance tag, the
originating human comment (if any), and source URLs for traceability back
to the PR/commit.

**Below ~200-300 cases per repo, confidence intervals on any score computed
from this dataset are not meaningful — the CLI will warn but will not
refuse to write a smaller file.** A random sample of generated cases should
be hand-verified before trusting a run; that verification error rate should
be reported alongside any score, not hidden.

## Development

```bash
pip install -e ".[dev]"
pytest
```

Tests mock the GitHub API (no network, no token required) and exercise the
miner parsing logic and the dataset writer/dedup path directly.
