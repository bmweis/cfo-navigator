#!/usr/bin/env python3
"""Read-only content-quality report for the citation-tag investigation's
blast-radius question (2026-08, see CLAUDE.md/ARCHITECTURE.md's citation-tag
bullets): which tools and communities currently carry legacy-shape damage
(pseudo-citation tags, editor-facing asides, or a spaced em dash/markdown
bold left over from the old strict-JSON prompt).

Log-independent by default (2026-08 follow-up). The original version of
this script only ever scanned entities named in a `--log-file` written by
`scripts/regen_ai_drafted_fields.py` — that log lived at a path on the
production container's local filesystem, and the very first real-world run
of the em-dash cleanup lost its own log to a redeploy (a fresh container on
merge, the file never made it to the persistent volume), so the "what did
we even touch" question had to be re-derived by hand via the cleanup
script's own write-then-read-back output instead of this report. Rather
than fix that one path, this script now scans the FULL current catalog
directly by default — every `tools` row and every `community_profiles`
row — since pollution/legacy-shape are properties of what's actually
stored right now, not of any particular regen run; a log was never
strictly necessary to answer that question, only convenient for narrowing
scope. `--log-file` still works exactly as before when passed (useful
mid-session, before a redeploy can lose it, or to see which specific
regen attempts failed outright) — passing it switches the report back to
log-scoped mode for that run.

Prints four sections in DB-scan mode (the new default), five when
`--log-file` is passed (log-scoped mode retains the FAILURES section,
which needs attempt-status data no full-catalog scan can reconstruct):
  1. (log-scoped mode only) Per-field failure list straight from the log —
     status="failure" rows, with their logged `detail`.
  2. Cite-tag pollution — for every checked entity/field, checks the
     CURRENT stored text against the forbidden-substring list the matching
     `diagnose_*_citations.py` script uses (tools and communities use
     different lists — different JSON key names leaked in the pre-fix
     incident on each side).
  3. Legacy-shape rows — the same current text checked for a spaced em
     dash (" — ", banned by the voice guide's em-dash policy regardless of
     citations) and markdown bold ("**", banned by the D1 prompt rules).
  4. Deduplicated summary — tools and communities reported separately
     (different tables, different id spaces — a tool id and a community id
     can collide numerically, so they are never merged into one set): the
     union of distinct entity IDs across the checks above, broken out per
     field/column, since raw per-section counts double-count an entity hit
     by more than one check.
  5. (communities only) A per-column pollution/legacy-shape breakdown
     across every checked community, so a "which of the 23 fields is
     actually the problem" question doesn't require re-deriving it from
     section 2/3's flat list by hand.

------------------------------------------------------------------------
THIS SCRIPT MAKES NO WRITE CALLS OF ANY KIND.
It only calls Library.get_tool / Library.get_community_profile / read-only
SELECTs against tools and community_profiles, and (in log-scoped mode)
reads the JSONL log file(s) from disk. Nothing in this script writes to
library.db, entity_citations, the JSONL log, or any other file. Output
goes to stdout only.
------------------------------------------------------------------------

Usage (from the production container, e.g. `railway ssh`):
    # Default: scan the full current catalog directly, no log needed.
    python -m scripts.report_regen_blast_radius

    # Log-scoped: only entities named in one or more regen-run log files
    # (repeatable --log-file; multiple files are deduplicated to each
    # entity's LAST logged status per (entity_type, entity_id, field), so a
    # community regenerated twice across two dropped-SSH-session files is
    # judged on its most recent attempt).
    python -m scripts.report_regen_blast_radius \
        --log-file /data/regen_log_2026-08-26.jsonl
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

# Field name -> the tools.* column(s) that field actually writes.
# "description" covers both description and summary in one call (see
# _regen_tool_description/generate_tool_description).
_TOOL_FIELD_TO_COLUMNS = {
    "description": ["description", "summary"],
    "agent_taxonomy": ["agent_taxonomy_note"],
    "competitive_differentiation": ["competitive_differentiation"],
}

# The community regen script (and generate_community_profile itself) treats
# the whole profile draft as one unit — all 23 columns drafted in a single
# call sharing one citation set — so it's checked as one logical field,
# "community_profile", covering every prose-capable community_profiles
# column. founded_year/low_confidence/needs_review are excluded (not prose
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


def _all_tools_as_rows(lib: Library) -> list[dict]:
    """DB-scan mode: a synthetic 'success' row per (tool, field) for EVERY
    tool in the catalog (approved or not — a pending tool's drafted content
    matters before approval too), for every field this script knows how to
    check. Only entity_type/entity_id/field/status/name are used downstream
    (the real column text is re-read fresh from the DB by _analyze, not
    taken from this synthetic row), so no other data needs to be faked."""
    rows: list[dict] = []
    for tool in lib.list_tools(approved_only=False):
        for field in _TOOL_FIELD_TO_COLUMNS:
            rows.append({
                "entity_type": "tool", "entity_id": tool["id"], "field": field,
                "status": "success", "name": tool.get("name", "?"),
            })
    return rows


def _all_community_profiles_as_rows(lib: Library) -> list[dict]:
    """DB-scan mode equivalent for communities: one synthetic row per
    community that actually HAS a profile row (no point checking profile
    columns on a community with no profile yet) — a direct read-only join,
    same as the rest of this script's read-only DB access."""
    rows: list[dict] = []
    for community_id, name in lib.conn.execute(
        "SELECT cp.community_id, c.name FROM community_profiles cp "
        "JOIN communities c ON c.id = cp.community_id ORDER BY cp.community_id"
    ).fetchall():
        rows.append({
            "entity_type": "community", "entity_id": community_id,
            "field": "community_profile", "status": "success", "name": name,
        })
    return rows


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


def _print_sections(entity_type: str, analysis: dict, *, db_scan_mode: bool) -> None:
    failures = analysis["failures"]
    pollution_hits = analysis["pollution_hits"]
    legacy_shape_hits = analysis["legacy_shape_hits"]

    if db_scan_mode:
        print(f"\n{'=' * 70}\n1{entity_type[0].upper()}. {entity_type.upper()} FAILURES — "
              f"not tracked in DB-scan mode (no log file supplied); pass --log-file for "
              f"per-attempt failure detail\n{'=' * 70}")
    else:
        print(f"\n{'=' * 70}\n1{entity_type[0].upper()}. {entity_type.upper()} FAILURES ({len(failures)}) "
              f"— never overwritten, still on old content\n{'=' * 70}")
        if not failures:
            print("  (none)")
        for r in failures:
            print(f"  [{entity_type} {r['entity_id']}] {r.get('name', '?')} — field={r.get('field')!r}")
            print(f"      detail: {r.get('detail', '')!r}")

    print(f"\n{'=' * 70}\n2{entity_type[0].upper()}. {entity_type.upper()} CITE-TAG POLLUTION "
          f"({len(pollution_hits)}) — currently-stored content still carrying a forbidden "
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


def _print_dedup_summary(entity_type: str, analysis: dict, *, db_scan_mode: bool) -> list[int]:
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
    failures_note = " (failures not tracked in DB-scan mode)" if db_scan_mode else ""
    print(f"  Distinct ({entity_type}, field) pairs: {len(affected)}  "
          f"(raw section counts were {len(failures)}+{len(pollution_hits)}+{len(legacy_shape_hits)} "
          f"= {raw_total}{failures_note}, which double-counts an entity hit by more than one check "
          f"on the same field, and doesn't collapse per-entity at all)")
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
    ap.add_argument("--log-file", action="append", default=None, dest="log_files",
                     help="Path to a JSONL log written by scripts/regen_ai_drafted_fields.py. "
                          "Repeatable — pass once per session's log file if a run spanned "
                          "multiple dropped SSH sessions. Omit entirely (the default) to scan "
                          "the full current catalog directly instead — no log needed.")
    args = ap.parse_args()

    db_scan_mode = not args.log_files
    db_path = resolve_db_path(args.db, allow_missing=False)
    print(f"DB: {db_path}")
    lib = Library(db_path)
    try:
        if db_scan_mode:
            print("Mode: DB-scan (no --log-file supplied) — scanning the full current catalog directly.")
            tool_rows = _all_tools_as_rows(lib)
            community_rows = _all_community_profiles_as_rows(lib)
        else:
            print(f"Mode: log-scoped — log file(s): {args.log_files}")
            raw_rows = _load_log(args.log_files)
            rows = _dedupe_last_status(raw_rows)
            tool_rows = [r for r in rows if r.get("entity_type") == "tool"]
            community_rows = [r for r in rows if r.get("entity_type") == "community"]
            print(f"Total lines (raw, all files): {len(raw_rows)}")
            print(f"After de-duplicating to the last status per (entity_type, entity_id, field): "
                  f"{len(rows)}  (tool: {len(tool_rows)}, community: {len(community_rows)})")

        touched_tool_ids = sorted({r["entity_id"] for r in tool_rows})
        touched_community_ids = sorted({r["entity_id"] for r in community_rows})
        print(f"Tool IDs checked: {len(touched_tool_ids)}")
        print(f"Community IDs checked: {len(touched_community_ids)}")

        tool_analysis = _analyze(
            tool_rows, entity_type="tool", lib=lib,
            field_to_columns=_TOOL_FIELD_TO_COLUMNS,
            forbidden_substrings=_TOOL_FORBIDDEN_SUBSTRINGS,
            get_entity=lambda lib_, tid: lib_.get_tool(tid),
            get_name=lambda lib_, tid, entity: entity.get("name", "?"),
        )
        _print_sections("tool", tool_analysis, db_scan_mode=db_scan_mode)

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
        _print_sections("community", community_analysis, db_scan_mode=db_scan_mode)

        print(f"\n{'=' * 70}\n4. DEDUPLICATED SUMMARY — real \"needs regeneration\" picture, "
              f"tools and communities reported separately (different tables/id spaces)\n{'=' * 70}")
        tool_distinct_ids = _print_dedup_summary("tool", tool_analysis, db_scan_mode=db_scan_mode)
        community_distinct_ids = _print_dedup_summary("community", community_analysis, db_scan_mode=db_scan_mode)

        # -- Section 5: per-column breakdown across checked communities —
        # since a single "community_profile" field can hide which of the 23
        # underlying columns is actually the problem.
        print(f"\n{'=' * 70}\n5. COMMUNITY PER-COLUMN BREAKDOWN — which of the 23 profile "
              f"columns actually shows pollution/legacy-shape, across every checked "
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

        source_desc = "the full current catalog" if db_scan_mode else f"{args.log_files!r}"
        print(f"\n{'=' * 70}\nDone. No writes were made — this script only read library.db "
              f"({source_desc}).\n{'=' * 70}")
        print(f"\nOVERALL: {len(tool_distinct_ids)} tool(s) and {len(community_distinct_ids)} "
              f"community(ies) still need regeneration or a fix, per the sections above.")
        return 0
    finally:
        lib.close()


if __name__ == "__main__":
    sys.exit(main())
