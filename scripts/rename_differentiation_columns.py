#!/usr/bin/env python3
"""Phase P (edit-page layout reorg): renames `tools.differentiation_note` to
`tools.competitive_differentiation`, and its companion
`tools.differentiation_needs_verification` to
`tools.competitive_differentiation_needs_verification` — matching the
Software edit page's new "Competition" section, which relabels the field
"Competitive differentiation" (was "How this differs from the competition").
See CLAUDE.md's Phase P note and this PR's description for the full Part 0
investigation and naming rationale (including why the `tool_competitors`/
`community_competitors` join tables are NOT renamed — a proportionality call,
not an oversight).

RELATIONSHIP TO THE AUTOMATIC BOOT MIGRATION: `linklib/db.py`'s migration
list already includes two `ALTER TABLE tools RENAME COLUMN` lines that
perform this exact rename automatically, the same idempotent way every other
schema change in this codebase applies — any process that constructs a
`Library(...)` (the app on boot, any script that opens one) will run it.
This script is NOT a substitute for that — it's a narrower, manual tool for
two situations the automatic path doesn't cover:
  1. Pre-migrating a database's schema via `railway ssh` BEFORE deploying the
     new code, so there's no window where new code (which reads/writes
     `competitive_differentiation`) meets an old-schema database mid-deploy.
  2. Migrating/verifying a standalone copy of `library.db` (e.g. a downloaded
     backup snapshot) without booting the whole app.
Deliberately opens a raw sqlite3 connection rather than `Library(...)`, so it
does NOT also run every other unrelated migration in that list — it touches
only these two columns, nothing else.

Safe by default: preview only, no writes, unless --apply is passed. Per the
standing one-off-fix write-then-read-back practice, an --apply run re-reads
`PRAGMA table_info(tools)` afterward and asserts both new column names are
present and both old names are gone before reporting success.

Idempotent: if the columns have already been renamed (by this script or by
the automatic boot migration), --apply is a no-op that reports "already
renamed" rather than erroring.

NOT run with --apply against library.db or any production database as part
of building this PR — tested only against a throwaway scratch SQLite DB
created for that purpose. Running --apply against the real database is
reserved for Brian, via `railway ssh`.

Usage:
    python -m scripts.rename_differentiation_columns --db library.db            # preview
    python -m scripts.rename_differentiation_columns --db library.db --apply    # write for real
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from linklib.db import resolve_db_path

_OLD_COL = "differentiation_note"
_NEW_COL = "competitive_differentiation"
_OLD_FLAG_COL = "differentiation_needs_verification"
_NEW_FLAG_COL = "competitive_differentiation_needs_verification"


def _tool_columns(conn: sqlite3.Connection) -> set[str]:
    return {row[1] for row in conn.execute("PRAGMA table_info(tools)").fetchall()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None, help="Path to library.db (or set LINKLIB_DB)")
    ap.add_argument("--apply", action="store_true",
                     help="Actually rename the columns. Without this flag, only a preview "
                          "is printed — no DB writes.")
    args = ap.parse_args()
    args.db = resolve_db_path(args.db, allow_missing=False)
    print(f"Target database: {args.db}\n")

    conn = sqlite3.connect(args.db)
    try:
        cols = _tool_columns(conn)
        already_done = _NEW_COL in cols and _NEW_FLAG_COL in cols
        still_old = _OLD_COL in cols and _OLD_FLAG_COL in cols

        if already_done and not still_old:
            print(f"Already renamed — `tools` has `{_NEW_COL}` and `{_NEW_FLAG_COL}`, "
                  f"no `{_OLD_COL}`/`{_OLD_FLAG_COL}`. Nothing to do.")
            return 0
        if not still_old:
            print(f"ERROR: `tools` has neither the old columns ({_OLD_COL}/{_OLD_FLAG_COL}) "
                  f"nor a clean renamed pair ({_NEW_COL}/{_NEW_FLAG_COL}) — unexpected schema "
                  f"state, refusing to guess. Current tools columns: {sorted(cols)}",
                  file=sys.stderr)
            return 1

        row_count = conn.execute("SELECT COUNT(*) FROM tools").fetchone()[0]
        non_empty = conn.execute(
            f"SELECT COUNT(*) FROM tools WHERE {_OLD_COL} != ''"
        ).fetchone()[0]
        print(f"Would rename on `tools` ({row_count} row(s), {non_empty} with a non-empty value):")
        print(f"  {_OLD_COL} -> {_NEW_COL}")
        print(f"  {_OLD_FLAG_COL} -> {_NEW_FLAG_COL}")

        if not args.apply:
            print("\nPREVIEW ONLY — no DB writes. Re-run with --apply to write for real.")
            return 0

        # Snapshot a few rows' old values before the rename, purely so the
        # write-then-read-back check below has something concrete to compare
        # against (RENAME COLUMN preserves data by definition, but verify
        # anyway rather than assume).
        before = conn.execute(
            f"SELECT id, {_OLD_COL}, {_OLD_FLAG_COL} FROM tools WHERE {_OLD_COL} != '' LIMIT 5"
        ).fetchall()

        conn.execute(f"ALTER TABLE tools RENAME COLUMN {_OLD_COL} TO {_NEW_COL}")
        conn.execute(f"ALTER TABLE tools RENAME COLUMN {_OLD_FLAG_COL} TO {_NEW_FLAG_COL}")
        conn.commit()
        print("\nApplied — renamed both columns on `tools`.\n")

        # Write-then-read-back: re-read the schema and a sample of rows,
        # assert the rename actually landed and no data moved/vanished.
        cols_after = _tool_columns(conn)
        ok = (_NEW_COL in cols_after and _NEW_FLAG_COL in cols_after
              and _OLD_COL not in cols_after and _OLD_FLAG_COL not in cols_after)
        if not ok:
            print(f"ERROR: schema after ALTER does not match expectations: {sorted(cols_after)}",
                  file=sys.stderr)
            return 1
        for tool_id, old_val, old_flag in before:
            row = conn.execute(
                f"SELECT {_NEW_COL}, {_NEW_FLAG_COL} FROM tools WHERE id=?", (tool_id,)
            ).fetchone()
            assert row is not None, f"tools id={tool_id} vanished after rename"
            new_val, new_flag = row
            if new_val != old_val or new_flag != old_flag:
                ok = False
                print(f"  MISMATCH: tools id={tool_id} — expected {old_val!r}/{old_flag!r}, "
                      f"got {new_val!r}/{new_flag!r}", file=sys.stderr)
            else:
                print(f"  confirmed: tools id={tool_id} -> {_NEW_COL}={new_val!r}, "
                      f"{_NEW_FLAG_COL}={new_flag!r}")
        if not ok:
            print("\nERROR: one or more rows did not verify after the rename — see MISMATCH lines above.",
                  file=sys.stderr)
            return 1
        print(f"\nVerified {len(before)} sample row(s) plus the schema. Rename complete.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
