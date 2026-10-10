#!/usr/bin/env python3
"""Backfill Primary use (issue #624, PR 1) for Software vendors.

Rule: a vendor with exactly one category and an empty Primary use takes that
category. A vendor with two or more categories and an empty Primary use is
NEVER touched: the script prints each one (id, name, tags) as the worksheet
Brian works from, and he sets those by hand on the edit page. A vendor with no
categories is reported and left alone. A vendor that already has a Primary use
is never changed, so the script is idempotent.

Safe by default: previews and writes nothing unless --apply is passed. Each
write goes through `Library.set_tool_primary` (the invariant lives there) and is
read back and compared; any mismatch exits nonzero.

Side effects of the admin UI save that this script does or does not repeat:
  - updated_at: NOT bumped (`touch=False`). A mechanical fill is not an edit, and
    "Last edited" on the edit page would otherwise lie.
  - needs_review and the three *_needs_verification flags: not touched. The UI
    save only changes them when a field was freshly AI-drafted, which is not the
    case here.
  - voice review queue / voice normalization: nothing to do, no prose is written.
  - entity_citations, narrative_review_log, tool_audit_log: not touched.
  - caches and mirrors: none. Nothing derived from categories is cached, and the
    OPML and article mirrors do not read tools.categories_json.

Usage (production, over `railway ssh`):
    python -m scripts.backfill_tool_primary --db /data/library.db            # preview
    python -m scripts.backfill_tool_primary --db /data/library.db --apply    # write
"""
from __future__ import annotations

import argparse
import sys

from linklib.db import Library, resolve_db_path


def plan(tools: list[dict]) -> dict:
    """Classify every tool. Pure, so it can be tested without a database."""
    out = {"zero": [], "one": [], "many": [], "has_primary": [], "to_set": []}
    for t in tools:
        cats = t.get("categories") or []
        if t.get("primary_category"):
            out["has_primary"].append(t)
            continue
        if len(cats) == 0:
            out["zero"].append(t)
        elif len(cats) == 1:
            out["one"].append(t)
            out["to_set"].append((t, cats[0]))
        else:
            out["many"].append(t)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", help="Absolute path to library.db (or set LINKLIB_DB)")
    ap.add_argument("--apply", action="store_true", help="Write for real (default: preview only)")
    args = ap.parse_args(argv)

    db_path = resolve_db_path(args.db)
    lib = Library(db_path)
    try:
        tools = lib.list_tools(approved_only=False)
        p = plan(tools)
        print(f"Vendors: {len(tools)}")
        print(f"  already have a Primary use: {len(p['has_primary'])}")
        print(f"  0 categories (left alone):   {len(p['zero'])}")
        print(f"  1 category (will be filled): {len(p['one'])}")
        print(f"  2+ categories (by hand):     {len(p['many'])}")

        if p["to_set"]:
            print("\nWill set Primary use to the only category:")
            for t, cat in p["to_set"]:
                print(f"  id={t['id']:<5} {t['name']}  ->  {cat}")
        if p["zero"]:
            print("\nNo categories at all (add one on the edit page):")
            for t in p["zero"]:
                print(f"  id={t['id']:<5} {t['name']}")
        if p["many"]:
            print("\nWORKSHEET: 2+ categories and no Primary use. Choose on the edit page:")
            for t in p["many"]:
                print(f"  id={t['id']:<5} {t['name']}  [{' | '.join(t['categories'])}]")

        if not args.apply:
            print("\nPreview only. Nothing was written. Pass --apply to write.")
            return 0

        bad = 0
        for t, cat in p["to_set"]:
            lib.set_tool_primary(t["id"], cat, touch=False)
            back = lib.get_tool(t["id"])
            ok = back is not None and back["primary_category"] == cat and cat in back["categories"]
            print(f"  {'OK  ' if ok else 'FAIL'} id={t['id']} {t['name']} -> {back['primary_category'] if back else None!r}")
            bad += 0 if ok else 1
        print(f"\nWrote {len(p['to_set']) - bad} of {len(p['to_set'])}.")
        if bad:
            print("READ-BACK MISMATCH. Investigate before running again.")
            return 1
        return 0
    finally:
        lib.close()


if __name__ == "__main__":
    sys.exit(main())
