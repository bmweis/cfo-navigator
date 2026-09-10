"""MCP server, Phase 4: Library (Archive) search + Feed browse/search.

Two tools registered onto the same FastMCP instance `webapp/mcp_server.py`
builds: `search_archive`/`get_article` (Track A, the Archive's indexed
`articles` corpus) and `browse_feed`/`search_feed` (Track B, the live RSS
Feed). Kept in its own module for the same reason `mcp_toolbox.py` is
separate from `mcp_server.py`: this is domain content, reusing the shared
auth/host-security plumbing rather than duplicating it.

Auth model — re-verified against the live route code before building this
(see CLAUDE.md's MCP Phase 4 pointer bullet / the Step 0 report), NOT
assumed from an earlier planning note: `/read`, `/read/{id}`, and
`/api/read-article` all gate on `_is_authed` (admin specifically), not
`_is_member` (any signed-in user). All four tools here are **admin-role
only**, via `webapp.mcp_server.require_admin` — matching what the Reader
UI actually enforces today, not a stale "Library tools are admin-only"
assumption. (`GET /api/search`, a separate, older route wrapping the same
`Library.search()`, was member-tier-gated at the time this phase shipped —
a likely-unintentional survivor of the Phase 1 Library-goes-admin-only
restructure, deliberately left untouched by this phase. Fixed in a later,
separate PR (2026-09): it now uses `_require_api`, matching `/read`'s
admin-only enforcement.)

Track A reuses `linklib.agent.retrieve()` (hybrid FTS5+vector, RRF-merged)
and `Library.get_article`/`get_article_by_url` completely unmodified — no
new search infrastructure. `search_archive` returns compact hits (an
excerpt, not full article text); `get_article` is the full-detail
companion, mirroring Phase 3's search-thin/get-full split
(`search_tools`/`get_tool`).

Track B reuses `linklib.feed.get_feed_items()` and
`linklib.agent.retrieve_feed()` completely unmodified. Feed has no DB-backed
history of items (30-minute in-memory cache only, per `feed.py`), so
"browse chronologically, optionally by category" (`browse_feed`) and
"keyword-relevance rank" (`search_feed`) are shipped as two separate tools
rather than forcing one shape to cover both — confirmed against the actual
`/read?view=feed` route before proposing this, not inferred from
`retrieve_feed()`'s Buddy-internal usage alone.

No HTML/markup anywhere in any tool's output — plain dict/list/str/bool/int
only, same discipline `mcp_toolbox.py`'s own docstring establishes.
"""
from __future__ import annotations

import re
from typing import Callable

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from linklib.db import Library

from webapp.mcp_server import require_admin

_MAX_LIMIT = 50
_DEFAULT_SEARCH_LIMIT = 20
_DEFAULT_FEED_LIMIT = 20
_EXCERPT_CHARS = 300


def _clamp_limit(limit: int, default: int = _DEFAULT_SEARCH_LIMIT) -> int:
    try:
        n = int(limit)
    except (TypeError, ValueError):
        n = default
    return max(1, min(n, _MAX_LIMIT))


def _excerpt(text: str, max_chars: int = _EXCERPT_CHARS) -> str:
    """Whitespace-collapsed lead-in from a plain-text field, truncated with
    a visible marker — same "cap it, mark the cut" discipline
    `sample_rows`'s own `max_cell_chars` truncation uses, just fixed-length
    here rather than tool-configurable (a search hit's excerpt is a
    preview, not the point of calling search_archive — the full text is
    one `get_article` call away)."""
    collapsed = re.sub(r"\s+", " ", (text or "")).strip()
    if len(collapsed) <= max_chars:
        return collapsed
    return collapsed[:max_chars].rstrip() + "…"


def _search_hit(a: dict) -> dict:
    return {
        "id": a["id"],
        "url": a.get("url") or "",
        "title": a.get("title") or "",
        "author": a.get("author") or "",
        "source": a.get("source") or "",
        "tags": a.get("tags") or [],
        "published_at": a.get("published_at") or "",
        "saved_at": a.get("saved_at") or "",
        "is_own_content": bool(a.get("is_own_content")),
        "excerpt": _excerpt(a.get("content") or a.get("summary") or ""),
    }


def _resolve_article(lib: Library, id_or_url: str) -> dict | None:
    s = str(id_or_url).strip()
    if s.isdigit():
        return lib.get_article(int(s))
    return lib.get_article_by_url(s)


def _feed_item(item: dict) -> dict:
    return {
        "title": item.get("title") or "",
        "url": item.get("url") or "",
        "source": item.get("source") or "",
        "category": item.get("category") or "",
        "published_at": item.get("published_at") or "",
        "summary": item.get("summary") or "",
        "paywalled": bool(item.get("paywalled")),
    }


def register_library_tools(mcp: FastMCP, lib_factory: Callable[[], Library],
                            opml_path: str) -> None:
    """Register the four Library/Feed tools onto an already-built FastMCP
    instance (webapp.app calls this right after `mcp_toolbox.
    register_toolbox_tools`). `opml_path` is passed in explicitly (rather
    than imported from webapp.app) for the same reason `lib_factory` is —
    it keeps this module free of a circular import back into webapp.app
    and independently testable against any OPML fixture path."""

    @mcp.tool()
    async def search_archive(ctx: Context, query: str = "",
                              limit: int = _DEFAULT_SEARCH_LIMIT) -> list[dict]:
        """Search Brian's saved-article Archive (the CFO Library) — the
        same hybrid FTS5 + vector semantic search (RRF-merged) FP&A Buddy
        uses for its own library retrieval (`linklib.agent.retrieve`),
        wrapped unmodified. An empty `query` returns the most recently
        saved articles instead of running a search (matching
        `Library.search`'s own empty-query behavior), so this also works
        as "what's newest in the Archive". Each hit's `is_own_content`
        flags Brian's own writing (the 3 flagship Original Content pieces,
        mirrored in; any bookmarklet-saved external piece matched against
        `thought_leadership`) versus a saved external article — both are
        just articles with a flag, so there's no separate tool for
        "published content". Returns compact hits (title/url/source/tags/
        dates/an excerpt) — call `get_article` with a hit's `id` or `url`
        for full text. `limit` capped at 50.

        Note: a non-empty query may trigger one OpenAI query-embedding
        call (the vector half of hybrid retrieval) — a fraction of a cent,
        uncapped and untracked here (unlike FP&A Buddy's own query-embed
        cost, which folds into a user's dollar cap; this tool has no
        per-call cost ledger of its own). Acceptable at admin-only,
        single-user scale; flagging it rather than silently having a cost
        with no visible ledger anywhere.

        Admin role only — matches what `/read` (the Reader UI this wraps)
        actually enforces today."""
        require_admin(ctx, lib_factory)
        limit = _clamp_limit(limit)
        lib = lib_factory()
        try:
            q = query.strip()
            if not q:
                hits = lib.search("", limit=limit)
            else:
                from linklib.agent import retrieve
                hits, _embed_tokens, _embed_cost = retrieve(lib, q, max_sources=limit)
        finally:
            lib.close()
        return [_search_hit(a) for a in hits]

    @mcp.tool()
    async def get_article(ctx: Context, id_or_url: str) -> dict:
        """Full detail for one Archive article — the companion to
        `search_archive` for full-text retrieval. `id_or_url` may be the
        article's numeric id or its exact stored URL. Returns the full
        plain-text `content` (never `content_html` — this tool never
        returns markup, same as every other /mcp tool). Refuses
        (`ToolError`) when no matching article exists. Admin role only."""
        require_admin(ctx, lib_factory)
        lib = lib_factory()
        try:
            article = _resolve_article(lib, id_or_url)
        finally:
            lib.close()
        if not article:
            raise ToolError(f"no such article: {id_or_url!r}")
        return {
            "id": article["id"],
            "url": article.get("url") or "",
            "title": article.get("title") or "",
            "author": article.get("author") or "",
            "source": article.get("source") or "",
            "tags": article.get("tags") or [],
            "summary": article.get("summary") or "",
            "content": article.get("content") or "",
            "published_at": article.get("published_at") or "",
            "saved_at": article.get("saved_at") or "",
            "is_own_content": bool(article.get("is_own_content")),
        }

    @mcp.tool()
    async def browse_feed(ctx: Context, category: str = "",
                           limit: int = _DEFAULT_FEED_LIMIT) -> list[dict]:
        """Browse the live RSS Feed — Brian's subscribed sources
        (`preferred_sites.opml`), newest-first, optionally filtered to one
        `category` (a feed section name, e.g. "News" — matches exactly,
        case-sensitive, same as the site's own curated section names;
        pass "" for every category). Wraps `linklib.feed.get_feed_items`
        unmodified — a live fetch with a 30-minute per-feed cache, NOT a
        DB-backed history; there's no long-term record of what appeared in
        the Feed beyond that cache window, so this always reflects
        "what's in the feed right now", same as `/read?view=feed`.
        `limit` capped at 50. Admin role only — matches what `/read`
        (Feed is one of its three views) actually enforces today."""
        require_admin(ctx, lib_factory)
        limit = _clamp_limit(limit, default=_DEFAULT_FEED_LIMIT)
        from linklib.feed import get_feed_items
        try:
            items, _categories = get_feed_items(opml_path, category=category.strip(), max_total=limit)
        except Exception:
            items = []
        return [_feed_item(i) for i in items]

    @mcp.tool()
    async def search_feed(ctx: Context, query: str,
                           limit: int = _DEFAULT_FEED_LIMIT) -> list[dict]:
        """Keyword-relevance search over the live RSS Feed — wraps
        `linklib.agent.retrieve_feed` unmodified (the same scoring FP&A
        Buddy's own web-tier fallback uses): ranks by keyword-overlap
        count between `query` and each item's title/summary, over
        whatever's currently in the 30-minute feed cache. This is a coarse
        relevance heuristic, not real semantic search — for a stronger
        match, prefer `browse_feed` with a `category` filter and scan the
        results. `limit` capped at 50. Admin role only, same as
        `browse_feed`."""
        require_admin(ctx, lib_factory)
        limit = _clamp_limit(limit, default=_DEFAULT_FEED_LIMIT)
        q = query.strip()
        if not q:
            raise ToolError("search_feed requires a non-empty query — use browse_feed to list items with no query")
        from linklib.agent import retrieve_feed
        items = retrieve_feed(q, opml_path, max_items=limit)
        return [_feed_item(i) for i in items]
