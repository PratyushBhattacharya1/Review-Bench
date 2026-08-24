"""Reviewer backed by the OpenAI API.

A second provider is not decoration. With one provider you cannot separate
"this model is weak at review" from "this prompt suits this model", and the
SWR-Bench finding that review ability does not track general coding ability
is exactly the kind of claim that needs more than one vendor behind it.

Shares the prompt and schema in `reviewers/base.py` with the Anthropic
adapter, so a scoring difference is a model difference rather than a
prompt difference.
"""

from __future__ import annotations

import json
import logging

from reviewbench.cache import ResponseCache, cache_key
from reviewbench.models import Case
from reviewbench.reviewers.base import (
    FINDINGS_SCHEMA,
    REVIEW_SYSTEM_PROMPT,
    ReviewResult,
    build_review_prompt,
    parse_findings,
)

logger = logging.getLogger(__name__)

DEFAULT_OPENAI_MODEL = "gpt-4o"


class OpenAIReviewer:
    name = "openai"

    def __init__(
        self,
        *,
        model: str = DEFAULT_OPENAI_MODEL,
        api_key: str | None = None,
        cache: ResponseCache | None = None,
    ) -> None:
        try:
            import openai
        except ImportError as e:  # pragma: no cover - only without the extra
            raise ImportError(
                "The OpenAI SDK is required for this reviewer. "
                'Install it with: pip install -e ".[openai]"'
            ) from e

        self._client = openai.OpenAI(api_key=api_key) if api_key else openai.OpenAI()
        self.model = model
        self.cache = cache if cache is not None else ResponseCache()

    def review(self, case: Case) -> ReviewResult:
        prompt = build_review_prompt(case)
        key = cache_key(provider="openai", model=self.model, prompt=prompt, schema=FINDINGS_SCHEMA)

        cached = self.cache.get(key)
        if cached is not None:
            return ReviewResult(
                findings=parse_findings(cached, file_path=case.file_path), cached=True
            )

        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": REVIEW_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "findings",
                        "schema": FINDINGS_SCHEMA,
                        "strict": True,
                    },
                },
            )
            payload = json.loads(response.choices[0].message.content or "{}")
        except Exception as e:
            logger.warning("Review failed for case %s: %s", case.id, e)
            return ReviewResult(error=str(e))

        self.cache.set(key, payload)
        usage = getattr(response, "usage", None)
        return ReviewResult(
            findings=parse_findings(payload, file_path=case.file_path),
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        )
