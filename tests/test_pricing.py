"""Coverage for linklib/pricing.py — real per-call USD cost from token usage.

Added alongside the Sonnet 5 pricing correction (issue #98): the row's
numeric values were already correct at $2/$10/$2.50/$0.20, but nothing
pinned them down, so a future accidental edit (e.g. "helpfully" bumping
Sonnet 5 to the once-planned $3/$15) would go uncaught.
"""
from __future__ import annotations

from linklib.agent import EFFORT_SETTINGS
from linklib.models import _REGISTRY
from linklib.pricing import (
    MODEL_PRICING,
    compute_cost,
    exa_pricing_review_is_stale,
    pricing_review_is_stale,
)


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


def test_model_pricing_tracks_both_cache_write_ttl_rates():
    """Each row carries the 5-minute cache_write rate and the 1-hour
    cache_write_1h rate (2x input; the 5-minute rate is 1.25x)."""
    for model, rates in MODEL_PRICING.items():
        assert set(rates.keys()) == {"input", "output", "cache_write", "cache_write_1h", "cache_read"}
        assert rates["cache_write"] == round(rates["input"] * 1.25, 4), model
        assert rates["cache_write_1h"] == round(rates["input"] * 2.0, 4), model


def test_compute_cost_prices_1h_cache_writes_at_the_1h_rate():
    """A 1-hour-TTL cache write bills at 2x input, not the 5-minute 1.25x. The
    old compute_cost had no way to express it, so a caller that requested the
    1-hour TTL would have been under-counted against the per-user dollar caps."""
    five = compute_cost("claude-sonnet-5", cache_creation_tokens=1_000_000)
    one_h = compute_cost("claude-sonnet-5", cache_creation_1h_tokens=1_000_000)
    assert five == 2.50
    assert one_h == 4.00
    both = compute_cost("claude-opus-5", cache_creation_tokens=200_000,
                        cache_creation_1h_tokens=100_000)
    assert abs(both - (0.2 * 6.25 + 0.1 * 10.00)) < 1e-12


def test_every_registry_model_has_a_pricing_row():
    """issue #98, Piece 1 — every model string in linklib.models's curated
    registry (the source every picker in the app renders from) must have a
    matching MODEL_PRICING entry, permanently, not just as of today. Without
    this, a model added to the registry with pricing forgotten would silently
    fall back to Sonnet 4.6 rates on every real call — this closes that gap
    for good, no human needs to remember to check it again.

    (Verified during development that this actually catches the regression it
    exists to catch: temporarily adding a registry-only id with no
    MODEL_PRICING row made this fail, as expected — not shipped as a
    permanent broken-state test, per the task brief.)"""
    registry_ids = {m["id"] for m in _REGISTRY}
    missing = registry_ids - set(MODEL_PRICING.keys())
    assert not missing, f"registered model(s) with no MODEL_PRICING row: {sorted(missing)}"


def test_every_effort_tier_model_has_a_pricing_row():
    """issue #98, Piece 1 follow-up — linklib.models._REGISTRY isn't the
    only place a model id lives. FP&A Buddy's Quick/Standard/Deep tiers
    (linklib.agent.EFFORT_SETTINGS) map to their own hardcoded model ids,
    completely disconnected from the registry the test above covers — the
    investigation confirmed a model swapped into EFFORT_SETTINGS is not
    required to appear in the registry at all (e.g. claude-opus-4-8, the
    'deep' tier's model, isn't a registry entry). Without this, changing
    which model a tier uses with pricing forgotten would silently record
    real Ask spend at Sonnet 4.6's rates (compute_cost's fallback) instead
    of the tier's actual model — a live-cost accuracy bug, not just a
    cosmetic one, since Ask cost is what per-user dollar caps are built on.

    (Verified this actually catches the regression it exists to catch:
    temporarily pointing a tier at a model with no MODEL_PRICING row made
    this fail, as expected.)"""
    effort_model_ids = {settings["model"] for settings in EFFORT_SETTINGS.values()}
    missing = effort_model_ids - set(MODEL_PRICING.keys())
    assert not missing, f"EFFORT_SETTINGS model(s) with no MODEL_PRICING row: {sorted(missing)}"


def test_every_effort_tier_model_has_a_cost_estimate_row():
    """Same gap as above, for linklib.agent.COST_ESTIMATES — the rough
    pre-call estimate shown on /tools/fpa-buddy before a question is asked.
    COST_ESTIMATES.get(model, {}).get(tier) degrades silently to None (a
    blank cost estimate in the UI) rather than raising, so nothing else
    would ever catch a tier pointed at a model missing from this table."""
    from linklib.agent import COST_ESTIMATES
    for tier, settings in EFFORT_SETTINGS.items():
        model = settings["model"]
        assert model in COST_ESTIMATES, f"EFFORT_SETTINGS['{tier}'] model {model!r} has no COST_ESTIMATES row"
        assert tier in COST_ESTIMATES[model], (
            f"COST_ESTIMATES[{model!r}] has no '{tier}' entry"
        )


def test_pricing_review_is_stale_thresholds():
    from datetime import datetime, timedelta, timezone
    now = datetime(2026, 9, 7, tzinfo=timezone.utc)
    fresh = (now - timedelta(days=10)).isoformat()
    stale = (now - timedelta(days=91)).isoformat()
    boundary = (now - timedelta(days=90)).isoformat()
    assert pricing_review_is_stale(fresh, now=now) is False
    assert pricing_review_is_stale(stale, now=now) is True
    assert pricing_review_is_stale(boundary, now=now) is True
    assert pricing_review_is_stale("", now=now) is True
    assert pricing_review_is_stale("not-a-date", now=now) is True


def test_exa_pricing_review_is_stale_thresholds():
    """Same threshold logic as pricing_review_is_stale above, mirrored for
    EXA_PRICING's own independent 90-day reminder."""
    from datetime import datetime, timedelta, timezone
    now = datetime(2026, 9, 7, tzinfo=timezone.utc)
    fresh = (now - timedelta(days=10)).isoformat()
    stale = (now - timedelta(days=91)).isoformat()
    boundary = (now - timedelta(days=90)).isoformat()
    assert exa_pricing_review_is_stale(fresh, now=now) is False
    assert exa_pricing_review_is_stale(stale, now=now) is True
    assert exa_pricing_review_is_stale(boundary, now=now) is True
    assert exa_pricing_review_is_stale("", now=now) is True
    assert exa_pricing_review_is_stale("not-a-date", now=now) is True


def test_opus_5_5_has_registry_and_pricing_rows_with_its_own_cache_read_rate():
    """Opus 5.5 (claude-opus-5-5) is in the curated registry and priced at
    $4/$20, 5-minute cache write $5, and a cache read of $0.20: 0.05x input,
    not the 0.1x ($0.40) every other row uses. Anthropic's table says 0.20
    for this model; a "tidy-up" to 0.40 would double-count cache reads."""
    assert "claude-opus-5-5" in {m["id"] for m in _REGISTRY}
    rates = MODEL_PRICING["claude-opus-5-5"]
    assert rates["input"] == 4.00
    assert rates["output"] == 20.00
    assert rates["cache_write"] == 5.00
    assert rates["cache_read"] == 0.20
    # Real, hand-checked call: 1M each of input/output/cache-read/5m-write.
    assert compute_cost("claude-opus-5-5", 1_000_000, 1_000_000, 1_000_000, 1_000_000) == 4.00 + 20.00 + 5.00 + 0.20
