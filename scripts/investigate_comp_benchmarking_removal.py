#!/usr/bin/env python3
"""Read-only investigation for the Pave/Culpepper/Radford removal (2026-08).

Reports the FULL footprint of these three compensation-benchmarking vendors
across tools/tool_feature_links/tool_leads/tool_competitors/
tool_name_dedupe_decisions/entity_citations/narrative_review_log/
feature_review_queue, plus the category_features roster for Headcount
Planning (category_id=34, per scripts/seed_data/
headcount_planning_feature_framework.json's own comment) so Brian can
confirm exactly what a hard-delete would touch before any write happens.

Never writes anything — no --apply flag exists on purpose. This is Phase 0
of the removal: investigate and propose, do not execute (see CLAUDE.md's
"no dead data" / one-off-admin-fix discipline).

Usage:
    python -m scripts.investigate_comp_benchmarking_removal --db library.db
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from linklib.db import Library, resolve_db_path

VENDOR_NAMES = ["Pave", "Culpepper", "Radford (acquired by Aon)"]
HEADCOUNT_CATEGORY_ID = 34


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    args = ap.parse_args()
    db_path = resolve_db_path(args.db)
    print(f"Resolved DB path: {db_path}\n")

    lib = Library(db_path)
    conn = lib.conn
    try:
        # 1. Resolve the three tool rows.
        tool_rows = []
        for name in VENDOR_NAMES:
            row = conn.execute(
                "SELECT * FROM tools WHERE name=?", (name,)
            ).fetchone()
            if row is None:
                # fall back to a loose match in case the stored name differs
                row = conn.execute(
                    "SELECT * FROM tools WHERE name LIKE ?", (f"%{name.split(' ')[0]}%",)
                ).fetchone()
            tool_rows.append(dict(row) if row else None)

        print("=" * 70)
        print("1. Tool rows")
        print("=" * 70)
        tool_ids = []
        for name, row in zip(VENDOR_NAMES, tool_rows):
            if row is None:
                print(f"  NOT FOUND: {name!r}")
                continue
            tool_ids.append(row["id"])
            print(f"  id={row['id']:>4}  name={row['name']!r:35} slug={row['slug']!r}")
            print(f"        url={row['url']}")
            print(f"        categories_json={row['categories_json']}")
        if not tool_ids:
            print("\nNo matching tool rows found at all — stopping.")
            return
        print(f"\nResolved tool_ids: {tool_ids}")

        # 2. tool_feature_links for these tools, with feature/category detail.
        print("\n" + "=" * 70)
        print("2. tool_feature_links for these tools")
        print("=" * 70)
        placeholders = ",".join("?" * len(tool_ids))
        rows = conn.execute(
            f"""SELECT l.*, cf.name AS feature_name, cf.category_id, cf.retired_at,
                       tc.name AS category_name
                FROM tool_feature_links l
                JOIN category_features cf ON cf.id = l.feature_id
                JOIN tool_categories tc ON tc.id = cf.category_id
                WHERE l.tool_id IN ({placeholders})
                ORDER BY tc.name, cf.name""",
            tool_ids,
        ).fetchall()
        affected_feature_ids = set()
        for r in rows:
            r = dict(r)
            affected_feature_ids.add(r["feature_id"])
            print(f"  tool_id={r['tool_id']}  feature_id={r['feature_id']:>4}  "
                  f"[{r['category_name']}] {r['feature_name']!r}  "
                  f"availability={r['availability']} ai_enabled={r['ai_enabled']} "
                  f"retired={'yes' if r['retired_at'] else 'no'}")
        if not rows:
            print("  (none)")

        # 3. tool_leads (informational — NOT proposed for deletion).
        print("\n" + "=" * 70)
        print("3. tool_leads (intro requests) — proposed to LEAVE UNTOUCHED")
        print("=" * 70)
        rows = conn.execute(
            f"SELECT tool_id, tool_name, COUNT(*) n FROM tool_leads "
            f"WHERE tool_id IN ({placeholders}) GROUP BY tool_id", tool_ids
        ).fetchall()
        for r in rows:
            print(f"  tool_id={r['tool_id']} ({r['tool_name']}): {r['n']} lead(s)")
        if not rows:
            print("  (none)")

        # 4. tool_competitors.
        print("\n" + "=" * 70)
        print("4. tool_competitors")
        print("=" * 70)
        rows = conn.execute(
            f"SELECT * FROM tool_competitors WHERE tool_id IN ({placeholders}) "
            f"OR competitor_id IN ({placeholders})", tool_ids + tool_ids
        ).fetchall()
        for r in rows:
            print(f"  {dict(r)}")
        if not rows:
            print("  (none)")

        # 5. tool_name_dedupe_decisions.
        print("\n" + "=" * 70)
        print("5. tool_name_dedupe_decisions")
        print("=" * 70)
        try:
            rows = conn.execute(
                f"SELECT * FROM tool_name_dedupe_decisions WHERE tool_id_a IN ({placeholders}) "
                f"OR tool_id_b IN ({placeholders})", tool_ids + tool_ids
            ).fetchall()
            for r in rows:
                print(f"  {dict(r)}")
            if not rows:
                print("  (none)")
        except Exception as e:
            print(f"  (query failed: {e})")

        # 6. entity_citations.
        print("\n" + "=" * 70)
        print("6. entity_citations (entity_type='tool')")
        print("=" * 70)
        rows = conn.execute(
            f"SELECT * FROM entity_citations WHERE entity_type='tool' AND entity_id IN ({placeholders})",
            tool_ids,
        ).fetchall()
        for r in rows:
            print(f"  entity_id={r['entity_id']} field_name={r['field_name']} "
                  f"generated_at={r['generated_at']}")
        if not rows:
            print("  (none)")

        # 7. narrative_review_log (informational — survives by design).
        print("\n" + "=" * 70)
        print("7. narrative_review_log — survives by design (append-only, like tool_audit_log)")
        print("=" * 70)
        rows = conn.execute(
            f"SELECT * FROM narrative_review_log WHERE entity_type='tool' AND item_id IN ({placeholders})",
            tool_ids,
        ).fetchall()
        for r in rows:
            print(f"  item_id={r['item_id']} field_type={r['field_type']} created_at={r['created_at']}")
        if not rows:
            print("  (none)")

        # 8. feature_review_queue — by tool_id AND by payload text match (a
        # consolidated multi-tool proposal may not set tool_id).
        print("\n" + "=" * 70)
        print("8. feature_review_queue — pending/other rows naming these vendors")
        print("=" * 70)
        rows = conn.execute(
            f"SELECT * FROM feature_review_queue WHERE tool_id IN ({placeholders})", tool_ids
        ).fetchall()
        for r in rows:
            print(f"  id={r['id']} status={r['status']} tool_id={r['tool_id']} "
                  f"proposal_type={r['proposal_type']}")
        like_clauses = " OR ".join(["payload LIKE ?"] * len(VENDOR_NAMES))
        like_params = [f"%{n.split(' ')[0]}%" for n in VENDOR_NAMES]
        payload_rows = conn.execute(
            f"SELECT * FROM feature_review_queue WHERE ({like_clauses}) AND tool_id IS NULL",
            like_params,
        ).fetchall()
        for r in payload_rows:
            print(f"  [payload match, no tool_id] id={r['id']} status={r['status']} "
                  f"proposal_type={r['proposal_type']}")
        if not rows and not payload_rows:
            print("  (none)")

        # 9. Headcount Planning category_features roster + live link counts,
        #    excluding these three tools' links, to size the soft-retire question.
        print("\n" + "=" * 70)
        print(f"9. category_features under category_id={HEADCOUNT_CATEGORY_ID} (Headcount Planning)")
        print("   — 'remaining_after_removal' excludes the 3 vendors' own links")
        print("=" * 70)
        feats = conn.execute(
            "SELECT * FROM category_features WHERE category_id=? ORDER BY sort_order, name",
            (HEADCOUNT_CATEGORY_ID,),
        ).fetchall()
        for f in feats:
            f = dict(f)
            total = conn.execute(
                "SELECT COUNT(*) FROM tool_feature_links WHERE feature_id=?", (f["id"],)
            ).fetchone()[0]
            remaining = conn.execute(
                f"SELECT COUNT(*) FROM tool_feature_links WHERE feature_id=? "
                f"AND tool_id NOT IN ({placeholders})",
                [f["id"]] + tool_ids,
            ).fetchone()[0]
            flag = ""
            if f["id"] in affected_feature_ids and remaining == 0:
                flag = "  <-- ALL links would be from these 3 vendors: PROPOSE SOFT-RETIRE"
            retired = "RETIRED" if f["retired_at"] else "live"
            job_arch = "  <-- 'Job architecture' rename target" if f["name"].strip().lower() == "job architecture" else ""
            print(f"  id={f['id']:>4}  [{retired}]  {f['name']!r:35} "
                  f"total_links={total}  remaining_after_removal={remaining}{flag}{job_arch}")
        if not feats:
            print("  (no category_features rows exist yet for this category)")

        print("\nDone. No writes performed.")
    finally:
        lib.close()


if __name__ == "__main__":
    main()
