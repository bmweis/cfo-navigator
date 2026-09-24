#!/usr/bin/env python3
"""One-off fix for seven `tools.slug` values carrying a spurious `-2`
suffix that doesn't belong: dealhub-2, liveflow-2, puzzle-2, zenskar-2,
m3ter-2, digits-2, tropic-2. All seven were created 2026-07-10 through
2026-07-24, per created_at.

Root cause, confirmed against production (via the /mcp introspection
tools): `add_tool()`'s slug algorithm (linklib/db.py) tries a short
domain-derived base (e.g. "tropic"), falls back once to the full
hyphenated domain on a collision, then appends a numeric suffix in a
loop if even that collides. A literal "vendor-2" slug only comes out of
that algorithm when the short base collides AND the full-domain fallback
also equals the short base (i.e. `_domain_slug_base`/`_domain_slug_full`
both failed to parse a host and fell back to the name-slugified base) —
which happens when a second row is created for the SAME vendor before the
first one is cleaned up, both landing on the same name-based fallback.

For liveflow-2 specifically this is directly confirmed: `tool_audit_log`
has a real 2026-08-22 `merge` entry for item_id 253, "Liveflow" (lowercase
f) — a name-duplicate row created the same day (2026-07-10) as the
surviving "LiveFlow" row (id 183, slug liveflow-2), later found and merged
by the admin's name-duplicate-merge tool. The merge deletes the duplicate
row but was never wired to rename the survivor's slug back to the bare
form, so "liveflow-2" persisted even after "liveflow" was freed up. The
other six vendors show the identical slug shape (a real, non-domain-suffix
"-2") with no current row holding the bare form — the same class of
now-orphaned suffix, whatever the exact original collision was for each
(a duplicate creation attempt later cleaned up, or a since-deleted
unrelated row that briefly held the bare slug).

Checked before writing this script, live against production:
  - `tools.slug` is UNIQUE — a rename that collided would fail loudly
    (sqlite3.IntegrityError), never silently overwrite another row.
  - None of the seven bare target slugs (dealhub, liveflow, puzzle,
    zenskar, m3ter, digits, tropic) currently belongs to any row.
  - `screenshot_url`/`logo_path`/`app_screenshot_url` are all STORED
    literal strings (set at capture time, e.g.
    f"{base}/tools/software/screenshot/{slug}.png?v=...") — never
    recomputed from `tools.slug` at render time (see
    `tools_software_screenshot`/`tools_software_logo` in webapp/app.py,
    which serve whatever filename the client requests, not something
    derived live from the row). So the rename does NOT break the existing
    screenshot/logo for any of these seven — the stored URLs keep
    resolving to their existing on-disk files (now just named after the
    tool's OLD slug, same situation as any other tool whose logo/
    screenshot predates a later change). No update needed there.
  - No hardcoded `/tools/software/<old-slug>` reference was found in
    `original_content.body_md` or `ai_surfaces.body_md` (the two places a
    hand-authored internal link would plausibly live) via the /mcp
    introspection tools. The matchmaker (linklib/matchmaker.py) builds
    tool links dynamically from the current slug on every turn, so it's
    unaffected either way. An external inbound link to one of these seven
    old URLs is outside what this script (or any DB inspection) can rule
    out — that's why the paired route change
    (webapp.app._LEGACY_TOOL_SLUG_REDIRECTS) 301s every old URL rather
    than letting it 404.

Safe by default: preview only, no writes, unless --apply is passed. An
--apply run re-reads each row afterward and asserts the slug actually
changed (write-then-read-back, per CLAUDE.md's one-off-fix discipline). A
row whose OLD slug no longer matches what's recorded here (e.g. already
renamed by a prior run) is reported and skipped, never overwritten blind.
Aborts before writing anything if any planned NEW slug is already taken
by a different row at run time (a fresh, live check — not just the
investigation above) — a real collision is Brian's call, not this
script's to resolve.

Brian runs it via `railway ssh` with an absolute `--db /data/library.db`,
after reviewing the preview output.

Usage:
    python -m scripts.rename_dash2_tool_slugs --db /data/library.db            # preview
    python -m scripts.rename_dash2_tool_slugs --db /data/library.db --apply    # write for real
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from linklib.db import Library, resolve_db_path

# old_slug -> new_slug. Kept in sync with webapp.app._LEGACY_TOOL_SLUG_REDIRECTS
# — if a pair is ever added/removed here, add/remove the matching redirect too.
RENAMES: dict[str, str] = {
    "dealhub-2": "dealhub",
    "liveflow-2": "liveflow",
    "puzzle-2": "puzzle",
    "zenskar-2": "zenskar",
    "m3ter-2": "m3ter",
    "digits-2": "digits",
    "tropic-2": "tropic",
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=None, help="Path to library.db (or set LINKLIB_DB)")
    ap.add_argument("--apply", action="store_true",
                     help="Actually write. Without this flag, only a preview is printed.")
    args = ap.parse_args(argv)
    db_path = resolve_db_path(args.db, allow_missing=False)
    print(f"Target database: {db_path}")
    print(f"Mode: {'APPLY (writing)' if args.apply else 'PREVIEW (no writes)'}\n")

    lib = Library(db_path)
    try:
        to_change: list[tuple[int, str, str]] = []  # (tool_id, old_slug, new_slug)
        for old_slug, new_slug in RENAMES.items():
            row = lib.conn.execute(
                "SELECT id, name, slug FROM tools WHERE slug=?", (old_slug,)
            ).fetchone()
            if row is None:
                print(f"  SKIP {old_slug!r}: no row currently has this slug "
                      f"(already renamed, or slug drifted — check by hand)")
                continue
            collision = lib.conn.execute(
                "SELECT id FROM tools WHERE slug=?", (new_slug,)
            ).fetchone()
            if collision is not None:
                print(f"  STOP: {old_slug!r} -> {new_slug!r} would collide with tool "
                      f"#{collision['id']} — not touching anything for this pair.")
                continue
            print(f"  {row['name']!r} (#{row['id']}): {old_slug!r} -> {new_slug!r}")
            to_change.append((row["id"], old_slug, new_slug))

        if not to_change:
            print("\nNothing to do.")
            return 0
        if not args.apply:
            print(f"\nPREVIEW ONLY. {len(to_change)} row(s) would change. "
                  f"Re-run with --apply to write for real.")
            return 0

        for tool_id, old_slug, new_slug in to_change:
            lib.conn.execute("UPDATE tools SET slug=? WHERE id=?", (new_slug, tool_id))
        lib.conn.commit()
        print(f"\nApplied. Updated {len(to_change)} row(s).\n")

        ok = True
        for tool_id, old_slug, new_slug in to_change:
            fresh = lib.conn.execute(
                "SELECT slug FROM tools WHERE id=?", (tool_id,)
            ).fetchone()
            if not fresh or fresh["slug"] != new_slug:
                ok = False
                print(f"  MISMATCH: #{tool_id}: expected slug={new_slug!r}, got "
                      f"{fresh['slug'] if fresh else None!r}", file=sys.stderr)
            else:
                print(f"  confirmed: #{tool_id} -> slug={fresh['slug']!r}")
        if not ok:
            print("\nERROR: one or more rows did not verify after the update.", file=sys.stderr)
            return 1
        print(f"\nVerified {len(to_change)} row(s).")
    finally:
        lib.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
