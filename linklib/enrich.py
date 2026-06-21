"""Optional: use the Claude API to generate a clean summary + auto-tags.

This replaces what Feedly used to do for you, but on a schema you control —
which is what makes the library more searchable than Feedly was.

Requires the `anthropic` SDK and an API key:
    pip install anthropic
    export ANTHROPIC_API_KEY=sk-ant-...

Defaults to Haiku because you'll be enriching a few thousand articles and
it's the cheapest capable model. Override with LINKLIB_ENRICH_MODEL.
Model names change over time — verify current options at
https://docs.claude.com/en/docs/about-claude/models
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

DEFAULT_MODEL = os.environ.get("LINKLIB_ENRICH_MODEL", "claude-haiku-4-5-20251001")

_PROMPT = """You are enriching a personal finance/business research library so it
is highly searchable. Given an article's title and text, return STRICT JSON only
(no prose, no markdown fences) with exactly two keys:

  "summary": 3-5 sentences. Capture the core argument, plus any specific
     frameworks, metrics, benchmarks, formulas, and named people or companies.
     Favor concrete, searchable terms over generic description — someone should
     be able to find this article later by searching for what it actually says.

  "tags": 3-7 topic tags. PREFER reusing tags from this existing vocabulary
     wherever they fit (match the wording exactly):
{known}

     Only invent a new lowercase tag when nothing in the vocabulary fits.

Title: {title}

Text:
{text}
"""


@dataclass
class Enrichment:
    summary: str
    tags: list[str]


def enrich(title: str, text: str, known_tags: list[str] | None = None,
           model: str = DEFAULT_MODEL) -> Enrichment | None:
    """Return an Enrichment, or None if the SDK/key is unavailable or the call fails."""
    try:
        from anthropic import Anthropic
    except ImportError:
        return None
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None

    snippet = (text or title)[:12000]  # more room now that we feed full text
    known = "\n".join(f"     - {t}" for t in (known_tags or [])) or "     (none yet)"
    try:
        client = Anthropic()
        resp = client.messages.create(
            model=model,
            max_tokens=600,
            messages=[{"role": "user", "content": _PROMPT.format(known=known, title=title, text=snippet)}],
        )
        raw = "".join(block.text for block in resp.content if getattr(block, "type", None) == "text")
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        data = json.loads(raw)
        tags = [str(t).strip() for t in data.get("tags", []) if str(t).strip()]
        return Enrichment(summary=str(data.get("summary", "")).strip(), tags=tags)
    except Exception:
        return None
