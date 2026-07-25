"""Exact per-call USD cost from real Claude API token usage — no approximation.

Used to record actual spend per Ask question (see `agent.answer_question`) so
per-user dollar caps and the admin/user usage reports are built on real
billing figures, not a token-count proxy. This is separate from
`agent.COST_ESTIMATES`, which is a rough *pre-call* estimate shown in the UI
before a question is asked (we don't know real usage until the call returns).

Pricing checked 2026-07-02 against Anthropic's published rates. Sonnet 5 is in
an introductory pricing window through 2026-08-31 ($2/$10 per MTok instead of
the standard $3/$15) — after that date this file needs a manual edit to the
Sonnet 5 row. There's no live pricing API to reconcile against (unlike
`linklib.models`, which reconciles against the live Models API), so a stale
row here silently under- or over-charges until someone updates it.
"""
from __future__ import annotations

# USD per million tokens. cache_write is the 5-minute-TTL rate (1.25x input);
# nothing in this codebase sets a 1-hour cache TTL, so the 2x rate isn't
# modeled. cache_read is ~0.1x input, per Anthropic's published cache
# economics. Add a row here whenever a new model becomes selectable in the Ask
# picker (linklib/models.py) — an unlisted model silently falls back to
# Sonnet 4.6 rates rather than recording $0.
MODEL_PRICING: dict[str, dict[str, float]] = {
    "claude-haiku-4-5-20251001": {"input": 1.00, "output": 5.00,  "cache_write": 1.25, "cache_read": 0.10},
    "claude-sonnet-4-6":         {"input": 3.00, "output": 15.00, "cache_write": 3.75, "cache_read": 0.30},
    "claude-sonnet-5":           {"input": 2.00, "output": 10.00, "cache_write": 2.50, "cache_read": 0.20},  # intro pricing through 2026-08-31; becomes $3/$15 after
    "claude-opus-4-8":           {"input": 5.00, "output": 25.00, "cache_write": 6.25, "cache_read": 0.50},
    "claude-opus-5":             {"input": 5.00, "output": 25.00, "cache_write": 6.25, "cache_read": 0.50},
}

_FALLBACK = MODEL_PRICING["claude-sonnet-4-6"]


def compute_cost(model: str, input_tokens: int = 0, output_tokens: int = 0,
                 cache_creation_tokens: int = 0, cache_read_tokens: int = 0) -> float:
    """Exact USD cost for one API call from its real token usage.

    Falls back to Sonnet 4.6 rates for a model not in `MODEL_PRICING` so an
    unrecognized model ID never silently records a $0 cost.
    """
    rates = MODEL_PRICING.get(model, _FALLBACK)
    return (
        input_tokens * rates["input"]
        + output_tokens * rates["output"]
        + cache_creation_tokens * rates["cache_write"]
        + cache_read_tokens * rates["cache_read"]
    ) / 1_000_000


# USD per million input tokens. Embeddings have no output/cache tokens.
# Checked 2026-07-02 against OpenAI's published rates alongside the Claude
# table above — same "no live pricing API" caveat applies (see module
# docstring): a stale row here silently mis-records overhead spend.
EMBEDDING_PRICING: dict[str, float] = {
    "text-embedding-3-small": 0.02,
}

_EMBEDDING_FALLBACK = EMBEDDING_PRICING["text-embedding-3-small"]


def compute_embedding_cost(model: str, input_tokens: int = 0) -> float:
    """Exact USD cost for one embeddings API call from its real token usage.

    Falls back to text-embedding-3-small's rate for an unrecognized model ID,
    matching compute_cost's never-silently-$0 behavior above.
    """
    rate = EMBEDDING_PRICING.get(model, _EMBEDDING_FALLBACK)
    return input_tokens * rate / 1_000_000
