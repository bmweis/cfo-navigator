#!/usr/bin/env python3
"""Phase 3 (docs/FEATURE_TAXONOMY.md §10, origination mode) — runs the full
research/cluster/judge pipeline (linklib.feature_scan.originate_category_features)
against one real Toolbox category's whole tool roster, and either previews
the result or writes it to feature_review_queue for real.

Unlike scripts/backfill_logos.py's preview (which lists already-known rows
for free before spending any API quota), there is no cheap way to preview
this pipeline's output — the research/cluster/judge calls ARE the work, so
even a preview run makes real, billed API calls (Exa + Claude). Default
(no --apply) runs the full pipeline and prints exactly what WOULD be
queued, writing nothing; --apply additionally writes it to
feature_review_queue (source='scan') via the same code path.

Cost, roughly, per 10-tool category (see the Phase 3 build proposal): 10
research calls + 1 clustering call + one judgment call per multi-member
cluster — ballpark $2-5 total for a category this size. Set
ANTHROPIC_API_KEY and EXA_API_KEY first.

Usage:
    python -m scripts.originate_category_features --db library.db --category Neobanking
    python -m scripts.originate_category_features --db library.db --category Neobanking --apply
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from linklib.db import Library, resolve_db_path
from linklib.feature_scan import DEFAULT_MODEL, originate_category_features


def _tool_roster_for_category(lib: Library, category_name: str) -> list[dict]:
    """[{"id","name","url"}, ...] for every tool exactly tagged with
    category_name — LIKE-filtered then exact-membership-checked against
    the parsed categories_json list, same double-check pattern
    Library.delete_tool_category/rename_tool_category already use, so a
    category name that's a substring of another (e.g. a hypothetical
    "FP&A" vs. "FP&A Planning") can't produce a false match."""
    rows = lib.conn.execute(
        "SELECT id, name, url, categories_json FROM tools WHERE categories_json LIKE ?",
        (f'%"{category_name}"%',),
    ).fetchall()
    roster = []
    for r in rows:
        cats = json.loads(r["categories_json"]) or []
        if category_name in cats:
            roster.append({"id": r["id"], "name": r["name"], "url": r["url"]})
    return roster


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None, help="Path to library.db (or set LINKLIB_DB)")
    ap.add_argument("--category", required=True, help="tool_categories name, e.g. Neobanking")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--apply", action="store_true",
                     help="Actually write to feature_review_queue. Without this flag, the full "
                          "pipeline still runs (real API cost) but nothing is written — every "
                          "payload that WOULD be queued is printed for review instead.")
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set — nothing to run.", file=sys.stderr)
        return 1
    if not os.environ.get("EXA_API_KEY"):
        print("Note: EXA_API_KEY is not set — every tool's research will ground on the "
              "model's own knowledge only (low_confidence).", file=sys.stderr)

    db_path = resolve_db_path(args.db)
    print(f"Reading from: {db_path}\n")

    lib = Library(db_path)
    try:
        category_id = lib.get_tool_category_id(args.category)
        if category_id is None:
            print(f"No tool_categories row named {args.category!r}. Check "
                  "/admin/tools/software/categories for the exact name.", file=sys.stderr)
            return 1

        roster = _tool_roster_for_category(lib, args.category)
        if not roster:
            print(f"No tools are tagged with category {args.category!r} — nothing to research.",
                  file=sys.stderr)
            return 1

        existing_features = lib.list_category_features(category_id)
        existing_names = [f["name"] for f in existing_features]

        print(f"Category: {args.category} (id={category_id})")
        print(f"Roster: {len(roster)} tool(s) — {', '.join(t['name'] for t in roster)}")
        if existing_names:
            print(f"Already-curated features ({len(existing_names)}): {', '.join(existing_names)}")
        print(f"Mode: {'APPLY (writing to feature_review_queue)' if args.apply else 'PREVIEW (no writes)'}")
        print()

        summary = originate_category_features(
            lib, category_id, args.category, roster,
            existing_feature_names=existing_names, model=args.model, dry_run=not args.apply,
        )
    finally:
        lib.close()

    if summary is None:
        print("Every tool's research failed — nothing to report. Check ANTHROPIC_API_KEY/"
              "EXA_API_KEY and network access, or re-run with logging enabled.", file=sys.stderr)
        return 1

    print("=" * 80)
    print(f"Tools researched: {summary.tools_researched}   Tools failed: {summary.tools_failed}")
    print(f"Candidates drafted: {summary.candidates_total}   Clusters found: {summary.clusters_found}")
    print(f"Features {'queued' if args.apply else 'that would be queued'}: {summary.features_queued}  "
          f"(merged: {summary.features_merged}, single-tool: {summary.features_split})")
    print(f"Cost: Exa ${summary.exa_cost_usd:.4f}  Claude ${summary.claude_cost_usd:.4f}  "
          f"Total ${summary.exa_cost_usd + summary.claude_cost_usd:.4f}")
    print("=" * 80)
    print()

    for i, payload in enumerate(summary.queued_payloads, 1):
        feature = payload["feature"]
        links = payload["links"]
        print(f"{i}. {feature['name']}  ({len(links)} tool{'s' if len(links) != 1 else ''})")
        print(f"   {feature['definition']}")
        for link in links:
            print(f"   - tool_id={link['tool_id']}  availability={link['availability']}  "
                  f"ai_enabled={link['ai_enabled']}  source={link['source_url'] or '(none cited)'}")
        print()

    if args.apply:
        print(f"Written to feature_review_queue: {len(summary.queue_item_ids)} item(s), "
              f"ids={summary.queue_item_ids}")
        print("Review at /admin/tools/software/feature-review-queue.")
    else:
        print("Nothing was written. Re-run with --apply to write these to feature_review_queue.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
