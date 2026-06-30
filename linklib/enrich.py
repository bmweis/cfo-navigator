"""Optional: use the Claude API to generate a clean summary + auto-tags.

This replaces what Feedly used to do for you, but on a schema you control —
which is what makes the library more searchable than Feedly was.

Requires the `anthropic` SDK and an API key:
    pip install anthropic
    export ANTHROPIC_API_KEY=sk-ant-...

Defaults to Opus for depth: the summary is the material the Ask assistant
reasons from and the resale-safe surface, so quality matters more than the
per-article cost of a one-time or low-volume run. Override with
LINKLIB_ENRICH_MODEL. The web pickers source their options from
``linklib.models`` (curated registry reconciled with the live Models API).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

DEFAULT_MODEL = os.environ.get("LINKLIB_ENRICH_MODEL", "claude-opus-4-8")

# Version of the enrichment "rules" (the prompt below). Stored alongside each
# article's enrichment so you can tell which ruleset produced a given summary,
# and re-run rows enriched under older rules. BUMP THIS whenever _PROMPT changes.
ENRICH_RULES_VERSION = "v4"

_PROMPT = """You are enriching a curated research library for a specific audience:
finance leaders at high-growth technology companies — people running FP&A or
strategic finance at a startup or scaleup, and the operators and founders growing
into that work.

The library powers two things: (1) an AI assistant that ANSWERS finance questions
by retrieving and synthesizing these articles, and (2) a search box. So the
summary must be substantive and retrievable — it is the material the assistant
reasons from, not a teaser.

Given an article's title and text, return STRICT JSON only (no prose, no markdown
fences) with exactly these four keys:

  "summary": 4-7 sentences, written to be retrieved and reasoned from. Lead with
     the article's central claim or recommendation. Include the specific figures
     it gives (benchmarks, ranges, percentages, multiples) and name the
     frameworks, metrics, formulas, companies, and people it discusses. Make
     explicit the practical question(s) a finance leader could use this article
     to answer. Favor concrete, searchable terms over generic description.

  "tags": 3-7 topic tags. PREFER reusing tags from this existing vocabulary
     wherever they fit (match the wording exactly):
{known}

     Only invent a new lowercase tag when nothing in the vocabulary fits.
{guide_block}
  "in_scope": true or false. TRUE if this is a WRITTEN ARTICLE useful to a finance
     leader, founder, or executive at a high-growth tech company — INCLUDING venture
     capital and fundraising content that helps operators (how investors evaluate
     metrics, term sheets, board management, raising a round).

     The audience is OPERATORS — finance leaders, founders, and executives running
     companies. So return FALSE when the piece is off-audience — most importantly
     content about pursuing a personal CAREER in venture capital (how to break into
     VC, get a job at a fund, become an investor), or material unrelated to
     operating and finance leadership.{cleanup_block}
     Otherwise, when in doubt, return true.

  "scope_reason": one short phrase explaining the in_scope decision (e.g.
     "operator fundraising guidance - keep", "how to get a job in VC - off-audience").

Title: {title}

Text:
{text}
"""

# Stricter exclusions applied during the first-time library cleanup. Toggleable
# (setting `scope_cleanup`) so they don't have to live forever — once the library
# is clean, ongoing queue review is the gate. Injected into the prompt only when
# cleanup mode is on.
_CLEANUP_EXCLUSIONS = """ For this library cleanup, ALSO return FALSE for:
       - content written FOR fund professionals rather than company operators —
         i.e. for VC or PE fund managers/GPs (running or operating a fund, fund
         strategy, deal sourcing, portfolio support as an investor), fund
         administration/accounting, management fees or carry, fund formation, or
         for LPs (how LPs evaluate and pick funds, LP portfolio construction, fund
         performance benchmarking). KEEP content that helps a company operator deal
         with investors (raising a round, what investors look for in YOUR metrics,
         managing your board);
       - a PODCAST or podcast episode, a WEBINAR, a video, or an audio show — even
         when summarized or republished on a blog (e.g. a 20VC episode posted on
         SaaStr). These are recordings, not articles;
       - a SLIDE DECK or third-party slides / conference presentation;
       - an annual PREDICTIONS or year-ahead roundup (e.g. "10 predictions for 2026").
"""


@dataclass
class Enrichment:
    summary: str
    tags: list[str]
    model: str = ""           # model that produced this enrichment
    rules_version: str = ""   # ENRICH_RULES_VERSION at the time
    in_scope: bool = True     # False = off-audience (e.g. how-to-get-into-VC)
    scope_reason: str = ""    # short rationale for the in_scope call


def enrich(title: str, text: str, known_tags: list[str] | None = None,
           model: str = DEFAULT_MODEL, tag_guide: str = "",
           cleanup_mode: bool = False) -> Enrichment | None:
    """Return an Enrichment, or None if the SDK/key is unavailable or the call fails.

    `tag_guide`, when set, is a short description of how the librarian tags (learned
    from their original library) — injected so auto-tagging mimics their judgment,
    not just their vocabulary.

    `cleanup_mode` adds the stricter first-time-cleanup exclusions (fund/LP content,
    podcasts/webinars, slide decks, predictions). Toggleable so it doesn't persist
    past the cleanup.
    """
    try:
        from anthropic import Anthropic
    except ImportError:
        return None
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None

    snippet = (text or title)[:12000]  # more room now that we feed full text
    known = "\n".join(f"     - {t}" for t in (known_tags or [])) or "     (none yet)"
    guide_block = (f"\n     How this librarian tags (follow these soft rules):\n{tag_guide.strip()}\n"
                   if tag_guide and tag_guide.strip() else "")
    cleanup_block = ("\n" + _CLEANUP_EXCLUSIONS) if cleanup_mode else ""
    try:
        client = Anthropic()
        resp = client.messages.create(
            model=model,
            max_tokens=1000,  # room for a fuller answer-bearing summary + scope JSON
            messages=[{"role": "user", "content": _PROMPT.format(known=known, guide_block=guide_block, cleanup_block=cleanup_block, title=title, text=snippet)}],
        )
        raw = "".join(block.text for block in resp.content if getattr(block, "type", None) == "text")
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        data = json.loads(raw)
        tags = [str(t).strip() for t in data.get("tags", []) if str(t).strip()]
        return Enrichment(
            summary=str(data.get("summary", "")).strip(), tags=tags,
            model=model, rules_version=ENRICH_RULES_VERSION,
            in_scope=bool(data.get("in_scope", True)),
            scope_reason=str(data.get("scope_reason", "")).strip(),
        )
    except Exception:
        return None
