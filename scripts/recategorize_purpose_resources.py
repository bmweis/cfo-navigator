#!/usr/bin/env python3
"""One-off classification pass: Recommender weighting redesign, PR 2 of the
post-#159 vocabulary overhaul.

Retires `primary_purpose_tags` and `resources_included_tags` as weighting
dimensions in favor of one merged multi-select, `looking_for_tags`
("What you're looking for"): Peer discussions | Networking | Learning &
education | Vendor connections | Resources & templates. This is genuine
re-derivation, not a relabel — the old `primary_purpose` vocabulary bundled
"peer networking" as a single option; this hand-classification splits it
into:
  - Peer discussions: an active peer Q&A/crowdsourcing mechanic (a Slack
    channel, forum, "OG Live", "Braintrust", "Shareforums") where members
    ask/discuss real problems with each other.
  - Networking: live gatherings for relationship-building (dinners,
    conferences, roundtables, summits) — distinct from a discussion
    mechanic even though many communities have both.
  - Learning & education: courses, webinars, CPE, certifications, thought
    leadership, coaching.
  - Vendor connections: an explicit vendor/tool-matchmaking service (a
    peer-vetted vendor database, an auditor/tech-stack matchmaking program,
    a curated solutions directory) — a wholly new concept the old
    vocabulary never captured; only tagged where the research prose
    explicitly supports it, not inferred from vendor sponsorship alone.
  - Resources & templates: the old `resources_included` "Yes" bucket
    (templates, benchmarking surveys, tools libraries, playbooks) folded in
    as one of five checkboxes rather than its own dimension.

Read straight from scripts/_community_profile_data.py's value_prop/
format_reality/engagement_level prose per community (same reading process
as scripts/backfill_community_weight_tags.py and
scripts/recategorize_level_function.py used for their dimensions).
Communities whose read required a genuine judgment call between two
plausible tag sets get needs_review=1 here so they surface for Brian's
review rather than silently landing on a guess.

Usage:
    python -m scripts.recategorize_purpose_resources --db library.db
    python -m scripts.recategorize_purpose_resources --db library.db --dry-run
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from linklib.db import Library

# name -> looking_for tags
# vocabulary: peer_discussions | networking | learning | vendor_connections
#             | resources_templates
LOOKING_FOR_TAGS: dict[str, list[str]] = {
    "The F Suite": ["peer_discussions", "networking", "vendor_connections", "resources_templates"],
    "Operators Guild": ["peer_discussions", "networking", "learning", "vendor_connections", "resources_templates"],
    "CFO Leadership Council": ["peer_discussions", "networking", "learning", "resources_templates"],
    "Evanta CFO Community": ["networking", "learning", "resources_templates"],
    "CFO Connect": ["peer_discussions", "networking", "learning", "resources_templates"],
    "Financial Executives International (FEI)": ["peer_discussions", "networking", "learning", "resources_templates"],
    "Association for Financial Professionals (AFP)": ["peer_discussions", "networking", "learning", "resources_templates"],
    "CFO Alliance": ["peer_discussions", "networking", "learning", "resources_templates"],
    # Sponsor-paid Resource Directory reads as members finding vendors
    # through the community, closer to Vendor connections than the old
    # "resources_included" framing alone; judgment call, flagged for review.
    "Controllers Council": ["peer_discussions", "learning", "vendor_connections", "resources_templates"],
    "CFO Executive Forum (Open Future Forum)": ["peer_discussions", "networking"],
    "CFO Chat": ["peer_discussions", "networking"],
    "Proformative": ["peer_discussions", "learning", "resources_templates"],
    "Association of Corporate Treasurers (ACT)": ["networking", "learning", "resources_templates"],
    "Boston Corporate Finance Community (BCFC)": ["networking"],
    "CFO Circle (Blueprint for Growth)": ["peer_discussions", "networking", "learning", "resources_templates"],
    "CFO Mastermind Group": ["peer_discussions", "networking", "learning"],
    "CFOMeet": ["peer_discussions", "networking"],
    "Close Club": ["peer_discussions", "networking", "learning", "resources_templates"],
    "FIF Collective (Females in Finance)": ["networking", "learning", "resources_templates"],
    "Finance Alliance": ["peer_discussions", "networking", "learning", "resources_templates"],
    # format_reality is asynchronous/virtual-first with events mentioned only
    # generically; Networking inclusion is a judgment call, flagged for review.
    "FP&A Community (Datarails)": ["peer_discussions", "networking", "learning", "resources_templates"],
    "Financial Women's Association (FWA)": ["networking", "learning", "resources_templates"],
    "Fractionals United": ["peer_discussions", "networking", "learning", "resources_templates"],
    "Healthcare Financial Management Association (HFMA)": ["peer_discussions", "networking", "learning", "resources_templates"],
    "Modern Finance Forum for CFOs": ["learning", "resources_templates"],
    # Explicitly "does not sell attendee lists or passive event sponsorships"
    # — the opposite of Vendor connections, deliberately omitted.
    "NeuGroup": ["peer_discussions", "networking", "learning", "resources_templates"],
    # "Limited: community advice and events (no formal library emphasized)"
    # — Resources & templates omitted per the old resources_included=[]
    # convention; judgment call on omitting it, flagged for review.
    "Off The Ledger": ["peer_discussions", "networking", "learning"],
    "Private Equity CFO Association (PECFOA)": ["networking", "learning"],
    "Private Funds CFO Network": ["peer_discussions", "networking", "learning", "resources_templates"],
    "Senior Executive Network (CFO/VP Finance)": ["peer_discussions", "networking", "learning"],
    "Startup CFO": ["peer_discussions", "networking", "learning", "resources_templates"],
    "Finance & Accounting for Bioscience (Informa Connect)": ["peer_discussions", "networking", "learning", "resources_templates"],
    # Coaching/practice-building community, not really an events-networking
    # format; Networking omission is a judgment call, flagged for review.
    "The CFO Accelerator Inner Circle": ["peer_discussions", "learning", "resources_templates"],
    "The Circle (Founders Circle Capital)": ["peer_discussions", "networking", "learning", "resources_templates"],
    "The Conference Board Corporate Treasurers Council": ["peer_discussions", "networking", "learning", "resources_templates"],
    "GaapSavvy": ["peer_discussions", "networking", "learning", "vendor_connections", "resources_templates"],
    "Otto-Mates": ["peer_discussions", "networking", "learning", "vendor_connections", "resources_templates"],
    "SENG-NE (Senior Executive Networking Group of New England)": ["networking", "learning", "resources_templates"],
}

NEEDS_REVIEW = {
    "Controllers Council",
    "FP&A Community (Datarails)",
    "Off The Ledger",
    "The CFO Accelerator Inner Circle",
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
        for name, tags in LOOKING_FOR_TAGS.items():
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
                lib.update_community_weight_tags(community_id, looking_for_tags=tags)
                if name in NEEDS_REVIEW:
                    lib.update_community_profile_research_fields(community_id, needs_review=1)

        if missing:
            print(f"\nNo matching communities.name for {len(missing)} entries: {missing}")
        print(f"\n{'Would update' if args.dry_run else 'Updated'} {matched}/{len(LOOKING_FOR_TAGS)} communities.")
    finally:
        lib.close()


if __name__ == "__main__":
    main()
