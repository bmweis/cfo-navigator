#!/usr/bin/env python3
"""One-off, read-only diagnostic for the Community profile citation fix
(2026-08, see CLAUDE.md — the second `generate_*()` rewritten this way,
after Description/Agent taxonomy). Sibling to
`scripts/diagnose_agent_taxonomy_citations.py`, not an extension of it —
`generate_community_profile` has a materially different shape (single-page
grounding like Description, but 23 fields + one shared 12-key confidence
object parsed via `_parse_labeled_blocks`/`terminal_key="confidence"`
instead of Agent taxonomy's `_split_trailing_sentinels`-only two-field
shape), so a shared script would have needed near-total branching anyway.

Makes the raw Anthropic call itself (rather than going through
`generate_community_profile`) so the RAW response can be logged before
`extract_citations`/`_parse_labeled_blocks` ever touch it — every content
block's type/.text/.citations, plus resp.stop_reason — AND the FINAL
PARSED result (all 23 fields + the 12-key confidence dict + citations) via
the exact same `extract_citations` + `_parse_labeled_blocks` +
`_split_trailing_sentinels` + `_parse_community_confidence` calls the real
function makes, plus an explicit pass/fail check for the literal
pseudo-citation-tag text (and other legacy-JSON-shape tells) the whole
investigation started from.

Run against a couple of real, previously-profiled communities — pass
--community NAME (repeatable), or use the defaults below.

------------------------------------------------------------------------
THIS SCRIPT MAKES NO WRITE CALLS OF ANY KIND.
It never calls Library.upsert_community_profile, Library.set_entity_citations,
Library.clear_entity_citations, Library.mark_community_profile_reviewed, or
any other Library method that mutates a row. It opens the DB read-only
(only Library.list_communities / a plain SELECT, both reads) to pull each
community's name/url for the prompt, and otherwise only calls the
Anthropic API directly and prints what comes back. Nothing in this script
writes to library.db, entity_citations, community_profiles, or any other
table. Output goes to stdout only.
------------------------------------------------------------------------

Usage (from the production container, e.g. `railway ssh`):
    python -m scripts.diagnose_community_profile_citations
    python -m scripts.diagnose_community_profile_citations --community Chief --community "Pavilion"
"""
from __future__ import annotations

import argparse
import sys

from linklib.db import Library, resolve_db_path

DEFAULT_COMMUNITIES = ["Chief", "Pavilion"]


def _find_community(lib: Library, name: str) -> dict | None:
    """Read-only lookup by name (case-insensitive substring match on the
    approved communities list) — no write of any kind."""
    communities = lib.list_communities(approved_only=True)
    for c in communities:
        if c["name"].strip().lower() == name.strip().lower():
            return c
    for c in communities:
        if name.strip().lower() in c["name"].strip().lower():
            return c
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
            # eating the max_tokens budget could truncate a 23-field draft.
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


_FORBIDDEN_SUBSTRINGS = ["cite index=", "<cite", '"confidence"', '"ideal_member"', "{\"ideal_member\"", "```"]


def _dump_parsed(enrich_mod, resp, sent_docs) -> None:
    """The final parsed result, via the SAME calls generate_community_profile
    itself makes (extract_citations, then _parse_labeled_blocks with
    terminal_key="confidence", then _split_trailing_sentinels scoped to
    just the confidence block, then _parse_community_confidence) — not a
    reimplementation, so this can't silently drift from what the real
    function actually produces. Prints an explicit PASS/FAIL for the
    literal pseudo-citation-tag text (and a few other legacy-JSON-shape
    tells) the investigation exists to close out, plus every field so a
    reviewer can eyeball the whole draft, not just confidence."""
    raw, citations = enrich_mod.extract_citations(resp.content, sent_docs, inject_markers=True)
    raw = raw.strip().removeprefix("```").removesuffix("```").strip()
    blocks = enrich_mod._parse_labeled_blocks(
        raw, enrich_mod.COMMUNITY_PROFILE_FIELDS + ["confidence"], terminal_key="confidence")

    print("\n  --- PARSED (what generate_community_profile would actually store) ---")
    if not blocks:
        print("  NO RECOGNIZED FIELD HEADERS AT ALL — generate_community_profile would return None here.")
        print(f"\n  REGRESSION CHECK: FAIL — no labeled blocks parsed from a "
              f"{len(raw)}-char response (raw text logged above)")
        return

    _, confidence_pairs = enrich_mod._split_trailing_sentinels(
        blocks.get("confidence", ""), enrich_mod.COMMUNITY_CONFIDENCE_FIELDS)
    confidence = enrich_mod._parse_community_confidence(confidence_pairs)

    print(f"  fields parsed: {sorted(blocks)}")
    print(f"  citations: {len(citations)}")
    for c in citations:
        print(f"    [{c['n']}] {c['title']!r} — {c['url']}")
    print("  confidence (12 tracked fields):")
    for f in enrich_mod.COMMUNITY_CONFIDENCE_FIELDS:
        print(f"    {f}: {confidence.get(f)}")
    print("\n  fields (all 23):")
    for f in enrich_mod.COMMUNITY_PROFILE_FIELDS:
        value = blocks.get(f, "").strip()
        print(f"    {f}: {value!r}")

    # Check every parsed field's text, not just one flattened blob — a tag
    # buried in field #14 shouldn't be missed because only field #1 was checked.
    all_text = "\n".join(blocks.get(f, "") for f in enrich_mod.COMMUNITY_PROFILE_FIELDS)
    hits = [s for s in _FORBIDDEN_SUBSTRINGS if s in all_text]
    if hits:
        print(f"\n  REGRESSION CHECK: FAIL — found {hits!r} in the parsed fields")
    else:
        print(f"\n  REGRESSION CHECK: PASS — none of {_FORBIDDEN_SUBSTRINGS!r} found in any parsed field")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=None, help="Path to library.db (defaults via LINKLIB_DB)")
    ap.add_argument("--community", action="append", dest="communities", default=None,
                     help="Community name to diagnose (repeatable). Default: Chief, Pavilion.")
    ap.add_argument("--model", default=None,
                     help="Override the model (default: the DB's current enrich model, read-only)")
    args = ap.parse_args()
    community_names = args.communities or DEFAULT_COMMUNITIES

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
    from linklib import extract
    from linklib.citations import make_document_block
    from linklib.voice_settings import VoicePromptMissing, require_voice_setting

    db_path = resolve_db_path(args.db, allow_missing=False)
    print(f"DB (read-only lookups): {db_path}")
    lib = Library(db_path)
    try:
        model = args.model or lib.get_enrich_model()  # read-only
        try:
            voice_core = require_voice_setting(lib, "voice_core")  # read-only
        except VoicePromptMissing as e:
            print(f"ERROR: {e}")
            return 1
        print(f"Model: {model}\n")

        client = Anthropic()

        for name in community_names:
            community = _find_community(lib, name)
            if community is None:
                print(f"\n{'=' * 70}\n{name}: NOT FOUND among approved communities — skipping\n{'=' * 70}")
                continue

            # --- exact replica of generate_community_profile's fetch +
            # prompt-building steps (linklib/enrich.py) — read-only, no
            # writes; this only fetches the community's own public page,
            # same as the real generator does. ---
            page = extract.fetch_page(community["url"])
            low_confidence = not bool(page.content.strip())
            doc_blocks: list[dict] = []
            sent_docs: list[dict] = []
            if low_confidence:
                content_block = ("(Could not fetch page content — draft from your own knowledge of this "
                                  "community if you have it, keeping to the rules above.)")
            else:
                content_block = ""
                body = page.content.strip()[:15000]
                doc_blocks.append(make_document_block(community["name"], body))
                sent_docs.append({"title": community["name"], "url": community["url"], "type": "community_page"})

            prompt = enrich_mod._COMMUNITY_PROFILE_PROMPT.format(
                name=community["name"], url=community["url"], existing_block="", content_block=content_block,
                voice_core=voice_core)
            message_content = (doc_blocks + [{"type": "text", "text": prompt}]) if doc_blocks else prompt

            print(f"\nFetched page for {community['name']} (low_confidence={low_confidence}); "
                  f"{len(doc_blocks)} document block(s) sent.")

            # --- the ONLY API call this script makes — a plain read, no
            # write anywhere in this loop. ---
            resp = client.messages.create(
                model=model,
                max_tokens=enrich_mod._checked_max_tokens(6000),  # same budget generate_community_profile uses
                messages=[{"role": "user", "content": message_content}],
            )
            _dump_response(community["name"], resp)
            _dump_parsed(enrich_mod, resp, sent_docs)

        print(f"\n{'=' * 70}\nDone. No writes were made — nothing in this run touched library.db, "
              f"entity_citations, community_profiles, or any log file.\n{'=' * 70}")
        return 0
    finally:
        lib.close()


if __name__ == "__main__":
    sys.exit(main())
