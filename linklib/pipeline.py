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

    Durability audit item 1: when a real fetch happens (fetch_fulltext=True),
    the result is run through extract.assess_extraction_quality() before it's
    stored. A bad result (paywall preview, bot-challenge interstitial, a
    fetch that failed outright, or real content under _MIN_CONTENT_WORDS)
    NEVER blocks or rejects the save — it's still stored exactly as before —
    but the article is flagged via Library.set_content_check_flag with the
    failure reason, and a content_refetch_log row (source='save') is written
    so the article participates in the same manual-review/backfill-scope
    machinery a failed Reader-backfill attempt would. The flag clears the
    moment content_html is later populated for real (set_article_content_html).

    Bookmarklet title-extraction follow-up (2026-08): a direct fetch that
    fails outright or fails the quality check against a recognized
    Cloudflare-blocked host (medium.com and friends — see
    linklib.medium_platform.is_recognized_blocked_host) never had a real
    <title>/og:title/twitter:title or a real content pull to work from
    either way — confirmed live via scripts/trace_medium_tier.py against a
    real article that had been falling back to storing its own URL as the
    title. In that specific case only, medium_recovery() is tried — the
    same Exa-based recovery tier the Reader content backfill already uses,
    reused as-is (see medium_recovery's own docstring). A hit replaces the
    failed title/content with the recovered ones and stores the recovered
    structured HTML via set_article_content_html, same as a successful
    backfill run would; a miss (host not recognized, no EXA_API_KEY, Exa
    disabled, or the tier itself came up empty) changes nothing — the
    existing fetch-failure handling below runs exactly as it always has.
    """
    from .extract import fetch_page, assess_extraction_quality

    page_title = ""
    content = ""
    content_html = ""
    medium_source = ""
    medium_exa_cost = 0.0
    quality_checked = False
    quality_ok = True
    quality_reason = ""
    if fetch_fulltext:
        page = fetch_page(url)
        page_title = page.title
        content = page.content
        # Durability audit item 1: run the same sanity check the Reader
        # content backfill uses, right here at save time, instead of storing
        # whatever came back with no judgment at all. A failed fetch (no
        # HTML at all) is judged directly from PageData.fetch_error rather
        # than assess_extraction_quality — which assumes a fetch that
        # actually returned something and would otherwise mislabel a
        # connection failure as "too-thin". Never blocks or rejects the
        # save either way — see set_content_check_flag below.
        quality_checked = True
        if page.fetch_error:
            quality_ok, quality_reason = False, "fetch-error"
        else:
            quality_ok, quality_reason = assess_extraction_quality(
                page.raw_html, content, page.blocked)

        if not quality_ok:
            recovered, medium_exa_cost = medium_recovery(lib, url, page_title)
            if recovered:
                page_title = recovered["title"] or page_title
                content = recovered["content"]
                content_html = recovered["content_html"]
                medium_source = recovered["source"]
                quality_ok, quality_reason = True, ""

    art = Article(
        url=url,
        title=page_title or url,
        content=content,
        notes=notes or "",
        tags=tags or [],
        saved_at=datetime.now(timezone.utc).isoformat(),
    )
    article_id = lib.upsert(art)

    # Published-content provenance (2026-09): a save whose URL matches one of
    # Brian's own externally-hosted pieces (thought_leadership.url — e.g. the
    # bookmarklet path for the ~9 text-fetchable pieces) gets flagged the
    # same way the 3 native original_content mirrors are — a citation-label
    # signal only, never a ranking one (see linklib.agent._build_source_documents
    # /_rrf_merge; nothing in retrieval reads this flag). Set-only, matching
    # every other durable-fact flag in this codebase: a match here is never
    # cleared by a later resave.
    if lib.is_thought_leadership_url(art.url):
        lib.set_article_own_content(article_id, True)

    if content_html:
        lib.set_article_content_html(article_id, content_html)

    if quality_checked:
        # upsert() is write-once for content (existing["content"] or
        # art.content — see tests/test_content_downgrade_guard.py): a resave
        # of an article that already had good content keeps that content
        # even when THIS fetch came back bad. Flagging off this fetch's
        # verdict would then wrongly mark a perfectly fine article as
        # needing a content check. Only trust the verdict when the fetch we
        # just judged is actually what ended up stored.
        stored = lib.get_article(article_id)
        if stored is not None and stored.get("content") == content:
            lib.set_content_check_flag(article_id, not quality_ok, quality_reason)
            if not quality_ok:
                lib.log_content_refetch_attempt(article_id, "failure",
                                                reason=quality_reason, source="save",
                                                exa_cost_usd=medium_exa_cost)
            elif medium_source:
                lib.log_content_refetch_attempt(article_id, "success",
                                                source=medium_source, detail=url,
                                                exa_cost_usd=medium_exa_cost)

    if do_enrich:
        from . import tagstyle
        result = enrich_mod.enrich(art.title, content or art.title,
                                   known_tags=lib.known_tags(),
                                   tag_guide=tagstyle.effective_tag_guidance(lib),
                                   model=lib.get_enrich_model())
        if result:
            lib.apply_enrichment(article_id, result.summary, result.tags,
                                 model=result.model, rules=result.rules_version)
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
                                 model=result.model, rules=result.rules_version)
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
#   karenroterdavis.com -> karenroterdavis.wordpress.com
#     Reported by Brian (2026-08): karenroterdavis.com has moved to
#     karenroterdavis.wordpress.com. Unlike the two entries above, this
#     session couldn't independently live-verify it the same way — the old
#     domain didn't resolve at all from this session's network (consistent
#     with having moved off its own hosting), and the new domain was
#     blocked outright by this session's egress proxy, so neither side
#     could be fetched and inspected directly. Added on Brian's reported
#     fact, not a live check performed in this session — flagged here per
#     this dict's own "each entry requires a live confirmation, not a
#     hunch" discipline, since that confirmation wasn't done the usual way
#     this time.
#   calacanis.com -> calacanis.substack.com
#   newageaccounting.ai -> substack.newageaccounting.ai
#     Reported by Brian (2026-08, Reader backfill failure-cluster cleanup):
#     both blogs moved to Substack under these domains. Same caveat as
#     karenroterdavis.com above — this session's outbound network is
#     entirely egress-blocked (confirmed against unrelated, definitely-live
#     hosts, not just these two), so neither the old nor the new domain
#     could be fetched and inspected directly here. Added on Brian's
#     reported fact, not a live check performed in this session.
_DOMAIN_MIGRATIONS: dict[str, str] = {
    "pointsandfigures.com": "jeffreycarter.substack.com",
    "avc.com": "avc.xyz",
    "karenroterdavis.com": "karenroterdavis.wordpress.com",
    "calacanis.com": "calacanis.substack.com",
    "newageaccounting.ai": "substack.newageaccounting.ai",
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
                           original_url: str) -> tuple[bool, str, str, float]:
    """Attempts the domain-migration tier for one article: find a candidate
    on `new_domain` via Exa (linklib.domain_migration.find_migrated_url),
    then fetch and sanity-check it exactly as a direct fetch or a Wayback
    snapshot would have to. Returns (ok, structured_html, migrated_url,
    exa_cost_usd) — exa_cost_usd (2026-09, Exa cost-tracking foundation) is
    whatever find_migrated_url's own Exa call cost, win or miss (0.0 if it
    was never reached at all). Never raises — any failure at any stage (no
    title to search with, no Exa hit, the candidate fails to fetch, fails
    assess_extraction_quality, or has no extractable structure) resolves to
    (False, "", "", cost), same best-effort contract as the Wayback
    fallback."""
    from .extract import fetch_page, extract_reader_html, assess_extraction_quality
    from . import domain_migration

    if not title.strip():
        return False, "", "", 0.0
    candidate_url, cost = domain_migration.find_migrated_url(lib, new_domain, title)
    if not candidate_url:
        return False, "", "", cost

    try:
        page = fetch_page(candidate_url)
    except Exception:
        return False, "", "", cost
    if not page.raw_html:
        return False, "", "", cost

    ok, _reason = assess_extraction_quality(page.raw_html, page.content, page.blocked)
    if not ok:
        return False, "", "", cost

    structured = extract_reader_html(page.raw_html, candidate_url)
    if not structured:
        return False, "", "", cost
    return True, structured, candidate_url, cost


def _try_medium_platform(lib: Library, title: str, author: str,
                          original_url: str) -> tuple[bool, str, str, str, str, float]:
    """Attempts the Medium-platform tier for one article.

    Tries, in order:

    1. **Fetch-by-URL** (2026-08 wrap-up sprint item 1): a direct Exa
       `/contents` fetch of the article's own current URL
       (linklib.medium_platform.fetch_content_by_url) — original_url is
       already a recognized blocked host by the time this function is
       called (the caller gates on is_recognized_blocked_host), and it may
       be a manually-corrected, human-confirmed URL (see CLAUDE.md's
       manual-review bullets), so there's no candidate-disambiguation
       problem the way a title search has: if Exa returns substantive text
       for that exact URL, it's accepted on the word-count floor alone
       (extract._MIN_CONTENT_WORDS) — no title-match check, since there's
       no candidate to match against, just the one true URL. A miss or a
       too-thin result here falls through to search-by-title, unchanged.
    2. **Search-by-title** (the original tier): find a candidate via Exa
       (linklib.medium_platform.find_medium_candidate), then validate it
       one of two ways depending on where the candidate itself resolved to
       (mirrors scripts/medium_platform_scale_check.py's `_spike_one_article`
       validation split, the diagnostic that found this distinction
       necessary):

       - Candidate resolved to some OTHER host: live re-fetch it and run the
         exact same extract_reader_html + assess_extraction_quality gate a
         direct fetch or a domain-migration candidate has to clear.
       - Candidate resolved back onto a recognized blocked host itself
         (Medium-platform or otherwise — see
         medium_platform.is_recognized_blocked_host): a live re-fetch
         would just re-hit the same block the ORIGINAL url already failed
         on, not a real test of the candidate — validate against Exa's own
         already-returned text instead (the same word-count floor), then
         convert that plain text into Reader-consistent HTML via
         extract.paragraphs_html_from_text — the same Medium-navigation-
         chrome strip and structure normalization used wherever else this
         tier's Exa text lands, so the Reader reads identically to any
         other source (see extract.py's module comment on that function
         for the "consistent house reading experience" principle).

    Returns (ok, structured_html, candidate_url, source, note, exa_cost_usd)
    — source is 'medium-fetch' for a fetch-by-URL success, 'medium-search'
    for a search-by-title success (distinguishable in content_refetch_log —
    see _finish_backfill_after_direct_failure), or '' on failure. `note` is a
    short diagnostic trace of what this call actually attempted and why it
    didn't return a hit — ALWAYS populated, success or failure, so a caller
    (and, via _finish_backfill_after_direct_failure, the eventual
    content_refetch_log row) can tell "this tier ran and missed" apart from
    "this tier was never reached" (2026-08 wrap-up sprint follow-up: two
    live production traces came back with a log signature indistinguishable
    from the pre-fetch-by-URL flow, and there was no way to confirm from the
    log alone whether the new code path had actually executed). exa_cost_usd
    (2026-09, Exa cost-tracking foundation) is the SUM of both Exa calls this
    attempt made — fetch-by-URL always runs first and its cost always counts
    even when it's a miss that falls through; search-by-title's cost is
    added on top only if that step is actually reached. Never raises — any
    failure at any stage resolves to (False, "", "", "", note, cost), same
    best-effort contract as _try_domain_migration."""
    from .extract import (fetch_page, extract_reader_html, assess_extraction_quality,
                           paragraphs_html_from_text, _MIN_CONTENT_WORDS)
    from . import medium_platform

    direct_text, cost = medium_platform.fetch_content_by_url(lib, original_url)
    if direct_text:
        text = direct_text.strip()
        word_count = len(text.split())
        if word_count >= _MIN_CONTENT_WORDS:
            structured = paragraphs_html_from_text(text)
            if structured:
                return True, structured, original_url, "medium-fetch", "fetch-by-url: hit", cost
            fetch_note = f"fetch-by-url: got {word_count} words but no extractable structure after chrome-strip"
        else:
            fetch_note = f"fetch-by-url: too-thin ({word_count} words)"
        # Thin or unusable direct-fetch result — fall through to
        # search-by-title rather than giving up outright.
    else:
        fetch_note = "fetch-by-url: no result from Exa"

    if not title.strip():
        return False, "", "", "", f"{fetch_note}; search-by-title: skipped (no title)", cost
    candidate_url, candidate_text, search_cost = medium_platform.find_medium_candidate(lib, title, author)
    cost += search_cost
    if not candidate_url:
        return False, "", "", "", f"{fetch_note}; search-by-title: no candidate", cost

    if medium_platform.is_recognized_blocked_host(candidate_url):
        text = (candidate_text or "").strip()
        if len(text.split()) < _MIN_CONTENT_WORDS:
            return False, "", "", "", f"{fetch_note}; search-by-title: candidate too-thin (exa-text, {candidate_url})", cost
        structured = paragraphs_html_from_text(text)
        if not structured:
            return False, "", "", "", f"{fetch_note}; search-by-title: candidate had no extractable structure (exa-text, {candidate_url})", cost
        return True, structured, candidate_url, "medium-search", f"{fetch_note}; search-by-title: hit (exa-text, {candidate_url})", cost

    try:
        page = fetch_page(candidate_url)
    except Exception as exc:
        return False, "", "", "", f"{fetch_note}; search-by-title: candidate fetch raised {type(exc).__name__} ({candidate_url})", cost
    if not page.raw_html:
        return False, "", "", "", f"{fetch_note}; search-by-title: candidate fetch returned no content ({candidate_url})", cost

    ok, reason = assess_extraction_quality(page.raw_html, page.content, page.blocked)
    if not ok:
        return False, "", "", "", f"{fetch_note}; search-by-title: candidate failed quality check ({reason}, {candidate_url})", cost

    structured = extract_reader_html(page.raw_html, candidate_url)
    if not structured:
        return False, "", "", "", f"{fetch_note}; search-by-title: candidate had no extractable structure ({candidate_url})", cost
    return True, structured, candidate_url, "medium-search", f"{fetch_note}; search-by-title: hit (live-refetch, {candidate_url})", cost


def _first_heading_text(html: str) -> str:
    """Best-effort title recovered from Medium-tier structured HTML.
    _try_medium_platform has no separate title field to hand back — Exa's
    contents.text is plain body text — but paragraphs_html_from_text
    classifies a short, Title-Case, punctuation-free leading line as a
    heading (see that function's own docstring: "Medium's own in-article
    section headers arrive this way"), and the article's own title is
    almost always exactly that shape at the very top of the extracted
    text, past the point the leading-chrome strip stopped. Returns the
    first h1/h2/h3's text found, or "" if none — callers keep their own
    existing title fallback in that case. Deliberately doesn't strip the
    heading back out of the body: CLAUDE.md's Medium-platform tier notes
    already flag occasional title/byline duplication inside a candidate's
    own text as a known, accepted gap, not something this adds."""
    from bs4 import BeautifulSoup
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return ""
    tag = soup.find(["h1", "h2", "h3"])
    if tag:
        text = tag.get_text(strip=True)
        if text:
            return text
    return ""


def _plain_text_from_structured_html(html: str) -> str:
    """Plain-text rendering of Medium-tier structured HTML, for the
    ingest/search/enrichment pipeline's plain-text contract (see
    extract._extract_content's own docstring on why `articles.content`
    must stay plain text, never raw HTML). Block-joined the same way
    extract._extract_content's own BS4 fallback path is, so paragraph
    breaks survive as blank lines rather than collapsing into one line."""
    from bs4 import BeautifulSoup
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return ""
    blocks = [el.get_text(" ", strip=True)
              for el in soup.find_all(["p", "h1", "h2", "h3", "li", "blockquote"])]
    blocks = [b for b in blocks if b]
    return "\n\n".join(blocks) if blocks else soup.get_text(" ", strip=True)


def medium_recovery(lib: Library, url: str, title: str = "", author: str = "") -> tuple[Optional[dict], float]:
    """Bookmarklet/Reader title-and-content-extraction follow-up (2026-08):
    a direct fetch of a recognized Cloudflare-blocked host (medium.com and
    friends — see linklib.medium_platform.is_recognized_blocked_host) never
    gets real HTML back at all, so neither a <title>/og:title/twitter:title
    extraction (extract._extract_title) nor a plain-text content pull has
    anything real to work from — confirmed live against a real stuck
    article via scripts/trace_medium_tier.py (medium-fetch, real structured
    content pulled for the exact URL that had been falling back to storing
    its own URL as the title).

    Wraps the SAME Exa-based recovery tier the Reader content backfill
    already uses for exactly this class of host (_try_medium_platform,
    reused as-is — no changes to it or to linklib/medium_platform.py) so a
    live save (linklib.pipeline.ingest_url) or a live Reader read
    (webapp._resolve_reader_content) can recover too, not just the offline
    backfill job. Gates on is_recognized_blocked_host itself (mirrors
    _finish_backfill_after_direct_failure's own gating) so a non-blocked
    host never spends an Exa call here — a caller doesn't need to
    duplicate that check first.

    Returns (result, exa_cost_usd). result is None on any miss: host not
    recognized, no EXA_API_KEY, Exa disabled via the admin toggle, or the
    tier itself came up empty — never raises, same best-effort contract as
    _try_medium_platform. On a hit, result is a dict with:
      - content_html: the tier's structured Reader HTML
      - content: a plain-text rendering of the same HTML (see
        _plain_text_from_structured_html) — NOT the raw Exa text, so a
        caller storing this into articles.content gets the same
        Medium-chrome-stripped, structure-normalized text the Reader
        itself would show, not an unprocessed dump.
      - title: a best-effort title from the structured HTML's own first
        heading (see _first_heading_text) — "" if none found.
      - candidate_url, source: passed through from _try_medium_platform
        ('medium-fetch' | 'medium-search'), for a caller's own
        content_refetch_log row or UI display.
    exa_cost_usd (2026-09, Exa cost-tracking foundation) is
    _try_medium_platform's own accumulated cost — real even on a miss
    (result is None), so a caller that logs to content_refetch_log on
    either outcome can record what was actually spent trying. 0.0 when the
    host isn't recognized at all (Exa was never reached).
    """
    from . import medium_platform
    if not medium_platform.is_recognized_blocked_host(url):
        return None, 0.0
    ok, structured, candidate_url, source, _note, cost = _try_medium_platform(lib, title, author, url)
    if not ok:
        return None, cost
    return {
        "content_html": structured,
        "content": _plain_text_from_structured_html(structured),
        "title": _first_heading_text(structured),
        "candidate_url": candidate_url,
        "source": source,
    }, cost


def _finish_backfill_after_direct_failure(lib: Library, article: dict,
                                           direct_reason: str, direct_detail: str
                                           ) -> tuple[bool, str]:
    """Called only once a direct fetch has already failed — the shared exit
    point for all of backfill_article_content's failure branches (Phase 5b
    follow-up #2). Tries the domain-migration tier first (only if the
    article's URL host is a known migration AND the article has a title to
    search with), then the Medium-platform tier (only if the URL's host is
    recognized by linklib.medium_platform.is_recognized_blocked_host —
    Medium-platform hosts plus any other confirmed Exa-recoverable blocked
    host, see that function's docstring), then falls through to the
    pre-existing Wayback fallback on ANY miss from either of those.

    Preserves the "exactly one content_refetch_log row per
    backfill_article_content() call" invariant: a migration-tier or
    Medium-platform-tier SUCCESS logs its own single success row
    (source='migration', or source='medium-fetch'/'medium-search' — see
    _try_medium_platform) and returns immediately without ever calling
    _finish_backfill_via_wayback; a miss from either tier (no match, or the
    candidate failed its own sanity check) logs NOTHING here and simply
    falls through — to the Medium tier, then to Wayback — each of which
    does its own single log, so there's never a double-log, whichever tier
    ultimately succeeds or all fail. Deliberately Medium-platform-tier-
    before-Wayback, not the reverse: the Wayback fallback is currently
    unreliable due to archive.org-side rate-limiting (see linklib.wayback's
    module docstring), while the Medium tier's hit rate on real diagnostic
    data was strong enough to try first (see linklib/medium_platform.py's
    module docstring).

    **Tier-attempt trace (2026-08 wrap-up sprint follow-up):** a miss from
    every tier used to fall through to Wayback with NO record that any tier
    other than Wayback itself was ever tried — a live production trace could
    not tell "the Medium tier ran and missed" apart from "the Medium tier
    was never reached" just from the logged content_refetch_log row, since
    both look identical (source='direct', the original direct_reason, only
    Wayback's own outcome appended to detail). Fixed by accumulating a
    `tier_notes` list of what each attempted-and-missed tier actually did
    (never populated for a tier that wasn't reached at all — host not
    recognized, no migration match, etc.) and passing it through to
    _finish_backfill_via_wayback, which appends it to the final logged
    detail regardless of the Wayback outcome. An article whose host isn't
    recognized by any tier logs identically to before this fix (empty
    trace, no detail change) — this only adds information when a tier
    genuinely ran. exa_cost_usd (2026-09, Exa cost-tracking foundation)
    accumulates across every tier actually attempted during this one call —
    a miss's cost isn't lost just because a later tier goes on to succeed
    (or because everything ultimately falls through to Wayback, which
    itself has no Exa cost) — and lands on whichever single
    content_refetch_log row this call ultimately writes, preserving the
    same one-row-per-call invariant described above."""
    article_id = article["id"]
    url = article["url"]
    tier_notes: list[str] = []
    tier_exa_cost = 0.0

    migration_domain = _domain_migration_target(url)
    if migration_domain:
        title = article.get("title") or ""
        ok, structured, migrated_url, cost = _try_domain_migration(lib, migration_domain, title, url)
        tier_exa_cost += cost
        if ok:
            lib.set_article_content_html(article_id, structured)
            lib.log_content_refetch_attempt(article_id, "success",
                                            source="migration", detail=migrated_url,
                                            exa_cost_usd=tier_exa_cost)
            return True, ""
        tier_notes.append(f"migration({migration_domain}): no usable candidate")

    from . import medium_platform
    if medium_platform.is_recognized_blocked_host(url):
        title = article.get("title") or ""
        author = article.get("author") or ""
        ok, structured, candidate_url, source, tier_note, cost = _try_medium_platform(lib, title, author, url)
        tier_exa_cost += cost
        if ok:
            lib.set_article_content_html(article_id, structured)
            lib.log_content_refetch_attempt(article_id, "success",
                                            source=source, detail=candidate_url,
                                            exa_cost_usd=tier_exa_cost)
            return True, ""
        tier_notes.append(f"medium[{tier_note}]")

    tier_trace = "; ".join(tier_notes)
    return _finish_backfill_via_wayback(lib, article_id, url, direct_reason, direct_detail,
                                        tier_trace, tier_exa_cost)


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
    host is a confirmed migrated domain, then the Medium-platform tier (see
    linklib/medium_platform.py) if the URL's host is a recognized blocked
    host (Medium-platform, or another confirmed-blocked host like
    shockwaveinnovations.com — is_recognized_blocked_host), then the
    Wayback Machine as a last resort before giving up (see linklib.wayback's
    module docstring — deliberately not scoped to 404 only, since a
    stubborn bot-block a direct fetch can't get past may still have a
    usable archived snapshot). All three fallbacks funnel through
    _finish_backfill_after_direct_failure, which guarantees exactly one
    content_refetch_log row is written no matter which tier (direct,
    migration, medium-fetch, medium-search, or wayback) ultimately succeeds
    or all fail. A migration-sourced success is logged with
    source='migration'; a Medium-platform-tier success is logged
    source='medium-fetch' when a direct Exa fetch of the article's own
    exact URL succeeded, or source='medium-search' when it took a
    search-by-title match instead (see _try_medium_platform); a
    Wayback-sourced success is logged source='wayback' — all distinguishable
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
                                  direct_reason: str, direct_detail: str,
                                  tier_trace: str = "",
                                  tier_exa_cost: float = 0.0) -> tuple[bool, str]:
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

    `tier_trace` (2026-08 wrap-up sprint follow-up) is whatever
    _finish_backfill_after_direct_failure accumulated about the
    migration/Medium tiers it tried before falling through here — appended
    to `detail` in square brackets, regardless of what Wayback itself does,
    so the final logged row answers "which tiers actually ran" directly
    instead of looking identical to a run where they were never reached.
    Empty (the default) when no other tier applies to this URL at all —
    that case's logged detail is byte-for-byte unchanged from before this
    parameter existed.

    `tier_exa_cost` (2026-09, Exa cost-tracking foundation) is whatever the
    migration/Medium tiers already spent before falling through here — 0.0
    when neither tier was reached. Carried onto whichever single row this
    call logs (a Wayback success, or the final failure), regardless of the
    fact that Wayback itself never spends an Exa call.
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
                                                    source="wayback", detail=snap_url,
                                                    exa_cost_usd=tier_exa_cost)
                    return True, ""
                wb_note = "snapshot fetched but had no extractable structure"
            else:
                wb_note = f"snapshot failed its own sanity check ({sanity_reason})"
        else:
            wb_note = f"snapshot found but fetch failed: {fetch_note}" if fetch_note else "snapshot found but fetch failed"

    combined_detail = f"{direct_detail} (wayback: {wb_note})" if direct_detail else f"wayback: {wb_note}"
    if tier_trace:
        combined_detail = f"{combined_detail} [{tier_trace}]"
    lib.log_content_refetch_attempt(article_id, "failure", reason=direct_reason,
                                    detail=combined_detail, source="direct",
                                    exa_cost_usd=tier_exa_cost)
    return False, direct_reason


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
