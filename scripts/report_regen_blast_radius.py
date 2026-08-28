#!/usr/bin/env python3
"""One-off, read-only report for the citation-tag investigation's blast-
radius question (2026-08, see CLAUDE.md/ARCHITECTURE.md's citation-tag
bullets): of the tools AND communities the `scripts/regen_ai_drafted_fields.py`
run(s) actually touched, which ones currently carry legacy-shape damage
(pseudo-citation tags, editor-facing asides, or a spaced em dash/markdown
bold left over from the old strict-JSON prompt) versus which ones failed
outright and were never overwritten at all.

Cross-references two sources, not just a blind ID range: `--log-file`
(the JSONL log the regen script itself wrote, one line per attempted
(entity_type, entity_id, field) tuple — see `scripts/
regen_ai_drafted_fields.py`'s own `_log()`) names exactly which tool/
community IDs and fields were actually attempted; `--db` is queried only
for those IDs, never a blind `id <= N` sweep, since a run's ID range isn't
guaranteed contiguous.

Community support (2026-08 follow-up): the original version of this script
excluded communities outright, since the throwaway run that motivated it
never touched them. That's no longer true — Brian's full 157-tool +
40-community `--apply` run logged real community rows (98 log lines / 40
distinct communities, some regenerated more than once across dropped SSH
sessions), so community rows are now analyzed with the same three checks
as tools: failures, cite-tag pollution, and legacy-shape (spaced em dash /
markdown bold). The regen script logs a community's entire drafted profile
under one field name, `"community_profile"` (all 23 columns drafted in a
single Claude call — see `generate_community_profile`'s "one shared
citation set" design), so community pollution/legacy-shape checks read
every prose-capable `community_profiles` column for a successfully-logged
community and report which COLUMN(S) within that one field are affected,
not just that the field as a whole is dirty. The forbidden-substring list
for communities is deliberately a different list than the tools' own
(different JSON key names leaked in the pre-fix incident — see
`scripts/diagnose_community_profile_citations.py`, which this script's
list is kept identical to) rather than reused wholesale.

Prints five sections:
  1. Per-field failure list straight from the log — status="failure" rows
     for both tools and communities, with their logged `detail`, so a
     re-run can be scoped to exactly what's still missing.
  2. Cite-tag pollution — for every entity ID logged as a SUCCESS on a
     given field, checks that field's CURRENT stored text against the
     forbidden-substring list the matching `diagnose_*_citations.py`
     script uses (tools and communities use different lists — see above).
  3. Legacy-shape rows — the same current text checked for a spaced em
     dash (" — ", banned by the voice guide's em-dash policy regardless of
     citations) and markdown bold ("**", banned by the D1 prompt rules),
     both tells of the old prompt shape even when no literal pseudo-
     citation tag survived.
  4. Deduplicated summary — tools and communities reported separately
     (different tables, different id spaces — a tool id and a community id
     can collide numerically, so they are never merged into one set): the
     union of distinct entity IDs across sections 1-3 combined, broken out
     per field/column, since raw per-section counts double-count an entity
     hit by more than one check.
  5. (communities only) A per-column pollution/legacy-shape breakdown
     across all logged-successful communities, so a "which of the 23
     fields is actually the problem" question doesn't require re-deriving
     it from section 2/3's flat list by hand.

------------------------------------------------------------------------
THIS SCRIPT MAKES NO WRITE CALLS OF ANY KIND.
It only calls Library.get_tool / Library.get_community_profile (reads) for
each ID named in the log, and otherwise only reads the JSONL log file from
disk. Nothing in this script writes to library.db, entity_citations, the
JSONL log, or any other file. Output goes to stdout only.
------------------------------------------------------------------------

Usage (from the production container, e.g. `railway ssh`):
    python -m scripts.report_regen_blast_radius \
        --log-file /data/regen_log_2026-08-26.jsonl

Multiple log files from separate runs/sessions (e.g. the dropped-SSH-session
community reruns) can all be passed at once — repeat --log-file, or pass a
single file that's the concatenation of all of them; either way rows are
deduplicated per (entity_type, entity_id, field) by keeping only the LAST
logged status for that tuple, so a community regenerated twice across two
sessions is judged on its most recent attempt, not double-counted.
"""
from __future__ import annotations

import argparse
import json
import sys

from linklib.db import Library, resolve_db_path

# Same list scripts/diagnose_agent_taxonomy_citations.py already checks for
# tools — kept identical rather than reinvented, so a "clean" verdict here
# means the same thing it means there.
_TOOL_FORBIDDEN_SUBSTRINGS = [
    "cite index=", "<cite", '"confident"', '"summary"', '"description"',
    '"ideal_member"', "{\"description\"", "{\"ideal_member\"", "```",
]

# Same list scripts/diagnose_community_profile_citations.py already checks
# — a deliberately different list from the tools' own: the pre-fix Community
# profile prompt leaked different JSON key names ("confidence" as a block
# header, "ideal_member" as a top-level key) than Description/Agent
# taxonomy's own JSON shape did.
_COMMUNITY_FORBIDDEN_SUBSTRINGS = [
    "cite index=", "<cite", '"confidence"', '"ideal_member"', "{\"ideal_member\"", "```",
]

# Field name (as logged by regen_ai_drafted_fields.py) -> the tools.*
# column(s) that field actually wrote. "description" writes both
# description and summary in one call (see _regen_tool_description).
_TOOL_FIELD_TO_COLUMNS = {
    "description": ["description", "summary"],
    "agent_taxonomy": ["agent_taxonomy_note"],
    "competitive_differentiation": ["competitive_differentiation"],
}

# The community regen script logs the whole profile draft under one field
# name — "community_profile" — since generate_community_profile drafts all
# 23 columns in a single call sharing one citation set. Every prose-capable
# community_profiles column is checked when that one field is logged as a
# success; founded_year/low_confidence/needs_review are excluded (not prose
# — an em dash or a leaked JSON key can't appear in an int/bool column).
_COMMUNITY_PROSE_COLUMNS = [
    "ideal_member", "anti_fit", "value_prop", "format_reality",
    "engagement_level", "sponsor_relationship_note", "application_friction",
    "cost_value_verdict", "notable_members", "public_criticism",
    "verdict_summary", "business_model", "primary_purpose", "cpe_eligible",
    "platform_type", "meeting_format", "event_style", "seniority_band",
    "resources_included", "stage_focus", "jobs_program", "team_or_individual",
]
_COMMUNITY_FIELD_TO_COLUMNS = {"community_profile": _COMMUNITY_PROSE_COLUMNS}


def _load_log(log_files: list[str]) -> list[dict]:
    rows: list[dict] = []
    for log_file in log_files:
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


def _dedupe_last_status(rows: list[dict]) -> list[dict]:
    """Keeps only the LAST logged row per (entity_type, entity_id, field) —
    across one file or several concatenated ones — so an entity that was
    regenerated more than once (e.g. the community reruns across dropped
    SSH sessions) is judged on its most recent attempt, not counted once
    per attempt."""
    latest: dict[tuple[str, int, str], dict] = {}
    for r in rows:
        key = (r.get("entity_type"), r.get("entity_id"), r.get("field"))
        latest[key] = r  # last one wins — rows are appended in run order
    return list(latest.values())


def _analyze(rows: list[dict], *, entity_type: str, lib: Library,
             field_to_columns: dict[str, list[str]],
             forbidden_substrings: list[str],
             get_entity, get_name) -> dict:
    """Shared analysis for one entity type. `get_entity(lib, id) -> dict|None`
    reads the row that holds the checked columns (Library.get_tool for
    tools, Library.get_community_profile for communities); `get_name(lib,
    id, entity)` resolves a display name (tools carry their own `name`
    column; communities' name lives on the separate `communities` row, not
    `community_profiles`, so it needs its own lookup)."""
    failures = [r for r in rows if r.get("status") == "failure"]
    successes = [r for r in rows if r.get("status") == "success"]
    by_id_field: dict[tuple[int, str], dict] = {(r["entity_id"], r["field"]): r for r in successes}

    pollution_hits: list[tuple[int, str, str, str, str]] = []
    legacy_shape_hits: list[tuple[int, str, str, str, str]] = []
    entity_cache: dict[int, dict | None] = {}

    for (entity_id, field), _log_row in sorted(by_id_field.items()):
        if field not in field_to_columns:
            continue
        if entity_id not in entity_cache:
            entity_cache[entity_id] = get_entity(lib, entity_id)
        entity = entity_cache[entity_id]
        if entity is None:
            print(f"  [{entity_type} {entity_id}] logged success but no longer exists in the DB — skipped")
            continue
        name = get_name(lib, entity_id, entity)
        for col in field_to_columns[field]:
            text = entity.get(col) or ""
            if not isinstance(text, str) or not text:
                continue
            hits = [s for s in forbidden_substrings if s in text]
            if hits:
                pollution_hits.append((entity_id, name, f"{field} ({col})", field, ", ".join(hits)))
            if " — " in text:
                legacy_shape_hits.append((entity_id, name, f"{field} ({col})", field, "spaced em dash"))
            if "**" in text:
                legacy_shape_hits.append((entity_id, name, f"{field} ({col})", field, "markdown bold (**)"))

    return {
        "failures": failures,
        "pollution_hits": pollution_hits,
        "legacy_shape_hits": legacy_shape_hits,
    }


def _print_sections(entity_type: str, analysis: dict) -> None:
    failures = analysis["failures"]
    pollution_hits = analysis["pollution_hits"]
    legacy_shape_hits = analysis["legacy_shape_hits"]

    print(f"\n{'=' * 70}\n1{entity_type[0].upper()}. {entity_type.upper()} FAILURES ({len(failures)}) "
          f"— never overwritten, still on old content\n{'=' * 70}")
    if not failures:
        print("  (none)")
    for r in failures:
        print(f"  [{entity_type} {r['entity_id']}] {r.get('name', '?')} — field={r.get('field')!r}")
        print(f"      detail: {r.get('detail', '')!r}")

    print(f"\n{'=' * 70}\n2{entity_type[0].upper()}. {entity_type.upper()} CITE-TAG POLLUTION "
          f"({len(pollution_hits)}) — successfully-overwritten rows still carrying a forbidden "
          f"substring\n{'=' * 70}")
    if not pollution_hits:
        print("  (none)")
    for entity_id, name, label, _base_field, hits in pollution_hits:
        print(f"  [{entity_type} {entity_id}] {name} — {label}: {hits}")

    print(f"\n{'=' * 70}\n3{entity_type[0].upper()}. {entity_type.upper()} LEGACY-SHAPE ROWS "
          f"({len(legacy_shape_hits)}) — spaced em dash or markdown bold, no literal tag "
          f"required\n{'=' * 70}")
    if not legacy_shape_hits:
        print("  (none)")
    for entity_id, name, label, _base_field, kind in legacy_shape_hits:
        print(f"  [{entity_type} {entity_id}] {name} — {label}: {kind}")


def _print_dedup_summary(entity_type: str, analysis: dict) -> None:
    failures = analysis["failures"]
    pollution_hits = analysis["pollution_hits"]
    legacy_shape_hits = analysis["legacy_shape_hits"]

    affected: dict[tuple[int, str], str] = {}
    for r in failures:
        affected[(r["entity_id"], r.get("field", "?"))] = r.get("name", "?")
    for entity_id, name, _label, base_field, _detail in pollution_hits:
        affected[(entity_id, base_field)] = name
    for entity_id, name, _label, base_field, _detail in legacy_shape_hits:
        affected[(entity_id, base_field)] = name

    distinct_ids = sorted({tid for tid, _field in affected})
    by_field: dict[str, set[int]] = {}
    for (tid, field) in affected:
        by_field.setdefault(field, set()).add(tid)
    fields_per_entity: dict[int, set[str]] = {}
    for (tid, field) in affected:
        fields_per_entity.setdefault(tid, set()).add(field)
    multi_field = {tid: fields for tid, fields in fields_per_entity.items() if len(fields) > 1}

    plural = "communities" if entity_type == "community" else "tools"
    print(f"\n{'-' * 70}\n{entity_type.upper()} — deduplicated \"needs regeneration\" count\n{'-' * 70}")
    print(f"  Distinct {plural} needing regeneration (any field): {len(distinct_ids)}")
    raw_total = len(failures) + len(pollution_hits) + len(legacy_shape_hits)
    print(f"  Distinct ({entity_type}, field) pairs: {len(affected)}  "
          f"(raw section counts were {len(failures)}+{len(pollution_hits)}+{len(legacy_shape_hits)} "
          f"= {raw_total}, which double-counts an entity hit by more than one check on the same field, "
          f"and doesn't collapse per-entity at all)")
    print(f"\n  By field (distinct {plural}):")
    for field in sorted(by_field):
        print(f"    {field}: {len(by_field[field])}")
    print(f"\n  {plural.capitalize()} needing more than one field/column regenerated: {len(multi_field)}")
    for tid in sorted(multi_field):
        print(f"    [{entity_type} {tid}] {affected.get((tid, sorted(multi_field[tid])[0]), '?')} — "
              f"{sorted(multi_field[tid])}")
    print(f"\n  Full list ({entity_type}_id, name):")
    for tid in distinct_ids:
        name = next((n for (t, _f), n in affected.items() if t == tid), "?")
        print(f"    [{entity_type} {tid}] {name}")
    return distinct_ids


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=None, help="Path to library.db (defaults via LINKLIB_DB)")
    ap.add_argument("--log-file", action="append", required=True, dest="log_files",
                     help="Path to a JSONL log written by scripts/regen_ai_drafted_fields.py. "
                          "Repeatable — pass once per session's log file if a run spanned "
                          "multiple dropped SSH sessions.")
    args = ap.parse_args()

    raw_rows = _load_log(args.log_files)
    rows = _dedupe_last_status(raw_rows)
    tool_rows = [r for r in rows if r.get("entity_type") == "tool"]
    community_rows = [r for r in rows if r.get("entity_type") == "community"]
    print(f"Log file(s): {args.log_files}")
    print(f"Total lines (raw, all files): {len(raw_rows)}")
    print(f"After de-duplicating to the last status per (entity_type, entity_id, field): {len(rows)}  "
          f"(tool: {len(tool_rows)}, community: {len(community_rows)})")

    touched_tool_ids = sorted({r["entity_id"] for r in tool_rows})
    touched_community_ids = sorted({r["entity_id"] for r in community_rows})
    print(f"Distinct tool IDs touched: {len(touched_tool_ids)}")
    print(f"Distinct community IDs touched: {len(touched_community_ids)}")

    db_path = resolve_db_path(args.db, allow_missing=False)
    print(f"\nDB (read-only lookups): {db_path}")
    lib = Library(db_path)
    try:
        tool_analysis = _analyze(
            tool_rows, entity_type="tool", lib=lib,
            field_to_columns=_TOOL_FIELD_TO_COLUMNS,
            forbidden_substrings=_TOOL_FORBIDDEN_SUBSTRINGS,
            get_entity=lambda lib_, tid: lib_.get_tool(tid),
            get_name=lambda lib_, tid, entity: entity.get("name", "?"),
        )
        _print_sections("tool", tool_analysis)

        def _get_community_profile(lib_: Library, cid: int) -> dict | None:
            return lib_.get_community_profile(cid)

        def _get_community_name(lib_: Library, cid: int, _profile: dict) -> str:
            c = lib_.get_community(cid)
            return c.get("name", "?") if c else "?"

        community_analysis = _analyze(
            community_rows, entity_type="community", lib=lib,
            field_to_columns=_COMMUNITY_FIELD_TO_COLUMNS,
            forbidden_substrings=_COMMUNITY_FORBIDDEN_SUBSTRINGS,
            get_entity=_get_community_profile,
            get_name=_get_community_name,
        )
        _print_sections("community", community_analysis)

        print(f"\n{'=' * 70}\n4. DEDUPLICATED SUMMARY — real \"needs regeneration\" picture, "
              f"tools and communities reported separately (different tables/id spaces)\n{'=' * 70}")
        tool_distinct_ids = _print_dedup_summary("tool", tool_analysis)
        community_distinct_ids = _print_dedup_summary("community", community_analysis)

        # -- Section 5: per-column breakdown across logged-successful
        # communities — since a single "community_profile" field can hide
        # which of the 23 underlying columns is actually the problem.
        print(f"\n{'=' * 70}\n5. COMMUNITY PER-COLUMN BREAKDOWN — which of the 23 profile "
              f"columns actually shows pollution/legacy-shape, across every logged-successful "
              f"community\n{'=' * 70}")
        col_pollution: dict[str, int] = {}
        col_legacy: dict[str, int] = {}
        for _cid, _name, label, _field, _hits in community_analysis["pollution_hits"]:
            col = label.split("(", 1)[1].rstrip(")")
            col_pollution[col] = col_pollution.get(col, 0) + 1
        for _cid, _name, label, _field, _kind in community_analysis["legacy_shape_hits"]:
            col = label.split("(", 1)[1].rstrip(")")
            col_legacy[col] = col_legacy.get(col, 0) + 1
        if not col_pollution and not col_legacy:
            print("  (no per-column pollution or legacy-shape hits — nothing to break down)")
        else:
            all_cols = sorted(set(col_pollution) | set(col_legacy))
            for col in all_cols:
                print(f"    {col}: pollution={col_pollution.get(col, 0)}, legacy_shape={col_legacy.get(col, 0)}")

        print(f"\n{'=' * 70}\nDone. No writes were made — this script only read library.db and "
              f"{args.log_files!r}.\n{'=' * 70}")
        print(f"\nOVERALL: {len(tool_distinct_ids)} tool(s) and {len(community_distinct_ids)} "
              f"community(ies) still need regeneration or a fix, per the sections above.")
        return 0
    finally:
        lib.close()


if __name__ == "__main__":
    sys.exit(main())
