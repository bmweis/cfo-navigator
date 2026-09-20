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

Respects any existing "Accept as exception" row (``Library.
is_voice_exception``) — re-running this after an admin has already accepted
a specific record+column+rule as a deliberate exception will not re-queue
it. Idempotent for everything else too, on a best-effort basis: it does not
re-insert a finding that's already an ``open`` row for the identical
(table, row_id, column, rule, excerpt) — running this twice against an
unchanged database adds nothing the second time.

Safe by default: preview only (prints every finding that WOULD be queued,
grouped by rule), no writes, unless --apply is passed. Per the standing
write-then-read-back practice, --apply re-queries the table's own count
before and after and prints the delta.

This session has no access to the production database (Railway-volume-
only) — tested only against a local fixture DB. Brian runs this via
`railway ssh` once merged.

Usage:
    python -m scripts.backfill_voice_review_queue --db library.db            # preview
    python -m scripts.backfill_voice_review_queue --db library.db --apply    # write for real
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from linklib.db import Library, resolve_db_path  # noqa: E402
from linklib.voice_db_scan import scan_db_copy_report  # noqa: E402


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
        skipped_exceptions = 0
        would_insert = 0
        for v in report.violations:
            if lib.is_voice_exception(v.table, v.row_id, v.column, v.rule):
                skipped_exceptions += 1
            else:
                would_insert += 1
        print(f"Would insert: {would_insert} (skipping {skipped_exceptions} already-accepted exceptions)")
        return

    before = lib.conn.execute("SELECT COUNT(*) FROM voice_review_queue").fetchone()[0]
    inserted = 0
    for v in report.violations:
        # Best-effort de-dupe against an already-queued identical finding —
        # not a strict UNIQUE constraint (a finding can legitimately recur
        # after being resolved and then reappearing), just avoids piling up
        # duplicate 'open' rows on a repeated run against an unchanged DB.
        existing = lib.conn.execute(
            "SELECT 1 FROM voice_review_queue WHERE table_name=? AND column_name=? AND rule=? "
            "AND excerpt=? AND status='open' AND row_id IS ?",
            (v.table, v.column, v.rule, v.excerpt,
             str(v.row_id) if v.row_id is not None else None),
        ).fetchone()
        if existing:
            continue
        new_id = lib.add_voice_review_item(v.table, v.row_id, v.column, v.rule, v.excerpt)
        if new_id:
            inserted += 1
    after = lib.conn.execute("SELECT COUNT(*) FROM voice_review_queue").fetchone()[0]
    print(f"\nInserted {inserted} rows. Table count: {before} -> {after} "
          f"(verified via read-back: {'OK' if after - before == inserted else 'MISMATCH'})")


if __name__ == "__main__":
    main()
