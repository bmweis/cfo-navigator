"""Coverage for linklib/pricing.py — real per-call USD cost from token usage.

Added alongside the Sonnet 5 pricing correction (issue #98): the row's
numeric values were already correct at $2/$10/$2.50/$0.20, but nothing
pinned them down, so a future accidental edit (e.g. "helpfully" bumping
Sonnet 5 to the once-planned $3/$15) would go uncaught.
"""
from __future__ import annotations

from linklib.pricing import MODEL_PRICING, compute_cost


def test_sonnet_5_pricing_is_the_confirmed_permanent_rate():
    """Per https://www.anthropic.com/news/claude-sonnet-5 — the introductory
    rate is permanent, not a temporary window that reverts to $3/$15."""
    rates = MODEL_PRICING["claude-sonnet-5"]
    assert rates["input"] == 2.00
    assert rates["output"] == 10.00
    assert rates["cache_read"] == 0.20
    assert rates["cache_write"] == 2.50


def test_compute_cost_sonnet_5_matches_hand_calculation():
    # 1,000,000 input tokens + 500,000 output tokens + 200,000 cache-write
    # tokens + 1,000,000 cache-read tokens, at Sonnet 5 rates.
    cost = compute_cost(
        "claude-sonnet-5",
        input_tokens=1_000_000,
        output_tokens=500_000,
        cache_creation_tokens=200_000,
        cache_read_tokens=1_000_000,
    )
    expected = 1.0 * 2.00 + 0.5 * 10.00 + 0.2 * 2.50 + 1.0 * 0.20
    assert cost == expected == 7.70


def test_compute_cost_sonnet_5_realistic_small_call():
    # A more realistic single Ask call: ~3k input, ~600 output tokens.
    cost = compute_cost("claude-sonnet-5", input_tokens=3000, output_tokens=600)
    expected = (3000 * 2.00 + 600 * 10.00) / 1_000_000
    assert abs(cost - expected) < 1e-12
    assert round(cost, 6) == 0.012000


def test_compute_cost_unknown_model_falls_back_to_sonnet_4_6_not_zero():
    cost = compute_cost("some-unrecognized-model", input_tokens=1_000_000)
    assert cost == MODEL_PRICING["claude-sonnet-4-6"]["input"]


def test_model_pricing_only_tracks_a_single_cache_write_rate():
    """Cache pricing tracks one cache_write rate (the 5-minute-TTL rate) and
    cache_read, but has no separate 1-hour-TTL cache_write rate — flagged as
    a known, out-of-scope gap by issue #98's investigation, not a bug."""
    for rates in MODEL_PRICING.values():
        assert set(rates.keys()) == {"input", "output", "cache_write", "cache_read"}
