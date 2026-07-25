#!/usr/bin/env python3
"""Feature comparison data — LLM enrichment first pass (search overhaul
Phase 4b). For each Software entry, drafts standalone-vs-bundled feature
rows via linklib.enrich.generate_tool_features and writes them to
tool_features with needs_verification=1 (or 0 for anything the model itself
reported as confident and grounded) — a reviewable first draft, never
treated as confirmed. Nothing here changes what the public site renders;
Phase 5's comparison matrix is the first thing that reads this data, and
only after a human review pass.

This makes real API calls under YOUR OWN Anthropic API credits — set
ANTHROPIC_API_KEY first. Always dry-run a new tool list before writing:

    python -m scripts.enrich_tool_features --db library.db --tools "Ramp,Brex" --dry-run
    python -m scripts.enrich_tool_features --db library.db --tools "Ramp,Brex"
    python -m scripts.enrich_tool_features --db library.db --limit 10

Re-running is safe: a tool/feature-name pair (case-insensitive) that's
already in tool_features is skipped, not duplicated — rerun after fixing a
bad prompt or adding new tools without redoing the ones already drafted.
Pass --force to redraft everything anyway.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from linklib import enrich as enrich_mod
from linklib.db import Library

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
        # drafting (and paying for) features twice. Not this script's job to
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
    ap.add_argument("--db", default=os.environ.get("LINKLIB_DB", "library.db"))
    ap.add_argument("--tools", default="", help="comma-separated tool names to run (test batch)")
    ap.add_argument("--limit", type=int, default=0, help="run the first N approved tools instead")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="Claude model to use")
    ap.add_argument("--dry-run", action="store_true", help="print what would be drafted, write nothing")
    ap.add_argument("--force", action="store_true",
                    help="redraft even tools that already have feature rows")
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: set ANTHROPIC_API_KEY first (this makes real API calls).", file=sys.stderr)
        return 2
    if not args.tools and not args.limit:
        print("ERROR: pass --tools \"Name1,Name2\" or --limit N — running against the whole "
              "catalog unscoped isn't supported here on purpose.", file=sys.stderr)
        return 2

    lib = Library(args.db)
    try:
        tools = _select_tools(lib, args.tools, args.limit)
        if not tools:
            print("No matching tools found.", file=sys.stderr)
            return 1

        total_cost = 0.0
        total_features = 0
        total_skipped_dupe = 0
        total_failed = 0

        for t in tools:
            existing = {f["feature_name"].strip().lower() for f in lib.list_tool_features(t["id"])}
            if existing and not args.force:
                print(f"[{t['name']}] already has {len(existing)} feature(s) — skipping (use --force to redraft)")
                continue

            print(f"[{t['name']}] drafting features…", flush=True)
            result = enrich_mod.generate_tool_features(t["name"], t["url"], t.get("description", ""), model=args.model)
            if result is None:
                print("  FAILED (SDK/key unavailable or the call errored)")
                total_failed += 1
                continue

            new_count = 0
            dupe_count = 0
            for draft in result.features:
                if draft.feature_name.strip().lower() in existing:
                    dupe_count += 1
                    continue
                existing.add(draft.feature_name.strip().lower())
                new_count += 1
                if args.dry_run:
                    # Print each drafted feature so a dry-run is actually
                    # reviewable for accuracy, not just an aggregate count —
                    # that's the whole point of running it dry first.
                    avail = []
                    if draft.standalone_available:
                        avail.append("standalone")
                    if draft.bundled_only:
                        avail.append("bundled-only")
                    avail_label = "+".join(avail) or "availability unset"
                    verify_note = "" if not draft.needs_verification else " [needs verification]"
                    notes_note = f" — {draft.notes}" if draft.notes else ""
                    print(f"    - {draft.feature_name}: {avail_label}{notes_note}{verify_note}")
                else:
                    lib.add_tool_feature(
                        t["id"], draft.feature_name,
                        standalone_available=int(draft.standalone_available),
                        bundled_only=int(draft.bundled_only),
                        notes=draft.notes, source_url=draft.source_url,
                        needs_verification=int(draft.needs_verification),
                        source="llm_enrichment", model=result.model,
                    )

            if not args.dry_run and (new_count or result.cost_usd):
                lib.record_enrichment_cost(None, result.model, result.input_tokens,
                                           result.output_tokens, result.cost_usd)

            total_cost += result.cost_usd
            total_features += new_count
            total_skipped_dupe += dupe_count
            low_conf_note = " (low confidence — thin/no page content)" if result.low_confidence else ""
            print(f"  {new_count} feature(s){low_conf_note}, ${result.cost_usd:.4f}"
                  + (f", {dupe_count} dupe(s) skipped" if dupe_count else ""))
            time.sleep(0.5)  # light rate-limit courtesy, this is a small interactive batch not a bulk job

        print(f"\n{len(tools)} tool(s) processed: {total_features} feature(s) drafted, "
              f"{total_skipped_dupe} duplicate(s) skipped, {total_failed} failed.")
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
