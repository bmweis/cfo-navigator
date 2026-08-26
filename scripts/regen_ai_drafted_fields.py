#!/usr/bin/env python3
"""One-time, throwaway script (run via `railway ssh`, never a permanent admin
feature) that regenerates every AI-draftable field on every approved
Software tool and Community, writing directly and unconditionally — an
explicit, deliberate deviation from the publish-gate/review-queue work
shipped earlier today (2026-08). Existing content is unconditionally
overwritten; every needs_verification/needs_review flag this would
normally set on a fresh draft is deliberately bypassed (written as 0/false)
so a verified/public render reflects the fresh content immediately, with
no separate review step — per Brian's explicit direction, this is his
mechanism for the "go through them one at a time" review pass instead.

Fields regenerated, one field at a time, in order, per item (Description
saved before Agent taxonomy is even generated, and so on) — confirmed by
direct code reading, not guessed, to be the complete set of "Generate"
buttons for these two entity types (see the PR description for the full
investigation, including the other Generate-labeled buttons in the admin
UI that are deliberately NOT touched here: Community "Auto-fill from URL",
"Suggest similar communities"/"Suggest competitors", and the screenshot
capture buttons):

  Tools (`/tools/software/{slug}/edit`):
    1. Description + Summary        -> linklib.enrich.generate_tool_description
    2. Agent taxonomy               -> linklib.enrich.generate_tool_agent_taxonomy
    3. Competitive differentiation  -> linklib.enrich.generate_tool_differentiation

  Communities (`/admin/tools/communities/{id}/profile`):
    1. Community profile draft, all 23 fields in one call
                                     -> linklib.enrich.generate_community_profile

Bypass mechanics — no linklib signature changes were needed; every one of
these Library methods already accepts an explicit needs_verification/
needs_review value:
  - Description: `Library.update_tool(...)` is a full-row save (not
    COALESCE'd for name/url/categories/etc.), so this script fetches the
    tool's current row first and passes every other field straight back
    unchanged, overriding only description/summary and passing
    description_needs_verification=0 explicitly.
  - Agent taxonomy: `Library.set_tool_agent_taxonomy_draft(..., needs_verification=0,
    ai_confident=...)` — already a plain kwarg.
  - Differentiation: `Library.update_tool_differentiation(..., needs_verification=0,
    ai_confident=...)` — already a plain kwarg. This field has no
    entity_citations mechanism at all (ToolDifferentiationDraft carries no
    `citations` attribute), so nothing is written/cleared there.
  - Community profile: `Library.upsert_community_profile(..., needs_review=0,
    confidence=...)` is a full replace, but generate_community_profile
    drafts all 23 fields in the same call, so there's nothing stale to
    carry forward except the confidence dict for the 12 confidence-tracked
    fields, which the fresh draft already supplies for all 12.

entity_citations is written/cleared exactly as the live admin routes do
(`Library.set_entity_citations` / `clear_entity_citations`) for
Description, Agent taxonomy, and Community profile — completely
unaffected by this script; only the verification-flag write is bypassed.

Safe by default: PREVIEW ONLY (prints the resolved approved-tool/
approved-community counts, the resulting total generation-call count, and
a wall-clock estimate; makes zero Claude calls and zero writes) unless
--apply is passed. Every --apply write is verified by an immediate
read-back before being logged as a success, per the standing one-off-fix
practice (CLAUDE.md's "one-off admin fixes against the database").

Resumable/safe-to-stop: every attempted (entity_type, entity_id, field)
regeneration this pass is appended to the JSONL log named by --log-file
(default regen_ai_drafted_fields_log.jsonl in the current directory) as
soon as it's decided, success or failure. On startup the script reads
that file (if it already exists) and skips any (entity_type, entity_id,
field) already logged as "success" in it — so re-running with the SAME
--log-file after an interruption picks up where it left off, never
duplicating a regeneration or restarting from zero. Pass a fresh
--log-file to start a clean pass instead.

Batching/pacing (Brian-approved defaults, see PR description): 25 items
per batch, 3s between individual generation calls, 15s between batches,
one 30s-backoff retry on what looks like a rate-limit error before giving
up on that one call. Tools run to completion before communities start.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone

from linklib.db import Library, resolve_db_path
from linklib.enrich import (
    generate_tool_description,
    generate_tool_agent_taxonomy,
    generate_tool_differentiation,
    generate_community_profile,
)

# -- Brian-approved defaults ----------------------------------------------
BATCH_SIZE = 25
INTER_CALL_SLEEP = 3.0
INTER_BATCH_SLEEP = 15.0
RATE_LIMIT_BACKOFF = 30.0

# -- rough per-call wall-clock estimates, for the preview-mode time estimate
# only (grounding fetch + a real Claude call is much slower than a plain
# enrichment call — these are deliberately conservative, not measured). --
EST_SECONDS_PER_TOOL_FIELD = 20.0
EST_SECONDS_PER_COMMUNITY_PROFILE = 40.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_rate_limit_error(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return "429" in text or "rate_limit" in text or "rate limit" in text or "overloaded" in text


def _load_done(log_file: str) -> set[tuple[str, int, str]]:
    """(entity_type, entity_id, field) already logged as a success in a
    prior run against this same --log-file, so re-running skips them."""
    done: set[tuple[str, int, str]] = set()
    try:
        with open(log_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("status") == "success":
                    done.add((row["entity_type"], row["entity_id"], row["field"]))
    except FileNotFoundError:
        pass
    return done


def _log(log_file: str, entity_type: str, entity_id: int, name: str, field: str,
          status: str, detail: str = "") -> None:
    row = {
        "timestamp": _now_iso(),
        "entity_type": entity_type,
        "entity_id": entity_id,
        "name": name,
        "field": field,
        "status": status,
        "detail": detail,
    }
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def _call_with_retry(fn, *args, **kwargs):
    """Calls fn(*args, **kwargs); on what looks like a rate-limit error,
    sleeps RATE_LIMIT_BACKOFF once and retries exactly once more, then
    gives up (raises). Every linklib.enrich generate_* function already
    swallows its own internal failures and returns None rather than
    raising, so a raised exception here means something outside that
    (e.g. a raw SDK rate-limit error surfacing before the function's own
    try/except — defensive, in case of a transient network issue too)."""
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        if _is_rate_limit_error(e):
            print(f"      rate-limited ({type(e).__name__}) — waiting "
                  f"{RATE_LIMIT_BACKOFF:.0f}s and retrying once...")
            time.sleep(RATE_LIMIT_BACKOFF)
            return fn(*args, **kwargs)
        raise


# -- Tools -----------------------------------------------------------------

def _regen_tool_description(lib: Library, tool: dict, model: str, voice_core: str,
                             log_file: str, apply: bool) -> None:
    tool_id, name, url = tool["id"], tool["name"], tool["url"]
    print(f"  [tool {tool_id}] {name}: Description...")
    if not apply:
        return
    try:
        draft = _call_with_retry(generate_tool_description, name, url, model=model, voice_core=voice_core)
    except Exception as e:
        print(f"      FAILED: {type(e).__name__}: {e}")
        _log(log_file, "tool", tool_id, name, "description", "failure", f"{type(e).__name__}: {e}")
        return
    if draft is None:
        print("      FAILED: generate_tool_description returned None")
        _log(log_file, "tool", tool_id, name, "description", "failure",
             "generate_tool_description returned None (missing key or generation failed)")
        return

    current = lib.get_tool(tool_id)
    if current is None:
        print("      FAILED: tool vanished before save")
        _log(log_file, "tool", tool_id, name, "description", "failure", "tool vanished before save")
        return

    lib.update_tool(
        tool_id,
        name=current["name"], description=draft.description, url=current["url"],
        categories=current["categories"], advisor=current["advisor"], promoted=current["promoted"],
        vendor_email=current.get("vendor_email") or "", warm_intro_enabled=current.get("warm_intro_enabled") or 0,
        vendor_name=current.get("vendor_name") or "", summary=draft.summary,
        description_needs_verification=0, description_ai_confident=int(bool(draft.confident)),
    )
    if draft.citations:
        lib.set_entity_citations("tool", tool_id, "description", draft.citations, model=draft.model)
    else:
        lib.clear_entity_citations("tool", tool_id, "description")

    verify = lib.get_tool(tool_id)
    ok = bool(
        verify
        and verify["description"] == draft.description.strip()
        and verify["summary"] == draft.summary.strip()
        and verify["description_needs_verification"] == 0
    )
    if not ok:
        print("      VERIFY FAILED — post-write read-back did not match")
        _log(log_file, "tool", tool_id, name, "description", "failure",
             "post-write verification failed")
        return
    print("      OK — description/summary regenerated, needs_verification=0, verified")
    _log(log_file, "tool", tool_id, name, "description", "success")


def _regen_tool_agent_taxonomy(lib: Library, tool: dict, model: str, voice_core: str,
                                log_file: str, apply: bool) -> None:
    tool_id, name, url = tool["id"], tool["name"], tool["url"]
    print(f"  [tool {tool_id}] {name}: Agent taxonomy...")
    if not apply:
        return
    current = lib.get_tool(tool_id)
    description = (current or {}).get("description") or ""
    try:
        result = _call_with_retry(generate_tool_agent_taxonomy, name, url, description=description,
                                   model=model, voice_core=voice_core)
    except Exception as e:
        print(f"      FAILED: {type(e).__name__}: {e}")
        _log(log_file, "tool", tool_id, name, "agent_taxonomy", "failure", f"{type(e).__name__}: {e}")
        return
    if result is None:
        print("      FAILED: generate_tool_agent_taxonomy returned None")
        _log(log_file, "tool", tool_id, name, "agent_taxonomy", "failure",
             "generate_tool_agent_taxonomy returned None (missing key or generation failed)")
        return

    lib.set_tool_agent_taxonomy_draft(
        tool_id, result.agent_taxonomy_note,
        needs_verification=0, ai_confident=int(bool(result.confident)),
    )
    if result.citations:
        lib.set_entity_citations("tool", tool_id, "agent_taxonomy", result.citations, model=result.model)
    else:
        lib.clear_entity_citations("tool", tool_id, "agent_taxonomy")

    verify = lib.get_tool(tool_id)
    ok = bool(
        verify
        and verify["agent_taxonomy_note"] == result.agent_taxonomy_note.strip()
        and verify["agent_taxonomy_needs_verification"] == 0
    )
    if not ok:
        print("      VERIFY FAILED — post-write read-back did not match")
        _log(log_file, "tool", tool_id, name, "agent_taxonomy", "failure",
             "post-write verification failed")
        return
    print("      OK — agent taxonomy regenerated, needs_verification=0, verified")
    _log(log_file, "tool", tool_id, name, "agent_taxonomy", "success")


def _regen_tool_differentiation(lib: Library, tool: dict, model: str, voice_core: str,
                                 log_file: str, apply: bool) -> None:
    tool_id, name, url = tool["id"], tool["name"], tool["url"]
    print(f"  [tool {tool_id}] {name}: Competitive differentiation...")
    if not apply:
        return
    current = lib.get_tool(tool_id)
    description = (current or {}).get("description") or ""
    competitor_names = [c["name"] for c in lib.list_tool_competitors(tool_id)]
    try:
        draft = _call_with_retry(generate_tool_differentiation, name, url, description,
                                  competitor_names=competitor_names, model=model, voice_core=voice_core)
    except Exception as e:
        print(f"      FAILED: {type(e).__name__}: {e}")
        _log(log_file, "tool", tool_id, name, "competitive_differentiation", "failure",
             f"{type(e).__name__}: {e}")
        return
    if draft is None:
        print("      FAILED: generate_tool_differentiation returned None")
        _log(log_file, "tool", tool_id, name, "competitive_differentiation", "failure",
             "generate_tool_differentiation returned None (missing key or generation failed)")
        return

    lib.update_tool_differentiation(
        tool_id, draft.competitive_differentiation,
        needs_verification=0, ai_confident=int(bool(draft.confident)),
    )
    # No entity_citations mechanism for this field — ToolDifferentiationDraft
    # carries no `citations` attribute at all, confirmed by reading enrich.py.

    verify = lib.get_tool(tool_id)
    ok = bool(
        verify
        and verify["competitive_differentiation"] == draft.competitive_differentiation.strip()
        and verify["competitive_differentiation_needs_verification"] == 0
    )
    if not ok:
        print("      VERIFY FAILED — post-write read-back did not match")
        _log(log_file, "tool", tool_id, name, "competitive_differentiation", "failure",
             "post-write verification failed")
        return
    print("      OK — competitive differentiation regenerated, needs_verification=0, verified")
    _log(log_file, "tool", tool_id, name, "competitive_differentiation", "success")


# -- Communities ------------------------------------------------------------

def _regen_community_profile(lib: Library, community: dict, model: str,
                              log_file: str, apply: bool) -> None:
    community_id, name, url = community["id"], community["name"], community["url"]
    print(f"  [community {community_id}] {name}: Community profile (23 fields)...")
    if not apply:
        return
    existing_profile = lib.get_community_profile(community_id)
    try:
        draft = _call_with_retry(generate_community_profile, name, url,
                                  existing=existing_profile, model=model)
    except Exception as e:
        print(f"      FAILED: {type(e).__name__}: {e}")
        _log(log_file, "community", community_id, name, "community_profile", "failure",
             f"{type(e).__name__}: {e}")
        return
    if draft is None:
        print("      FAILED: generate_community_profile returned None")
        _log(log_file, "community", community_id, name, "community_profile", "failure",
             "generate_community_profile returned None (missing key or generation failed)")
        return

    lib.upsert_community_profile(
        community_id,
        ideal_member=draft.ideal_member, anti_fit=draft.anti_fit, value_prop=draft.value_prop,
        format_reality=draft.format_reality, engagement_level=draft.engagement_level,
        sponsor_relationship_note=draft.sponsor_relationship_note,
        application_friction=draft.application_friction, cost_value_verdict=draft.cost_value_verdict,
        notable_members=draft.notable_members, founded_year=draft.founded_year,
        public_criticism=draft.public_criticism, verdict_summary=draft.verdict_summary,
        low_confidence=int(bool(draft.low_confidence)), business_model=draft.business_model,
        primary_purpose=draft.primary_purpose, cpe_eligible=draft.cpe_eligible,
        platform_type=draft.platform_type, meeting_format=draft.meeting_format,
        event_style=draft.event_style, seniority_band=draft.seniority_band,
        resources_included=draft.resources_included, needs_review=0,
        stage_focus=draft.stage_focus, jobs_program=draft.jobs_program,
        team_or_individual=draft.team_or_individual, confidence=draft.confidence,
    )
    if draft.citations:
        lib.set_entity_citations("community", community_id, "community_profile",
                                  draft.citations, model=draft.model)
    else:
        lib.clear_entity_citations("community", community_id, "community_profile")

    verify = lib.get_community_profile(community_id)
    ok = bool(
        verify
        and verify["ideal_member"] == draft.ideal_member.strip()
        and verify["verdict_summary"] == draft.verdict_summary.strip()
        and verify["needs_review"] == 0
    )
    if not ok:
        print("      VERIFY FAILED — post-write read-back did not match")
        _log(log_file, "community", community_id, name, "community_profile", "failure",
             "post-write verification failed")
        return
    print("      OK — profile regenerated (23 fields), needs_review=0, verified")
    _log(log_file, "community", community_id, name, "community_profile", "success")


# -- Batch driver ------------------------------------------------------------

def _run_tools(lib: Library, tools: list[dict], model: str, voice_core: str,
               log_file: str, apply: bool, done: set[tuple[str, int, str]]) -> None:
    total = len(tools)
    for batch_start in range(0, total, BATCH_SIZE):
        batch = tools[batch_start:batch_start + BATCH_SIZE]
        batch_num = batch_start // BATCH_SIZE + 1
        num_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE
        print(f"\n-- Tools batch {batch_num}/{num_batches} "
              f"({batch_start + 1}-{batch_start + len(batch)} of {total}) --")
        for tool in batch:
            tool_id = tool["id"]
            for field, fn in (
                ("description", _regen_tool_description),
                ("agent_taxonomy", _regen_tool_agent_taxonomy),
                ("competitive_differentiation", _regen_tool_differentiation),
            ):
                if ("tool", tool_id, field) in done:
                    print(f"  [tool {tool_id}] {tool['name']}: {field} — already done this pass, skipping")
                    continue
                fn(lib, tool, model, voice_core, log_file, apply)
                if apply:
                    time.sleep(INTER_CALL_SLEEP)
        if apply and batch_start + BATCH_SIZE < total:
            print(f"  ...pausing {INTER_BATCH_SLEEP:.0f}s between batches...")
            time.sleep(INTER_BATCH_SLEEP)


def _run_communities(lib: Library, communities: list[dict], model: str,
                      log_file: str, apply: bool, done: set[tuple[str, int, str]]) -> None:
    total = len(communities)
    for batch_start in range(0, total, BATCH_SIZE):
        batch = communities[batch_start:batch_start + BATCH_SIZE]
        batch_num = batch_start // BATCH_SIZE + 1
        num_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE
        print(f"\n-- Communities batch {batch_num}/{num_batches} "
              f"({batch_start + 1}-{batch_start + len(batch)} of {total}) --")
        for community in batch:
            community_id = community["id"]
            if ("community", community_id, "community_profile") in done:
                print(f"  [community {community_id}] {community['name']}: "
                      f"community_profile — already done this pass, skipping")
                continue
            _regen_community_profile(lib, community, model, log_file, apply)
            if apply:
                time.sleep(INTER_CALL_SLEEP)
        if apply and batch_start + BATCH_SIZE < total:
            print(f"  ...pausing {INTER_BATCH_SLEEP:.0f}s between batches...")
            time.sleep(INTER_BATCH_SLEEP)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=None, help="Path to library.db (defaults via LINKLIB_DB)")
    ap.add_argument("--apply", action="store_true",
                     help="Actually regenerate and write (default: preview counts/estimate only)")
    ap.add_argument("--only", choices=("tools", "communities", "both"), default="both",
                     help="Restrict the run to one entity type (default: both, tools first)")
    ap.add_argument("--limit", type=int, default=None,
                     help="Process at most this many items per entity type (for a small test run)")
    ap.add_argument("--log-file", default="regen_ai_drafted_fields_log.jsonl",
                     help="JSONL log path — also the resume marker (default: "
                          "regen_ai_drafted_fields_log.jsonl in the current directory)")
    args = ap.parse_args()

    db_path = resolve_db_path(args.db, allow_missing=False)
    print(f"Mode: {'APPLY (writing)' if args.apply else 'PREVIEW (no writes, no Claude calls)'}")
    print(f"DB: {db_path}")
    print(f"Log file: {args.log_file}")
    print()

    lib = Library(db_path)
    try:
        tools = lib.list_tools(approved_only=True)
        communities = lib.list_communities(approved_only=True)
        if args.limit is not None:
            tools = tools[: args.limit]
            communities = communities[: args.limit]

        n_tools = len(tools) if args.only in ("tools", "both") else 0
        n_communities = len(communities) if args.only in ("communities", "both") else 0
        tool_calls = n_tools * 3
        community_calls = n_communities * 1
        total_calls = tool_calls + community_calls

        est_seconds = (
            n_tools * 3 * EST_SECONDS_PER_TOOL_FIELD
            + n_communities * EST_SECONDS_PER_COMMUNITY_PROFILE
        )
        num_tool_batches = (n_tools + BATCH_SIZE - 1) // BATCH_SIZE if n_tools else 0
        num_community_batches = (n_communities + BATCH_SIZE - 1) // BATCH_SIZE if n_communities else 0
        est_seconds += (num_tool_batches + num_community_batches) * INTER_BATCH_SLEEP
        est_seconds += total_calls * INTER_CALL_SLEEP

        print("Counts (approved only):")
        print(f"  Tools:       {n_tools}  x 3 fields (description, agent_taxonomy, "
              f"competitive_differentiation) = {tool_calls} calls")
        print(f"  Communities: {n_communities}  x 1 call (23-field profile draft)  "
              f"= {community_calls} calls")
        print(f"  TOTAL generation calls: {total_calls}")
        print(f"  Estimated wall-clock time: ~{est_seconds / 60:.0f} minutes "
              f"(~{est_seconds / 3600:.1f} hours) at the current batch/pacing settings "
              f"(batch size {BATCH_SIZE}, {INTER_CALL_SLEEP:.0f}s/call, "
              f"{INTER_BATCH_SLEEP:.0f}s/batch — see the module docstring)")
        print()

        if not args.apply:
            print("Preview only — pass --apply to actually regenerate and write these fields.")
            return 0

        done = _load_done(args.log_file)
        if done:
            print(f"Resuming: {len(done)} (entity, field) pair(s) already logged as success in "
                  f"{args.log_file!r} — skipping those.\n")

        model = lib.get_enrich_model()
        from linklib.agent import VOICE_CORE_DEFAULT
        voice_core = lib.get_setting("voice_core") or VOICE_CORE_DEFAULT
        print(f"Using enrichment model: {model}\n")

        if args.only in ("tools", "both"):
            _run_tools(lib, tools, model, voice_core, args.log_file, args.apply, done)
        if args.only in ("communities", "both"):
            _run_communities(lib, communities, model, args.log_file, args.apply, done)

        print(f"\nDone. Full per-field log at {args.log_file!r} — review it (or grep for "
              f"'\"status\": \"failure\"') before considering this pass complete.")
        return 0
    finally:
        lib.close()


if __name__ == "__main__":
    sys.exit(main())
