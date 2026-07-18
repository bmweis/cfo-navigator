#!/usr/bin/env python3
"""One-time bulk import of researched Community Profiles (37 communities),
voice-rewritten to match the site's tone.

Applies corrections-and-overrides.md's 3 removals first (deletes those
community rows entirely — no-op, not an error, if a row is already absent),
then for each of the 37 remaining communities:

  1. Looks up the community by name (must already exist — this script never
     creates a community row, only its profile).
  2. Maps the source `confidence` rating to `low_confidence` (medium/low -> True).
  3. Runs the 11 narrative fields (linklib.enrich.VOICE_REWRITE_FIELDS) through
     one voice-rewrite Claude call per community, using the live `voice_core`
     setting as the style guide — a style pass only, facts must survive
     unchanged. The 8 short factual/categorical fields are saved as researched,
     untouched.
  4. Logs each call's real cost via Library.record_enrichment_cost (the same
     ledger/contract as the single-profile "Generate profile draft" button —
     article_id=None, since enrichment_cost has no community-specific column).
  5. Saves via upsert_community_profile with needs_review=1, so every
     imported row surfaces on the admin communities list for Brian's
     personal read-through before he treats it as final.

Usage:
    export ANTHROPIC_API_KEY=...
    python -m scripts.import_community_profiles --db library.db
    python -m scripts.import_community_profiles --db library.db --dry-run
    python -m scripts.import_community_profiles --db library.db --only "SENG-NE (Senior Executive Networking Group of New England)"

`--only NAME` restricts the run to a single community (exact `name` match) —
for a community whose research landed later than the original batch (e.g. a
Round 3 addition), so it can be voice-rewritten and imported without
re-running (and re-spending) voice-rewrite on every community already done.
Removals still run either way (cheap, idempotent, no API cost).
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from linklib.db import Library
from linklib.agent import VOICE_CORE_DEFAULT
from linklib.enrich import voice_rewrite_community_fields, VOICE_REWRITE_FIELDS
from scripts._community_profile_data import COMMUNITY_PROFILES, REMOVALS, DO_NOT_IMPORT


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.environ.get("LINKLIB_DB", "library.db"))
    ap.add_argument("--model", default=None,
                    help="voice-rewrite model override (default: LINKLIB_ENRICH_MODEL)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would happen without writing anything or "
                         "calling the API")
    ap.add_argument("--only", default=None,
                    help="restrict the import to a single community (exact name match), "
                         "so a later addition doesn't re-voice-rewrite everything already done")
    args = ap.parse_args()

    if not args.dry_run and not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: set ANTHROPIC_API_KEY first (or pass --dry-run).", file=sys.stderr)
        return 2

    lib = Library(args.db)
    model = args.model  # None -> voice_rewrite_community_fields' own DEFAULT_MODEL
    voice_core = lib.get_setting("voice_core") or VOICE_CORE_DEFAULT

    print(f"== Removals ({len(REMOVALS)}) ==")
    for name in REMOVALS:
        row = lib.conn.execute("SELECT id FROM communities WHERE name=?", (name,)).fetchone()
        if not row:
            print(f"  SKIP (not found): {name}")
            continue
        if args.dry_run:
            print(f"  Would delete: {name} (id={row[0]})")
        else:
            lib.delete_community(row[0])
            print(f"  Deleted: {name} (id={row[0]})")

    for name in DO_NOT_IMPORT:
        print(f"  Leaving alone (no research yet, do not import): {name}")

    profiles_to_import = COMMUNITY_PROFILES
    if args.only:
        profiles_to_import = [e for e in COMMUNITY_PROFILES if e["name"] == args.only]
        if not profiles_to_import:
            print(f"\nERROR: --only {args.only!r} matched no entry in COMMUNITY_PROFILES "
                  f"(check the name matches exactly).", file=sys.stderr)
            lib.close()
            return 2

    print(f"\n== Importing {len(profiles_to_import)} community profile(s) =="
          + (f" (--only {args.only!r})" if args.only else ""))
    total_cost = 0.0
    imported = 0
    spot_check: list[tuple[str, dict, dict]] = []

    for entry in profiles_to_import:
        name = entry["name"]
        row = lib.conn.execute("SELECT id FROM communities WHERE name=?", (name,)).fetchone()
        if not row:
            print(f"  SKIP (community row not found — check the name matches `communities.name` exactly): {name}")
            continue
        community_id = row[0]

        source_narrative = {k: entry.get(k, "") or "" for k in VOICE_REWRITE_FIELDS}
        low_confidence = 1 if entry["confidence"] in ("medium", "low") else 0

        if args.dry_run:
            print(f"  Would voice-rewrite + save: {name} (id={community_id}, low_confidence={low_confidence})")
            imported += 1
            continue

        result = voice_rewrite_community_fields(name, source_narrative, voice_core, model=model or "claude-sonnet-4-6")
        if result is None:
            print(f"  WARNING: voice-rewrite unavailable/failed for {name} — saving with UN-rewritten source text.")
            rewritten = dict(source_narrative)
        else:
            rewritten = {**source_narrative, **result.fields}
            if result.cost_usd:
                lib.record_enrichment_cost(None, result.model, result.input_tokens,
                                            result.output_tokens, result.cost_usd)
                total_cost += result.cost_usd
                print(f"  Rewrote {name}: {result.input_tokens}in/{result.output_tokens}out tokens, ${result.cost_usd:.4f}")

        spot_check.append((name, source_narrative, rewritten))

        lib.upsert_community_profile(
            community_id,
            ideal_member=rewritten["ideal_member"],
            anti_fit=rewritten["anti_fit"],
            value_prop=rewritten["value_prop"],
            format_reality=rewritten["format_reality"],
            engagement_level=rewritten["engagement_level"],
            sponsor_relationship_note=rewritten["sponsor_relationship_note"],
            business_model=rewritten["business_model"],
            application_friction=rewritten["application_friction"],
            cost_value_verdict=rewritten["cost_value_verdict"],
            notable_members=entry.get("notable_members") or "",
            founded_year=entry.get("founded_year"),
            public_criticism=rewritten["public_criticism"],
            verdict_summary=rewritten["verdict_summary"],
            low_confidence=low_confidence,
            primary_purpose=entry.get("primary_purpose", ""),
            cpe_eligible=entry.get("cpe_eligible", ""),
            platform_type=entry.get("platform_type", ""),
            meeting_format=entry.get("meeting_format", ""),
            event_style=entry.get("event_style", ""),
            seniority_band=entry.get("seniority_band", ""),
            resources_included=entry.get("resources_included", ""),
            needs_review=1,
        )
        imported += 1

    print(f"\nImported {imported}/{len(profiles_to_import)} profile(s). Total voice-rewrite spend: ${total_cost:.4f}")

    if spot_check and not args.dry_run:
        print("\n== Spot-check: source vs. rewritten (first 3) ==")
        for name, source, rewritten in spot_check[:3]:
            print(f"\n--- {name} ---")
            for field in ("verdict_summary", "cost_value_verdict"):
                print(f"  [{field}] SOURCE:    {source[field]}")
                print(f"  [{field}] REWRITTEN: {rewritten[field]}")

    lib.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
