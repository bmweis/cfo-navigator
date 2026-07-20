#!/usr/bin/env python3
"""One-off classification pass: Recommender weighting redesign, PR 3 of the
post-#159 vocabulary overhaul.

Platform (`platform_type_tags`) narrows from its original 3-value vocabulary
(chat | in_person | mix) to naming the actual platform a community runs on:
slack | circle | email | linkedin | proprietary. A community with no
persistent digital hub (pure in-person, or a coordination tool like Zoom/
Luma that isn't itself a community platform) gets an empty tag list rather
than being force-fit into one of the five — the schema and scoring already
support a dimension with no tags for a given community (several already
carry `[]` for other dimensions; see the Phase 0 investigation). `linkedin`
was added after the initial pass specifically for Modern Finance Forum for
CFOs, whose actual platform is a LinkedIn group — the Phase 0 report had
flagged this as a vocabulary gap (a community on a real, named platform that
just wasn't in the original 4-option list) rather than force-fitting it into
Proprietary or leaving it untagged.

Read straight from scripts/_community_profile_data.py's platform_type prose
per community (same reading process as the other recategorize_*.py
scripts). Two honesty notes surfaced during this pass, both left as-is
rather than force-fit:
  - `email` has almost no support in the source text — only The F Suite
    explicitly names an email listserv as one of its platforms. Every other
    "newsletter" mention in the research is content distribution, not the
    community's actual discussion platform, so it's not tagged.
  - A few communities run on a named vendor platform that isn't Slack,
    Circle, or a from-scratch build (e.g. MemberClicks, a member-portal
    vendor) — these read closest to "Proprietary" (a dedicated platform
    that isn't Slack/Circle) even though it's technically a third-party
    product from the community's own perspective; flagged needs_review
    since it's a judgment call on this vocabulary's boundary.

Usage:
    python -m scripts.recategorize_platform --db library.db
    python -m scripts.recategorize_platform --db library.db --dry-run
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from linklib.db import Library

# name -> platform_type tags
# vocabulary: slack | circle | email | linkedin | proprietary
PLATFORM_TAGS: dict[str, list[str]] = {
    "The F Suite": ["proprietary", "slack", "email"],
    "Operators Guild": ["circle"],
    "CFO Leadership Council": ["proprietary"],
    "Evanta CFO Community": [],
    "CFO Connect": ["slack"],
    # "FEIconnect" is a named custom platform but the description is thin;
    # judgment call, flagged for review.
    "Financial Executives International (FEI)": ["proprietary"],
    # "AFP Collaborate" named custom platform; judgment call, flagged for review.
    "Association for Financial Professionals (AFP)": ["proprietary"],
    # "an online content hub and member groups" — vague, no named platform;
    # judgment call, flagged for review.
    "CFO Alliance": ["proprietary"],
    "Controllers Council": ["proprietary"],
    # LinkedIn/Luma used for event coordination only, not a persistent hub.
    "CFO Executive Forum (Open Future Forum)": [],
    "CFO Chat": ["slack"],
    "Proformative": ["proprietary"],
    # "online learning academy" — vague, no named platform; judgment call,
    # flagged for review.
    "Association of Corporate Treasurers (ACT)": ["proprietary"],
    "Boston Corporate Finance Community (BCFC)": [],
    "CFO Circle (Blueprint for Growth)": ["proprietary"],
    # Zoom is a meeting tool, not a persistent community platform.
    "CFO Mastermind Group": [],
    "CFOMeet": [],
    "Close Club": ["slack"],
    # "online platform/newsletter" — vague, no named platform; judgment
    # call, flagged for review.
    "FIF Collective (Females in Finance)": ["proprietary"],
    "Finance Alliance": ["slack"],
    "FP&A Community (Datarails)": ["slack"],
    "Financial Women's Association (FWA)": ["proprietary"],
    "Fractionals United": ["slack"],
    # "online communities" — vague, no named platform; judgment call,
    # flagged for review.
    "Healthcare Financial Management Association (HFMA)": ["proprietary"],
    "Modern Finance Forum for CFOs": ["linkedin"],
    # "confidential online community" — vague, no named platform; judgment
    # call, flagged for review.
    "NeuGroup": ["proprietary"],
    "Off The Ledger": ["slack"],
    "Private Equity CFO Association (PECFOA)": [],
    # "member directory" implies a dedicated platform but isn't named;
    # judgment call, flagged for review.
    "Private Funds CFO Network": ["proprietary"],
    # MemberClicks is a named third-party member-portal vendor, not Slack/
    # Circle; reads closest to Proprietary from the community's own
    # perspective. Judgment call, flagged for review.
    "Senior Executive Network (CFO/VP Finance)": ["proprietary"],
    "Startup CFO": ["slack"],
    # Conference app is event-specific, not a persistent community platform.
    "Finance & Accounting for Bioscience (Informa Connect)": [],
    "The CFO Accelerator Inner Circle": ["slack"],
    "The Circle (Founders Circle Capital)": ["proprietary"],
    # "myTCB platform" is a named custom platform; judgment call, flagged
    # for review since the description is thin.
    "The Conference Board Corporate Treasurers Council": ["proprietary"],
    "GaapSavvy": ["slack"],
    "Otto-Mates": ["slack"],
    # Explicitly "not a Slack/forum"; Zoom is a meeting tool, not a
    # persistent community platform.
    "SENG-NE (Senior Executive Networking Group of New England)": [],
}

NEEDS_REVIEW = {
    "Financial Executives International (FEI)",
    "Association for Financial Professionals (AFP)",
    "CFO Alliance",
    "Association of Corporate Treasurers (ACT)",
    "FIF Collective (Females in Finance)",
    "Healthcare Financial Management Association (HFMA)",
    "NeuGroup",
    "Private Funds CFO Network",
    "Senior Executive Network (CFO/VP Finance)",
    "The Conference Board Corporate Treasurers Council",
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
        for name, tags in PLATFORM_TAGS.items():
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
                lib.update_community_weight_tags(community_id, platform_type_tags=tags)
                if name in NEEDS_REVIEW:
                    lib.update_community_profile_research_fields(community_id, needs_review=1)

        if missing:
            print(f"\nNo matching communities.name for {len(missing)} entries: {missing}")
        print(f"\n{'Would update' if args.dry_run else 'Updated'} {matched}/{len(PLATFORM_TAGS)} communities.")
    finally:
        lib.close()


if __name__ == "__main__":
    main()
