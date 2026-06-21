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

from linklib.db import Library
from linklib.pipeline import enrich_library


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="library.db")
    ap.add_argument("--limit", type=int, default=1000)
    ap.add_argument("--no-fetch", action="store_true",
                    help="skip live full-text fetch (summarize from title only)")
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: set ANTHROPIC_API_KEY first.", file=sys.stderr)
        return 2

    lib = Library(args.db)

    def progress(done, total, title):
        print(f"  [{done}/{total}] {title[:70]}")

    n = enrich_library(lib, limit=args.limit, fetch=not args.no_fetch, progress=progress)
    print(f"\nEnriched {n} articles.")
    lib.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
