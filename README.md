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

New here (human or Claude)? Read [CLAUDE.md](CLAUDE.md) and
[docs/STATUS.md](docs/STATUS.md) first. `docs/DESIGN.md` is an append-only
audit trail whose early status claims are superseded by design.

This project is being built in public phases. Current state:

| Phase | Scope | Status |
|---|---|---|
| 0 | Scoping, README, design doc | done |
| 1 | Dataset builder (all three ground-truth sources) | code complete; **labels not yet trustworthy** — see below |
| 2 | Runner + adapters (Anthropic, OpenAI) | built; PR-Agent adapter deferred to Phase 4 |
| 3 | Scoring (detection, noise rate, cost/latency) | built; LLM-judge semantic match deferred |
| 4 | Ship (Docker, GitHub Action, results site) | not started |
| 5 | Write-up | **next priority** — findings are measured and need no further spend |

**Stated plainly: label quality still gates publishing any score.** The
pipeline runs end to end — mine, review, score — but the three sources are
not equally trustworthy, and every claim below is measured rather than
assumed:

| Source | Corpus | Label quality | Usable? |
|---|---|---|---|
| `fixup` | 169 cases, 4 repos | clean after filtering; positives only | yes, but cannot measure precision or noise rate alone |
| `synthetic` | not yet run (needs an API key) | clean by construction, paired +/- | the source that makes noise rate measurable |
| `review_comment` | 201 cases | 20% precision (strict) / 47.5% (generous) | **no** — documented as a negative result |

`review_comment` is the approach the CodeReviewer dataset uses, and
measuring it at 20% precision on a well-reviewed repo is a finding, not a
setback. Every measurement and every open defect lives in
[docs/DESIGN.md](docs/DESIGN.md).

Ground truth comes from three sources, of ascending difficulty to mine. See
[docs/DESIGN.md](docs/DESIGN.md) for the full rationale, including how this
project tries to avoid the known failure modes documented in the SWE-Bench+
and SWR-Bench papers.

1. **Fixup/revert mining** (`reviewbench/miners/fixup_miner.py`) —
   implemented. Finds revert PRs via GitHub search (reverts are rare and
   old, so a recency scan finds none), then resolves what each one undid.
   Both reference styles are handled: GitHub's generated
   `This reverts commit <sha>` footer *and* human-written `Reverts #2442`
   references — measured on `psf/requests`, only 1 revert in 8 uses the
   footer. Oversized reverts are excluded as unlocalizable, and cases are
   stratified by whether the revert itself cites a bug ticket.
2. **Human review comments as labels**
   (`reviewbench/miners/review_comment_miner.py`) — implemented. Pulls
   inline review comments on merged PRs as positive (defect) cases, and
   samples uncommented hunks from the same PRs as negative (no-defect)
   cases. Filters low-signal comments (LGTM/praise-only) with a documented
   heuristic — see the design doc for its known limitations.
3. **Synthetic bug injection** (`reviewbench/synthetic/injector.py`) —
   implemented. Sources clean merged-PR hunks
   (`reviewbench/miners/clean_pr_miner.py` — merged, not a revert, no
   substantive comments), then asks a model to inject exactly one realistic
   bug, in the style of DebugBench. Emits the injected hunk as a `defect`
   case and the clean original as a paired `no_defect` case, so
   false-positive rate is measurable on the same code minus the bug.
   Injections the model itself flags as obvious-on-sight are dropped by
   default. **Synthetic cases are unvalidated by construction** — DebugBench
   used a model to inject and humans to validate, and skipping the second
   half produces a benchmark that measures nothing.

Every emitted case is tagged with its `provenance` (`fixup`,
`review_comment`, or `synthetic`) so results can — and will — be reported
broken out by source rather than pooled. Synthetic bugs are easier to catch
than real ones; pooling them would inflate every reviewer's score.

## Usage

```bash
pip install -e ".[dev]"
export GITHUB_TOKEN=ghp_...

# The two organic sources — free, no model calls.
python -m reviewbench.cli build-dataset \
  --repo psf/requests \
  --sources fixup,review_comment \
  --limit 300 \
  --out data/requests.jsonl
```

Synthetic injection needs the Anthropic SDK and an API key, and costs money
per case, so it is opt-in:

```bash
pip install -e ".[synthetic]"
export ANTHROPIC_API_KEY=sk-ant-...

python -m reviewbench.cli build-dataset \
  --repo psf/requests \
  --sources fixup,review_comment,synthetic \
  --synthetic-limit 50 \
  --out data/requests.jsonl
```

Model responses are cached on disk keyed by a hash of the full request
(`.reviewbench_cache/` by default), so re-running a build after a crash or
a tweak elsewhere in the pipeline does not re-pay for injections you have
already generated. `--no-cache` disables it.

### Scoring a reviewer

```bash
python -m reviewbench.cli run \
  --dataset data/requests.jsonl \
  --reviewer anthropic --model claude-opus-4-8 \
  --out data/preds.jsonl

python -m reviewbench.cli score \
  --dataset data/requests.jsonl \
  --predictions data/preds.jsonl
```

Scores are always broken out by provenance, never pooled, and rows whose
ratios cannot be interpreted are flagged inline — `fixup` emits positives
only, so its precision is 100% by construction and the report says so rather
than letting the number stand. Cost and latency are reported per case; an
unpriced model reports `None` rather than `0.0`, because zero reads as free.

Each line of the dataset is one `Case` (see [reviewbench/models.py](reviewbench/models.py)):
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

Every test runs with no network, no GitHub token, and no API key. The
GitHub API is mocked; the injector and the reviewer adapters run against
fakes. That is the payoff of `StructuredModelClient` and `Reviewer` being
Protocols rather than base classes — the whole mine-review-score pipeline is
exercisable offline.
