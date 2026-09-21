#!/usr/bin/env python3
"""One-off cleanup for content written before `normalize_voice_mechanics`'s
invisible-character strip existed (2026-09, Part 5 of the voice review
queue safety PR — see CLAUDE.md's "Voice review queue" writeup).

The write-time backstop (`linklib.voice_mechanics.normalize_voice_mechanics`,
wired into every `Library` write path) now strips zero-width space (U+200B),
the zero-width no-break space/BOM (U+FEFF), and the word joiner (U+2060) on
every future save. It does NOT retroactively touch anything already sitting
in the database — this script is that retroactive pass, the same relationship
`scripts/fix_spaced_em_dashes.py` has to the spaced-em-dash half of the same
backstop. This is the fix for the real production row this whole feature was
built around: `category_features` id 104's `definition` ends in a zero-width
space that predates the write-time strip.

Deliberately reuses `fix_spaced_em_dashes.py`'s own `_TARGETS`/
`_SETTINGS_TARGETS` table (imported, not duplicated — the two scripts must
never drift on which columns are in scope) and mirrors its exact structure:
narrow, single-column raw UPDATEs, never `Library.update_tool`/
`upsert_community_profile`/etc., which each carry side effects (clearing
`*_needs_verification`, clearing `entity_citations`) a pure mechanical
character-strip must not trigger — this is a text substitution, not an
edit. Safe by default: preview only, no writes, unless --apply is passed.
Per the standing one-off-fix write-then-read-back practice, each --apply
write is immediately followed by a SELECT verifying the stored value now
matches. Idempotent: a column with no auto-strippable invisible character
is skipped, so a second run over an already-fixed database reports zero
changes.

Only strips the three characters `strip_safe_invisible_chars` already
treats as safe to silently remove (zero-width space, BOM, word joiner) —
never the flag-only characters (zero-width joiner, zero-width non-joiner,
bidi marks, soft hyphen), which stay in the database exactly as before,
same as the live write-time backstop's own behavior. A row this script
fixes will naturally drop off the `/admin/voice/review-queue`'s open
"invisible-character" findings on the next `reconcile_voice_review_queue()`
pass (the background checks refresher), which already closes any open row
the live scan no longer reproduces — no separate queue-closing logic is
needed here.

NOT run against library.db or any production database as part of building
this — verified only against a temp scratch SQLite DB. Running --apply
against the real database is reserved for Brian, via `railway ssh`.

Usage:
    python -m scripts.fix_invisible_characters --db library.db            # preview
    python -m scripts.fix_invisible_characters --db library.db --apply    # write for real
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from linklib.db import resolve_db_path  # noqa: E402
from linklib.voice_mechanics import strip_safe_invisible_chars  # noqa: E402
from scripts.fix_spaced_em_dashes import _SETTINGS_TARGETS, _TARGETS, _row_label  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=None, help="Path to library.db (defaults via LINKLIB_DB)")
    ap.add_argument("--apply", action="store_true", help="Actually write the fixes (default: preview only)")
    args = ap.parse_args()

    db_path = resolve_db_path(args.db, allow_missing=False)
    print(f"Database: {db_path}")
    print(f"Mode: {'APPLY (writing)' if args.apply else 'PREVIEW (no writes)'}\n")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    total_changed = 0
    total_rows_touched: set[tuple[str, object]] = set()

    try:
        for table, id_col, name_col, columns in _TARGETS:
            existing_cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            cols = [c for c in columns if c in existing_cols]
            if not cols:
                continue
            select_cols = ", ".join([id_col] + cols)
            rows = conn.execute(f"SELECT {select_cols} FROM {table}").fetchall()
            print(f"{'=' * 70}\n{table} ({len(rows)} rows, checking columns: {cols})\n{'=' * 70}")
            table_changed = 0
            for row in rows:
                row_id = row[id_col]
                for col in cols:
                    original = row[col]
                    if not original:
                        continue
                    fixed = strip_safe_invisible_chars(original)
                    if fixed == original:
                        continue
                    table_changed += 1
                    total_changed += 1
                    total_rows_touched.add((table, row_id))
                    label = _row_label(table, row_id, conn, name_col)
                    print(f"  [{table} {id_col}={row_id}] {label} — {col}:")
                    print(f"    before: {original!r}")
                    print(f"    after:  {fixed!r}")
                    if args.apply:
                        conn.execute(
                            f"UPDATE {table} SET {col}=? WHERE {id_col}=?", (fixed, row_id)
                        )
                        conn.commit()
                        check = conn.execute(
                            f"SELECT {col} FROM {table} WHERE {id_col}=?", (row_id,)
                        ).fetchone()[0]
                        assert check == fixed, (
                            f"Write-then-read-back FAILED for {table}.{col} "
                            f"{id_col}={row_id}: expected {fixed!r}, got {check!r}"
                        )
            if table_changed == 0:
                print("  (no auto-strippable invisible characters found — clean)")
            print()

        settings_table_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='settings'"
        ).fetchone()
        if settings_table_exists:
            print(f"{'=' * 70}\nsettings ({len(_SETTINGS_TARGETS)} keys checked)\n{'=' * 70}")
            settings_changed = 0
            for key in _SETTINGS_TARGETS:
                row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
                if row is None or not row[0]:
                    continue
                original = row[0]
                fixed = strip_safe_invisible_chars(original)
                if fixed == original:
                    continue
                settings_changed += 1
                total_changed += 1
                total_rows_touched.add(("settings", key))
                print(f"  [settings key={key!r}]:")
                print(f"    before: {original!r}")
                print(f"    after:  {fixed!r}")
                if args.apply:
                    conn.execute("UPDATE settings SET value=? WHERE key=?", (fixed, key))
                    conn.commit()
                    check = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()[0]
                    assert check == fixed, (
                        f"Write-then-read-back FAILED for settings key={key!r}: "
                        f"expected {fixed!r}, got {check!r}"
                    )
            if settings_changed == 0:
                print("  (no auto-strippable invisible characters found — clean)")
            print()

        print(f"{'=' * 70}")
        print(f"{'Fixed' if args.apply else 'Would fix'}: {total_changed} field(s) across "
              f"{len(total_rows_touched)} distinct row(s).")
        if not args.apply and total_changed:
            print("Re-run with --apply to write for real.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
