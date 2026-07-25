"""Chat Matchmaker: a conversational alternative to the old quiz-based
Recommender at /tools/communities/find (a later phase adds a Software
equivalent). Free-type what you're looking for, the assistant asks a few
clarifying questions, then narrows to 2-3 best-fit suggestions.

Simpler than FP&A Buddy (linklib/agent.py) in one important way: it queries
only the existing structured Community/Software profile data, never the full
Library — no FTS5/vector retrieval, no RSS feed, no web search, no citations.
The dataset (~38 communities today) is small enough to hand Claude as full
context on every turn rather than retrieve a subset of it.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from .db import Library
from .enrich import NEEDS_VERIFICATION

DEFAULT_MODEL = os.environ.get("LINKLIB_CHAT_MODEL", "claude-sonnet-4-6")
MAX_TOKENS = 900

# Conversation cost guard — a matching conversation is naturally more
# back-and-forth than a single FP&A Buddy question (there's no single
# retrieval query to get right; the assistant is narrowing down through
# clarifying questions), so this is deliberately higher than agent.py's
# MAX_FOLLOWUPS even though each individual turn is cheaper (no retrieval,
# no web search tool calls).
MAX_FOLLOWUPS = 10
MAX_HISTORY_CHARS = 6000

# Rough per-turn cost estimate (USD), shown nowhere in the UI today (matching
# FP&A Buddy's own COST_ESTIMATES intent) but kept here for admin-facing use
# if that becomes useful later. Based on the ~38-community dataset's system
# prompt size with prompt caching in effect after the first turn of a
# conversation (see _client_kwargs's cache_control).
COST_ESTIMATE_USD = 0.01


# Voice: layers the shared mechanical rubric (linklib.agent.VOICE_CORE_DEFAULT
# — em dash, filler, sentence-case rules) with a matchmaker-specific register.
# Unlike FP&A Buddy's voice_fpa_buddy layer, this isn't DB-backed/admin-editable
# yet — it's a code constant, reviewed inline as part of this feature's own
# voice pass rather than exposed on /admin/voice. Add a settings-backed
# override later if it needs to change without a redeploy.
VOICE_MATCHMAKER_DEFAULT = """You are a helpful matchmaker connecting the visitor with the right fit from a curated directory — first person plural ("we", "here's a fit"), warm but efficient, never salesy.

- Reference the specific thing the visitor said back to them when explaining a fit — don't give a generic pitch that could apply to anyone.
- No fabricated first-person experience with any community, vendor, or company — you have a directory of profiles, not a personal history.
- When nothing in the directory fits well, say so plainly rather than force a weak match."""


def _build_communities_context(lib: Library) -> str:
    """Every approved community plus its profile, formatted as compact
    plaintext blocks — the full dataset, not a retrieved subset (see module
    docstring). NEEDS_VERIFICATION-sentinel and empty fields are skipped
    rather than shown, so an unresearched field doesn't read as a real
    (empty/placeholder) answer to the model."""
    communities = lib.list_communities(approved_only=True)

    def _line(label: str, value) -> str:
        if not value or value == NEEDS_VERIFICATION:
            return ""
        return f"{label}: {value}\n"

    blocks = []
    for c in communities:
        profile = lib.get_community_profile(c["id"]) or {}
        lines = [f"### {c['name']} (slug: {c['slug']})"]
        lines.append(_line("URL", c.get("url")))
        lines.append(_line("Who it's for", c.get("demographic")))
        lines.append(_line("Categories", ", ".join(c.get("categories") or [])))
        lines.append(_line("Cost", c.get("cost_band")))
        lines.append(_line("Cost detail", c.get("cost_note")))
        lines.append(_line("Access", c.get("access")))
        lines.append(_line("Format", c.get("format")))
        lines.append(_line("Reach", c.get("reach")))
        lines.append(_line("Local markets", c.get("local_markets")))
        lines.append(_line("Sponsorship", c.get("sponsorship_type")))
        lines.append(_line("Sponsor", c.get("sponsor_name")))
        lines.append(_line("Notes", c.get("notes")))
        if profile:
            lines.append(_line("Ideal member", profile.get("ideal_member")))
            lines.append(_line("Not a fit for", profile.get("anti_fit")))
            lines.append(_line("Value proposition", profile.get("value_prop")))
            lines.append(_line("What it's actually like", profile.get("format_reality")))
            lines.append(_line("Engagement level", profile.get("engagement_level")))
            lines.append(_line("Application friction", profile.get("application_friction")))
            lines.append(_line("Cost vs. value", profile.get("cost_value_verdict")))
            lines.append(_line("Business model", profile.get("business_model")))
            lines.append(_line("Founded", profile.get("founded_year")))
        blocks.append("".join(l for l in lines if l))
    return "\n".join(blocks)


def _build_system(lib: Library) -> str:
    from .agent import VOICE_CORE_DEFAULT

    voice_core = lib.get_setting("voice_core") or VOICE_CORE_DEFAULT
    voice = f"{voice_core}\n\n{VOICE_MATCHMAKER_DEFAULT}"

    directory = _build_communities_context(lib)

    return (
        "You are a matchmaker helping a finance professional find the right community "
        "(peer group, association, or Slack community) from a curated directory below. "
        "You are NOT searching the web or any other source — only the directory text "
        "provided here is real; never invent a community that isn't in it.\n\n"
        "How to run the conversation:\n"
        "- Ask a small number of concrete, closed-leaning clarifying questions (role or "
        "stage, budget, what kind of access they want, anything more specific) — one or "
        "two questions per turn, not a long list at once.\n"
        "- If the visitor's first message is already specific enough, skip straight to "
        "suggestions rather than force clarifying questions they didn't need.\n"
        "- Aim to land on 2-3 best-fit suggestions within a few turns.\n"
        "- When you suggest communities, give a short paragraph per suggestion explaining "
        "why it fits what THIS visitor said, then end with a compact markdown list of the "
        "suggested communities, each as a link in the exact form "
        "[Community Name](/tools/communities/<slug>) using that community's own slug from "
        "the directory below — never a bare slug, never the full https:// URL.\n"
        "- If nothing in the directory is a good fit, say so plainly rather than force a "
        "weak match, and suggest the visitor browse the full directory at "
        "/tools/communities instead.\n\n"
        f"DIRECTORY (every approved community, current as of this conversation):\n{directory}\n\n"
        "Voice — write every message this way:\n"
        f"{voice}"
    )


@dataclass
class MatchAnswer:
    text: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0


_client = None


def _get_client():
    global _client
    if _client is None:
        from anthropic import Anthropic
        _client = Anthropic()
    return _client


def _trim_history(history) -> list[dict]:
    """Same shape/purpose as agent._trim_history: bound prior turns carried
    into the prompt to well-formed user/assistant messages, most recent
    first, capped at MAX_HISTORY_CHARS total."""
    if not history:
        return []
    clean: list[dict] = []
    for m in history:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        content = m.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            clean.append({"role": role, "content": content.strip()})
    kept: list[dict] = []
    total = 0
    for m in reversed(clean[-2 * (MAX_FOLLOWUPS + 1):]):
        total += len(m["content"])
        if total > MAX_HISTORY_CHARS and kept:
            break
        kept.append(m)
    return list(reversed(kept))


def answer_communities_question(lib: Library, question: str,
                                history: list[dict] | None = None) -> MatchAnswer:
    """Answer one turn of a Communities matchmaker conversation.

    The full communities directory + profiles rides in the system prompt on
    every call (see _build_system) — cheap here since the dataset is small,
    and cached server-side via Anthropic's prompt caching (cache_control on
    the system block) so a multi-turn conversation only pays full price for
    that system prompt once.
    """
    model = DEFAULT_MODEL
    trimmed_history = _trim_history(history)

    import importlib.util
    if importlib.util.find_spec("anthropic") is None:
        return MatchAnswer(text="(Install `anthropic` to enable the matchmaker.)", model=model)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return MatchAnswer(text="(Set ANTHROPIC_API_KEY to enable the matchmaker.)", model=model)

    system = _build_system(lib)
    messages = trimmed_history + [{"role": "user", "content": question}]

    try:
        resp = _get_client().messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            system=[{
                "type": "text",
                "text": system,
                # The directory text dominates this prompt and is identical
                # across every turn of every visitor's conversation until a
                # community changes — an ideal prompt-caching candidate, so a
                # multi-turn matching conversation (or the next visitor,
                # within the 5-minute TTL) only pays full input price once.
                "cache_control": {"type": "ephemeral"},
            }],
            messages=messages,
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()

        from .pricing import compute_cost
        usage = resp.usage
        in_tok = getattr(usage, "input_tokens", 0) or 0
        out_tok = getattr(usage, "output_tokens", 0) or 0
        cache_w = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cache_r = getattr(usage, "cache_read_input_tokens", 0) or 0
        cost = compute_cost(model, in_tok, out_tok, cache_w, cache_r)

        return MatchAnswer(text=text, model=model, input_tokens=in_tok, output_tokens=out_tok,
                           cache_creation_tokens=cache_w, cache_read_tokens=cache_r, cost_usd=cost)
    except Exception as e:
        return MatchAnswer(text=f"(Answer call failed: {e})", model=model)
