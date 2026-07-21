"""Source 3: synthetic bug injection — not yet implemented.

Planned approach (see docs/DESIGN.md "Non-goals for Phase 1" for why this
is deliberately last): take clean, merged, never-reverted PR diffs and use
an LLM to inject a plausible bug into them, in the style of DebugBench
(4,253 instances built by injecting bugs into LeetCode solutions with
GPT-4, then manually validated). The injected version becomes a `defect`
case; the original clean diff becomes a paired `no_defect` case. Every
synthetic case must carry the injection prompt and the model that
generated it in `context`, and a sample must be manually validated before
use, exactly as DebugBench did — synthetic bugs are the least realistic
ground-truth source and the easiest to fake confidence with.

This is stubbed rather than half-built because it needs the runner/adapter
work from Phase 2 (a model-calling interface) to do properly, and because
the two organic sources should be validated against a real repo first.
"""

from __future__ import annotations

from typing import Iterator

from reviewbench.models import Case


def inject_synthetic_bugs(*_args, **_kwargs) -> Iterator[Case]:
    raise NotImplementedError(
        "Synthetic bug injection is planned but not yet implemented. "
        "Use provenance='fixup' or 'review_comment' sources for now — "
        "see docs/DESIGN.md."
    )
