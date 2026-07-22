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
The verification error rate on positives should be re-measured on a full
authenticated run before the dataset is used for scoring; the number above
is from an unauthenticated ~20-PR scan and is directional, not final.

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
