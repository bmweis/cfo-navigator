#!/usr/bin/env python3
"""Remaps pending source='scan' feature_review_queue proposals for one
category against a HUMAN-DEFINED target feature framework — a different job
from linklib.feature_scan.originate_category_features' own judge_cluster,
which invents its own groupings as it drafts. Here the groupings (a fixed,
Brian-approved bucket list) are given; only the per-item assignment is a
judgment call, made by linklib.feature_scan.match_items_to_framework via one
Claude call per batch.

Built for the real Neobanking incident (2026-08): 202 pending scan proposals
from the 8/23 corrected origination run needed remapping against a 41-bucket
target list Brian defined by hand after reviewing the raw output. Where
several pending items map to the same bucket, they're CONSOLIDATED into one
updated queue row (the framework's own canonical name, a synthesized
definition, and the union of every contributing item's tool links, deduped
by tool_id keeping the most complete/best-sourced copy of each) — the
now-redundant rows are DENIED with a reason naming what they were folded
into, never deleted, per CLAUDE.md's no-dead-data/always-leave-a-trace
discipline. An item matching none of the framework's buckets is denied as
out of scope. A framework bucket with no matching pending item at all is
simply skipped — no placeholder feature/link is invented for a capability no
real tool in this roster actually offers.

Hard rule, enforced by construction (this script never calls
add_category_feature/upsert_tool_feature_link, and never sets a queue item's
status to 'approved'): this is a QUEUE-TO-QUEUE remap. Every affected item
ends this script either 'pending' (rewritten in place, ready for a human's
final approve/deny pass in the existing review-queue UI) or 'denied'
(consolidated-away or out-of-scope). Final approval into category_features/
tool_feature_links stays a human action, per docs/FEATURE_TAXONOMY.md §9.

Preview by default (runs the real Claude matching/synthesis calls — there's
no cheaper way to preview a judgment call than making it, same reasoning as
originate_category_features.py's own preview mode — but writes nothing);
--apply commits the plan.

The target framework is a JSON file (see scripts/seed_data/
neobanking_feature_framework.json for the shape/format), not hardcoded, so a
different category's 30-40-line bucket list can be handed in via --framework
without touching this script.

Usage:
    python -m scripts.remap_queue_to_framework --db library.db --category Neobanking
    python -m scripts.remap_queue_to_framework --db library.db --category Neobanking --apply
    python -m scripts.remap_queue_to_framework --db library.db \\
        --framework scripts/seed_data/some_other_category_framework.json --apply
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from linklib.db import Library, resolve_db_path
from linklib.feature_scan import (
    FrameworkBucket,
    QueueItemCandidate,
    match_items_to_framework,
    synthesize_bucket_definition,
)
from linklib.voice_settings import VoicePromptMissing, require_voice_setting

_CONSOLIDATION_REASON_TMPL = "Consolidated into '{name}' during framework remap 8/24"
_OUT_OF_SCOPE_REASON = "Out of scope for confirmed Neobanking framework (8/24) — not CFO-relevant"


def _load_framework(path: str) -> tuple[str, list[FrameworkBucket]]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    buckets = [
        FrameworkBucket(index=i, group=b.get("group", ""), name=b["name"], hint=b.get("hint", ""))
        for i, b in enumerate(data.get("buckets", []))
    ]
    if not buckets:
        raise ValueError(f"{path!r} has no 'buckets' list.")
    return data.get("category", ""), buckets


def _link_completeness_score(link: dict) -> tuple:
    """Ranks two candidate copies of the same tool's link when it appears in
    more than one contributing item — prefers a real source_url, then a
    verified_as_of date, then a longer note, over whichever copy happened to
    be encountered first."""
    return (
        1 if (link.get("source_url") or "").strip() else 0,
        1 if (link.get("verified_as_of") or "").strip() else 0,
        len((link.get("note") or "").strip()),
    )


def _union_links(contributing_items: list[dict]) -> list[dict]:
    """Unions tool links across every queue item mapped to one bucket,
    deduped by tool_id — keeps whichever copy of a repeated tool's link
    scores as most complete via _link_completeness_score, not just whichever
    came first. Stable output order (by tool_id) so a re-run over an
    unchanged plan produces byte-identical payloads."""
    best_by_tool: dict[int, dict] = {}
    for item in contributing_items:
        for link in item["payload"].get("links", []):
            tool_id = link.get("tool_id")
            if tool_id is None:
                continue
            existing = best_by_tool.get(tool_id)
            if existing is None or _link_completeness_score(link) > _link_completeness_score(existing):
                best_by_tool[tool_id] = link
    return [best_by_tool[t] for t in sorted(best_by_tool)]


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--db", default=None, help="Path to library.db (or set LINKLIB_DB)")
    ap.add_argument("--category", default=None,
                     help="tool_categories name, e.g. Neobanking. Defaults to the framework "
                          "file's own 'category' field if omitted.")
    ap.add_argument("--framework", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "seed_data", "neobanking_feature_framework.json"),
                     help="Path to the target-framework JSON file (see scripts/seed_data/"
                          "neobanking_feature_framework.json for the shape).")
    ap.add_argument("--model", default=None,
                     help="Claude model for matching/synthesis. Defaults to the live "
                          "enrichment-model setting (/admin/system/ai).")
    ap.add_argument("--batch-size", type=int, default=50,
                     help="Pending items per matching call — the full bucket list is sent on "
                          "every batch regardless (default 50).")
    ap.add_argument("--apply", action="store_true",
                     help="Actually write the remap. Without this flag, runs the full matching/"
                          "synthesis pipeline and prints the plan, but writes nothing.")
    args = ap.parse_args()

    db_path = resolve_db_path(args.db)
    print(f"Reading from: {db_path}\n")

    framework_category, buckets = _load_framework(args.framework)
    category_name = args.category or framework_category
    if not category_name:
        print("No --category given and the framework file has no 'category' field.", file=sys.stderr)
        return 1
    print(f"Framework: {args.framework} ({len(buckets)} target bucket(s))")

    lib = Library(db_path)
    try:
        category_id = lib.get_tool_category_id(category_name)
        if category_id is None:
            print(f"No tool_categories row named {category_name!r}.", file=sys.stderr)
            return 1

        model = args.model or lib.get_enrich_model()
        try:
            voice_core = require_voice_setting(lib, "voice_core")
        except VoicePromptMissing as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
        print(f"Category: {category_name} (id={category_id})")
        print(f"Model: {model}")
        print(f"Mode: {'APPLY (writing for real)' if args.apply else 'PREVIEW (no writes)'}\n")

        pending = [
            item for item in lib.list_feature_review_queue(status="pending")
            if item["category_id"] == category_id and item["source"] == "scan"
        ]
        if not pending:
            print(f"No pending scan-sourced feature_review_queue items for {category_name!r}. "
                  f"Nothing to do.")
            return 0
        print(f"Pending scan item(s) found: {len(pending)}")

        candidates: list[QueueItemCandidate] = []
        unclassifiable = []
        for item in pending:
            feature = item["payload"].get("feature") or {}
            name = (feature.get("name") or "").strip()
            if not name:
                unclassifiable.append(item)
                continue
            candidates.append(QueueItemCandidate(
                item_id=item["id"], name=name, definition=feature.get("definition", ""),
            ))

        if unclassifiable:
            print(f"\n{len(unclassifiable)} pending item(s) have no payload.feature.name to match on "
                  f"(likely an existing-feature link proposal, not a new-feature draft) — left "
                  f"untouched, neither updated nor denied:")
            for item in unclassifiable:
                print(f"  #{item['id']}: proposal_type={item['proposal_type']!r}")

        if not candidates:
            print("\nNothing left to classify. Nothing was written.")
            return 0

        print(f"\nMatching {len(candidates)} item(s) against the framework via Claude ({model})...")
        matches, match_cost = match_items_to_framework(
            candidates, buckets, category_name, model=model, batch_size=args.batch_size,
        )
        if matches is None:
            print("\nmatch_items_to_framework() failed — ANTHROPIC_API_KEY is missing/invalid, or "
                  "the API call errored outright. Nothing was written; re-run once the underlying "
                  "issue is fixed.", file=sys.stderr)
            return 1

        by_id = {item["id"]: item for item in pending}
        bucket_groups: dict[int, list[dict]] = {b.index: [] for b in buckets}
        none_matches = []
        for candidate, match in zip(candidates, matches):
            item = by_id[candidate.item_id]
            if match is None:
                none_matches.append(item)
            else:
                bucket_groups[match].append(item)

        print("\n=== Plan ===")
        print(f"Claude matching cost: ${match_cost:.4f}")

        plan_rows = []
        synth_cost_total = 0.0
        for bucket in buckets:
            items = bucket_groups[bucket.index]
            if not items:
                continue
            primary, redundant = items[0], items[1:]
            contributing_defs = [(i["payload"].get("feature") or {}).get("definition", "") for i in items]

            if len(items) > 1:
                definition, synth_cost = synthesize_bucket_definition(
                    bucket.name, contributing_defs, category_name, model=model,
                    voice_core=voice_core,
                )
                synth_cost_total += synth_cost
            else:
                definition = contributing_defs[0]

            union_links = _union_links(items)
            new_payload = {
                "category_id": category_id,
                "feature": {"name": bucket.name, "definition": definition, "pointer_note": ""},
                "links": union_links,
            }
            plan_rows.append({"bucket": bucket, "primary": primary, "redundant": redundant,
                               "new_payload": new_payload})

            merge_note = f" [merging {len(items)}→1]" if len(items) > 1 else ""
            deny_note = f", denying {[r['id'] for r in redundant]}" if redundant else ""
            print(f"  bucket #{bucket.index} \"{bucket.name}\"{merge_note}: {len(items)} item(s) "
                  f"→ queue #{primary['id']} ({len(union_links)} tool link(s)){deny_note}")

        empty_buckets = [b.name for b in buckets if not bucket_groups[b.index]]
        if empty_buckets:
            print(f"\n{len(empty_buckets)} bucket(s) with NO matching pending item — skipped, no "
                  f"placeholder written:")
            for name in empty_buckets:
                print(f"  - {name}")

        if none_matches:
            print(f"\n{len(none_matches)} item(s) matched NONE of the {len(buckets)} buckets — will "
                  f"be denied as out of scope:")
            for item in none_matches:
                fname = (item["payload"].get("feature") or {}).get("name", "(unnamed)")
                print(f"  #{item['id']}: {fname}")

        total_kept = len(plan_rows)
        total_consolidated = sum(len(r["redundant"]) for r in plan_rows)
        print("\n=== Summary ===")
        print(f"  {len(candidates)} classifiable pending item(s)")
        print(f"  → {total_kept} bucket(s) matched, {total_consolidated} redundant item(s) "
              f"consolidated away")
        print(f"  → {len(none_matches)} item(s) denied as out of scope")
        print(f"  → {len(empty_buckets)} bucket(s) with no real match (skipped, no placeholder)")
        print(f"  → {len(unclassifiable)} item(s) left untouched (no feature name in payload)")
        print(f"  Expected pending count for this category after this runs: {total_kept}")
        total_cost = match_cost + synth_cost_total
        print(f"  Claude cost: matching ${match_cost:.4f} + definition synthesis "
              f"${synth_cost_total:.4f} = ${total_cost:.4f}")

        if not args.apply:
            print("\nPREVIEW ONLY — nothing was written. Re-run with --apply once this plan looks "
                  "right (flag any specific mapping decision that needs a second look first).")
            return 0

        print("\nApplying...")
        for row in plan_rows:
            bucket = row["bucket"]
            lib.update_feature_review_queue_payload(row["primary"]["id"], row["new_payload"])
            for r in row["redundant"]:
                lib.deny_feature_review_queue_item(r["id"], _CONSOLIDATION_REASON_TMPL.format(name=bucket.name))
        for item in none_matches:
            lib.deny_feature_review_queue_item(item["id"], _OUT_OF_SCOPE_REASON)

        # Write-then-read-back verification, per CLAUDE.md's one-off admin-fix discipline.
        remaining_for_category = sum(
            1 for i in lib.list_feature_review_queue(status="pending")
            if i["category_id"] == category_id
        )
        assert remaining_for_category == total_kept + len(unclassifiable), (
            f"Post-write pending count for {category_name!r} is {remaining_for_category}, expected "
            f"{total_kept + len(unclassifiable)} ({total_kept} remapped + {len(unclassifiable)} "
            f"left untouched)."
        )
        print(f"Done. {total_kept} item(s) rewritten in place, {total_consolidated + len(none_matches)} "
              f"denied. Pending count for {category_name!r} is now {remaining_for_category} — verify at "
              f"/admin/tools/software/feature-review-queue.")
    finally:
        lib.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
