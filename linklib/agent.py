"""Ask FP&A / finance questions against your own library.

Retrieval-augmented: pulls the most relevant saved articles, hands them to
Claude as the ONLY allowed sources, and returns an answer with numbered
citations back to your saved URLs. Grounded in what you've actually saved —
so it answers in the frame of the thinkers you already trust.

Needs the `anthropic` SDK + ANTHROPIC_API_KEY (same key as enrichment).
Defaults to Sonnet for synthesis quality; override with LINKLIB_CHAT_MODEL.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from .db import Library

DEFAULT_MODEL = os.environ.get("LINKLIB_CHAT_MODEL", "claude-sonnet-4-6")

_STOP = {
    "the", "a", "an", "and", "or", "but", "for", "with", "how", "what", "why",
    "when", "should", "would", "could", "about", "into", "from", "that", "this",
    "are", "is", "do", "does", "my", "me", "i", "to", "of", "in", "on", "it",
    "you", "your", "can", "be", "as", "at", "we", "our", "vs",
}

_SYSTEM = """You are an FP&A and startup-finance research assistant. Answer the \
question for a finance leader—concise, concrete, practical.

You have TWO source types:
1. SAVED LIBRARY sources (provided in the user message): articles the user has already \
saved. Ground your answer in these first.
2. WEB SEARCH (restricted to the user's trusted sites): use it to pull in NEW, recent \
articles the user may not have saved yet, so the answer reflects current thinking, not \
just the backlog.

Cite saved sources with [n] matching their numbers. Cite web sources inline with their \
title and link. Combine both. If neither covers the question, say so plainly instead of \
guessing. End with a one-line "Worth reading:" pointer to the most useful 1-2 sources."""


def _safe_fts_query(question: str) -> str:
    """Turn a natural question into a safe FTS5 query (no operator surprises)."""
    tokens = [w for w in re.findall(r"[a-z0-9]+", question.lower())
              if len(w) > 2 and w not in _STOP]
    if not tokens:
        return ""
    # Quote each token so '-', ':', etc. can't be read as FTS operators; OR for recall.
    return " OR ".join(f'"{t}"' for t in tokens)


@dataclass
class Answer:
    text: str
    sources: list[dict] = field(default_factory=list)       # saved-library hits
    web_sources: list[dict] = field(default_factory=list)   # fresh web results


def retrieve(lib: Library, question: str, max_sources: int = 8) -> list[dict]:
    return lib.search(_safe_fts_query(question), limit=max_sources)


def _format_sources(hits: list[dict]) -> str:
    blocks = []
    for i, h in enumerate(hits, 1):
        body = (h.get("summary") or h.get("content") or "")[:700]
        tags = ", ".join(h.get("tags", []))
        blocks.append(f"[{i}] {h['title']}\n    URL: {h['url']}\n    Tags: {tags}\n    {body}")
    return "\n\n".join(blocks) or "(no saved articles matched)"


def _collect_web_sources(content_blocks) -> list[dict]:
    """Pull title/url out of any web_search_tool_result blocks in the response."""
    out = []
    for block in content_blocks:
        if getattr(block, "type", None) == "web_search_tool_result":
            for r in getattr(block, "content", None) or []:
                url = getattr(r, "url", None) or (r.get("url") if isinstance(r, dict) else None)
                title = getattr(r, "title", None) or (r.get("title") if isinstance(r, dict) else None)
                if url:
                    out.append({"title": title or url, "url": url})
    return out


def answer_question(lib: Library, question: str, model: str = DEFAULT_MODEL,
                    max_sources: int = 8, use_web: bool = True) -> Answer:
    """Answer from saved library + (optionally) fresh articles on trusted sites."""
    hits = retrieve(lib, question, max_sources=max_sources)

    try:
        from anthropic import Anthropic
    except ImportError:
        return Answer(text="(Install `anthropic` to enable answers.)", sources=hits)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return Answer(text="(Set ANTHROPIC_API_KEY to enable answers.)", sources=hits)

    prompt = f"SAVED LIBRARY SOURCES:\n{_format_sources(hits)}\n\nQUESTION: {question}"

    kwargs = {"model": model, "max_tokens": 1500,
              "system": _SYSTEM,
              "messages": [{"role": "user", "content": prompt}]}
    if use_web:
        from .sources import preferred_domains
        domains = list(preferred_domains())
        tool = {"type": "web_search_20250305", "name": "web_search", "max_uses": 4}
        if domains:
            tool["allowed_domains"] = domains
        kwargs["tools"] = [tool]

    try:
        client = Anthropic()
        resp = client.messages.create(**kwargs)
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
        web = _collect_web_sources(resp.content) if use_web else []
        return Answer(text=text, sources=hits, web_sources=web)
    except Exception as e:
        return Answer(text=f"(Answer call failed: {e})", sources=hits)
