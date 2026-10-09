#!/usr/bin/env python3
"""One-time, human-run drop of the legacy `tool_features` table (Feature
Taxonomy Phase 1b PR 2 — see CLAUDE.md/ARCHITECTURE.md's "Legacy tool_features
retirement" note). By the time this script is meant to run, PR 2 has already
deployed and removed every live code path that reads or writes
`tool_features` — this script performs the actual data deletion the standing
"no dead data" rule (CLAUDE.md) calls for, against a table that's already
been fully retired from code, not a table still in active use.

Deliberately NOT wired into any boot hook, migration list, or deploy step —
Brian runs this by hand via `railway ssh` after PR 2 is live and verified in
production, per the standing "human review + confirmed backup before a
destructive production write" rule. Sequence, matching the build brief
exactly:

  1. Prints the live `tool_features` row count.
  2. Requires TYPING that exact row count back to confirm — not a bare
     y/n — so a stale or misremembered expectation can't slip through.
  3. Shows the most recent `backup_log` entry (from `/admin/library-backup`'s
     own log) and requires a separate typed confirmation that a same-day
     successful backup exists before proceeding — shown as a courtesy, not
     trusted blindly: the typed confirmation is still required even if the
     log entry looks stale or missing.
  4. Drops the table. `idx_tool_features_tool` is dropped explicitly first
     rather than relying on SQLite's implicit "drop the table's indexes too"
     behavior, so the log is self-documenting about exactly what left the
     schema. `tool_features` was never part of the `articles_fts` FTS5
     virtual table or any other FTS artifact — nothing FTS-side to clean up.
  5. Write-then-read-back verification (house rule): re-queries
     `sqlite_master` immediately after and asserts the table is actually
     gone.
  6. Runs `PRAGMA integrity_check` (via `linklib.backup.check_integrity`,
     the same mechanism the daily backup runs) against the live DB and
     prints the result — the standing post-destructive-write verification,
     also logged to `integrity_check_log` so it shows up on
     `/admin/library-backup` like any other check.

No --apply/dry-run flag, unlike most scripts/archive/ migrations: this
script IS the confirmation flow — there's nothing meaningfully different a
"preview" mode would show beyond the row count and prompts every run already
prints before writing anything.

Usage:
    python -m scripts.drop_legacy_tool_features --db /data/library.db
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from linklib.db import Library, resolve_db_path
from linklib import backup as backup_mod


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None,
                     help="Path to library.db (or set LINKLIB_DB) — use the absolute "
                          "production path, e.g. /data/library.db, not a bare filename.")
    args = ap.parse_args()
    db_path = resolve_db_path(args.db, allow_missing=False)
    print(f"Resolved DB path: {db_path}\n")

    lib = Library(db_path)
    try:
        exists = lib.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='tool_features'"
        ).fetchone()
        if not exists:
            print("tool_features doesn't exist in this database — nothing to do "
                  "(already dropped?).")
            return 0

        count = lib.conn.execute("SELECT COUNT(*) FROM tool_features").fetchone()[0]
        print(f"tool_features currently has {count} row(s).\n")

        typed_count = input(
            f"This will PERMANENTLY drop tool_features and its {count} row(s). "
            f"Type the row count ({count}) to confirm: "
        ).strip()
        if typed_count != str(count):
            print(f"Aborted — typed {typed_count!r}, expected {count!r}.")
            return 1

        recent = lib.list_backup_log(limit=1)
        if recent:
            r = recent[0]
            err_note = f" — error: {r['error']}" if r["error"] else ""
            print(f"\nMost recent backup_log entry: {r['status']} at {r['created_at']} "
                  f"({r['row_count']} rows, {r['bytes']} bytes){err_note}")
        else:
            print("\nNo backup_log entries found at all.")

        confirm_backup = input(
            "Confirm a SAME-DAY successful backup of this database exists before "
            "continuing (check /admin/library-backup if unsure). Type 'yes' to confirm: "
        ).strip()
        if confirm_backup.lower() != "yes":
            print("Aborted — no confirmed same-day backup.")
            return 1

        print("\nDropping idx_tool_features_tool...")
        lib.conn.execute("DROP INDEX IF EXISTS idx_tool_features_tool")
        print("Dropping tool_features...")
        lib.conn.execute("DROP TABLE tool_features")
        lib.conn.commit()

        # Write-then-read-back verification, per house rules — even for a
        # DROP, not just an UPDATE/INSERT.
        still_there = lib.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='tool_features'"
        ).fetchone()
        assert still_there is None, "tool_features still present after DROP TABLE — did not take effect."
        print("Verified: tool_features no longer exists in sqlite_master.\n")

        print("Running PRAGMA integrity_check...")
        result = backup_mod.check_integrity(db_path)
        status = "ok" if result["ok"] else "failure"
        lib.record_integrity_check(status, result["detail"])
        print(f"Integrity check: {'OK' if result['ok'] else 'FAILED'} — {result['detail']}")
        if not result["ok"]:
            print("\n⚠️  Integrity check FAILED after the drop — investigate immediately; "
                  "restore from the confirmed backup above if needed.")
            return 1
    finally:
        lib.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
