#!/usr/bin/env python3
"""JOB 2 (CLI) — save a single link going forward.

Usage:
    python -m scripts.add_link "https://example.com/post" --tags saas,metrics --note "great CAC framing"

The web endpoint (webapp/app.py) calls the same pipeline, so this and the
phone shortcut behave identically.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from linklib.db import Library
from linklib.pipeline import ingest_url


def main() -> int:
    ap = argparse.ArgumentParser(description="Add one URL to the link library.")
    ap.add_argument("url")
    ap.add_argument("--db", default="library.db")
    ap.add_argument("--tags", default="", help="comma-separated")
    ap.add_argument("--note", default="")
    ap.add_argument("--no-fetch", action="store_true", help="skip live full-text fetch")
    ap.add_argument("--no-enrich", action="store_true", help="skip Claude summary/tags")
    args = ap.parse_args()

    tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    lib = Library(args.db)
    row = ingest_url(
        lib, args.url, tags=tags, notes=args.note,
        fetch_fulltext=not args.no_fetch, do_enrich=not args.no_enrich,
    )
    print(f"Saved #{row['id']}: {row.get('title', args.url)}")
    if row.get("tags"):
        print(f"  tags: {', '.join(row['tags'])}")
    lib.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
