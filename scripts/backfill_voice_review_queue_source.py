#!/usr/bin/env python3
"""One-off backfill for `voice_review_queue.source` on the two rows that
predate the `source`/trigger taxonomy column (2026-09 seed-sync
infinite-loop investigation — see CLAUDE.md's "Voice review queue" and
`linklib/db.py`'s ALTER TABLE comment for the full column write-up).

Those two rows are the concrete evidence behind the whole investigation:
`_seed_toolbox()` re-syncing `communities.notes` from `scripts/
seed_communities.py`'s raw (pre-fix) spaced-em-dash source text against an
already-voice-fixed DB value, on every process boot — the "Proformative"
and "Finance & Accounting for Bioscience (Informa Connect)" community
notes fields. Both are unambiguously `source='startup-sync'`: the only
writer of `communities.notes` outside a deliberate admin edit is
`_seed_toolbox()`'s per-boot re-sync (see `Library.update_community_content`'s
callers), and both predate the `source` column's own migration, so both
currently read `source=NULL` ("unknown" in the /admin/voice/review-queue
UI) rather than the accurate 'startup-sync'.

This does NOT retroactively fix every row logged before the column
existed — only these two specific, already-identified rows (matched by
table_name='communities', column_name='notes', and the exact excerpt text
each Proformative/Bioscience `_voice_fix` correction produced). A row for
a different table/column with source=NULL stays NULL/"unknown" — this
script makes no general claim about what wrote it.

Safe by default: preview only (prints every matching row's id, table,
column, and current source), no writes, unless --apply is passed.
Write-then-read-back verified per row on --apply, per the standing
one-off-admin-fix discipline (CLAUDE.md's "One-off admin fixes against
the database"). Idempotent — a row already set to 'startup-sync' is
reported as already-correct and skipped, so a second run changes nothing.

NOT run against library.db or any production database as part of building
this — verified only against a temp scratch SQLite DB. Running --apply
against the real database is reserved for Brian, via `railway ssh`.

Usage:
    python -m scripts.backfill_voice_review_queue_source --db library.db            # preview
    python -m scripts.backfill_voice_review_queue_source --db library.db --apply    # write for real
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from linklib.db import Library, resolve_db_path  # noqa: E402

# The two specific rows this script targets — matched on
# (table_name, column_name, a substring of the logged excerpt), not a
# blanket "every communities.notes row with source IS NULL" sweep, since
# a future genuinely-unrelated communities.notes row could also have
# source=NULL for a different, legitimate reason (e.g. logged by a
# different mechanism before this column existed).
_TARGETS = (
    ("communities", "notes", "No longer an independent community"),
    ("communities", "notes", "Annual event, part of Biotech Week Boston"),
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=None, help="Path to library.db")
    ap.add_argument("--apply", action="store_true", help="Write for real (default: preview only)")
    args = ap.parse_args()

    db_path = resolve_db_path(args.db)
    lib = Library(db_path)
    try:
        rows = lib.list_voice_review_queue()
        matches = []
        for row in rows:
            for table, column, needle in _TARGETS:
                if (row["table_name"] == table and row["column_name"] == column
                        and needle in (row["excerpt"] or "")):
                    matches.append(row)
                    break

        if not matches:
            print("No matching rows found — nothing to do.")
            return 0

        print(f"Found {len(matches)} matching row(s):\n")
        to_apply = []
        for row in matches:
            current = row.get("source")
            print(f"  id={row['id']} {row['table_name']}.{row['column_name']} "
                  f"source={current!r} excerpt={row['excerpt'][:70]!r}")
            if current == "startup-sync":
                print("    SKIP — already 'startup-sync'")
            else:
                to_apply.append(row)

        if not to_apply:
            print("\nNothing to change — every matching row is already 'startup-sync'.")
            return 0

        if not args.apply:
            print(f"\nPREVIEW ONLY — {len(to_apply)} row(s) would be set to source='startup-sync'. "
                  "Re-run with --apply to write for real.")
            return 0

        for row in to_apply:
            lib.conn.execute(
                "UPDATE voice_review_queue SET source=? WHERE id=?",
                ("startup-sync", row["id"]),
            )
        lib.conn.commit()
        print(f"\nApplied — updated {len(to_apply)} row(s).\n")

        # Write-then-read-back.
        ok = True
        for row in to_apply:
            after = lib.conn.execute(
                "SELECT source FROM voice_review_queue WHERE id=?", (row["id"],)
            ).fetchone()
            actual = after["source"] if after else None
            status = "OK" if actual == "startup-sync" else "MISMATCH"
            if status != "OK":
                ok = False
            print(f"  id={row['id']}: source={actual!r} [{status}]")
        return 0 if ok else 1
    finally:
        lib.close()


if __name__ == "__main__":
    raise SystemExit(main())
