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

Stale "Verified by X on Y" stamp fix (2026-08): forcing needs_verification/
needs_review=0 above only suppresses the review BADGE — it does not touch
narrative_review_log, so without an explicit signal the edit page would go
on showing a prior human's stamp against content that human never actually
saw. Every write call above also passes `clear_description_verification_stamp`/
`clear_verification_stamp=True` (agent taxonomy needs no such kwarg — see
`Library.set_tool_agent_taxonomy_draft`'s docstring), so a regenerated
field always renders "never verified" until someone explicitly clicks
Mark verified again — same outcome a human clicking Generate/Refresh/Save
in the live admin UI produces, just reached via this script's different
needs_verification=0 bypass instead of the live routes' =1.

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

------------------------------------------------------------------------
2026-08 hardening — see CLAUDE.md's citation-tag investigation bullets for
the full write-up; briefly:

  1. --ids targeting: comma-separated tool or community IDs, bypassing the
     approved-only list entirely (a non-approved targeted ID is still
     processed, with a printed note, never silently dropped — the same
     "no silent drop" discipline every other admin-facing filter in this
     repo already follows). Requires --only tools or --only communities
     (not the default "both"), since a tool ID and a community ID are
     different ID spaces and applying the same numeric list to both tables
     at once would silently do the wrong thing for whichever table an ID
     doesn't belong to.

  2. --sample N: makes N REAL generation calls (costs real money — this is
     the one thing about this mode that isn't free) against the resolved
     item list and prints the FULL generated content for each field, with
     no DB writes at all. Closes a real gap the original preview mode had:
     it only ever showed counts and a rough cost/time estimate, never
     actual generated text, so there was no way to eyeball whether a fix
     actually produces good output before committing to a real --apply
     pass. Logged to the JSONL log as status="preview" — a third status
     alongside "success"/"failure", so _load_done's resume logic can never
     mistake a preview draw for a completed regeneration. Runs standalone
     and returns immediately; --apply is ignored if --sample is also given
     (re-run without --sample to actually do the full pass).

  3. Real per-item logging: every JSONL row now also carries input_tokens/
     output_tokens/cost_usd (whenever a draft actually came back, even if
     the subsequent write failed verification), citations_count, and
     low_confidence — not just success/failure, the only two facts the
     original log captured.

  4. Cost/spend logging closes a real, confirmed gap: the ORIGINAL
     throwaway run (2026-08-26, ~84 tools) never called
     Library.record_enrichment_cost at all, so that run's real spend was
     never recorded anywhere and is now unrecoverable except by
     re-running. Every real generation call this hardened script makes —
     in --apply mode AND in --sample mode — now calls
     record_enrichment_cost the moment a draft comes back (cost was
     already incurred at that point regardless of what happens next, same
     "the API call cost real money either way" principle CLAUDE.md's
     enrichment_cost table comment already states for article enrichment).

  5. Empty-result guards, closing the Phase 0 report's A1 parity gap: this
     script used to write draft.description / result.agent_taxonomy_note /
     draft.competitive_differentiation to the DB unconditionally once the
     generator returned non-None, unlike the live `_run_tool_research`
     background job's own `if result.agent_taxonomy_note.strip():` guard
     (webapp/app.py). Description and Agent taxonomy are now DEFENSIVE
     checks only — generate_tool_description/generate_tool_agent_taxonomy
     already raise internally on a totally-empty parsed result as of the
     citation-tag fix (#427/#428), so an empty draft.description or
     result.agent_taxonomy_note should be unreachable here in practice;
     the guard exists so this script's STRUCTURE actually matches the live
     route's, not just its outcome. Competitive differentiation is a REAL
     fix: generate_tool_differentiation has no citations mechanism at all
     (still strict JSON, untouched by the citation-tag fix) and has no
     internal empty-result protection of its own — an empty
     competitive_differentiation could genuinely come back and, before
     this hardening, would have silently blanked the field.

  6. Citations-validation parity, closing the Phase 0 report's other A1
     gap: Description's and the Community profile draft's citations now
     round-trip through the same `_validate_citations_payload` (imported
     from webapp.app) the live submit routes use, before being persisted —
     both fields' real admin Generate call is stateless AJAX with no
     entity id at draft time, so their citations normally travel through
     the browser as a JSON hidden-input value and get re-validated
     server-side (URL-scheme check, title length cap, sequential
     renumbering) before ever reaching Library.set_entity_citations (see
     CLAUDE.md's Description/Community-profile grounding-fix bullets).
     This script calls the generators directly, server-side, with no
     browser round-trip at all — draft.citations is already well-typed
     Python, not untrusted client JSON — but running it through the same
     validation closes the parity gap defensively rather than assuming a
     server-computed list can never need it. Agent taxonomy's citations
     are UNCHANGED — its live path (_run_tool_research) writes
     result.citations directly with no _validate_citations_payload call
     either, so this script already matched it correctly.

  7. --field (2026-08 follow-up, motivated by the Differentiation
     content-exclusion fix — see CLAUDE.md and issue #445): narrows TOOLS
     mode to a subset of {description, agent_taxonomy,
     competitive_differentiation} — repeatable and/or comma-separated,
     both forms combine, order on the command line doesn't matter (always
     normalized back to the real per-tool regeneration order). Omitting it
     regenerates all three, exactly as before this flag existed — a true
     no-op for every prior invocation, tested explicitly
     (test_regen_field_flag.py). Lets a single-field full-catalog pass
     (e.g. `--only tools --field competitive_differentiation --apply`,
     no --ids) run far cheaper than the original 3-fields-per-tool design
     when only one field needs re-doing — the exact shape of Differentiation's
     own targeted re-run once #445 landed. Has no effect on Communities'
     single community_profile draft, which has no field concept to narrow
     — --field with --only communities prints a note and changes nothing.
------------------------------------------------------------------------
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from linklib.db import Library, resolve_db_path  # noqa: E402
from linklib.enrich import (
    COMMUNITY_PROFILE_FIELDS,
    generate_tool_description,
    generate_tool_agent_taxonomy,
    generate_tool_differentiation,
    generate_community_profile,
)
from linklib.voice_mechanics import normalize_voice_mechanics
from webapp.app import _validate_citations_payload

# -- --field (2026-08 follow-up) --------------------------------------------
# The three tools-mode fields, in the same order they're regenerated for a
# given tool (see the module docstring above) — --field limits a run to a
# subset of these without touching Communities' single community_profile
# call, which has no field concept of its own to select from.
TOOL_FIELDS = ("description", "agent_taxonomy", "competitive_differentiation")

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
    prior run against this same --log-file, so re-running skips them.
    Only "success" counts — a "preview" row (from --sample) never marks
    anything done, since nothing was written to the DB."""
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
          status: str, detail: str = "", input_tokens: int | None = None,
          output_tokens: int | None = None, cost_usd: float | None = None,
          citations_count: int | None = None, low_confidence: bool | None = None) -> None:
    """status is "success" | "failure" | "preview" (the last only from
    --sample mode — see the module docstring's hardening section). The
    five optional fields are only present when known — a call that raised
    before a draft ever came back has none of them; a call that returned a
    draft but then failed verification still has all five, since the cost
    was incurred either way."""
    row = {
        "timestamp": _now_iso(),
        "entity_type": entity_type,
        "entity_id": entity_id,
        "name": name,
        "field": field,
        "status": status,
        "detail": detail,
    }
    if input_tokens is not None:
        row["input_tokens"] = input_tokens
    if output_tokens is not None:
        row["output_tokens"] = output_tokens
    if cost_usd is not None:
        row["cost_usd"] = cost_usd
    if citations_count is not None:
        row["citations_count"] = citations_count
    if low_confidence is not None:
        row["low_confidence"] = low_confidence
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


def _validated_citations(citations: list[dict]) -> list[dict]:
    """Runs an already-well-typed, server-computed citations list through
    the SAME validation the live browser-round-trip submit routes apply to
    Description's/the Community profile's citations — see the module
    docstring's hardening section, item 6. A JSON round-trip
    (_validate_citations_payload only accepts a raw string, matching its
    real caller's hidden-form-input shape) rather than a re-implementation,
    so this can't silently drift from what the live route actually does."""
    if not citations:
        return []
    return _validate_citations_payload(json.dumps(citations))


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

    # Cost was incurred the moment `draft` came back non-None, regardless
    # of what happens next — record it now, not conditionally on a
    # successful write (hardening item 4).
    lib.record_enrichment_cost(None, draft.model, draft.input_tokens, draft.output_tokens, draft.cost_usd)
    log_kwargs = dict(input_tokens=draft.input_tokens, output_tokens=draft.output_tokens,
                       cost_usd=draft.cost_usd, citations_count=len(draft.citations),
                       low_confidence=bool(draft.low_confidence))

    # Defensive empty-result guard (hardening item 5) — generate_tool_description
    # already raises internally on a totally-empty parsed description as of
    # the citation-tag fix, so this should be unreachable in practice; kept
    # so this function's structure actually matches _run_tool_research's
    # own guard, not just its outcome.
    if not draft.description.strip():
        print("      FAILED: empty description — not written, existing content preserved")
        _log(log_file, "tool", tool_id, name, "description", "failure",
             "generate_tool_description returned an empty description", **log_kwargs)
        return

    current = lib.get_tool(tool_id)
    if current is None:
        print("      FAILED: tool vanished before save")
        _log(log_file, "tool", tool_id, name, "description", "failure", "tool vanished before save", **log_kwargs)
        return

    lib.update_tool(
        tool_id,
        name=current["name"], description=draft.description, url=current["url"],
        categories=current["categories"], advisor=current["advisor"], promoted=current["promoted"],
        vendor_email=current.get("vendor_email") or "", warm_intro_enabled=current.get("warm_intro_enabled") or 0,
        vendor_name=current.get("vendor_name") or "", summary=draft.summary,
        description_needs_verification=0, description_ai_confident=int(bool(draft.confident)),
        # Stale-stamp fix (2026-08): forcing needs_verification=0 above is
        # this script's own deliberate bypass of the review badge, but the
        # content is still a fresh, not-yet-human-reviewed draft — the old
        # "Verified by X on Y" stamp must not go on showing against it.
        clear_description_verification_stamp=True,
        source="script",
    )
    # Citations-validation parity (hardening item 6).
    validated_citations = _validated_citations(draft.citations)
    if validated_citations:
        lib.set_entity_citations("tool", tool_id, "description", validated_citations, model=draft.model)
    else:
        lib.clear_entity_citations("tool", tool_id, "description")

    # Compare against the NORMALIZED value, not the raw draft — Library.
    # update_tool() runs every prose field through normalize_voice_mechanics
    # (the spaced-em-dash mechanical backstop) before writing, so a draft
    # containing a spaced em dash is stored correctly-but-different from
    # draft.description.strip() itself. Comparing against the raw draft
    # made this verify step fail on perfectly good, already-fixed writes.
    verify = lib.get_tool(tool_id)
    ok = bool(
        verify
        and verify["description"] == normalize_voice_mechanics(draft.description.strip())
        and verify["summary"] == normalize_voice_mechanics(draft.summary.strip())
        and verify["description_needs_verification"] == 0
    )
    if not ok:
        print("      VERIFY FAILED — post-write read-back did not match")
        _log(log_file, "tool", tool_id, name, "description", "failure",
             "post-write verification failed", **log_kwargs)
        return
    print(f"      OK — description/summary regenerated, needs_verification=0, verified "
          f"(cost=${draft.cost_usd:.4f}, citations={len(validated_citations)})")
    _log(log_file, "tool", tool_id, name, "description", "success", **log_kwargs)


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

    lib.record_enrichment_cost(None, result.model, result.input_tokens, result.output_tokens, result.cost_usd)
    log_kwargs = dict(input_tokens=result.input_tokens, output_tokens=result.output_tokens,
                       cost_usd=result.cost_usd, citations_count=len(result.citations),
                       low_confidence=bool(result.low_confidence))

    # This is the EXACT gap the Phase 0 report's A1 flagged: the live
    # _run_tool_research background job only persists when
    # `result.agent_taxonomy_note.strip()` is truthy (webapp/app.py); this
    # script used to write unconditionally. generate_tool_agent_taxonomy
    # already raises internally on a totally-empty parsed note as of the
    # citation-tag fix, so this should be unreachable in practice now —
    # kept anyway to actually match the live route's structure.
    if not result.agent_taxonomy_note.strip():
        print("      FAILED: empty agent taxonomy note — not written, existing content preserved")
        _log(log_file, "tool", tool_id, name, "agent_taxonomy", "failure",
             "generate_tool_agent_taxonomy returned an empty note", **log_kwargs)
        return

    lib.set_tool_agent_taxonomy_draft(
        tool_id, result.agent_taxonomy_note,
        needs_verification=0, ai_confident=int(bool(result.confident)),
        source="script",
    )
    # Citations parity: kept as a DIRECT write here, matching the live
    # _run_tool_research background job exactly — Agent taxonomy's
    # citations are always server-computed and persisted directly, never
    # round-tripped through a browser hidden input the way Description's
    # are, so there's no _validate_citations_payload call in ITS live path
    # either (hardening item 6's docstring note). Nothing to change here.
    if result.citations:
        lib.set_entity_citations("tool", tool_id, "agent_taxonomy", result.citations, model=result.model)
    else:
        lib.clear_entity_citations("tool", tool_id, "agent_taxonomy")

    # Same normalize-before-compare fix as description above —
    # set_tool_agent_taxonomy_draft also runs agent_taxonomy_note through
    # normalize_voice_mechanics before writing.
    verify = lib.get_tool(tool_id)
    ok = bool(
        verify
        and verify["agent_taxonomy_note"] == normalize_voice_mechanics(result.agent_taxonomy_note.strip())
        and verify["agent_taxonomy_needs_verification"] == 0
    )
    if not ok:
        print("      VERIFY FAILED — post-write read-back did not match")
        _log(log_file, "tool", tool_id, name, "agent_taxonomy", "failure",
             "post-write verification failed", **log_kwargs)
        return
    print(f"      OK — agent taxonomy regenerated, needs_verification=0, verified "
          f"(cost=${result.cost_usd:.4f}, citations={len(result.citations)})")
    _log(log_file, "tool", tool_id, name, "agent_taxonomy", "success", **log_kwargs)


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

    lib.record_enrichment_cost(None, draft.model, draft.input_tokens, draft.output_tokens, draft.cost_usd)
    log_kwargs = dict(input_tokens=draft.input_tokens, output_tokens=draft.output_tokens,
                       cost_usd=draft.cost_usd, low_confidence=bool(draft.low_confidence))

    # A REAL fix, unlike description/agent_taxonomy above:
    # generate_tool_differentiation is untouched by the citation-tag fix
    # (no citations mechanism at all — still strict JSON, no document
    # blocks) and has no internal empty-result protection of its own;
    # `data.get("competitive_differentiation", "")` can legitimately come
    # back empty with no exception raised. The live AJAX Generate route
    # doesn't guard this either, but it only ever hands the draft back to
    # a human for review before any DB write happens — this script writes
    # directly, so it's the one place that actually needed the check.
    if not draft.competitive_differentiation.strip():
        print("      FAILED: empty competitive differentiation — not written, existing content preserved")
        _log(log_file, "tool", tool_id, name, "competitive_differentiation", "failure",
             "generate_tool_differentiation returned an empty result", **log_kwargs)
        return

    lib.update_tool_differentiation(
        tool_id, draft.competitive_differentiation,
        needs_verification=0, ai_confident=int(bool(draft.confident)),
        # Stale-stamp fix (2026-08) — same reasoning as the description call
        # above: needs_verification=0 is this script's own bypass, but this
        # is still a fresh draft, so any old "Verified by X on Y" stamp must
        # be cleared.
        clear_verification_stamp=True,
        source="script",
    )
    # No entity_citations mechanism for this field — ToolDifferentiationDraft
    # carries no `citations` attribute at all, confirmed by reading enrich.py.

    # Same normalize-before-compare fix as description above —
    # update_tool_differentiation also runs competitive_differentiation
    # through normalize_voice_mechanics before writing.
    verify = lib.get_tool(tool_id)
    ok = bool(
        verify
        and verify["competitive_differentiation"] == normalize_voice_mechanics(
            draft.competitive_differentiation.strip())
        and verify["competitive_differentiation_needs_verification"] == 0
    )
    if not ok:
        print("      VERIFY FAILED — post-write read-back did not match")
        _log(log_file, "tool", tool_id, name, "competitive_differentiation", "failure",
             "post-write verification failed", **log_kwargs)
        return
    print(f"      OK — competitive differentiation regenerated, needs_verification=0, verified "
          f"(cost=${draft.cost_usd:.4f})")
    _log(log_file, "tool", tool_id, name, "competitive_differentiation", "success", **log_kwargs)


# -- Communities ------------------------------------------------------------

def _regen_community_profile(lib: Library, community: dict, model: str, voice_core: str,
                              log_file: str, apply: bool) -> None:
    community_id, name, url = community["id"], community["name"], community["url"]
    print(f"  [community {community_id}] {name}: Community profile (23 fields)...")
    if not apply:
        return
    existing_profile = lib.get_community_profile(community_id)
    try:
        draft = _call_with_retry(generate_community_profile, name, url,
                                  existing=existing_profile, model=model, voice_core=voice_core)
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

    lib.record_enrichment_cost(None, draft.model, draft.input_tokens, draft.output_tokens, draft.cost_usd)
    log_kwargs = dict(input_tokens=draft.input_tokens, output_tokens=draft.output_tokens,
                       cost_usd=draft.cost_usd, citations_count=len(draft.citations),
                       low_confidence=bool(draft.low_confidence))
    # No per-field empty guard needed here — generate_community_profile
    # already hard-fails (returns None) on a totally-empty parsed draft as
    # of the community-profile citation fix, and a partial draft (most
    # fields populated, a few genuinely blank) is still useful content,
    # unlike a single-field draft going empty.

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
        # Stale-stamp fix (2026-08) — same reasoning as the tool-side calls
        # above: needs_review=0 is this script's own bypass, but this is
        # still a fresh draft, so any old "Reviewed by X on Y" stamp must be
        # cleared.
        clear_verification_stamp=True,
        source="script",
    )
    # Citations-validation parity (hardening item 6) — same reasoning as
    # Description above: the Community profile's Generate call is also
    # stateless AJAX with no community_id at draft time, so its citations
    # normally round-trip through the browser and get validated
    # server-side. This script calls the generator directly and already
    # has well-typed citations, but runs them through the same validation
    # defensively.
    validated_citations = _validated_citations(draft.citations)
    if validated_citations:
        lib.set_entity_citations("community", community_id, "community_profile",
                                  validated_citations, model=draft.model)
    else:
        lib.clear_entity_citations("community", community_id, "community_profile")

    # Same normalize-before-compare fix as description above —
    # upsert_community_profile also runs every prose field (including these
    # two) through normalize_voice_mechanics before writing.
    verify = lib.get_community_profile(community_id)
    ok = bool(
        verify
        and verify["ideal_member"] == normalize_voice_mechanics(draft.ideal_member.strip())
        and verify["verdict_summary"] == normalize_voice_mechanics(draft.verdict_summary.strip())
        and verify["needs_review"] == 0
    )
    if not ok:
        print("      VERIFY FAILED — post-write read-back did not match")
        _log(log_file, "community", community_id, name, "community_profile", "failure",
             "post-write verification failed", **log_kwargs)
        return
    print(f"      OK — profile regenerated (23 fields), needs_review=0, verified "
          f"(cost=${draft.cost_usd:.4f}, citations={len(validated_citations)})")
    _log(log_file, "community", community_id, name, "community_profile", "success", **log_kwargs)


# -- Sample preview (hardening item 2) --------------------------------------

def _print_draft_fields(field: str, draft) -> None:
    """Full, untruncated content for one generated draft — the actual gap
    the old preview mode had (counts/estimates only, never real text)."""
    citations = getattr(draft, "citations", []) or []
    print(f"  model={draft.model}  cost=${draft.cost_usd:.4f}  "
          f"tokens=in:{draft.input_tokens}/out:{draft.output_tokens}  "
          f"low_confidence={bool(getattr(draft, 'low_confidence', False))}  "
          f"confident={bool(getattr(draft, 'confident', False))}")
    if citations:
        print(f"  citations ({len(citations)}):")
        for c in citations:
            print(f"    [{c['n']}] {c['title']!r} — {c['url']}")
    else:
        print("  citations: none")
    if field == "description":
        print(f"  SUMMARY:\n    {draft.summary}")
        print(f"  DESCRIPTION:\n    {draft.description}")
    elif field == "agent_taxonomy":
        print(f"  AGENT_TAXONOMY_NOTE:\n    {draft.agent_taxonomy_note}")
    elif field == "competitive_differentiation":
        print(f"  COMPETITIVE_DIFFERENTIATION:\n    {draft.competitive_differentiation}")
    elif field == "community_profile":
        for f in COMMUNITY_PROFILE_FIELDS:
            print(f"  {f.upper()}:\n    {getattr(draft, f, '')!r}")
        print(f"  CONFIDENCE: {draft.confidence}")


def _run_sample(lib: Library, tools: list[dict], communities: list[dict], model: str,
                 voice_core: str, log_file: str, n: int,
                 fields: tuple[str, ...] = TOOL_FIELDS) -> None:
    """Makes up to N real generation calls (no DB writes) across the
    resolved tool/community list, printing the full generated content for
    each — see the module docstring's hardening item 2. `fields` narrows
    which tool fields are sampled (--field, 2026-08 follow-up); Communities'
    single community_profile draft is unaffected, same as --apply mode."""
    made = 0

    tool_jobs = [
        (tool, field)
        for tool in tools
        for field in fields
    ]
    for tool, field in tool_jobs:
        if made >= n:
            break
        made += 1
        tool_id, name, url = tool["id"], tool["name"], tool["url"]
        print(f"\n{'=' * 70}\nSAMPLE {made}/{n}: [tool {tool_id}] {name} — {field}\n{'=' * 70}")
        try:
            if field == "description":
                draft = _call_with_retry(generate_tool_description, name, url,
                                          model=model, voice_core=voice_core)
            elif field == "agent_taxonomy":
                draft = _call_with_retry(generate_tool_agent_taxonomy, name, url,
                                          description=tool.get("description") or "",
                                          model=model, voice_core=voice_core)
            else:
                competitor_names = [c["name"] for c in lib.list_tool_competitors(tool_id)]
                draft = _call_with_retry(generate_tool_differentiation, name, url,
                                          tool.get("description") or "",
                                          competitor_names=competitor_names,
                                          model=model, voice_core=voice_core)
        except Exception as e:
            print(f"  FAILED: {type(e).__name__}: {e}")
            _log(log_file, "tool", tool_id, name, field, "preview", f"{type(e).__name__}: {e}")
            continue
        if draft is None:
            print("  FAILED: generator returned None")
            _log(log_file, "tool", tool_id, name, field, "preview", "generator returned None")
            continue
        lib.record_enrichment_cost(None, draft.model, draft.input_tokens, draft.output_tokens, draft.cost_usd)
        _print_draft_fields(field, draft)
        citations = getattr(draft, "citations", []) or []
        _log(log_file, "tool", tool_id, name, field, "preview", "",
             input_tokens=draft.input_tokens, output_tokens=draft.output_tokens,
             cost_usd=draft.cost_usd, citations_count=len(citations),
             low_confidence=bool(getattr(draft, "low_confidence", False)))

    for community in communities:
        if made >= n:
            break
        made += 1
        community_id, name, url = community["id"], community["name"], community["url"]
        print(f"\n{'=' * 70}\nSAMPLE {made}/{n}: [community {community_id}] {name} — "
              f"community_profile\n{'=' * 70}")
        existing_profile = lib.get_community_profile(community_id)
        try:
            draft = _call_with_retry(generate_community_profile, name, url,
                                      existing=existing_profile, model=model, voice_core=voice_core)
        except Exception as e:
            print(f"  FAILED: {type(e).__name__}: {e}")
            _log(log_file, "community", community_id, name, "community_profile", "preview",
                 f"{type(e).__name__}: {e}")
            continue
        if draft is None:
            print("  FAILED: generator returned None")
            _log(log_file, "community", community_id, name, "community_profile", "preview",
                 "generator returned None")
            continue
        lib.record_enrichment_cost(None, draft.model, draft.input_tokens, draft.output_tokens, draft.cost_usd)
        _print_draft_fields("community_profile", draft)
        _log(log_file, "community", community_id, name, "community_profile", "preview", "",
             input_tokens=draft.input_tokens, output_tokens=draft.output_tokens,
             cost_usd=draft.cost_usd, citations_count=len(draft.citations),
             low_confidence=bool(draft.low_confidence))

    print(f"\n{'=' * 70}\nSample preview done — {made} real generation call(s) made, logged as "
          f"status=\"preview\" in {log_file!r} (never counted as done by --apply's resume logic). "
          f"No DB writes were made.\n{'=' * 70}")
    if made < n:
        print(f"(Only {made} field-slot(s) were available across the resolved item list — "
              f"fewer than the requested {n}.)")


# -- Batch driver ------------------------------------------------------------

_TOOL_FIELD_FNS = {
    "description": _regen_tool_description,
    "agent_taxonomy": _regen_tool_agent_taxonomy,
    "competitive_differentiation": _regen_tool_differentiation,
}


def _run_tools(lib: Library, tools: list[dict], model: str, voice_core: str,
               log_file: str, apply: bool, done: set[tuple[str, int, str]],
               fields: tuple[str, ...] = TOOL_FIELDS) -> None:
    total = len(tools)
    for batch_start in range(0, total, BATCH_SIZE):
        batch = tools[batch_start:batch_start + BATCH_SIZE]
        batch_num = batch_start // BATCH_SIZE + 1
        num_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE
        print(f"\n-- Tools batch {batch_num}/{num_batches} "
              f"({batch_start + 1}-{batch_start + len(batch)} of {total}) --")
        for tool in batch:
            tool_id = tool["id"]
            for field in fields:
                fn = _TOOL_FIELD_FNS[field]
                if ("tool", tool_id, field) in done:
                    print(f"  [tool {tool_id}] {tool['name']}: {field} — already done this pass, skipping")
                    continue
                fn(lib, tool, model, voice_core, log_file, apply)
                if apply:
                    time.sleep(INTER_CALL_SLEEP)
        if apply and batch_start + BATCH_SIZE < total:
            print(f"  ...pausing {INTER_BATCH_SLEEP:.0f}s between batches...")
            time.sleep(INTER_BATCH_SLEEP)


def _run_communities(lib: Library, communities: list[dict], model: str, voice_core: str,
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
            _regen_community_profile(lib, community, model, voice_core, log_file, apply)
            if apply:
                time.sleep(INTER_CALL_SLEEP)
        if apply and batch_start + BATCH_SIZE < total:
            print(f"  ...pausing {INTER_BATCH_SLEEP:.0f}s between batches...")
            time.sleep(INTER_BATCH_SLEEP)


# -- ID/scope resolution (hardening item 1) ----------------------------------

def _parse_ids(raw: str | None) -> set[int] | None:
    if not raw:
        return None
    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            raise SystemExit(f"--ids: {part!r} is not an integer")
    return ids


def _parse_fields(raw: list[str] | None) -> tuple[str, ...]:
    """--field is repeatable (--field description --field agent_taxonomy)
    AND accepts a comma-separated value in each occurrence
    (--field description,agent_taxonomy) — both forms combine. Order is
    normalized to TOOL_FIELDS' own regeneration order regardless of the
    order given on the command line, matching _run_tools'/_run_sample's
    existing per-tool ordering (Description saved before Agent taxonomy is
    even generated, and so on — see the module docstring). Returns
    TOOL_FIELDS unchanged (every field) when --field was never given, so
    omitting the flag is a true no-op against every existing invocation."""
    if not raw:
        return TOOL_FIELDS
    requested: set[str] = set()
    for occurrence in raw:
        for part in occurrence.split(","):
            part = part.strip()
            if not part:
                continue
            if part not in TOOL_FIELDS:
                raise SystemExit(
                    f"--field: {part!r} is not one of {TOOL_FIELDS} "
                    f"(Communities' community_profile draft has no field concept to select from — "
                    f"--field only ever narrows tools mode)"
                )
            requested.add(part)
    if not requested:
        raise SystemExit("--field: no field names given")
    return tuple(f for f in TOOL_FIELDS if f in requested)


def _resolve_tools(lib: Library, ids: set[int] | None, limit: int | None) -> list[dict]:
    if ids is not None:
        tools = []
        for tid in sorted(ids):
            t = lib.get_tool(tid)
            if t is None:
                print(f"WARNING: --ids included tool {tid}, but no such tool exists in the DB — skipping")
                continue
            if not t.get("approved"):
                print(f"NOTE: tool {tid} ({t['name']}) is not in the approved list — processing anyway "
                      f"since it was explicitly targeted via --ids")
            tools.append(t)
        return tools
    tools = lib.list_tools(approved_only=True)
    if limit is not None:
        tools = tools[:limit]
    return tools


def _resolve_communities(lib: Library, ids: set[int] | None, limit: int | None) -> list[dict]:
    if ids is not None:
        communities = []
        for cid in sorted(ids):
            c = lib.get_community(cid)
            if c is None:
                print(f"WARNING: --ids included community {cid}, but no such community exists in the "
                      f"DB — skipping")
                continue
            if not c.get("approved"):
                print(f"NOTE: community {cid} ({c['name']}) is not in the approved list — processing "
                      f"anyway since it was explicitly targeted via --ids")
            communities.append(c)
        return communities
    communities = lib.list_communities(approved_only=True)
    if limit is not None:
        communities = communities[:limit]
    return communities


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=None, help="Path to library.db (defaults via LINKLIB_DB)")
    ap.add_argument("--apply", action="store_true",
                     help="Actually regenerate and write (default: preview counts/estimate only)")
    ap.add_argument("--only", choices=("tools", "communities", "both"), default="both",
                     help="Restrict the run to one entity type (default: both, tools first)")
    ap.add_argument("--limit", type=int, default=None,
                     help="Process at most this many items per entity type (for a small test run). "
                          "Ignored when --ids is given.")
    ap.add_argument("--ids", default=None,
                     help="Comma-separated list of specific tool or community IDs to target, "
                          "bypassing the approved-only list entirely — a non-approved targeted ID is "
                          "still processed (with a printed note), never silently dropped. Requires "
                          "--only tools or --only communities (a tool ID and a community ID are "
                          "different ID spaces).")
    ap.add_argument("--sample", type=int, default=None, metavar="N",
                     help="Make N real generation calls (costs real money) against the resolved item "
                          "list and print the FULL generated content for each, with no DB writes at "
                          "all. Logged as status=\"preview\" in the JSONL log. Runs standalone and "
                          "returns immediately — --apply is ignored if --sample is also given.")
    ap.add_argument("--field", action="append", default=None,
                     help=f"Limit TOOLS mode to a subset of {TOOL_FIELDS} — repeatable "
                          "(--field description --field agent_taxonomy) and/or comma-separated "
                          "(--field description,agent_taxonomy); both forms combine. Defaults to all "
                          "three when omitted, so existing invocations are unaffected. Communities' "
                          "single community_profile draft has no field concept and is never narrowed "
                          "by this flag.")
    ap.add_argument("--log-file", default="regen_ai_drafted_fields_log.jsonl",
                     help="JSONL log path — also the resume marker (default: "
                          "regen_ai_drafted_fields_log.jsonl in the current directory)")
    args = ap.parse_args()

    if args.ids is not None and args.only == "both":
        ap.error("--ids requires --only tools or --only communities (a tool ID and a community ID "
                  "are different ID spaces — applying the same numeric IDs to both tables at once "
                  "would silently do the wrong thing for whichever table an ID doesn't belong to)")
    if args.sample is not None and args.sample <= 0:
        ap.error("--sample must be a positive integer")

    ids = _parse_ids(args.ids)
    fields = _parse_fields(args.field)
    if args.field is not None and args.only == "communities":
        print("NOTE: --field has no effect with --only communities (the community profile draft has "
              "no field concept to select from) — every field is still regenerated for tools if any "
              "are also in scope.\n")

    db_path = resolve_db_path(args.db, allow_missing=False)
    mode = "SAMPLE (real calls, no writes)" if args.sample else \
           ("APPLY (writing)" if args.apply else "PREVIEW (no writes, no Claude calls)")
    print(f"Mode: {mode}")
    print(f"DB: {db_path}")
    print(f"Log file: {args.log_file}")
    print()

    lib = Library(db_path)
    try:
        want_tools = args.only in ("tools", "both")
        want_communities = args.only in ("communities", "both")
        tools = _resolve_tools(lib, ids, args.limit) if want_tools else []
        communities = _resolve_communities(lib, ids, args.limit) if want_communities else []

        model = lib.get_enrich_model()
        from linklib.voice_settings import VoicePromptMissing, require_voice_setting
        try:
            voice_core = require_voice_setting(lib, "voice_core")
        except VoicePromptMissing as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2

        if args.sample:
            print(f"Using enrichment model: {model}\n")
            _run_sample(lib, tools, communities, model, voice_core, args.log_file, args.sample, fields)
            if args.apply:
                print("\nNote: --apply was also given but is ignored while --sample is set — "
                      "re-run without --sample to actually run the full pass.")
            return 0

        n_tools = len(tools)
        n_communities = len(communities)
        n_fields = len(fields)
        tool_calls = n_tools * n_fields
        community_calls = n_communities * 1
        total_calls = tool_calls + community_calls

        est_seconds = (
            n_tools * n_fields * EST_SECONDS_PER_TOOL_FIELD
            + n_communities * EST_SECONDS_PER_COMMUNITY_PROFILE
        )
        num_tool_batches = (n_tools + BATCH_SIZE - 1) // BATCH_SIZE if n_tools else 0
        num_community_batches = (n_communities + BATCH_SIZE - 1) // BATCH_SIZE if n_communities else 0
        est_seconds += (num_tool_batches + num_community_batches) * INTER_BATCH_SLEEP
        est_seconds += total_calls * INTER_CALL_SLEEP

        scope_label = "targeted via --ids" if ids is not None else "approved"
        fields_label = ", ".join(fields) if n_fields < len(TOOL_FIELDS) else "description, agent_taxonomy, competitive_differentiation"
        print(f"Counts ({scope_label}):")
        print(f"  Tools:       {n_tools}  x {n_fields} field(s) ({fields_label}) = {tool_calls} calls")
        print(f"  Communities: {n_communities}  x 1 call (23-field profile draft)  "
              f"= {community_calls} calls")
        print(f"  TOTAL generation calls: {total_calls}")
        print(f"  Estimated wall-clock time: ~{est_seconds / 60:.0f} minutes "
              f"(~{est_seconds / 3600:.1f} hours) at the current batch/pacing settings "
              f"(batch size {BATCH_SIZE}, {INTER_CALL_SLEEP:.0f}s/call, "
              f"{INTER_BATCH_SLEEP:.0f}s/batch — see the module docstring)")
        print()

        if not args.apply:
            print("Preview only — pass --apply to actually regenerate and write these fields, "
                  "or --sample N to see real generated output first without writing anything.")
            return 0

        done = _load_done(args.log_file)
        if done:
            print(f"Resuming: {len(done)} (entity, field) pair(s) already logged as success in "
                  f"{args.log_file!r} — skipping those.\n")

        print(f"Using enrichment model: {model}\n")

        if want_tools:
            _run_tools(lib, tools, model, voice_core, args.log_file, args.apply, done, fields)
        if want_communities:
            _run_communities(lib, communities, model, voice_core, args.log_file, args.apply, done)

        print(f"\nDone. Full per-field log at {args.log_file!r} — review it (or grep for "
              f"'\"status\": \"failure\"') before considering this pass complete.")
        return 0
    finally:
        lib.close()


if __name__ == "__main__":
    sys.exit(main())
