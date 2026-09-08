#!/usr/bin/env python3
"""Manual QA / one-off diagnostic — trace whether the Medium-platform tier
(fetch-by-URL and search-by-title, linklib/medium_platform.py +
linklib.pipeline._try_medium_platform) actually ran for specific stuck
articles, and inspect what a stored Wayback snapshot actually contains.

Built for a real live-proof round on PR #358 (2026-08 wrap-up sprint item 1):
two production traces (medium.com #437, shockwaveinnovations.com #142) came
back with a log signature (source='direct', reason='fetch-error'/'too-thin')
indistinguishable from the pre-#358 flow, so there was no way to tell from
the log alone whether the new tier ran and missed, or was never reached.
PR #358 also fixed that ambiguity going forward (a `tier_trace` is now
appended to the logged `detail` whenever a tier is actually attempted — see
linklib.pipeline._finish_backfill_after_direct_failure/_finish_backfill_via_wayback).
This script exists for the same investigation as an independent, direct
confirmation: it calls linklib.pipeline._try_medium_platform() itself (not
through the full backfill_article_content() write path) so you can watch its
actual behavior right now, plus a Wayback-snapshot content inspection for the
"is the stored snapshot an empty JS shell" hypothesis.

Read-only except for the live Exa/Wayback network calls _try_medium_platform
and wayback.find_snapshot_verbose/fetch_snapshot_verbose make on their own —
nothing here writes to content_html or content_refetch_log; that only ever
happens inside backfill_article_content() itself, which this script never
calls. Meant to be run by hand (e.g. via `railway ssh`) against production,
same "manual-QA tool, not an automated pass/fail eval" shape as
scripts/medium_platform_scale_check.py.

Usage:
    # Trace specific article IDs (existing log history + a live re-trace):
    python -m scripts.trace_medium_tier --db /data/library.db --ids 437 142

    # Also auto-pick N more stuck articles (mix of medium.com and
    # shockwaveinnovations.com) not already in --ids, so conclusions aren't
    # drawn from two data points:
    python -m scripts.trace_medium_tier --db /data/library.db --ids 437 142 --auto 3

    # Inspect a stored Wayback snapshot's actual HTML/content for one URL
    # (the "is this an empty JS shell" question) without re-running the
    # whole tier:
    python -m scripts.trace_medium_tier --db /data/library.db --inspect-wayback \
        --url "https://www.shockwaveinnovations.com/some-article"
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from linklib.db import Library, resolve_db_path
from linklib import medium_platform as mp_mod
from linklib import pipeline as pl
from linklib import wayback
from linklib.extract import assess_extraction_quality, _page_data_from_html, _MIN_CONTENT_WORDS


def _host(url: str) -> str:
    from urllib.parse import urlsplit
    host = (urlsplit(url or "").netloc or "").lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host


def _print_existing_log(lib: Library, article_id: int) -> None:
    rows = lib.conn.execute(
        "SELECT * FROM content_refetch_log WHERE article_id=? ORDER BY attempted_at",
        (article_id,),
    ).fetchall()
    if not rows:
        print("    (no content_refetch_log rows yet for this article)")
        return
    for r in rows:
        print(f"    [{r['attempted_at']}] status={r['status']!r} source={r['source']!r} "
              f"reason={r['reason']!r}")
        print(f"        detail: {r['detail']!r}")


def trace_article(lib: Library, article_id: int) -> None:
    article = lib.get_article(article_id)
    if not article:
        print(f"=== Article #{article_id}: NOT FOUND ===\n")
        return
    url = article["url"]
    title = article.get("title") or ""
    author = article.get("author") or ""
    host = _host(url)
    recognized = mp_mod.is_recognized_blocked_host(url)

    print(f"=== Article #{article_id}: {title!r} ===")
    print(f"    url: {url}")
    print(f"    host: {host}  |  is_recognized_blocked_host: {recognized}")
    print("    Existing content_refetch_log history:")
    _print_existing_log(lib, article_id)

    if not recognized:
        print("    Host is NOT a recognized blocked host — the Medium tier "
              "would never be reached for this article by the current code, "
              "regardless of what the log shows.\n")
        return

    print("    Live re-trace of _try_medium_platform() (read-only, no DB write):")
    ok, structured, candidate_url, source, note, exa_cost = pl._try_medium_platform(lib, title, author, url)
    print(f"        -> ok={ok}  source={source!r}  candidate_url={candidate_url!r}")
    print(f"        -> note: {note}")
    print(f"        -> exa_cost_usd: {exa_cost}")
    if ok:
        print(f"        -> structured content length: {len(structured)} chars "
              f"(this run did NOT write it — call backfill_article_content() for that)")
    print()


def inspect_wayback(url: str) -> None:
    print(f"=== Wayback snapshot inspection for: {url} ===")
    snap_url, wb_note = wayback.find_snapshot_verbose(url)
    if not snap_url:
        print(f"    No snapshot found: {wb_note}")
        return
    print(f"    Snapshot URL: {snap_url}")
    snap_html, fetch_note = wayback.fetch_snapshot_verbose(snap_url)
    if not snap_html:
        print(f"    Snapshot found but fetch failed: {fetch_note}")
        return
    print(f"    Raw snapshot HTML length: {len(snap_html):,} chars")

    page = _page_data_from_html(snap_html)
    word_count = len((page.content or "").split())
    print(f"    Extracted plain-text word count: {word_count} "
          f"(floor is {_MIN_CONTENT_WORDS})")
    ok, reason = assess_extraction_quality(page.raw_html, page.content, page.blocked)
    print(f"    assess_extraction_quality: ok={ok}  reason={reason!r}")

    preview = (page.content or "").strip()[:400]
    print("    First ~400 chars of extracted plain text:")
    print(f"        {preview!r}")
    if len(snap_html) > 500 and word_count < _MIN_CONTENT_WORDS:
        print("    NOTE: raw HTML is substantial but extracted text is thin — "
              "consistent with a client-side-rendered page whose real content "
              "never appears in the raw HTML response (the JS-shell hypothesis). "
              "Not proof by itself — eyeball the raw HTML below for a <script>-"
              "heavy, near-empty <body> to confirm.")
    print()


def _pick_auto_articles(lib: Library, n: int, exclude_ids: set[int]) -> list[int]:
    """Pick up to n stuck (needs-manual-review) articles not already in
    exclude_ids, alternating medium.com/link.medium.com/custom-domain hosts
    with shockwaveinnovations.com so a small sample still covers both."""
    rows = lib.list_articles_needing_manual_review(limit=2000)
    medium_ids, shockwave_ids = [], []
    for r in rows:
        aid = r["article_id"]
        if aid in exclude_ids:
            continue
        art = lib.get_article(aid)
        if not art:
            continue
        if mp_mod.is_medium_platform_host(art["url"]):
            medium_ids.append(aid)
        elif _host(art["url"]) == "shockwaveinnovations.com":
            shockwave_ids.append(aid)

    picked: list[int] = []
    pools = [medium_ids, shockwave_ids]
    i = 0
    while len(picked) < n and any(pools):
        pool = pools[i % 2]
        i += 1
        if pool:
            picked.append(pool.pop(0))
    return picked


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=None)
    ap.add_argument("--ids", type=int, nargs="*", default=[])
    ap.add_argument("--auto", type=int, default=0,
                    help="Also trace N more stuck articles auto-picked from "
                         "the manual-review queue (mix of medium.com and "
                         "shockwaveinnovations.com), not already in --ids.")
    ap.add_argument("--inspect-wayback", action="store_true",
                    help="Instead of tracing article IDs, inspect a stored "
                         "Wayback snapshot's actual content for --url.")
    ap.add_argument("--url", default="")
    args = ap.parse_args()

    db_path = resolve_db_path(args.db)
    lib = Library(db_path)
    try:
        if args.inspect_wayback:
            if not args.url:
                ap.error("--inspect-wayback requires --url")
            inspect_wayback(args.url)
            return

        ids = list(args.ids)
        if args.auto:
            auto_ids = _pick_auto_articles(lib, args.auto, set(ids))
            print(f"Auto-picked {len(auto_ids)} additional article(s): {auto_ids}\n")
            ids.extend(auto_ids)

        if not ids:
            ap.error("pass --ids, --auto, or both")

        for article_id in ids:
            trace_article(lib, article_id)
    finally:
        lib.close()


if __name__ == "__main__":
    main()
