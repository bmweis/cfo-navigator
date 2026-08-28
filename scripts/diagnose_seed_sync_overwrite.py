#!/usr/bin/env python3
"""One-off, READ-ONLY diagnostic for a real production incident (2026-08):
Abacum's live Description/Agent taxonomy still showed old, pre-incident
content after a full 157-tool regen run reported clean. Two independent
questions, both answered here:

1. Does `_seed_toolbox()` (webapp/app.py, runs on EVERY process startup —
   every deploy, restart, or crash recovery) silently revert an
   AI-regenerated `tools.description` back to `scripts/seed_tools.py`'s
   hardcoded blurb, for every tool present in that seed list? Confirmed by
   direct code read before writing this script: `_seed_toolbox()` does

       if row["name"] != t["name"] or row["description"] != t["description"]:
           lib.update_tool_content(row["id"], t["name"], t["description"])

   unconditionally, every boot, for every seed-listed tool whose live
   description differs from the seed value — which is exactly what a
   successful AI regeneration produces (a much longer, grounded
   description vs. the seed list's short 1-2 sentence blurb). This
   catalog-wide blast radius was never scoped to Abacum alone — this
   script reports the full cross-reference against every one of the 148
   seed-listed tools, not just the one that was noticed.

   `update_tool_content` (linklib/db.py) touches ONLY `name`/`description`
   — it never touches `agent_taxonomy_note`, `competitive_differentiation`,
   citations, or any `*_needs_verification`/`*_ai_confident` flag. So this
   mechanism explains a reverted Description specifically; a stale Agent
   taxonomy/Differentiation is NOT caused by this path (see item 2 below).

2. Was a given tool's regen actually reattempted in this morning's
   supposedly clean run, or did `_load_done()`'s resume logic silently
   skip it because an OLDER log file (from a run predating the citation-
   tag/max_tokens/em-dash fixes) already logged a "success" for that
   (entity_type, entity_id, field) tuple? `_load_done()` treats ANY
   "success" row in the log file it's pointed at as done, regardless of
   which code version produced it or how long ago. If tonight's run
   reused an old log path instead of a fresh one, a tool that succeeded
   under broken pre-fix code would never be reattempted, and its DB
   content would remain whatever the broken run wrote (or, per item 1,
   subsequently got reverted to the seed blurb).

------------------------------------------------------------------------
THIS SCRIPT MAKES NO WRITE CALLS OF ANY KIND. It only reads library.db
(via a handful of plain SELECTs / Library.list_tools) and scans JSONL log
files for matching lines. Nothing is modified anywhere.
------------------------------------------------------------------------

Usage (from the production container, e.g. `railway ssh`):
    python -m scripts.diagnose_seed_sync_overwrite
    python -m scripts.diagnose_seed_sync_overwrite --tool Abacum
    python -m scripts.diagnose_seed_sync_overwrite --log-dir /data
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from linklib.db import Library, normalize_url, resolve_db_path
from scripts.seed_tools import TOOLS as SEED_TOOLS


def _scan_logs_for_entity(log_dir: str, entity_type: str, entity_id: int) -> list[dict]:
    """Every JSONL line across every *.jsonl file under log_dir (and the
    current working directory, in case a log was written with a bare
    relative --log-file) that mentions this exact (entity_type, entity_id)
    — regardless of filename, since Brian's own report named a log file
    (`/data/regen_differentiation_log.jsonl`) that isn't this script's own
    default (`regen_ai_drafted_fields_log.jsonl`), meaning at least one
    run used a custom --log-file path. Read-only: opens each file, never
    writes."""
    hits: list[dict] = []
    search_dirs = {log_dir, os.getcwd()}
    seen_files: set[str] = set()
    for d in search_dirs:
        for path in glob.glob(os.path.join(d, "*.jsonl")):
            path = os.path.abspath(path)
            if path in seen_files:
                continue
            seen_files.add(path)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for line_no, line in enumerate(f, 1):
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            row = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if row.get("entity_type") == entity_type and row.get("entity_id") == entity_id:
                            row["_source_file"] = path
                            row["_line_no"] = line_no
                            hits.append(row)
            except OSError:
                continue
    hits.sort(key=lambda r: r.get("timestamp", ""))
    return hits


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=None, help="Path to library.db (defaults via LINKLIB_DB)")
    ap.add_argument("--log-dir", default="/data",
                     help="Directory to scan for *.jsonl regen logs (default: /data — the Railway "
                          "volume convention). Also always scans the current working directory, "
                          "in case a log was written with a bare relative --log-file.")
    ap.add_argument("--tool", action="append", dest="tools", default=None,
                     help="Restrict item 2's per-tool log scan to these tool names (repeatable). "
                          "Default: Abacum only (the tool this incident was noticed on). "
                          "Item 1's full seed-vs-live comparison always covers all 148 seed tools "
                          "regardless of this flag.")
    args = ap.parse_args()
    tool_names = args.tools or ["Abacum"]

    db_path = resolve_db_path(args.db, allow_missing=False)
    print(f"DB (read-only): {db_path}")
    print(f"Log scan dirs: {args.log_dir!r}, {os.getcwd()!r}\n")

    lib = Library(db_path)
    try:
        # --- Item 1: seed-vs-live comparison across all 148 seed tools ---
        print("=" * 70)
        print(f"ITEM 1 — seed_tools.py vs. live description, all {len(SEED_TOOLS)} seed-listed tools")
        print("=" * 70)
        existing_by_url = {
            normalize_url(r["url"]): r for r in lib.conn.execute(
                "SELECT id, name, description, updated_at, url FROM tools").fetchall()
        }
        matches_seed: list[tuple[int, str, str]] = []
        no_row: list[str] = []
        differs: list[tuple[int, str]] = []
        for t in SEED_TOOLS:
            row = existing_by_url.get(normalize_url(t["url"]))
            if not row:
                no_row.append(t["name"])
                continue
            live_desc = (row["description"] or "").strip()
            seed_desc = t["description"].strip()
            if live_desc == seed_desc:
                matches_seed.append((row["id"], row["name"], row["updated_at"]))
            else:
                differs.append((row["id"], row["name"]))

        print(f"\n{len(matches_seed)} tool(s) whose LIVE description currently matches the seed-list "
              f"value EXACTLY — each of these is either (a) never regenerated at all, so it was "
              f"always at the seed value, or (b) regenerated and then silently reverted by "
              f"_seed_toolbox() on a later deploy. Cross-reference each against item 2's log scan "
              f"(or your own memory of which tools you specifically regenerated) to tell the two "
              f"apart:\n")
        for tool_id, name, updated_at in matches_seed:
            print(f"  [{tool_id}] {name}  (tools.updated_at={updated_at})")
        if not matches_seed:
            print("  (none — every seed-listed tool's live description differs from the seed value)")

        print(f"\n{len(differs)} tool(s) whose live description differs from the seed value (i.e. "
              f"currently carries real, non-reverted content) — {len(no_row)} seed entr{'y' if len(no_row)==1 else 'ies'} "
              f"with no matching live row (deleted, or DB doesn't have it).")

        # --- Item 2: per-tool log history for the named tool(s) ---
        print("\n" + "=" * 70)
        print("ITEM 2 — regen log history for the requested tool(s), across every field")
        print("=" * 70)
        for name in tool_names:
            tools = lib.list_tools(approved_only=False)
            match = next((t for t in tools if t["name"].strip().lower() == name.strip().lower()), None)
            if match is None:
                match = next((t for t in tools if name.strip().lower() in t["name"].strip().lower()), None)
            if match is None:
                print(f"\n{name}: NOT FOUND in tools table — skipping")
                continue
            tool_id = match["id"]
            print(f"\n{match['name']} (tool_id={tool_id}, url={match['url']})")
            print(f"  live description ({len((match.get('description') or ''))} chars): "
                  f"{(match.get('description') or '')[:120]!r}...")
            print(f"  live agent_taxonomy_note ({len((match.get('agent_taxonomy_note') or ''))} chars): "
                  f"{(match.get('agent_taxonomy_note') or '')[:120]!r}...")
            print(f"  tools.updated_at: {match.get('updated_at')}")

            hits = _scan_logs_for_entity(args.log_dir, "tool", tool_id)
            if not hits:
                print("  No log entry found for this tool_id in ANY *.jsonl file scanned — "
                      "this tool was NEVER attempted by any regen run this script could find "
                      "(or the log lives somewhere this scan didn't check — pass --log-dir).")
                continue
            print(f"  {len(hits)} log entr{'y' if len(hits) == 1 else 'ies'} found across all scanned files:")
            for h in hits:
                extra = ""
                if h.get("status") == "failure":
                    extra = f"  detail={h.get('detail', '')!r}"
                print(f"    {h.get('timestamp', '?')}  field={h.get('field', '?'):28s}  "
                      f"status={h.get('status', '?'):8s}  file={h.get('_source_file')}:{h.get('_line_no')}{extra}")
            print("  --> If a \"success\" row above for field=\"description\" (or \"agent_taxonomy\"/"
                  "\"competitive_differentiation\") predates the fix commits, --log-file pointed at "
                  "this SAME file in a later run would have made _load_done() skip re-attempting it, "
                  "even though the earlier run wrote broken/polluted content.")

        print(f"\n{'=' * 70}\nDone. Read-only — nothing was written to library.db or any log file.\n{'=' * 70}")
        return 0
    finally:
        lib.close()


if __name__ == "__main__":
    sys.exit(main())
