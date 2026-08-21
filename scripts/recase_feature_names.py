#!/usr/bin/env python3
"""Feature Taxonomy naming rule #3 (docs/FEATURE_TAXONOMY.md §3, added in the
Manage Features pivot-table redesign, Phase 1c): feature names use sentence
case — capitalize only the first word, plus any acronym or proper noun that's
always capitalized on its own. The 62 seeded feature names (and anything
added since, by hand or via review-queue approval) predate that rule and are
mostly Title Case (e.g. "Automated Journal Entry Creation").

This is a one-time, idempotent recasing pass over every LIVE
category_features.name value (retired rows are skipped — they're historical
record, not the active vocabulary the naming rule governs). Re-running is a
safe no-op: a name already in sentence case recases to itself.

Sentence-case heuristic (deliberately conservative — see the printed diff
before trusting it): the first word is capitalized; every other word is
lowercased UNLESS it's already all-uppercase (kept as-is — an acronym like
"ASC", "SOX", "GRC", "IFRS", "GAAP", "AI", "API", "ERP", "ARR", "KPI") or
contains a digit (kept as-is — "606", "1099"). This is exactly what the
naming rule's own worked examples need ("ASC 606 revenue recognition",
"GRC & SOX controls management") without a hardcoded word list. It does NOT
know about proper nouns that aren't already capitalized in the source data —
the naming rule already prohibits vendor/brand names in feature names (§3),
so this is not expected to matter in practice, but review the printed diff
before running --apply regardless, per the standing one-off-fix discipline.

Safe by default: preview only, no writes, unless --apply is passed. Per the
standing write-then-read-back practice, an --apply run re-reads every
changed row afterward and asserts the stored name matches what was printed
before reporting success.

NOT run with --apply against library.db or any production database as part
of building this PR — tested only against a throwaway scratch SQLite DB.
Running --apply against the real database is reserved for Brian, via
`railway ssh`.

Usage:
    python -m scripts.recase_feature_names --db library.db            # preview
    python -m scripts.recase_feature_names --db library.db --apply    # write for real

Lives in scripts/, not scripts/archive/, until it's actually been run against
production — same convention as drop_legacy_tool_features.py: a one-time
script moves to scripts/archive/ once its job is done (git mv), not before.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from linklib.db import resolve_db_path


def sentence_case(name: str) -> str:
    words = name.split(" ")
    out = []
    for i, w in enumerate(words):
        core = w.strip("()[]\"'")
        if not core:
            out.append(w)
        elif core.isupper() and len(core) > 1:
            out.append(w)  # acronym — keep as-is (ASC, SOX, GRC, IFRS, GAAP, AI, API, ERP, ...)
        elif any(c.isdigit() for c in core):
            out.append(w)  # "606", "1099" — keep as-is
        elif i == 0:
            out.append(w[:1].upper() + w[1:].lower())
        else:
            out.append(w.lower())
    return " ".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=None, help="Path to library.db (or set LINKLIB_DB)")
    parser.add_argument("--apply", action="store_true", help="Write the recased names for real (default: preview only)")
    args = parser.parse_args()

    db_path = resolve_db_path(args.db)
    print(f"Using database: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        "SELECT id, category_id, name FROM category_features WHERE retired_at='' ORDER BY category_id, name"
    ).fetchall()

    changes = []
    for r in rows:
        new_name = sentence_case(r["name"])
        if new_name != r["name"]:
            changes.append((r["id"], r["category_id"], r["name"], new_name))

    if not changes:
        print(f"Checked {len(rows)} live feature(s) — already in sentence case, nothing to do.")
        conn.close()
        return

    print(f"Checked {len(rows)} live feature(s) — {len(changes)} need recasing:")
    for fid, cat_id, old, new in changes:
        print(f'  id={fid} category_id={cat_id}: "{old}" -> "{new}"')

    if not args.apply:
        print("\nPreview only — pass --apply to write these changes for real.")
        conn.close()
        return

    for fid, cat_id, old, new in changes:
        cur = conn.execute("UPDATE category_features SET name=? WHERE id=?", (new, fid))
        assert cur.rowcount == 1, f"Expected to update exactly one row for id={fid}, updated {cur.rowcount}"
    conn.commit()

    # Write-then-read-back verification, per the standing one-off-fix discipline.
    ok = True
    for fid, cat_id, old, new in changes:
        row = conn.execute("SELECT name FROM category_features WHERE id=?", (fid,)).fetchone()
        if row is None or row["name"] != new:
            print(f"  MISMATCH: id={fid} expected \"{new}\", found {row['name'] if row else '(missing)'}")
            ok = False
    conn.close()

    if ok:
        print(f"\nApplied and verified {len(changes)} recased name(s).")
    else:
        print("\nApplied with at least one verification mismatch — see above. Investigate before trusting this run.")
        sys.exit(1)


if __name__ == "__main__":
    main()
