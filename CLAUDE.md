# reviewbench

Mines a repo's own merged PR history into a bespoke code-review eval set,
then scores any AI code reviewer against it. Python-only by design.

**Where truth lives:**
- [`docs/STATUS.md`](docs/STATUS.md) — current state. Read this first.
- [`docs/DESIGN.md`](docs/DESIGN.md) — append-only audit trail in discovery
  order. Every measurement is here, but **early status claims are
  superseded by later ones**; don't quote it without checking STATUS.md.

## Working rules

These are the project's method. They are not inferable from the code, and
abandoning them quietly is how this project fails.

1. **Verify before trusting a run.** Hand-classify a random sample and
   record the error rate. Every single run so far looked fine by case count
   and failed on inspection — a 391-case run carried 57–80% label error; a
   289-case fixup run had one PR contributing 23% of it. Case count is not
   evidence.
2. **Never pool provenances in a score.** `fixup`, `synthetic` and
   `review_comment` have genuinely different label quality. A blended
   number hides the only interesting gap. `scoring.py` enforces this.
3. **Record findings in `docs/DESIGN.md` as numbered defects rather than
   fixing silently.** The chronology of finding-then-fixing *is* the
   artifact — it is what makes the project defensible in an interview.
4. **A case that cannot be answered correctly must not enter the dataset.**
   This is why non-code paths, oversized reverts, and machine-authored
   comments are filtered at mining time rather than at scoring time.

## Environment

- Windows + Git Bash; venv at `.venv` (`source .venv/Scripts/activate`).
- `GITHUB_TOKEN` — **set** (user env var, ~5000 req/hr authenticated).
- `ANTHROPIC_API_KEY` — **not set**. Blocks synthetic injection and the
  LLM judge. Nothing else.
- `OPENAI_API_KEY` — not set. Blocks the OpenAI reviewer only.

## Commands

```bash
pytest                                    # 132 tests, no network/keys needed

python -m reviewbench.cli build-dataset --repo psf/requests,django/django \
  --sources fixup --limit 100 --out data/fixup_multi.jsonl

python -m reviewbench.cli run   --dataset data/x.jsonl --reviewer anthropic --out data/preds.jsonl
python -m reviewbench.cli score --dataset data/x.jsonl --predictions data/preds.jsonl
```

## Gotchas that have already cost time

- **`fixup` emits positives only**, by construction — a reverted PR gives
  buggy code, never a clean counterpart. So its precision is 100% with
  nothing to be wrong about, and its noise rate is uncomputable. This is
  why `synthetic` (paired +/−) is load-bearing, not optional.
  `ProvenanceScore.caveats` flags such rows in the report.
- **Don't write regexes through bash heredocs.** Doing so silently put a
  literal `\x08` byte into `fixup_miner.py`; only a test caught it. Use the
  Write tool for anything containing backslashes.
- **Prefix `PYTHONIOENCODING=utf-8`** when printing mined comment text, or
  the Windows console dies on emoji in review comments.
- **`data/` is gitignored.** Corpora are local-only; regenerate with
  `build-dataset`. A fresh clone has none of them.
- **Tests never need network, a token, or an API key.** `StructuredModelClient`
  and `Reviewer` are Protocols precisely so fakes can stand in. If a change
  makes tests need credentials, the seam is wrong — fix the seam.
