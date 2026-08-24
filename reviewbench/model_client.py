"""The seam between reviewbench and whatever model is behind it.

`StructuredModelClient` is a protocol, not a base class: the injector (and,
in Phase 2, the reviewer adapters) depend on this interface rather than on
the Anthropic SDK directly. That is what lets tests run with a fake client
and no network, and what will let a second provider drop in without the
callers changing.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Protocol, runtime_checkable

from reviewbench.cache import ResponseCache, cache_key

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-4-8"


@runtime_checkable
class StructuredModelClient(Protocol):
    """A model that returns JSON conforming to a caller-supplied schema."""

    def complete_json(self, *, system: str, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        ...


class AnthropicClient:
    """`StructuredModelClient` backed by the Anthropic API.

    Uses structured outputs so the response is guaranteed to match the
    schema — the alternative (asking for JSON in the prompt and hoping) is
    exactly the kind of thing that produces silently malformed cases.
    """

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
        cache: ResponseCache | None = None,
        max_tokens: int = 16000,
        effort: str = "high",
    ) -> None:
        try:
            import anthropic
        except ImportError as e:  # pragma: no cover - exercised only without the extra installed
            raise ImportError(
                "The Anthropic SDK is required for synthetic injection. "
                'Install it with: pip install -e ".[synthetic]"'
            ) from e

        self._client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        self.model = model
        self.max_tokens = max_tokens
        self.effort = effort
        self.cache = cache if cache is not None else ResponseCache()
        # Usage from the most recent call, so callers can price a request
        # without the protocol having to carry usage through every return
        # type. A cache hit reports zeros, which is accurate: no tokens were
        # spent.
        self.last_usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}
        self.last_was_cached = False

    def complete_json(self, *, system: str, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        key = cache_key(
            model=self.model,
            system=system,
            prompt=prompt,
            schema=schema,
            effort=self.effort,
            max_tokens=self.max_tokens,
        )
        cached = self.cache.get(key)
        if cached is not None:
            logger.debug("Cache hit for %s", key[:12])
            self.last_usage = {"input_tokens": 0, "output_tokens": 0}
            self.last_was_cached = True
            return cached
        self.last_was_cached = False

        response = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            thinking={"type": "adaptive"},
            output_config={
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": schema},
            },
            messages=[{"role": "user", "content": prompt}],
        )

        if response.stop_reason == "refusal":
            raise RuntimeError(
                f"Model declined the injection request (category="
                f"{getattr(response.stop_details, 'category', None)})"
            )

        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            raise RuntimeError(f"No text block in response (stop_reason={response.stop_reason})")

        usage = getattr(response, "usage", None)
        self.last_usage = {
            "input_tokens": getattr(usage, "input_tokens", 0) or 0,
            "output_tokens": getattr(usage, "output_tokens", 0) or 0,
        }

        parsed = json.loads(text)
        self.cache.set(key, parsed)
        return parsed
