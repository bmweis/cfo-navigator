"""Library Queue population — turn raw sources into reviewable candidates.

Two entry points share one enrichment path:

- `scan_feed_into_queue` : the go-forward path. Pulls current RSS feed items,
  skips anything already saved or already queued, enriches the new ones, and
  drops them into the queue for review. This is what keeps the library current
  without a future backfill.
- `scan_sitemaps_into_queue` (see `backfill` helpers): the one-time historical
  catch-up — reaches back via each source's sitemap to your saves cutoff.

Enrichment defaults to the most capable model for depth: the summary and tags
are the resale-safe, customer-facing asset, so quality matters more than the
per-item cost of a one-time or low-volume run. Full text (`content`) is fetched
only as an enrichment/search input and never surfaced to a reader.

Like the rest of the pipeline, enrichment degrades gracefully: with no API key
or SDK, candidates still queue (with heuristic tags) — just unenriched.
"""
from __future__ import annotations

import os
from typing import Optional

from .db import Library

# Depth over thrift: summaries/tags are the product surface. Overridable so a
# big sweep can dial down if needed. Verify current IDs at
# https://docs.claude.com/en/docs/about-claude/models
QUEUE_ENRICH_MODEL = os.environ.get("LINKLIB_QUEUE_ENRICH_MODEL", "claude-opus-4-8")


def suggest_tags_heuristic(title: str, summary: str, source: str,
                           vocab: list[str], max_tags: int = 5) -> list[str]:
    """Cheap fallback tags: match your existing vocabulary against the candidate's
    text. Used when enrichment is unavailable so a queued item is never tagless."""
    text = f"{title} {summary} {source}".lower()
    hits = [t for t in vocab if t and t.lower() in text]
    return sorted(set(hits))[:max_tags]


def _enrich_candidate(item: dict, vocab: list[str], *, enrich: bool,
                      model: str) -> dict:
    """Fetch + enrich one candidate into the kwargs `Library.add_to_queue` wants.

    `item` is a feed/sitemap dict with at least `url`; `title`, `source`,
    `summary`, `published_at` are used when present.
    """
    url = item["url"]
    title = item.get("title", "") or ""
    source = item.get("source", "") or ""
    summary = item.get("summary", "") or ""
    content = ""
    tags: list[str] = []
    enriched = False

    if enrich:
        try:
            from .extract import fetch_page
            page = fetch_page(url)
            if page.content:
                content = page.content
            if page.title and not title:
                title = page.title
        except Exception:
            pass
        try:
            from . import enrich as enrich_mod
            result = enrich_mod.enrich(
                title or url, content or summary or title,
                known_tags=vocab, model=model,
            )
        except Exception:
            result = None
        if result:
            summary = result.summary or summary
            tags = result.tags
            enriched = True

    if not tags:
        tags = suggest_tags_heuristic(title, summary, source, vocab)

    return dict(
        url=url, title=title, source=source, summary=summary, content=content,
        suggested_tags=tags, published_at=item.get("published_at") or None,
        origin=item.get("origin", "feed"), enriched=enriched,
    )


def scan_feed_into_queue(lib: Library, opml_path: str, *, enrich: bool = True,
                         model: str = QUEUE_ENRICH_MODEL, max_total: int = 300,
                         progress=lambda *_: None) -> dict:
    """Pull current feed items, queue the ones not already saved or queued.

    Returns stats: {"scanned", "new", "added"}. Deduping happens before
    enrichment, so we only spend API calls on genuinely new candidates.
    """
    try:
        from .feed import get_feed_items
        items, _ = get_feed_items(opml_path, max_total=max_total)
    except Exception:
        return {"scanned": 0, "new": 0, "added": 0}

    seen = lib.article_urls() | lib.queue_urls()
    new_items = [it for it in items if it.get("url") and it["url"] not in seen]

    added = 0
    for i, it in enumerate(new_items):
        cand = _enrich_candidate(it, lib.known_tags(), enrich=enrich, model=model)
        if lib.add_to_queue(**cand):
            added += 1
        progress(i + 1, len(new_items), it.get("title", ""))

    return {"scanned": len(items), "new": len(new_items), "added": added}
