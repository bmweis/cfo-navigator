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

# Same three hosts the Phase 0 investigation confirmed are one platform.
MEDIUM_DOMAINS = frozenset({"bothsidesofthetable.com", "medium.com", "link.medium.com"})

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
    """One Exa /search call with full text contents requested. Returns
    (results, error) — error is "" on success. Never raises."""
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


def exa_spike(lib: Library) -> list[dict]:
    """For every currently-known manual-review article on one of the three
    Medium-platform domains, ask Exa (title + author as the query, same
    query shape linklib.domain_migration.find_migrated_url uses for its
    title) whether it can find and retrieve the article at all — no domain
    restriction, since part of the question is WHERE Exa resolves it to
    (the same custom domain, or a medium.com/@handle canonical URL)."""
    api_key = os.environ.get("EXA_API_KEY")
    if not api_key:
        print("EXA_API_KEY not set — skipping Task 2 (Exa spike). Set it in the "
              "environment (same var the app itself uses) and re-run.", file=sys.stderr)
        return []

    review_rows = lib.list_articles_needing_manual_review(limit=100000)
    targets = [r for r in review_rows if _host(r.get("current_url") or "") in MEDIUM_DOMAINS]

    results = []
    for r in targets:
        article_id = r["article_id"]
        title = (r.get("title") or "").strip()
        current_url = r.get("current_url") or ""
        art = lib.get_article(article_id) or {}
        author = (art.get("author") or "").strip()
        query = f"{title} {author}".strip() if author else title

        row = {
            "article_id": article_id,
            "title": title,
            "author": author,
            "current_url": current_url,
            "found": False,
            "resolved_url": "",
            "same_domain": None,
            "content_length": 0,
            "excerpt": "",
            "error": "",
        }

        if not title:
            row["error"] = "no stored title to search with"
            results.append(row)
            continue

        exa_results, error = _exa_search(query, api_key)
        if error:
            row["error"] = error
            results.append(row)
            continue
        if not exa_results:
            row["error"] = "no Exa results"
            results.append(row)
            continue

        top = exa_results[0]
        resolved_url = top.get("url") or ""
        text = (top.get("text") or "").strip()
        row.update({
            "found": True,
            "resolved_url": resolved_url,
            "same_domain": (_host(resolved_url) == _host(current_url)) if resolved_url else None,
            "content_length": len(text),
            "excerpt": text[:200],
        })
        results.append(row)

    return results


def print_exa_spike(results: list[dict]) -> None:
    print("\n=== Task 2 — Exa validation spike (manual-review Medium-platform articles) ===\n")
    if not results:
        print("  (no matching articles in the manual-review queue, or Exa was skipped)")
        return
    for r in results:
        print(f"  article #{r['article_id']}: {r['title'][:70]!r}")
        print(f"    current_url:   {r['current_url']}")
        if r["error"]:
            print(f"    ERROR:         {r['error']}")
        else:
            print(f"    found:         {r['found']}")
            print(f"    resolved_url:  {r['resolved_url']}")
            print(f"    same_domain:   {r['same_domain']}")
            print(f"    content_len:   {r['content_length']}")
            print(f"    excerpt:       {r['excerpt']!r}")
        print()


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
        exa_results = [] if args.skip_exa else exa_spike(lib)
    finally:
        lib.close()

    if args.json:
        print(json.dumps({"scale_check": scale, "exa_spike": exa_results}, indent=2))
        return

    print_scale_check(scale)
    print_exa_spike(exa_results)


if __name__ == "__main__":
    main()
