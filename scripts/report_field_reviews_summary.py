#!/usr/bin/env python3
"""Read-only diagnostic for the Phase G PR 2 investigation: how much history
actually lives in `field_reviews` before deciding whether it's worth
backfilling into `narrative_review_log` as part of retiring it (see
CLAUDE.md's Phase G note). Makes NO writes — same safe-by-default shape as
scripts/report_orphaned_categories.py, no --apply flag because there's
nothing to apply.

Reports: total row count, the reviewed_at date range, a breakdown by
field_name, and a breakdown by entity_type — everything needed to judge
whether a backfill is worth building versus just letting the table go
frozen as of the migration date, the way screenshot_is_product did.

Usage (run against production via `railway ssh`, per CLAUDE.md's "One-off
admin fixes against the database" convention):
    python -m scripts.report_field_reviews_summary [--db library.db]
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from linklib.db import Library, resolve_db_path


def main():
    parser = argparse.ArgumentParser(
        description="Report field_reviews row count, date range, and breakdowns. Read-only."
    )
    parser.add_argument("--db", default=None, help="Path to library.db (or set LINKLIB_DB)")
    args = parser.parse_args()
    args.db = resolve_db_path(args.db, allow_missing=False)
    print(f"Reading from: {args.db}\n")

    lib = Library(args.db)
    try:
        total = lib.conn.execute("SELECT COUNT(*) FROM field_reviews").fetchone()[0]
        date_range = lib.conn.execute(
            "SELECT MIN(reviewed_at), MAX(reviewed_at) FROM field_reviews"
        ).fetchone()
        by_field = lib.conn.execute(
            "SELECT field_name, COUNT(*) AS n, MIN(reviewed_at) AS earliest, "
            "MAX(reviewed_at) AS latest FROM field_reviews "
            "GROUP BY field_name ORDER BY n DESC"
        ).fetchall()
        by_entity = lib.conn.execute(
            "SELECT entity_type, COUNT(*) AS n FROM field_reviews "
            "GROUP BY entity_type ORDER BY n DESC"
        ).fetchall()
        by_reviewer = lib.conn.execute(
            "SELECT reviewed_by, COUNT(*) AS n FROM field_reviews "
            "GROUP BY reviewed_by ORDER BY n DESC"
        ).fetchall()
    finally:
        lib.close()

    print(f"Total rows: {total}")
    print(f"Date range: {date_range[0] or '(none)'}  ->  {date_range[1] or '(none)'}\n")

    print("By field_name:")
    for r in by_field:
        print(f"  {r['field_name']:<30} {r['n']:>4}   earliest={r['earliest']}   latest={r['latest']}")
    print()

    print("By entity_type:")
    for r in by_entity:
        print(f"  {r['entity_type']:<12} {r['n']:>4}")
    print()

    print("By reviewed_by:")
    for r in by_reviewer:
        print(f"  {r['reviewed_by'] or '(blank)':<20} {r['n']:>4}")
    print()

    print(
        "Read this alongside the Phase G PR 2 investigation question: is a\n"
        "backfill into narrative_review_log worth building, or is the row\n"
        "count/date range small enough (and the semantics mismatch — saved-\n"
        "after-Generate, not a deliberate confirm click — real enough) that\n"
        "field_reviews should just go frozen as of the cutover, the same way\n"
        "screenshot_is_product did."
    )


if __name__ == "__main__":
    main()
