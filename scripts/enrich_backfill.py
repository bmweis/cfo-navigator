#!/usr/bin/env python3
"""Backfill Claude summaries + auto-tags over imported rows.

Usage:
    export ANTHROPIC_API_KEY=...
    python -m scripts.enrich_backfill --db library.db --limit 500
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from linklib.db import Library, resolve_db_path
from linklib.pipeline import enrich_library


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None, help="Path to library.db (or set LINKLIB_DB)")
    ap.add_argument("--limit", type=int, default=1000)
    ap.add_argument("--no-fetch", action="store_true",
                    help="skip live full-text fetch (summarize from title only)")
    ap.add_argument("--force", action="store_true",
                    help="re-enrich EVERY row, not just unenriched ones — use to "
                         "standardize the whole library on one model (with --model)")
    ap.add_argument("--model", default=None,
                    help="enrichment model override, e.g. claude-opus-4-8 "
                         "(default: LINKLIB_ENRICH_MODEL)")
    args = ap.parse_args()
    args.db = resolve_db_path(args.db)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: set ANTHROPIC_API_KEY first.", file=sys.stderr)
        return 2

    lib = Library(args.db)

    if args.force:
        total = lib.count()
        print(f"Force re-enrichment of up to {min(total, args.limit)} rows"
              f"{f' with {args.model}' if args.model else ''} — summaries overwritten, "
              f"tags unioned.\n")

    def progress(done, total, title):
        print(f"  [{done}/{total}] {title[:70]}")

    n = enrich_library(lib, limit=args.limit, fetch=not args.no_fetch,
                       force=args.force, model=args.model, progress=progress)
    print(f"\nEnriched {n} articles.")
    lib.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
