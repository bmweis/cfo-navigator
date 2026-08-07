#!/usr/bin/env python3
"""One-off migration: recompute every Software and Communities `slug` from its
URL's bare domain root instead of the old name-based `_slugify(name)` value
(Phase 2 of the admin tooling + profile pages build — see CLAUDE.md's
Contributing section for context).

Algorithm (matches linklib.db._domain_slug_base / _domain_slug_full, and the
generation logic now used by add_tool/add_community for new rows):
  1. Hostname of the stored URL, lowercased, leading "www." stripped.
  2. First label before the first remaining dot (e.g. "abacum.io" -> "abacum").
  3. If that short slug collides with another row of the SAME type, the
     colliding rows fall back to the full hyphenated domain (e.g. "abacum-io")
     instead of a numeric suffix — a numeric suffix is only used as a final
     fallback if even the full-domain form collides (rare).

Software and Communities each enforce slug uniqueness only within their own
table — a domain shared across a vendor's software listing and its own
branded community (e.g. datarails.com) is not a collision; Phase 0 found
exactly this pattern (airbase, datarails, rillet) and confirmed it's fine
since the two types use separate URL prefixes (/tools/software/... and
/tools/communities/...).

Old slugs are printed in a before/after report so any bookmarked/shared links
can be identified — this is a hard cutover per the build plan (no redirect
from old ID-based or old name-based-slug URLs).

Usage:
    python -m scripts.migrate_domain_slugs --db library.db
    python -m scripts.migrate_domain_slugs --db library.db --dry-run
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from linklib.db import Library, _domain_slug_base, _domain_slug_full, _slugify, resolve_db_path


def _assign_slugs(rows: list[tuple[int, str, str, str]]) -> dict[int, str]:
    """rows: (id, name, url, old_slug). Returns {id: new_slug}, applying the
    short-domain-first / full-domain-on-collision / numeric-suffix-last-resort
    algorithm across the whole batch at once (unlike the single-row INSERT-time
    check in linklib.db, this sees every row up front, so it can detect a
    same-type collision even between two rows neither of which exists in the
    DB yet under its new slug)."""
    bases = {}
    for row_id, name, url, _old_slug in rows:
        bases[row_id] = _domain_slug_base(url) or _slugify(name) or f"entry-{row_id}"

    base_counts = Counter(bases.values())
    assigned: dict[int, str] = {}
    used: set[str] = set()
    for row_id, name, url, _old_slug in rows:
        base = bases[row_id]
        if base_counts[base] > 1:
            full = _domain_slug_full(url)
            candidate = full if full else base
        else:
            candidate = base
        slug = candidate
        suffix = 2
        while slug in used:
            slug = f"{candidate}-{suffix}"
            suffix += 1
        used.add(slug)
        assigned[row_id] = slug
    return assigned


def _migrate_table(lib: Library, table: str, dry_run: bool) -> None:
    rows = lib.conn.execute(f"SELECT id, name, url, slug FROM {table}").fetchall()
    rows = [(r["id"], r["name"], r["url"], r["slug"]) for r in rows]
    if not rows:
        print(f"{table}: no rows.")
        return

    new_slugs = _assign_slugs(rows)
    changed = [(row_id, name, old_slug, new_slugs[row_id])
               for row_id, name, _url, old_slug in rows if new_slugs[row_id] != old_slug]

    print(f"\n{table}: {len(rows)} rows scanned, {len(changed)} slug(s) would change.")
    for row_id, name, old_slug, new_slug in changed:
        print(f"   [{row_id}] {name}: {old_slug!r} -> {new_slug!r}")

    if dry_run or not changed:
        return

    for row_id, _name, _old_slug, new_slug in changed:
        lib.conn.execute(f"UPDATE {table} SET slug=? WHERE id=?", (new_slug, row_id))
    lib.conn.commit()
    print(f"   Updated {len(changed)} row(s) in {table}.")


def main():
    parser = argparse.ArgumentParser(description="Migrate Software/Communities slugs to domain-derived values.")
    parser.add_argument("--db", default=None, help="Path to library.db (or set LINKLIB_DB)")
    parser.add_argument("--dry-run", action="store_true", help="Report what would change without writing.")
    args = parser.parse_args()
    args.db = resolve_db_path(args.db)

    lib = Library(args.db)
    try:
        _migrate_table(lib, "tools", args.dry_run)
        _migrate_table(lib, "communities", args.dry_run)
        if args.dry_run:
            print("\n--dry-run: no changes written.")
    finally:
        lib.close()


if __name__ == "__main__":
    main()
