"""Shared ingest pipeline used by both jobs.

- `ingest_url`  : the going-forward path. Given a URL (+ optional tags/notes),
                  fetch + enrich + store. The web endpoint and the CLI both
                  call this, so saving from a phone shortcut and from the
                  terminal go through identical logic.
- `enrich_library`: backfill Claude summaries/tags over rows that don't have
                  them yet (e.g. right after the Feedly import).
- `embed_article` : best-effort embed-on-save for one article (#93), called
                  after enrichment so the embedding sees the finished
                  summary. `scripts/embed_backfill.py` is the separate,
                  batched one-off pass over the existing corpus — it does
                  NOT call this (it needs the OpenAI API's batch endpoint for
                  efficiency), but shares its document_text/content_hash
                  primitives from `linklib.embeddings`.
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
    from .extract import fetch_page

    page_title = ""
    content = ""
    if fetch_fulltext:
        page = fetch_page(url)
        page_title = page.title
        content = page.content

    art = Article(
        url=url,
        title=page_title or url,
        content=content,
        notes=notes or "",
        tags=tags or [],
        saved_at=datetime.now(timezone.utc).isoformat(),
    )
    article_id = lib.upsert(art)

    if do_enrich:
        from . import tagstyle
        result = enrich_mod.enrich(art.title, content or art.title,
                                   known_tags=lib.known_tags(),
                                   tag_guide=tagstyle.effective_tag_guidance(lib))
        if result:
            lib.apply_enrichment(article_id, result.summary, result.tags,
                                 model=result.model, rules=result.rules_version,
                                 in_scope=result.in_scope, scope_reason=result.scope_reason)
            lib.record_enrichment_cost(article_id, result.model,
                                       input_tokens=result.input_tokens,
                                       output_tokens=result.output_tokens,
                                       cost_usd=result.cost_usd)

    embed_article(lib, article_id)

    return next((r for r in lib.search("", limit=10000) if r["id"] == article_id),
                {"id": article_id, "url": url})


def embed_article(lib: Library, article_id: int) -> bool:
    """Best-effort embed-on-save for one article (#93). Builds the same
    document text the backfill script embeds (title + tags + summary +
    content excerpt — see linklib.embeddings.document_text), skips the API
    call when the content hash matches what's already stored (nothing
    changed since the last embed), and persists the vector + overhead-cost
    ledger row via Library.upsert_article_embedding.

    Reads the article fresh from the DB rather than trusting the caller's
    in-memory Article: on a merge into an existing row, `upsert()` keeps the
    EXISTING content/summary over a new-but-empty field, so the row actually
    saved may differ from what the caller passed in.

    Never raises and never blocks a save — a skipped or failed embed leaves
    the article fully searchable via FTS5, just not via vector search yet
    (the backfill script will pick it up later). Returns False on any no-op
    (nothing to embed, vector search unavailable, API call failed, or
    already up to date) so callers/tests can tell whether it actually wrote.
    """
    from . import embeddings as embed_mod
    article = lib.get_article(article_id)
    if article is None:
        return False
    text = embed_mod.document_text(article)
    if not text:
        return False
    new_hash = embed_mod.content_hash(text)
    if lib.embedding_content_hash(article_id) == new_hash:
        return False
    result = embed_mod.embed_texts([text])
    if result is None or not result.vectors:
        return False
    return lib.upsert_article_embedding(
        article_id, result.vectors[0], new_hash, embed_mod.DEFAULT_MODEL,
        input_tokens=result.input_tokens, cost_usd=result.cost_usd,
    )


def enrich_library(lib: Library, limit: int = 1000, fetch: bool = True,
                   force: bool = False, model: Optional[str] = None,
                   progress=lambda *_: None) -> int:
    """Backfill enrichment over unenriched rows. Returns count enriched.

    When `fetch` is on, pulls the live page text first (best-effort) so the
    summary is built from the article body, not just the title — and the body
    itself gets indexed for search. Dead/paywalled links fall back to the title.
    Tags are biased toward your existing vocabulary.

    `force` re-enriches every row, not just unenriched ones — use it to
    standardize the whole library on a single (more capable) `model`. The
    summary is overwritten with the new one; tags are unioned, so any
    hand-curated or board tags survive. Rows that already have stored content
    aren't re-fetched, so a re-run is mostly API time, not crawling.
    """
    from .extract import fetch_page

    from . import tagstyle
    use_model = model or enrich_mod.DEFAULT_MODEL
    vocab = lib.known_tags()
    guide = tagstyle.effective_tag_guidance(lib)
    rows = lib.all_articles(limit=limit) if force else lib.unenriched(limit=limit)
    done = 0
    for row in rows:
        text = row["content"] or row["summary"] or ""
        if fetch and not text:
            page = fetch_page(row["url"])
            if page.content:
                lib.update_content(row["id"], page.content)
                text = page.content
            if page.title and not row["title"]:
                # fill in missing title while we have the page
                lib.conn.execute(
                    "UPDATE articles SET title=?, updated_at=? WHERE id=?",
                    (page.title, _now(), row["id"]),
                )
                lib.conn.commit()
        result = enrich_mod.enrich(row["title"] or row["url"], text or row["title"],
                                   known_tags=vocab, model=use_model, tag_guide=guide)
        if result:
            lib.apply_enrichment(row["id"], result.summary, result.tags,
                                 model=result.model, rules=result.rules_version,
                                 in_scope=result.in_scope, scope_reason=result.scope_reason)
            lib.record_enrichment_cost(row["id"], result.model,
                                       input_tokens=result.input_tokens,
                                       output_tokens=result.output_tokens,
                                       cost_usd=result.cost_usd)
            done += 1
        progress(done, len(rows), row["title"])
    return done


# Domains confirmed permanently dead — a discontinued service, not a
# recoverable block or a moved page. Deliberately a short, hand-curated set:
# each entry requires a real confirmation (a live request showing the
# service itself is gone, not just this one URL), not a hunch, because
# articles_needing_content_backfill() permanently excludes these from every
# future default-scope backfill run (see that method's docstring) — getting
# an entry here wrong means silently giving up on a recoverable article
# forever.
#
#   feedproxy.google.com — Google's FeedBurner proxy, retired ~2018-2019.
#     Confirmed via a live request (Phase 5b follow-up investigation):
#     returns Google's own genuine "Error 404 (Not Found)!!1" page, not a
#     WAF block or a redirect — the service itself is gone. A feedproxy URL
#     was only ever a redirect shim to the real article elsewhere; once the
#     shim is gone, the original URL is unrecoverable through it (Wayback
#     included — see backfill_article_content's early-exit below).
_DEFUNCT_SERVICE_DOMAINS = frozenset({
    "feedproxy.google.com",
})


def _defunct_service_domain(url: str) -> str:
    """The matching entry in _DEFUNCT_SERVICE_DOMAINS for `url`'s host, or
    "" if it doesn't match one."""
    from urllib.parse import urlsplit
    host = (urlsplit(url).netloc or "").lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host if host in _DEFUNCT_SERVICE_DOMAINS else ""


# Known domain migrations — Phase 5b follow-up #2. UNLIKE
# _DEFUNCT_SERVICE_DOMAINS above, a source domain here is NOT considered
# permanently dead; it's simply known to have moved wholesale to a new host,
# so its articles are worth trying to relocate on the new domain (via Exa,
# see linklib.domain_migration) before falling back to Wayback. Deliberately
# a short, hand-curated map — same discipline as _DEFUNCT_SERVICE_DOMAINS:
# each entry requires a live confirmation, not a hunch, before being added,
# since a wrong mapping would have this tool confidently attach a WRONG
# article's content to a saved link.
#
#   pointsandfigures.com -> jeffreycarter.substack.com
#     Confirmed live (Phase 5b follow-up #2 investigation): Jeff Carter's
#     "Points and Figures" blog relaunched on Substack under this domain;
#     the old domain is Cloudflare-blocked for this tool's fetches (2 of the
#     archive's 20 pointsandfigures.com articles already have a logged
#     failure — consistent with that).
#   avc.com -> avc.xyz
#     Confirmed live (same investigation): Fred Wilson's "AVC" blog moved to
#     this domain. Unlike the mapping above, none of the archive's 55
#     avc.com articles have a logged failure yet as of this writing (most
#     haven't been attempted in a batch since backfill reliability work
#     began) — the domain move itself is still independently confirmed by
#     direct observation, just not yet exercised against a real failing
#     article in this codebase.
_DOMAIN_MIGRATIONS: dict[str, str] = {
    "pointsandfigures.com": "jeffreycarter.substack.com",
    "avc.com": "avc.xyz",
}


def _domain_migration_target(url: str) -> str:
    """The new domain _DOMAIN_MIGRATIONS maps `url`'s host to, or "" if it
    doesn't match a known migration."""
    from urllib.parse import urlsplit
    host = (urlsplit(url).netloc or "").lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return _DOMAIN_MIGRATIONS.get(host, "")


def _try_domain_migration(lib: Library, new_domain: str, title: str,
                           original_url: str) -> tuple[bool, str, str]:
    """Attempts the domain-migration tier for one article: find a candidate
    on `new_domain` via Exa (linklib.domain_migration.find_migrated_url),
    then fetch and sanity-check it exactly as a direct fetch or a Wayback
    snapshot would have to. Returns (ok, structured_html, migrated_url).
    Never raises — any failure at any stage (no title to search with, no Exa
    hit, the candidate fails to fetch, fails assess_extraction_quality, or
    has no extractable structure) resolves to (False, "", ""), same
    best-effort contract as the Wayback fallback."""
    from .extract import fetch_page, extract_reader_html, assess_extraction_quality
    from . import domain_migration

    if not title.strip():
        return False, "", ""
    candidate_url = domain_migration.find_migrated_url(lib, new_domain, title)
    if not candidate_url:
        return False, "", ""

    try:
        page = fetch_page(candidate_url)
    except Exception:
        return False, "", ""
    if not page.raw_html:
        return False, "", ""

    ok, _reason = assess_extraction_quality(page.raw_html, page.content, page.blocked)
    if not ok:
        return False, "", ""

    structured = extract_reader_html(page.raw_html, candidate_url)
    if not structured:
        return False, "", ""
    return True, structured, candidate_url


def _finish_backfill_after_direct_failure(lib: Library, article: dict,
                                           direct_reason: str, direct_detail: str
                                           ) -> tuple[bool, str]:
    """Called only once a direct fetch has already failed — the shared exit
    point for all of backfill_article_content's failure branches (Phase 5b
    follow-up #2). Tries the domain-migration tier first (only if the
    article's URL host is a known migration AND the article has a title to
    search with), then falls through to the pre-existing Wayback fallback on
    ANY migration-tier miss.

    Preserves the "exactly one content_refetch_log row per
    backfill_article_content() call" invariant: a migration-tier SUCCESS
    logs its own single success row (source='migration') and returns
    immediately without ever calling _finish_backfill_via_wayback; a
    migration-tier miss (no match, wrong domain, or the candidate failed its
    own sanity check) logs NOTHING here and simply delegates to
    _finish_backfill_via_wayback, which does its own single log — so there's
    never a double-log, whichever tier ultimately succeeds or fails."""
    article_id = article["id"]
    url = article["url"]

    migration_domain = _domain_migration_target(url)
    if migration_domain:
        title = article.get("title") or ""
        ok, structured, migrated_url = _try_domain_migration(lib, migration_domain, title, url)
        if ok:
            lib.set_article_content_html(article_id, structured)
            lib.log_content_refetch_attempt(article_id, "success",
                                            source="migration", detail=migrated_url)
            return True, ""

    return _finish_backfill_via_wayback(lib, article_id, url, direct_reason, direct_detail)


def backfill_article_content(lib: Library, article: dict) -> tuple[bool, str]:
    """Re-fetch one already-saved article and, if the fetch produced real
    structured content, store it as `content_html` — the Phase 5b backfill's
    per-article glue (fetch via extract.py, store via db.py; same "glue
    lives in pipeline.py" convention as ingest_url above).

    Returns (ok, reason). `ok=True` on a real, sanity-checked success (a row
    is stored, no log-worthy detail beyond the durable content_refetch_log
    row this function also writes). `ok=False, reason='fetch-error'` when
    the HTTP fetch itself failed outright; `ok=False, reason in
    {'paywall','bot-challenge','too-thin'}` when the fetch "succeeded" but
    extract.assess_extraction_quality() judged the result unusable;
    `ok=False, reason='defunct-service'` when the URL's host is a known
    permanently-discontinued service (see _DEFUNCT_SERVICE_DOMAINS) — no
    fetch or Wayback attempt is made at all in that case, since neither can
    ever succeed and both would just spend a request (Wayback's especially
    scarce given its own rate limiting) on something already known
    unrecoverable. Every call — success or failure — writes one
    content_refetch_log row.

    On a direct-fetch failure of any OTHER kind above, first tries the
    known-domain-migration tier (Phase 5b follow-up #2 — see
    _DOMAIN_MIGRATIONS' comment and linklib.domain_migration) if the URL's
    host is a confirmed migrated domain, then the Wayback Machine as a last
    resort before giving up (see linklib.wayback's module docstring —
    deliberately not scoped to 404 only, since a stubborn bot-block a direct
    fetch can't get past may still have a usable archived snapshot). Both
    fallbacks funnel through _finish_backfill_after_direct_failure, which
    guarantees exactly one content_refetch_log row is written no matter
    which tier (direct, migration, or wayback) ultimately succeeds or all
    three fail. A migration-sourced success is logged with source='migration',
    a Wayback-sourced success with source='wayback' — both distinguishable
    from a normal direct fetch (linklib.wayback's docstring covers why the
    Wayback fallback's real-world reliability is unverified at the time it
    was built).

    Never destructive: a failure — even after the Wayback fallback is also
    exhausted — never touches articles.content or articles.content_html, so
    a bad re-fetch can't erase what a good ingest (or an earlier successful
    backfill run) already stored.
    """
    from .extract import fetch_page, extract_reader_html, assess_extraction_quality

    article_id = article["id"]
    url = article["url"]

    defunct_domain = _defunct_service_domain(url)
    if defunct_domain:
        lib.log_content_refetch_attempt(
            article_id, "failure", reason="defunct-service",
            detail=f"{defunct_domain} is a discontinued service", source="direct")
        return False, "defunct-service"

    try:
        page = fetch_page(url)
    except Exception as exc:
        return _finish_backfill_after_direct_failure(lib, article, "fetch-error", str(exc)[:500])

    if not page.raw_html:
        # fetch_page swallows its own request/HTTP errors and returns an
        # empty PageData rather than raising — this is that case.
        # page.fetch_error carries the specific reason (a status code, a
        # timeout, a connection error) so a batch of failures can be told
        # apart: independent dead links vs. one host systematically
        # blocking/throttling this tool — see PageData.fetch_error.
        return _finish_backfill_after_direct_failure(lib, article, "fetch-error", page.fetch_error)

    ok, reason = assess_extraction_quality(page.raw_html, page.content, page.blocked)
    if not ok:
        return _finish_backfill_after_direct_failure(lib, article, reason, "")

    structured = extract_reader_html(page.raw_html, url)
    if not structured:
        # Passed the content sanity check on plain text, but the structured
        # extractor itself came back empty (e.g. no <article>/<body> the
        # extractor recognizes) — nothing usable to store.
        return _finish_backfill_after_direct_failure(lib, article, "too-thin", "")

    lib.set_article_content_html(article_id, structured)
    lib.log_content_refetch_attempt(article_id, "success", source="direct")
    return True, ""


def _finish_backfill_via_wayback(lib: Library, article_id: int, url: str,
                                  direct_reason: str, direct_detail: str) -> tuple[bool, str]:
    """Called only once a direct fetch has already failed for
    `direct_reason` — tries a Wayback Machine snapshot as a last resort
    (linklib.wayback), reusing the exact same sanity-check/structured-
    extraction logic a direct fetch goes through (via
    extract._page_data_from_html), so a Wayback snapshot has to clear the
    identical bar a live page would.

    If Wayback comes up empty at ANY stage (no snapshot, the snapshot itself
    fails to fetch, or it fails the same sanity check a live page would —
    e.g. an archived paywall preview), this logs and returns the ORIGINAL
    `direct_reason` — that's still the true diagnostic signal for this
    article, not a synthetic "wayback also failed" reason, so the admin
    failure-reason breakdown stays meaningful and comparable to a
    pre-Wayback run. The logged `detail` DOES append what Wayback itself
    returned (e.g. "HTTP 403 (wayback: connection error: ...)") — a real
    production batch needed a `railway ssh` round-trip to answer "was
    Wayback even attempted, and what happened" before this existed; now
    that's answered by the log directly. Never destructive at any stage.
    """
    from .extract import extract_reader_html, assess_extraction_quality, _page_data_from_html
    from . import wayback

    snap_url, wb_note = wayback.find_snapshot_verbose(url)
    if snap_url:
        snap_html, fetch_note = wayback.fetch_snapshot_verbose(snap_url)
        if snap_html:
            page = _page_data_from_html(snap_html)
            ok, sanity_reason = assess_extraction_quality(page.raw_html, page.content, page.blocked)
            if ok:
                structured = extract_reader_html(snap_html, snap_url)
                if structured:
                    lib.set_article_content_html(article_id, structured)
                    lib.log_content_refetch_attempt(article_id, "success",
                                                    source="wayback", detail=snap_url)
                    return True, ""
                wb_note = "snapshot fetched but had no extractable structure"
            else:
                wb_note = f"snapshot failed its own sanity check ({sanity_reason})"
        else:
            wb_note = f"snapshot found but fetch failed: {fetch_note}" if fetch_note else "snapshot found but fetch failed"

    combined_detail = f"{direct_detail} (wayback: {wb_note})" if direct_detail else f"wayback: {wb_note}"
    lib.log_content_refetch_attempt(article_id, "failure", reason=direct_reason,
                                    detail=combined_detail, source="direct")
    return False, direct_reason


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
