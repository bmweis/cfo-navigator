#!/usr/bin/env python3
"""Read-only diagnostic — Feature Taxonomy scan-tool Phase 0/1: confirms which
`tool_categories` rows already have a curated `category_features` list and
which don't, so the scan tool's build (docs/FEATURE_TAXONOMY.md §10,
origination mode) can be scoped against real numbers instead of the "~22
categories" estimate in CLAUDE.md's Phase 1c note.

This session had no access to production `library.db` — it's not in git and
lives only on the Railway volume — so this script exists to be run by hand
(e.g. via `railway ssh`) against the real database and its output pasted
back, per the standing "no guessing against production" rule (CLAUDE.md,
"One-off admin fixes against the database").

Makes NO writes of any kind — same safe-by-default shape as
scripts/report_orphaned_categories.py; no --apply flag because there's
nothing to apply.

For each tool_categories row, reports:
  - tool_count            (from Library.list_tool_categories, already free)
  - live category_features count (retired_at='' rows only — the curated,
    non-retired list a tool actually gets mapped against today)
  - retired category_features count (retired_at != '') for context
  - pending feature_review_queue count for that category (source breakdown:
    admin / scan / public) — a category can show 0 live features but still
    have proposals already sitting in the queue

Usage:
    python -m scripts.report_feature_taxonomy_coverage [--db library.db]
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from linklib.db import Library, resolve_db_path


def main():
    parser = argparse.ArgumentParser(
        description="Report which tool_categories rows already have a curated "
                    "category_features list, and which don't. Read-only."
    )
    parser.add_argument("--db", default=None, help="Path to library.db (or set LINKLIB_DB)")
    args = parser.parse_args()
    args.db = resolve_db_path(args.db, allow_missing=False)
    print(f"Reading from: {args.db}\n")

    lib = Library(args.db)
    try:
        categories = lib.list_tool_categories()

        live_counts: dict[int, int] = {}
        retired_counts: dict[int, int] = {}
        for (cat_id, retired_at) in lib.conn.execute(
            "SELECT category_id, retired_at FROM category_features"
        ).fetchall():
            if retired_at:
                retired_counts[cat_id] = retired_counts.get(cat_id, 0) + 1
            else:
                live_counts[cat_id] = live_counts.get(cat_id, 0) + 1

        queue_counts: dict[int, dict[str, int]] = {}
        for (cat_id, source, status) in lib.conn.execute(
            "SELECT category_id, source, status FROM feature_review_queue "
            "WHERE category_id IS NOT NULL AND status='pending'"
        ).fetchall():
            queue_counts.setdefault(cat_id, {}).setdefault(source, 0)
            queue_counts[cat_id][source] += 1
    finally:
        lib.close()

    print(f"Total tool_categories rows: {len(categories)}\n")
    print("=" * 100)

    with_features = []
    without_features = []

    for c in sorted(categories, key=lambda d: d["name"].lower()):
        live = live_counts.get(c["id"], 0)
        retired = retired_counts.get(c["id"], 0)
        pending = queue_counts.get(c["id"], {})
        pending_total = sum(pending.values())

        line = (
            f"[{c['id']:>3}] {c['name']:<28} "
            f"tools={c['tool_count']:<4} "
            f"live_features={live:<3} "
            f"retired_features={retired:<3} "
            f"pending_queue={pending_total:<3}"
        )
        if pending:
            breakdown = ", ".join(f"{src}={n}" for src, n in sorted(pending.items()))
            line += f"  ({breakdown})"
        print(line)

        (with_features if live > 0 else without_features).append(c["name"])

    print("=" * 100)
    print()
    print(f"Categories WITH a live curated feature list ({len(with_features)}): "
          f"{', '.join(with_features) or '(none)'}")
    print()
    print(f"Categories WITHOUT a live curated feature list ({len(without_features)}): "
          f"{', '.join(without_features) or '(none)'}")
    print()
    print(
        "Categories in the second list are origination-mode candidates. A category\n"
        "showing pending_queue>0 but live_features=0 already has scan/admin/public\n"
        "proposals waiting on review — check those before running a fresh origination\n"
        "scan against it, to avoid proposing duplicates of what's already queued."
    )


if __name__ == "__main__":
    main()
