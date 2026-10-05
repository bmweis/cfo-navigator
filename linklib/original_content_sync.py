"""Mirror `original_content` pieces into `articles` so FP&A Buddy's existing
hybrid retrieval (FTS5 + vector, linklib.agent.retrieve()) and citation
mechanism (_build_source_documents) can find and cite Brian's own published
writing — without a parallel index or a fourth retrieval branch.

Background: bmweis.com can't be self-fetched (Cloudflare Bot Fight Mode
blocks it), so `original_content.body_md` — already the canonical source
`webapp.app._render_original_content_markdown` renders publicly — is mirrored
directly, never via HTTP. See CLAUDE.md's "FP&A Buddy Published-Content
Ingestion" entry for the full design writeup, including the Step 0
investigation's locked decisions.

sync_original_content_article() is the one call site both admin routes
(`POST /admin/thought-leadership/original/new`, `POST /admin/thought-leadership/original/{id}/edit`)
use — mirrors pipeline.ingest_url's role as the single shared entry point for
"go get this content indexed," just from an internal row instead of a URL
fetch. Called synchronously at the mutation point (same "regenerate at the
point of mutation" convention Library.write_opml() established for the OPML
file), not queued or run on a schedule.

Provenance, not priority (locked decision — do not re-litigate): the mirrored
row carries articles.is_own_content=1, read only by
linklib.agent._build_source_documents/linklib.citations.extract_citations for
citation labeling. Nothing in retrieve()/_rrf_merge/lib.search()/
lib.vector_search() reads this column — a mirrored article surfaces only when
it's a genuine merit-based match, exactly like any other article.
"""
from __future__ import annotations

import os

import markdown as _markdown

from .db import Library, normalize_url

# Deliberately duplicated from webapp.app._OC_MARKDOWN_EXTENSIONS rather than
# imported — linklib never imports from webapp (a one-way dependency this
# codebase maintains throughout), and this module needs the same rendering
# pass webapp.app._render_original_content_markdown uses so indexed text
# matches what's actually shown on the public page. If _OC_MARKDOWN_EXTENSIONS
# ever changes, this constant needs the same change — flagged here on both
# ends (see webapp/app.py's own comment pointing back at this one).
_MARKDOWN_EXTENSIONS = ["fenced_code", "tables"]

# Same default LINKLIB_PUBLIC_BASE convention webapp/app.py's own PUBLIC_BASE
# uses (read independently here rather than importing webapp's constant, for
# the same one-way-dependency reason as _MARKDOWN_EXTENSIONS above).
_PUBLIC_BASE = os.environ.get("LINKLIB_PUBLIC_BASE", "http://localhost:8000")


def plain_text_from_body_md(body_md: str) -> str:
    """Render body_md the same way the public page does, then strip it down
    to clean indexable prose — not just "strip markdown," but strip whatever
    HTML the permissive renderer can produce, since original_content.body_md
    allows raw HTML passthrough (admin-authored, not public input — see
    _render_original_content_markdown's own docstring) and several already-
    shipped pieces use it (e.g. the ported .ns-table/.ger-pull/.fah-* blocks).

    Uses BeautifulSoup (already a dependency — linklib/extract.py) with a
    plain `" "` separator — CLAUDE.md's Reader follow-up bullet flagged that
    exact pattern (`get_text(" ", strip=True)`) as a bug there, but that was
    about DISPLAY text: a reader loses real paragraph structure. This is
    INDEX text (FTS/embeddings), where paragraph fidelity doesn't matter at
    all and a single-space separator is actually the right choice — it's
    what keeps words that share one inline run together as one phrase
    ("Some **bold** text" -> "Some bold text", not "Some\nbold\ntext", since
    get_text(sep) joins every leaf text node, including inline ones, with
    that separator) while still preventing adjacent block-level elements'
    words from gluing together at a tag boundary (e.g.
    "...text</td><td>more..." tokenizing as one run-on word for FTS).

    <script>/<style> contents are dropped outright before extracting text —
    the ported raw-HTML blocks include <style> tags whose CSS would
    otherwise get indexed as searchable "content."
    """
    from bs4 import BeautifulSoup

    if not body_md or not body_md.strip():
        return ""
    html = _markdown.markdown(body_md, extensions=_MARKDOWN_EXTENSIONS)
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return soup.get_text(" ", strip=True)


def mirrored_article_url(slug: str) -> str:
    """The canonical, real, fetchable URL for a mirrored piece — the actual
    /thought-leadership/{slug} page, not a placeholder or the raw markdown
    source. Absolute (not relative), since articles.url is the natural key
    everywhere else in this codebase and citation rendering links to it
    directly."""
    return f"{_PUBLIC_BASE.rstrip('/')}/thought-leadership/{slug}"


def sync_original_content_article(lib: Library, item_id: int) -> int | None:
    """Mirror one original_content row into articles (insert on first sync,
    overwrite-in-place on every later edit — never Library.upsert()'s
    merge-into-existing semantics, which would keep stale content on a
    deliberate edit). Returns the mirrored articles.id, or None when there's
    nothing to mirror (a card-metadata-only row: body_md IS NULL, one of the
    three literal bespoke routes renders the actual piece — see
    original_content's own schema comment).

    A row that HAD a mirror and had body_md cleared back to empty gets its
    mirror deleted (Library.delete_article, cascading FTS/vector/citation-
    log rows) rather than left orphaned — CLAUDE.md's "No dead data" rule.

    Never touches enrichment (summary/tags) or the enrichment cost ledger —
    out of scope per the locked decision to mirror content/index only.
    Embedding still runs (pipeline.embed_article, best-effort, silently
    no-ops without OPENAI_API_KEY) since vector retrieval needs it.
    """
    from .pipeline import embed_article

    row = lib.get_original_content(item_id)
    if row is None:
        return None

    body_md = (row.get("body_md") or "").strip()
    existing_mirror_id = row.get("mirrored_article_id")

    if not body_md:
        if existing_mirror_id:
            lib.delete_article(existing_mirror_id)
            lib.set_original_content_mirrored_article_id(item_id, None)
        return None

    url = mirrored_article_url(row["slug"])
    title = row["title"] or row["slug"]
    content = plain_text_from_body_md(body_md)

    article_id = existing_mirror_id
    if article_id is None:
        # A stray prior article already at this exact URL (e.g. a leftover
        # from before this feature existed) is adopted as the mirror rather
        # than colliding on articles.url's UNIQUE constraint — the mirror's
        # content always wins from here on, via update_mirrored_article
        # below, same as any other re-sync.
        # Normalize first: every writer stores articles.url via normalize_url
        # (https, no www), so a raw http:// base would miss a stray stored as
        # https and the insert below would collide (issue #665).
        existing = lib.get_article_by_url(normalize_url(url))
        article_id = existing["id"] if existing else None

    if article_id is None:
        article_id = lib.insert_mirrored_article(url=url, title=title, content=content)
    else:
        lib.update_mirrored_article(article_id, title=title, url=url, content=content)

    lib.set_article_own_content(article_id, True)
    if existing_mirror_id != article_id:
        lib.set_original_content_mirrored_article_id(item_id, article_id)

    embed_article(lib, article_id)
    return article_id
