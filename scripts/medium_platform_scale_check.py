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

--skip-exa runs Task 1 (the scale check) only — no EXA_API_KEY / network
calls needed. Task 2 (the Exa spike) needs EXA_API_KEY set in the
environment, same as the app itself.

Phase 0 (Medium fetch tier build) revision: the first spike accepted
Exa's top-1 result blind and trusted Exa's own returned snippet text as
the content-length signal. Both were gaps in the SPIKE, not in the real
tier design — linklib.domain_migration.find_migrated_url already loops
candidates and validates via _titles_match(), and the real tier will
validate a candidate via a genuine fetch_page()+assess_extraction_quality()
pass (see linklib.pipeline._try_domain_migration), not Exa's own text
field. This revision closes both gaps so the spike's numbers actually
reflect what the real tier would do: every candidate is title-matched
(same _titles_match(), reused not reimplemented) before acceptance, and
an accepted candidate is re-fetched live and run through the real
quality check before its content length is reported. Also adds a small
sample of `link.medium.com` articles regardless of manual-review status
(that domain has zero manual-review articles yet, so the first spike
never tested it at all) — labeled separately since these are UNTESTED
articles, not known failures.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from urllib.parse import urlsplit

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from linklib.db import Library, resolve_db_path
from linklib.domain_migration import _titles_match
from linklib.extract import fetch_page, assess_extraction_quality, extract_reader_html

# Same three hosts the Phase 0 investigation confirmed are one platform.
MEDIUM_DOMAINS = frozenset({"bothsidesofthetable.com", "medium.com", "link.medium.com"})

# How many untouched link.medium.com articles to sample — that domain has
# zero manual-review articles yet (nothing has failed there so far), so
# without this it would never appear in the Exa spike at all. Small on
# purpose — this is a feasibility check, not exhaustive coverage.
_LINK_MEDIUM_SAMPLE_SIZE = 5

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
    article might resolve to). Returns (results, error) — error is "" on
    success. Never raises."""
    try:
        resp = requests.post(
            _EXA_SEARCH_URL,
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
            json={"query": query, "numResults": 5},
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
    blind, the exact gap the first spike round left open), then re-fetch
    that candidate live and run it through the real
    extract.assess_extraction_quality() check — the same validation path
    linklib.pipeline._try_domain_migration already uses for the existing
    domain-migration tier — rather than trusting Exa's own returned text
    field, which the real tier will never do either."""
    row = {
        "title": title, "author": author, "current_url": current_url,
        "found": False, "title_match": None, "candidate_url": "",
        "candidate_title": "", "quality_ok": None, "quality_reason": "",
        "content_length": 0, "excerpt": "", "error": "",
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
        if r["error"]:
            print(f"    result:           {r['error']}")
        else:
            print(f"    found:            {r['found']}  (title-matched: {r['title_match']})")
            print(f"    candidate_url:    {r['candidate_url']}")
            print(f"    candidate_title:  {r['candidate_title']!r}")
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

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", help="Path to library.db (or set LINKLIB_DB)")
    ap.add_argument("--skip-exa", action="store_true", help="Run Task 1 only, no Exa calls")
    ap.add_argument("--json", action="store_true", help="Emit machine-readable JSON instead of the printed report")
    args = ap.parse_args()

    db_path = resolve_db_path(args.db)
    lib = Library(db_path)
    try:
        scale = scale_check(lib)
        exa_results = {"manual_review": [], "link_medium_sample": []} if args.skip_exa else exa_spike(lib)
    finally:
        lib.close()

    if args.json:
        print(json.dumps({"scale_check": scale, "exa_spike": exa_results}, indent=2))
        return

    print_scale_check(scale)
    print_exa_spike(exa_results)


if __name__ == "__main__":
    main()
