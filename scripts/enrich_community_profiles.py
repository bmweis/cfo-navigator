#!/usr/bin/env python3
"""Bulk/backfill Community Profile drafting — the Communities equivalent of
scripts/enrich_agent_taxonomy.py. For each Community, one Claude call
(linklib.enrich.generate_community_profile) drafts the sixteen qualitative
profile fields (ideal_member, anti_fit, value_prop, ... — see
enrich.COMMUNITY_PROFILE_FIELDS) from the community's name + URL, the same
call the admin "Auto-fill from URL"/"Regenerate" button on
/admin/tools/communities/{id}/edit makes one community at a time. This
script is for running it against many communities in one pass instead of
clicking that button repeatedly.

Never auto-confirmed, same review contract as the live admin button and as
Software's agent-taxonomy drafts: every profile this writes gets
needs_review=1 (webapp/app.py's admin communities list already badges and
filters on that flag), and only the sixteen drafted fields are touched —
every other community_profiles column (the retired primary_purpose/
cpe_eligible/platform_type/meeting_format/event_style/seniority_band/
resources_included fields) is read back from the existing row and passed
through unchanged, since upsert_community_profile always fully replaces
every column and has no partial-update mode — the same "echo every field
back or it gets silently blanked" hazard the Software bulk-edit route had
for `summary`.

This makes real API calls under YOUR OWN Anthropic API credits — set
ANTHROPIC_API_KEY first. Always dry-run a new community list before writing:

    python -m scripts.enrich_community_profiles --db /data/library.db --communities "Chief,Rho Community" --dry-run
    python -m scripts.enrich_community_profiles --db /data/library.db --communities "Chief,Rho Community"
    python -m scripts.enrich_community_profiles --db /data/library.db --limit 10

Re-running is safe: a community whose profile already has a non-empty
`ideal_member` is skipped by default. Pass --force to redraft everything
anyway (e.g. re-running the whole catalog after a prompt change) — the
existing profile row is still read first so the untouched columns above are
preserved either way.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from linklib import enrich as enrich_mod
from linklib.db import Library, resolve_db_path
from linklib.voice_settings import VoicePromptMissing, require_voice_setting

DEFAULT_MODEL = os.environ.get("LINKLIB_ENRICH_MODEL", enrich_mod.DEFAULT_MODEL)


def _select_communities(lib: Library, names: str, limit: int) -> list[dict]:
    all_communities = lib.list_communities(approved_only=True)
    if names:
        wanted = {n.strip().lower() for n in names.split(",") if n.strip()}
        selected = [c for c in all_communities if c["name"].lower() in wanted]
        found = {c["name"].lower() for c in selected}
        missing = wanted - found
        if missing:
            print(f"WARNING: no approved community found for: {', '.join(sorted(missing))}", file=sys.stderr)
        name_counts: dict[str, int] = {}
        for c in selected:
            name_counts[c["name"].lower()] = name_counts.get(c["name"].lower(), 0) + 1
        dupes = sorted(n for n, count in name_counts.items() if count > 1)
        if dupes:
            print(f"WARNING: multiple approved rows share these names — each will be drafted "
                  f"separately: {', '.join(dupes)}. Dedupe in the admin table first if that's "
                  f"not intended.", file=sys.stderr)
        return selected
    if limit:
        return all_communities[:limit]
    return all_communities


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None, help="Path to library.db (or set LINKLIB_DB)")
    ap.add_argument("--communities", default="", help="comma-separated community names to run (test batch)")
    ap.add_argument("--limit", type=int, default=0, help="run the first N approved communities instead")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="Claude model to use")
    ap.add_argument("--dry-run", action="store_true", help="print what would be drafted, write nothing")
    ap.add_argument("--force", action="store_true",
                    help="redraft even communities that already have a profile")
    args = ap.parse_args()
    args.db = resolve_db_path(args.db)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: set ANTHROPIC_API_KEY first (this makes real API calls).", file=sys.stderr)
        return 2
    if not args.communities and not args.limit:
        print("ERROR: pass --communities \"Name1,Name2\" or --limit N — running against the "
              "whole catalog unscoped isn't supported here on purpose.", file=sys.stderr)
        return 2

    lib = Library(args.db)
    try:
        try:
            voice_core = require_voice_setting(lib, "voice_core")
        except VoicePromptMissing as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2

        communities = _select_communities(lib, args.communities, args.limit)
        if not communities:
            print("No matching communities found.", file=sys.stderr)
            return 1

        total_cost = 0.0
        total_drafted = 0
        total_skipped = 0
        total_failed = 0

        for c in communities:
            existing_profile = lib.get_community_profile(c["id"]) or {}
            already_researched = bool((existing_profile.get("ideal_member") or "").strip())
            if already_researched and not args.force:
                print(f"[{c['name']}] already has a profile — skipping (use --force to redraft)")
                total_skipped += 1
                continue

            print(f"[{c['name']}] researching…", flush=True)
            existing_for_prompt = {
                field: existing_profile.get(field) for field in enrich_mod.COMMUNITY_PROFILE_FIELDS
                if str(existing_profile.get(field) or "").strip()
            }
            draft = enrich_mod.generate_community_profile(
                c["name"], c["url"], existing=existing_for_prompt or None, model=args.model,
                voice_core=voice_core)
            if draft is None:
                print("  FAILED (SDK/key unavailable or the call errored)")
                total_failed += 1
                continue

            low_conf_note = " (low confidence — thin/no page content)" if draft.low_confidence else ""
            if args.dry_run:
                for field in enrich_mod.COMMUNITY_PROFILE_FIELDS:
                    value = getattr(draft, field)
                    if value not in (None, ""):
                        print(f"    {field}: {value}")
            else:
                # PR 2a: only live columns are written; the retired ones are
                # frozen and upsert_community_profile leaves them untouched.
                lib.upsert_community_profile(
                    c["id"], source="script",
                    ideal_member=draft.ideal_member, anti_fit=draft.anti_fit,
                    value_prop=draft.value_prop, format_reality=draft.format_reality,
                    engagement_level=draft.engagement_level,
                    sponsor_relationship_note=draft.sponsor_relationship_note,
                    business_model=draft.business_model,
                    application_friction=draft.application_friction,
                    cost_value_verdict=draft.cost_value_verdict,
                    notable_members=draft.notable_members,
                    public_criticism=draft.public_criticism, verdict_summary=draft.verdict_summary,
                    resources_included=draft.resources_included, jobs_program=draft.jobs_program,
                    cpe_eligible=draft.cpe_eligible,
                    low_confidence=int(draft.low_confidence), needs_review=1,
                )
                lib.record_enrichment_cost(None, draft.model, draft.input_tokens,
                                           draft.output_tokens, draft.cost_usd)
                total_drafted += 1

            total_cost += draft.cost_usd
            print(f"  drafted{low_conf_note}, ${draft.cost_usd:.4f}")
            time.sleep(0.5)  # light rate-limit courtesy, this is a small interactive batch not a bulk job

        print(f"\n{len(communities)} community(ies) processed: {total_drafted} profile(s) drafted "
              f"(needs_review=1, pending your review), {total_skipped} already-researched skipped, "
              f"{total_failed} failed.")
        print(f"Total cost: ${total_cost:.4f}" + (" (dry run — nothing written)" if args.dry_run else ""))
        if communities:
            processed = max(len(communities) - total_failed - total_skipped, 0)
            if processed:
                per_community = total_cost / processed
                approved_count = len(lib.list_communities(approved_only=True))
                print(f"Avg cost/community: ${per_community:.4f} — full catalog ({approved_count} "
                      f"communities) would be roughly ${per_community * approved_count:.2f}")
    finally:
        lib.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
