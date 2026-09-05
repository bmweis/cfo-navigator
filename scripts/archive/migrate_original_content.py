#!/usr/bin/env python3
"""One-time data migration (Original Content Phase 1): moves the 3 flagship
pieces hardcoded in `webapp/app.py`'s `_TL_FEATURED_CARDS` tuple into the new
`original_content` DB table, which the homepage's flagship row and
/thought-leadership's featured row now read from instead. See CLAUDE.md's
Original Content Phase 1 entry and `linklib/db.py`'s `original_content` table
comment for the full reasoning.

Deliberately a manual, run-by-hand script — NOT wired into an automatic boot
hook. This is a production DATA write, not a schema/column backfill, and the
standing rule (CLAUDE.md, "One-off admin fixes against the database")
requires Brian's review of the affected rows BEFORE a production write, not
an after-the-fact deploy-log line. Safe by default (preview only, no writes)
— same --apply convention as scripts/archive/migrate_thought_leadership.py.

Migrated losslessly: each card's href supplies both the slug (its last path
segment — deliberately identical to the piece's existing bespoke route, so
the literal /thought-leadership/{growth-engine-ratio,ai-hackathon-playbook,
netsuite-mcp} routes always win over the generic GET /thought-leadership/{slug}
catch-all by registration order) and confirms body_md stays NULL (card
metadata only — the three bespoke pages keep rendering the actual piece).
status='live', featured_home=1 (all three keep showing on the homepage,
matching pre-migration behavior), display_order preserves the tuple's
original order. tag_color isn't a stored column (see the schema comment) —
webapp/app.py derives it by cycling the same 3 established colors by
position, so the first 3 migrated pieces render with their exact original
colors with no data needed for it.

Idempotent: guarded by an empty `original_content` table — if the table
already has any rows, the script reports that and does nothing, rather than
risking duplicate inserts (or a slug collision) on a second run.

Per the write-then-read-back standing practice, an --apply run re-lists the
table afterward and asserts the row count and a few spot-checked fields
match what was migrated.

Usage:
    python -m scripts.migrate_original_content --db library.db            # preview
    python -m scripts.migrate_original_content --db library.db --apply     # write for real
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from linklib.db import Library, resolve_db_path
from webapp.app import _TL_FEATURED_CARDS


def planned_rows() -> list[dict]:
    """Flatten _TL_FEATURED_CARDS into insert-ready dicts, in the tuple's own
    order (display_order = its index). Shared with tests that need the same
    seed data a real migration run would produce."""
    planned = []
    for i, (href, tag, _tag_color, title, desc, cta) in enumerate(_TL_FEATURED_CARDS):
        slug = href.rstrip("/").rsplit("/", 1)[-1]
        planned.append({
            "slug": slug,
            "title": title,
            "teaser": desc,
            "tag_label": tag,
            "link_label": cta,
            "body_md": None,
            "status": "live",
            "featured_home": True,
            "date_label": "",
            "sort_key": "",
            "display_order": i,
        })
    return planned


def _print_rows(rows: list[dict]) -> None:
    for r in rows:
        print(f"  order={r['display_order']} slug={r['slug']!r} {r['title']!r} (tag={r['tag_label']!r})")


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
        existing = lib.list_original_content()
        if existing:
            print(f"original_content already has {len(existing)} row(s) — nothing to do. "
                  "(Idempotency guard: this script only ever runs against an empty table.)")
            return 0

        planned = planned_rows()
        print(f"{len(planned)} row(s) would be inserted:\n")
        _print_rows(planned)

        if not args.apply:
            print(
                "\nPREVIEW ONLY — no DB writes. This is exactly the row list --apply would "
                "insert, in the same order. Re-run with --apply to write for real."
            )
            return 0

        for r in planned:
            lib.add_original_content(
                r["slug"], r["title"], r["teaser"], r["tag_label"], r["link_label"],
                r["body_md"], r["status"], r["featured_home"], r["date_label"], r["sort_key"],
                r["display_order"],
            )
        print(f"\nApplied — {len(planned)} row(s) inserted.\n")

        # Write-then-read-back: re-list the table and confirm the row count
        # and a spot-check of the first/last planned row match.
        after = lib.list_original_content()
        assert len(after) == len(planned), f"expected {len(planned)} rows, found {len(after)}"
        by_slug = {r["slug"]: r for r in after}
        for spot in (planned[0], planned[-1]):
            fresh = by_slug.get(spot["slug"])
            assert fresh is not None, f"{spot['slug']!r} missing after migration"
            assert fresh["title"] == spot["title"] and fresh["body_md"] is None, \
                f"{spot['slug']!r} landed with mismatched fields"
        print(f"Verified — {len(after)} row(s) read back from original_content, "
              "spot-checked fields match.")
        return 0
    finally:
        lib.close()


if __name__ == "__main__":
    raise SystemExit(main())
