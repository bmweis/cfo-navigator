#!/usr/bin/env python3
"""Bulk-deny pending feature_review_queue items, scoped to one category and
(by default) source='scan' — cleanup tool for a botched origination run.

Built for the real Neobanking incident (2026-08): the first live run of
scripts/originate_category_features.py produced 364 singleton proposals
with zero real merges, due to the whole-batch clustering bug (see
linklib/feature_scan.py's Phase 3 section header for the full post-mortem
and fix). Those 364 items needed clearing before a corrected re-run, but
per CLAUDE.md's "no dead data" / always-leave-a-trace discipline, the
right move is to DENY them (preserving the record that this run happened
and why it was thrown out), never to delete the rows outright.

Preview by default (lists exactly what would be denied, writes nothing);
--apply denies each one via Library.deny_feature_review_queue_item with a
shared resolution note.

Usage:
    python -m scripts.deny_pending_scan_proposals --db /data/library.db --category Neobanking \\
        --reason "Superseded by corrected clustering re-run, 2026-08-23"
    python -m scripts.deny_pending_scan_proposals --db /data/library.db --category Neobanking \\
        --reason "..." --apply
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from linklib.db import Library, resolve_db_path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None, help="Path to library.db (or set LINKLIB_DB)")
    ap.add_argument("--category", required=True, help="tool_categories name, e.g. Neobanking")
    ap.add_argument("--source", default="scan", choices=["admin", "scan", "public"],
                     help="Only deny pending items from this source (default: scan).")
    ap.add_argument("--reason", required=True,
                     help="Resolution note recorded on every denied item — make it specific "
                          "enough that a later reader understands why these were thrown out.")
    ap.add_argument("--apply", action="store_true",
                     help="Actually deny the matching items. Without this flag, only lists "
                          "what would be denied — writes nothing.")
    args = ap.parse_args()

    db_path = resolve_db_path(args.db)
    print(f"Reading from: {db_path}\n")

    lib = Library(db_path)
    try:
        category_id = lib.get_tool_category_id(args.category)
        if category_id is None:
            print(f"No tool_categories row named {args.category!r}.", file=sys.stderr)
            return 1

        pending = [
            item for item in lib.list_feature_review_queue(status="pending")
            if item["category_id"] == category_id and item["source"] == args.source
        ]

        if not pending:
            print(f"No pending {args.source!r}-sourced items found for category "
                  f"{args.category!r} (id={category_id}). Nothing to do.")
            return 0

        print(f"Category: {args.category} (id={category_id})")
        print(f"Matching pending {args.source!r} items: {len(pending)}")
        print(f"Reason to be recorded: {args.reason!r}")
        print(f"Mode: {'APPLY (denying for real)' if args.apply else 'PREVIEW (no writes)'}\n")

        for item in pending:
            name = (item["payload"].get("feature") or {}).get("name", "(existing-feature link)")
            print(f"  #{item['id']}: {name}")

        if not args.apply:
            print("\nNothing was denied. Re-run with --apply to deny these for real.")
            return 0

        print()
        denied = 0
        for item in pending:
            lib.deny_feature_review_queue_item(item["id"], args.reason)
            denied += 1
        print(f"Denied {denied} item(s). They remain in feature_review_queue as a "
              f"'denied' historical record — verify at /admin/tools/software/feature-review-queue.")
    finally:
        lib.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
