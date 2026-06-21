"""Standalone LinkedIn post generator in Brian's voice.

Ported from the write-like-brian skill so the app doesn't depend on Claude.ai
or the skill system — it's self-contained, powered by the Claude API. Drafts a
post from a saved article (or a topic + supporting saved articles), matching
voice, mechanics, and LinkedIn shape.

Needs the `anthropic` SDK + ANTHROPIC_API_KEY.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from .db import Library
from .agent import retrieve

DEFAULT_MODEL = os.environ.get("LINKLIB_CHAT_MODEL", "claude-sonnet-4-6")

# Distilled from the write-like-brian skill (voice + hard rules + LinkedIn mode).
BRIAN_VOICE = """You draft in Brian Weisberg's voice — VP of BizOps and Strategic Finance
at Mux, a known voice in the CFO/finance community. Lead with the point, support it with
ONE concrete detail, and stop. Direct, low-ceremony, with matter-of-fact warmth. It earns
trust by being specific and honest, not polished.

VOICE:
- Specific over abstract: numbers, names, the actual thing that happened — never stacked adjectives.
- First-person proof: when he recommends something he's done it, and says so plainly.
- Honest about the hard parts; push past WHAT happened to WHY it matters.
- Earned metaphors, used sparingly — one load-bearing image beats five decorative ones.
- Authentic references from his real interests (basketball/Tom Izzo, golden-era hip-hop,
  skateboarding/snowboarding, street art) ONLY when they genuinely parallel the point. A
  forced reference is worse than none.
- Confident, not boastful. No gratitude theater.

HARD MECHANICAL RULES (never violate):
- Emdashes have NO surrounding spaces, and are used sparingly — one well-placed, never peppered.
- Sentence case for any heading/title; proper nouns and acronyms stay capped (Mux, NetSuite, FP&A, AI, Ramp).
- Spell out "and"; never "&" except in terms like FP&A.
- No performative openers or closers ("I'm excited to share", "thrilled to", "Onward!", "Excited for what's next").
- No filler ("at the end of the day", "it's worth noting that", "needless to say", "in order to" → "to").
- Avoid: genuinely, honestly, actually (as filler), leverage (as a verb), delve, robust, seamless,
  synergy, transformative, game-changer, journey (career sense).
- Don't thank the reader or beg engagement.

LINKEDIN SHAPE (this is a finance-leader-with-a-POV post for peers and operators):
- Open with a hook line that stands ALONE — a relatable confession/observation, or a vivid cold-open
  fact (often sports) that pivots to the point via one rhetorical question. NOT the thesis. Never open
  with the conclusion.
- One-line paragraphs with blank lines between, built for a phone. Fragment stacking for rhythm
  ("It's overwhelming. It moves fast.").
- Hold the reveal: let the best beat land about two-thirds down.
- Earn exactly ONE closing aphorism that crystallizes the point (the one place a one-liner close is allowed).
- 1-3 emoji total, each mapped to a specific beat, never mid-sentence clutter.

Output ONLY the post text. No preamble, no "here's your post", no surrounding quotes."""

_MODE_GUIDANCE = {
    "original": "An original POV post sparked by the article's idea — make the point his own, not a summary of theirs.",
    "self_promo": "Promoting his own work: lean and single-analogy. Open cold on a vivid fact, pivot with one rhetorical question, state the point once, close on a flat declarative punch. No escalation.",
    "amplification": "Amplifying someone else's piece: name the source, signal genuine resonance, restate the core point sharper than the original, add first-person proof, then build PAST it before the CTA.",
}


@dataclass
class Draft:
    post: str
    based_on: list[dict]


def draft_post(lib: Library, article_id: int | None = None, url: str | None = None,
               topic: str | None = None, mode: str = "original",
               model: str = DEFAULT_MODEL) -> Draft:
    """Draft a LinkedIn post from a saved article (by id/url) or a topic."""
    source_rows: list[dict] = []
    if article_id is not None:
        source_rows = [r for r in lib.search("", limit=100000) if r["id"] == article_id][:1]
    elif url:
        source_rows = [r for r in lib.search("", limit=100000) if r["url"] == url][:1]
    elif topic:
        source_rows = retrieve(lib, topic, max_sources=4)

    try:
        from anthropic import Anthropic
    except ImportError:
        return Draft(post="(Install `anthropic` to enable drafting.)", based_on=source_rows)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return Draft(post="(Set ANTHROPIC_API_KEY to enable drafting.)", based_on=source_rows)

    src_text = "\n\n".join(
        f"- {r['title']} ({r['url']})\n  {(r.get('summary') or r.get('content') or '')[:800]}"
        for r in source_rows
    ) or f"(topic: {topic})"

    guidance = _MODE_GUIDANCE.get(mode, _MODE_GUIDANCE["original"])
    user = (f"Draft a LinkedIn post. Mode: {guidance}\n\n"
            f"Source material:\n{src_text}\n\n"
            f"{'Angle/topic: ' + topic if topic else ''}")

    try:
        client = Anthropic()
        resp = client.messages.create(
            model=model, max_tokens=900,
            system=BRIAN_VOICE,
            messages=[{"role": "user", "content": user}],
        )
        post = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
        return Draft(post=post, based_on=source_rows)
    except Exception as e:
        return Draft(post=f"(Draft call failed: {e})", based_on=source_rows)
