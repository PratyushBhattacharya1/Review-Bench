# Status — current state

Last updated: 2026-08-27. Branch `phase-1-dataset-builder`, 132 tests green.

This page is the **current** state. [`DESIGN.md`](DESIGN.md) is the
append-only audit trail and contains superseded status claims by design —
when the two disagree, this page wins.

## Phases

| Phase | Scope | Status |
|---|---|---|
| 0 | Scoping, README, design doc | done |
| 1 | Dataset builder, 3 ground-truth sources | built; label quality varies by source (below) |
| 2 | Runner + Anthropic/OpenAI adapters | built; PR-Agent adapter deferred to Phase 4 |
| 3 | Scoring (detection, noise, localization proxy, cost/latency) | built; LLM-judge semantic match deferred |
| 4 | Ship (Docker, GitHub Action, results site) | not started |
| 5 | Write-up | **next priority** |

## The corpus — the central fact of the project

| Source | Cases | Label quality | Usable? |
|---|---|---|---|
| `fixup` | 169 across 4 repos | clean after filtering; **positives only** | Yes — but cannot measure precision or noise rate on its own |
| `synthetic` | 0 — never run | clean by construction, paired +/− | Blocked on `ANTHROPIC_API_KEY`. The only source that makes noise rate computable |
| `review_comment` | 201 | 20% precision (strict) / 47.5% (generous) | **No.** Kept as a documented negative result |

`fixup` yield by repo: ansible/ansible 112, django/django 52,
psf/requests 6, scrapy/scrapy 1. Yield tracks revert *culture*, not repo
size — scrapy is substantial and simply reverts rarely.

`review_comment` is exactly the CodeReviewer dataset's method
("comments ≈ defects"). Measuring it at 20% precision on a well-reviewed
repo is a finding, not a setback — see the write-up plan below.

## Data files (`data/` is gitignored — local only)

| File | What it is |
|---|---|
| `fixup_multi.jsonl` | **The clean corpus.** 169 fixup cases, 4 repos |
| `requests_filtered.jsonl` | 207 cases, post-path-filter review_comment run on psf/requests |
| `e2e_cases.jsonl` / `e2e_preds.jsonl` | 100-case offline pipeline demo (fake reviewer, no credentials) |
| `requests_full.jsonl`, `requests_fixup.jsonl`, `requests_dedup.jsonl`, `requests.jsonl` | Historical runs, kept for before/after comparison |

## Open defects

Only genuinely open ones. DESIGN.md's "Known defects" list still shows
fixed items struck through for the record.

1. **`review_comment` positives are 53–80% non-defects.** Not fixed by
   thread-root filtering or path filtering — both were tried and measured.
   The noise is about comment *intent* (nits, questions, author
   self-explanations), which no lexical rule separates. Only the LLM judge
   could plausibly rescue it.
2. **`localization_rate` is a weak proxy.** It measures "a line-bearing
   finding landed inside the case's hunk", not "found the right bug".
   `fixup` ground truth knows the file but not the offending line, so
   nothing stronger is computable without a judge. The report labels it as
   a proxy.

## Next work, in order

### 1. Phase 5 write-up — the priority

Findings are measured and need no further spend. Deliverable:
`docs/WRITEUP.md`. The three strongest, each contradicting an assumption in
the prior art:

- **The standard approach measures 20% precision.** Thread-root filtering
  did not move it; the noise is intent, not form.
- **Benchmark circularity survives the obvious filter.** 8.1% of positives
  were machine-authored, and **43% of those carry `user.type == "User"`** —
  AI review tools posting from ordinary accounts. Filtering on
  `user.type == "Bot"`, the natural defence, misses nearly half.
- **Revert mining as usually described finds almost nothing.** Only 1 in 8
  real reverts uses the `This reverts commit <sha>` footer; the rest cite a
  PR number. Reverts are also old, so a recency scan finds none regardless
  — search is required.

Include the negative results. "I built the standard thing and measured it
as unusable" is the contribution.

### 2. Blocked — synthetic injection (needs `ANTHROPIC_API_KEY`)

Everything downstream is built and tested against fakes:

```bash
python -m reviewbench.cli build-dataset \
  --repo psf/requests,django/django \
  --sources synthetic --synthetic-limit 50 \
  --out data/synthetic.jsonl
```

Then hand-validate ~20 injections (real bug? in the hunk? subtle enough?)
and record the rate in DESIGN.md. DebugBench injected with a model and
validated with humans; skipping the second half is the failure this project
is written against.

Unblocks noise rate — uncomputable today because `fixup` has no negatives.

### 3. Blocked — LLM-as-judge (needs a key *and* a methodology pass)

Not just an API call. Required by construction: randomised ordering, a judge
from a **different model family** than the reviewer under test, and
validated judge-versus-human agreement on a labelled subset (MT-Bench
documents verbosity and position bias). Would upgrade `localization_rate`
to real semantic matching and is the only remaining candidate for rescuing
`review_comment`.

### 4. Phase 4 ship

Dockerfile, GitHub Action gating on regression, static results page. Runs on
the corpus that already exists.

## Open decisions awaiting the user

1. **Metrics framework.** The original brief argued for wrapping DeepEval or
   Inspect AI rather than hand-rolling. Current code hand-rolls, reasoning
   that P/R/F1 is ~50 lines and the provenance breakdown is custom either
   way — so a framework earns its place at the judge step, not here. Flagged
   rather than silently overridden; still unresolved.
2. **Does `review_comment` stay a negative result**, or get rescued if the
   judge is ever built?
3. **Corpus breadth.** `scrapy/scrapy` yielded 1 case. Worth adding repos
   with more revert culture if `fixup` needs to grow.
