#!/usr/bin/env python3
"""One-off backfill for the 2026-09 voice review queue: runs
``linklib.voice_db_scan.scan_db_copy_report`` (the same live DB scan that
powers ``/admin/checks``' "Database-backed copy" section) and inserts each
finding as an ``open`` row in ``voice_review_queue``, so the queue launches
populated rather than empty.

This is backfillable, unlike the ``auto_corrected`` log — that only starts
recording from the moment the per-write-path logging shipped (see
``Library.log_voice_correction``/``Library._vf`` in ``linklib/db.py``); a
scanner finding, by contrast, is a live fact about content that's already
sitting in the database right now, so it can be discovered and queued
retroactively.

Idempotent against a repeated run, checked TWO ways, reported separately:

- ``Library.is_voice_exception`` — a specific (table, row_id, column, rule)
  an admin has explicitly marked "Accept as exception" (permanent,
  ``status='exception'``).
- ``Library.has_open_voice_review_item`` — a specific (table, row_id,
  column, rule) that already has an OPEN row waiting for review.

**2026-09 fix — a real double-insert bug, found in production.** This
script originally checked only the first of those two. A DB scan is a
re-proposal machine: every finding still present in the database gets
reported again on every run, whether or not it's already sitting in the
queue waiting for a human to look at it — only an *exception* row (or a
row a human resolved) makes a finding stop being reported by the scanner
at all. So a re-run after ANY unrelated scanner change (a new mechanical
rule added, a new table added to ``_SCAN_TABLES``, ...) reported every
already-queued finding as if it were brand new, and this script's own
preview counted every one of them as "would insert" with a misleading
"skipping 0 already-accepted exceptions" line — technically true, and
useless, since it implied full dedupe coverage it didn't have.

Concretely: an initial run queued 33 findings. The invisible-character
mechanical rule shipped. A second ``--preview`` run reported 33 findings
again — 32 were re-proposals of findings ALREADY IN THE QUEUE as open rows
from the first run, plus 1 genuinely new finding (the invisible-character
rule's own). Running ``--apply`` blind at that point would have produced
65 rows, 32 of them duplicates of already-open review items. The only
reason this wasn't a silent duplication was the preview count being read
carefully — not a real control.

Fixed at the `Library` layer (`has_open_voice_review_item`, `linklib/
db.py`), not just here: an existing OPEN row now blocks a duplicate
insert regardless of which caller adds it, and this script reports the
two skip reasons — "already queued" vs. "already accepted" — separately
in both preview and apply, since they're genuinely different reasons and
conflating them is what made the misleading report possible in the first
place. Matching intentionally ignores the excerpt text: an open row at
the same (table, row_id, column, rule) is the same review item even if a
re-scan's excerpt differs slightly, so it's still correctly reported as
"already queued," not re-inserted as new.

Safe by default: preview only (prints every finding that WOULD be queued,
grouped by rule), no writes, unless --apply is passed. Per the standing
write-then-read-back practice, --apply re-queries the table's own count
before and after and prints the delta.

This session has no access to the production database (Railway-volume-
only) — tested only against a local fixture DB. Brian runs this via
`railway ssh` once merged.

Usage:
    python -m scripts.backfill_voice_review_queue --db /data/library.db            # preview
    python -m scripts.backfill_voice_review_queue --db /data/library.db --apply    # write for real
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from linklib.db import Library, resolve_db_path  # noqa: E402
from linklib.voice_db_scan import DbCopyViolation, scan_db_copy_report  # noqa: E402


def skip_reason(lib: Library, v: DbCopyViolation) -> str | None:
    """Why `v` would be skipped, if at all — checked in this order because
    "already queued" is the common, expected case on a repeated run against
    unchanged content, and "already accepted" is the deliberate, permanent
    override. Returns None when `v` is a genuinely new finding that would be
    inserted."""
    if lib.has_open_voice_review_item(v.table, v.row_id, v.column, v.rule):
        return "queued"
    if lib.is_voice_exception(v.table, v.row_id, v.column, v.rule):
        return "accepted"
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", help="Path to library.db (falls back to LINKLIB_DB)")
    ap.add_argument("--apply", action="store_true", help="Write for real (default: preview only)")
    args = ap.parse_args()

    db_path = resolve_db_path(args.db)
    print(f"Using database: {db_path}")
    lib = Library(db_path)

    report = scan_db_copy_report(lib)
    print(f"Scanned {len(report.tables_checked)} tables, "
          f"{report.settings_checked} settings keys, {report.rows_checked} rows.")
    if report.tables_skipped:
        print(f"Skipped tables (missing/error): {report.tables_skipped}")
    print(f"Found {len(report.violations)} violations.")

    by_rule: dict[str, int] = {}
    for v in report.violations:
        by_rule[v.rule] = by_rule.get(v.rule, 0) + 1
    for rule, n in sorted(by_rule.items(), key=lambda kv: -kv[1]):
        print(f"  {rule}: {n}")

    if not args.apply:
        print("\nPreview only — pass --apply to write these as 'open' review-queue rows.")
        skipped_queued = 0
        skipped_accepted = 0
        would_insert = 0
        for v in report.violations:
            reason = skip_reason(lib, v)
            if reason == "queued":
                skipped_queued += 1
            elif reason == "accepted":
                skipped_accepted += 1
            else:
                would_insert += 1
        print(f"Would insert: {would_insert} "
              f"(skipping {skipped_queued} already queued, {skipped_accepted} already accepted)")
        return

    before = lib.conn.execute("SELECT COUNT(*) FROM voice_review_queue").fetchone()[0]
    inserted = 0
    skipped_queued = 0
    skipped_accepted = 0
    for v in report.violations:
        reason = skip_reason(lib, v)
        if reason == "queued":
            skipped_queued += 1
            continue
        if reason == "accepted":
            skipped_accepted += 1
            continue
        new_id = lib.add_voice_review_item(v.table, v.row_id, v.column, v.rule, v.excerpt)
        if new_id:
            inserted += 1
    after = lib.conn.execute("SELECT COUNT(*) FROM voice_review_queue").fetchone()[0]
    print(f"\nInserted {inserted} rows "
          f"(skipped {skipped_queued} already queued, {skipped_accepted} already accepted). "
          f"Table count: {before} -> {after} "
          f"(verified via read-back: {'OK' if after - before == inserted else 'MISMATCH'})")


if __name__ == "__main__":
    main()
