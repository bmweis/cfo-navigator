#!/usr/bin/env python3
"""Agent-taxonomy research — LLM enrichment first pass (search overhaul Phase
4b, extended by the automated-research follow-up; narrowed to agent-taxonomy
only in the Feature Taxonomy Phase 1b PR 2 legacy retirement — this script
used to also draft standalone-vs-bundled tool_features rows in the same call,
before that table was retired). For each Software entry, one Claude call
(linklib.enrich.generate_tool_agent_taxonomy) drafts a whole-tool
agent-taxonomy summary — grounded in the tool's homepage plus its real
Product/Solutions-type nav pages (not guessed URL paths), written with
needs_verification=1 (or 0 for anything the model itself reported as
confident and grounded) — a reviewable draft, never treated as confirmed.
This is also the exact same drafting logic the live app runs automatically
when a new tool is added, or on-demand via the "Refresh AI research" admin
button — this script is for bulk/backfill only.

This makes real API calls under YOUR OWN Anthropic API credits — set
ANTHROPIC_API_KEY first. Always dry-run a new tool list before writing:

    python -m scripts.enrich_agent_taxonomy --db library.db --tools "Ramp,Brex" --dry-run
    python -m scripts.enrich_agent_taxonomy --db library.db --tools "Ramp,Brex"
    python -m scripts.enrich_agent_taxonomy --db library.db --limit 10

Re-running is safe: a tool with an existing agent_taxonomy_note is skipped
by default. Pass --force to redraft everything anyway — e.g. re-running the
whole catalog against improved real-nav-page grounding, replacing an earlier
guessed-path draft.
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


def _select_tools(lib: Library, names: str, limit: int) -> list[dict]:
    all_tools = lib.list_tools(approved_only=True)
    if names:
        wanted = {n.strip().lower() for n in names.split(",") if n.strip()}
        selected = [t for t in all_tools if t["name"].lower() in wanted]
        found = {t["name"].lower() for t in selected}
        missing = wanted - found
        if missing:
            print(f"WARNING: no approved tool found for: {', '.join(sorted(missing))}", file=sys.stderr)
        # A name matching more than one approved row means duplicate Software
        # entries exist for it — surface that loudly rather than silently
        # drafting (and paying for) taxonomy twice. Not this script's job to
        # dedupe; that's the admin table's duplicate-blocking feature.
        name_counts: dict[str, int] = {}
        for t in selected:
            name_counts[t["name"].lower()] = name_counts.get(t["name"].lower(), 0) + 1
        dupes = sorted(n for n, c in name_counts.items() if c > 1)
        if dupes:
            print(f"WARNING: multiple approved rows share these names — each will be drafted "
                  f"separately: {', '.join(dupes)}. Dedupe in the admin table first if that's "
                  f"not intended.", file=sys.stderr)
        return selected
    if limit:
        return all_tools[:limit]
    return all_tools


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None, help="Path to library.db (or set LINKLIB_DB)")
    ap.add_argument("--tools", default="", help="comma-separated tool names to run (test batch)")
    ap.add_argument("--limit", type=int, default=0, help="run the first N approved tools instead")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="Claude model to use")
    ap.add_argument("--dry-run", action="store_true", help="print what would be drafted, write nothing")
    ap.add_argument("--force", action="store_true",
                    help="redraft even tools that already have an agent-taxonomy note")
    args = ap.parse_args()
    args.db = resolve_db_path(args.db)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: set ANTHROPIC_API_KEY first (this makes real API calls).", file=sys.stderr)
        return 2
    if not args.tools and not args.limit:
        print("ERROR: pass --tools \"Name1,Name2\" or --limit N — running against the whole "
              "catalog unscoped isn't supported here on purpose.", file=sys.stderr)
        return 2

    lib = Library(args.db)
    try:
        try:
            voice_core = require_voice_setting(lib, "voice_core")
        except VoicePromptMissing as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2

        tools = _select_tools(lib, args.tools, args.limit)
        if not tools:
            print("No matching tools found.", file=sys.stderr)
            return 1

        total_cost = 0.0
        total_skipped_dupe = 0
        total_failed = 0
        total_taxonomy = 0

        for t in tools:
            already_researched = bool((t.get("agent_taxonomy_note") or "").strip())
            if already_researched and not args.force:
                print(f"[{t['name']}] already has agent-taxonomy research — skipping (use --force to redraft)")
                total_skipped_dupe += 1
                continue

            print(f"[{t['name']}] researching…", flush=True)
            result = enrich_mod.generate_tool_agent_taxonomy(
                t["name"], t["url"], t.get("description", ""), model=args.model, voice_core=voice_core)
            if result is None:
                print("  FAILED (SDK/key unavailable or the call errored)")
                total_failed += 1
                continue

            drafted_taxonomy = bool(result.agent_taxonomy_note.strip())
            if drafted_taxonomy:
                if args.dry_run:
                    verify_note = " [needs verification]" if result.agent_taxonomy_needs_verification else ""
                    print(f"    Agent taxonomy: {result.agent_taxonomy_note}{verify_note}")
                else:
                    lib.set_tool_agent_taxonomy_draft(
                        t["id"], result.agent_taxonomy_note,
                        needs_verification=int(result.agent_taxonomy_needs_verification),
                        source="script",
                    )
                total_taxonomy += 1

            if not args.dry_run and (drafted_taxonomy or result.cost_usd):
                lib.record_enrichment_cost(None, result.model, result.input_tokens,
                                           result.output_tokens, result.cost_usd)

            total_cost += result.cost_usd
            low_conf_note = " (low confidence — thin/no page content)" if result.low_confidence else ""
            print(f"  taxonomy drafted={drafted_taxonomy}{low_conf_note}, ${result.cost_usd:.4f}")
            time.sleep(0.5)  # light rate-limit courtesy, this is a small interactive batch not a bulk job

        print(f"\n{len(tools)} tool(s) processed: {total_taxonomy} agent-taxonomy note(s) drafted, "
              f"{total_skipped_dupe} already-researched skipped, {total_failed} failed.")
        print(f"Total cost: ${total_cost:.4f}" + (" (dry run — nothing written)" if args.dry_run else ""))
        if tools:
            per_tool = total_cost / max(len(tools) - total_failed, 1)
            approved_count = len(lib.list_tools(approved_only=True))
            print(f"Avg cost/tool: ${per_tool:.4f} — full catalog ({approved_count} tools) "
                  f"would be roughly ${per_tool * approved_count:.2f}")
    finally:
        lib.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
