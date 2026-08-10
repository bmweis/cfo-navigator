#!/usr/bin/env python3
"""One-off audit (2026-08 screenshot_is_product-clobber incident): compares
a pre-bug Google Drive snapshot of library.db against the current live DB
to find the EXACT set of tools/communities rows whose screenshot_is_product
flag was silently reset from 1 to 0 by the bug described in PR #289
(admin_tools_edit_submit / admin_communities_edit_submit calling
update_tool_screenshot / update_community_screenshot with a hardcoded
screenshot_is_product=0 on every full-form save).

This is NOT something the current DB alone can answer — screenshot_is_product
has no audit trail (unlike tool_audit_log/community_audit_log, which only
cover deletions), so a row that's screenshot_is_product=0 today with a
non-empty screenshot_url is otherwise indistinguishable between "always was
a normal homepage capture" and "got clobbered." A pre-bug snapshot is the
only real source of truth.

Usage:
    python -m scripts.audit_screenshot_is_product_regression \\
        --before-db /path/to/library-YYYYMMDD-HHMMSS.db \\
        --after-db  /path/to/current/library.db

--before-db should be the newest Drive snapshot from BEFORE PR #288 merged
(2026-08-09) — download it from Google Drive per RUNBOOK.md §1. --after-db
is the current live DB — download via GET /admin/library/backup/download-db
(admin cookie or ?token=), or railway ssh onto the volume directly.

Read-only against both files — never writes anything, safe to run as many
times as you like.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from linklib.db import Library


def _flagged_rows(db_path: str, table: str) -> dict[int, dict]:
    lib = Library(db_path)
    try:
        rows = lib.conn.execute(
            f"SELECT id, name, slug, screenshot_url, screenshot_captured_at FROM {table} "
            f"WHERE screenshot_is_product = 1"
        ).fetchall()
        return {r["id"]: dict(r) for r in rows}
    finally:
        lib.close()


def _current_state(db_path: str, table: str, row_id: int) -> dict | None:
    lib = Library(db_path)
    try:
        row = lib.conn.execute(
            f"SELECT id, name, slug, screenshot_is_product, screenshot_url, "
            f"app_screenshot_url FROM {table} WHERE id=?",
            (row_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        lib.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--before-db", required=True, help="pre-bug Drive snapshot (library-YYYYMMDD-HHMMSS.db)")
    ap.add_argument("--after-db", required=True, help="current live library.db")
    args = ap.parse_args()

    for path, label in [(args.before_db, "--before-db"), (args.after_db, "--after-db")]:
        if not os.path.isfile(path):
            print(f"ERROR: {label} not found at {path}", file=sys.stderr)
            return 1

    clobbered: list[dict] = []
    still_fine: list[dict] = []
    missing: list[dict] = []

    for table in ("tools", "communities"):
        before = _flagged_rows(args.before_db, table)
        print(f"\n{table}: {len(before)} row(s) had screenshot_is_product=1 in the snapshot")
        for row_id, before_row in before.items():
            current = _current_state(args.after_db, table, row_id)
            if current is None:
                missing.append({"table": table, **before_row})
                continue
            if current["screenshot_is_product"] == 1:
                still_fine.append({"table": table, **current})
            elif current["app_screenshot_url"]:
                # Already moved to the app slot — either the Phase E manual
                # migration was already run for this row, or it was manually
                # re-curated since. Either way, not an open clobber.
                still_fine.append({"table": table, **current})
            else:
                clobbered.append({
                    "table": table, "id": row_id,
                    "name": before_row["name"], "slug": before_row["slug"],
                    "screenshot_url": before_row["screenshot_url"],
                })

    print(f"\n{'='*70}")
    if clobbered:
        print(f"CLOBBERED — {len(clobbered)} row(s) had screenshot_is_product=1 in the "
              f"snapshot, are now 0, and never made it into app_screenshot_url:\n")
        for r in clobbered:
            print(f"  [{r['table']:11s}] id={r['id']:>4} {r['name']!r} ({r['slug']}) "
                  f"screenshot_url={r['screenshot_url']!r}")
        print("\nThese are exactly the rows scripts/migrate_app_screenshot_from_product_flag.py "
              "will now miss. screenshot_url itself is untouched on all of them (no image/URL "
              "data was lost) — only the flag identifying them as a product shot is gone. If you "
              "want them migrated anyway, re-check the 'This is an actual product screenshot' "
              "flag by hand via the DB (or just re-paste the URL into the app screenshot section "
              "directly on the edit page — same end state, no code change needed).")
    else:
        print("No clobbered rows found — every row flagged in the snapshot is either still "
              "flagged or already has an app_screenshot_url.")
    if missing:
        print(f"\n{len(missing)} row(s) from the snapshot no longer exist in the current DB "
              f"(deleted since the snapshot was taken) — not counted as clobbered:")
        for r in missing:
            print(f"  [{r['table']:11s}] id={r['id']:>4} {r['name']!r}")
    print(f"{'='*70}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
