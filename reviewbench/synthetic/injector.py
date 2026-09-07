"""Source 3: synthetic bug injection.

Takes clean merged-PR diffs and asks a model to introduce a plausible
defect, in the style of DebugBench (4,253 instances built by injecting bugs
into LeetCode solutions with GPT-4, then manually validated). The injected
diff becomes a `defect` case; the original clean diff becomes a paired
`no_defect` case, so a reviewer's false-positive rate is measurable on the
exact same code minus the bug.

This is the least realistic of the three ground-truth sources and the
easiest to generate a lot of — which is precisely why results are reported
broken out by provenance rather than pooled (see docs/DESIGN.md). Every
synthetic case records the model and prompt that produced it in `context`,
so a human validating a sample can see what generated the label.

Nothing here validates that the injected bug is *real*. That is a manual
step, by design: DebugBench used GPT-4 to inject and humans to validate,
and skipping the second half is how you end up with a benchmark that
measures nothing.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Iterator

from reviewbench.model_client import StructuredModelClient
from reviewbench.models import Case, make_case_id

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are helping build an evaluation set for AI code reviewers.

Given a clean diff hunk from a merged pull request, introduce exactly one \
realistic bug into it. The bug should be the kind that a real developer \
would plausibly write and that a careless reviewer would plausibly miss — \
an off-by-one, an inverted condition, a missing null/empty check, a \
resource left unclosed, a wrong variable in a similar-looking pair, a \
mishandled error path.

Do not introduce bugs that are obviously wrong on sight (syntax errors, \
undefined names, blatantly nonsensical logic). The value of this dataset \
depends on the bugs being subtle enough to be worth catching.

Return the modified hunk in the same unified-diff format as the input, \
changing as little as possible beyond the bug itself."""

INJECTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "injected_hunk": {
            "type": "string",
            "description": "The diff hunk with exactly one bug introduced, same unified-diff format as the input.",
        },
        "defect_category": {
            "type": "string",
            "enum": [
                "off_by_one",
                "inverted_condition",
                "missing_null_check",
                "resource_leak",
                "wrong_variable",
                "error_handling",
                "other",
            ],
            "description": "The class of bug introduced.",
        },
        "explanation": {
            "type": "string",
            "description": "One or two sentences on what the bug is and how it manifests.",
        },
        "confidence_subtle": {
            "type": "boolean",
            "description": "True if the bug is subtle enough that a competent reviewer might miss it; false if it is obvious on sight.",
        },
    },
    "required": ["injected_hunk", "defect_category", "explanation", "confidence_subtle"],
    "additionalProperties": False,
}


def _build_prompt(case: Case) -> str:
    return (
        f"File: {case.file_path}\n"
        f"Repository: {case.repo}\n\n"
        f"Clean diff hunk:\n```diff\n{case.diff_hunk}\n```"
    )


def inject_synthetic_bugs(
    client: StructuredModelClient,
    clean_cases: Iterable[Case],
    *,
    limit: int | None = None,
    keep_obvious: bool = False,
    emit_paired_negatives: bool = True,
) -> Iterator[Case]:
    """Yield synthetic cases derived from `clean_cases`.

    For each clean case the model successfully injects a bug into, yields a
    `defect` case and (unless disabled) the paired `no_defect` original.

    `keep_obvious=False` drops injections the model itself flags as
    obvious-on-sight. Those inflate every reviewer's score without
    distinguishing between them, which is the failure mode docs/DESIGN.md
    is written against.
    """
    injected = 0
    for case in clean_cases:
        if limit is not None and injected >= limit:
            break
        if not case.diff_hunk.strip():
            continue

        prompt = _build_prompt(case)
        try:
            result = client.complete_json(
                system=SYSTEM_PROMPT, prompt=prompt, schema=INJECTION_SCHEMA
            )
        except Exception as e:
            # One bad generation shouldn't abort a 300-case run.
            logger.warning("Injection failed for %s (PR #%s): %s", case.file_path, case.pr_number, e)
            continue

        if not result.get("injected_hunk", "").strip():
            logger.warning("Model returned an empty hunk for %s; skipping", case.file_path)
            continue

        if result["injected_hunk"].strip() == case.diff_hunk.strip():
            logger.warning("Model returned the hunk unchanged for %s; skipping", case.file_path)
            continue

        if not keep_obvious and not result.get("confidence_subtle", False):
            logger.info(
                "Dropping obvious injected bug in %s (category=%s)",
                case.file_path,
                result.get("defect_category"),
            )
            continue

        injected += 1

        yield Case(
            id=make_case_id("synthetic", case.repo, str(case.pr_number), case.file_path),
            repo=case.repo,
            pr_number=case.pr_number,
            provenance="synthetic",
            label="defect",
            file_path=case.file_path,
            diff_hunk=result["injected_hunk"],
            base_commit_sha=case.base_commit_sha,
            head_commit_sha=case.head_commit_sha,
            defect_category=result.get("defect_category"),
            context=(
                f"Synthetically injected bug. Model explanation: {result.get('explanation', '')} "
                f"[UNVALIDATED — see docs/DESIGN.md verification plan]"
            ),
            human_comment=None,
            source_urls=list(case.source_urls),
            created_at=case.created_at,
        )

        if emit_paired_negatives:
            yield Case(
                id=make_case_id("synthetic_clean", case.repo, str(case.pr_number), case.file_path),
                repo=case.repo,
                pr_number=case.pr_number,
                provenance="synthetic",
                label="no_defect",
                file_path=case.file_path,
                diff_hunk=case.diff_hunk,
                base_commit_sha=case.base_commit_sha,
                head_commit_sha=case.head_commit_sha,
                defect_category=None,
                context="Paired clean original for the synthetic injection on this hunk.",
                human_comment=None,
                source_urls=list(case.source_urls),
                created_at=case.created_at,
            )
