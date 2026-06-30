"""Model registry for the picker UIs — one place to add a model.

Every model picker (Q&A, LinkedIn posts, re-enrich, backfill) renders from this
registry, so a new Claude model becomes selectable everywhere by editing one row
here instead of four scattered lists.

`models_for` optionally consults the live Anthropic Models API (`models.list()`):

* models the API reports as **retired** drop off the pickers on their own, and
* for the chat pickers, models Anthropic ships that are **newer** than anything in
  the registry are surfaced automatically (labelled from the API) — so "pull any
  new model" holds without a code change.

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
    {"id": "claude-opus-4-8", "label": "Opus 4.8", "short": "Best quality",
     "enrich": "Deepest summaries. The one to standardize the archive on."},
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
        client = anthropic.Anthropic()
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
    `allow_new` lets newly released models surface (chat pickers only).
    """
    view = [{"id": m["id"], "label": m["label"], "blurb": m.get(blurb) or m["short"]}
            for m in _REGISTRY]
    return _merge(view, _live_models(), allow_new)
