#!/usr/bin/env python3
"""One-off backfill for `voice_review_queue.source` on rows that predate
the `source`/trigger taxonomy column (2026-09 seed-sync infinite-loop
investigation — see CLAUDE.md's "Voice review queue" and `linklib/db.py`'s
ALTER TABLE comment for the full column write-up).

Two independent, separately-reasoned target sets, both matched on real
evidence rather than a blanket "everything NULL is X" guess:

**Phase 1 — two specific, hand-identified `auto_corrected` rows.**
`_seed_toolbox()` re-syncing `communities.notes` from `scripts/
seed_communities.py`'s raw (pre-fix) spaced-em-dash source text against an
already-voice-fixed DB value, on every process boot — the "Proformative"
and "Finance & Accounting for Bioscience (Informa Connect)" community
notes fields. Both are unambiguously `source='startup-sync'`: the only
writer of `communities.notes` outside a deliberate admin edit is
`_seed_toolbox()`'s per-boot re-sync (see `Library.update_community_content`'s
callers), and both predate the `source` column's own migration. Matched
by `table_name='communities'`, `column_name='notes'`, and the exact
excerpt text each correction produced — never a blanket
"every communities.notes row with source IS NULL" sweep, since a
different communities.notes row could have source=NULL for a genuinely
different, legitimate reason.

**Phase 2 — every legacy `open` row with `source IS NULL`, tagged
'script'.** This one IS a blanket sweep, but a well-founded one, not a
guess: `Library.add_voice_review_item` is the sole method that has ever
created an `open`-status row (`add_seed_disagreement_item`, the only other
creator of `open` rows, is brand-new in this same PR and always sets a
real `source` value, so it can never be the cause of a NULL one), and
`add_voice_review_item`'s own `source` parameter has defaulted to
`"script"` since it was introduced — `scripts/backfill_voice_review_queue.py`
(its only caller before `reconcile_voice_review_queue()` existed) has
never passed a different value. So any `open` row still showing
`source IS NULL` must predate the `source` column's own migration, and
must have come from that same backfill script — there is no other code
path it could have come from. This phase deliberately does NOT touch any
`auto_corrected` row with `source IS NULL` outside Phase 1's two exact
matches — an auto-corrected row's origin (an admin edit route, a script, or
`_seed_toolbox()`'s startup sync, each of which threads its own real
`source` value today) is genuinely ambiguous for a pre-migration row, so
guessing at it would violate this repo's own "never a blanket sweep on an
ambiguous case" rule.

Safe by default: preview only (prints every matching row's id, table,
column, and current source), no writes, unless --apply is passed.
Write-then-read-back verified per row on --apply, per the standing
one-off-admin-fix discipline (CLAUDE.md's "One-off admin fixes against
the database"). Idempotent — a row already set to the target source is
reported as already-correct and skipped, so a second run changes nothing.

NOT run against library.db or any production database as part of building
this — verified only against a temp scratch SQLite DB. Running --apply
against the real database is reserved for Brian, via `railway ssh`.

Usage:
    python -m scripts.backfill_voice_review_queue_source --db /data/library.db            # preview
    python -m scripts.backfill_voice_review_queue_source --db /data/library.db --apply    # write for real
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from linklib.db import Library, resolve_db_path  # noqa: E402

# Phase 1 — the two specific rows this script targets — matched on
# (table_name, column_name, a substring of the logged excerpt), not a
# blanket "every communities.notes row with source IS NULL" sweep, since
# a future genuinely-unrelated communities.notes row could also have
# source=NULL for a different, legitimate reason (e.g. logged by a
# different mechanism before this column existed).
_TARGETS = (
    ("communities", "notes", "No longer an independent community"),
    ("communities", "notes", "Annual event, part of Biotech Week Boston"),
)


def _apply_and_verify(lib: Library, to_apply: list[dict], target_source: str) -> bool:
    """Shared write-then-read-back for one phase's set of rows. Returns
    True iff every row verified correctly after the write."""
    for row in to_apply:
        lib.conn.execute(
            "UPDATE voice_review_queue SET source=? WHERE id=?",
            (target_source, row["id"]),
        )
    lib.conn.commit()
    print(f"\nApplied — updated {len(to_apply)} row(s).\n")

    ok = True
    for row in to_apply:
        after = lib.conn.execute(
            "SELECT source FROM voice_review_queue WHERE id=?", (row["id"],)
        ).fetchone()
        actual = after["source"] if after else None
        status = "OK" if actual == target_source else "MISMATCH"
        if status != "OK":
            ok = False
        print(f"  id={row['id']}: source={actual!r} [{status}]")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=None, help="Path to library.db")
    ap.add_argument("--apply", action="store_true", help="Write for real (default: preview only)")
    args = ap.parse_args()

    db_path = resolve_db_path(args.db)
    lib = Library(db_path)
    try:
        rows = lib.list_voice_review_queue()
        overall_ok = True
        anything_to_apply = False

        # --- Phase 1: the two specific hand-identified auto_corrected rows ---
        print("=== Phase 1: two specific communities.notes rows -> 'startup-sync' ===\n")
        matches = []
        for row in rows:
            for table, column, needle in _TARGETS:
                if (row["table_name"] == table and row["column_name"] == column
                        and needle in (row["excerpt"] or "")):
                    matches.append(row)
                    break

        if not matches:
            print("No matching rows found — nothing to do.\n")
        else:
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
                print("\nNothing to change — every matching row is already 'startup-sync'.\n")
            elif not args.apply:
                anything_to_apply = True
                print(f"\nPREVIEW ONLY — {len(to_apply)} row(s) would be set to source='startup-sync'.\n")
            else:
                anything_to_apply = True
                if not _apply_and_verify(lib, to_apply, "startup-sync"):
                    overall_ok = False

        # --- Phase 2: every legacy open row with source IS NULL -> 'script' ---
        print("=== Phase 2: legacy open rows with source IS NULL -> 'script' ===\n")
        rows = lib.list_voice_review_queue()  # re-fetch — Phase 1 may have written
        legacy_open = [r for r in rows if r["status"] == "open" and r.get("source") is None]

        if not legacy_open:
            print("No matching rows found — nothing to do.\n")
        else:
            print(f"Found {len(legacy_open)} matching row(s):\n")
            for row in legacy_open:
                print(f"  id={row['id']} {row['table_name']}.{row['column_name']} "
                      f"rule={row['rule']!r} excerpt={(row['excerpt'] or '')[:70]!r}")

            if not args.apply:
                anything_to_apply = True
                print(f"\nPREVIEW ONLY — {len(legacy_open)} row(s) would be set to source='script'.\n")
            else:
                anything_to_apply = True
                if not _apply_and_verify(lib, legacy_open, "script"):
                    overall_ok = False

        if not args.apply and anything_to_apply:
            print("Re-run with --apply to write for real.")
        elif not anything_to_apply:
            print("Nothing to do in either phase.")

        return 0 if overall_ok else 1
    finally:
        lib.close()


if __name__ == "__main__":
    raise SystemExit(main())
