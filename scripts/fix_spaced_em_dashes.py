#!/usr/bin/env python3
"""One-off cleanup for the 2026-08 spaced-em-dash incident: pure deterministic
text substitution (no API/model call) that collapses every spaced em dash
(and spaced "--" used as a dash) already sitting in the database into the
unspaced form `voice_core`'s "HARD MECHANICAL RULES" require.

This is a CLEANUP script for content that was ALREADY WRITTEN before the
permanent backstop (`linklib.voice_mechanics.normalize_voice_mechanics`,
now wired into every `Library` write path — see CLAUDE.md's matching 2026-08
bullet) started running on every future save. It does not fix the root cause
— the backstop does that — it fixes the 63 rows / 52 tools (mostly
`agent_taxonomy_note`) and any affected `community_profiles` rows that were
written by the full 157-tool + 40-community regeneration BEFORE the backstop
existed.

Deliberately narrow, single-column raw UPDATEs — NOT `Library.update_tool`/
`update_tool_agent_taxonomy`/`upsert_community_profile`, which each carry
side effects a pure mechanical whitespace fix must not trigger (clearing
`*_needs_verification`, clearing `entity_citations` on the assumption a
human just hand-edited the field — neither is true here: this is a text
substitution, not an edit). Every touched row keeps its existing
verification/citation state exactly as it was.

Safe by default: preview only (prints every row that WOULD change, old vs.
new text, with the change visibly highlighted), no writes, unless --apply is
passed. Per the standing one-off-fix write-then-read-back practice, each
--apply write is immediately followed by a SELECT of that exact row/column,
asserting the stored value now equals the expected fixed text before moving
on — not just trusting `cursor.rowcount`. Idempotent: a column that's
already clean (no spaced em dash) is skipped, so a second run over an
already-fixed database reports zero changes.

NOT run against library.db or any production database as part of building
this — verified only against a temp scratch SQLite DB. Running --apply
against the real database is reserved for Brian, via `railway ssh`.

Usage:
    python -m scripts.fix_spaced_em_dashes --db library.db            # preview
    python -m scripts.fix_spaced_em_dashes --db library.db --apply    # write for real
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from linklib.db import resolve_db_path  # noqa: E402
from linklib.voice_mechanics import fix_spaced_em_dashes  # noqa: E402

# (table, id_column, name_column_or_None, [prose columns to check])
# name_column is used only for a readable per-row label in the printout; it
# is never written to. Mirrors exactly the write paths patched with
# normalize_voice_mechanics() in linklib/db.py (see that PR's diff) — the
# same columns the backstop now guards going forward are the ones this
# cleanup fixes for content written before it existed.
_TARGETS = [
    ("tools", "id", "name",
     ["description", "summary", "agent_taxonomy_note", "competitive_differentiation", "suite_note"]),
    ("communities", "id", "name",
     ["demographic", "cost_note", "notes", "local_markets"]),
    ("community_profiles", "community_id", None,
     ["ideal_member", "anti_fit", "value_prop", "format_reality", "engagement_level",
      "sponsor_relationship_note", "application_friction", "cost_value_verdict",
      "notable_members", "public_criticism", "verdict_summary", "business_model",
      "primary_purpose", "cpe_eligible", "platform_type", "meeting_format",
      "event_style", "seniority_band", "resources_included", "stage_focus",
      "jobs_program", "team_or_individual"]),
]


def _row_label(table: str, row_id, conn: sqlite3.Connection, name_column: str | None) -> str:
    if table == "community_profiles":
        r = conn.execute("SELECT name FROM communities WHERE id=?", (row_id,)).fetchone()
        return r[0] if r else f"community_id={row_id}"
    if name_column:
        r = conn.execute(f"SELECT {name_column} FROM {table} WHERE id=?", (row_id,)).fetchone()
        return r[0] if r else f"id={row_id}"
    return f"id={row_id}"


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
    total_rows_touched: set[tuple[str, int]] = set()

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
                    fixed = fix_spaced_em_dashes(original)
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
                        # Write-then-read-back verification, per row/column,
                        # per the standing one-off-fix discipline — never
                        # just trust that the UPDATE succeeded.
                        check = conn.execute(
                            f"SELECT {col} FROM {table} WHERE {id_col}=?", (row_id,)
                        ).fetchone()[0]
                        assert check == fixed, (
                            f"Write-then-read-back FAILED for {table}.{col} "
                            f"{id_col}={row_id}: expected {fixed!r}, got {check!r}"
                        )
            if table_changed == 0:
                print("  (no spaced em dashes found — clean)")
            print()

        print(f"{'=' * 70}")
        print(f"{'Fixed' if args.apply else 'Would fix'}: {total_changed} field(s) across "
              f"{len(total_rows_touched)} distinct row(s).")
        if not args.apply and total_changed:
            print("\nRe-run with --apply to write these fixes for real.")
        if args.apply:
            print("\nEvery write above was verified by an immediate read-back before moving to "
                  "the next row — see the per-row output for the exact before/after text.")
        print(f"{'=' * 70}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
