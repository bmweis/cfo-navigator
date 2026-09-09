"""Shared Citations-API helpers: building `document` content blocks with
citations enabled, and extracting verified citations back out of a Claude
response.

Phase 1a of the Citations-API grounding fix (2026-08) — a pure extraction
of `linklib/agent.py`'s existing FP&A Buddy citation mechanism
(`_build_source_documents`'s per-source document-block builder,
`_assemble_cited_answer`) into a shared module, with no behavior change
(see `tests/test_citations_refactor_parity.py`, which proves the move is
byte-identical against real pre-refactor output captured in
`tests/citations_fixtures/golden_output.json`). `enrich.py`'s grounded
AI-drafted directory fields (Agent taxonomy first, per the phase plan)
import this module too, in a later phase — that's why this lives here
rather than staying agent.py-private, but nothing in this module has
changed to accommodate that yet.
"""
from __future__ import annotations


def make_document_block(title: str, body: str) -> dict:
    """One Citations-API `document` content block: plain text, citations
    enabled. Title is truncated to the API's practical limit. Caller is
    responsible for skipping an empty body before calling this — an empty
    document block is worse than none."""
    return {
        "type": "document",
        "source": {"type": "text", "media_type": "text/plain", "data": body},
        "title": (title or "")[:250],
        "citations": {"enabled": True},
    }


def _cit_get(c, name):
    """Read a field off a citation that may be an SDK object or a raw dict."""
    v = getattr(c, name, None)
    if v is None and isinstance(c, dict):
        v = c.get(name)
    return v


def extract_citations(content_blocks, sent_docs: list[dict],
                       inject_markers: bool = True) -> tuple[str, list[dict]]:
    """Walk a Claude response's content blocks and pull out verified
    citations, deduplicated and numbered in first-use order.

    `sent_docs[i]` describes the document block sent at `document_index=i`
    — {title, url, type, article_id?, provider?, own_content?} — and must
    describe what was actually SENT to the API, in the same order, so a
    response citation's `document_index` resolves to the right source.

    `inject_markers` controls what the returned text looks like:
    - True (agent.py's prose-answer use case): each cited span gets a
      trailing `[n]` marker, and the reassembled text is meant to be shown
      to a reader directly.
    - False (a future strict-JSON use case — not yet exercised by any
      caller in Phase 1a): splicing a `[n]` marker into the middle of a
      JSON string would corrupt it, since the API can split text into
      multiple blocks at a citation boundary that lands inside a field
      value. The text is returned as a plain concatenation of every block,
      byte-identical to not using citations at all — safe to `json.loads()`
      — while citations are still collected the same way, as a separate
      list the caller renders alongside the parsed fields rather than
      inline within them.

    Also recognizes automatic URL citations (no `document_index` — e.g. from
    Claude's native `web_search` tool): resolved by URL instead, tagged
    `provider="native"`.

    Best-effort by design: any surprise in the citation metadata degrades to
    the plain flattened text and an empty citation list — citation handling
    must never fail an answer or a draft.
    """
    try:
        parts: list[str] = []
        cited: list[dict] = []
        seen: dict = {}   # dedupe key -> assigned 1-based n

        for block in content_blocks:
            if getattr(block, "type", None) != "text":
                continue
            text = getattr(block, "text", "") or ""
            nums: list[int] = []
            for c in getattr(block, "citations", None) or []:
                doc_idx = _cit_get(c, "document_index")
                url = _cit_get(c, "url")
                if isinstance(doc_idx, int) and 0 <= doc_idx < len(sent_docs):
                    key = ("doc", doc_idx)
                    info = sent_docs[doc_idx]
                elif url:
                    key = ("web", url)
                    info = {"title": _cit_get(c, "title") or url,
                            "url": url, "type": "web", "provider": "native"}
                else:
                    continue   # unrecognized citation shape — skip silently
                n = seen.get(key)
                if n is None:
                    n = len(cited) + 1
                    seen[key] = n
                    entry = {"n": n, "title": info["title"],
                             "url": info["url"], "type": info["type"]}
                    if info.get("article_id") is not None:
                        entry["article_id"] = info["article_id"]
                    if info.get("provider") is not None:
                        entry["provider"] = info["provider"]
                    if info.get("own_content"):
                        # Published-content ingestion (2026-09): Brian's own
                        # writing, surfaced on merit like any other source —
                        # this only labels the citation, never affects
                        # whether/how highly it was retrieved.
                        entry["own_content"] = True
                    cited.append(entry)
                if n not in nums:
                    nums.append(n)
            if inject_markers and nums:
                # Attach markers to the span itself, before trailing
                # whitespace, so they hug the sentence they cite.
                stripped = text.rstrip()
                trail = text[len(stripped):]
                text = stripped + "".join(f"[{n}]" for n in sorted(nums)) + trail
            parts.append(text)

        return "".join(parts).strip(), cited
    except Exception:
        text = "".join(
            getattr(b, "text", "") or "" for b in content_blocks
            if getattr(b, "type", None) == "text"
        ).strip()
        return text, []
