#!/usr/bin/env python3
"""One-off classification pass: the Communities Recommender's best-fit
weighting matches a visitor's quiz answers against a controlled-vocabulary
*_tags column per community_profiles dimension (seniority_band_tags,
cpe_eligible_tags, primary_purpose_tags, platform_type_tags,
meeting_format_tags, event_style_tags, resources_included_tags) rather than
the free-text research column of the same base name.

That free-text prose was investigated (Phase 0 of the Recommender weighting
build) and found too inconsistent for reliable keyword/substring matching —
e.g. SENG-NE's platform_type is "Regional membership organization (in-person
+ Zoom), not a Slack/forum," which would false-match a naive "Slack"
substring check despite explicitly saying it ISN'T Slack-based. So instead
of a keyword pass, this script hand-classifies each of the existing 38
communities into the fixed vocabulary (see webapp/app.py's
_WEIGHT_DIMENSIONS) by reading the actual researched text in
scripts/_community_profile_data.py.

A community can legitimately carry more than one tag per dimension (e.g. AFP
serves "senior" executives and "controller"-level staff and is explicitly
"mixed"). cpe_eligible_tags/resources_included_tags are effectively booleans
(["yes"] or []) since their quiz checkbox is a single "Yes" option — a
"Limited"/"Some"/"Unclear" research value maps to [] (not a confirmed Yes),
same as an "Unclear... leans No" cpe_eligible reads as [] rather than "yes".

New communities added after this backfill get their tags set the normal way:
the checkbox groups on the admin profile edit form
(/admin/tools/communities/{id}/profile).

Usage:
    python -m scripts.backfill_community_weight_tags --db library.db
    python -m scripts.backfill_community_weight_tags --db library.db --dry-run
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from linklib.db import Library

# name -> {dimension: [tags]}, dimension keys matching the *_tags column
# suffix (seniority_band, cpe_eligible, primary_purpose, platform_type,
# meeting_format, event_style, resources_included). Vocabulary:
#   seniority_band: senior | controller | mixed
#   cpe_eligible: yes (absent = not confirmed)
#   primary_purpose: networking | learning | career_transition | both
#     ("both" added whenever 2+ of the other three genuinely apply)
#   platform_type: chat | in_person | mix
#   meeting_format: in_person | online | hybrid
#   event_style: intimate | large_format | mix (absent = doesn't fit cleanly,
#     e.g. a forum/resource-only community with no live "events")
#   resources_included: yes (absent = "Limited"/"Some"/"No" in the research)
WEIGHT_TAGS: dict[str, dict[str, list[str]]] = {
    "The F Suite": {
        "seniority_band": ["senior"], "cpe_eligible": [],
        "primary_purpose": ["networking", "learning", "both"],
        "platform_type": ["mix"], "meeting_format": ["hybrid"],
        "event_style": ["mix"], "resources_included": ["yes"],
    },
    "Operators Guild": {
        "seniority_band": ["senior", "mixed"], "cpe_eligible": [],
        "primary_purpose": ["networking", "learning", "both"],
        "platform_type": ["chat"], "meeting_format": ["hybrid"],
        "event_style": ["mix"], "resources_included": ["yes"],
    },
    "CFO Leadership Council": {
        "seniority_band": ["senior", "controller", "mixed"], "cpe_eligible": ["yes"],
        "primary_purpose": ["networking", "learning", "both"],
        "platform_type": ["mix"], "meeting_format": ["hybrid"],
        "event_style": ["mix"], "resources_included": ["yes"],
    },
    "Evanta CFO Community": {
        "seniority_band": ["senior"], "cpe_eligible": ["yes"],
        "primary_purpose": ["networking", "learning", "both"],
        "platform_type": ["in_person"], "meeting_format": ["in_person"],
        "event_style": ["mix"], "resources_included": [],
    },
    "CFO Connect": {
        "seniority_band": ["senior"], "cpe_eligible": [],
        "primary_purpose": ["networking", "learning", "both"],
        "platform_type": ["mix"], "meeting_format": ["hybrid"],
        "event_style": ["mix"], "resources_included": ["yes"],
    },
    "Financial Executives International (FEI)": {
        "seniority_band": ["senior", "controller"], "cpe_eligible": ["yes"],
        "primary_purpose": ["networking", "learning", "both"],
        "platform_type": ["mix"], "meeting_format": ["hybrid"],
        "event_style": ["mix"], "resources_included": ["yes"],
    },
    "Association for Financial Professionals (AFP)": {
        "seniority_band": ["senior", "controller", "mixed"], "cpe_eligible": ["yes"],
        "primary_purpose": ["networking", "learning", "both"],
        "platform_type": ["mix"], "meeting_format": ["hybrid"],
        "event_style": ["mix"], "resources_included": ["yes"],
    },
    "CFO Alliance": {
        "seniority_band": ["senior"], "cpe_eligible": ["yes"],
        "primary_purpose": ["networking", "learning", "both"],
        "platform_type": ["mix"], "meeting_format": ["hybrid"],
        "event_style": ["intimate"], "resources_included": ["yes"],
    },
    "Controllers Council": {
        "seniority_band": ["controller", "senior"], "cpe_eligible": ["yes"],
        "primary_purpose": ["networking", "learning", "both"],
        "platform_type": ["chat"], "meeting_format": ["online"],
        "event_style": [], "resources_included": ["yes"],
    },
    "CFO Executive Forum (Open Future Forum)": {
        "seniority_band": ["senior"], "cpe_eligible": [],
        "primary_purpose": ["networking"],
        "platform_type": ["in_person"], "meeting_format": ["in_person"],
        "event_style": ["intimate"], "resources_included": [],
    },
    "CFO Chat": {
        "seniority_band": ["mixed", "senior"], "cpe_eligible": [],
        "primary_purpose": ["networking"],
        "platform_type": ["chat"], "meeting_format": ["online"],
        "event_style": [], "resources_included": [],
    },
    "Proformative": {
        "seniority_band": ["mixed", "controller"], "cpe_eligible": ["yes"],
        "primary_purpose": ["networking", "learning", "both"],
        "platform_type": ["chat"], "meeting_format": ["online"],
        "event_style": [], "resources_included": ["yes"],
    },
    "Association of Corporate Treasurers (ACT)": {
        "seniority_band": ["mixed"], "cpe_eligible": ["yes"],
        "primary_purpose": ["networking", "learning", "both"],
        "platform_type": ["mix"], "meeting_format": ["hybrid"],
        "event_style": ["mix"], "resources_included": ["yes"],
    },
    "Boston Corporate Finance Community (BCFC)": {
        "seniority_band": ["senior", "mixed"], "cpe_eligible": [],
        "primary_purpose": ["networking"],
        "platform_type": ["in_person"], "meeting_format": ["in_person"],
        "event_style": ["large_format"], "resources_included": [],
    },
    "CFO Circle (Blueprint for Growth)": {
        "seniority_band": ["senior"], "cpe_eligible": [],
        "primary_purpose": ["networking", "learning", "both"],
        "platform_type": ["mix"], "meeting_format": ["hybrid"],
        "event_style": ["intimate"], "resources_included": ["yes"],
    },
    "CFO Mastermind Group": {
        "seniority_band": ["senior"], "cpe_eligible": ["yes"],
        "primary_purpose": ["networking", "learning", "both"],
        "platform_type": ["mix"], "meeting_format": ["hybrid"],
        "event_style": ["intimate"], "resources_included": [],
    },
    "CFOMeet": {
        "seniority_band": ["senior"], "cpe_eligible": [],
        "primary_purpose": ["networking"],
        "platform_type": ["in_person"], "meeting_format": ["in_person"],
        "event_style": ["intimate"], "resources_included": [],
    },
    "Close Club": {
        "seniority_band": ["mixed", "controller"], "cpe_eligible": [],
        "primary_purpose": ["networking", "learning", "both"],
        "platform_type": ["mix"], "meeting_format": ["hybrid"],
        "event_style": ["intimate"], "resources_included": ["yes"],
    },
    "FIF Collective (Females in Finance)": {
        "seniority_band": ["senior"], "cpe_eligible": ["yes"],
        "primary_purpose": ["networking", "career_transition"],
        "platform_type": ["mix"], "meeting_format": ["hybrid"],
        "event_style": ["mix"], "resources_included": ["yes"],
    },
    "Finance Alliance": {
        "seniority_band": ["mixed"], "cpe_eligible": [],
        "primary_purpose": ["networking", "learning", "both"],
        "platform_type": ["mix"], "meeting_format": ["hybrid"],
        "event_style": ["mix"], "resources_included": ["yes"],
    },
    "FP&A Community (Datarails)": {
        "seniority_band": ["mixed"], "cpe_eligible": [],
        "primary_purpose": ["networking", "learning", "both"],
        "platform_type": ["chat"], "meeting_format": ["online"],
        "event_style": [], "resources_included": ["yes"],
    },
    "Financial Women's Association (FWA)": {
        "seniority_band": ["mixed"], "cpe_eligible": ["yes"],
        "primary_purpose": ["networking", "learning", "both"],
        "platform_type": ["mix"], "meeting_format": ["hybrid"],
        "event_style": ["mix"], "resources_included": ["yes"],
    },
    "Fractionals United": {
        "seniority_band": ["senior"], "cpe_eligible": [],
        "primary_purpose": ["networking", "learning", "both"],
        "platform_type": ["mix"], "meeting_format": ["hybrid"],
        "event_style": ["intimate"], "resources_included": ["yes"],
    },
    "Healthcare Financial Management Association (HFMA)": {
        "seniority_band": ["mixed"], "cpe_eligible": ["yes"],
        "primary_purpose": ["networking", "learning", "both"],
        "platform_type": ["mix"], "meeting_format": ["hybrid"],
        "event_style": ["mix"], "resources_included": ["yes"],
    },
    "Modern Finance Forum for CFOs": {
        "seniority_band": ["senior"], "cpe_eligible": [],
        "primary_purpose": ["networking", "learning"],
        "platform_type": ["chat"], "meeting_format": ["online"],
        "event_style": [], "resources_included": ["yes"],
    },
    "NeuGroup": {
        "seniority_band": ["senior"], "cpe_eligible": ["yes"],
        "primary_purpose": ["networking", "learning", "both"],
        "platform_type": ["mix"], "meeting_format": ["hybrid"],
        "event_style": ["intimate"], "resources_included": ["yes"],
    },
    "Off The Ledger": {
        "seniority_band": ["mixed"], "cpe_eligible": [],
        "primary_purpose": ["networking", "learning", "both"],
        "platform_type": ["chat"], "meeting_format": ["online"],
        "event_style": [], "resources_included": [],
    },
    "Private Equity CFO Association (PECFOA)": {
        "seniority_band": ["senior"], "cpe_eligible": ["yes"],
        "primary_purpose": ["networking", "learning", "both"],
        "platform_type": ["mix"], "meeting_format": ["hybrid"],
        "event_style": ["intimate"], "resources_included": [],
    },
    "Private Funds CFO Network": {
        "seniority_band": ["senior"], "cpe_eligible": ["yes"],
        "primary_purpose": ["networking", "learning", "both"],
        "platform_type": ["mix"], "meeting_format": ["hybrid"],
        "event_style": ["mix"], "resources_included": ["yes"],
    },
    "Senior Executive Network (CFO/VP Finance)": {
        "seniority_band": ["senior", "controller"], "cpe_eligible": ["yes"],
        "primary_purpose": ["networking", "learning", "both"],
        "platform_type": ["in_person"], "meeting_format": ["in_person"],
        "event_style": ["mix"], "resources_included": [],
    },
    "Startup CFO": {
        "seniority_band": ["senior"], "cpe_eligible": [],
        "primary_purpose": ["networking", "learning", "both"],
        "platform_type": ["mix"], "meeting_format": ["hybrid"],
        "event_style": ["mix"], "resources_included": ["yes"],
    },
    "Finance & Accounting for Bioscience (Informa Connect)": {
        "seniority_band": ["senior", "controller", "mixed"], "cpe_eligible": ["yes"],
        "primary_purpose": ["networking", "learning"],
        "platform_type": ["in_person"], "meeting_format": ["in_person"],
        "event_style": ["large_format"], "resources_included": ["yes"],
    },
    "The CFO Accelerator Inner Circle": {
        "seniority_band": ["mixed", "senior"], "cpe_eligible": [],
        "primary_purpose": ["networking", "learning", "both"],
        "platform_type": ["chat"], "meeting_format": ["online"],
        "event_style": [], "resources_included": ["yes"],
    },
    "The Circle (Founders Circle Capital)": {
        "seniority_band": ["senior"], "cpe_eligible": [],
        "primary_purpose": ["networking", "learning", "both"],
        "platform_type": ["mix"], "meeting_format": ["hybrid"],
        "event_style": ["mix"], "resources_included": ["yes"],
    },
    "The Conference Board Corporate Treasurers Council": {
        "seniority_band": ["senior"], "cpe_eligible": ["yes"],
        "primary_purpose": ["networking", "learning", "both"],
        "platform_type": ["mix"], "meeting_format": ["hybrid"],
        "event_style": ["intimate"], "resources_included": ["yes"],
    },
    "GaapSavvy": {
        "seniority_band": ["mixed", "controller"], "cpe_eligible": ["yes"],
        "primary_purpose": ["networking", "learning", "both"],
        "platform_type": ["mix"], "meeting_format": ["hybrid"],
        "event_style": ["intimate"], "resources_included": ["yes"],
    },
    "Otto-Mates": {
        "seniority_band": ["mixed"], "cpe_eligible": [],
        "primary_purpose": ["networking", "learning", "both"],
        "platform_type": ["chat"], "meeting_format": ["hybrid"],
        "event_style": [], "resources_included": ["yes"],
    },
    "SENG-NE (Senior Executive Networking Group of New England)": {
        "seniority_band": ["senior", "controller"], "cpe_eligible": [],
        "primary_purpose": ["networking", "career_transition"],
        "platform_type": ["mix"], "meeting_format": ["hybrid"],
        "event_style": ["intimate"], "resources_included": ["yes"],
    },
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
        for name, tags in WEIGHT_TAGS.items():
            community_id = by_name.get(name)
            if community_id is None:
                missing.append(name)
                continue
            profile = lib.get_community_profile(community_id)
            if profile is None:
                print(f"  skip (no profile row yet): {name}")
                continue
            matched += 1
            print(f"  {name}: {tags}")
            if not args.dry_run:
                lib.update_community_weight_tags(
                    community_id,
                    seniority_band_tags=tags["seniority_band"],
                    cpe_eligible_tags=tags["cpe_eligible"],
                    primary_purpose_tags=tags["primary_purpose"],
                    platform_type_tags=tags["platform_type"],
                    meeting_format_tags=tags["meeting_format"],
                    event_style_tags=tags["event_style"],
                    resources_included_tags=tags["resources_included"],
                )

        if missing:
            print(f"\nNo matching communities.name for {len(missing)} entries: {missing}")
        print(f"\n{'Would update' if args.dry_run else 'Updated'} {matched}/{len(WEIGHT_TAGS)} communities.")
    finally:
        lib.close()


if __name__ == "__main__":
    main()
