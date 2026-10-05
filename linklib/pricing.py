"""Exact per-call USD cost from real Claude API token usage — no approximation.

Used to record actual spend per Ask question (see `agent.answer_question`) so
per-user dollar caps and the admin/user usage reports are built on real
billing figures, not a token-count proxy. This is separate from
`agent.COST_ESTIMATES`, which is a rough *pre-call* estimate shown in the UI
before a question is asked (we don't know real usage until the call returns).

Pricing seed (PR 3a: the live source is the model_pricing table; these dicts are the seed and last-resort fallback). Checked 2026-07-02 against Anthropic's published rates, re-verified
2026-09-07 (issue #98). Sonnet 5's introductory rate ($2/$10 per MTok,
instead of the originally-planned standard $3/$15) is now permanent — the
planned September 1 increase to $3/$15 was cancelled by Anthropic, so no
further edit is needed here on that account. See
https://www.anthropic.com/news/claude-sonnet-5 There's no live pricing API to
reconcile against (unlike `linklib.models`, which reconciles against the live
Models API), so a stale row here silently under- or over-charges until
someone updates it — re-verify against Anthropic's published rates
periodically.
"""
from __future__ import annotations

from datetime import datetime, timezone

# How often Brian should manually re-check MODEL_PRICING against Anthropic's
# published rates — there's no pricing API to reconcile against
# automatically (unlike linklib.models, which reconciles the model
# *registry* against the live Models API), so this is a dated-reminder
# threshold for a human attestation, not something a test can verify on its
# own. Confirmed with Brian (issue #98 follow-up, 2026-09). MODEL_PRICING is
# Claude-only — OpenAI's embedding rate is tracked separately, in
# EMBEDDING_PRICING below, which has no freshness-reminder banner of its
# own yet (corrected 2026-09, admin-sprawl follow-up: this comment and the
# /admin/checks banner it backs used to say "(and OpenAI's)," asserting
# coverage this table doesn't actually have).
PRICING_REVIEW_STALE_DAYS = 90


def row_pricing_state(verified_on: str, *, now: datetime | None = None) -> str:
    """'unverified' (no date), 'stale' (90+ days) or 'fresh', for one pricing
    row. Replaces the single global timestamp: each row ages on its own."""
    if not (verified_on or "").strip():
        return "unverified"
    return "stale" if pricing_review_is_stale(verified_on, now=now) else "fresh"


def pricing_review_is_stale(last_verified_iso: str, *, now: datetime | None = None) -> bool:
    """True when `last_verified_iso` (a stored settings value, empty string
    if never recorded) is older than PRICING_REVIEW_STALE_DAYS — or missing
    entirely, which is the same "go check it" signal as genuinely stale."""
    if not last_verified_iso:
        return True
    try:
        then = datetime.fromisoformat(last_verified_iso)
    except ValueError:
        return True
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return (now - then).days >= PRICING_REVIEW_STALE_DAYS


# USD per million tokens. cache_write is the 5-minute-TTL rate (1.25x input);
# cache_write_1h is the 1-hour-TTL rate (2x input). Nothing in this codebase
# requests the 1-hour TTL today (linklib.matchmaker's cache_control sets no
# ttl, so it gets the 5-minute default), but the rate is modeled so a caller
# that does is billed correctly instead of silently at the 5-minute rate; see
# compute_cost's cache_creation_1h_tokens. cache_read is ~0.1x input, per
# Anthropic's published cache economics. Add a row here whenever a new model becomes selectable in the Ask
# picker (linklib/models.py) — an unlisted model silently falls back to
# Sonnet 4.6 rates rather than recording $0.
MODEL_PRICING: dict[str, dict[str, float]] = {
    "claude-haiku-4-5-20251001": {"input": 1.00, "output": 5.00,  "cache_write": 1.25, "cache_write_1h": 2.00, "cache_read": 0.10},
    "claude-sonnet-4-6":         {"input": 3.00, "output": 15.00, "cache_write": 3.75, "cache_write_1h": 6.00, "cache_read": 0.30},
    "claude-sonnet-5":           {"input": 2.00, "output": 10.00, "cache_write": 2.50, "cache_write_1h": 4.00, "cache_read": 0.20},  # permanent rate (was introductory; the planned $3/$15 increase was cancelled — see module docstring)
    "claude-opus-4-8":           {"input": 5.00, "output": 25.00, "cache_write": 6.25, "cache_write_1h": 10.00, "cache_read": 0.50},
    # Opus 5.5: cache_read is 0.20, which is 0.05x input. Every other row uses
    # ~0.1x. That is Anthropic's published rate for this model, not a typo; do
    # not "correct" it to 0.40. cache_write_1h follows the repo-wide 2x rule.
    "claude-opus-5-5":           {"input": 4.00, "output": 20.00, "cache_write": 5.00, "cache_write_1h": 8.00, "cache_read": 0.20},
    "claude-opus-5":             {"input": 5.00, "output": 25.00, "cache_write": 6.25, "cache_write_1h": 10.00, "cache_read": 0.50},
}

_FALLBACK = MODEL_PRICING["claude-sonnet-4-6"]

# The five rows above were checked against Anthropic's pricing page on this
# date (PR 3a seed). Rows added below are NOT verified.
SEED_PRICING_VERIFIED_ON = "2026-09-28"

# Models added to the catalog before their pricing is confirmed. A None rate
# means "not confirmed": it is never filled from a multiplier. Source: the
# cached API reference of 2026-09-25, not confirmed against the live page.
# Fable 5's cache read is left empty too: the reference's $1.00 is exactly 0.1x
# input, the multiplier guess this table avoids, and Fable 5.1 does not follow it.
# Cache-write rates are unconfirmed for all three, so every row here is
# incomplete and the model cannot be enabled for any role until a person
# fills the gaps and verifies the row on /admin/system/ai.
_UNVERIFIED_NOTE = "from cached API reference 2026-09-25, not confirmed against the live page"
SEED_ONLY_PRICING: dict[str, dict] = {
    "claude-fable-5":   {"input": 10.0, "output": 50.0, "cache_write": None, "cache_write_1h": None, "cache_read": None},
    "claude-fable-5-1": {"input": 10.0, "output": 50.0, "cache_write": None, "cache_write_1h": None, "cache_read": 0.25},
    "claude-sonnet-5-5": {"input": 2.0, "output": 10.0, "cache_write": None, "cache_write_1h": None, "cache_read": 0.20},
}


def seed_pricing_rows() -> dict[str, dict]:
    """Rows for the one-time model_pricing seed: the verified code dict plus
    the unverified, deliberately incomplete additions."""
    rows = {mid: {**r, "verified_on": SEED_PRICING_VERIFIED_ON, "source_note": ""}
            for mid, r in MODEL_PRICING.items()}
    for mid, r in SEED_ONLY_PRICING.items():
        rows[mid] = {**r, "verified_on": "", "source_note": _UNVERIFIED_NOTE}
    return rows


import logging
import os
import sqlite3
import threading
import time

_logger = logging.getLogger(__name__)

# Price cache. compute_cost has no Library handle (a dozen call sites), so it
# reads model_pricing straight from LINKLIB_DB and caches per db path. An
# admin edit calls reset_price_cache() in its own process; another uvicorn
# worker would only see the change when its entry expires, so entries live
# _PRICE_TTL seconds (the app runs one worker today; a second would be stale
# for at most this long). Tests reset it in tests/conftest.py.
_PRICE_TTL = 60.0
_price_cache: dict = {}
_price_lock = threading.Lock()
_warned: set = set()


def reset_price_cache() -> None:
    with _price_lock:
        _price_cache.clear()
        _warned.clear()


def _db_rates() -> dict[str, dict]:
    """Complete model_pricing rows from the DB, {} when unavailable."""
    path = os.environ.get("LINKLIB_DB", "")
    if not path or not os.path.exists(path):
        return {}
    now = time.time()
    with _price_lock:
        hit = _price_cache.get(path)
        if hit and now - hit[0] < _PRICE_TTL:
            return hit[1]
    rates: dict[str, dict] = {}
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2.0)
        try:
            conn.row_factory = sqlite3.Row
            for r in conn.execute("SELECT * FROM model_pricing").fetchall():
                row = {k: r[k] for k in ("input", "output", "cache_write", "cache_write_1h", "cache_read")}
                if all(v is not None for v in row.values()):
                    rates[r["model_id"]] = row
        finally:
            conn.close()
    except sqlite3.Error:
        return {}
    with _price_lock:
        _price_cache[path] = (now, rates)
    return rates


def rates_for(model: str) -> dict[str, float]:
    """DB row if complete, else the code dict (last resort), else the Sonnet
    4.6 fallback with a warning (once per model per process)."""
    rates = _db_rates().get(model) or MODEL_PRICING.get(model)
    if rates is None:
        with _price_lock:
            first = model not in _warned
            _warned.add(model)
        if first:
            _logger.warning("compute_cost: no complete pricing for model %r; billing at Sonnet 4.6 "
                            "rates. Fix it on /admin/system/ai.", model)
        return _FALLBACK
    return rates


def compute_cost(model: str, input_tokens: int = 0, output_tokens: int = 0,
                 cache_creation_tokens: int = 0, cache_read_tokens: int = 0,
                 cache_creation_1h_tokens: int = 0) -> float:
    """Exact USD cost for one API call from its real token usage.

    Rates come from the model_pricing table (see rates_for). A model with no
    complete row anywhere falls back to Sonnet 4.6 rates, never $0, and logs
    a warning.

    `cache_creation_tokens` are priced at the 5-minute-TTL write rate;
    `cache_creation_1h_tokens` (the 1-hour-TTL share of cache writes, a
    separate bucket in the API's usage breakdown) at the higher 1-hour rate.
    Pass each bucket once: a 1-hour token must not also be counted in
    `cache_creation_tokens`.
    """
    rates = rates_for(model)
    return (
        input_tokens * rates["input"]
        + output_tokens * rates["output"]
        + cache_creation_tokens * rates["cache_write"]
        + cache_creation_1h_tokens * rates["cache_write_1h"]
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


# How often Brian should manually re-check EXA_PRICING against Exa's
# published rates (exa.ai/pricing) — same "no pricing API to reconcile
# against" situation as PRICING_REVIEW_STALE_DAYS above, so this is a third,
# separate dated-reminder threshold for a human attestation. 90 days, same
# window as Claude pricing above (not the 30-day new-model-awareness window
# in linklib.models) — this checks whether an existing rate is still
# accurate, not whether something new exists to add, the same category of
# check as Claude pricing. Confirmed with Brian (issue tracking the Exa
# pricing freshness banner, 2026-09).
EXA_PRICING_REVIEW_STALE_DAYS = 90


def exa_pricing_review_is_stale(last_verified_iso: str, *, now: datetime | None = None) -> bool:
    """True when `last_verified_iso` (a stored settings value, empty string
    if never recorded) is older than EXA_PRICING_REVIEW_STALE_DAYS — or
    missing entirely, the same "go check it" signal as genuinely stale.
    Mirrors pricing_review_is_stale exactly (same logic, separate
    function/setting so the two reminders can go stale independently)."""
    if not last_verified_iso:
        return True
    try:
        then = datetime.fromisoformat(last_verified_iso)
    except ValueError:
        return True
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return (now - then).days >= EXA_PRICING_REVIEW_STALE_DAYS


# USD per 1,000 requests. Checked 2026-07-26 against Exa's published rates
# (exa.ai/pricing). `included_results` is how many results the base price
# covers before `extra_per_1k_result` kicks in per result over that (Exa's
# per-additional-result fee is the same $1/1k across every search tier).
# Only "search" is modeled — the one endpoint Phase 1 has a concrete use for.
# Contents ($1/1k pages/content type), Deep Search ($12/1k), Deep-Reasoning
# Search ($15/1k), and Answer ($5/1k) are real Exa tiers but aren't wired to
# anything yet; add a row here only once a caller actually needs one, same
# as the "add a model row when it becomes selectable" rule above.
EXA_PRICING: dict[str, dict[str, float]] = {
    "search": {"base_per_1k": 7.00, "included_results": 10, "extra_per_1k_result": 1.00},
}

_EXA_FALLBACK = EXA_PRICING["search"]


def compute_exa_cost(endpoint: str = "search", num_results: int = 0) -> float:
    """Exact USD cost for one Exa API call from its real result count.

    `num_results` is how many results the call actually returned (not the
    number requested) — Exa's overage fee is billed per result delivered.
    Falls back to the "search" rates for an unrecognized endpoint, matching
    compute_cost's never-silently-$0 behavior above.
    """
    rates = EXA_PRICING.get(endpoint, _EXA_FALLBACK)
    overage = max(0, num_results - rates["included_results"])
    return (rates["base_per_1k"] + overage * rates["extra_per_1k_result"]) / 1_000
