#!/usr/bin/env python3
"""Manual QA — Feature Taxonomy scan tool Phase 2: run the origination-mode
drafting function (linklib/feature_scan.py) against 1-2 real tools and print
the result for quality review. This makes real API calls (Exa + Claude, a
few cents/dollars) and writes NOTHING to any database — the point is to
read the printed features and judge whether the naming/curation/sourcing is
good enough before Phase 3 (roster-wide accumulation + merge + the actual
feature_review_queue write) gets built on top of it.

Set ANTHROPIC_API_KEY and EXA_API_KEY first. Without EXA_API_KEY the draft
still runs, but grounds on the model's own knowledge only (low_confidence
will read True) — worth one comparison run either way to see the quality
gap.

Usage:
    python -m scripts.test_feature_scan_origination \\
        --tool "Mercury" --url https://mercury.com --category "Neobanking"

    # First real test per the approved Phase 2 plan: Neobanking (10 tools,
    # thin-roster threshold doesn't trigger since 10 >= 4) — try 1-2 tools:
    python -m scripts.test_feature_scan_origination \\
        --tool "Mercury" --url https://mercury.com --category "Neobanking" --roster-size 10
    python -m scripts.test_feature_scan_origination \\
        --tool "Rho" --url https://rho.co --category "Neobanking" --roster-size 10

--existing lets you pass a comma-separated list of already-curated feature
names for the category (empty for a true from-scratch origination run,
which is the normal case since this category has none yet).
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from linklib.feature_scan import draft_tool_features_for_category


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tool", required=True, help="vendor/tool name")
    ap.add_argument("--url", required=True, help="vendor homepage URL")
    ap.add_argument("--category", required=True, help="tool_categories name, e.g. Neobanking")
    ap.add_argument("--existing", default="", help="comma-separated existing curated feature names")
    ap.add_argument("--roster-size", type=int, default=0,
                     help="category's full tool count (drives the thin-roster rule below 4)")
    ap.add_argument("--model", default=os.environ.get("LINKLIB_ENRICH_MODEL", "claude-opus-5"))
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set — nothing to run.", file=sys.stderr)
        return 1
    if not os.environ.get("EXA_API_KEY"):
        print("Note: EXA_API_KEY is not set — this run will ground on the model's own "
              "knowledge only (low_confidence will read True).", file=sys.stderr)

    existing = [n.strip() for n in args.existing.split(",") if n.strip()]

    print(f"Researching {args.tool} ({args.url}) for category '{args.category}' "
          f"[roster_size={args.roster_size or 'unknown'}]...\n")

    draft = draft_tool_features_for_category(
        args.tool, args.url, args.category,
        existing_feature_names=existing, roster_size=args.roster_size, model=args.model,
    )

    if draft is None:
        print("Drafting failed — see logged warning (linklib.feature_scan) for the exception.")
        return 1

    print(f"Grounding sources found: {len(draft.grounding_sources)} (low_confidence={draft.low_confidence})")
    if draft.truncated:
        print("WARNING: response was truncated mid-generation (even after the automatic retry) — "
              "the feature list below may be missing whatever the model would have proposed after "
              "the cutoff point.")
    for h in draft.grounding_sources:
        print(f"  [tier {h.tier}] {h.title}  ({h.url})")
    print()

    print(f"Proposed features: {len(draft.features)}\n")
    for i, f in enumerate(draft.features, 1):
        print(f"{i}. {f.name}")
        print(f"   definition:   {f.definition}")
        print(f"   availability: {f.availability}   ai_enabled: {f.ai_enabled}   "
              f"confident: {f.confident}")
        print(f"   source:       [tier {f.source_tier}] {f.source_url or '(none cited)'}")
        if f.note:
            print(f"   note:         {f.note}")
        print()

    print("=" * 80)
    print(f"Model: {draft.model}   verified_as_of: {draft.verified_as_of}")
    print(f"Claude tokens: in={draft.input_tokens} out={draft.output_tokens}   "
          f"claude_cost=${draft.cost_usd:.4f}   exa_cost=${draft.exa_cost_usd:.4f}   "
          f"total=${draft.cost_usd + draft.exa_cost_usd:.4f}")
    print("\nNo writes were made — this is a print-only quality check (Phase 2 scope).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
