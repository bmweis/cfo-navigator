#!/usr/bin/env python3
"""One-time historical catch-up: populate the Library Queue from each source's
sitemap, back to your saves cutoff. Review the results at /admin/library/queue.

This is the throwaway companion to the go-forward Library Queue — run it once,
review what it proposes, and you never need a backfill again. RSS only carries
recent items, so this reads each subscription's sitemap instead. Coverage is
best-effort and varies by source; the run prints a per-source report so the
gaps are visible.

Usage:
    export ANTHROPIC_API_KEY=...                  # for Opus enrichment (recommended)
    python -m scripts.backfill_queue --db library.db --dry-run        # preview reach
    python -m scripts.backfill_queue --db library.db                  # cutoff = your last save
    python -m scripts.backfill_queue --db library.db --since 2024-08-01 --per-source 100
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from linklib.db import Library
from linklib.feed import parse_opml
from linklib.queue import QUEUE_ENRICH_MODEL, scan_sitemaps_into_queue


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="library.db")
    ap.add_argument("--opml", default=os.environ.get("LINKLIB_SITES_OPML", "preferred_sites.opml"))
    ap.add_argument("--since", default="",
                    help="cutoff date YYYY-MM-DD (default: your most recent saved_at)")
    ap.add_argument("--per-source", type=int, default=150,
                    help="max candidates queued per source")
    ap.add_argument("--model", default=QUEUE_ENRICH_MODEL,
                    help="enrichment model (default: most capable, for depth)")
    ap.add_argument("--no-enrich", action="store_true",
                    help="queue with heuristic tags only (no API spend)")
    ap.add_argument("--dry-run", action="store_true",
                    help="report coverage without enriching or saving")
    args = ap.parse_args()

    lib = Library(args.db)

    # Determine the cutoff: explicit --since, else your most recent save.
    if args.since:
        try:
            since = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            print(f"ERROR: --since must be YYYY-MM-DD, got {args.since!r}", file=sys.stderr)
            return 2
    else:
        last = lib.last_saved_at()
        if not last:
            print("No saved_at found in the library — pass --since YYYY-MM-DD to set a cutoff.",
                  file=sys.stderr)
            return 2
        try:
            since = datetime.fromisoformat(last.replace("Z", "+00:00"))
        except ValueError:
            since = datetime.strptime(last[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)

    enrich = not args.no_enrich
    if enrich and not os.environ.get("ANTHROPIC_API_KEY"):
        print("WARNING: ANTHROPIC_API_KEY not set — queuing with heuristic tags, no summaries.")
        enrich = False

    feeds = parse_opml(args.opml)
    mode = "DRY RUN (no saving)" if args.dry_run else (
        f"enriching with {args.model}" if enrich else "heuristic tags only")
    print(f"Catch-up cutoff: {since.date()}  |  {len(feeds)} sources from {args.opml}  |  {mode}\n")

    def progress(source, i, n):
        print(f"\r  {source[:34]:34} {i}/{n}   ", end="", flush=True)
        if i == n:
            print()

    report = scan_sitemaps_into_queue(
        lib, feeds, since, enrich=enrich, model=args.model,
        per_source_limit=args.per_source, dry_run=args.dry_run, progress=progress,
    )

    print(f"\n{'SOURCE':28} {'CANDS':>6} {'ADDED':>6} {'UNDATED':>8}  NOTE / SITEMAP")
    print("-" * 78)
    tot_c = tot_a = 0
    for st in report:
        tot_c += st["candidates"]
        tot_a += st["added"]
        note = st["note"] or (st["sitemap"] or "")
        print(f"{st['source'][:28]:28} {st['candidates']:>6} {st['added']:>6} "
              f"{st['undated']:>8}  {note[:40]}")
    print("-" * 78)
    print(f"{'TOTAL':28} {tot_c:>6} {tot_a:>6}")

    if args.dry_run:
        print("\nDry run — nothing saved. Drop --dry-run to enrich and queue these.")
    else:
        print(f"\nQueued {tot_a} candidates. Review them at /admin/library/queue.")

    lib.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
