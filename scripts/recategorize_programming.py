#!/usr/bin/env python3
"""One-off classification pass: Recommender weighting redesign, PR 4 of the
post-#159 vocabulary overhaul.

Retires `meeting_format_tags` and `event_style_tags` as weighting dimensions
in favor of one merged multi-select, `programming_tags`, deliberately
reusing the "Programming" admin label that used to belong to
`meeting_format` alone — a repurposing to a new vocabulary, not a naming
collision to preserve. New vocabulary: Meals (dinners, etc.) | Conferences |
Retreats | Virtual Panels.

Per the Phase 0 investigation, "Demo Days" (part of the originally proposed
vocabulary) has no supporting text anywhere in the current research corpus
and is deliberately left out of this vocabulary rather than force-fit — a
candidate for a future research round, same treatment as the stage_focus
placeholder.

Read straight from scripts/_community_profile_data.py's format_reality
prose per community (the narrative field describing actual programming),
not from meeting_format/event_style, since that's where this level of
detail actually lives. Several communities' programming doesn't map
cleanly onto any of the four buckets (e.g. a roundtable/Shareforum/
workshop-only format) — those get an empty tag list rather than a forced
fit, same honesty standard as the other recategorize_*.py passes, and are
flagged needs_review where the call was close.

Usage:
    python -m scripts.recategorize_programming --db library.db
    python -m scripts.recategorize_programming --db library.db --dry-run
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from linklib.db import Library

# name -> programming tags
# vocabulary: meals | conferences | retreats | virtual_panels
PROGRAMMING_TAGS: dict[str, list[str]] = {
    "The F Suite": ["meals", "conferences", "virtual_panels"],
    "Operators Guild": ["meals", "conferences"],
    "CFO Leadership Council": ["conferences", "virtual_panels"],
    "Evanta CFO Community": ["meals", "conferences"],
    "CFO Connect": ["meals", "virtual_panels"],
    "Financial Executives International (FEI)": ["conferences", "virtual_panels"],
    "Association for Financial Professionals (AFP)": ["conferences", "virtual_panels"],
    # In-person/virtual "roundtables" don't map cleanly onto Meals/
    # Conferences/Retreats; judgment call to tag only the virtual half,
    # flagged for review.
    "CFO Alliance": ["virtual_panels"],
    "Controllers Council": ["virtual_panels"],
    "CFO Executive Forum (Open Future Forum)": ["meals"],
    "CFO Chat": [],
    "Proformative": ["virtual_panels"],
    "Association of Corporate Treasurers (ACT)": ["conferences", "virtual_panels"],
    # A single annual cocktail reception reads closest to Meals, though it's
    # not literally a meal; judgment call, flagged for review.
    "Boston Corporate Finance Community (BCFC)": ["meals"],
    # Monthly facilitated workshop/assessment format doesn't map cleanly
    # onto any of the four buckets; judgment call, flagged for review.
    "CFO Circle (Blueprint for Growth)": [],
    "CFO Mastermind Group": ["meals"],
    "CFOMeet": ["meals"],
    "Close Club": ["meals", "virtual_panels"],
    "FIF Collective (Females in Finance)": ["conferences", "virtual_panels"],
    "Finance Alliance": ["conferences", "virtual_panels"],
    # format_reality names no specific event types beyond generic "events";
    # judgment call to leave untagged, flagged for review.
    "FP&A Community (Datarails)": [],
    "Financial Women's Association (FWA)": ["meals", "virtual_panels"],
    "Fractionals United": ["virtual_panels"],
    "Healthcare Financial Management Association (HFMA)": ["conferences", "virtual_panels"],
    "Modern Finance Forum for CFOs": [],
    # "Full day to day-and-a-half" twice-yearly peer-group meetings read
    # closest to Retreats (multi-day, intensive) though never named that;
    # judgment call, flagged for review.
    "NeuGroup": ["retreats", "virtual_panels"],
    "Off The Ledger": ["conferences"],
    "Private Equity CFO Association (PECFOA)": ["conferences", "virtual_panels"],
    "Private Funds CFO Network": ["conferences", "virtual_panels"],
    # "1.5-day gatherings" twice yearly read closest to Retreats though never
    # named that; judgment call, flagged for review.
    "Senior Executive Network (CFO/VP Finance)": ["retreats", "conferences"],
    "Startup CFO": ["retreats", "virtual_panels"],
    "Finance & Accounting for Bioscience (Informa Connect)": ["conferences"],
    "The CFO Accelerator Inner Circle": ["virtual_panels"],
    "The Circle (Founders Circle Capital)": ["virtual_panels"],
    "The Conference Board Corporate Treasurers Council": ["virtual_panels"],
    # Quarterly "Shareforums" are a distinct hybrid format that doesn't map
    # cleanly onto any of the four buckets; judgment call, flagged for review.
    "GaapSavvy": [],
    "Otto-Mates": ["virtual_panels"],
    "SENG-NE (Senior Executive Networking Group of New England)": ["meals", "virtual_panels"],
}

NEEDS_REVIEW = {
    "CFO Alliance",
    "Boston Corporate Finance Community (BCFC)",
    "CFO Circle (Blueprint for Growth)",
    "FP&A Community (Datarails)",
    "NeuGroup",
    "Senior Executive Network (CFO/VP Finance)",
    "GaapSavvy",
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
        for name, tags in PROGRAMMING_TAGS.items():
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
                lib.update_community_weight_tags(community_id, programming_tags=tags)
                if name in NEEDS_REVIEW:
                    lib.update_community_profile_research_fields(community_id, needs_review=1)

        if missing:
            print(f"\nNo matching communities.name for {len(missing)} entries: {missing}")
        print(f"\n{'Would update' if args.dry_run else 'Updated'} {matched}/{len(PROGRAMMING_TAGS)} communities.")
    finally:
        lib.close()


if __name__ == "__main__":
    main()
