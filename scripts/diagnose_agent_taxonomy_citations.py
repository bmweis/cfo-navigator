#!/usr/bin/env python3
"""One-off, read-only diagnostic for the citation-tag investigation
(2026-08, see CLAUDE.md). Originally Phase 0's A3 exception (what does the
raw Anthropic response actually look like before linklib.citations.
extract_citations touches it); now doubles as the acceptance check for the
generation-path fix — since it imports enrich_mod._AGENT_TAXONOMY_PROMPT
and calls enrich_mod._fetch_taxonomy_grounding/_build_taxonomy_documents
directly rather than duplicating them, it automatically exercises whatever
prompt/parsing shape generate_tool_agent_taxonomy currently has, live,
without this script needing its own update every time that function
changes. Runs the exact fetch + prompt-building steps for two named tools,
but makes the raw Anthropic call itself (instead of going through
generate_tool_agent_taxonomy) so the RAW response can be logged before
extract_citations ever touches it — every content block's
type/.text/.citations, plus resp.stop_reason — AND, since the
citation-tag-investigation follow-up, the FINAL PARSED result
(note/confident/citations) via the same extract_citations +
_split_trailing_sentinels calls the real function makes, plus an explicit
pass/fail check for the literal pseudo-citation-tag text the whole
investigation started from.

Run against:
  - Concourse  (had a populated Sources list AND raw tags in the text)
  - GoClose or Expensify (had raw tags with an EMPTY Sources list)
pass --tool NAME (repeatable) to pick different ones, or use the defaults.

------------------------------------------------------------------------
THIS SCRIPT MAKES NO WRITE CALLS OF ANY KIND.
It never calls Library.update_tool, Library.set_tool_agent_taxonomy_draft,
Library.update_tool_differentiation, Library.upsert_community_profile,
Library.set_entity_citations, Library.clear_entity_citations, or any other
Library method that mutates a row. It opens the DB read-only (only
Library.get_tool_by_slug / a plain SELECT, both reads) to pull each tool's
name/url/description for the prompt, and otherwise only calls the Anthropic
API directly and prints what comes back. Nothing in this script writes to
library.db, entity_citations, or any other table. Nothing is logged back
into regen_ai_drafted_fields_log.jsonl or any other production artifact —
output goes to stdout only.
------------------------------------------------------------------------

Usage (from the production container, e.g. `railway ssh`):
    python -m scripts.diagnose_agent_taxonomy_citations
    python -m scripts.diagnose_agent_taxonomy_citations --tool Concourse --tool GoClose
"""
from __future__ import annotations

import argparse
import sys

from linklib.db import Library, resolve_db_path

DEFAULT_TOOLS = ["Concourse", "GoClose"]


def _find_tool(lib: Library, name: str) -> dict | None:
    """Read-only lookup by name (case-insensitive substring match on the
    approved tools list) — no write of any kind."""
    for t in lib.list_tools(approved_only=True):
        if t["name"].strip().lower() == name.strip().lower():
            return t
    for t in lib.list_tools(approved_only=True):
        if name.strip().lower() in t["name"].strip().lower():
            return t
    return None


def _dump_response(name: str, resp) -> None:
    print(f"\n{'=' * 70}\n{name}\n{'=' * 70}")
    print(f"stop_reason: {getattr(resp, 'stop_reason', None)!r}")
    usage = getattr(resp, "usage", None)
    if usage is not None:
        print(f"usage: input={getattr(usage, 'input_tokens', None)} "
              f"output={getattr(usage, 'output_tokens', None)}")
    print(f"content blocks: {len(resp.content)}")
    for i, block in enumerate(resp.content):
        btype = getattr(block, "type", None)
        print(f"\n  --- block {i} (type={btype!r}) ---")
        if btype == "thinking":
            # Don't dump full thinking text (can be long/irrelevant) — just
            # confirm it's present and how big, since a large thinking block
            # eating the max_tokens budget is exactly the G1 truncation
            # hypothesis this diagnostic exists to check.
            thinking_text = getattr(block, "thinking", "") or ""
            print(f"  thinking chars: {len(thinking_text)}")
            continue
        text = getattr(block, "text", None)
        if text is not None:
            print(f"  .text ({len(text)} chars):")
            print("  " + text.replace("\n", "\n  "))
        citations = getattr(block, "citations", None)
        if citations:
            print(f"  .citations ({len(citations)}):")
            for c in citations:
                doc_idx = getattr(c, "document_index", None)
                url = getattr(c, "url", None)
                print(f"    - document_index={doc_idx!r} url={url!r} "
                      f"cited_text={getattr(c, 'cited_text', None)!r}")
        else:
            print("  .citations: none")


_FORBIDDEN_SUBSTRINGS = ["cite index=", "<cite", '"confident"', '"summary"', "{\"description\"", "```"]


def _dump_parsed(enrich_mod, resp, sent_docs) -> None:
    """The final parsed result, via the SAME calls
    generate_tool_agent_taxonomy itself makes (extract_citations then
    _split_trailing_sentinels) — not a reimplementation, so this can't
    silently drift from what the real function actually produces. Prints
    an explicit PASS/FAIL for the literal pseudo-citation-tag text
    (and a few other legacy-JSON-shape tells) the investigation exists to
    close out."""
    raw, citations = enrich_mod.extract_citations(resp.content, sent_docs, inject_markers=True)
    raw = raw.strip().removeprefix("```").removesuffix("```").strip()
    note_text, sentinels = enrich_mod._split_trailing_sentinels(raw, ["CONFIDENT"])
    note_text = note_text.strip()
    confident = sentinels.get("CONFIDENT", "").strip().lower() == "true"

    print("\n  --- PARSED (what generate_tool_agent_taxonomy would actually store) ---")
    print(f"  confident: {confident} (raw sentinel value: {sentinels.get('CONFIDENT')!r})")
    print(f"  citations: {len(citations)}")
    for c in citations:
        print(f"    [{c['n']}] {c['title']!r} — {c['url']}")
    print(f"  agent_taxonomy_note ({len(note_text)} chars):")
    print("  " + note_text.replace("\n", "\n  "))

    hits = [s for s in _FORBIDDEN_SUBSTRINGS if s in note_text]
    if hits:
        print(f"\n  REGRESSION CHECK: FAIL — found {hits!r} in the parsed note")
    else:
        print(f"\n  REGRESSION CHECK: PASS — none of {_FORBIDDEN_SUBSTRINGS!r} found in the parsed note")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=None, help="Path to library.db (defaults via LINKLIB_DB)")
    ap.add_argument("--tool", action="append", dest="tools", default=None,
                     help="Tool name to diagnose (repeatable). Default: Concourse, GoClose.")
    ap.add_argument("--model", default=None,
                     help="Override the model (default: the DB's current enrich model, read-only)")
    args = ap.parse_args()
    tool_names = args.tools or DEFAULT_TOOLS

    try:
        from anthropic import Anthropic
    except ImportError:
        print("ERROR: the `anthropic` package isn't installed here. `pip install anthropic` and re-run.")
        return 1
    import os
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY is not set in this environment.")
        return 1

    from linklib import enrich as enrich_mod
    from linklib.agent import VOICE_CORE_DEFAULT

    db_path = resolve_db_path(args.db, allow_missing=False)
    print(f"DB (read-only lookups): {db_path}")
    lib = Library(db_path)
    try:
        model = args.model or lib.get_enrich_model()  # read-only
        voice_core = lib.get_setting("voice_core") or VOICE_CORE_DEFAULT  # read-only
        print(f"Model: {model}\n")

        client = Anthropic()

        for name in tool_names:
            tool = _find_tool(lib, name)
            if tool is None:
                print(f"\n{'=' * 70}\n{name}: NOT FOUND among approved tools — skipping\n{'=' * 70}")
                continue

            # --- exact replica of generate_tool_agent_taxonomy's fetch +
            # prompt-building steps (linklib/enrich.py) — read-only, no
            # writes; this only fetches the vendor's own public web pages,
            # same as the real generator does. ---
            _, fetched = enrich_mod._fetch_taxonomy_grounding(tool["url"])
            low_confidence = not fetched
            doc_blocks, sent_docs = (
                enrich_mod._build_taxonomy_documents(fetched) if fetched else ([], [])
            )
            content_note = "" if doc_blocks else (
                f"(Could not fetch any page content for {tool['url']} — draft from your own "
                f"knowledge of {tool['name']} if you have it, keeping to the rules above.)"
            )
            prompt = enrich_mod._AGENT_TAXONOMY_PROMPT.format(
                name=tool["name"], url=tool["url"],
                description=(tool.get("description") or "").strip() or "(none provided)",
                content_block=content_note,
                voice_core=enrich_mod._resolve_voice_core(voice_core),
                structure_guidance=enrich_mod._STRUCTURE_GUIDANCE,
            )
            message_content = (doc_blocks + [{"type": "text", "text": prompt}]) if doc_blocks else prompt

            print(f"\nFetched {len(fetched)} page(s) for {tool['name']} "
                  f"(low_confidence={low_confidence}); {len(doc_blocks)} document block(s) sent.")

            # --- the ONLY API call this script makes — a plain read, no
            # write anywhere in this loop. ---
            resp = client.messages.create(
                model=model,
                max_tokens=enrich_mod._checked_max_tokens(2000),  # same budget generate_tool_agent_taxonomy uses
                messages=[{"role": "user", "content": message_content}],
            )
            _dump_response(tool["name"], resp)
            _dump_parsed(enrich_mod, resp, sent_docs)

        print(f"\n{'=' * 70}\nDone. No writes were made — nothing in this run touched library.db, "
              f"entity_citations, or any log file.\n{'=' * 70}")
        return 0
    finally:
        lib.close()


if __name__ == "__main__":
    sys.exit(main())
