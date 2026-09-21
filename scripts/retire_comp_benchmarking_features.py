#!/usr/bin/env python3
"""Follow-up to the Pave/Culpepper/Radford removal (2026-08): once those
three comp-benchmarking-only vendors are deleted (via the admin UI's own
Delete button, now safe to use because of Library.delete_tool()'s cascade
fix — see CLAUDE.md), some Headcount Planning (category_id=34)
category_features buckets go to zero real tool links, since every link
they had was from one of these three vendors. This script:

  1. Renames the category_id=34 feature "Job architecture" to
     "Job levels & pay bands" (Library.update_category_feature).
  2. Soft-retires (Library.retire_category_feature — retired_at set, links
     left untouched, per docs/FEATURE_TAXONOMY.md's "features are retired,
     never deleted" rule) every category_id=34 feature that has ZERO
     tool_feature_links once Pave/Culpepper/Radford's own links are
     excluded from the count. A feature that still has a real link from
     another Headcount Planning vendor (Doublefin, Orgvue, Teamohana,
     Knoetic, ChartHop) is left untouched, matching the standing rule this
     is a follow-up to.

Computes the zero-link set dynamically rather than hardcoding feature
names/ids — this script is only correct to run AFTER Pave/Culpepper/Radford
have actually been deleted (so "current tool_feature_links count" and
"count excluding those three vendors" are the same query); if any of the
three still exist as tools, this script refuses to run and says so, rather
than silently retiring a feature some OTHER vendor might still need once
the deletion actually happens.

Preview by default; --apply commits. Write-then-read-back verified per the
standing one-off-fix discipline: after applying, re-reads every touched
category_features row and asserts the rename/retirement actually took.

NOT run against production as part of writing this script — preview-only
locally. Brian runs --apply via railway ssh, after Pave/Culpepper/Radford
have been deleted via the admin UI.

Usage:
    python -m scripts.retire_comp_benchmarking_features --db library.db            # preview
    python -m scripts.retire_comp_benchmarking_features --db library.db --apply    # write for real
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from linklib.db import Library, resolve_db_path

HEADCOUNT_CATEGORY_ID = 34
VENDOR_NAMES = ["Pave", "Culpepper", "Radford (acquired by Aon)"]
OLD_NAME = "Job architecture"
NEW_NAME = "Job levels & pay bands"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    db_path = resolve_db_path(args.db)
    print(f"Resolved DB path: {db_path}")
    print(f"Mode: {'APPLY (writing)' if args.apply else 'PREVIEW (no writes)'}\n")

    lib = Library(db_path)
    conn = lib.conn
    try:
        still_present = [
            name for name in VENDOR_NAMES
            if conn.execute("SELECT 1 FROM tools WHERE name=?", (name,)).fetchone()
        ]
        if still_present:
            print("REFUSING TO RUN: the following vendor(s) still exist as tools rows — "
                  "delete them first via the admin UI, then re-run this script:")
            for name in still_present:
                print(f"  - {name!r}")
            return 1

        feats = conn.execute(
            "SELECT * FROM category_features WHERE category_id=? AND retired_at='' "
            "ORDER BY sort_order, name",
            (HEADCOUNT_CATEGORY_ID,),
        ).fetchall()
        if not feats:
            print(f"No live category_features rows under category_id={HEADCOUNT_CATEGORY_ID} — nothing to do.")
            return 0

        rename_target = None
        retire_ids = []
        print("=" * 70)
        print(f"category_features under category_id={HEADCOUNT_CATEGORY_ID} (live only)")
        print("=" * 70)
        for f in feats:
            f = dict(f)
            link_count = conn.execute(
                "SELECT COUNT(*) FROM tool_feature_links WHERE feature_id=?", (f["id"],)
            ).fetchone()[0]
            note = ""
            if link_count == 0:
                retire_ids.append(f["id"])
                note = "  <-- PROPOSE RETIRE (zero live tool links)"
            if f["name"].strip().lower() == OLD_NAME.lower():
                rename_target = f["id"]
                note += f"  <-- PROPOSE RENAME to {NEW_NAME!r}"
            print(f"  id={f['id']:>4}  {f['name']!r:35} link_count={link_count}{note}")

        print()
        if rename_target is None:
            print(f"WARNING: no live feature named {OLD_NAME!r} found under category_id="
                  f"{HEADCOUNT_CATEGORY_ID} — nothing to rename (maybe already renamed?).")
        if not retire_ids:
            print("No features have zero live links — nothing to retire.")

        if not args.apply:
            print("\nPreview only — pass --apply to write these changes.")
            return 0

        if rename_target is not None:
            row = lib.get_category_feature(rename_target)
            lib.update_category_feature(
                rename_target, NEW_NAME, row["definition"], row["pointer_note"], row["sort_order"],
                source="script",
            )
        for fid in retire_ids:
            lib.retire_category_feature(fid)

        # Write-then-read-back verification.
        print("\n" + "=" * 70)
        print("Verification (read back after write)")
        print("=" * 70)
        ok = True
        if rename_target is not None:
            row = lib.get_category_feature(rename_target)
            match = row["name"] == NEW_NAME
            ok = ok and match
            print(f"  rename: id={rename_target} name={row['name']!r} "
                  f"{'OK' if match else 'MISMATCH — expected ' + repr(NEW_NAME)}")
        for fid in retire_ids:
            row = lib.get_category_feature(fid)
            retired = bool(row["retired_at"])
            ok = ok and retired
            print(f"  retire: id={fid} name={row['name']!r} retired_at={row['retired_at']!r} "
                  f"{'OK' if retired else 'MISMATCH — not retired'}")

        if ok:
            print("\nAll changes verified.")
            return 0
        print("\nSOME CHANGES DID NOT VERIFY — investigate before trusting this run.")
        return 1
    finally:
        lib.close()


if __name__ == "__main__":
    sys.exit(main())
