#!/usr/bin/env python3
"""One-off backfill: reconcile the existing free-text `access`/`format` values
on communities rows into the new fixed dropdown options (Communities Access/
Format dropdown conversion) now that both are `<select>` fields on the admin
form instead of free text.

The old values were audited across all 35 live rows before picking the
option lists (see `_COMMUNITY_ACCESS`/`_COMMUNITY_FORMAT` in webapp/app.py) —
every existing value's own leading word/phrase became one of the fixed
options, so this is a lossy-by-design bucketing (e.g. "Invite-only (~10%
acceptance, ~95% referral rate)" -> "Invite-only"), not a data-preserving
migration. A value that doesn't start with any known option is left as
NEEDS_VERIFICATION instead of guessed — same sentinel the auto-populate
feature uses, so it surfaces via the existing admin "N fields need
verification" badge rather than failing silently. Safe to re-run: a row
already on one of the fixed options (or NEEDS_VERIFICATION) is skipped.

Usage:
    python -m scripts.backfill_community_access_format [--db library.db] [--dry-run]
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from linklib.db import Library
from linklib.enrich import NEEDS_VERIFICATION

ACCESS_OPTIONS = ["Open", "Application", "Invite-only", "Qualification-based"]
FORMAT_OPTIONS = ["Hybrid", "In-person", "Slack", "Online", "LinkedIn group"]


def _bucket(value: str, options: list[str]) -> str:
    """The option whose text `value` starts with, or NEEDS_VERIFICATION if
    none matches — never a guess."""
    for opt in options:
        if value.startswith(opt):
            return opt
    return NEEDS_VERIFICATION


def main():
    parser = argparse.ArgumentParser(
        description="Bucket communities.access/format free text into the fixed dropdown options."
    )
    parser.add_argument("--db", default=os.environ.get("LINKLIB_DB", "library.db"))
    parser.add_argument("--dry-run", action="store_true", help="Print what would change without writing.")
    args = parser.parse_args()

    lib = Library(args.db)
    updated = flagged = 0
    try:
        rows = lib.conn.execute("SELECT id, name, access, format FROM communities").fetchall()
        for row in rows:
            old_access, old_format = row["access"] or "", row["format"] or ""
            if old_access in ACCESS_OPTIONS + [NEEDS_VERIFICATION] and old_format in FORMAT_OPTIONS + [NEEDS_VERIFICATION]:
                print(f"  SKIP  {row['name']} (already reconciled)")
                continue
            new_access = _bucket(old_access, ACCESS_OPTIONS)
            new_format = _bucket(old_format, FORMAT_OPTIONS)
            if new_access == NEEDS_VERIFICATION or new_format == NEEDS_VERIFICATION:
                flagged += 1
            print(f"  {'WOULD UPDATE' if args.dry_run else 'UPDATE'} {row['name']}: "
                  f"access {old_access!r} -> {new_access!r}, format {old_format!r} -> {new_format!r}")
            if not args.dry_run:
                lib.conn.execute(
                    "UPDATE communities SET access=?, format=? WHERE id=?",
                    (new_access, new_format, row["id"]),
                )
            updated += 1
        if not args.dry_run:
            lib.conn.commit()
    finally:
        lib.close()

    print(f"\n{updated} {'would be ' if args.dry_run else ''}updated, "
          f"{flagged} flagged {NEEDS_VERIFICATION!r} (no matching option).")


if __name__ == "__main__":
    main()
