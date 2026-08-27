#!/usr/bin/env python3
"""One-off, read-only report for the citation-tag investigation's blast-
radius question (2026-08, see CLAUDE.md/ARCHITECTURE.md's citation-tag
bullets): of the tools the throwaway `scripts/regen_ai_drafted_fields.py`
run actually touched, which ones currently carry the legacy-shape damage
(pseudo-citation tags, editor-facing asides, or a spaced em dash/markdown
bold left over from the old strict-JSON prompt) versus which ones failed
outright and were never overwritten at all.

Cross-references two sources, not just a blind ID range: `--log-file`
(the JSONL log the throwaway script itself wrote, one line per attempted
(entity_type, entity_id, field) tuple — see `scripts/
regen_ai_drafted_fields.py`'s own `_log()`) names exactly which tool IDs
and fields were actually attempted; `--db` is queried only for those IDs,
never a blind `id <= N` sweep, since the throwaway run's ID range isn't
guaranteed contiguous. Communities are deliberately excluded — confirmed
in the citation-tag investigation that the throwaway script's actual run
never touched communities (see the `generate_community_profile`-was-never-
exercised finding in CLAUDE.md).

Prints four sections:
  1. Per-field failure list straight from the log — status="failure" rows,
     with their logged `detail`, so a re-run can be scoped to exactly
     what's still missing.
  2. Cite-tag pollution — for every tool ID logged as a SUCCESS on a given
     field, checks that field's CURRENT stored text (description/summary/
     agent_taxonomy_note/competitive_differentiation, whichever the field
     name maps to) against the same forbidden-substring list the two
     `diagnose_*_citations.py` scripts already use.
  3. Legacy-shape rows — the same current text checked for a spaced em
     dash (" — ", banned by the voice guide's em-dash policy regardless of
     citations) and markdown bold ("**", banned by the new D1 prompt
     rules), both tells of the old prompt shape even when no literal
     pseudo-citation tag survived.
  4. Deduplicated summary — the real "needs regeneration" picture: the
     union of distinct tool IDs across sections 1-3 combined (a tool
     counted once even if it shows up in more than one section, or more
     than once within one section — e.g. both a failure AND a pollution
     hit on different fields), broken out per field (how many distinct
     tools need description vs. agent_taxonomy vs.
     competitive_differentiation vs. more than one), since the raw
     21+75+25 counts overcount whenever the same tool appears in more
     than one section or more than one field.

------------------------------------------------------------------------
THIS SCRIPT MAKES NO WRITE CALLS OF ANY KIND.
It only calls Library.get_tool (a read) for each ID named in the log, and
otherwise only reads the JSONL log file from disk. Nothing in this script
writes to library.db, entity_citations, the JSONL log, or any other file.
Output goes to stdout only.
------------------------------------------------------------------------

Usage (from the production container, e.g. `railway ssh`):
    python -m scripts.report_regen_blast_radius \
        --log-file /data/regen_log_2026-08-26.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys

from linklib.db import Library, resolve_db_path

# Same list the two diagnose_*_citations.py scripts already check —
# kept identical rather than reinvented, so a "clean" verdict here means
# the same thing it means there.
_FORBIDDEN_SUBSTRINGS = [
    "cite index=", "<cite", '"confident"', '"summary"', '"description"',
    '"ideal_member"', "{\"description\"", "{\"ideal_member\"", "```",
]

# Field name (as logged by regen_ai_drafted_fields.py) -> the tools.*
# column(s) that field actually wrote. "description" writes both
# description and summary in one call (see _regen_tool_description).
_FIELD_TO_COLUMNS = {
    "description": ["description", "summary"],
    "agent_taxonomy": ["agent_taxonomy_note"],
    "competitive_differentiation": ["competitive_differentiation"],
}


def _load_log(log_file: str) -> list[dict]:
    rows: list[dict] = []
    with open(log_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=None, help="Path to library.db (defaults via LINKLIB_DB)")
    ap.add_argument("--log-file", required=True,
                     help="Path to the JSONL log written by scripts/regen_ai_drafted_fields.py")
    args = ap.parse_args()

    rows = _load_log(args.log_file)
    tool_rows = [r for r in rows if r.get("entity_type") == "tool"]
    community_rows = [r for r in rows if r.get("entity_type") == "community"]
    print(f"Log: {args.log_file}")
    print(f"Total lines: {len(rows)}  (tool: {len(tool_rows)}, community: {len(community_rows)})")
    if community_rows:
        print(f"NOTE: {len(community_rows)} community row(s) found in the log — the investigation's "
              f"finding that the throwaway run never touched communities may not hold for THIS log file. "
              f"Not analyzed by this script (tools only) — flag before proceeding.")

    touched_tool_ids = sorted({r["entity_id"] for r in tool_rows})
    print(f"Distinct tool IDs touched: {len(touched_tool_ids)}  "
          f"(range {min(touched_tool_ids) if touched_tool_ids else '-'}"
          f"-{max(touched_tool_ids) if touched_tool_ids else '-'})")

    # -- Section 1: failures, straight from the log ---------------------
    failures = [r for r in tool_rows if r.get("status") == "failure"]
    print(f"\n{'=' * 70}\n1. FAILURES ({len(failures)}) — never overwritten, still on old content\n{'=' * 70}")
    if not failures:
        print("  (none)")
    for r in failures:
        print(f"  [tool {r['entity_id']}] {r.get('name', '?')} — field={r.get('field')!r}")
        print(f"      detail: {r.get('detail', '')!r}")

    # Only fields logged as an actual SUCCESS were overwritten with the
    # (possibly-polluted) new content — a failure means the row is
    # unchanged from whatever it was before this run, so it's out of
    # scope for sections 2/3 below (nothing NEW to check there).
    successes = [r for r in tool_rows if r.get("status") == "success"]
    by_id_field: dict[tuple[int, str], dict] = {(r["entity_id"], r["field"]): r for r in successes}

    db_path = resolve_db_path(args.db, allow_missing=False)
    print(f"\nDB (read-only lookups): {db_path}")
    lib = Library(db_path)
    try:
        # (id, name, display_label, base_field, detail) — display_label is
        # "field (column)" for section 2/3's existing per-row printout;
        # base_field is the plain log field name ("description",
        # "agent_taxonomy", "competitive_differentiation"), used by
        # section 4's dedup below so "description (description)" and
        # "description (summary)" collapse back to one logical field
        # instead of counting as two.
        pollution_hits: list[tuple[int, str, str, str, str]] = []
        legacy_shape_hits: list[tuple[int, str, str, str, str]] = []

        # Cache tool rows so a tool with multiple successful fields is
        # only fetched once.
        tool_cache: dict[int, dict | None] = {}

        for (tool_id, field), log_row in sorted(by_id_field.items()):
            if field not in _FIELD_TO_COLUMNS:
                continue
            if tool_id not in tool_cache:
                tool_cache[tool_id] = lib.get_tool(tool_id)
            tool = tool_cache[tool_id]
            if tool is None:
                print(f"  [tool {tool_id}] logged success but no longer exists in the DB — skipped")
                continue
            name = tool.get("name", "?")
            for col in _FIELD_TO_COLUMNS[field]:
                text = tool.get(col) or ""
                if not text:
                    continue
                hits = [s for s in _FORBIDDEN_SUBSTRINGS if s in text]
                if hits:
                    pollution_hits.append((tool_id, name, f"{field} ({col})", field, ", ".join(hits)))
                if " — " in text:
                    legacy_shape_hits.append((tool_id, name, f"{field} ({col})", field, "spaced em dash"))
                if "**" in text:
                    legacy_shape_hits.append((tool_id, name, f"{field} ({col})", field, "markdown bold (**)"))

        print(f"\n{'=' * 70}\n2. CITE-TAG POLLUTION ({len(pollution_hits)}) — "
              f"successfully-overwritten rows still carrying a forbidden substring\n{'=' * 70}")
        if not pollution_hits:
            print("  (none)")
        for tool_id, name, label, _base_field, hits in pollution_hits:
            print(f"  [tool {tool_id}] {name} — {label}: {hits}")

        print(f"\n{'=' * 70}\n3. LEGACY-SHAPE ROWS ({len(legacy_shape_hits)}) — "
              f"spaced em dash or markdown bold, no literal tag required\n{'=' * 70}")
        if not legacy_shape_hits:
            print("  (none)")
        for tool_id, name, label, _base_field, kind in legacy_shape_hits:
            print(f"  [tool {tool_id}] {name} — {label}: {kind}")

        # -- Section 4: deduplicated summary ---------------------------
        # (tool_id, base_field) -> name, for every distinct hit across all
        # three sections combined — a plain set union, so a tool appearing
        # in more than one section (e.g. a failure on one field AND a
        # pollution hit on another) or more than once within one section
        # (e.g. both description and summary polluted) still counts once
        # per (tool, field), and once per tool overall.
        affected: dict[tuple[int, str], str] = {}
        for r in failures:
            affected[(r["entity_id"], r.get("field", "?"))] = r.get("name", "?")
        for tool_id, name, _label, base_field, _detail in pollution_hits:
            affected[(tool_id, base_field)] = name
        for tool_id, name, _label, base_field, _detail in legacy_shape_hits:
            affected[(tool_id, base_field)] = name

        distinct_tool_ids = sorted({tid for tid, _field in affected})
        by_field: dict[str, set[int]] = {}
        for (tid, field) in affected:
            by_field.setdefault(field, set()).add(tid)
        fields_per_tool: dict[int, set[str]] = {}
        for (tid, field) in affected:
            fields_per_tool.setdefault(tid, set()).add(field)
        multi_field_tools = {tid: fields for tid, fields in fields_per_tool.items() if len(fields) > 1}

        print(f"\n{'=' * 70}\n4. DEDUPLICATED SUMMARY — real \"needs regeneration\" count\n{'=' * 70}")
        print(f"  Distinct tools needing regeneration (any field): {len(distinct_tool_ids)}")
        print(f"  Distinct (tool, field) pairs: {len(affected)}  "
              f"(raw section 1+2+3 counts were {len(failures)}+{len(pollution_hits)}+{len(legacy_shape_hits)} "
              f"= {len(failures) + len(pollution_hits) + len(legacy_shape_hits)}, which double-counts a tool "
              f"hit by more than one check on the same field, and doesn't collapse per-tool at all)")
        print("\n  By field (distinct tools):")
        for field in sorted(by_field):
            print(f"    {field}: {len(by_field[field])}")
        print(f"\n  Tools needing more than one field regenerated: {len(multi_field_tools)}")
        for tid in sorted(multi_field_tools):
            print(f"    [tool {tid}] {affected.get((tid, sorted(multi_field_tools[tid])[0]), '?')} — "
                  f"{sorted(multi_field_tools[tid])}")
        print("\n  Full list (tool_id, name):")
        for tid in distinct_tool_ids:
            name = next((n for (t, _f), n in affected.items() if t == tid), "?")
            print(f"    [tool {tid}] {name}")

        print(f"\n{'=' * 70}\nDone. No writes were made — this script only read library.db "
              f"and {args.log_file!r}.\n{'=' * 70}")
        return 0
    finally:
        lib.close()


if __name__ == "__main__":
    sys.exit(main())
