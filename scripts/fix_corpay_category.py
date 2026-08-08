#!/usr/bin/env python3
"""One-off admin fix: check (and optionally correct) Corpay's categories in
the live tools table.

Corpay should carry Procurement/Spend + Travel Management only. Its
scripts/seed_tools.py entry used to say Treasury/Cash Management only (a
stale value from before the original out-of-scope cleanup that trimmed it
by hand via the admin UI) — if the deleted-tools-reappearing bug hard-
deleted and silently re-inserted Corpay before that bug was fixed, it would
have come back with the old, wrong category. This script checks which
state the live row is actually in and, only with --apply, corrects it.

Safe by default: with no flags, this ONLY reads and reports — it makes no
writes. Pass --apply to actually perform the correction, after you've
reviewed the report. Per the write-then-read-back standing practice, an
--apply run re-SELECTs the row after the UPDATE and asserts the change
took, printing the row it just read back.

Usage:
    python -m scripts.fix_corpay_category [--db library.db]              # check only (no writes)
    python -m scripts.fix_corpay_category [--db library.db] --apply      # check, then correct if needed
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from linklib.db import Library, resolve_db_path

CORPAY_URL = "https://www.corpay.com"
DESIRED_CATEGORIES = ["Procurement/Spend", "Travel Management"]


def main():
    parser = argparse.ArgumentParser(description="Check/fix Corpay's categories in the live DB.")
    parser.add_argument("--db", default=None, help="Path to library.db (or set LINKLIB_DB)")
    parser.add_argument("--apply", action="store_true",
                         help="Actually perform the correction. Without this flag, report only.")
    args = parser.parse_args()
    args.db = resolve_db_path(args.db, allow_missing=False)
    print(f"Reading from: {args.db}\n")

    lib = Library(args.db)
    try:
        row = lib._find_tool_by_normalized_url(CORPAY_URL)
        if not row:
            print("Corpay not found in the tools table by URL "
                  f"({CORPAY_URL}) — nothing to check or fix. If it was "
                  "deleted and hasn't reappeared, no action is needed.")
            return

        current = lib.get_tool(row["id"])
        print(f"Found Corpay (id={current['id']}, slug={current['slug']})")
        print(f"  Current categories: {current['categories']}")
        print(f"  Desired categories: {sorted(DESIRED_CATEGORIES)}")

        if sorted(current["categories"]) == sorted(DESIRED_CATEGORIES):
            print("\nAlready correct — no action needed.")
            return

        print("\nMISMATCH — Corpay's live category does not match what it should be.")
        if not args.apply:
            print("Re-run with --apply to correct it (no write has been made).")
            return

        print("\n--apply given: correcting now...")
        lib.update_tool(
            current["id"], current["name"], current["description"], current["url"],
            DESIRED_CATEGORIES, advisor=current["advisor"], promoted=current["promoted"],
            vendor_email=current["vendor_email"], warm_intro_enabled=current["warm_intro_enabled"],
            vendor_name=current["vendor_name"], summary=current["summary"],
        )

        # Write-then-read-back: re-select and assert the change actually applied.
        after = lib.get_tool(current["id"])
        assert sorted(after["categories"]) == sorted(DESIRED_CATEGORIES), (
            f"UPDATE did not take — categories are still {after['categories']}"
        )
        print(f"Corrected. Row read back after the write: categories={after['categories']}")
    finally:
        lib.close()


if __name__ == "__main__":
    main()
