"""Reviewer backed by the Anthropic API."""

from __future__ import annotations

import logging

from reviewbench.cache import ResponseCache
from reviewbench.model_client import DEFAULT_MODEL, AnthropicClient
from reviewbench.models import Case
from reviewbench.reviewers.base import (
    FINDINGS_SCHEMA,
    REVIEW_SYSTEM_PROMPT,
    ReviewResult,
    build_review_prompt,
    parse_findings,
)

logger = logging.getLogger(__name__)


class AnthropicReviewer:
    """Scores a diff hunk by asking a Claude model for structured findings.

    Reuses `AnthropicClient` rather than calling the SDK again, so the
    prompt-hash cache built in Phase 1 covers scoring runs too — re-running
    a pass after a metrics bug costs nothing.
    """

    name = "anthropic"

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
        cache: ResponseCache | None = None,
        effort: str = "medium",
    ) -> None:
        self.model = model
        self._client = AnthropicClient(
            model=model, api_key=api_key, cache=cache, effort=effort
        )

    def review(self, case: Case) -> ReviewResult:
        try:
            payload = self._client.complete_json(
                system=REVIEW_SYSTEM_PROMPT,
                prompt=build_review_prompt(case),
                schema=FINDINGS_SCHEMA,
            )
        except Exception as e:
            # One failed case must not end a 300-case scoring run. The error
            # is recorded on the prediction so it is visible in scoring
            # rather than silently counting as "found nothing".
            logger.warning("Review failed for case %s: %s", case.id, e)
            return ReviewResult(error=str(e))

        return ReviewResult(
            findings=parse_findings(payload, file_path=case.file_path),
            input_tokens=self._client.last_usage["input_tokens"],
            output_tokens=self._client.last_usage["output_tokens"],
            cached=self._client.last_was_cached,
        )
