#!/usr/bin/env python3
"""One-off classification pass: Recommender weighting redesign, PR 1 of the
post-#159 vocabulary overhaul (see corrections-and-overrides.md-style Phase 0
investigation in the PR description).

Two changes bundled here:

1. `seniority_band_tags` narrows from its original 3-value vocabulary
   (senior | controller | mixed) to a pure seniority read: cfo | senior_exec
   | open_to_all. The old "controller / accounting-focused" bucket moves
   to the new Function dimension below rather than staying part of Level.
2. `function_tags` is brand new (Overall finance org | FP&A | Accounting |
   Treasury) — there's no free-text `function` column to read back from
   (see linklib/db.py's ALTER TABLE comment), so this hand-classifies
   straight from each community's `ideal_member`/`value_prop` prose and its
   `categories` tags in scripts/seed_communities.py, same reading process as
   scripts/backfill_community_weight_tags.py used for the original 7.

Communities whose ideal_member spans genuinely more than one Function
bucket, or whose Level/Function fit is otherwise a judgment call rather than
a clean read (e.g. a cross-functional community that isn't finance-exclusive
at all), get needs_review=1 set here so they surface for Brian's review
rather than silently landing on a guess — same use of the flag as the
original bulk profile import.

Usage:
    python -m scripts.recategorize_level_function --db library.db
    python -m scripts.recategorize_level_function --db library.db --dry-run
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from linklib.db import Library

# name -> {"seniority_band": [...], "function": [...]}
# seniority_band vocabulary: cfo | senior_exec | open_to_all
# function vocabulary: overall | fpa | accounting | treasury
LEVEL_FUNCTION_TAGS: dict[str, dict[str, list[str]]] = {
    "The F Suite": {"seniority_band": ["cfo", "senior_exec"], "function": ["overall"]},
    "Operators Guild": {"seniority_band": ["senior_exec", "open_to_all"], "function": ["overall"]},
    "CFO Leadership Council": {"seniority_band": ["cfo", "senior_exec", "open_to_all"], "function": ["overall"]},
    "Evanta CFO Community": {"seniority_band": ["cfo"], "function": ["overall"]},
    "CFO Connect": {"seniority_band": ["cfo", "senior_exec"], "function": ["overall"]},
    # Spans CAO/controller/treasurer/VP-Finance under one roof — no single
    # Function bucket cleanly fits; flagged for review.
    "Financial Executives International (FEI)": {"seniority_band": ["cfo", "senior_exec"], "function": ["overall"]},
    # Explicitly both treasury and FP&A leaders (plus CFOs); flagged for review.
    "Association for Financial Professionals (AFP)": {"seniority_band": ["senior_exec", "open_to_all"], "function": ["treasury", "fpa"]},
    "CFO Alliance": {"seniority_band": ["cfo", "senior_exec"], "function": ["overall"]},
    "Controllers Council": {"seniority_band": ["cfo", "senior_exec", "open_to_all"], "function": ["accounting"]},
    "CFO Executive Forum (Open Future Forum)": {"seniority_band": ["cfo"], "function": ["overall"]},
    "CFO Chat": {"seniority_band": ["cfo", "open_to_all"], "function": ["overall"]},
    "Proformative": {"seniority_band": ["open_to_all"], "function": ["overall"]},
    "Association of Corporate Treasurers (ACT)": {"seniority_band": ["open_to_all"], "function": ["treasury"]},
    # Deal/banking/PE/advisory community, not really scoped to one CFO-org
    # function; flagged for review.
    "Boston Corporate Finance Community (BCFC)": {"seniority_band": ["senior_exec", "open_to_all"], "function": ["overall"]},
    "CFO Circle (Blueprint for Growth)": {"seniority_band": ["cfo"], "function": ["overall"]},
    "CFO Mastermind Group": {"seniority_band": ["cfo", "senior_exec"], "function": ["overall"]},
    "CFOMeet": {"seniority_band": ["cfo", "senior_exec"], "function": ["overall"]},
    "Close Club": {"seniority_band": ["senior_exec", "open_to_all"], "function": ["accounting"]},
    "FIF Collective (Females in Finance)": {"seniority_band": ["cfo", "senior_exec"], "function": ["overall"]},
    "Finance Alliance": {"seniority_band": ["senior_exec", "open_to_all"], "function": ["overall"]},
    "FP&A Community (Datarails)": {"seniority_band": ["open_to_all"], "function": ["fpa"]},
    "Financial Women's Association (FWA)": {"seniority_band": ["open_to_all"], "function": ["overall"]},
    "Fractionals United": {"seniority_band": ["senior_exec"], "function": ["overall"]},
    # Spans controller/revenue-cycle/CFO across a whole industry vertical, no
    # single Function bucket dominant; flagged for review.
    "Healthcare Financial Management Association (HFMA)": {"seniority_band": ["open_to_all"], "function": ["overall"]},
    "Modern Finance Forum for CFOs": {"seniority_band": ["cfo", "senior_exec"], "function": ["overall"]},
    # Treasurers plus a distinct FP&A-leader cohort; flagged for review.
    "NeuGroup": {"seniority_band": ["senior_exec"], "function": ["treasury", "fpa"]},
    # Finance/accounting/procurement mix, no dominant single function;
    # flagged for review.
    "Off The Ledger": {"seniority_band": ["open_to_all"], "function": ["overall"]},
    "Private Equity CFO Association (PECFOA)": {"seniority_band": ["cfo", "senior_exec"], "function": ["overall"]},
    "Private Funds CFO Network": {"seniority_band": ["cfo", "senior_exec"], "function": ["overall"]},
    "Senior Executive Network (CFO/VP Finance)": {"seniority_band": ["cfo", "senior_exec"], "function": ["overall"]},
    "Startup CFO": {"seniority_band": ["cfo", "senior_exec"], "function": ["overall"]},
    # CFO/CAO/controller/FP&A all represented within one industry vertical;
    # flagged for review.
    "Finance & Accounting for Bioscience (Informa Connect)": {"seniority_band": ["senior_exec", "open_to_all"], "function": ["overall"]},
    "The CFO Accelerator Inner Circle": {"seniority_band": ["open_to_all"], "function": ["overall"]},
    # Spans COO/CMO/CRO/CPO alongside CFO — not finance-exclusive; flagged
    # for review.
    "The Circle (Founders Circle Capital)": {"seniority_band": ["cfo", "senior_exec"], "function": ["overall"]},
    "The Conference Board Corporate Treasurers Council": {"seniority_band": ["senior_exec"], "function": ["treasury"]},
    "GaapSavvy": {"seniority_band": ["open_to_all"], "function": ["accounting"]},
    # Automation/AI-in-accounting community spanning finance and ops broadly,
    # not accounting-only; flagged for review.
    "Otto-Mates": {"seniority_band": ["open_to_all"], "function": ["accounting"]},
    # Cross-functional executive networking group, explicitly not
    # finance-exclusive; flagged for review.
    "SENG-NE (Senior Executive Networking Group of New England)": {"seniority_band": ["senior_exec", "open_to_all"], "function": ["overall"]},
}

# Subset of the above whose Level/Function fit is a genuine judgment call
# (see the inline comments above) — flagged needs_review=1 so they surface
# for Brian's review rather than silently landing on a guess.
NEEDS_REVIEW = {
    "Financial Executives International (FEI)",
    "Association for Financial Professionals (AFP)",
    "Boston Corporate Finance Community (BCFC)",
    "Healthcare Financial Management Association (HFMA)",
    "NeuGroup",
    "Off The Ledger",
    "Finance & Accounting for Bioscience (Informa Connect)",
    "The Circle (Founders Circle Capital)",
    "Otto-Mates",
    "SENG-NE (Senior Executive Networking Group of New England)",
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="library.db")
    parser.add_argument("--dry-run", action="store_true", help="Print what would change, write nothing.")
    args = parser.parse_args()

    lib = Library(args.db)
    try:
        rows = lib.conn.execute("SELECT id, name FROM communities").fetchall()
        by_name = {r["name"]: r["id"] for r in rows}

        matched, missing = 0, []
        for name, tags in LEVEL_FUNCTION_TAGS.items():
            community_id = by_name.get(name)
            if community_id is None:
                missing.append(name)
                continue
            profile = lib.get_community_profile(community_id)
            if profile is None:
                print(f"  skip (no profile row yet): {name}")
                continue
            matched += 1
            flag = " [needs_review]" if name in NEEDS_REVIEW else ""
            print(f"  {name}: {tags}{flag}")
            if not args.dry_run:
                lib.update_community_weight_tags(
                    community_id,
                    seniority_band_tags=tags["seniority_band"],
                    function_tags=tags["function"],
                )
                if name in NEEDS_REVIEW:
                    lib.update_community_profile_research_fields(community_id, needs_review=1)

        if missing:
            print(f"\nNo matching communities.name for {len(missing)} entries: {missing}")
        print(f"\n{'Would update' if args.dry_run else 'Updated'} {matched}/{len(LEVEL_FUNCTION_TAGS)} communities.")
    finally:
        lib.close()


if __name__ == "__main__":
    main()
