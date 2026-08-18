#!/usr/bin/env python3
"""Manual QA / one-off diagnostic — Medium-platform fetch-failure spike.

A Phase 0 investigation (see CLAUDE.md / the "Reader content backfill"
admin page) found that `bothsidesofthetable.com`, `medium.com`, and
`link.medium.com` failures in the Reader content backfill's "needs manual
review" queue are almost certainly the same underlying platform
(Medium, behind a TLS-fingerprint-level Cloudflare block that no
header/UA swap gets past), currently unrecognized as related by the
fetch pipeline. Before scoping a real Exa-based fetch tier for these
(mirroring the existing `pointsandfigures.com`/`avc.com` domain-migration
tier — see `linklib/domain_migration.py`), this answers two questions:

1. Scale — how many saved articles actually live on one of these three
   domains, across the WHOLE library (not just the ~25 articles the
   backfill has processed so far)? And of those, how many already have
   `content_refetch_log` history vs. haven't been touched yet?
2. Feasibility — for the articles currently sitting in the manual-review
   queue on one of these domains, can Exa actually find and retrieve
   real article content for them at all?

Read-only. Writes nothing to the database, calls no pipeline code, wires
into nothing — this is a standalone report, same "manual-QA tool, not an
automated pass/fail eval" shape as scripts/eval_retrieval.py and
scripts/enrich_compare.py. Meant to be run by hand (e.g. via
`railway ssh`) against production and its output read/pasted back for
review, not part of any recurring job.

Usage:
    python -m scripts.medium_platform_scale_check --db /data/library.db
    python -m scripts.medium_platform_scale_check --db /data/library.db --skip-exa
    python -m scripts.medium_platform_scale_check --db /data/library.db --json
    python -m scripts.medium_platform_scale_check --db /data/library.db --list-empties

--skip-exa runs Tasks 1/3/4 (no Exa calls needed) and skips Task 2. Task 2
(the Exa spike) needs EXA_API_KEY set in the environment, same as the app
itself. --list-empties additionally prints a CSV of every empty-bucket
article found library-wide by Task 4 (see below) — listing only, never
writes or flags anything.

Phase 0 (Medium fetch tier build) revision #2: the first spike accepted
Exa's top-1 result blind and trusted Exa's own returned snippet text as
the content-length signal. Both were gaps in the SPIKE, not in the real
tier design — linklib.domain_migration.find_migrated_url already loops
candidates and validates via _titles_match(), and the real tier will
validate a candidate via a genuine fetch_page()+assess_extraction_quality()
pass (see linklib.pipeline._try_domain_migration), not Exa's own text
field. This revision closed both gaps: every candidate is title-matched
(same _titles_match(), reused not reimplemented) before acceptance, and
an accepted candidate is re-fetched live and run through the real
quality check before its content length is reported. Also added a small
sample of `link.medium.com` articles regardless of manual-review status
(that domain has zero manual-review articles yet, so the first spike
never tested it at all) — labeled separately since these are UNTESTED
articles, not known failures.

Revision #3: the corrected run found a real recovery rate far below the
first spike's uncorrected 100% — expected, since that was the whole
point of revision #2 — but also surfaced a NEW gap: several
"unrecovered" results were Exa resolving an article back onto another
Medium-platform URL, which the live-refetch validation was then
guaranteed to fail on (same Cloudflare block, not a genuine test of the
candidate). Three fixes: (1) `candidate_url` is now printed on every
result line, success or failure, so a same-domain circular failure is
visible without re-running by hand; (2) a same-domain candidate now
validates against Exa's own returned `contents.text` (a plain
_MIN_CONTENT_WORDS word-count check, reused from extract.py) instead of
re-fetching — `validation_path` on each result says which path ran
('live-refetch' vs 'exa-text'); (3) a new Task 3, `content_audit()`,
reports word counts of what's ALREADY STORED for every Medium-platform
article, regardless of attempt/manual-review status — a separate
question from "can Exa find a replacement," report-only, no
flagging/deletion logic (see the standing "human review before any
destructive production action" rule).

Revision #4: Task 3's first real run found 80/147 Medium-platform
articles (54%) empty or thin. Before locking the recovery tier's design
around "Medium-platform specifically," we need to know whether that
emptiness is Medium-specific (Cloudflare blocking the original save-time
fetches — a domain-triggered recovery tier is the right shape) or
library-wide (the save mechanism itself under-captures, and recovery
should eventually generalize beyond Medium). New Task 4,
`library_wide_content_audit()`, runs the SAME bucketing logic as Task 3
— `_content_word_count()`/`_bucket_for_word_count()`, factored out of
`content_audit()` so the two reports can't drift apart — across every
article in the library (~4,516), grouped by URL host, showing the top 25
hosts by empty-article count plus a full-library summary. `--list-empties`
additionally prints a CSV of every empty-bucket article library-wide
(id/title/url/word_count) — the raw material for a later deletion
review, not acted on here. Still report-only throughout.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from urllib.parse import urlsplit

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from linklib.db import Library, resolve_db_path
from linklib.domain_migration import _titles_match
from linklib.extract import (
    fetch_page, assess_extraction_quality, extract_reader_html,
    _extract_content, _MIN_CONTENT_WORDS,
)

# Same three hosts the Phase 0 investigation confirmed are one platform.
MEDIUM_DOMAINS = frozenset({"bothsidesofthetable.com", "medium.com", "link.medium.com"})

# How many untouched link.medium.com articles to sample — that domain has
# zero manual-review articles yet (nothing has failed there so far), so
# without this it would never appear in the Exa spike at all. Small on
# purpose — this is a feasibility check, not exhaustive coverage.
_LINK_MEDIUM_SAMPLE_SIZE = 5

# Task 3 (existing-content audit) thresholds — Brian's own call, flagged
# rather than silently picked, same as every other hand-set threshold in
# this codebase (_MANUAL_REVIEW_ATTEMPT_THRESHOLD, etc.). 200 words is his
# "effectively useless as saved" line for a preview/intro-only stub; 5
# words separates "genuinely nothing saved" from "merely thin" (a handful
# of words is more likely a leaked bot-block fragment than real content).
# Audit-only — neither threshold feeds any retry/exclusion/deletion logic.
_CONTENT_AUDIT_NEAR_ZERO_WORDS = 5
_CONTENT_AUDIT_THIN_WORDS = 200

# Same endpoint/contract linklib.agent.retrieve_exa and
# linklib.domain_migration.find_migrated_url already use — nothing new here.
_EXA_SEARCH_URL = "https://api.exa.ai/search"
_EXA_TIMEOUT = 15.0


def _host(url: str) -> str:
    """Same host-normalization every other domain-matching path in this
    codebase uses (linklib.pipeline._defunct_service_domain,
    Library.content_refetch_failure_domains): lowercase, strip port, strip
    a leading www."""
    host = (urlsplit(url or "").netloc or "").lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host


# --------------------------------------------------------------------------
# Task 1 — scale check
# --------------------------------------------------------------------------

def scale_check(lib: Library) -> dict:
    """Every saved article whose URL host matches MEDIUM_DOMAINS — not
    filtered to already-attempted or manual-review rows, per the ask. Cross-
    referenced against content_refetch_log to show attempted-and-failed vs.
    never-touched, so the visible 14-from-25-processed sample can be
    compared against the real denominator."""
    rows = lib.conn.execute("SELECT id, url, title, author FROM articles WHERE url != ''").fetchall()

    by_domain: dict[str, list[dict]] = {d: [] for d in sorted(MEDIUM_DOMAINS)}
    for r in rows:
        host = _host(r["url"])
        if host in MEDIUM_DOMAINS:
            by_domain[host].append({"id": r["id"], "url": r["url"],
                                    "title": r["title"], "author": r["author"]})

    all_ids = [a["id"] for arts in by_domain.values() for a in arts]
    attempted_ids: set[int] = set()
    if all_ids:
        placeholders = ",".join("?" * len(all_ids))
        attempted_rows = lib.conn.execute(
            f"SELECT DISTINCT article_id FROM content_refetch_log WHERE article_id IN ({placeholders})",
            all_ids,
        ).fetchall()
        attempted_ids = {r[0] for r in attempted_rows}

    manual_review_ids = lib._manual_review_article_ids()

    summary = {}
    for domain, arts in by_domain.items():
        ids = {a["id"] for a in arts}
        summary[domain] = {
            "total": len(arts),
            "attempted": len(ids & attempted_ids),
            "untouched": len(ids - attempted_ids),
            "in_manual_review": len(ids & manual_review_ids),
        }

    return {
        "by_domain": by_domain,
        "summary": summary,
        "total_across_all_three": len(all_ids),
        "total_attempted": len(set(all_ids) & attempted_ids),
        "total_untouched": len(set(all_ids) - attempted_ids),
        "total_in_manual_review": len(set(all_ids) & manual_review_ids),
    }


def print_scale_check(result: dict) -> None:
    print("\n=== Task 1 — Medium-platform scale check (full library) ===\n")
    for domain, s in result["summary"].items():
        print(f"  {domain:28s} total={s['total']:<5} attempted={s['attempted']:<5} "
              f"untouched={s['untouched']:<5} in_manual_review={s['in_manual_review']}")
    print(f"\n  {'TOTAL':28s} total={result['total_across_all_three']:<5} "
          f"attempted={result['total_attempted']:<5} "
          f"untouched={result['total_untouched']:<5} "
          f"in_manual_review={result['total_in_manual_review']}")
    print("\n  (14 is the count from the ~25 articles processed so far via the backfill's\n"
          "  manual-review queue — compare against in_manual_review/total above for the\n"
          "  real picture. 'untouched' articles haven't been attempted at all yet, so\n"
          "  their eventual failure/success is not yet known.)")


# --------------------------------------------------------------------------
# Task 2 — Exa validation spike
# --------------------------------------------------------------------------

def _exa_search(query: str, api_key: str) -> tuple[list[dict], str]:
    """One Exa /search call, no domain restriction (unlike
    domain_migration.find_migrated_url, which restricts to one confirmed
    destination domain — here we don't know in advance where a Medium
    article might resolve to). Requests contents.text too — needed for the
    same-domain carve-out in _spike_one_article, which validates against
    Exa's own returned text rather than re-fetching (see there for why).
    Returns (results, error) — error is "" on success. Never raises."""
    try:
        resp = requests.post(
            _EXA_SEARCH_URL,
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
            json={"query": query, "numResults": 5, "contents": {"text": True}},
            timeout=_EXA_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        return [], f"{type(exc).__name__}: {exc}"
    results = data.get("results") or []
    return [r for r in results if isinstance(r, dict)], ""


def _spike_one_article(title: str, author: str, current_url: str, api_key: str) -> dict:
    """Search Exa for one article, validate the first title-matching
    candidate (reusing linklib.domain_migration._titles_match — NOT top-1
    blind, the exact gap the first spike round left open).

    Validation then splits on ONE thing: is the candidate itself on a
    recognized Medium-platform domain? If it's on some OTHER host, re-fetch
    it live and run the real extract.assess_extraction_quality() check —
    the same validation path linklib.pipeline._try_domain_migration already
    uses. But if Exa resolved the article back onto medium.com/
    bothsidesofthetable.com/link.medium.com itself, a live re-fetch is
    guaranteed to hit the exact same Cloudflare block the ORIGINAL url
    already failed on — that's not a real test of the candidate, it's
    re-proving the block exists. Revision #3's real finding: at least 3-4
    of the manual-review sample's "unrecovered" results were exactly this
    circular case, not genuinely bad candidates. For a same-domain
    candidate, validate against Exa's own already-returned `contents.text`
    instead — a plain word-count check against the same _MIN_CONTENT_WORDS
    threshold assess_extraction_quality() uses, since the paywall/bot-
    challenge marker checks need raw HTML Exa's text field doesn't carry.
    `validation_path` on the result says which path ran: 'live-refetch' or
    'exa-text'."""
    row = {
        "title": title, "author": author, "current_url": current_url,
        "found": False, "title_match": None, "candidate_url": "",
        "candidate_title": "", "validation_path": "", "quality_ok": None,
        "quality_reason": "", "content_length": 0, "excerpt": "", "error": "",
    }
    if not title.strip():
        row["error"] = "no stored title to search with"
        return row

    query = f"{title} {author}".strip() if author else title
    exa_results, error = _exa_search(query, api_key)
    if error:
        row["error"] = error
        return row
    if not exa_results:
        row["error"] = "no Exa results at all"
        return row

    matched = None
    for r in exa_results:
        cand_title = r.get("title") or ""
        if r.get("url") and _titles_match(title, cand_title):
            matched = r
            break
    if matched is None:
        row["error"] = f"no title-matching candidate among {len(exa_results)} results (would stay in manual review)"
        row["found"] = False
        row["title_match"] = False
        return row

    row["found"] = True
    row["title_match"] = True
    candidate_url = matched.get("url") or ""
    row["candidate_url"] = candidate_url
    row["candidate_title"] = matched.get("title") or ""

    if _host(candidate_url) in MEDIUM_DOMAINS:
        # Same-domain carve-out: re-fetching would just re-hit the same
        # block, so validate against what Exa already returned instead.
        row["validation_path"] = "exa-text"
        exa_text = (matched.get("text") or "").strip()
        word_count = len(exa_text.split())
        ok = word_count >= _MIN_CONTENT_WORDS
        row["quality_ok"] = ok
        row["quality_reason"] = "" if ok else "too-thin (exa-text word count)"
        if not ok:
            return row
        row["content_length"] = len(exa_text)
        row["excerpt"] = exa_text[:200]
        return row

    row["validation_path"] = "live-refetch"
    try:
        page = fetch_page(candidate_url)
    except Exception as exc:
        row["error"] = f"candidate fetch raised: {type(exc).__name__}: {exc}"
        return row
    if not page.raw_html:
        row["error"] = f"candidate fetch failed: {page.fetch_error or 'no raw_html returned'}"
        return row

    ok, reason = assess_extraction_quality(page.raw_html, page.content, page.blocked)
    row["quality_ok"] = ok
    row["quality_reason"] = reason
    if not ok:
        return row

    structured = extract_reader_html(page.raw_html, candidate_url)
    if not structured:
        row["quality_ok"] = False
        row["quality_reason"] = "too-thin (structured extraction empty)"
        return row

    row["content_length"] = len(page.content)
    row["excerpt"] = (page.content or "")[:200]
    return row


def exa_spike(lib: Library) -> dict:
    """Two groups, both validated through the SAME real pipeline (title
    match, then a real fetch + assess_extraction_quality — see
    _spike_one_article):

    - manual_review: every currently-known manual-review article on one of
      the three Medium-platform domains.
    - link_medium_sample: a small sample of link.medium.com articles
      regardless of manual-review status (that domain has zero manual-
      review articles yet — it's never actually been tested, so without
      this it would silently never appear here at all)."""
    api_key = os.environ.get("EXA_API_KEY")
    if not api_key:
        print("EXA_API_KEY not set — skipping Task 2 (Exa spike). Set it in the "
              "environment (same var the app itself uses) and re-run.", file=sys.stderr)
        return {"manual_review": [], "link_medium_sample": []}

    review_rows = lib.list_articles_needing_manual_review(limit=100000)
    review_targets = [r for r in review_rows if _host(r.get("current_url") or "") in MEDIUM_DOMAINS]

    manual_review_results = []
    for r in review_targets:
        art = lib.get_article(r["article_id"]) or {}
        row = _spike_one_article(
            (r.get("title") or "").strip(), (art.get("author") or "").strip(),
            r.get("current_url") or "", api_key,
        )
        row["article_id"] = r["article_id"]
        manual_review_results.append(row)

    link_medium_rows = lib.conn.execute(
        "SELECT id, url, title, author FROM articles WHERE url != '' ORDER BY id"
    ).fetchall()
    link_medium_sample = [r for r in link_medium_rows if _host(r["url"]) == "link.medium.com"][:_LINK_MEDIUM_SAMPLE_SIZE]

    link_medium_results = []
    for r in link_medium_sample:
        row = _spike_one_article((r["title"] or "").strip(), (r["author"] or "").strip(), r["url"], api_key)
        row["article_id"] = r["id"]
        link_medium_results.append(row)

    return {"manual_review": manual_review_results, "link_medium_sample": link_medium_results}


def _print_spike_rows(results: list[dict]) -> None:
    for r in results:
        print(f"  article #{r['article_id']}: {r['title'][:70]!r}")
        print(f"    current_url:      {r['current_url']}")
        # candidate_url printed unconditionally, success or failure — a
        # failed live-refetch used to print only "HTTP 403" with no URL,
        # making it impossible to tell a same-domain circular failure from
        # a genuinely different newly-blocked host without re-running by
        # hand. It's "" only when no title-matching candidate was ever
        # found (nothing to show).
        print(f"    candidate_url:    {r['candidate_url'] or '(none found)'}")
        if r["error"]:
            print(f"    result:           {r['error']}")
        else:
            print(f"    found:            {r['found']}  (title-matched: {r['title_match']})")
            print(f"    candidate_title:  {r['candidate_title']!r}")
            print(f"    validation_path:  {r['validation_path']}")
            print(f"    quality_ok:       {r['quality_ok']}  (reason: {r['quality_reason'] or 'n/a'})")
            if r["quality_ok"]:
                print(f"    content_len:      {r['content_length']}")
                print(f"    excerpt:          {r['excerpt']!r}")
        print()


def print_exa_spike(results: dict) -> None:
    print("\n=== Task 2 — Exa validation spike (title-match + real fetch + quality-check) ===\n")
    mr = results.get("manual_review") or []
    lm = results.get("link_medium_sample") or []

    print(f"-- Manual-review Medium-platform articles ({len(mr)}) --\n")
    if not mr:
        print("  (none — either EXA_API_KEY is unset, or nothing is currently in manual review)")
    else:
        _print_spike_rows(mr)

    print(f"-- link.medium.com sample, untested/not-yet-failing ({len(lm)}) --\n")
    if not lm:
        print("  (no link.medium.com articles found, or EXA_API_KEY is unset)")
    else:
        _print_spike_rows(lm)


# --------------------------------------------------------------------------
# Task 3 — existing-content audit (Medium-platform only)
# Task 4 — the same audit, library-wide, grouped by host
# --------------------------------------------------------------------------

def _content_word_count(content: str, content_html: str) -> int:
    """Word count of what's ALREADY STORED for one article, right now —
    no fetching. Prefers content_html (structured, what the Reader has
    shown since Phase 5b) converted to plain text via
    extract._extract_content() — reused, not reimplemented, the same
    plain-text contract FTS5/enrichment/search already depend on — and
    falls back to the plain `content` column when content_html is empty.
    Shared by content_audit() (Task 3, Medium-only) and
    library_wide_content_audit() (Task 4, every host) so both reports use
    identical bucketing logic — no drift between the two."""
    html = content_html or ""
    text = _extract_content(html) if html.strip() else (content or "")
    return len((text or "").split())


def _bucket_for_word_count(word_count: int) -> str:
    """'empty' (<= _CONTENT_AUDIT_NEAR_ZERO_WORDS), 'thin' (<
    _CONTENT_AUDIT_THIN_WORDS), or 'ok'."""
    if word_count <= _CONTENT_AUDIT_NEAR_ZERO_WORDS:
        return "empty"
    if word_count < _CONTENT_AUDIT_THIN_WORDS:
        return "thin"
    return "ok"


def content_audit(lib: Library) -> dict:
    """Task 3: the three-bucket word-count audit, scoped to
    Medium-platform articles only — regardless of attempt or
    manual-review status. A different question from _spike_one_article's
    60-word acceptance threshold for a FOUND REPLACEMENT: this doesn't
    fetch anything, it audits existing rows.

    Report only: no flagging or deletion logic — see this script's module
    docstring and the standing "human review before any destructive
    production action" rule."""
    rows = lib.conn.execute(
        "SELECT id, url, title, content, content_html FROM articles WHERE url != ''"
    ).fetchall()
    medium_rows = [r for r in rows if _host(r["url"]) in MEDIUM_DOMAINS]

    empty, under_thin, ok = [], [], []
    buckets = {"empty": empty, "thin": under_thin, "ok": ok}
    for r in medium_rows:
        word_count = _content_word_count(r["content"], r["content_html"])
        entry = {"id": r["id"], "url": r["url"], "title": r["title"], "word_count": word_count}
        buckets[_bucket_for_word_count(word_count)].append(entry)

    return {
        "total_checked": len(medium_rows),
        "empty_or_near_zero": empty,
        "under_thin_threshold": under_thin,
        "at_or_above_thin_threshold": ok,
    }


def print_content_audit(result: dict) -> None:
    print("\n=== Task 3 — Existing-content audit, Medium-platform only (what's stored today, no fetching) ===\n")
    print(f"  Total Medium-platform articles checked: {result['total_checked']}")
    print(f"  Empty or near-zero (<= {_CONTENT_AUDIT_NEAR_ZERO_WORDS} words) — genuinely "
          f"nothing saved: {len(result['empty_or_near_zero'])}")
    print(f"  Under {_CONTENT_AUDIT_THIN_WORDS} words — likely a preview/intro-only stub: "
          f"{len(result['under_thin_threshold'])}")
    print(f"  {_CONTENT_AUDIT_THIN_WORDS}+ words — probably fine as a fallback even without "
          f"an upgrade: {len(result['at_or_above_thin_threshold'])}")
    print("\n  (Report only — no article is flagged, excluded, or touched by this audit.)")


# How many top-by-empty-count hosts to show in the library-wide breakdown —
# the ask's own floor ("at least the top 25"), not exhaustive (~4,516
# articles could span hundreds of distinct hosts).
_LIBRARY_WIDE_TOP_HOSTS = 25


def library_wide_content_audit(lib: Library, top_n: int = _LIBRARY_WIDE_TOP_HOSTS) -> dict:
    """Task 4: the SAME three-bucket word-count audit as content_audit()
    (Task 3) — same _content_word_count()/_bucket_for_word_count(), reused
    not reimplemented, so the two reports can't drift apart — but across
    EVERY article in the library, grouped by URL host. Answers the
    question Task 3 alone couldn't: is the Medium-platform emptiness rate
    (54% in the first run) specific to Medium's Cloudflare block, or does
    the save mechanism itself under-capture library-wide? Report only —
    same no-flagging/no-deletion contract as Task 3."""
    rows = lib.conn.execute(
        "SELECT id, url, title, content, content_html FROM articles WHERE url != ''"
    ).fetchall()

    by_host: dict[str, dict] = {}
    empties: list[dict] = []
    totals = {"empty": 0, "thin": 0, "ok": 0}
    for r in rows:
        host = _host(r["url"]) or "(no host)"
        word_count = _content_word_count(r["content"], r["content_html"])
        bucket = _bucket_for_word_count(word_count)
        h = by_host.setdefault(host, {"total": 0, "empty": 0, "thin": 0, "ok": 0})
        h["total"] += 1
        h[bucket] += 1
        totals[bucket] += 1
        if bucket == "empty":
            empties.append({"id": r["id"], "title": r["title"], "url": r["url"], "word_count": word_count})

    top_hosts = sorted(by_host.items(), key=lambda kv: -kv[1]["empty"])[:top_n]

    return {
        "total_checked": len(rows),
        "total_empty": totals["empty"],
        "total_thin": totals["thin"],
        "total_ok": totals["ok"],
        "by_host_top": [{"host": h, **stats} for h, stats in top_hosts],
        "empties": empties,
    }


def print_library_wide_audit(result: dict, list_empties: bool) -> None:
    print("\n=== Task 4 — Library-wide content audit, grouped by host (what's stored today, no fetching) ===\n")
    total = result["total_checked"]
    te, tt, tok = result["total_empty"], result["total_thin"], result["total_ok"]

    def pct(n: int) -> str:
        return f"{n / total * 100:.1f}%" if total else "0.0%"

    print(f"  Total articles checked (whole library): {total}")
    print(f"  Empty or near-zero (<= {_CONTENT_AUDIT_NEAR_ZERO_WORDS} words): {te} ({pct(te)})")
    print(f"  Thin (< {_CONTENT_AUDIT_THIN_WORDS} words, not already counted above): {tt} ({pct(tt)})")
    print(f"  {_CONTENT_AUDIT_THIN_WORDS}+ words: {tok} ({pct(tok)})")

    print(f"\n  Top {len(result['by_host_top'])} hosts by empty-article count:\n")
    print(f"  {'HOST':<40s}{'TOTAL':>8s}{'EMPTY':>8s}{'THIN':>8s}{'OK':>8s}")
    for row in result["by_host_top"]:
        print(f"  {row['host'][:40]:<40s}{row['total']:>8d}{row['empty']:>8d}{row['thin']:>8d}{row['ok']:>8d}")

    print("\n  (Report only — no article is flagged, excluded, or touched by this audit.)")

    if list_empties:
        print(f"\n-- Empty-bucket articles, library-wide, CSV ({len(result['empties'])} rows) --\n")
        writer = csv.writer(sys.stdout)
        writer.writerow(["article_id", "title", "url", "word_count"])
        for e in result["empties"]:
            writer.writerow([e["id"], e["title"], e["url"], e["word_count"]])


# --------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", help="Path to library.db (or set LINKLIB_DB)")
    ap.add_argument("--skip-exa", action="store_true", help="Run Tasks 1/3/4 only, no Exa calls")
    ap.add_argument("--json", action="store_true", help="Emit machine-readable JSON instead of the printed report")
    ap.add_argument("--list-empties", action="store_true",
                     help="Also print a CSV (id,title,url,word_count) of every empty-bucket article "
                          "library-wide (Task 4) — listing only, never writes or flags anything")
    args = ap.parse_args()

    db_path = resolve_db_path(args.db)
    lib = Library(db_path)
    try:
        scale = scale_check(lib)
        exa_results = {"manual_review": [], "link_medium_sample": []} if args.skip_exa else exa_spike(lib)
        audit = content_audit(lib)
        library_audit = library_wide_content_audit(lib)
    finally:
        lib.close()

    if args.json:
        payload = {"scale_check": scale, "exa_spike": exa_results, "content_audit": audit,
                   "library_wide_content_audit": {k: v for k, v in library_audit.items() if k != "empties"}}
        if args.list_empties:
            payload["library_wide_content_audit"]["empties"] = library_audit["empties"]
        print(json.dumps(payload, indent=2))
        return

    print_scale_check(scale)
    print_exa_spike(exa_results)
    print_content_audit(audit)
    print_library_wide_audit(library_audit, args.list_empties)


if __name__ == "__main__":
    main()
