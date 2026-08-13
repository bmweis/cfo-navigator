#!/usr/bin/env python3
"""Read-only diagnostic for the Phase L tools-category orphan-data
investigation: lists every tool AND community currently carrying a category
string that doesn't exist in the active `tool_categories` /
`community_categories` vocabulary anymore. Makes NO writes of any kind —
same safe-by-default shape as scripts/archive/report_flagged_categories.py, no
--apply flag because there's nothing to apply.

Why this exists: the prior investigation confirmed delete_tool_category /
delete_community_category correctly strip a deleted category from every
row's categories_json at the moment of deletion (verified by test and by
direct reproduction) — so an orphaned category surviving on a row today
means something OTHER than a normal admin-UI deletion wrote it there, or the
row predates that strip logic ever running. This script can't tell which by
itself, but each orphaned row's `updated_at` is the tell: a stale one is
consistent with old, one-time drift (safe to strip and be done); a recent
one — especially one at or after whenever the category was deleted — means
something is still actively writing bad data, and the write path needs to be
found and closed before any cleanup script runs, or the cleanup just
reappears the same way the tools/communities row-reappearing bug did.

Output is sorted by updated_at descending (most recently touched first) so
that signal is the first thing visible, not buried in an alphabetical list.

Usage:
    python -m scripts.report_orphaned_categories [--db library.db]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from linklib.db import Library, resolve_db_path


def _orphans(rows: list, active_names: set[str]) -> list[dict]:
    """rows: sqlite3.Row-like dicts with id, name, categories_json, updated_at.
    Returns one entry per row that carries at least one category not in
    active_names, with the specific orphaned values called out separately
    from the row's full category list."""
    found = []
    for r in rows:
        cats = json.loads(r["categories_json"]) or []
        orphaned = [c for c in cats if c not in active_names]
        if orphaned:
            found.append({
                "id": r["id"],
                "name": r["name"],
                "categories_json": r["categories_json"],
                "orphaned": orphaned,
                "updated_at": r["updated_at"],
            })
    found.sort(key=lambda d: d["updated_at"], reverse=True)
    return found


def _print_section(title: str, entries: list[dict]) -> None:
    print(f"{title}: {len(entries)} row(s) carrying an orphaned category\n")
    print("=" * 100)
    for e in entries:
        print(f"[{e['id']}] {e['name']}")
        print(f"    updated_at:  {e['updated_at'] or '(blank)'}")
        print(f"    orphaned:    {', '.join(e['orphaned'])}")
        print(f"    full categories_json: {e['categories_json']}")
        print()
    if not entries:
        print("(none found)\n")
    print("=" * 100)
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Report tools/communities carrying a category not in the active vocabulary. Read-only."
    )
    parser.add_argument("--db", default=None, help="Path to library.db (or set LINKLIB_DB)")
    args = parser.parse_args()
    args.db = resolve_db_path(args.db, allow_missing=False)
    print(f"Reading from: {args.db}\n")

    lib = Library(args.db)
    try:
        active_tool_cats = {c["name"] for c in lib.list_tool_categories()}
        active_community_cats = {c["name"] for c in lib.list_community_categories()}
        tool_rows = lib.conn.execute(
            "SELECT id, name, categories_json, updated_at FROM tools"
        ).fetchall()
        community_rows = lib.conn.execute(
            "SELECT id, name, categories_json, updated_at FROM communities"
        ).fetchall()
    finally:
        lib.close()

    print(f"Active tool categories ({len(active_tool_cats)}): {', '.join(sorted(active_tool_cats))}")
    print(f"Active community categories ({len(active_community_cats)}): {', '.join(sorted(active_community_cats))}\n")

    tool_orphans = _orphans(tool_rows, active_tool_cats)
    community_orphans = _orphans(community_rows, active_community_cats)

    _print_section("TOOLS", tool_orphans)
    _print_section("COMMUNITIES", community_orphans)

    print(
        "Read the updated_at column, most-recent first: if any orphaned row's\n"
        "updated_at is very recent (especially at or after when a category was\n"
        "last deleted at /admin/tools/categories or /admin/tools/communities),\n"
        "that's a strong signal something is still actively writing bad data —\n"
        "find and close that write path before running any strip-and-clean\n"
        "script. If every orphaned row's updated_at is old and predates the\n"
        "known category deletions, this is one-time drift and safe to clean up."
    )


if __name__ == "__main__":
    main()
