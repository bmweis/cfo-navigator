#!/usr/bin/env python3
"""Round 3 research: a targeted, partial-field update for CFO Chat and
Proformative — NOT a full re-import. Only six columns change per community:

  Written directly (no rewrite — short factual fields):
    founded_year, notable_members, low_confidence (derived from `confidence`)

  Voice-rewritten (scoped to ONLY these three — every other narrative field
  on the existing row, ideal_member/value_prop/format_reality/
  engagement_level/application_friction/cost_value_verdict/verdict_summary,
  is left exactly as it already is):
    anti_fit, sponsor_relationship_note, public_criticism

Then needs_review=1 on both, same as any other research update Brian hasn't
personally read yet.

Uses Library.update_community_profile_research_fields — a genuine partial
UPDATE (only touches the 7 columns above), not upsert_community_profile's
full-row replace, so nothing else on either row is disturbed.

Usage:
    export ANTHROPIC_API_KEY=...
    python -m scripts.archive.patch_round3_community_profiles --db library.db
    python -m scripts.archive.patch_round3_community_profiles --db library.db --dry-run
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from linklib.db import Library, resolve_db_path
from linklib.agent import VOICE_CORE_DEFAULT
from linklib.enrich import voice_rewrite_community_fields

# Round 3 research (research-round-3-community-profiles.md, Part A) — only
# the fields that changed. Everything else on these two rows is untouched.
ROUND3_UPDATES = [
    {
        "name": "CFO Chat",
        "founded_year": 2020,
        "notable_members": "No named member CFOs publicly identifiable beyond founder Jacob Sheldon. Launch-era LinkedIn engagement came from startup-ecosystem figures, not sitting CFOs.",
        "confidence": "medium",
        "anti_fit": "Wrong fit for a sitting, full-time CFO at a funded company who wants active, moderated peer discussion. The founding company pivoted away in 2021 and the founder has left, so expect minimal moderation, programming, or curation. Also skews fractional/part-time; a VP of Finance or full-time CFO may find fewer true peers than the name suggests. Versus Proformative: CFO Chat is real-time chat with an orphaned-community risk; Proformative is an archival Q&A library with a corporate owner.",
        "sponsor_relationship_note": "CFO Chat was built as top-of-funnel for SiliconCFO's fractional-CFO marketplace. Membership criteria explicitly welcomed anyone \"seeking a CFO position,\" which skews the room toward fractional and job-seeking CFOs rather than sitting operators.",
        "public_criticism": "No direct member complaints found (no reviews on Hive Index or elsewhere). However, a material structural finding: SiliconCFO rebranded to Shiny in November 2021 and pivoted to a general fractional-executive marketplace, and founder Jacob Sheldon has since moved on to other ventures (MedianFi, Firstbase). The community's parent company no longer exists in its original form. Absence of criticism here likely reflects a community too quiet to generate any, not a spotless reputation.",
    },
    {
        "name": "Proformative",
        "founded_year": 2008,
        "notable_members": "Co-founder John Kogan, a four-time CFO (VUDU, Encirq; finance roles at Cisco and AlliedSignal, now at Armanino). Community answers historically came from named practitioners posting under real identities, e.g. consultant/ERP strategist Len Green. At its 2014 peak it claimed 70,000+ registered members; current marketing claims of ~2M \"members\" reflect the owner's media audience, not forum participants.",
        "confidence": "medium",
        "anti_fit": "Wrong fit for anyone wanting live peer interaction or timely answers. Much of the highly-ranked Q&A content dates to the 2010s, new-question flow is email-gated, and the operator is a B2B media company monetizing the audience via vendor lead generation. Versus CFO Chat: Proformative's value is the searchable archive of practitioner answers (RSU accounting, intercompany entries, etc.), not the living community.",
        "sponsor_relationship_note": "Historically monetized via content/courses and vendor content; specifics not independently verified here.",
        "public_criticism": "Two concrete findings. (1) A Future Firm review of accounting forums notes new questions must be submitted by email to the Proformative team with no transparency into which get featured, i.e. it no longer functions as an open forum. (2) ZoomInfo flags \"very low activity levels\" versus sector peers. Ownership churn supports the archival read: Kogan-founded startup to Argyle Executive Forum (2016) to Argyle Group to Industry Dive to now operated by Informa TechTarget, whose Proformative pitch to vendors is \"put your brand in front of industry decision makers.\" It is now a lead-gen media property wearing a community's clothes.",
    },
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None, help="Path to library.db (or set LINKLIB_DB)")
    ap.add_argument("--model", default=None,
                    help="voice-rewrite model override (default: claude-sonnet-4-6)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would happen without writing anything or calling the API")
    args = ap.parse_args()
    args.db = resolve_db_path(args.db)

    if not args.dry_run and not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: set ANTHROPIC_API_KEY first (or pass --dry-run).", file=sys.stderr)
        return 2

    lib = Library(args.db)
    voice_core = lib.get_setting("voice_core") or VOICE_CORE_DEFAULT
    total_cost = 0.0

    for entry in ROUND3_UPDATES:
        name = entry["name"]
        row = lib.conn.execute("SELECT id FROM communities WHERE name=?", (name,)).fetchone()
        if not row:
            print(f"SKIP (community row not found): {name}")
            continue
        community_id = row[0]
        if lib.get_community_profile(community_id) is None:
            print(f"SKIP (no existing community_profiles row — this script only updates "
                  f"an already-imported profile, it doesn't create one): {name}")
            continue

        low_confidence = 1 if entry["confidence"] in ("medium", "low") else 0
        narrative = {
            "anti_fit": entry["anti_fit"],
            "sponsor_relationship_note": entry["sponsor_relationship_note"],
            "public_criticism": entry["public_criticism"],
        }

        if args.dry_run:
            print(f"Would update: {name} (founded_year={entry['founded_year']}, "
                  f"low_confidence={low_confidence}, needs_review=1) "
                  f"and voice-rewrite: anti_fit, sponsor_relationship_note, public_criticism")
            continue

        result = voice_rewrite_community_fields(name, narrative, voice_core, model=args.model or "claude-sonnet-4-6")
        if result is None:
            print(f"WARNING: voice-rewrite unavailable/failed for {name} — saving with UN-rewritten source text.")
            rewritten = dict(narrative)
        else:
            rewritten = {**narrative, **result.fields}
            if result.cost_usd:
                lib.record_enrichment_cost(None, result.model, result.input_tokens,
                                            result.output_tokens, result.cost_usd)
                total_cost += result.cost_usd
                print(f"Rewrote {name}: {result.input_tokens}in/{result.output_tokens}out tokens, ${result.cost_usd:.4f}")

        lib.update_community_profile_research_fields(
            community_id,
            founded_year=entry["founded_year"],
            notable_members=entry["notable_members"],
            low_confidence=low_confidence,
            anti_fit=rewritten["anti_fit"],
            sponsor_relationship_note=rewritten["sponsor_relationship_note"],
            public_criticism=rewritten["public_criticism"],
            needs_review=1,
        )
        print(f"Updated: {name}")
        print(f"  [anti_fit] SOURCE:    {narrative['anti_fit']}")
        print(f"  [anti_fit] REWRITTEN: {rewritten['anti_fit']}")
        print(f"  [sponsor_relationship_note] SOURCE:    {narrative['sponsor_relationship_note']}")
        print(f"  [sponsor_relationship_note] REWRITTEN: {rewritten['sponsor_relationship_note']}")
        print(f"  [public_criticism] SOURCE:    {narrative['public_criticism']}")
        print(f"  [public_criticism] REWRITTEN: {rewritten['public_criticism']}")

    print(f"\nTotal voice-rewrite spend: ${total_cost:.4f}")
    lib.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
