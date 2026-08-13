#!/usr/bin/env python3
"""One-time data migration (Thought Leadership Admin CRUD, Phase 1): moves the
33 entries hardcoded in `webapp/thought_leadership_data.py` (WRITING,
SPEAKING, PODCASTS, PRESS) into the new `thought_leadership` DB table, which
`/thought-leadership` and its admin CRUD (`/admin/thought-leadership`) now
read from instead. See CLAUDE.md's Phase 1 entry and
`linklib/db.py`'s `thought_leadership` table comment for the full reasoning.

Deliberately a manual, run-by-hand script — NOT wired into an automatic boot
hook. This is a production DATA write, not a schema/column backfill, and the
standing rule (CLAUDE.md, "One-off admin fixes against the database")
requires Brian's review of the affected rows BEFORE a production write, not
an after-the-fact deploy-log line. Safe by default (preview only, no writes)
— same --apply convention as scripts/backfill_logos.py and
scripts/archive/migrate_app_screenshot_from_product_flag.py.

Excludes the one Speaking & Events entry with photos (Abacum AI Summit) —
that entry stays hardcoded in webapp/app.py's `_TL_PHOTO_ENTRY` instead,
since the new table has no photos column (a single-use field not worth
migrating — see CLAUDE.md). 32 rows are migrated, not 33.

Idempotent: guarded by an empty `thought_leadership` table — if the table
already has any rows, the script reports that and does nothing, rather than
risking duplicate inserts on a second run. (There's no natural unique key
across (type, title, url) worth enforcing at the schema level — a title can
legitimately repeat across a rename — so idempotency is enforced at the
"table already seeded" level instead.)

Per the write-then-read-back standing practice, an --apply run re-lists the
table afterward and asserts the row count and a few spot-checked fields
match what was migrated.

Usage:
    python -m scripts.archive.migrate_thought_leadership --db library.db            # preview
    python -m scripts.archive.migrate_thought_leadership --db library.db --apply     # write for real
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from linklib.db import Library, resolve_db_path
from webapp.thought_leadership_data import SECTIONS


def _planned_rows() -> list[dict]:
    """Flatten SECTIONS into insert-ready dicts, in the display_order each
    entry has today (its index within its own type's list) — see
    `Library.list_thought_leadership`'s ordering note for why that matters."""
    planned = []
    for _title, _emoji, items in SECTIONS:
        for i, it in enumerate(items):
            if it.photos:
                continue  # the one Abacum entry — excluded, see module docstring
            planned.append({
                "type": it.type,
                "title": it.title,
                "url": it.url,
                "venue": it.venue,
                "date_label": it.date_label,
                "sort_key": it.sort_key,
                "description": it.description,
                "needs_synopsis": it.needs_synopsis,
                "display_order": i,
            })
    return planned


def _print_rows(rows: list[dict]) -> None:
    for r in rows:
        print(f"  [{r['type']:8s}] order={r['display_order']:>2} {r['title']!r} "
              f"({r['sort_key'] or 'undated'})")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None, help="Path to library.db (or set LINKLIB_DB)")
    ap.add_argument("--apply", action="store_true",
                     help="Actually write the migration. Without this flag, only a preview "
                          "is printed — no DB writes.")
    args = ap.parse_args()
    args.db = resolve_db_path(args.db, allow_missing=False)
    print(f"Reading from: {args.db}\n")

    lib = Library(args.db)
    try:
        existing = lib.list_thought_leadership()
        if existing:
            print(f"thought_leadership already has {len(existing)} row(s) — nothing to do. "
                  "(Idempotency guard: this script only ever runs against an empty table.)")
            return 0

        planned = _planned_rows()
        by_type = {}
        for r in planned:
            by_type[r["type"]] = by_type.get(r["type"], 0) + 1
        print(f"{len(planned)} row(s) would be inserted "
              f"({', '.join(f'{n} {t}' for t, n in by_type.items())}):\n")
        _print_rows(planned)

        if not args.apply:
            print(
                "\nPREVIEW ONLY — no DB writes. This is exactly the row list --apply would "
                "insert, in the same order. Re-run with --apply to write for real."
            )
            return 0

        for r in planned:
            lib.add_thought_leadership(r["type"], r["title"], r["url"], r["venue"], r["date_label"],
                                       r["sort_key"], r["description"], r["needs_synopsis"], r["display_order"])
        print(f"\nApplied — {len(planned)} row(s) inserted.\n")

        # Write-then-read-back: re-list the table and confirm the row count
        # and a spot-check of the first/last planned row match.
        after = lib.list_thought_leadership()
        assert len(after) == len(planned), f"expected {len(planned)} rows, found {len(after)}"
        by_title = {r["title"]: r for r in after}
        for spot in (planned[0], planned[-1]):
            fresh = by_title.get(spot["title"])
            assert fresh is not None, f"{spot['title']!r} missing after migration"
            assert fresh["type"] == spot["type"] and fresh["sort_key"] == spot["sort_key"], \
                f"{spot['title']!r} landed with mismatched fields"
        print(f"Verified — {len(after)} row(s) read back from thought_leadership, "
              "spot-checked fields match.")
        return 0
    finally:
        lib.close()


if __name__ == "__main__":
    raise SystemExit(main())
