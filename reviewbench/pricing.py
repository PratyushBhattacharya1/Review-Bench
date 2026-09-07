"""Token pricing, used to report cost per case.

Deliberately explicit and deliberately incomplete. Prices change, and a
wrong price silently produces a wrong cost column that looks authoritative.
An unknown model yields `None` rather than a guess, and the runner records
that as "cost unavailable" instead of zero — zero would read as free.

Anthropic figures are USD per million tokens as of 2026-06-24. Verify
against current pricing before publishing any cost comparison.
"""

from __future__ import annotations

# model id -> (input $/MTok, output $/MTok)
PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-6": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    """Cost of one call, or None if this model has no price on file."""
    prices = PRICES.get(model)
    if prices is None:
        return None
    in_rate, out_rate = prices
    return (input_tokens / 1_000_000) * in_rate + (output_tokens / 1_000_000) * out_rate
