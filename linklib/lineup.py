"""Compare Anthropic's live model list with what the app claims to know.

The live list (`linklib.models._live_models`, the Models API) is the real
signal for "a new model shipped". This module turns it into findings:

- a live model that is in none of the registry, any FP&A Buddy tier, the
  matchmaker default or the enrichment setting, and is not on the "not using"
  list;
- a model the app uses that has no `MODEL_PRICING` row (its spend would be
  recorded at Sonnet 4.6 rates, so this one cannot be waved away);
- a model the app uses that the API no longer lists.

A registry model that no Buddy tier uses is not a gap: the registry feeds the
enrichment, re-enrich and backfill pickers on its own.

When the API cannot be reached the result says it could not compare. It never
returns an empty finding list for that case.

The "not using" list lives in the `models_not_using` setting (JSON list of
{id, reason, added_at}); see Library.list_models_not_using.
"""
from __future__ import annotations

from . import models as _models


def _label(mid: str, live: dict) -> str:
    return (live.get(mid) or {}).get("name") or mid


def lineup_findings(live: dict | None, *, registry_ids, tier_ids, priced_ids,
                    extra_in_use=(), ignored_ids=()) -> dict:
    """Pure comparison. Returns {"compared": bool, "reason": str, "findings": [...]}.

    Each finding: {"id", "kind", "message", "ignorable"} with kind one of
    "unknown", "no-pricing", "not-live".
    """
    if not live:
        return {"compared": False, "findings": [],
                "reason": "The Models API could not be reached, so the lineup was not compared."}
    registry_ids, tier_ids = set(registry_ids), set(tier_ids)
    priced_ids, ignored_ids = set(priced_ids), set(ignored_ids)
    where: dict[str, list[str]] = {}
    for mid in registry_ids:
        where.setdefault(mid, []).append("the registry")
    for mid in tier_ids:
        where.setdefault(mid, []).append("an FP&A Buddy tier")
    for mid in extra_in_use:
        where.setdefault(mid, []).append("a model default or setting")
    findings: list[dict] = []
    for mid in sorted(live):
        if mid in where or mid in ignored_ids:
            continue
        if mid in priced_ids:
            msg = (f"{_label(mid, live)} ({mid}) is live and not in the registry or an "
                   f"FP&A Buddy tier. It has a pricing row.")
        else:
            msg = (f"{_label(mid, live)} ({mid}) is live and has no MODEL_PRICING row. "
                   f"It is also not in the registry or an FP&A Buddy tier.")
        findings.append({"id": mid, "kind": "unknown", "message": msg, "ignorable": True})
    for mid in sorted(where):
        used_in = " and ".join(where[mid])
        if mid not in priced_ids:
            findings.append({"id": mid, "kind": "no-pricing", "ignorable": False, "message":
                             f"{mid} is used ({used_in}) and has no MODEL_PRICING row. "
                             f"Its spend would be recorded at Sonnet 4.6 rates."})
        if mid not in live:
            findings.append({"id": mid, "kind": "not-live", "ignorable": False, "message":
                             f"{mid} is used ({used_in}) but the Models API no longer lists it."})
    return {"compared": True, "reason": "", "findings": findings}


def check_lineup(lib, *, fetch: bool = True) -> dict:
    """Gather the app's own claims and compare them to the live list.

    `fetch=False` reads only an already-warm cache (the admin badge path must
    not make a network call); with a cold cache it reports "not compared".
    """
    from .agent import DEFAULT_MODEL, EFFORT_SETTINGS
    from .pricing import MODEL_PRICING
    live = _models._live_models() if fetch else _models.cached_live_models()
    ignored = lib.list_ignored_model_ids()
    extra = {DEFAULT_MODEL, _models.DEFAULT_CHAT_MODEL, lib.get_enrich_model()}
    return lineup_findings(
        live,
        registry_ids={m["id"] for m in _models._REGISTRY},
        tier_ids={s["model"] for s in EFFORT_SETTINGS.values()},
        priced_ids=set(MODEL_PRICING) | {r['model_id'] for r in lib.list_model_pricing()
                                         if not lib.pricing_problems(r)},
        extra_in_use=extra,
        ignored_ids=ignored,
    )
