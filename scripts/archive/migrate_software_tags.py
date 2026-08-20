#!/usr/bin/env python3
"""One-off migration: consolidate the CFO Toolbox Software tag vocabulary
from the old 21-category ad hoc list down to the fixed 15-tag taxonomy
approved for the Software search overhaul (Phase 1).

Safe to re-run — remapping is idempotent (mapping a category that's already
one of the 15 new names is a no-op) and the category table is rebuilt from
scratch each run rather than incrementally patched, so a partial or repeated
run converges on the same end state.

Two things happen:
  1. Every `tools.categories_json` row gets its old category names replaced
     with their new-taxonomy equivalents, deduped and sorted (a tool tagged
     ["Spend Management", "Procurement"] collapses to ["Procurement/Spend"]).
  2. The `tool_categories` table (the filter-pill vocabulary) is replaced
     wholesale with the 15 new categories — this is simpler and safer than
     trying to thread `rename_tool_category`/`delete_tool_category` through
     a many-old-names-to-one-new-name merge, since `rename_tool_category`
     rejects renaming into a name that already exists (by design, for the
     ordinary single-category-at-a-time admin UI case).

The two new tags with no current mapping target (Neobanking, Travel
Management) are seeded with zero tools — expected, not a bug; nothing in
the old vocabulary was ever about corporate travel or neobanking.

Usage:
    python -m scripts.archive.migrate_software_tags --db library.db
    python -m scripts.archive.migrate_software_tags --db library.db --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from linklib.db import Library, resolve_db_path

# Old category name -> new taxonomy name. Every category ever seeded via
# scripts/seed_tools.py or added by hand at /admin/tools/software/categories should
# appear here; anything encountered that isn't in this map is left as-is
# and flagged loudly (rather than silently dropped) so it can be triaged.
CATEGORY_MAP = {
    "FP&A": "FP&A",
    "Headcount Planning": "Headcount Planning",
    "Treasury": "Treasury/Cash Management",
    "Cash Flow Forecasting": "Treasury/Cash Management",
    "AI Agents": "FP&A",
    "ERP": "ERP",
    "Cap Table Management": "Equity Management",
    "Spend Management": "Procurement/Spend",
    "Financial Close": "Accounting",
    "Financial Reporting": "Accounting",
    "Revenue Recognition": "Revenue",
    "Billing": "Revenue",
    "Collections": "Revenue",
    "Sales Tax": "Tax Management",
    "Commission Calculations": "Revenue Operations",
    "Compensation Data": "Headcount Planning",
    "Contract Management": "Legal and Contracting",
    "Procurement": "Procurement/Spend",
    "RevOps": "Revenue Operations",
    "Cloud/IT Spend": "Cloud/IT Spend",
    "BI & Analytics": "BI/Analytics",
    "Tax Compliance": "Tax Management",
    "CLM": "Legal and Contracting",
    "Legal AI": "Legal and Contracting",
    "Process Optimization": "Accounting",
}

# Kept in sync by hand with _DEFAULT_TOOL_CATEGORIES / _DEFAULT_CATEGORY_DESCRIPTIONS
# in webapp/app.py — duplicated here rather than imported since scripts/ doesn't
# import the FastAPI app module, and this list is fixed for the life of this
# one-off migration regardless of what a later admin edit changes it to.
NEW_CATEGORIES = [
    ("Accounting", "Core bookkeeping, close management, and financial reporting—reconciliations, journal entries, flux analysis, and the statements finance produces each period."),
    ("BI/Analytics", "Business intelligence, data visualization, SQL analytics, and data infrastructure CFOs own or use for reporting."),
    ("Cloud/IT Spend", "Cloud cost management and SaaS management—visibility into and control over cloud infrastructure spend and software license costs."),
    ("Equity Management", "Cap table management, 409A valuations, and employee equity plan administration for private companies."),
    ("ERP", "Core accounting and enterprise resource planning—general ledger, system of record, and financial management."),
    ("FP&A", "Business-wide financial planning, budgeting, forecasting, and management reporting—including AI-native and agentic platforms built for FP&A workflows."),
    ("Headcount Planning", "Planning and tracking headcount and people costs—open reqs, budget vs. actuals, the finance-HR handoff, and the compensation benchmarking data used to set pay."),
    ("Legal and Contracting", "Contract lifecycle management—drafting, negotiation, approvals, eSign, obligation tracking, and renewals."),
    ("Neobanking", "Digital-first business banking—accounts, cards, and payments built for startups and scale-ups, without a traditional bank relationship."),
    ("Procurement/Spend", "Corporate cards, expense management, AP automation, purchasing workflows, and vendor procurement—employee and company spend controls end to end."),
    ("Revenue", "Billing, invoicing, revenue recognition, and accounts receivable/collections—the full revenue cycle from invoice to cash."),
    ("Revenue Operations", "Revenue operations—pipeline management, revenue forecasting, deal intelligence, and incentive compensation/commission management for finance and sales leaders."),
    ("Tax Management", "Sales tax, VAT, and GST compliance—nexus monitoring, real-time calculation, and filing."),
    ("Travel Management", "Corporate travel booking, policy enforcement, and expense integration for business travel programs."),
    ("Treasury/Cash Management", "Treasury management, cash flow forecasting, FX risk, global payments infrastructure, and corporate cash investment platforms."),
]

NEW_NAMES = {name for name, _ in NEW_CATEGORIES}


def main():
    parser = argparse.ArgumentParser(description="Migrate CFO Toolbox Software tags to the 15-tag taxonomy.")
    parser.add_argument("--db", default=None, help="Path to library.db (or set LINKLIB_DB)")
    parser.add_argument("--dry-run", action="store_true", help="Report what would change without writing.")
    args = parser.parse_args()
    args.db = resolve_db_path(args.db)

    lib = Library(args.db)
    try:
        rows = lib.conn.execute("SELECT id, name, categories_json FROM tools").fetchall()
        new_tag_counts = Counter()
        unmapped = Counter()
        updates = []  # (id, new_categories_json)
        changed = 0

        for r in rows:
            old_cats = json.loads(r["categories_json"]) or []
            new_cats = set()
            for c in old_cats:
                if c in NEW_NAMES:
                    new_cats.add(c)  # already-migrated / hand-typed new-taxonomy name
                elif c in CATEGORY_MAP:
                    new_cats.add(CATEGORY_MAP[c])
                else:
                    unmapped[c] += 1
                    new_cats.add(c)  # leave untouched — surfaced below for triage
            new_cats_sorted = sorted(new_cats)
            for c in new_cats_sorted:
                new_tag_counts[c] += 1
            if new_cats_sorted != sorted(old_cats):
                changed += 1
                updates.append((r["id"], r["name"], old_cats, new_cats_sorted))

        print(f"{len(rows)} tools scanned, {changed} would change tags.")
        if unmapped:
            print("\n⚠️  Encountered category names with no mapping (left untouched — triage before "
                  "relying on this run):")
            for name, n in unmapped.most_common():
                print(f"   {name!r}: {n} tool(s)")

        if args.dry_run:
            print("\n--dry-run: no changes written. Sample of what would change:")
            for tool_id, name, old, new in updates[:10]:
                print(f"   [{tool_id}] {name}: {old} -> {new}")
            if len(updates) > 10:
                print(f"   ...and {len(updates) - 10} more.")
            return

        for tool_id, name, old, new in updates:
            lib.conn.execute(
                "UPDATE tools SET categories_json=? WHERE id=?",
                (json.dumps(new), tool_id),
            )
        lib.conn.commit()

        # Rebuild the filter-pill vocabulary from scratch as the fixed 15,
        # alphabetized. Wholesale replace rather than incremental
        # rename/delete — see module docstring for why.
        lib.conn.execute("DELETE FROM tool_categories")
        for order, (name, description) in enumerate(NEW_CATEGORIES):
            lib.conn.execute(
                "INSERT INTO tool_categories (name, description, sort_order) VALUES (?,?,?)",
                (name, description, order),
            )
        lib.conn.commit()

        print(f"\nUpdated {changed} tool(s). Rebuilt tool_categories with {len(NEW_CATEGORIES)} tags.")
        print("\nNew tag distribution:")
        for name, _ in NEW_CATEGORIES:
            print(f"   {name}: {new_tag_counts.get(name, 0)}")
    finally:
        lib.close()


if __name__ == "__main__":
    main()
