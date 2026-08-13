#!/usr/bin/env python3
"""One-time data migration (Phase E, dual screenshot capture): moves any
pre-existing `screenshot_is_product=1` row's screenshot into the new
`app_screenshot_url` slot and clears the homepage slot — see
`Library.migrate_app_screenshot_from_product_flag`'s docstring for the full
reasoning on why this is the correct read of what that row actually had.

Deliberately a manual, run-by-hand script — NOT wired into an automatic boot
hook. This is a production DATA write, not a schema/column backfill, and the
standing rule (CLAUDE.md, "One-off admin fixes against the database")
requires Brian's review of the affected rows BEFORE a production write, not
an after-the-fact deploy-log line. Safe by default (preview only, no writes)
— same --apply convention as scripts/backfill_logos.py. Per the write-then-
read-back standing practice, an --apply run re-SELECTs every row it touched
afterward and asserts the new value stuck.

Idempotent: guarded by `app_screenshot_url = ''`, so re-running after a
successful --apply finds nothing left to do. Non-destructive: the
`screenshot_is_product` column itself is never cleared or dropped, so which
rows this touched stays visible/reconstructable afterward purely by
re-querying for `screenshot_is_product=1`.

Usage:
    python -m scripts.archive.migrate_app_screenshot_from_product_flag --db library.db            # preview
    python -m scripts.archive.migrate_app_screenshot_from_product_flag --db library.db --apply     # write for real
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from linklib.db import Library, resolve_db_path


def _print_rows(rows: list[dict]) -> None:
    for r in rows:
        print(f"  [{r['table']:11s}] id={r['id']:>4} {r['name']!r} ({r['slug']}) "
              f"screenshot_url={r['screenshot_url']!r}")


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
        candidates = lib.find_legacy_product_screenshot_rows()
        if not candidates:
            print("No legacy screenshot_is_product=1 rows found — nothing to do.")
            return 0

        print(f"{len(candidates)} row(s) would move their screenshot into app_screenshot_url "
              f"and clear the homepage slot:\n")
        _print_rows(candidates)

        if not args.apply:
            print(
                "\nPREVIEW ONLY — no DB writes. This is exactly the row list --apply would "
                "process, in the same order. Re-run with --apply to write for real."
            )
            return 0

        touched = lib.migrate_app_screenshot_from_product_flag()
        print(f"\nApplied — {len(touched)} row(s) migrated.\n")

        # Write-then-read-back: re-SELECT every touched row and assert the
        # migration actually landed, per the standing one-off-fix practice.
        ok = True
        for r in touched:
            fresh = lib.get_tool(r["id"]) if r["table"] == "tools" else lib.get_community(r["id"])
            assert fresh is not None, f"{r['table']} id={r['id']} vanished after migration"
            if fresh["app_screenshot_url"] != r["screenshot_url"] or fresh["screenshot_url"] != "":
                ok = False
                print(f"  MISMATCH: {r['table']} id={r['id']} {r['name']!r} — "
                      f"app_screenshot_url={fresh['app_screenshot_url']!r}, "
                      f"screenshot_url={fresh['screenshot_url']!r}")
            else:
                print(f"  confirmed: {r['table']} id={r['id']} {r['name']!r} "
                      f"-> app_screenshot_url={fresh['app_screenshot_url']!r}")
        if not ok:
            print("\nERROR: one or more rows did not verify after the write — see MISMATCH lines above.",
                  file=sys.stderr)
            return 1
    finally:
        lib.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
