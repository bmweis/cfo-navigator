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
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests

from .db import Library, normalize_url

# Depth over thrift: summaries/tags are the product surface. Overridable so a
# big sweep can dial down if needed. Verify current IDs at
# https://docs.claude.com/en/docs/about-claude/models
QUEUE_ENRICH_MODEL = os.environ.get("LINKLIB_QUEUE_ENRICH_MODEL", "claude-opus-4-8")

# Feeds that belong in the Reader but NOT the curated library. News is timely
# and high-volume — good to read, not something Brian saves. Those sources stay
# live in the Reader; they're just never proposed into the queue.
#
# This used to be a module-level set built from LINKLIB_QUEUE_EXCLUDE_CATEGORIES
# and matched against a section's NAME. It's now a stored per-FEED boolean
# (feeds.exclude_from_queue), read via Library.excluded_feed_urls() and matched
# on each item's originating feed URL. Two things the old shape got wrong:
# renaming a section silently changed which sources reached the queue, and
# exclusion was all-or-nothing per section when it's really a judgment about an
# individual source. The env var is retired.


def suggest_tags_heuristic(title: str, summary: str, source: str,
                           vocab: list[str], max_tags: int = 5) -> list[str]:
    """Cheap fallback tags: match your existing vocabulary against the candidate's
    text. Used when enrichment is unavailable so a queued item is never tagless."""
    text = f"{title} {summary} {source}".lower()
    hits = [t for t in vocab if t and t.lower() in text]
    return sorted(set(hits))[:max_tags]


def _enrich_candidate(item: dict, vocab: list[str], *, enrich: bool,
                      model: str, tag_guide: str = "") -> dict:
    """Fetch + enrich one candidate into the kwargs `Library.add_to_queue` wants.

    `item` is a feed/sitemap dict with at least `url`; `title`, `source`,
    `summary`, `published_at` are used when present.

    The returned dict also carries `input_tokens`/`output_tokens`/`cost_usd`
    (issue #105) — real ledger fields `Library.add_to_queue` doesn't accept,
    so callers must pop them before `**cand`-ing into it, then persist them
    via `Library.record_enrichment_cost(article_id=None, ...)` since this
    candidate has no `articles.id` yet (it may never get one, if dismissed).
    """
    url = item["url"]
    title = item.get("title", "") or ""
    source = item.get("source", "") or ""
    summary = item.get("summary", "") or ""
    content = ""
    tags: list[str] = []
    enriched = False
    enrich_model = ""
    enrich_rules = ""
    in_scope = True
    page_published = ""
    input_tokens = 0
    output_tokens = 0
    cost_usd = 0.0

    if enrich:
        try:
            from .extract import fetch_page
            page = fetch_page(url)
            if page.content:
                content = page.content
            if page.title and not title:
                title = page.title
            page_published = page.published
        except Exception:
            pass
        try:
            from . import enrich as enrich_mod
            result = enrich_mod.enrich(
                title or url, content or summary or title,
                known_tags=vocab, model=model, tag_guide=tag_guide,
            )
        except Exception:
            result = None
        if result:
            summary = result.summary or summary
            tags = result.tags
            enriched = True
            enrich_model = result.model
            enrich_rules = result.rules_version
            in_scope = result.in_scope
            input_tokens = result.input_tokens
            output_tokens = result.output_tokens
            cost_usd = result.cost_usd

    if not tags:
        tags = suggest_tags_heuristic(title, summary, source, vocab)

    # The page's own metadata beats a sitemap lastmod (static sites stamp every
    # URL with the build date), so prefer it when we found one.
    published_at = page_published or item.get("published_at") or None

    return dict(
        url=url, title=title, source=source, summary=summary, content=content,
        suggested_tags=tags, published_at=published_at,
        origin=item.get("origin", "feed"), enriched=enriched,
        enrich_model=enrich_model, enrich_rules=enrich_rules,
        in_scope=in_scope,
        input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=cost_usd,
    )


def redate_from_article_pages(lib: Library, source_substr: str = "",
                              progress=lambda *_: None) -> dict:
    """Repair publish dates by re-reading each article page's own metadata.

    The historical sweep takes dates from sitemaps; static sites stamp every URL
    with the build date, so a whole source can land on one wrong day. This re-reads
    the true date from pending queue items (and saved articles) whose source matches
    `source_substr` (case-insensitive; blank = all) and updates the ones it can fix.
    Cheap — fetches the page but does not re-enrich. Returns {scanned, updated}.
    """
    from .extract import fetch_page

    sub = source_substr.strip().lower()
    def _match(src: str) -> bool:
        return (not sub) or (sub in (src or "").lower())

    targets = [("queue", r["url"], None, r.get("source", ""))
               for r in lib.list_queue(status="pending") if _match(r.get("source", ""))]
    targets += [("article", a["url"], a["id"], a.get("source", ""))
                for a in lib.all_articles(limit=100000) if _match(a.get("source", ""))]

    scanned = updated = 0
    for i, (kind, url, art_id, _src) in enumerate(targets):
        scanned += 1
        try:
            page = fetch_page(url)
        except Exception:
            page = None
        if page and page.published:
            if kind == "queue":
                lib.update_queue_published(url, page.published)
            else:
                lib.update_article_published(art_id, page.published)
            updated += 1
        progress(i + 1, len(targets), url)
    return {"scanned": scanned, "updated": updated}


def _excluded_feed_urls(lib: Library,
                        override: Optional[set[str]] = None) -> set[str]:
    """`xml_url` of every read-only feed, or the legacy default when unseeded.

    An unseeded DB returns an empty set from `excluded_feed_urls()`, which is
    indistinguishable from "nothing is excluded" and would start funnelling
    News into the queue. `has_feeds()` separates those two cases; when the
    tables are empty we fall back to the pre-migration News feed URLs.
    """
    if override is not None:
        return override
    if lib.has_feeds():
        return lib.excluded_feed_urls()
    return set(_LEGACY_EXCLUDED_FEED_URLS)


# The two feeds that sat in the "News" section before exclusion moved from
# section to feed. Only consulted when the feeds tables are empty (a fresh test
# fixture, or the window before the boot-time seed runs) — see
# _excluded_feed_urls.
_LEGACY_EXCLUDED_FEED_URLS = frozenset({
    "https://news.crunchbase.com/sections/enterprise/feed/",
    "https://techcrunch.com/enterprise/feed/",
})


def scan_feed_into_queue(lib: Library, opml_path: str, *, enrich: bool = True,
                         model: str = QUEUE_ENRICH_MODEL, max_total: int = 300,
                         excluded_feed_urls: Optional[set[str]] = None,
                         progress=lambda *_: None) -> dict:
    """Pull current feed items, queue the ones not already saved or queued.

    Returns stats: {"scanned", "new", "added"}. Deduping happens before
    enrichment, so we only spend API calls on genuinely new candidates.

    Feeds flagged `exclude_from_queue` are read in the Reader but never
    proposed to the library, so the queue stays curation-grade. Each item is
    matched back to its originating feed by `feed_url` (the feed's own
    `xml_url`, carried on every item by `feed.get_feed_items`), NOT by the
    section name it happens to sit under — a feed can be read-only while its
    section-mates aren't, and names are editable while `xml_url` is the key.
    """
    excluded = _excluded_feed_urls(lib, excluded_feed_urls)
    try:
        from .feed import get_feed_items
        items, _ = get_feed_items(opml_path, max_total=max_total)
    except Exception:
        return {"scanned": 0, "new": 0, "added": 0}

    seen = {normalize_url(u) for u in (lib.article_urls() | lib.queue_urls())}
    new_items = [it for it in items
                 if it.get("url") and normalize_url(it["url"]) not in seen
                 and it.get("feed_url", "") not in excluded]

    added = 0
    skipped_scope = 0
    from . import tagstyle
    guide = tagstyle.effective_tag_guidance(lib)

    for i, it in enumerate(new_items):
        cand = _enrich_candidate(it, lib.known_tags(), enrich=enrich, model=model,
                                 tag_guide=guide)
        in_tok, out_tok, cost = (cand.pop("input_tokens"), cand.pop("output_tokens"),
                                 cand.pop("cost_usd"))
        if cost:
            lib.record_enrichment_cost(None, cand.get("enrich_model", ""),
                                       input_tokens=in_tok, output_tokens=out_tok,
                                       cost_usd=cost)
        if not cand.pop("in_scope", True):
            skipped_scope += 1       # off-audience — don't even propose it
            progress(i + 1, len(new_items), it.get("title", ""))
            continue
        if lib.add_to_queue(**cand):
            added += 1
        progress(i + 1, len(new_items), it.get("title", ""))

    return {"scanned": len(items), "new": len(new_items), "added": added,
            "skipped_scope": skipped_scope}


# ---------------------------------------------------------------------------
# One-time historical catch-up — reach back via each source's sitemap.
#
# RSS only carries recent items, so the backfill reads sitemaps instead. This
# is best-effort per source: coverage varies, and undated or non-article URLs
# are skipped rather than guessed at. The sweep reports per-source coverage so
# the gaps are visible — it doesn't pretend it reached everything.
# ---------------------------------------------------------------------------

_SITEMAP_UA = "Mozilla/5.0 (compatible; CFONavigator/1.0; +https://bmweis.com)"
_SITEMAP_TIMEOUT = 12

# Path fragments that are almost never article posts — skip to cut noise.
_NON_POST = ("/tag/", "/tags/", "/category/", "/categories/", "/author/",
             "/authors/", "/page/", "/about", "/contact", "/privacy",
             "/terms", "/feed", "/archive", "/search", "/subscribe", "/wp-content/")


def _http_get(url: str):
    return requests.get(url, timeout=_SITEMAP_TIMEOUT,
                        headers={"User-Agent": _SITEMAP_UA}, allow_redirects=True)


def _parse_lastmod(s: str | None) -> Optional[datetime]:
    if not s:
        return None
    s = s.strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        pass
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _ensure_aware(dt) -> datetime:
    if isinstance(dt, str):
        dt = _parse_lastmod(dt) or datetime(2000, 1, 1, tzinfo=timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _looks_like_post(url: str) -> bool:
    low = url.lower().rstrip("/")
    return not any(frag in low for frag in _NON_POST)


def discover_sitemaps(site_url: str) -> list[str]:
    """Candidate sitemap URLs for a site: robots.txt declarations first, then
    the common conventional paths. Order matters — the caller uses the first
    that yields entries."""
    if not urlparse(site_url).scheme:
        site_url = "https://" + site_url
    p = urlparse(site_url)
    root = f"{p.scheme}://{p.netloc}"
    found: list[str] = []
    try:
        r = _http_get(urljoin(root, "/robots.txt"))
        if r.ok:
            for line in r.text.splitlines():
                if line.lower().startswith("sitemap:"):
                    sm = line.split(":", 1)[1].strip()
                    if sm:
                        found.append(sm)
    except Exception:
        pass
    for path in ("/sitemap.xml", "/sitemap_index.xml", "/sitemap-index.xml",
                 "/wp-sitemap.xml", "/sitemap/sitemap-index.xml"):
        found.append(urljoin(root, path))
    seen: set[str] = set()
    out: list[str] = []
    for u in found:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def parse_sitemap_xml(content: bytes) -> tuple[str, list[dict]]:
    """Parse sitemap XML bytes. Returns (kind, entries) where kind is
    'sitemapindex' (entries are {'url'} sub-sitemaps) or 'urlset' (entries are
    {'url', 'lastmod'}). Returns ('', []) on parse failure."""
    try:
        root = ET.fromstring(content)
    except Exception:
        return "", []
    kind = _localname(root.tag)
    entries: list[dict] = []
    for node in root:
        loc = None
        lastmod = None
        for child in node:
            ln = _localname(child.tag)
            if ln == "loc":
                loc = (child.text or "").strip()
            elif ln == "lastmod":
                lastmod = _parse_lastmod(child.text)
        if loc:
            entries.append({"url": loc, "lastmod": lastmod})
    return kind, entries


def fetch_sitemap_entries(sitemap_url: str, *, _depth: int = 0,
                          _budget: Optional[list[int]] = None) -> list[dict]:
    """Return [{'url', 'lastmod'}] from a sitemap, recursing one level into a
    sitemap index. Best-effort — returns [] on any network/parse error."""
    if _budget is None:
        _budget = [50]
    if _depth > 2:
        return []
    try:
        r = _http_get(sitemap_url)
        if not r.ok:
            return []
    except Exception:
        return []
    kind, entries = parse_sitemap_xml(r.content)
    if kind == "sitemapindex":
        out: list[dict] = []
        for sm in entries:
            if _budget[0] <= 0:
                break
            _budget[0] -= 1
            out.extend(fetch_sitemap_entries(sm["url"], _depth=_depth + 1, _budget=_budget))
        return out
    return entries


def scan_sitemaps_into_queue(lib: Library, feeds, since, *, enrich: bool = True,
                             model: str = QUEUE_ENRICH_MODEL,
                             per_source_limit: int = 150, dry_run: bool = False,
                             excluded_feed_urls: Optional[set[str]] = None,
                             progress=lambda *_: None) -> list[dict]:
    """One-time historical sweep across `feeds` (FeedMeta with .name/.html_url),
    queuing article candidates published on/after `since` (a datetime or ISO
    string = your saves cutoff). Returns a per-source coverage report.

    `dry_run` reports candidate counts without fetching/enriching/saving — use
    it to preview reach before spending API calls.

    Feeds flagged `exclude_from_queue` are skipped — they belong in the Reader,
    not the curated library. Matched on each FeedMeta's `xml_url` against the
    stored per-feed flag, not on its section name.
    """
    excluded = _excluded_feed_urls(lib, excluded_feed_urls)
    since = _ensure_aware(since)
    seen = {normalize_url(u) for u in (lib.article_urls() | lib.queue_urls())}
    from . import tagstyle
    guide = tagstyle.effective_tag_guidance(lib)
    report: list[dict] = []

    for f in feeds:
        site = getattr(f, "html_url", "") or ""
        stat = {"source": f.name, "site": site, "sitemap": None,
                "candidates": 0, "added": 0, "undated": 0, "skipped_scope": 0,
                "note": ""}
        if getattr(f, "xml_url", "") in excluded:
            stat["note"] = "skipped — read-only feed (Reader only, not library)"
            report.append(stat)
            continue
        if not site:
            stat["note"] = "no site URL in OPML"
            report.append(stat)
            continue

        entries: list[dict] = []
        for sm in discover_sitemaps(site):
            entries = fetch_sitemap_entries(sm)
            if entries:
                stat["sitemap"] = sm
                break
        if not entries:
            stat["note"] = "no usable sitemap"
            report.append(stat)
            continue

        candidates: list[dict] = []
        for e in entries:
            url = e["url"]
            if not _looks_like_post(url) or normalize_url(url) in seen:
                continue
            if e["lastmod"] is None:
                stat["undated"] += 1
                continue
            if e["lastmod"] >= since:
                candidates.append(e)
        candidates.sort(key=lambda e: e["lastmod"], reverse=True)
        candidates = candidates[:per_source_limit]
        stat["candidates"] = len(candidates)

        if not dry_run:
            for i, e in enumerate(candidates):
                item = {"url": e["url"], "source": f.name,
                        "published_at": e["lastmod"].isoformat(),
                        "origin": f"backfill:{f.name}"}
                cand = _enrich_candidate(item, lib.known_tags(), enrich=enrich, model=model,
                                         tag_guide=guide)
                in_tok, out_tok, cost = (cand.pop("input_tokens"), cand.pop("output_tokens"),
                                         cand.pop("cost_usd"))
                if cost:
                    lib.record_enrichment_cost(None, cand.get("enrich_model", ""),
                                               input_tokens=in_tok, output_tokens=out_tok,
                                               cost_usd=cost)
                if not cand.pop("in_scope", True):
                    stat["skipped_scope"] += 1   # off-audience — don't propose it
                    progress(f.name, i + 1, len(candidates))
                    continue
                if lib.add_to_queue(**cand):
                    stat["added"] += 1
                    seen.add(normalize_url(e["url"]))
                progress(f.name, i + 1, len(candidates))

        report.append(stat)

    return report
