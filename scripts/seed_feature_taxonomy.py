#!/usr/bin/env python3
"""Seed the Feature Taxonomy (docs/FEATURE_TAXONOMY.md is canon) from the
nine-vendor pilot's three CSVs in scripts/seed_data/:

  seed_category_features.csv  -> category_features  (the curated per-category list)
  seed_tool_feature_links.csv -> tool_feature_links  (each tool's designations)
  seed_review_queue.csv       -> feature_review_queue (proposals awaiting Brian's review)

Idempotent and safe to re-run: category_features/tool_feature_links rows are
per-row deduped (a category_feature that already exists by name is skipped,
not duplicated; a tool_feature_link is upserted, not inserted twice), and
feature_review_queue rows are skipped if an equivalent entry (same source,
category, tool, and proposed feature name) already exists in any status.
Nothing here is wired into an app-boot hook — like seed_tools.py and
seed_communities.py, this is meant to be run once by hand.

Category resolution (Brian's decision, 2026-08 Phase 0 investigation):
  - ERP and FP&A must already exist as tool_categories rows — this script
    resolves against them by exact name and aborts with a clear report if
    either is missing, rather than guessing or creating them.
  - Close Management is a genuinely NEW pill: created here if missing
    (idempotent — add_tool_category is a no-op-with-report if it already
    exists), and the three tools with a Close Management link/feature row in
    the CSVs (FloQast, Numeric, Ledge, per the CSV data itself, not a
    hardcoded list) get the "Close Management" tag ADDED to their existing
    categories_json — additive only, nothing is removed.

Usage:
    LINKLIB_DB=/data/library.db python -m scripts.seed_feature_taxonomy
    python -m scripts.seed_feature_taxonomy --db /data/library.db
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from linklib.db import Library, resolve_db_path

_SEED_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seed_data")
_CATEGORY_FEATURES_CSV = os.path.join(_SEED_DIR, "seed_category_features.csv")
_TOOL_FEATURE_LINKS_CSV = os.path.join(_SEED_DIR, "seed_tool_feature_links.csv")
_REVIEW_QUEUE_CSV = os.path.join(_SEED_DIR, "seed_review_queue.csv")

# The one category this seed is allowed to create if missing. Every other
# category referenced by the CSVs must already exist — an unresolved name
# aborts the run rather than silently guessing (house rule).
_NEW_CATEGORY = "Close Management"

# Placeholder suite notation for NetSuite (rules-doc §5's "beyond the office
# of the CFO" case) — Brian's own wording from the build brief, seeded as a
# placeholder for him to finalize later. Only NetSuite among the nine pilot
# tools has a broader operational suite in this sense.
_NETSUITE_TOOL_NAME = "NetSuite (acquired by Oracle)"
_NETSUITE_SUITE_NOTE = (
    "Part of a broader vendor suite including operational solutions beyond "
    "the office of the CFO (e.g., CRM, HRIS)."
)


def seed_suite_notes(lib: Library) -> bool:
    """Sets NetSuite's placeholder suite_note if it isn't already set.
    Idempotent by design (not just by accident of re-running the same
    write): once Brian finalizes real copy through the admin edit form
    (set_tool_suite_note), a re-run of this script must never clobber it
    back to the placeholder — so this only ever writes when the column is
    still empty."""
    tool = lib.get_tool_by_name(_NETSUITE_TOOL_NAME)
    if tool is None:
        sys.exit(f"Aborting — could not resolve the NetSuite tool by name {_NETSUITE_TOOL_NAME!r}.")
    if (tool.get("suite_note") or "").strip():
        print(f'  SKIP  suite_note for "{_NETSUITE_TOOL_NAME}" (already set)')
        return False
    lib.set_tool_suite_note(tool["id"], _NETSUITE_SUITE_NOTE, source="script")
    print(f'  SET   suite_note for "{_NETSUITE_TOOL_NAME}"')
    return True


def _read_csv(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return [row for row in csv.DictReader(f) if any((v or "").strip() for v in row.values())]


def _bool(s: str) -> int:
    return 1 if (s or "").strip().lower() == "true" else 0


def _resolve_categories(lib: Library, category_names: set[str]) -> dict[str, int]:
    """Resolves every category name in the CSVs to a live tool_categories id.
    Creates ONLY _NEW_CATEGORY if it's missing; any other unresolved name is
    a hard abort with a report, never a guess."""
    resolved: dict[str, int] = {}
    unresolved: list[str] = []
    for name in sorted(category_names):
        cid = lib.get_tool_category_id(name)
        if cid is not None:
            resolved[name] = cid
            continue
        if name == _NEW_CATEGORY:
            cid = lib.add_tool_category(_NEW_CATEGORY, source="script")
            print(f'  CREATED new category pill: "{_NEW_CATEGORY}" (id={cid})')
            resolved[name] = cid
            continue
        unresolved.append(name)
    if unresolved:
        sys.exit(
            "Aborting — the following categories in the seed CSVs don't match any "
            "live tool_categories row and aren't the one category this script is "
            f"allowed to create ({_NEW_CATEGORY!r}):\n"
            + "\n".join(f"  - {n}" for n in unresolved)
            + "\nResolve the name mismatch (or add the category by hand at "
              "/admin/tools/software/categories) before re-running."
        )
    return resolved


def seed_category_features(lib: Library, categories: dict[str, int]) -> tuple[int, int]:
    added = skipped = 0
    for row in _read_csv(_CATEGORY_FEATURES_CSV):
        category_id = categories[row["category"].strip()]
        name = row["feature_name"].strip()
        existing = next(
            (f for f in lib.list_category_features(category_id, include_retired=True)
             if f["name"].strip().lower() == name.lower()),
            None,
        )
        if existing:
            print(f'  SKIP  category_feature "{row["category"]}" / "{name}" (already exists)')
            skipped += 1
            continue
        lib.add_category_feature(
            category_id, name,
            definition=row.get("definition", ""),
            pointer_note=row.get("pointer_note", ""),
            sort_order=int(row["sort_order"]),
            source="script",
        )
        print(f'  ADDED category_feature "{row["category"]}" / "{name}"')
        added += 1
    return added, skipped


def seed_tool_feature_links(lib: Library, categories: dict[str, int]) -> tuple[int, int, int]:
    added = updated = tagged = 0
    unresolved_tools: set[str] = set()
    unresolved_features: set[str] = set()
    for row in _read_csv(_TOOL_FEATURE_LINKS_CSV):
        tool_name = row["tool"].strip()
        category_name = row["category"].strip()
        category_id = categories[category_name]
        feature_name = row["feature_name"].strip()

        tool = lib.get_tool_by_name(tool_name)
        if tool is None:
            unresolved_tools.add(tool_name)
            continue
        feature = next(
            (f for f in lib.list_category_features(category_id, include_retired=True)
             if f["name"].strip().lower() == feature_name.lower()),
            None,
        )
        if feature is None:
            unresolved_features.add(f"{category_name} / {feature_name}")
            continue

        existed_before = lib.get_tool_feature_link(tool["id"], feature["id"]) is not None
        lib.upsert_tool_feature_link(
            tool["id"], feature["id"],
            availability=row["availability"].strip(),
            ai_enabled=_bool(row["ai_enabled"]),
            verified_as_of=row["verified_as_of"].strip(),
            note=row.get("note", ""),
        )
        if existed_before:
            updated += 1
        else:
            added += 1

        # Additive-only category tagging: the CSV data itself, not a
        # hardcoded list, is exactly the set of tools that get the new
        # Close Management pill (Brian's explicit call — see module docstring).
        if category_name == _NEW_CATEGORY:
            if lib.add_category_to_tool(tool["id"], _NEW_CATEGORY):
                tagged += 1
                print(f'  TAGGED "{tool_name}" with "{_NEW_CATEGORY}" (additive, existing tags kept)')

    if unresolved_tools or unresolved_features:
        sys.exit(
            "Aborting — seed_tool_feature_links.csv references rows that don't resolve:\n"
            + ("".join(f"  - unresolved tool: {t}\n" for t in sorted(unresolved_tools)))
            + ("".join(f"  - unresolved feature: {f}\n" for f in sorted(unresolved_features)))
            + "Fix the seed CSV or the underlying data, then re-run."
        )
    return added, updated, tagged


# Matches "AI=true" / "AI=false" (case-insensitive) inside an articulation
# string, e.g. "Native, AI=true. Source: ...".
_AI_FLAG_RE = re.compile(r"AI\s*=\s*(true|false)", re.IGNORECASE)
# Extracts a quoted pointer-note suggestion from an articulation, e.g.
# "...carries a pointer note: 'Dedicated headcount-planning tools exist...'"
_POINTER_NOTE_RE = re.compile(r"pointer note:\s*'([^']+)'", re.IGNORECASE)
_DEFAULT_VERIFIED_AS_OF = "2026-08-19"  # the pilot's own scan/verification date


def seed_review_queue(lib: Library, categories: dict[str, int],
                       tools_by_category: dict[str, list[str]]) -> tuple[int, int]:
    added = skipped = 0
    for row in _read_csv(_REVIEW_QUEUE_CSV):
        category_name = row["category"].strip()
        category_id = categories[category_name]
        tool_name = row["tool"].strip()
        feature_name = row["proposed_feature"].strip()
        articulation = row["articulation_and_source"].strip()

        ai_match = _AI_FLAG_RE.search(articulation)
        ai_enabled = 1 if (ai_match and ai_match.group(1).lower() == "true") else 0
        pointer_match = _POINTER_NOTE_RE.search(articulation)
        pointer_note = pointer_match.group(1) if pointer_match else ""

        if tool_name == "(all three)":
            link_tool_names = tools_by_category.get(category_name, [])
            single_tool_id = None
        else:
            tool = lib.get_tool_by_name(tool_name)
            if tool is None:
                sys.exit(f"Aborting — review queue references unresolved tool: {tool_name!r}")
            link_tool_names = [tool_name]
            single_tool_id = tool["id"]

        # Idempotency: skip if an equivalent proposal (same source, category,
        # tool, feature name) already exists in the queue, in any status —
        # a re-run must not pile up duplicate proposals for Brian to review.
        existing = [
            q for q in lib.list_feature_review_queue(status=None)
            if q["source"] == "scan" and q["category_id"] == category_id
            and q["tool_id"] == single_tool_id
            and (q["payload"].get("feature", {}).get("name", "").strip().lower() == feature_name.lower())
        ]
        if existing:
            print(f'  SKIP  review queue "{category_name}" / "{tool_name}" / "{feature_name}" (already queued)')
            skipped += 1
            continue

        links = []
        for name in link_tool_names:
            t = lib.get_tool_by_name(name)
            if t is None:
                sys.exit(f"Aborting — review queue references unresolved tool: {name!r}")
            links.append({
                "tool_id": t["id"], "availability": "native", "ai_enabled": ai_enabled,
                "verified_as_of": _DEFAULT_VERIFIED_AS_OF,
            })

        payload = {
            "category_id": category_id,
            "feature": {"name": feature_name, "pointer_note": pointer_note},
            "links": links,
        }
        lib.add_feature_review_queue_item(
            source="scan", proposal_type=row["proposal_type"].strip(), payload=payload,
            category_id=category_id, tool_id=single_tool_id, articulation=articulation,
        )
        print(f'  QUEUED "{category_name}" / "{tool_name}" / "{feature_name}"')
        added += 1
    return added, skipped


def main():
    parser = argparse.ArgumentParser(description="Seed the Feature Taxonomy from the pilot CSVs.")
    parser.add_argument("--db", default=None, help="Path to library.db (or set LINKLIB_DB)")
    args = parser.parse_args()
    # Deliberately NOT allow_missing — this seed assumes the pilot's tools
    # (Rillet, Campfire, NetSuite, ...) already exist, so it must run against
    # a real, already-populated DB, never silently create an empty one.
    db_path = resolve_db_path(args.db, allow_missing=False)

    lib = Library(db_path)
    try:
        category_rows = _read_csv(_CATEGORY_FEATURES_CSV)
        link_rows = _read_csv(_TOOL_FEATURE_LINKS_CSV)
        queue_rows = _read_csv(_REVIEW_QUEUE_CSV)
        all_category_names = (
            {r["category"].strip() for r in category_rows}
            | {r["category"].strip() for r in link_rows}
            | {r["category"].strip() for r in queue_rows}
        )
        tools_by_category: dict[str, list[str]] = {}
        for r in link_rows:
            tools_by_category.setdefault(r["category"].strip(), [])
            if r["tool"].strip() not in tools_by_category[r["category"].strip()]:
                tools_by_category[r["category"].strip()].append(r["tool"].strip())

        print(f"Resolved DB path: {db_path}\n")
        print("Resolving categories...")
        categories = _resolve_categories(lib, all_category_names)
        for name, cid in sorted(categories.items()):
            print(f'  {name} -> id={cid}')

        print("\nSeeding category_features...")
        cf_added, cf_skipped = seed_category_features(lib, categories)

        print("\nSeeding tool_feature_links...")
        tfl_added, tfl_updated, tagged = seed_tool_feature_links(lib, categories)

        print("\nSeeding feature_review_queue...")
        rq_added, rq_skipped = seed_review_queue(lib, categories, tools_by_category)

        print("\nSeeding suite_note (NetSuite placeholder)...")
        suite_note_set = seed_suite_notes(lib)

        # Write-then-read-back verification, per house rules — print live
        # row counts straight from the DB, not from the counters above.
        print("\n--- Write-then-read-back verification ---")
        for table in ("category_features", "tool_feature_links", "feature_review_queue"):
            n = lib.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"  {table}: {n} rows")
        n_close_mgmt_tools = lib.conn.execute(
            "SELECT COUNT(*) FROM tools WHERE categories_json LIKE ?", (f'%"{_NEW_CATEGORY}"%',)
        ).fetchone()[0]
        print(f'  tools tagged "{_NEW_CATEGORY}": {n_close_mgmt_tools}')
        netsuite_note = lib.conn.execute(
            "SELECT suite_note FROM tools WHERE name=?", (_NETSUITE_TOOL_NAME,)
        ).fetchone()[0]
        print(f'  NetSuite suite_note: {netsuite_note!r}')

        print(
            f"\ncategory_features: {cf_added} added, {cf_skipped} skipped (already existed)\n"
            f"tool_feature_links: {tfl_added} added, {tfl_updated} updated, {tagged} tools newly tagged\n"
            f"feature_review_queue: {rq_added} added, {rq_skipped} skipped (already queued)\n"
            f"suite_note: {'set' if suite_note_set else 'skipped (already set)'}"
        )
    finally:
        lib.close()


if __name__ == "__main__":
    main()
