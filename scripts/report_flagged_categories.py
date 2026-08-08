#!/usr/bin/env python3
"""Read-only report for the Part 3 deliverable of the deleted-tools-
reappearing fix: lists every Software entry currently carrying one of the
four flagged categories from the original out-of-scope cleanup, so Brian
can review and delete by hand via /admin/tools/software — same workflow as
the original cleanup. Makes NO writes of any kind.

Usage:
    python -m scripts.report_flagged_categories [--db library.db]

Flagged categories (from the original cleanup criteria):
    BI/Analytics, Cloud/IT Spend, Legal and Contracting, Treasury/Cash Management

For each tool carrying any flagged category, this prints:
  - name, full category list (not just the flagged one), and url
  - whether it's a named exception / known mixed-category case from prior
    cleanup, or a clean candidate for full deletion

It also separately checks the three tools reported as "previously removed
entirely" (DigitalRoute, Nilus, LinkSquares) by name/URL — not just by
flagged category, since a reappeared row keeps its *seed-file* categories,
which may not include a flagged one (see the module docstring in the PR
this shipped with for why that matters).
"""
from __future__ import annotations

import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from linklib.db import Library, resolve_db_path
from scripts.seed_tools import TOOLS

FLAGGED_CATEGORIES = {
    "BI/Analytics",
    "Cloud/IT Spend",
    "Legal and Contracting",
    "Treasury/Cash Management",
}

# name (case-insensitive prefix match) -> note
NAMED_EXCEPTIONS = {
    "dealhub": "Named exception — keep. Should carry CPQ (recategorized), not Legal and Contracting.",
    "finquery": "Named exception — keep. Should carry Accounting (recategorized, lease accounting), not Legal and Contracting.",
    "corpay": "Named exception — keep. Should carry Procurement/Spend + Travel Management only.",
}

# name (case-insensitive prefix match) -> (categories to keep, note)
MIXED_CATEGORY_CASES = {
    "mineraltree": (["Procurement/Spend"], "Keep, trim Treasury/Cash Management."),
    "orb": (["Revenue"], "Keep, trim Legal and Contracting."),
    "campfire": (["ERP", "Revenue"], "Keep, trim Treasury/Cash Management."),
}

PREVIOUSLY_REMOVED = ["DigitalRoute", "Nilus", "LinkSquares"]


def _classify(name: str) -> str | None:
    lname = name.lower()
    for key, note in NAMED_EXCEPTIONS.items():
        if lname.startswith(key):
            return note
    for key, (keep, note) in MIXED_CATEGORY_CASES.items():
        if lname.startswith(key):
            return f"Known mixed-category case — {note} (keep {', '.join(keep)})"
    return None


def main():
    parser = argparse.ArgumentParser(description="Report tools carrying a flagged category. Read-only.")
    parser.add_argument("--db", default=None, help="Path to library.db (or set LINKLIB_DB)")
    args = parser.parse_args()
    args.db = resolve_db_path(args.db, allow_missing=False)
    print(f"Reading from: {args.db}\n")

    lib = Library(args.db)
    try:
        all_tools = lib.list_tools(approved_only=False)
    finally:
        lib.close()

    flagged = []
    for t in all_tools:
        cats = set(t["categories"])
        if cats & FLAGGED_CATEGORIES:
            flagged.append(t)

    print(f"{len(all_tools)} total tools in the database.")
    print(f"{len(flagged)} carry at least one flagged category: "
          f"{', '.join(sorted(FLAGGED_CATEGORIES))}\n")
    print("=" * 100)

    for t in sorted(flagged, key=lambda t: t["name"].lower()):
        note = _classify(t["name"])
        verdict = note if note else "CLEAN CANDIDATE FOR FULL DELETION — not a named exception or known mixed case."
        print(f"[ ] {t['name']}")
        print(f"    url:        {t['url']}")
        print(f"    categories: {', '.join(t['categories'])}")
        print(f"    verdict:    {verdict}")
        print()

    print("=" * 100)
    print("\nPreviously-removed-entirely check (by name, not flagged category — a\n"
          "reappeared row keeps the seed file's own categories, which may not be\n"
          "one of the four flagged ones):\n")
    by_name = {t["name"].lower(): t for t in all_tools}
    for name in PREVIOUSLY_REMOVED:
        hit = by_name.get(name.lower())
        if hit:
            print(f"[ ] {name}: STILL PRESENT (id={hit['id']}) — categories: "
                  f"{', '.join(hit['categories'])}, url: {hit['url']}. "
                  f"Flag for re-deletion.")
        else:
            print(f"[x] {name}: confirmed gone.")


if __name__ == "__main__":
    main()
