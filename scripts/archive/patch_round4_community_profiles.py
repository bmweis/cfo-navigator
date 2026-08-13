#!/usr/bin/env python3
"""Round 4 research: a targeted, partial-field update for the 3 placeholder
fields across all 38 communities — NOT a full re-import. Direct writes only
(no voice-rewrite anywhere in this script — all three fields are short
categorical/factual values, not narrative prose):

  stage_focus: source is research-round-4 (compass_artifact...markdown),
    recoded to add "Enterprise" as a new vocabulary value. Evanta CFO
    Community, NeuGroup, and The Conference Board Corporate Treasurers
    Council are recoded from the doc's "No stage focus" to "Enterprise" per
    Brian's direct call, overriding the doc (the doc's own Caveats section
    flags these three, plus GaapSavvy, as judgment calls: enterprise-scale
    orgs targeted by revenue/market-cap, not a fundraising stage). Every
    other community keeps the doc's stage_focus verbatim (mapped onto the
    existing vocabulary: "Growth stage" -> "Growth", "Late stage/pre-IPO"
    unchanged, "No stage focus" unchanged).

  jobs_program: was boolean (Yes/No) in the round-4 doc; now categorical:
    "Job board" | "Recruiting/placement service" | "Career-transition
    program" | "None". Doc's Key Findings/Recommendations name most of these
    explicitly: Operators Guild (Guild Talent recruiting agency) and FIF
    Collective (dedicated job-placement programs) = Recruiting/placement
    service; SENG-NE (ASCENDANCY executive career-search workshops) =
    Career-transition program; AFP/ACT/FEI/FWA/HFMA/Controllers Council
    (passive career-center job boards) = Job board. Three the doc itself
    flagged as ambiguous/unspecified were resolved by Brian directly,
    overriding the doc's own coding/framing where noted:
      - Fractionals United -> None. Confirmed directly: it's a referral
        program for business development among fractional CFOs, not job
        placement — the doc's "gig-matching mechanism" framing (Caveats,
        line 214) overstated it.
      - CFO Connect -> Job board. Confirmed correct as originally proposed
        (grouped with The F Suite in the doc's TL;DR as a "venue/job-board"
        community, distinct from Operators Guild's named recruiting agency).
      - The F Suite -> Recruiting/placement service. Confirmed directly:
        there's an active job board AND a person who helps place CFOs in
        roles — that human-assisted placement element goes beyond a passive
        board, putting it in the same category as Operators Guild.

  team_or_individual: direct write from the doc, unchanged vocabulary
    ("Individual only" / "Both") — out of scope for this round's changes
    per Brian, included here only because every touched row needs it
    written alongside the other two.

One community name mismatch worth flagging: the doc's heading is "Modern
Finance Forum for CFOs (FSN)" but the community's row in this DB (see
scripts/_community_profile_data.py) is named "Modern Finance Forum for
CFOs" with no "(FSN)" suffix. This script uses the DB's canonical name.

needs_review=1 on every touched row (same as every prior research import in
this build — surfaces on the admin communities list for Brian's read).

Uses Library.update_community_profile_research_fields — a genuine partial
UPDATE (only touches the columns actually passed), not upsert_community_profile's
full-row replace, so nothing else on any row is disturbed.

Usage:
    python -m scripts.archive.patch_round4_community_profiles --db library.db
    python -m scripts.archive.patch_round4_community_profiles --db library.db --dry-run

No ANTHROPIC_API_KEY needed — this script makes no LLM calls.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from linklib.db import Library, resolve_db_path

# Round 4 research (compass_artifact_wf-253b8762-e423-50a7-85b2-cd012549cc88_text_markdown.md)
# stage_focus doc value -> new vocabulary (Early/seed, Growth, Late stage/pre-IPO,
# Public companies, No stage focus, Enterprise)
_STAGE_MAP = {
    "No stage focus (open to all stages)": "No stage focus",
    "Growth stage": "Growth",
    "Late stage/pre-IPO": "Late stage/pre-IPO",
}

# name -> (stage_focus doc value, jobs_program category, team_or_individual)
ROUND4_UPDATES = {
    "Association for Financial Professionals (AFP)": ("No stage focus (open to all stages)", "Job board", "Both"),
    "Association of Corporate Treasurers (ACT)": ("No stage focus (open to all stages)", "Job board", "Both"),
    "Boston Corporate Finance Community (BCFC)": ("No stage focus (open to all stages)", "None", "Individual only"),
    "CFO Alliance": ("No stage focus (open to all stages)", "None", "Both"),
    "CFO Chat": ("No stage focus (open to all stages)", "None", "Individual only"),
    "CFO Circle (Blueprint for Growth)": ("No stage focus (open to all stages)", "None", "Individual only"),
    "CFO Connect": ("No stage focus (open to all stages)", "Job board", "Individual only"),
    "CFO Executive Forum (Open Future Forum)": ("No stage focus (open to all stages)", "None", "Individual only"),
    "CFO Leadership Council": ("No stage focus (open to all stages)", "None", "Individual only"),
    "CFO Mastermind Group": ("No stage focus (open to all stages)", "None", "Individual only"),
    "CFOMeet": ("No stage focus (open to all stages)", "None", "Individual only"),
    "Close Club": ("Growth stage", "None", "Individual only"),
    "Controllers Council": ("No stage focus (open to all stages)", "Job board", "Both"),
    "Evanta CFO Community": ("Enterprise", "None", "Individual only"),  # Brian override, was "No stage focus" in doc
    "FIF Collective (Females in Finance)": ("No stage focus (open to all stages)", "Recruiting/placement service", "Individual only"),
    "FP&A Community (Datarails)": ("No stage focus (open to all stages)", "None", "Individual only"),
    "Finance & Accounting for Bioscience (Informa Connect)": ("No stage focus (open to all stages)", "None", "Individual only"),
    "Finance Alliance": ("No stage focus (open to all stages)", "None", "Both"),
    "Financial Executives International (FEI)": ("No stage focus (open to all stages)", "Job board", "Individual only"),
    "Financial Women's Association (FWA)": ("No stage focus (open to all stages)", "Job board", "Individual only"),
    "Fractionals United": ("No stage focus (open to all stages)", "None", "Individual only"),
    "Healthcare Financial Management Association (HFMA)": ("No stage focus (open to all stages)", "Job board", "Both"),
    "Modern Finance Forum for CFOs": ("No stage focus (open to all stages)", "None", "Individual only"),
    "NeuGroup": ("Enterprise", "None", "Both"),  # Brian override, was "No stage focus" in doc
    "Off The Ledger": ("No stage focus (open to all stages)", "None", "Individual only"),
    "Operators Guild": ("Growth stage", "Recruiting/placement service", "Individual only"),
    "Private Equity CFO Association (PECFOA)": ("No stage focus (open to all stages)", "None", "Individual only"),
    "Private Funds CFO Network": ("No stage focus (open to all stages)", "None", "Individual only"),
    "Proformative": ("No stage focus (open to all stages)", "None", "Individual only"),
    "Senior Executive Network (CFO/VP Finance)": ("No stage focus (open to all stages)", "None", "Both"),
    "Startup CFO": ("Growth stage", "None", "Both"),
    "The CFO Accelerator Inner Circle": ("No stage focus (open to all stages)", "None", "Individual only"),
    "The Circle (Founders Circle Capital)": ("Growth stage", "None", "Individual only"),
    "The Conference Board Corporate Treasurers Council": ("Enterprise", "None", "Individual only"),  # Brian override, was "No stage focus" in doc
    "The F Suite": ("No stage focus (open to all stages)", "Recruiting/placement service", "Both"),
    "GaapSavvy": ("Late stage/pre-IPO", "None", "Individual only"),
    "Otto-Mates": ("No stage focus (open to all stages)", "None", "Individual only"),
    "SENG-NE (Senior Executive Networking Group of New England)": ("No stage focus (open to all stages)", "Career-transition program", "Individual only"),
}

# Overrides already carry the final vocabulary value ("Enterprise"), not a
# doc phrase to run through _STAGE_MAP.
_ALREADY_MAPPED_STAGE = {"Enterprise"}

JUDGMENT_CALLS = """
== jobs_program calls Brian resolved directly (doc flagged these as ambiguous) ==
  Fractionals United -> None
    It's a referral program for business development among fractional
    CFOs, not job placement — the doc's "gig-matching mechanism" framing
    overstated it.
  CFO Connect -> Job board
    Confirmed correct as originally proposed.
  The F Suite -> Recruiting/placement service
    Active job board AND a person who helps place CFOs in roles —
    human-assisted placement goes beyond a passive board, same category
    as Operators Guild.

== Name mismatch flagged, confirmed no change needed ==
  Doc heading "Modern Finance Forum for CFOs (FSN)" vs. this DB's canonical
  community name "Modern Finance Forum for CFOs" (no FSN suffix, per
  scripts/_community_profile_data.py). Matched by the DB's name — Brian
  confirmed this DB name is exact, proceed as-is.
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None, help="Path to library.db (or set LINKLIB_DB)")
    ap.add_argument("--dry-run", action="store_true",
                     help="print what would happen without writing anything")
    args = ap.parse_args()
    args.db = resolve_db_path(args.db)

    lib = Library(args.db)
    updated = 0
    skipped: list[str] = []

    for name, (stage_doc_value, jobs_program, team_or_individual) in ROUND4_UPDATES.items():
        row = lib.conn.execute("SELECT id FROM communities WHERE name=?", (name,)).fetchone()
        if not row:
            print(f"SKIP (community row not found — check name matches `communities.name` exactly): {name}")
            skipped.append(name)
            continue
        community_id = row[0]
        if lib.get_community_profile(community_id) is None:
            print(f"SKIP (no existing community_profiles row — this script only updates "
                  f"an already-imported profile, it doesn't create one): {name}")
            skipped.append(name)
            continue

        stage_focus = (stage_doc_value if stage_doc_value in _ALREADY_MAPPED_STAGE
                        else _STAGE_MAP[stage_doc_value])

        if args.dry_run:
            print(f"Would update: {name} -> stage_focus={stage_focus!r}, "
                  f"jobs_program={jobs_program!r}, team_or_individual={team_or_individual!r}, "
                  f"needs_review=1")
            updated += 1
            continue

        lib.update_community_profile_research_fields(
            community_id,
            stage_focus=stage_focus,
            jobs_program=jobs_program,
            team_or_individual=team_or_individual,
            needs_review=1,
        )
        print(f"Updated: {name}")
        updated += 1

    print(f"\n{'Would update' if args.dry_run else 'Updated'} {updated}/{len(ROUND4_UPDATES)} community profile(s).")
    if skipped:
        print(f"Skipped {len(skipped)}: {', '.join(skipped)}")
    print(JUDGMENT_CALLS)

    lib.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
