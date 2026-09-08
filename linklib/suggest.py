"""Predict which queued candidates the curator would approve.

Getting through a big backlog (e.g. a SaaStr backfill) by hand is slow. This
learns from what's already in the library from that source (what gets KEPT) and
what was dismissed (what gets SKIPPED), then asks Claude to predict keep/skip for
the pending candidates — so the obvious keeps can be approved in bulk and the
obvious skips dismissed, leaving only the judgment calls for review.

Predictions are advisory: nothing is auto-approved. Needs the anthropic SDK + key;
returns None when unavailable.
"""
from __future__ import annotations

import json
import os

from .db import Library
from .models import DEFAULT_CHAT_MODEL

DEFAULT_MODEL = os.environ.get("LINKLIB_CHAT_MODEL", DEFAULT_CHAT_MODEL)

_PROMPT = """A finance leader curates a research library for finance leaders at
high-growth tech companies. They keep substantive written articles and skip
podcasts/webinars, slide decks, annual predictions, and content aimed at VC/PE
fund managers or LPs (rather than company operators).

Below is what they have KEPT in their library (recent saves, especially from
"{source}") and what they have SKIPPED, then a list of CANDIDATES from "{source}"
awaiting review. For each candidate, predict whether this curator would keep it,
based on the patterns in their keeps and skips.

Return STRICT JSON only: an array of objects, one per candidate, each
{{"url": "<the candidate url>", "keep": true|false, "reason": "<≤10 words>"}}.
Include every candidate exactly once. No prose, no markdown.

KEPT (examples of what they add):
{kept}

SKIPPED (examples of what they reject):
{skipped}

CANDIDATES to predict:
{candidates}
"""


def _fmt(items: list[dict], with_tags: bool = False) -> str:
    lines = []
    for it in items:
        title = (it.get("title") or it.get("url") or "").strip()
        summ = (it.get("summary") or "").strip().replace("\n", " ")[:220]
        line = f"- {title}"
        if summ:
            line += f" — {summ}"
        if with_tags and it.get("tags"):
            line += f"  [tags: {', '.join(it['tags'][:6])}]"
        lines.append(line)
    return "\n".join(lines) or "(none)"


def _fmt_candidates(items: list[dict]) -> str:
    lines = []
    for it in items:
        title = (it.get("title") or it.get("url") or "").strip()
        summ = (it.get("summary") or "").strip().replace("\n", " ")[:220]
        lines.append(f'- url: {it["url"]}\n  title: {title}' + (f"\n  summary: {summ}" if summ else ""))
    return "\n".join(lines)


def suggest_approvals(lib: Library, source: str, model: str | None = None,
                      max_items: int = 80) -> dict | None:
    """Predict keep/skip for up to `max_items` pending candidates from `source`.

    Returns {url: {"keep": bool, "reason": str}} for the predicted items, {} when
    there's nothing to predict, or None when we can't learn/judge (no examples or
    no API)."""
    # Taste profile: prefer same-source approvals; supplement with library-wide
    # recent saves when this source has few (so it works on a fresh backlog too).
    kept = lib.recent_articles_by_source(source, limit=25)
    if len(kept) < 8:
        seen = {a["url"] for a in kept}
        for a in lib.recent_articles(limit=40):
            if a["url"] not in seen:
                kept.append(a)
                seen.add(a["url"])
            if len(kept) >= 25:
                break
    if not kept:
        return None  # nothing approved anywhere yet to learn from
    pending = [c for c in lib.list_queue(status="pending") if (c.get("source") or "") == source]
    if not pending:
        return {}
    pending = pending[:max_items]
    dismissed = [c for c in lib.list_queue(status="dismissed") if (c.get("source") or "") == source][:20]

    try:
        from anthropic import Anthropic
    except ImportError:
        return None
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None

    prompt = _PROMPT.format(source=source, kept=_fmt(kept, with_tags=True),
                            skipped=_fmt(dismissed), candidates=_fmt_candidates(pending))
    try:
        client = Anthropic()
        resp = client.messages.create(
            model=model or DEFAULT_MODEL, max_tokens=4000,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        data = json.loads(raw)
        valid = {c["url"] for c in pending}
        out: dict = {}
        for row in data:
            url = str(row.get("url", "")).strip()
            if url in valid:
                out[url] = {"keep": bool(row.get("keep")),
                            "reason": str(row.get("reason", "")).strip()[:80]}
        return out
    except Exception:
        return None
