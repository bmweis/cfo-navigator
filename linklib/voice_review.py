"""Voice review — keep written content on the site's voice guide.

Two layers, mirroring the two kinds of rules in the DB-backed voice guide
(``linklib.agent.VOICE_CORE_DEFAULT`` / ``voice_core`` setting):

* **Mechanical** (`mechanical_findings`) — deterministic, no API. The hard rules
  that can be checked with string matching: banned buzzwords, filler phrases,
  performative openers/closers. Used by the automated QA test and surfaced in the
  on-demand reviewer. Kept intentionally conservative — only unambiguous offenders,
  so it never false-positives on legitimate finance copy (e.g. "leverage" the noun,
  "actually"/"honestly" which are only banned *as filler*) — those judgment calls
  are left to the holistic review.
* **Holistic** (`review_text`) — calls Claude with the voice guide as the rubric to
  judge tone ("does this sound on-voice"). Needs ``anthropic`` + ``ANTHROPIC_API_KEY``;
  imported lazily so this module stays dependency-free for the test.
"""
from __future__ import annotations

import os
import re

# --- Deterministic rules (single source of truth) ---------------------------
# Unambiguous buzzwords from the voice guide's "Avoid" list. Context-dependent
# ones ("leverage", "journey", "actually", "honestly", "genuinely") are
# deliberately NOT here — the holistic review handles those to avoid false positives.
BANNED_WORDS = [
    "delve", "robust", "seamless", "synergy",
    "transformative", "game-changer", "game changer",
]
FILLER_PHRASES = [
    "at the end of the day", "needless to say", "in order to",
    "it's worth noting", "it is worth noting",
]
PERFORMATIVE = [
    "i'm excited to share", "i am excited to share", "thrilled to",
    "onward!", "excited for what's next", "without further ado",
]


def mechanical_findings(text: str) -> list[tuple[str, str]]:
    """Deterministic voice violations as (rule, matched_phrase). No API calls."""
    low = text.lower()
    findings: list[tuple[str, str]] = []
    for w in BANNED_WORDS:
        if re.search(r"\b" + re.escape(w) + r"\b", low):
            findings.append(("buzzword", w))
    for p in FILLER_PHRASES:
        if p in low:
            findings.append(("filler", p))
    for p in PERFORMATIVE:
        if p in low:
            findings.append(("performative", p))
    return findings


# --- Holistic tone review (Claude) ------------------------------------------

def review_text(text: str, voice_prompt: str | None = None, model: str | None = None) -> dict:
    """Review `text` against the voice guide.

    Returns {"mechanical": [...], "review": str, "ok": bool|None}. `ok` is None when
    the LLM step is unavailable (no SDK / no key) — the mechanical findings still apply.
    """
    from .agent import VOICE_CORE_DEFAULT, DEFAULT_MODEL

    voice = (voice_prompt or VOICE_CORE_DEFAULT).strip()
    model = model or DEFAULT_MODEL
    mechanical = mechanical_findings(text)

    try:
        import anthropic  # noqa: F401
    except ImportError:
        return {"mechanical": mechanical, "review": "(Install `anthropic` to enable tone review.)", "ok": None}
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return {"mechanical": mechanical, "review": "(Set ANTHROPIC_API_KEY to enable tone review.)", "ok": None}

    system = (
        "You are a careful editor checking whether a piece of writing matches a specific "
        "author's voice. Here is the author's voice guide:\n\n" + voice + "\n\n"
        "Review the user's text against this guide. Be concise and specific. Respond with:\n"
        "1. A one-line verdict: On voice / Minor drift / Off voice.\n"
        "2. Up to 5 bullet flags — each quotes the phrase, names the rule it bends, and gives a short fix.\n"
        "Judge tone and the hard rules, not formatting of UI labels. If it's clean, say so plainly. "
        "Do not rewrite the whole piece."
    )
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=model, max_tokens=900, system=system,
        messages=[{"role": "user", "content": text}],
    )
    review = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()
    return {"mechanical": mechanical, "review": review, "ok": True}
