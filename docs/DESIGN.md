# Design doc: ground truth and known failure modes

This is written before the dataset builder, per the project's own rule:
if the value proposition and the failure modes can't be stated up front,
the design isn't ready.

## Prior art (read before writing code)

- **SWR-Bench** (real-world code review comment generation). Its authors
  found that model rankings on SWR-Bench do not track rankings on
  SWE-bench or LiveCodeBench — optimizing for other SE tasks does not
  transfer to, and may erode, review proficiency. Implication for this
  project: don't assume a model's coding benchmark scores predict its
  review quality. Score review ability directly, per repo.
- **CodeReviewer dataset**. Operates on single diff hunks, uses human
  review comments as approximate defect labels, and supports
  defect-classification, comment-generation, and code-improvement tasks,
  scored with accuracy/F1/BLEU/exact-match. reviewbench's review-comment
  miner (source 2) is directly in this tradition — same source of signal,
  same approximation, same limitation (a hunk with no comment is not
  proof the hunk is defect-free; reviewers miss things).
- **Qodo's PR-Agent benchmark** (60.1% F1, 580 real-world issues). A
  vendor benchmarking its own tool on a fixed, undisclosed-to-us set.
  Useful data point, not a substitute for scoring on your own repo's
  code and your own team's review standards.
- **SWE-Bench+**. Found ~48% of "resolved" SWE-bench issues were
  incorrectly marked resolved because test suites were too weak to
  verify patch correctness; filtering those plausible-but-wrong patches
  dropped agent resolution rates from 42.1% to 21.8% on SWE-bench Lite.
  The general lesson: a benchmark's ground truth is only as strong as
  the weakest verification step in its pipeline, and weak verification
  produces a specific, predictable failure — silent inflation of every
  score computed on top of it.

## The SWE-Bench+ failure mode, translated to code review

SWE-Bench+'s failure was "the patch looks right but the test suite is too
weak to tell." reviewbench's equivalent failure is **"the reviewer's
comment looks like a hit but wasn't actually about the injected/real
defect."** A reviewer that emits "this could have a null pointer issue"
on every single diff will score well on naive recall and be worthless.

Concretely, this shows up in two places:

1. **Detection vs. localization.** A reviewer that flags *some* line in a
   PR as risky, when the real defect is elsewhere in the same PR, must
   not get credit for detection. Every positive case in the dataset
   carries the exact file path and diff hunk of the defect, not just "PR
   #N had a bug somewhere," so the scorer (Phase 3) can require
   line/hunk-level overlap, not just PR-level overlap.
2. **Noise rate as a first-class metric, not an afterthought.** Every
   dataset run should also produce negative (no-defect) cases —
   uncommented hunks in the review-comment miner, clean PRs unaffected by
   a revert in the fixup miner — so a reviewer's false-positive rate on
   genuinely clean code is measurable. A reviewer that comments on
   everything will look great on recall alone; noise rate is what
   catches it. This is why source 2 explicitly samples negatives, and
   why source 1's "clean" PRs (merged, never reverted, no follow-up fix)
   are a candidate negative pool worth mining later, not just an
   afterthought.

## Ground truth sources, and how much to trust each

| Source | Signal strength | Known weakness |
|---|---|---|
| Fixup/revert mining | Highest — a human explicitly undid this PR | Reverts happen for non-bug reasons too (scope cut, merge conflict cleanup, "reverting to unblock CI"); title/body pattern-matching will catch some false positives. Every case retains the revert PR's URL so a human can spot-check the reason. |
| Human review comments | Medium — approximate, per CodeReviewer's own framing | Comments that aren't about defects (style nits, questions, praise) get filtered by a keyword/length heuristic plus thread-root filtering (below); some non-defect comments still get through; absence of a comment is not proof of absence of a defect (reviewers miss things — this is exactly the gap the whole project exists to measure, so it cannot be fully removed) |
| Synthetic injection | Lowest realism, cleanest label | Injected bugs are, on average, easier to spot than organic ones (LLM-injected bugs tend toward "obviously wrong" rather than "subtly wrong"). Two partial mitigations are implemented: the injection prompt asks specifically for bugs a careless reviewer would plausibly miss, and injections the model itself flags as obvious-on-sight are dropped. Neither mitigation is verification — the model grading its own subtlety is exactly the kind of self-report this project exists to distrust. The clean source hunks also inherit source 2's weakness: "no substantive review comment" is not proof the original was defect-free, so an injected case could in principle contain two bugs, one of them unlabeled. |

Because these three sources have genuinely different signal strength,
**results are always reported broken out by provenance, never pooled into
a single blended score.** A reviewer that only looks good on synthetic
cases should be visibly worse on fixup cases, and that gap is itself the
finding.

## Minimum viable sample size

Below roughly 200-300 cases for a given repo, confidence intervals on any
F1/precision/recall number are not meaningful, and any such number should
be reported as directional, not as ground truth. The CLI does not enforce
this as a hard floor (a repo with genuinely few reverts and few review
comments still has research value at low N), but it warns loudly with the
current case count in the run summary, and the README repeats the same
warning.

## Verification plan

Before any run's output is used to score a reviewer, a random sample of
~50 generated cases (across sources, proportionally) is hand-verified —
is the label actually correct, is the diff hunk the actual defect
location, is the human comment (if present) actually about the flagged
defect. The resulting error rate is reported alongside every score. This
number is the project's credibility, not a footnote.

### What verification caught (first live run, psf/requests)

The first real run existed to produce exactly this number, and it did its
job. A hand-classification of the review-comment miner's positive labels
found roughly **55–60% of them were not defects** — they were replies
inside review threads ("good call", "thanks, pushed", "just saw your other
comment"). GitHub's PR-comments endpoint returns every comment in a thread,
and these replies cleared the length + non-trivial-ack filters.

Root cause and fix: a reply carries `in_reply_to_id`; a thread-initiating
comment does not. Filtering to thread roots (`is_thread_root`) removed the
reply chatter with no loss of true positives on the overlapping PRs — on
one two-PR overlap it dropped 6 positives to 3, and all 3 removed were
false positives.

Residual, un-fixed noise: a thread *root* can still be a question or an
informational observation rather than a defect flag ("Tested on my repo,
works both from the tab…"). That is a smaller and harder problem than the
systematic reply noise, and it is the kind of thing the Phase 3
LLM-as-judge semantic match is meant to catch, not a keyword heuristic.

### Second measurement (full authenticated run, 300 PRs)

The full run produced **391 cases (111 defect / 280 no_defect)** from
`psf/requests` — above the 200-case floor. A hand-classification of a
random 40-positive sample (seed 42) gives:

| Reading | Precision | Label error rate |
|---|---|---|
| Strict (only unambiguous defect flags) | 8/40 = **20%** | 80% |
| Generous (counting borderline suggestions/questions) | 17/40 = **43%** | 57% |

**Thread-root filtering did not lower the error rate — it changed the
composition of the error.** The reply chatter is gone; what remains is the
underlying comment population of a mature, well-reviewed repo, which is
mostly style nits ("can we add an empty line before `__init__`", "we can
unindent this to align with `with`"), scoping questions ("`object` or
`Any`?", "do we need `u'...'`?"), and informational discussion. Those are
real review comments — they are just not defect reports.

The honest conclusion: **`review_comment` alone does not produce a dataset
usable for scoring.** Its positives need semantic filtering, not lexical
filtering. This is a stronger statement than the CodeReviewer framing
("comments are approximately defects") and it is worth stating plainly,
because it is a result, not a setback.

### Bot and AI-reviewer contamination (the circularity problem)

The same sample surfaced a failure mode specific to this project. Of 113
root comments across the 47 PRs carrying defect labels, **5 came from
`github-advanced-security[bot]`** (CodeQL security findings), and three
more on PR #7431 are formatted as AI-code-review output:

```
⚠️ **HIGH** — *test_coverage* **Confidence:** 80%
New logic for handling MutableMapping in Request.headers is not tested.
```

Those three were posted by `sdm0p`, whose GitHub `user.type` is **`User`,
not `Bot`** — so the obvious defence (drop anything where
`user.type == "Bot"`) does not catch them.

This matters more than the raw percentage suggests. **Benchmarking an AI
code reviewer against ground truth that contains another AI code
reviewer's output is circular** — the score stops measuring "did it find
the bug" and starts measuring "did it agree with the other tool." This is
the SWE-Bench+ failure mode wearing a different costume, exactly as the
project scoping predicted, and it is not something any of the prior art
(SWR-Bench, CodeReviewer, Qodo's benchmark) appears to control for.

Blocking gap: **the `Case` schema does not record the comment author at
all**, so today the contamination cannot even be filtered or reported. The
schema needs `comment_author` and `comment_author_type`, and the miner
needs a heuristic for AI-generated comments posted from user accounts
(structured severity/confidence headers are a strong signal).

### Status

On the strength of these two measurements, **no dataset produced by this
tool should be used to score a reviewer yet.** The pipeline is sound; the
labels are not. Fixes are tracked in the next section.

## Known defects (measured, not yet fixed)

1. ~~**Fixup miner recognizes ~1 in 8 real reverts.**~~ **Fixed.** See
   "Fixup miner, after the fix" below.
2. **Positive labels are ~53-80% non-defects.** Unchanged by path
   filtering — see "Third measurement" below. Needs semantic filtering.
3. **No author recorded, so bot/AI comments cannot be excluded.** See
   above. Needs a schema field plus an AI-output heuristic.
4. ~~**Fixup cases are labelled per-file, including incidental files.**~~
   **Fixed** by the shared path filter — see below.

## Fixup miner, after the fix

Two changes: `extract_revert_targets` now resolves PR-number references
(`Reverts owner/repo#2442`, "reverts the changes from #6667") alongside the
SHA footer, and discovery moved from a recency scan to a GitHub search
query. The second change matters as much as the first — every one of this
repo's reverts predates its 300 most recent PRs, so no amount of pattern
matching would have found them by walking recent history.

Result on `psf/requests`: **0 → 10 cases across 6 origin PRs**, and the run
went from 6m23s to 12s (one search query beats paging thousands of PRs).
Both reference styles resolve: PR #3738 via the SHA footer, the rest via
PR-number references.

Precision guard worth keeping: PR-number references are only trusted when
the *title* also signals a revert, and only when the number is adjacent to
a revert verb. Real bodies cite issues they also address — "This addresses
#3481 and will revert #3362 back to its prior state" must yield #3362 and
not #3481, or the miner labels an innocent PR as defective.

### New defect found while verifying (defect 4 above)

Hand-inspecting all 10 cases shows the miner marks **every file in the
reverted PR** as a defect case, including files no reviewer could
meaningfully have flagged:

| Origin PR | File | Plausible defect location? |
|---|---|---|
| #6667 | `src/requests/adapters.py` | yes — the SSLContext concurrency bug |
| #3362 | `requests/utils.py`, `tests/test_requests.py` | yes |
| #2442 | `requests/cacert.pem` (78 KB of PEM) | **no** — a data blob |
| #2513 | `AUTHORS.rst` | **no** — a name addition |
| #3713 | `requests/models.py`, tests | yes |
| #3700 | `README.rst`, `docs/index.rst` | **no** — and the revert was a
  preference call about a docs snippet, not a bug at all |

That is **4 of 10 cases on files that cannot carry the defect**, and one
origin PR (#3700) that was reverted for editorial preference rather than
any defect. Roughly 40% questionable — notably better than
`review_comment`'s 57-80%, which supports the design doc's ordering of
these sources by signal strength, but not good enough to use unfiltered.

This is the localization problem from the top of this document showing up
in the dataset itself rather than in a reviewer's output: a case must name
the file where the defect actually lives, or scoring against it rewards
noise. Fixes needed: exclude non-code paths (docs, data blobs, AUTHORS,
changelogs) from fixup cases, and treat "reverted" as necessary but not
sufficient — the revert's stated reason still has to be a defect.

## Non-goals for Phase 1

- Multi-language support. Python only, to keep the miners' heuristics
  (e.g., "is this comment substantive") from needing per-language tuning
  before the core pipeline is proven.
- A hosted service or web UI. CLI + JSONL only.
- Automated validation of synthetic cases. The injector deliberately does
  not try to verify that an injected bug is real; DebugBench used a model
  to inject and humans to validate, and a pipeline that self-validates is
  just the model grading its own homework. Synthetic cases are emitted
  tagged `UNVALIDATED` and the CLI says so on every run that produces them.

## Cost control

Synthetic injection is the only source that costs money per case, and the
arithmetic gets bad quickly: a few hundred cases times several model
configs times several iterations of the pipeline. Three decisions follow:

1. **The response cache exists before the first real run**, not after the
   first surprising bill. It is keyed on a hash of the full request (model,
   system prompt, user prompt, schema, effort, max_tokens), so a hit is
   only returned for a byte-identical request, and it persists on disk so
   it survives the crash-fix-rerun loop that dominates early development.
2. **Synthetic is opt-in.** It never runs unless explicitly listed in
   `--sources`, and it errors rather than silently skipping if no model
   client is available — a silent skip would let someone believe they had
   synthetic coverage they had not paid for.
3. **`--synthetic-limit` defaults to 50**, not unlimited. The default
   should be a number someone can afford to run by accident.

## Scope filtering, and the third measurement

Verifying defect 4 turned up something larger than defect 4. Phase 0 scoped
this project to Python, but **59% of the first full run (232 of 391 cases)
sat on non-Python files** — CI YAML, changelogs, issue templates,
`pyproject.toml`, a stray `.coverage.<host>.<pid>` artifact someone
committed by accident, and 78 KB of PEM certificate data.

Two different problems were hiding in that one number, and it is worth
keeping them separate:

- **Out of scope.** A `no_defect` case on `.github/dependabot.yml` is not
  wrong, it is meaningless — it measures reviewer behaviour on a file type
  the benchmark never claimed to cover.
- **Unanswerable.** `cacert.pem` and `AUTHORS.rst` were labelled `defect`
  because the PR containing them was reverted. There is no bug in a name
  list. Scoring against those cases punishes a reviewer for being right.

`reviewbench/paths.py` (`is_reviewable_code_path`) now gates every miner —
both label sources and the synthetic injection candidates. Filtering at
mining time rather than at scoring time is deliberate: a case that cannot
be answered correctly should never enter the dataset, so the published case
count means what it claims.

### Results

| | Before | After |
|---|---|---|
| Total cases | 391 | 207 |
| Non-Python cases | 232 (59%) | **0** |
| Fixup cases | 10 | 6 |
| Fixup cases on unreviewable files | 4 of 10 (40%) | **0 of 6** |

All four unreviewable fixup cases are gone, and origin PR #3700 — reverted
over an editorial preference about a README snippet, never a defect —
dropped out entirely because both its files were documentation. The
remaining six fixup cases all sit on files that plausibly carry the defect
(`adapters.py`, `utils.py`, `models.py`, `connectionpool.py`, and two test
files). Small N, but clean.

### The part that did not improve

A second hand-classification of 40 `review_comment` positives (seed 42,
post-filter) gives **20% strict / 47.5% generous precision** — statistically
indistinguishable from the 20% / 43% measured before filtering.

This is the honest result and it should not be buried: **path filtering
fixed scope and fixed `fixup`, and did nothing for `review_comment` label
quality.** That follows once stated plainly — the noise there was never
about file type. It is about comment intent, and a `.py` file attracts
nits ("Nit: `HookType` doesn't need to be quotes", "the last sentence still
needs a period"), author self-explanations ("this change is needed
because…"), approvals ("That's good, this appears to be the same way
urllib3 handles it"), and feature requests just as readily as a `.rst` file
does. No path-based or keyword-based rule separates those from a defect
report; only reading the comment does.

Bot contamination also **rose** as a share, from 4/40 to 3/40 — restricting
to `.py` concentrates it, because CodeQL and AI review bots comment on code
and not on changelogs.

### Where that leaves the dataset

`fixup` is now clean enough to use, and too small to use alone (6 cases from
this repo). `review_comment` is large enough and not clean enough. Both
statements have to be fixed before Phase 3 scoring means anything, and
neither is fixed by another heuristic — the next move on defect 2 is the
LLM judge, which is Phase 3 work pulled forward, and on defect 3 a schema
change plus an AI-output detector.
