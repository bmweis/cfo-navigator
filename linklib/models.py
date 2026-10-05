"""Model registry for the picker UIs — one place to add a model, for the
pickers that actually read this registry.

**Corrected (issue #98 Piece 1, 2026-09) — this registry does NOT drive
every model picker in the app.** It feeds exactly three admin surfaces:
/admin/system/ai (the live enrichment-model setting), the re-enrich
picker, and the backfill picker. It does NOT feed FP&A Buddy — Buddy's
Quick/Standard/Deep tiers map to hardcoded model ids in
`linklib.agent.EFFORT_SETTINGS`, a separate dict this registry has no
connection to; adding a model here does not make it selectable (or even
reachable) by a Buddy question. "LinkedIn posts" no longer exists as a
capability at all — that generator (`linklib/social.py`, `scripts/post.py`)
was removed outright in #95 — so this docstring's old "Q&A, LinkedIn posts,
re-enrich, backfill" claim was stale on two of its four counts. See
CLAUDE.md's "Adding a new Claude model — every touchpoint" section for the
full, current list of every place a new model id needs adding.

`models_for` optionally consults the live Anthropic Models API (`models.list()`):

* models the API reports as **retired** drop off the pickers on their own, and
* with `allow_new=True`, models Anthropic ships that are newer than anything in the
  registry are appended (labelled from the API). **No caller passes True today**: the
  chat pickers this was written for no longer exist, and the enrichment pickers are
  deliberately curated. Discovery of new models is now done by `linklib.lineup`,
  which diffs the live list against the registry, pricing and the Buddy tiers and
  shows findings on /admin/checks. `allow_new` is kept only for a future picker that
  wants auto-surfacing.

The enrichment pickers stay curated (no auto-surfacing): a re-enrich runs over the
whole archive, so we don't want to point a 1,500-article pass at an unexpectedly
pricey new model by accident.

If the API or key is missing — or the call fails — every picker falls back to the
static registry, so the UI never breaks. The live result is cached for 30 minutes.
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone

# The shared "default chat model" — the interactive/general-purpose Claude
# default for linklib.agent (ask()'s last-resort fallback, reached only when
# neither an explicit model override nor the effort tier's own model is set)
# and linklib.matchmaker (the model used for every matchmaker turn).
# linklib.dedupe's own near-duplicate verifier shares this same ultimate
# default but keeps its own extra `LINKLIB_DEDUPE_MODEL` override layer ahead
# of it — that two-level fallback chain is deliberate, not drift, so it isn't
# collapsed into this constant's own env-var resolution.
#
# Deliberately NOT used for enrichment: linklib/enrich.py's own DEFAULT_MODEL
# ("claude-opus-5") intentionally picks a more capable/costlier model for
# depth, since that output is the resale-safe customer-facing asset — see
# that module's own docstring. Not unified with this constant on purpose.
# (linklib/queue.py's QUEUE_ENRICH_MODEL, the same idea for the Archive
# Queue's enrichment, was retired along with the queue itself — see PR 3.)
DEFAULT_CHAT_MODEL = "claude-sonnet-4-6"

# Single source of truth, ordered fast -> best. `short` is for compact pickers
# (Q&A, posts, backfill); `enrich` is the longer framing for the re-enrich page.
# Add a model by adding a row here.
_REGISTRY: list[dict] = [
    {"id": "claude-haiku-4-5-20251001", "label": "Haiku 4.5", "short": "Fast · cost-effective",
     "enrich": "Fast and cheap. Good for clearing a big unenriched backlog."},
    {"id": "claude-sonnet-4-6", "label": "Sonnet 4.6", "short": "Balanced",
     "enrich": "Solid summaries at a lower cost."},
    {"id": "claude-sonnet-5", "label": "Sonnet 5", "short": "Balanced · newest",
     "enrich": "Newest balanced model — stronger summaries than Sonnet 4.6 at similar cost."},
    {"id": "claude-opus-5", "label": "Opus 5", "short": "Deep · previous generation",
     "enrich": "Deep summaries. Superseded by Opus 5.5, which is newer and cheaper."},
    # Opus 5.5 ($4/$20 per MTok vs Opus 5's $5/$25). Id and price from Anthropic's
    # published model table; the models check on /admin/checks confirms the id
    # against the live Models API after deploy. Thinking is always on at a
    # default effort of medium; no call in this repo sets thinking or effort.
    {"id": "claude-opus-5-5", "label": "Opus 5.5", "short": "Best quality · newest",
     "enrich": "Deepest summaries, and cheaper than Opus 5. The one to standardize the archive on."},
]

_API_TTL = 1800.0  # 30 min, matching the feed cache
_cache: dict = {"data": None, "at": 0.0}
_lock = threading.Lock()


def _live_models() -> dict | None:
    """Available Claude models from the Anthropic Models API, keyed by id.

    Returns ``{id: {"name": str, "created": <comparable>|None}}`` or ``None`` when
    the SDK/key is absent or the call fails (so callers fall back to the registry).
    Cached for 30 minutes; only successful results are cached.
    """
    now = time.time()
    with _lock:
        if _cache["data"] is not None and now - _cache["at"] < _API_TTL:
            return _cache["data"]
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(timeout=10.0, max_retries=1)
        data: dict = {}
        for m in client.models.list():
            mid = getattr(m, "id", None)
            if not mid or not str(mid).startswith("claude-"):
                continue
            data[mid] = {"name": getattr(m, "display_name", None) or mid,
                         "created": getattr(m, "created_at", None)}
    except Exception:
        return None
    with _lock:
        _cache["data"] = data
        _cache["at"] = time.time()
    return data


def cached_live_models() -> dict | None:
    """The live model list if the 30-minute cache is warm, else None. Never
    makes a network call (the admin badge path uses this)."""
    with _lock:
        if _cache["data"] is not None and time.time() - _cache["at"] < _API_TTL:
            return _cache["data"]
    return None


def _merge(view: list[dict], live: dict | None, allow_new: bool) -> list[dict]:
    """Reconcile the curated `view` with the live model set.

    Pure (no I/O) so it's unit-testable. Drops registry models the API doesn't
    list; when `allow_new`, appends live models that aren't in the registry and
    are at least as new as the newest registry model present.
    """
    if not live:
        return list(view)
    known = {m["id"] for m in view}
    present = [m for m in view if m["id"] in live]
    if not present:
        return list(view)  # API returned nothing we recognize — trust the curation
    out = list(present)
    if allow_new:
        try:
            createds = [live[m["id"]]["created"] for m in present
                        if live[m["id"]].get("created") is not None]
            newest = max(createds) if createds else None
            if newest is not None:
                extras = [(mid, info) for mid, info in live.items()
                          if mid not in known and info.get("created") is not None
                          and info["created"] >= newest]
                extras.sort(key=lambda kv: kv[1]["created"], reverse=True)
                out += [{"id": mid, "label": info.get("name") or mid, "blurb": "Newly available"}
                        for mid, info in extras]
        except Exception:
            pass  # any comparison hiccup → just don't auto-surface; curated set stands
    return out


def models_for(*, blurb: str = "short", allow_new: bool = False) -> list[dict]:
    """Ordered ``[{id, label, blurb}]`` for a picker.

    `blurb` selects which registry field to show ("short" or "enrich").
    `allow_new` appends newer live models; no caller passes True today (see the module docstring).
    """
    view = [{"id": m["id"], "label": m["label"], "blurb": m.get(blurb) or m["short"]}
            for m in _REGISTRY]
    return _merge(view, _live_models(), allow_new)


# Backstop timer only. New models are found by `linklib.lineup`, which diffs the
# live Models API list against the registry, pricing and the Buddy tiers; a
# calendar reminder cannot tell whether anything shipped. What the live list
# cannot show is a silent change to a model that is already in it (a repricing,
# a retirement date), so a long reminder to re-read Anthropic's model docs stays
# as a safety net. 180 days; the pricing reminder (90 days) covers rate changes.
MODELS_REVIEW_STALE_DAYS = 180


def models_review_is_stale(last_reviewed_iso: str, *, now: datetime | None = None) -> bool:
    """True when `last_reviewed_iso` (a stored settings value, empty string
    if never recorded) is older than MODELS_REVIEW_STALE_DAYS — or missing
    entirely, the same "go check it" signal as genuinely stale. Mirrors
    linklib.pricing.pricing_review_is_stale exactly (same logic, separate
    function/setting so the two reminders can go stale independently)."""
    if not last_reviewed_iso:
        return True
    try:
        then = datetime.fromisoformat(last_reviewed_iso)
    except ValueError:
        return True
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return (now - then).days >= MODELS_REVIEW_STALE_DAYS
