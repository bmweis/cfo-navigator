"""Chat Matchmaker: a conversational alternative to the old quiz-based
Recommender at /tools/communities/find, plus the same pattern applied to
Software at /tools/software/find. Free-type what you're looking for, the
assistant asks a few clarifying questions, then narrows to 2-3 best-fit
suggestions.

Simpler than FP&A Buddy (linklib/agent.py) in one important way: it queries
only the existing structured Community/Software profile data, never the full
Library — no FTS5/vector retrieval, no RSS feed, no web search, no citations.
Both datasets (~38 communities, ~150 tools) are small enough to hand Claude
as full context on every turn rather than retrieve a subset of either.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from .db import Library
from .enrich import NEEDS_VERIFICATION
from .gates import MATCHMAKER_COMMUNITY_NOTE, MATCHMAKER_DISCLAIMER, MATCHMAKER_FIELD_SUFFIX
from .models import DEFAULT_CHAT_MODEL
from .voice_settings import VoicePromptMissing, require_voice_setting

DEFAULT_MODEL = os.environ.get("LINKLIB_CHAT_MODEL", DEFAULT_CHAT_MODEL)
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
# DB-backed/admin-editable (settings key "voice_matchmaker"). One shared field
# for both the Communities and Software matchmakers (see _build_system's kind
# param) since the register doesn't change between them.
#
# 2026-08 visibility follow-up: seed-only reference now, not an active
# runtime fallback — see linklib.agent.VOICE_CORE_DEFAULT's own comment for
# the full explanation (Library.seed_voice_prompts / require_voice_setting).
VOICE_MATCHMAKER_DEFAULT = """You are matching the visitor with the right fit from a curated directory. Speak as "we": "here's a fit," not "I found a fit." Direct and warm. No sales pitch.

- Reference what the visitor actually told you. A pitch that fits everyone fits no one.
- No invented experience with any community, vendor, or company—you have a directory of profiles, not a career.
- If nothing here is a good fit, say so. A weak match wastes the visitor's time."""


def _build_communities_context(lib: Library) -> tuple[str, bool]:
    """Every approved community plus its profile, formatted as compact
    plaintext blocks — the full dataset, not a retrieved subset (see module
    docstring). NEEDS_VERIFICATION-sentinel and empty fields are skipped
    rather than shown, so an unresearched field doesn't read as a real
    (empty/placeholder) answer to the model.

    Radical-transparency review standard (supersedes the old whole-profile
    publish gate, which used to swap an unreviewed profile to {} — see
    CLAUDE.md's transparency-standard note): a community's profile draft
    always ships to the model now, whether reviewed or not. When
    `needs_review` is set, one leading note line marks the whole block
    unverified (matching the single whole-profile flag — no per-line
    marking, since one flag governs all nine profile lines together, unlike
    Software's three independent per-field flags below) and the returned
    bool flips True so `_build_system` can append its standing disclaimer.
    """
    communities = lib.list_communities(approved_only=True)
    has_unverified = False

    def _line(label: str, value) -> str:
        if not value or value == NEEDS_VERIFICATION:
            return ""
        return f"{label}: {value}\n"

    blocks = []
    for c in communities:
        profile = lib.get_community_profile(c["id"]) or {}
        profile_unverified = bool(profile.get("needs_review"))
        lines = [f"### {c['name']} (slug: {c['slug']})"]
        lines.append(_line("URL", c.get("url")))
        lines.append(_line("Categories", ", ".join(c.get("categories") or [])))
        lines.append(_line("Cost", c.get("cost_band")))
        lines.append(_line("Access", c.get("access")))
        lines.append(_line("Format", c.get("format")))
        lines.append(_line("Reach", c.get("reach")))
        lines.append(_line("Local markets", c.get("local_markets")))
        lines.append(_line("Sponsorship", c.get("sponsorship_type")))
        lines.append(_line("Sponsor", c.get("sponsor_name")))
        if profile:
            if profile_unverified:
                has_unverified = True
                lines.append(MATCHMAKER_COMMUNITY_NOTE)
            lines.append(_line("Ideal member", profile.get("ideal_member")))
            lines.append(_line("Not a fit for", profile.get("anti_fit")))
            lines.append(_line("Value proposition", profile.get("value_prop")))
            lines.append(_line("What it's actually like", profile.get("format_reality")))
            lines.append(_line("Engagement level", profile.get("engagement_level")))
            lines.append(_line("Application friction", profile.get("application_friction")))
            lines.append(_line("Cost vs. value", profile.get("cost_value_verdict")))
            lines.append(_line("Business model", profile.get("business_model")))
        blocks.append("".join(l for l in lines if l))
    return "\n".join(blocks), has_unverified


def _build_software_context(lib: Library) -> tuple[str, bool]:
    """Every approved Software entry plus its features, formatted as compact
    plaintext blocks — the full dataset, not a retrieved subset (see module
    docstring). NEEDS_VERIFICATION-sentinel and empty fields are skipped,
    same reasoning as _build_communities_context.

    Radical-transparency review standard: each of the three per-field flags
    used to exclude that field's content from the matchmaker's context
    entirely when unverified. All three now always ship, each with its own
    inline "(unverified)" marker when its flag is set (unlike Communities'
    single whole-profile note above, since these are three independent
    flags, not one) — the returned bool flips True whenever any tool in the
    dataset carries at least one such marker, so `_build_system` can append
    its standing disclaimer.
    """
    tools = lib.list_tools(approved_only=True)
    has_unverified = False

    def _line(label: str, value, unverified: bool = False) -> str:
        if not value or value == NEEDS_VERIFICATION:
            return ""
        suffix = MATCHMAKER_FIELD_SUFFIX if unverified else ""
        return f"{label}{suffix}: {value}\n"

    blocks = []
    for t in tools:
        # Governed Feature Taxonomy links (category_features/tool_feature_links)
        # — replaces the legacy free-text tool_features read this used to do,
        # retired in the Feature Taxonomy Phase 1b PR 2 (CLAUDE.md's "no dead
        # data" note). Only covers tools in a seeded category (ERP/FP&A/Close
        # Management as of this phase) — a tool in an unseeded category just
        # gets no "Features" line, same as it did pre-retirement for any tool
        # with no legacy rows.
        links = lib.list_tool_feature_links_with_details(t["id"])
        lines = [f"### {t['name']} (slug: {t['slug']})"]
        lines.append(_line("URL", t.get("url")))
        lines.append(_line("Categories", ", ".join(t.get("categories") or [])))
        desc_unverified = bool(t.get("description_needs_verification"))
        diff_unverified = bool(t.get("competitive_differentiation_needs_verification"))
        taxonomy_unverified = bool(t.get("agent_taxonomy_needs_verification"))
        has_unverified = has_unverified or desc_unverified or diff_unverified or taxonomy_unverified
        lines.append(_line("What it does", t.get("summary") or t.get("description"), desc_unverified))
        lines.append(_line("How it differs from competitors", t.get("competitive_differentiation"), diff_unverified))
        lines.append(_line("Agent/automation taxonomy", t.get("agent_taxonomy_note"), taxonomy_unverified))
        if links:
            feature_bits = []
            for link in links:
                bits = []
                if link["availability"] == "add_on":
                    bits.append("add-on")
                if link["ai_enabled"]:
                    bits.append("AI")
                suffix = f" ({'/'.join(bits)})" if bits else ""
                feature_bits.append(f"{link['feature_name']}{suffix}")
            lines.append(_line("Features", ", ".join(feature_bits)))
        blocks.append("".join(l for l in lines if l))
    return "\n".join(blocks), has_unverified


def _build_system(lib: Library, kind: str) -> str:
    """kind: 'community' | 'software' — selects the dataset and persona copy;
    everything else (voice layering, conversation-shape instructions) is
    shared between the two matchmakers.

    2026-08 visibility follow-up: used to fall back to each setting's
    code-constant default when empty. Retired — require_voice_setting
    raises VoicePromptMissing instead; _answer (this function's one caller)
    catches it and returns a MatchAnswer with an explanatory text, the same
    shape it already uses for a missing SDK/API key."""
    voice_core = require_voice_setting(lib, "voice_core")
    voice_matchmaker = require_voice_setting(lib, "voice_matchmaker")
    voice = f"{voice_core}\n\n{voice_matchmaker}"

    if kind == "software":
        directory, has_unverified = _build_software_context(lib)
        subject = "the right software tool or vendor"
        link_form = "[Tool Name](/tools/software/<slug>)"
        directory_label = "every approved Software entry"
        browse_path = "/tools/software"
        clarify_hint = "the finance function it needs to cover, must-have integrations, budget"
    else:
        directory, has_unverified = _build_communities_context(lib)
        subject = "the right community (peer group, association, or Slack community)"
        link_form = "[Community Name](/tools/communities/<slug>)"
        directory_label = "every approved community"
        browse_path = "/tools/communities"
        clarify_hint = "role or stage, budget, what kind of access they want, anything more specific"

    # Radical-transparency review standard: the directory above now always
    # includes unreviewed content (marked inline — see _build_software_context/
    # _build_communities_context), where it used to be stripped out entirely.
    # This standing disclaimer only appears when at least one entry actually
    # carries an unverified marker, so a fully-reviewed catalog never gets a
    # disclaimer with nothing behind it.
    unverified_notice = MATCHMAKER_DISCLAIMER if has_unverified else ""

    return (
        f"You are a matchmaker helping a finance professional find {subject} from a "
        "curated directory below. You are NOT searching the web or any other source — "
        "only the directory text provided here is real; never invent an entry that isn't "
        "in it.\n\n"
        "How to run the conversation:\n"
        f"- Ask a small number of concrete, closed-leaning clarifying questions ({clarify_hint}) "
        "— one or two questions per turn, not a long list at once.\n"
        "- If the visitor's first message is already specific enough, skip straight to "
        "suggestions rather than force clarifying questions they didn't need.\n"
        "- Aim to land on 2-3 best-fit suggestions within a few turns.\n"
        "- When you suggest entries, give a short paragraph per suggestion explaining "
        "why it fits what THIS visitor said, then end with a compact markdown list of the "
        f"suggested entries, each as a link in the exact form {link_form} using that "
        "entry's own slug from the directory below — never a bare slug, never the full "
        "https:// URL.\n"
        "- If nothing in the directory is a good fit, say so plainly rather than force a "
        f"weak match, and suggest the visitor browse the full directory at {browse_path} "
        "instead.\n"
        "- If you suggest an entry with a detail marked (unverified) or from a community "
        "block noted as unverified, call that out inline in your answer (e.g. \"this hasn't "
        "been independently confirmed yet\") rather than presenting it as a confirmed fact."
        "\n\n"
        f"DIRECTORY ({directory_label}, current as of this conversation):\n{directory}"
        f"{unverified_notice}\n\n"
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


def _answer(lib: Library, kind: str, question: str,
           history: list[dict] | None = None) -> MatchAnswer:
    """Answer one turn of a matchmaker conversation (kind: 'community' |
    'software'). The full dataset for that kind rides in the system prompt
    on every call (see _build_system) — cheap here since both datasets are
    small, and cached server-side via Anthropic's prompt caching
    (cache_control on the system block) so a multi-turn conversation only
    pays full price for that system prompt once.
    """
    model = DEFAULT_MODEL
    trimmed_history = _trim_history(history)

    import importlib.util
    if importlib.util.find_spec("anthropic") is None:
        return MatchAnswer(text="(Install `anthropic` to enable the matchmaker.)", model=model)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return MatchAnswer(text="(Set ANTHROPIC_API_KEY to enable the matchmaker.)", model=model)

    try:
        system = _build_system(lib, kind)
    except VoicePromptMissing as e:
        return MatchAnswer(text=f"(Voice prompt not configured: {e})", model=model)
    messages = trimmed_history + [{"role": "user", "content": question}]

    try:
        resp = _get_client().messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            system=[{
                "type": "text",
                "text": system,
                # The directory text dominates this prompt and is identical
                # across every turn of every visitor's conversation until an
                # entry changes — an ideal prompt-caching candidate, so a
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


def answer_communities_question(lib: Library, question: str,
                                history: list[dict] | None = None) -> MatchAnswer:
    """Answer one turn of a Communities matchmaker conversation."""
    return _answer(lib, "community", question, history)


def answer_software_question(lib: Library, question: str,
                             history: list[dict] | None = None) -> MatchAnswer:
    """Answer one turn of a Software matchmaker conversation."""
    return _answer(lib, "software", question, history)
