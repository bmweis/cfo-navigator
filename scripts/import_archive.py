#!/usr/bin/env python3
"""JOB 1 (archive route) — import the Feedly "Download your data" archive.

No API token needed. Point it at the downloaded zip (or an unzipped folder).
Pass --db explicitly, or set LINKLIB_DB — there's no silent default:

    python -m scripts.import_archive --zip feedly-archive.zip --db library.db
    python -m scripts.import_archive --dir ./arch --db library.db --enrich

Flags:
    --include-read   also import read history (noisy; off by default)
    --enrich         run Claude summary/tagging during import (needs API key)

Idempotent: re-running merges (and unions board tags) rather than duplicating.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from linklib.db import Library, resolve_db_path
from linklib.archive import iter_archive
from linklib import enrich as enrich_mod


def main() -> int:
    ap = argparse.ArgumentParser(description="Import a Feedly data-export archive.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--zip", help="path to the Feedly archive .zip")
    src.add_argument("--dir", help="path to an already-unzipped archive folder")
    ap.add_argument("--db", default=None, help="Path to library.db (or set LINKLIB_DB)")
    ap.add_argument("--include-read", action="store_true")
    ap.add_argument("--exclude", default="Created,Unsaved",
                    help="comma-separated board names to skip (default: Created,Unsaved)")
    ap.add_argument("--enrich", action="store_true")
    args = ap.parse_args()
    # This is the one-time DB-creation script, so a missing file at the
    # resolved path is expected (first run) rather than a sign the path
    # is wrong — allow_missing=True.
    args.db = resolve_db_path(args.db, allow_missing=True)

    exclude = {e.strip() for e in args.exclude.split(",") if e.strip()}

    tmp = None
    if args.zip:
        tmp = tempfile.mkdtemp(prefix="feedly_")
        with zipfile.ZipFile(args.zip) as z:
            z.extractall(tmp)
        root = tmp
    else:
        root = args.dir

    lib = Library(args.db)
    parsed = 0
    for art in iter_archive(root, include_read=args.include_read, exclude=exclude):
        article_id = lib.upsert(art)
        if args.enrich:
            res = enrich_mod.enrich(art.title, art.title)  # no body text in export
            if res:
                lib.apply_enrichment(article_id, res.summary, res.tags)
                lib.record_enrichment_cost(article_id, res.model,
                                           input_tokens=res.input_tokens,
                                           output_tokens=res.output_tokens,
                                           cost_usd=res.cost_usd)
        parsed += 1
        if parsed % 250 == 0:
            print(f"  ...{parsed} links parsed ({lib.count()} unique so far)")

    print(f"\nDone. {parsed} links parsed, {lib.count()} unique articles in {args.db}.")
    print("Top boards:")
    for tag, n in lib.all_tags()[:12]:
        print(f"  {n:4d}  {tag}")
    if not args.enrich:
        print("\nTip: add summaries/tags later with `python -m scripts.enrich_backfill --db {}`".format(args.db))
    lib.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
