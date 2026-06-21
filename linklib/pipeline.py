"""Shared ingest pipeline used by both jobs.

- `ingest_url`  : the going-forward path. Given a URL (+ optional tags/notes),
                  fetch + enrich + store. The web endpoint and the CLI both
                  call this, so saving from a phone shortcut and from the
                  terminal go through identical logic.
- `enrich_library`: backfill Claude summaries/tags over rows that don't have
                  them yet (e.g. right after the Feedly import).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from .db import Article, Library
from . import enrich as enrich_mod


def ingest_url(
    lib: Library,
    url: str,
    tags: Optional[list[str]] = None,
    notes: str = "",
    fetch_fulltext: bool = True,
    do_enrich: bool = True,
) -> dict:
    """Save a single link going forward. Returns the stored row.

    New saves get auto-tagged against your existing board vocabulary, so a
    read-later queue stays organized in your own taxonomy.
    """
    from .extract import fetch_fulltext as _fetch

    content = _fetch(url) if fetch_fulltext else ""
    art = Article(
        url=url,
        title="",                      # filled by enrichment or left as URL
        content=content,
        notes=notes or "",
        tags=tags or [],
        saved_at=datetime.now(timezone.utc).isoformat(),
    )
    if not art.title:
        art.title = url
    article_id = lib.upsert(art)

    if do_enrich:
        result = enrich_mod.enrich(art.title, content or art.title, known_tags=lib.known_tags())
        if result:
            lib.apply_enrichment(article_id, result.summary, result.tags)

    return next((r for r in lib.search("", limit=10000) if r["id"] == article_id),
                {"id": article_id, "url": url})


def enrich_library(lib: Library, limit: int = 1000, fetch: bool = True,
                   progress=lambda *_: None) -> int:
    """Backfill enrichment over unenriched rows. Returns count enriched.

    When `fetch` is on, pulls the live page text first (best-effort) so the
    summary is built from the article body, not just the title — and the body
    itself gets indexed for search. Dead/paywalled links fall back to the title.
    Tags are biased toward your existing vocabulary.
    """
    from .extract import fetch_fulltext

    vocab = lib.known_tags()
    rows = lib.unenriched(limit=limit)
    done = 0
    for row in rows:
        text = row["content"] or row["summary"] or ""
        if fetch and not text:
            text = fetch_fulltext(row["url"])
            if text:
                lib.update_content(row["id"], text)
        result = enrich_mod.enrich(row["title"], text or row["title"], known_tags=vocab)
        if result:
            lib.apply_enrichment(row["id"], result.summary, result.tags)
            done += 1
        progress(done, len(rows), row["title"])
    return done
