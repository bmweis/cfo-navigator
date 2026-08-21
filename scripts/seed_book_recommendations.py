#!/usr/bin/env python3
"""One-time data migration: inserts the ten "Book recommendations" cards
into the `benchmarks` table's new "books" section (Resources page split —
see CLAUDE.md/ARCHITECTURE.md's "Resources — Book recommendations" note).

The `benchmarks` table's boot-time seed sync (`_seed_toolbox` in
webapp/app.py, `_DEFAULT_BENCHMARKS`) only ever SYNCS an existing row by
URL match — it never inserts one, since a missing row might be a
deliberate admin delete rather than an unseeded one (see that function's
own comment). These ten book rows are new data with nothing to sync
against, so they need a genuine one-time insert — this script, run by
hand, not the boot hook.

Deliberately a manual, run-by-hand script — NOT wired into an automatic
boot hook. This is a production DATA write, not a schema/column backfill,
and the standing rule (CLAUDE.md, "One-off admin fixes against the
database") requires Brian's review of the affected rows BEFORE a
production write. Safe by default (preview only, no writes) — same
--apply convention as scripts/backfill_logos.py and
scripts/archive/migrate_thought_leadership.py.

Idempotent by URL: a book whose URL already exists anywhere in
`benchmarks` (any section) is skipped, not re-inserted, so a partial or
repeated run never duplicates a row.

Per the write-then-read-back standing practice, an --apply run re-lists
the "books" section afterward and asserts the row count and a spot-check
of the first/last planned row match.

Lives in scripts/ (not scripts/archive/) until it's actually run — the
standing convention is that a script only moves to scripts/archive/ once
its job is done (git mv, same PR), never before.

Usage:
    python -m scripts.seed_book_recommendations --db library.db            # preview
    python -m scripts.seed_book_recommendations --db library.db --apply    # write for real
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from linklib.db import Library, resolve_db_path

BOOKS = [
    {
        "name": "The Holloway Guide to Equity Compensation",
        "url": "https://www.holloway.com/g/equity-compensation/about",
        "description": "Holloway. A comprehensive guide to stock options, RSUs, and equity mechanics for founders and employees.",
    },
    {
        "name": "The Holloway Guide to Raising Venture Capital",
        "url": "https://www.holloway.com/g/venture-capital/about",
        "description": "Holloway (Andy Sparks). A fundraising handbook covering VC basics, term sheets, investor outreach, and negotiation.",
    },
    {
        "name": "The Holloway Guide to Remote Work",
        "url": "https://www.holloway.com/g/remote-work/about",
        "description": "Holloway. A practical guide to building and managing distributed teams.",
    },
    {
        "name": "Founding Sales",
        "url": "https://www.holloway.com/b/founding-sales",
        "description": "Pete Kazanjy. The early-stage go-to-market handbook for founders and first-time sales hires figuring out B2B sales from scratch.",
    },
    {
        "name": "Great Founders Write",
        "url": "https://www.holloway.com/b/great-founders-write",
        "description": "Holloway. Principles for clear thinking and confident writing, aimed at founders.",
    },
    {
        "name": "Stop Asking Questions",
        "url": "https://www.holloway.com/b/stop-asking-questions",
        "description": "Andrew Warner. A guide to hosting better interviews, from a podcast host with 2,000+ episodes—useful well beyond podcasting (customer interviews, hiring, board updates).",
    },
    {
        "name": "Venture Deals",
        "url": "https://venturedeals.com/",
        "description": "Brad Feld & Jason Mendelson. The standard reference for understanding VC term sheets and how venture deals actually work, from an investor and a deal lawyer.",
    },
    {
        "name": "The Creative Act: A Way of Being",
        "url": "https://sites.prh.com/thecreativeact",
        "description": "Rick Rubin. A meditation on creativity and the creative process—not business-specific, but shapes how I think about the work.",
    },
    {
        "name": "Never Search Alone",
        "url": "https://www.neversearchalone.org/",
        "description": "Phyl Terry. A peer-support approach to job searching: building a \"search council,\" clarifying what you actually want, and negotiating with confidence.",
    },
    {
        "name": "The Advantage",
        "url": "https://www.tablegroup.com/product/the-advantage/",
        "description": "Patrick Lencioni. Makes the case that organizational health, not strategy or finance, is the biggest competitive advantage most companies leave on the table.",
    },
]


def _print_rows(rows: list[dict]) -> None:
    for r in rows:
        print(f"  {r['name']!r} — {r['url']}")


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
        existing_urls = {row["url"] for row in lib.list_benchmarks()}
        planned = [b for b in BOOKS if b["url"] not in existing_urls]
        skipped = [b for b in BOOKS if b["url"] in existing_urls]

        if skipped:
            print(f"Skipping {len(skipped)} book(s) already present by URL:")
            _print_rows(skipped)
            print()

        if not planned:
            print("Nothing to insert — every book URL already exists in benchmarks.")
            return 0

        print(f"{len(planned)} book(s) would be inserted into the 'books' section:\n")
        _print_rows(planned)

        if not args.apply:
            print(
                "\nPREVIEW ONLY — no DB writes. This is exactly the row list --apply would "
                "insert, in the same order. Re-run with --apply to write for real."
            )
            return 0

        for b in planned:
            lib.add_benchmark(b["name"], b["url"], b["description"],
                              coverage="Private", pricing="free", section="books")
        print(f"\nApplied — {len(planned)} book(s) inserted.\n")

        # Write-then-read-back: re-list the 'books' section and confirm the
        # row count and a spot-check of the first/last planned row match.
        after = lib.list_benchmarks(section="books")
        after_urls = {row["url"] for row in after}
        for b in planned:
            assert b["url"] in after_urls, f"{b['name']!r} missing from 'books' section after insert"
        by_url = {row["url"]: row for row in after}
        for spot in (planned[0], planned[-1]):
            fresh = by_url[spot["url"]]
            assert fresh["name"] == spot["name"] and fresh["section"] == "books", \
                f"{spot['name']!r} landed with mismatched fields"
        print(f"Verified — {len(after)} row(s) read back from the 'books' section, "
              "spot-checked fields match.")
        return 0
    finally:
        lib.close()


if __name__ == "__main__":
    raise SystemExit(main())
