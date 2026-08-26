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
import logging
import os
from dataclasses import dataclass, field

from .agent import VOICE_CORE_DEFAULT
from .citations import extract_citations, make_document_block

_logger = logging.getLogger(__name__)

DEFAULT_MODEL = os.environ.get("LINKLIB_ENRICH_MODEL", "claude-opus-5")


def _resolve_voice_core(voice_core: str) -> str:
    """Same DB-backed-setting-with-fallback pattern as linklib.agent's
    _build_system / linklib.matchmaker (PR #110): the caller resolves
    ``lib.get_setting("voice_core")`` and passes it in (enrich.py has no
    Library handle of its own), and an empty/unset value falls back to the
    code-constant default here rather than at every call site."""
    return (voice_core or "").strip() or VOICE_CORE_DEFAULT


# Shared structure guidance for the AI-drafted Software directory fields
# (Description, Agent taxonomy) that are long enough to read as one dense
# block otherwise — voice_core covers tone/mechanics (including the em dash
# rule) but says nothing about paragraph/list structure, so that instruction
# lives here in the prompt template rather than in voice_core's own copy
# (Brian's to edit, not this pass's to rewrite). Deliberately not prescriptive
# about paragraph count or forcing bullets where the content isn't list-like.
_STRUCTURE_GUIDANCE = (
    "Structure the writing for readability, the way the site's own Business "
    "summary copy reads: natural paragraph breaks (a blank line between "
    "distinct ideas) instead of one dense block, and a short bulleted list "
    "(each item on its own line, starting with \"- \") only where the "
    "content is genuinely list-like—naming several distinct features, "
    "capabilities, or named agents. Don't force a list where prose reads "
    "more naturally, and don't pad length just to create more paragraphs."
)

# Version of the enrichment "rules" (the prompt below). Stored alongside each
# article's enrichment so you can tell which ruleset produced a given summary,
# and re-run rows enriched under older rules. BUMP THIS whenever _PROMPT changes.
ENRICH_RULES_VERSION = "v4"

# Floor for every generate_*() call's max_tokens below. Claude's on-by-default
# adaptive thinking shares the same budget as the response (max_tokens caps
# thinking + response together), so a starved budget lets thinking alone
# exhaust it, leaving zero tokens for the actual JSON response — that empty
# string then fails json.loads and gets swallowed by the bare except below,
# surfacing as a misleading "missing ANTHROPIC_API_KEY" error even when the
# key and connectivity are fine (generate_tool_differentiation's original
# max_tokens=400, fixed one-off in PR 260). Every call site here runs its
# literal through _checked_max_tokens() so a future thin default fails loudly
# at call time instead of silently misbehaving in production.
MIN_GENERATE_MAX_TOKENS = 1200


def _checked_max_tokens(value: int) -> int:
    """Guard a generate_*() call's max_tokens against MIN_GENERATE_MAX_TOKENS.
    Raises immediately (first call) rather than letting a too-thin budget
    silently starve the response out from under adaptive thinking — see the
    floor's docstring above."""
    assert value >= MIN_GENERATE_MAX_TOKENS, (
        f"max_tokens={value} is below MIN_GENERATE_MAX_TOKENS ({MIN_GENERATE_MAX_TOKENS}) — "
        "adaptive thinking can consume the whole budget and leave nothing for the response."
    )
    return value


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
     operating and finance leadership.
     Otherwise, when in doubt, return true.

  "scope_reason": one short phrase explaining the in_scope decision (e.g.
     "operator fundraising guidance - keep", "how to get a job in VC - off-audience").

Title: {title}

Text:
{text}
"""


@dataclass
class Enrichment:
    summary: str
    tags: list[str]
    model: str = ""           # model that produced this enrichment
    rules_version: str = ""   # ENRICH_RULES_VERSION at the time
    in_scope: bool = True     # False = off-audience (e.g. how-to-get-into-VC)
    scope_reason: str = ""    # short rationale for the in_scope call
    input_tokens: int = 0     # real usage from this call, for the overhead-cost
    output_tokens: int = 0    # ledger (linklib.db.Library.record_enrichment_cost,
    cost_usd: float = 0.0     # issue #105) — callers persist it, not enrich() itself


def enrich(title: str, text: str, known_tags: list[str] | None = None,
           model: str = DEFAULT_MODEL, tag_guide: str = "") -> Enrichment | None:
    """Return an Enrichment, or None if the SDK/key is unavailable or the call fails.

    `tag_guide`, when set, is a short description of how the librarian tags (learned
    from their original library) — injected so auto-tagging mimics their judgment,
    not just their vocabulary.
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
    try:
        client = Anthropic()
        resp = client.messages.create(
            model=model,
            max_tokens=_checked_max_tokens(2000),  # room for a fuller answer-bearing summary + scope
                              # JSON, plus headroom for Opus 5's on-by-default adaptive thinking
                              # (max_tokens caps thinking + response together)
            messages=[{"role": "user", "content": _PROMPT.format(known=known, guide_block=guide_block, title=title, text=snippet)}],
        )
        raw = "".join(block.text for block in resp.content if getattr(block, "type", None) == "text")
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        data = json.loads(raw)
        tags = [str(t).strip() for t in data.get("tags", []) if str(t).strip()]

        from .pricing import compute_cost
        usage = getattr(resp, "usage", None)
        in_tok = getattr(usage, "input_tokens", 0) or 0
        out_tok = getattr(usage, "output_tokens", 0) or 0
        cache_w = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cache_r = getattr(usage, "cache_read_input_tokens", 0) or 0
        cost = compute_cost(model, in_tok, out_tok, cache_w, cache_r)

        return Enrichment(
            summary=str(data.get("summary", "")).strip(), tags=tags,
            model=model, rules_version=ENRICH_RULES_VERSION,
            in_scope=bool(data.get("in_scope", True)),
            scope_reason=str(data.get("scope_reason", "")).strip(),
            input_tokens=in_tok, output_tokens=out_tok, cost_usd=cost,
        )
    except Exception as e:
        _logger.warning("enrich() failed: %s: %s", type(e).__name__, e)
        return None


_TOOL_DESC_PROMPT = """You are drafting a vendor profile for the CFO Toolbox, a
directory read by finance leaders at high-growth tech companies. This has two
surfaces: a full profile-page write-up, and a short summary shown on the
directory card and in search results — write both.

Follow these rules exactly:
1. Say what the tool does — plainly and specifically, not marketing copy.
2. Cover what it does, who it's built for, and how it differs from
   competitors or its core strengths — capability-focused, grounded in the
   page content below wherever it supports a claim.
3. No marketing language: no "powerful," "seamless," "game-changing," "best-in-class,"
   or similar adjective stacking. No exclamation points.
4. Do not mention or guess whether the company has been acquired by another company —
   leave that out entirely, even if you believe you know.

Fields:
  "description": the full profile-page write-up — roughly 8-12 sentences
     (about 150-300 words). Budget and depth are not a constraint here; use
     the page content thoroughly rather than settling for a thin summary.
     Every sentence should carry real information, not padding.
  "summary": a short, standalone 2-3 sentence version (about 30-60 words)
     for the directory card and search results — a proper condensed
     rewrite someone could read on its own and understand what the tool is
     and does, not just the description's opening sentences copy-pasted.
  "confident": true if the page content below gave you a solid, specific
     basis for both fields; false if you had to draft from thin/ambiguous
     page content or from your own general knowledge rather than the page
     itself. Say so honestly rather than defaulting to true — a reader
     relies on this to know whether the write-up is well-grounded.

Voice guide — write both fields in this voice:
{voice_core}

{structure_guidance}
"summary" is the exception to the structure guidance above: it's a short
subhead/card teaser, always a single continuous paragraph, no line breaks
or bullets — the structure guidance applies only to "description".

Return STRICT JSON only (no prose, no markdown fences) with exactly these
keys: "description", "summary", "confident".

Tool name: {name}
Tool URL: {url}

{content_block}
"""


@dataclass
class ToolDescriptionDraft:
    description: str
    summary: str = ""
    low_confidence: bool = False   # page fetch failed; drafted from name/URL alone
    confident: bool = False   # the model's own self-reported certainty (see prompt above)
    citations: list = field(default_factory=list)   # Citations-API grounding fix, Phase 2
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


def generate_tool_description(name: str, url: str, model: str = DEFAULT_MODEL,
                               voice_core: str = "") -> ToolDescriptionDraft | None:
    """Draft a CFO Toolbox description (full profile-page write-up) plus a
    short summary (directory card / search) for a vendor from its name +
    URL, or None if the SDK/key is unavailable or the call fails. Fetches
    the URL's page text (best-effort, same fetch as article extraction) as
    grounding; when that fetch comes back empty, `low_confidence=True`
    flags the draft as based on the model's own knowledge rather than the
    live page, so the caller can warn whoever reviews it. Never infers
    acquisition status — see rule 4 above.

    Citations-API grounding fix, Phase 2: single-page grounding (unlike
    Agent taxonomy's multi-page nav crawl) — the one fetched page, when
    non-empty, rides as a real Citations-API `document` block instead of
    being flattened into the prompt, so `citations` reflects what the
    model actually cited, mechanically verified by the API. `confident`
    is a separate, independently-checkable self-report, as before.
    `inject_markers=False` since this response is strict JSON — see
    generate_tool_agent_taxonomy's docstring for why. `citations` is empty
    when the fetch failed (`low_confidence=True`), since there's nothing
    to cite.

    Voice enforcement + structure (2026-08): `voice_core` is the caller's
    already-resolved `lib.get_setting("voice_core") or VOICE_CORE_DEFAULT`
    — same pattern PR #110 established for FP&A Buddy/matchmaker/voice
    rewrite. Falls back to VOICE_CORE_DEFAULT itself if the caller passes
    nothing, so every existing call site (and every test) keeps working
    unchanged. Also instructs "description" (not "summary" — see the
    prompt) to use paragraph breaks and, where genuinely list-like,
    bullets — previously always one dense block regardless of length."""
    try:
        from anthropic import Anthropic
    except ImportError:
        return None
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None

    from . import extract
    page = extract.fetch_page(url)
    low_confidence = not bool(page.content.strip())
    doc_blocks: list[dict] = []
    sent_docs: list[dict] = []
    if low_confidence:
        content_block = ("(Could not fetch page content — draft from your own knowledge of this "
                          "company/product if you have it, keeping to the rules above.)")
    else:
        content_block = ""
        body = page.content.strip()[:15000]
        doc_blocks.append(make_document_block(name, body))
        sent_docs.append({"title": name, "url": url, "type": "tool_page"})

    prompt = _TOOL_DESC_PROMPT.format(
        name=name, url=url, content_block=content_block,
        voice_core=_resolve_voice_core(voice_core), structure_guidance=_STRUCTURE_GUIDANCE,
    )
    # Documents (when any) ride first, the drafting instructions last — same
    # ordering as generate_tool_agent_taxonomy, so citations resolve against
    # what was actually sent.
    message_content = (doc_blocks + [{"type": "text", "text": prompt}]) if doc_blocks else prompt

    try:
        client = Anthropic()
        resp = client.messages.create(
            model=model,
            max_tokens=_checked_max_tokens(1600),  # room for an 8-12 sentence description, plus
                              # headroom for Opus 5's on-by-default adaptive thinking
            messages=[{"role": "user", "content": message_content}],
        )
        # inject_markers=False: strict JSON — see generate_tool_agent_taxonomy.
        raw, citations = extract_citations(resp.content, sent_docs, inject_markers=False)
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        data = json.loads(raw)

        from .pricing import compute_cost
        usage = getattr(resp, "usage", None)
        in_tok = getattr(usage, "input_tokens", 0) or 0
        out_tok = getattr(usage, "output_tokens", 0) or 0
        cache_w = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cache_r = getattr(usage, "cache_read_input_tokens", 0) or 0
        cost = compute_cost(model, in_tok, out_tok, cache_w, cache_r)

        return ToolDescriptionDraft(
            description=str(data.get("description", "")).strip(),
            summary=str(data.get("summary", "")).strip(),
            low_confidence=low_confidence, confident=bool(data.get("confident")),
            citations=citations, model=model,
            input_tokens=in_tok, output_tokens=out_tok, cost_usd=cost,
        )
    except Exception as e:
        _logger.warning("generate_tool_description() failed: %s: %s", type(e).__name__, e)
        return None


_TOOL_DIFFERENTIATION_PROMPT = """You are drafting the "Bottom line" callout for a vendor's profile page on the
CFO Toolbox, a directory read by finance leaders deciding between tools. This
is the single most scannable takeaway on the page — a "best for X, trade-off
is Y" framing, not a restatement of the description.

Follow these rules exactly:
1. Name who the tool is genuinely best for (a specific buyer/use case, not
   "finance teams" generically) and the real trade-off or limitation that
   comes with picking it — every strength implies something it costs you.
2. Ground the claim in the description and competitor context below; never
   invent a comparison point you can't support.
3. No marketing language: no "powerful," "seamless," "best-in-class," or
   similar adjective stacking. No exclamation points.
4. One or two sentences. This is a callout, not a paragraph.

Voice guide — write the callout in this voice:
{voice_core}

Vendor: {name} ({url})
Description: {description}
{competitors_block}

Also report "confident": true if the description and competitor context above
gave you a real basis for a specific, defensible comparison; false if you had
to draft a generic-sounding callout with little to actually differentiate
against. Say so honestly rather than defaulting to true.

Respond with JSON only: {{"competitive_differentiation": "...", "confident": true|false}}"""


@dataclass
class ToolDifferentiationDraft:
    competitive_differentiation: str
    low_confidence: bool = False
    confident: bool = False   # the model's own self-reported certainty (see prompt above)
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


def generate_tool_differentiation(name: str, url: str, description: str,
                                   competitor_names: list[str] | None = None,
                                   model: str = DEFAULT_MODEL,
                                   voice_core: str = "") -> ToolDifferentiationDraft | None:
    """Draft the profile page's "Bottom line" callout — a first pass for
    Brian to review/edit in the admin edit form, never auto-saved (standing
    principle: AI drafts into the form, nothing publishes without an
    explicit human review-and-save). Grounded in the tool's own description
    plus its curated competitor list when available; `low_confidence=True`
    when there's no competitor context to compare against, since "how this
    differs" is weaker without something to differ from. Returns None if the
    SDK/key is unavailable or the call fails.

    Voice enforcement (2026-08): `voice_core` follows generate_tool_description's
    same resolve-with-fallback contract — this is the field a spaced em dash
    was actually observed in, so voice_core's existing unspaced-em-dash rule
    now reaches this prompt. No structure guidance added here: the callout is
    deliberately 1-2 sentences (rule 4 above), never long enough to need
    paragraph/bullet structure."""
    try:
        from anthropic import Anthropic
    except ImportError:
        return None
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None

    competitor_names = competitor_names or []
    low_confidence = not bool(competitor_names)
    competitors_block = (
        f"Curated competitors: {', '.join(competitor_names)}" if competitor_names
        else "(No competitors curated yet — draft from the description alone.)"
    )

    try:
        client = Anthropic()
        resp = client.messages.create(
            model=model,
            max_tokens=_checked_max_tokens(1200),  # headroom for Opus 5's on-by-default adaptive
                              # thinking (max_tokens caps thinking + response together) — the 1-2
                              # sentence output itself needs very little, but a starved budget
                              # (previously 400) let thinking alone exhaust it, leaving zero tokens
                              # for the JSON response (issue: generate-differentiation silently
                              # returning None on empty/unparseable output; fixed in PR 260)
            messages=[{"role": "user",
                       "content": _TOOL_DIFFERENTIATION_PROMPT.format(
                           name=name, url=url, description=description, competitors_block=competitors_block,
                           voice_core=_resolve_voice_core(voice_core))}],
        )
        raw = "".join(block.text for block in resp.content if getattr(block, "type", None) == "text")
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        data = json.loads(raw)

        from .pricing import compute_cost
        usage = getattr(resp, "usage", None)
        in_tok = getattr(usage, "input_tokens", 0) or 0
        out_tok = getattr(usage, "output_tokens", 0) or 0
        cache_w = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cache_r = getattr(usage, "cache_read_input_tokens", 0) or 0
        cost = compute_cost(model, in_tok, out_tok, cache_w, cache_r)

        return ToolDifferentiationDraft(
            competitive_differentiation=str(data.get("competitive_differentiation", "")).strip(),
            low_confidence=low_confidence, confident=bool(data.get("confident")),
            model=model,
            input_tokens=in_tok, output_tokens=out_tok, cost_usd=cost,
        )
    except Exception as e:
        _logger.warning("generate_tool_differentiation() failed: %s: %s", type(e).__name__, e)
        return None


_COMPETITOR_MATCH_PROMPT = """You are curating the "Competitors" section of a directory profile page, read by
finance leaders comparing options. Below is one entry and a shortlist of candidates
already pre-filtered by shared category tags — your job is to judge which of THOSE
candidates are genuinely close competitors or alternatives, not to invent new ones.

Follow these rules exactly:
1. A true competitor solves substantially the same problem for a similar buyer —
   shared category tags alone are not enough (the tag taxonomy is broad).
2. When in doubt, leave a candidate out — a false negative here just means Brian
   adds it by hand; a false positive misleads a reader comparing options.
3. Judge only the candidates listed below. Never suggest anything not in the list.

Entry: {name}
Description: {description}

Candidates (id: name — description):
{candidates_block}

Respond with JSON only: {{"competitor_ids": [<id>, <id>, ...]}}"""


@dataclass
class CompetitorMatchResult:
    competitor_ids: list[int] = field(default_factory=list)
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


def generate_competitor_matches(name: str, description: str, candidates: list[dict],
                                 model: str = DEFAULT_MODEL) -> CompetitorMatchResult | None:
    """Judges which of a pre-filtered candidate shortlist are genuine
    competitors/similar entries — shared by Software (Competitors) and
    Communities (Similar communities), since the underlying task is
    identical: given one entry and a shortlist already narrowed by category
    overlap (see Library.suggest_tool_competitors /
    suggest_community_competitors), decide which candidates are real
    matches. Returns only IDs drawn from `candidates` — never invents a new
    one. A first pass for the admin edit form's checkbox list: never
    auto-saved, same review-before-publish gate as every other generatable
    field. `candidates`: list of {{"id", "name", "description"}} dicts.
    Returns None if the SDK/key is unavailable, the call fails, or
    `candidates` is empty (nothing to judge)."""
    if not candidates:
        return None
    try:
        from anthropic import Anthropic
    except ImportError:
        return None
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None

    candidates_block = "\n".join(
        f"{c['id']}: {c['name']} — {(c.get('description') or '').strip()[:300]}" for c in candidates
    )

    try:
        client = Anthropic()
        resp = client.messages.create(
            model=model,
            max_tokens=_checked_max_tokens(MIN_GENERATE_MAX_TOKENS),  # was 500 — below the shared
                              # floor; a short "list of ids" output still needs headroom for Opus
                              # 5's on-by-default adaptive thinking, same failure mode as PR 260's
                              # differentiation fix
            messages=[{"role": "user",
                       "content": _COMPETITOR_MATCH_PROMPT.format(
                           name=name, description=description, candidates_block=candidates_block)}],
        )
        raw = "".join(block.text for block in resp.content if getattr(block, "type", None) == "text")
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        data = json.loads(raw)

        from .pricing import compute_cost
        usage = getattr(resp, "usage", None)
        in_tok = getattr(usage, "input_tokens", 0) or 0
        out_tok = getattr(usage, "output_tokens", 0) or 0
        cache_w = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cache_r = getattr(usage, "cache_read_input_tokens", 0) or 0
        cost = compute_cost(model, in_tok, out_tok, cache_w, cache_r)

        valid_ids = {c["id"] for c in candidates}
        raw_ids = data.get("competitor_ids") if isinstance(data, dict) else None
        matched = [int(i) for i in raw_ids if isinstance(i, (int, str)) and int(i) in valid_ids] if isinstance(raw_ids, list) else []

        return CompetitorMatchResult(
            competitor_ids=matched, model=model,
            input_tokens=in_tok, output_tokens=out_tok, cost_usd=cost,
        )
    except Exception as e:
        _logger.warning("generate_competitor_matches() failed: %s: %s", type(e).__name__, e)
        return None


# Agent-taxonomy drafting (search overhaul Phase 4b, extended in the
# automated-research follow-up; narrowed to agent-taxonomy-only in the
# Feature Taxonomy Phase 1b PR 2 legacy tool_features retirement — this used
# to also draft standalone-vs-bundled tool_features rows in the same call,
# before that table was retired; see CLAUDE.md's "no dead data" note and
# ARCHITECTURE.md's automated-research section for the full history). The
# grounding mechanism (real nav-page crawl, not guessed paths) is unchanged
# and still worth the real fetch, since a real per-vendor summary needs real
# page content regardless of what else used to ride along with it in the
# same call. Confidence is a boolean in the JSON response ("confident")
# rather than the sentinel-per-field NEEDS_VERIFICATION used elsewhere,
# since the agent-taxonomy verdict is already one semantic unit.
_AGENT_TAXONOMY_PAGE_GUESSES = ("pricing", "solutions", "product", "products", "platform", "features", "ai", "agents")
_NAV_LINK_KEYWORDS = ("product", "solution", "platform", "feature", "agent", "ai", "how it works", "use case")


def _discover_nav_pages(base_url: str, max_pages: int = 10) -> list[tuple[str, str]]:
    """Finds real Product/Solutions-type pages by parsing the homepage's own
    nav links, rather than guessing URL paths (the original Phase 4b
    approach: guessing /pricing, /solutions, /product often missed real
    content — a vendor's actual agent/feature page can live at any slug).
    Returns [(link text, absolute url)], same-domain only, deduped, capped
    at max_pages. Returns [] on any fetch/parse failure — callers fall back
    to the guessed-path approach, not an error."""
    import requests
    from bs4 import BeautifulSoup
    from urllib.parse import urljoin, urlsplit

    try:
        resp = requests.get(base_url, headers={"User-Agent": "Mozilla/5.0 (compatible; linklib/1.0)"},
                            timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception:
        return []

    base_host = urlsplit(base_url).netloc
    seen: set[str] = set()
    found: list[tuple[str, str]] = []
    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True)
        if not text or len(text) > 40:
            continue
        text_lc = text.lower()
        if not any(kw in text_lc for kw in _NAV_LINK_KEYWORDS):
            continue
        href = urljoin(base_url, a["href"]).split("#")[0].rstrip("/")
        if urlsplit(href).netloc != base_host or href == base_url.rstrip("/") or href in seen:
            continue
        seen.add(href)
        found.append((text, href))
        if len(found) >= max_pages:
            break
    return found


def _fetch_taxonomy_grounding(url: str) -> tuple[str, list[tuple[str, str, str]]]:
    """Fetches the tool's homepage plus its real Product/Solutions-type nav
    pages (_discover_nav_pages) — falling back to guessed paths (pricing,
    solutions, product) only if nav discovery finds nothing, e.g. a blocked
    fetch or a site with no matching nav text. Returns (content_block,
    fetched) where fetched is [(label, url, content)] for whichever pages
    actually returned content — a failed fetch (404, empty page, blocked) is
    silently dropped, not treated as an error, since that's expected across
    ~150+ external sites."""
    from . import extract
    base = url.rstrip("/")
    nav_pages = _discover_nav_pages(url)
    if nav_pages:
        candidates = [("Homepage", url)] + nav_pages
    else:
        candidates = [("Homepage", url)] + [
            (label.capitalize(), f"{base}/{label}") for label in _AGENT_TAXONOMY_PAGE_GUESSES
        ]
    fetched = []
    for label, page_url in candidates:
        try:
            page = extract.fetch_page(page_url)
        except Exception:
            continue
        if page.content.strip():
            fetched.append((label, page_url, page.content[:10000]))
    content_block = "\n\n".join(
        f"--- {label} ({page_url}) ---\n{content}" for label, page_url, content in fetched
    )[:60000]
    return content_block, fetched


_AGENT_TAXONOMY_PROMPT = """You are researching a vendor listed in the CFO Toolbox's Software
directory. Budget and depth are not a constraint here — read the page
content provided (as separate documents, when available) carefully and be
as thorough and specific as the material supports.

Write a thorough summary (aim for 3-6 sentences, more if there's real
material to cover) of whether and how AI agents are involved in this
product. Ground this strictly in the documents provided — every specific
claim should be traceable to something they actually say. If the vendor
names ANY specific agents anywhere in the content (e.g. "Aura," "Ember," a
"Contract Review Agent," a "flux agent") — find and name ALL of them, not
just the first one you notice; a reader comparing tools needs the complete
roster of named agents, not a sample. For each named agent, note what it
actually does if the content says so. Distinguish: a fully independent
agent that runs a workflow end-to-end, an agent-assisted feature where AI
helps but a human stays in the loop, or no real agent framing at all
(generic "AI-powered" marketing language without actual agent behavior
described doesn't count as agentic — say so plainly rather than
overstating it). If the documents give no genuine signal either way, say
that rather than guessing, and set "confident" to false.

Voice guide — write the summary in this voice:
{voice_core}

{structure_guidance}

Return STRICT JSON only (no prose, no markdown fences) with exactly this
shape:
{{"summary": "...", "confident": true|false}}

Product name: {name}
Product URL: {url}
Existing directory description: {description}

{content_block}
"""


@dataclass
class AgentTaxonomyResult:
    agent_taxonomy_note: str = ""
    agent_taxonomy_needs_verification: bool = True
    confident: bool = False   # the model's own self-reported certainty (see prompt above) —
                               # same raw signal agent_taxonomy_needs_verification is derived
                               # from (not confident), stored separately as of the 2026-08
                               # follow-up so the UI can show a genuine, permanent "Claude
                               # confidence: Yes/No" line matching Description/Differentiation,
                               # independent of needs_verification's own review-status meaning.
    low_confidence: bool = False   # no page content could be fetched at all
    # API-verified citations (Citations-API grounding fix, Phase 1b,
    # 2026-08): [{n, title, url, type: "tool_page"}], deduped by url,
    # first-use order, UNCAPPED — the caller (webapp/app.py) persists this
    # to the shared entity_citations table (Library.set_entity_citations)
    # and a public render site applies the 5-source display cap; this
    # field itself always holds everything. Empty whenever nothing was
    # fetched at all (low_confidence=True — no documents were sent, so
    # nothing could be cited) or the model simply didn't cite anything.
    # Resolved from real document blocks (linklib.citations), not
    # self-reported — independent of `confident` above.
    citations: list = field(default_factory=list)
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


# Total grounding text budget across every fetched page for one agent-
# taxonomy draft (Citations-API grounding fix, Phase 1b) — same
# 60,000-char ceiling _fetch_taxonomy_grounding's own joined content_block
# used to enforce, now applied while building per-page document blocks
# instead of one flattened string. Each individual page is already capped
# at 10,000 chars by _fetch_taxonomy_grounding itself.
_TAXONOMY_DOC_BUDGET_CHARS = 60000


def _build_taxonomy_documents(fetched: list[tuple[str, str, str]]
                              ) -> tuple[list[dict], list[dict]]:
    """Turn `_fetch_taxonomy_grounding`'s fetched pages into Citations-API
    `document` blocks, one per page, so the model's summary can be
    mechanically traced back to a real page rather than self-reported.
    Returns (doc_blocks, sent_docs) — sent_docs[i] describes doc_blocks[i]
    as {title, url, type: "tool_page"} for citation resolution (same
    contract as linklib.agent._build_source_documents's sent_docs). Stops
    once the combined budget is spent; a page with no content left over is
    skipped rather than sent as an empty document."""
    doc_blocks: list[dict] = []
    sent_docs: list[dict] = []
    used = 0
    for label, page_url, content in fetched:
        if used >= _TAXONOMY_DOC_BUDGET_CHARS:
            break
        body = content.strip()[: max(0, _TAXONOMY_DOC_BUDGET_CHARS - used)]
        if not body:
            continue
        used += len(body)
        doc_blocks.append(make_document_block(label, body))
        sent_docs.append({"title": label, "url": page_url, "type": "tool_page"})
    return doc_blocks, sent_docs


def generate_tool_agent_taxonomy(name: str, url: str, description: str = "",
                                 model: str = DEFAULT_MODEL,
                                 voice_core: str = "") -> AgentTaxonomyResult | None:
    """Draft an agent-taxonomy summary for a Software entry. Grounds on the
    homepage plus its real Product/Solutions-type nav pages
    (_fetch_taxonomy_grounding — falls back to guessed paths only if nav
    discovery finds nothing). The returned note is a first-pass draft: the
    caller is expected to write it with needs_verification set from the
    "confident" flag and source='llm_enrichment', never auto-confirmed.
    Returns None if the SDK/key is unavailable or the call fails — same
    contract as generate_tool_description.

    Used to also draft standalone-vs-bundled tool_features rows in the same
    call, before that table was retired (Feature Taxonomy Phase 1b PR 2 —
    see CLAUDE.md's "no dead data" note); this function keeps the real-crawl
    grounding mechanism, which is still worth it for the agent-taxonomy
    summary alone.

    Citations-API grounding fix, Phase 1b: each fetched page now rides as a
    real Citations-API `document` block (linklib.citations) instead of
    being flattened into the prompt as plain text, so `result.citations`
    reflects what the model actually cited, mechanically verified by the
    API — not the model's own self-report (that's what `confident` already
    was, and still is; citations is a separate, independently-checkable
    fact). When no page content could be fetched at all
    (`low_confidence=True`), no documents are sent and the model is told to
    draft from its own knowledge, same as before — `citations` is empty in
    that case, since there's nothing to cite.

    Voice enforcement + structure (2026-08): `voice_core` follows
    generate_tool_description's same resolve-with-fallback contract, and the
    summary is instructed to use paragraph breaks / bullets where genuinely
    list-like — most relevant here when several named agents need listing
    (see the prompt's own "find and name ALL of them" instruction)."""
    try:
        from anthropic import Anthropic
    except ImportError:
        return None
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None

    _, fetched = _fetch_taxonomy_grounding(url)
    low_confidence = not fetched
    doc_blocks, sent_docs = _build_taxonomy_documents(fetched) if fetched else ([], [])
    content_note = "" if doc_blocks else (
        f"(Could not fetch any page content for {url} — draft from your own "
        f"knowledge of {name} if you have it, keeping to the rules above.)"
    )

    prompt = _AGENT_TAXONOMY_PROMPT.format(
        name=name, url=url, description=description.strip() or "(none provided)",
        content_block=content_note,
        voice_core=_resolve_voice_core(voice_core), structure_guidance=_STRUCTURE_GUIDANCE,
    )
    # Documents (when any) ride first, the drafting instructions last — same
    # ordering as linklib.agent.answer_question's user turn, so citations
    # resolve against what was actually sent.
    message_content = (doc_blocks + [{"type": "text", "text": prompt}]) if doc_blocks else prompt

    try:
        client = Anthropic()
        resp = client.messages.create(
            model=model,
            max_tokens=_checked_max_tokens(2000),  # headroom for Opus 5's on-by-default adaptive thinking
            messages=[{"role": "user", "content": message_content}],
        )
        # inject_markers=False: this response is strict JSON, and splicing a
        # [n] marker into the middle of the "summary" string would corrupt
        # it — see linklib.citations.extract_citations's docstring. `raw` is
        # therefore a plain concatenation, safe to json.loads(); `citations`
        # is collected the same way regardless, already deduped by
        # document_index (== deduped by url in practice, since each fetched
        # page has its own distinct URL).
        raw, citations = extract_citations(resp.content, sent_docs, inject_markers=False)
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        data = json.loads(raw)

        from .pricing import compute_cost
        usage = getattr(resp, "usage", None)
        in_tok = getattr(usage, "input_tokens", 0) or 0
        out_tok = getattr(usage, "output_tokens", 0) or 0
        cache_w = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cache_r = getattr(usage, "cache_read_input_tokens", 0) or 0
        cost = compute_cost(model, in_tok, out_tok, cache_w, cache_r)

        data = data if isinstance(data, dict) else {}

        return AgentTaxonomyResult(
            agent_taxonomy_note=str(data.get("summary") or "").strip(),
            agent_taxonomy_needs_verification=not bool(data.get("confident")),
            confident=bool(data.get("confident")),
            low_confidence=low_confidence, citations=citations, model=model,
            input_tokens=in_tok, output_tokens=out_tok, cost_usd=cost,
        )
    except Exception as e:
        _logger.warning("generate_tool_agent_taxonomy() failed: %s: %s", type(e).__name__, e)
        return None


# The fields generate_community_profile drafts (excludes low_confidence,
# which is computed from the fetch, and updated_at, which is set on save) —
# shared with webapp/app.py so the "existing draft as context" block and the
# JSON response stay in lockstep with what the form actually submits.
# seniority_band/primary_purpose/resources_included/platform_type/
# meeting_format/event_style/cpe_eligible were originally hand-entry-only
# (added after this list, alongside the Details-card short-field inputs) —
# folded into the same generate call here per the AI-first-pass-on-every-
# field standing principle, rather than a second generate mechanism.
COMMUNITY_PROFILE_FIELDS = [
    "ideal_member", "anti_fit", "value_prop", "format_reality", "engagement_level",
    "sponsor_relationship_note", "business_model", "application_friction", "cost_value_verdict",
    "notable_members", "founded_year", "public_criticism", "verdict_summary",
    "stage_focus", "jobs_program", "team_or_individual",
    "seniority_band", "primary_purpose", "resources_included",
    "platform_type", "meeting_format", "event_style", "cpe_eligible",
]

# The 12 long-form/narrative Community profile fields judged to carry real
# fabrication risk (Phase 0 investigation + Brian's approval, 2026-08) —
# deliberately a SUBSET of COMMUNITY_PROFILE_FIELDS above, not all 23:
# excludes founded_year (a bare int, not prose) and the eleven short
# factual/categorical fields (stage_focus/jobs_program/team_or_individual
# included — judged categorical-not-narrative-risk despite being in
# VOICE_REWRITE_FIELDS below, since matching that boundary wasn't the goal;
# matching actual fabrication risk was). Shared with webapp/app.py so the
# confidence hidden-input wiring and the JSON schema above stay in lockstep.
COMMUNITY_CONFIDENCE_FIELDS = [
    "ideal_member", "anti_fit", "value_prop", "business_model", "format_reality",
    "engagement_level", "sponsor_relationship_note", "application_friction",
    "cost_value_verdict", "notable_members", "public_criticism", "verdict_summary",
]

_COMMUNITY_PROFILE_PROMPT = """You are drafting a deep, opinionated profile of a peer community for the
CFO Toolbox's Communities directory, read by finance leaders deciding whether a
community is worth their time and money. This is not directory metadata (cost,
access are handled elsewhere) — it's the qualitative read: who it's
actually for, what it's actually like, and whether it delivers.

Write about the community named below. Follow these rules exactly:
1. Be specific and opinionated, not generic marketing copy. No "powerful,"
   "vibrant," "world-class," or similar adjective stacking. No exclamation points.
2. Ground every claim in the page content provided below (or your own knowledge,
   if the page content is unavailable) — never invent specifics you can't support.
3. `founded_year` must be a four-digit integer or null — only if you're confident
   of the year.
4. `notable_members` must be null unless you know of PUBLICLY reported members or
   alumni — never guess or infer private membership from indirect signals.
5. `public_criticism` is a drawback that's actually been reported or is visible
   from the page/your knowledge (e.g. pay-to-play concerns, inconsistent chapter
   quality) — null if you don't know of any, never a fabricated nitpick.
6. `verdict_summary` is one short sentence in the shape "Best for X, not for Y."
7. Every prose field (see below) is 2-5 plain-prose sentences, no markdown, no
   quotes — budget and depth are not a constraint here, so use the page
   content below thoroughly rather than settling for a thin one-liner.
8. `seniority_band`/`primary_purpose`/`resources_included`/`platform_type`/
   `meeting_format`/`event_style` are short factual/categorical values (a
   phrase, not a paragraph) — deliberately brief, distinct from the prose
   fields above.
9. `cpe_eligible` must be one of "Yes", "No", or "Unclear", optionally with a
   short qualifier in parentheses (e.g. "Yes (NASBA-approved sponsor)") —
   never guess "Yes" without a specific reason to believe it.
10. For the "confidence" object below: for EACH of its twelve keys, report
    true only if the page content (or your own knowledge) gave you a real,
    specific basis for that field's answer; false if you had to draft it
    thin, generic, or largely inferred. Judge each key independently — a
    community's `value_prop` can be well-grounded while its `notable_members`
    is a guess, and the confidence for each should reflect that, not a single
    blanket judgment repeated twelve times.

Return STRICT JSON only (no prose, no markdown fences) with exactly these keys:

  "ideal_member": who this community is actually for.
  "anti_fit": who should probably skip it.
  "value_prop": the primary thing members get out of it.
  "format_reality": the actual cadence and mix of in-person vs. virtual.
  "engagement_level": how much active participation membership expects or rewards.
  "sponsor_relationship_note": whether sponsor presence (if any) reads as
     value-add or a sales funnel for members — a qualitative read, distinct from
     the factual sponsor name/sponsorship type recorded elsewhere.
  "business_model": how the community structurally sustains itself, e.g. a
     gated, dues-funded peer group insulated from a sales pitch by design, vs.
     a wide-funnel free-to-join community monetized via paid tiers, events, or
     sponsorships. Distinct from sponsor_relationship_note above, which judges
     whether a sponsor's presence feels value-add or salesy, not how the
     community itself makes money.
  "application_friction": the real barrier to entry, not just the access-model
     label (e.g. "invite-only in name, but any VP with a LinkedIn intro gets in").
  "cost_value_verdict": whether the price is justified by what members report
     getting out of it.
  "notable_members": publicly known alumni/members, or null.
  "founded_year": four-digit year, or null.
  "public_criticism": any visible/reported drawback, or null.
  "verdict_summary": one short "best for X, not for Y" line.
  "stage_focus": whether the community targets growth-stage, late-stage, or
     public companies, or has no particular stage focus — or null if unclear.
  "jobs_program": whether there's a FORMAL job-placement/transition program
     (not just informal networking that happens to help with job searches) —
     or null if unclear.
  "team_or_individual": whether membership is individual-only, team/company-
     based, or supports both — or null if unclear.
  "seniority_band": who it targets by seniority (e.g. "CFO and VP Finance
     only," "open to Controllers and up") — or null if unclear.
  "primary_purpose": the community's main purpose in a few words, e.g.
     "networking," "peer learning," or "both" — or null if unclear.
  "resources_included": templates, benchmarking data, research, job boards,
     etc. actually provided to members, or "No" if none — or null if unclear.
  "platform_type": the technical platform members actually use, e.g. "Slack,"
     "proprietary app," "in-person only" — or null if unclear.
  "meeting_format": in-person, virtual, or hybrid cadence — or null if unclear.
  "event_style": the feel of its events, e.g. "large-format conferences,"
     "intimate small-group," "forum-only, no events" — or null if unclear.
  "cpe_eligible": "Yes"/"No"/"Unclear", per rule 9 above.
  "confidence": an object with exactly these twelve boolean keys, one per
     the long-form/narrative fields above that carry real fabrication risk
     (the short factual/categorical fields above are not included — see
     rule 10): "ideal_member", "anti_fit", "value_prop", "business_model",
     "format_reality", "engagement_level", "sponsor_relationship_note",
     "application_friction", "cost_value_verdict", "notable_members",
     "public_criticism", "verdict_summary".

Community name: {name}
Community URL: {url}
{existing_block}
{content_block}
"""


@dataclass
class CommunityProfileDraft:
    ideal_member: str = ""
    anti_fit: str = ""
    value_prop: str = ""
    format_reality: str = ""
    engagement_level: str = ""
    sponsor_relationship_note: str = ""
    business_model: str = ""
    application_friction: str = ""
    cost_value_verdict: str = ""
    notable_members: str = ""
    founded_year: int | None = None
    public_criticism: str = ""
    verdict_summary: str = ""
    stage_focus: str = ""
    jobs_program: str = ""
    team_or_individual: str = ""
    seniority_band: str = ""
    primary_purpose: str = ""
    resources_included: str = ""
    platform_type: str = ""
    meeting_format: str = ""
    event_style: str = ""
    cpe_eligible: str = ""
    low_confidence: bool = False   # page fetch failed; drafted from name/URL alone
    confidence: dict = field(default_factory=dict)   # {field_name: bool}, COMMUNITY_CONFIDENCE_FIELDS keys only
    citations: list = field(default_factory=list)   # Citations-API grounding fix, Phase 3
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


def generate_community_profile(name: str, url: str, existing: dict | None = None,
                               model: str = DEFAULT_MODEL) -> CommunityProfileDraft | None:
    """Draft every Community Profile field (COMMUNITY_PROFILE_FIELDS — the
    original 13 narrative fields plus the short factual/categorical ones
    added later) from a community's name + URL in one Claude call, mirroring
    generate_tool_description exactly (same page-fetch grounding, same
    low_confidence rule) but sized for the larger field count. `existing`,
    when given, feeds back any already-drafted
    or admin-edited fields as context so a regenerate refines rather than
    starts from scratch. Never auto-saved — same review contract as the tool
    description draft. Returns None if the SDK/key is unavailable or the call
    fails.

    Citations-API grounding fix, Phase 3: single-page grounding, same as
    generate_tool_description — the one fetched page, when non-empty, rides
    as a real Citations-API `document` block instead of being flattened into
    the prompt, so `citations` reflects what the model actually cited,
    mechanically verified by the API. Unlike Description/Agent taxonomy,
    this is ONE citation set for the whole 23-field draft (decision 5,
    Phase 0) — not per field — since every field is drafted from the same
    single page in the same call. `inject_markers=False` since this response
    is strict JSON. `citations` is empty when the fetch failed
    (`low_confidence=True`), since there's nothing to cite."""
    try:
        from anthropic import Anthropic
    except ImportError:
        return None
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None

    from . import extract
    page = extract.fetch_page(url)
    low_confidence = not bool(page.content.strip())
    doc_blocks: list[dict] = []
    sent_docs: list[dict] = []
    if low_confidence:
        content_block = ("(Could not fetch page content — draft from your own knowledge of this "
                          "community if you have it, keeping to the rules above.)")
    else:
        content_block = ""
        body = page.content.strip()[:15000]
        doc_blocks.append(make_document_block(name, body))
        sent_docs.append({"title": name, "url": url, "type": "community_page"})
    existing = existing or {}
    existing_lines = "\n".join(
        f"  {field}: {existing[field]}" for field in COMMUNITY_PROFILE_FIELDS
        if str(existing.get(field) or "").strip()
    )
    existing_block = (
        f"\nExisting draft (refine using the content above rather than just repeating it):\n{existing_lines}\n"
        if existing_lines else ""
    )

    prompt = _COMMUNITY_PROFILE_PROMPT.format(
        name=name, url=url, existing_block=existing_block, content_block=content_block)
    # Documents (when any) ride first, the drafting instructions last — same
    # ordering as generate_tool_description, so citations resolve against
    # what was actually sent.
    message_content = (doc_blocks + [{"type": "text", "text": prompt}]) if doc_blocks else prompt

    try:
        client = Anthropic()
        resp = client.messages.create(
            model=model,
            max_tokens=_checked_max_tokens(6000),  # headroom for Opus 5's on-by-default adaptive thinking
            messages=[{"role": "user", "content": message_content}],
        )
        # inject_markers=False: strict JSON — see generate_tool_description.
        raw, citations = extract_citations(resp.content, sent_docs, inject_markers=False)
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        data = json.loads(raw)

        from .pricing import compute_cost
        usage = getattr(resp, "usage", None)
        in_tok = getattr(usage, "input_tokens", 0) or 0
        out_tok = getattr(usage, "output_tokens", 0) or 0
        cache_w = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cache_r = getattr(usage, "cache_read_input_tokens", 0) or 0
        cost = compute_cost(model, in_tok, out_tok, cache_w, cache_r)

        founded_year = data.get("founded_year")
        try:
            founded_year = int(founded_year) if founded_year is not None else None
        except (TypeError, ValueError):
            founded_year = None

        return CommunityProfileDraft(
            ideal_member=str(data.get("ideal_member", "")).strip(),
            anti_fit=str(data.get("anti_fit", "")).strip(),
            value_prop=str(data.get("value_prop", "")).strip(),
            format_reality=str(data.get("format_reality", "")).strip(),
            engagement_level=str(data.get("engagement_level", "")).strip(),
            sponsor_relationship_note=str(data.get("sponsor_relationship_note", "")).strip(),
            business_model=str(data.get("business_model", "")).strip(),
            application_friction=str(data.get("application_friction", "")).strip(),
            cost_value_verdict=str(data.get("cost_value_verdict", "")).strip(),
            notable_members=str(data.get("notable_members") or "").strip(),
            founded_year=founded_year,
            public_criticism=str(data.get("public_criticism") or "").strip(),
            verdict_summary=str(data.get("verdict_summary", "")).strip(),
            stage_focus=str(data.get("stage_focus") or "").strip(),
            jobs_program=str(data.get("jobs_program") or "").strip(),
            team_or_individual=str(data.get("team_or_individual") or "").strip(),
            seniority_band=str(data.get("seniority_band") or "").strip(),
            primary_purpose=str(data.get("primary_purpose") or "").strip(),
            resources_included=str(data.get("resources_included") or "").strip(),
            platform_type=str(data.get("platform_type") or "").strip(),
            meeting_format=str(data.get("meeting_format") or "").strip(),
            event_style=str(data.get("event_style") or "").strip(),
            cpe_eligible=str(data.get("cpe_eligible") or "").strip(),
            low_confidence=low_confidence,
            confidence=_parse_community_confidence(data.get("confidence")),
            citations=citations,
            model=model,
            input_tokens=in_tok, output_tokens=out_tok, cost_usd=cost,
        )
    except Exception as e:
        _logger.warning("generate_community_profile() failed: %s: %s", type(e).__name__, e)
        return None


def _parse_community_confidence(raw) -> dict:
    """Coerce the model's "confidence" object into {field: bool}, defaulting
    a missing/malformed key to False (needs review) rather than guessing
    true — same safe-default convention as generate_tool_agent_taxonomy's
    `not bool(data.get("confident"))`."""
    raw = raw if isinstance(raw, dict) else {}
    return {f: bool(raw.get(f)) for f in COMMUNITY_CONFIDENCE_FIELDS}


# Sentinel drafted into a listing field the model isn't confident about,
# instead of guessing — distinct from community_profiles.needs_review (a
# whole-profile, human-toggled sign-off flag): this is a per-field, machine-set
# gap marker on the basic directory listing. webapp/app.py strips it back out
# before any field reaches a public page (see _public_community).
NEEDS_VERIFICATION = "Needs verification"

_COMMUNITY_LISTING_PROMPT = """You are drafting the basic directory-listing fields for a peer community in
the CFO Toolbox's Communities directory — factual metadata (cost, access,
categories), distinct from the deeper qualitative profile handled
elsewhere.

Ground every answer in the page content provided below (or your own knowledge,
if the page content is unavailable) — never invent a specific you can't
support. For any field where the real value genuinely isn't clear from that
material, use the literal string "{needs_verification}" for that field (or
leave a list field empty) rather than guessing — a wrong answer in this
directory is worse than a flagged gap. Only use "{needs_verification}" when
the field plausibly applies but the specific value is unclear; if a field
clearly does not apply at all (e.g. there is no sponsor), use an empty string
instead of the sentinel.

Fields:
  "demographic": who this community is for, e.g. "CFOs & VP Finance at
     Series B+ SaaS companies" — one short phrase. "{needs_verification}" if unclear.
  "reach": exactly one value from this list, verbatim: {reach_options}.
     "{needs_verification}" if unclear.
  "local_markets": a short comma-separated list of city/region names where
     this community has a specific chapter, hub, or local in-person focus,
     e.g. "Boston, New York, SF Bay Area" — empty string if it's purely
     online/national with no specific local footprint mentioned on the page.
     Never invent a city the page doesn't name.
  "cost_band": exactly one value from this list, verbatim: {cost_band_options}.
     "{needs_verification}" if unclear.
  "cost_note": a short free-text note on pricing specifics (exact dues,
     multi-seat pricing) if known, else an empty string.
  "sponsorship_type": exactly one value from this list, verbatim:
     {sponsorship_options}. "{needs_verification}" if unclear.
  "sponsor_name": the sponsor's name, only if sponsorship_type indicates one
     and it's named on the page — else an empty string.
  "access": exactly one value from this list, verbatim: {access_options}.
     "{needs_verification}" if unclear.
  "format": exactly one value from this list, verbatim: {format_options}.
     "{needs_verification}" if unclear.
  "categories": a JSON list of zero or more values from exactly this list,
     verbatim: {category_options}. Only include a category that clearly
     applies — never a value outside this list.

Return STRICT JSON only (no prose, no markdown fences) with exactly these
keys: demographic, reach, local_markets, cost_band, cost_note, sponsorship_type,
sponsor_name, access, format, categories.

Community name: {name}
Community URL: {url}

{content_block}
"""


@dataclass
class CommunityListingDraft:
    demographic: str = ""
    reach: str = ""
    local_markets: str = ""
    cost_band: str = ""
    cost_note: str = ""
    sponsorship_type: str = ""
    sponsor_name: str = ""
    access: str = ""
    format: str = ""
    categories: list[str] = field(default_factory=list)
    low_confidence: bool = False   # page fetch failed; drafted from name/URL alone
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


def generate_community_listing(name: str, url: str, *, reach_options: list[str],
                                cost_band_options: list[str], sponsorship_options: list[str],
                                access_options: list[str], format_options: list[str],
                                category_options: list[str],
                                model: str = DEFAULT_MODEL) -> CommunityListingDraft | None:
    """Draft the basic directory-listing fields (distinct from the deeper
    generate_community_profile above) for a community from its name + URL,
    mirroring generate_tool_description's fetch/prompt/cost-tracking pattern.
    The enum fields are constrained to caller-supplied controlled
    vocabularies (webapp/app.py owns those lists — reach/cost_band/
    sponsorship_type/access/format options and the category checklist)
    rather than free text, and validated against them again on the way out
    in case the model drifts. local_markets is free text (no controlled
    vocabulary — see the Metros checkbox grid -> free text migration) so it's
    only trimmed, not validated against a list. Any field it isn't confident
    about is drafted as the literal NEEDS_VERIFICATION sentinel (or left
    empty for list fields) instead of a guess. Never auto-saved — same
    review contract as the other generate_* helpers. Returns None if the
    SDK/key is unavailable or the call fails."""
    try:
        from anthropic import Anthropic
    except ImportError:
        return None
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None

    from . import extract
    page = extract.fetch_page(url)
    low_confidence = not bool(page.content.strip())
    content_block = (
        f"Page content (fetched from the URL):\n{page.content[:15000]}" if not low_confidence
        else "(Could not fetch page content — draft from your own knowledge of this "
             "community if you have it, keeping to the rules above.)"
    )

    prompt = _COMMUNITY_LISTING_PROMPT.format(
        needs_verification=NEEDS_VERIFICATION,
        reach_options=json.dumps(reach_options),
        cost_band_options=json.dumps(cost_band_options),
        sponsorship_options=json.dumps(sponsorship_options),
        access_options=json.dumps(access_options),
        format_options=json.dumps(format_options),
        category_options=json.dumps(category_options),
        name=name, url=url, content_block=content_block,
    )

    try:
        client = Anthropic()
        resp = client.messages.create(
            model=model,
            max_tokens=_checked_max_tokens(1200),  # headroom for Opus 5's on-by-default adaptive thinking
            messages=[{"role": "user", "content": prompt}],
        )
        raw = "".join(block.text for block in resp.content if getattr(block, "type", None) == "text")
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        data = json.loads(raw)

        from .pricing import compute_cost
        usage = getattr(resp, "usage", None)
        in_tok = getattr(usage, "input_tokens", 0) or 0
        out_tok = getattr(usage, "output_tokens", 0) or 0
        cache_w = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cache_r = getattr(usage, "cache_read_input_tokens", 0) or 0
        cost = compute_cost(model, in_tok, out_tok, cache_w, cache_r)

        def _one_of(value, options: list[str]) -> str:
            value = str(value or "").strip()
            return value if (value in options or value == NEEDS_VERIFICATION) else ""

        def _subset_of(values, options: list[str]) -> list[str]:
            if not isinstance(values, list):
                return []
            allowed = set(options)
            return [v for v in values if isinstance(v, str) and v in allowed]

        return CommunityListingDraft(
            demographic=str(data.get("demographic", "")).strip(),
            reach=_one_of(data.get("reach"), reach_options),
            local_markets=str(data.get("local_markets", "")).strip(),
            cost_band=_one_of(data.get("cost_band"), cost_band_options),
            cost_note=str(data.get("cost_note", "")).strip(),
            sponsorship_type=_one_of(data.get("sponsorship_type"), sponsorship_options),
            sponsor_name=str(data.get("sponsor_name", "")).strip(),
            access=_one_of(data.get("access"), access_options),
            format=_one_of(data.get("format"), format_options),
            categories=_subset_of(data.get("categories"), category_options),
            low_confidence=low_confidence, model=model,
            input_tokens=in_tok, output_tokens=out_tok, cost_usd=cost,
        )
    except Exception as e:
        _logger.warning("generate_community_listing() failed: %s: %s", type(e).__name__, e)
        return None


# The 11 narrative community-profile fields eligible for a voice-rewrite pass
# (scripts/archive/import_community_profiles.py) — a style pass only, never a content
# edit. Deliberately excludes `notable_members` (a name list, not prose) and
# the 8 short factual/categorical fields (founded_year, low_confidence,
# primary_purpose, cpe_eligible, platform_type, meeting_format, event_style,
# seniority_band, resources_included) alongside it — none of those are prose
# that benefits from a tone pass.
VOICE_REWRITE_FIELDS = [
    "ideal_member", "anti_fit", "value_prop", "business_model", "format_reality",
    "engagement_level", "sponsor_relationship_note", "application_friction",
    "cost_value_verdict", "public_criticism", "verdict_summary",
    "stage_focus", "jobs_program", "team_or_individual",
]

_VOICE_REWRITE_PROMPT = """You are rewriting research copy for the CFO Toolbox's Communities
directory so it matches a specific author's voice. Here is the voice guide:

{voice_core}

Below are researched, factually-accurate field values describing a peer
community, written in a generic research-report style. Rewrite EACH field for
tone and sentence structure to match the voice guide above. This is a STYLE
pass only, not a content edit:

- Do not change, drop, soften, or add any factual detail: preserve every
  number, date, dollar figure, percentage, proper name, and specific claim
  exactly as given.
- Do not shorten a field to the point of losing information, and don't pad
  one out with new claims not present in the source.
- If a field is empty, return it empty — do not invent content for it.

Return STRICT JSON only (no prose, no markdown fences), with exactly these
keys, one rewritten string per field:

{field_list}

Community name: {name}

Source fields (JSON):
{fields_json}
"""


@dataclass
class VoiceRewriteResult:
    fields: dict            # field name -> rewritten text
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


def voice_rewrite_community_fields(name: str, fields: dict, voice_core: str,
                                    model: str = DEFAULT_MODEL) -> "VoiceRewriteResult | None":
    """Rewrite the narrative community-profile fields in `fields` (a subset of
    VOICE_REWRITE_FIELDS -> source text) to match `voice_core`'s tone, in one
    Claude call covering every field at once — cheaper and easier to spot-check
    than one call per field. Facts must survive unchanged; only style is
    rewritten. Returns None if the SDK/key is unavailable or the call fails,
    same never-auto-saved contract as generate_community_profile — the caller
    persists the result and logs cost via Library.record_enrichment_cost."""
    try:
        from anthropic import Anthropic
    except ImportError:
        return None
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None

    present = {k: v for k, v in fields.items() if k in VOICE_REWRITE_FIELDS and str(v or "").strip()}
    if not present:
        return VoiceRewriteResult(fields={})

    field_list = "\n".join(f'  "{k}": rewritten text for {k}' for k in present)
    prompt = _VOICE_REWRITE_PROMPT.format(
        voice_core=voice_core.strip(), field_list=field_list,
        name=name, fields_json=json.dumps(present, indent=2),
    )

    try:
        client = Anthropic()
        resp = client.messages.create(
            model=model,
            max_tokens=_checked_max_tokens(4000),  # headroom for Opus 5's on-by-default adaptive thinking
            messages=[{"role": "user", "content": prompt}],
        )
        raw = "".join(block.text for block in resp.content if getattr(block, "type", None) == "text")
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        data = json.loads(raw)

        from .pricing import compute_cost
        usage = getattr(resp, "usage", None)
        in_tok = getattr(usage, "input_tokens", 0) or 0
        out_tok = getattr(usage, "output_tokens", 0) or 0
        cache_w = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cache_r = getattr(usage, "cache_read_input_tokens", 0) or 0
        cost = compute_cost(model, in_tok, out_tok, cache_w, cache_r)

        rewritten = {k: str(data.get(k, present[k])).strip() for k in present}
        return VoiceRewriteResult(fields=rewritten, model=model,
                                  input_tokens=in_tok, output_tokens=out_tok, cost_usd=cost)
    except Exception as e:
        _logger.warning("voice_rewrite_community_fields() failed: %s: %s", type(e).__name__, e)
        return None


def test_model_connection(model_id: str) -> dict:
    """Fire one minimal, real Claude call against `model_id` to verify it
    actually works — manual/on-demand only from /admin/system/model's "Test
    connection" action, same shape and same "manual, never a background job"
    contract as linklib.agent.test_exa_connection. Returns
    {"ok", "error", "cost_usd"}: cost_usd is 0.0 on any failure (an
    auth/rate-limit/invalid-model error means nothing billed), and the real
    compute_cost() figure on a genuine success."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return {"ok": False, "error": "ANTHROPIC_API_KEY is not set.", "cost_usd": 0.0}
    try:
        from anthropic import Anthropic
    except ImportError:
        return {"ok": False, "error": "The anthropic SDK isn't installed.", "cost_usd": 0.0}

    try:
        client = Anthropic()
        resp = client.messages.create(
            model=model_id,
            max_tokens=64,   # a trivial one-word reply, not a generate_*() JSON call — the
                             # MIN_GENERATE_MAX_TOKENS floor above doesn't apply here
            messages=[{"role": "user", "content": "Reply with only the word: ok"}],
        )
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "cost_usd": 0.0}

    from .pricing import compute_cost
    usage = getattr(resp, "usage", None)
    in_tok = getattr(usage, "input_tokens", 0) or 0
    out_tok = getattr(usage, "output_tokens", 0) or 0
    cache_w = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_r = getattr(usage, "cache_read_input_tokens", 0) or 0
    try:
        cost = compute_cost(model_id, in_tok, out_tok, cache_w, cache_r)
    except Exception:
        cost = 0.0
    return {"ok": True, "error": "", "cost_usd": cost}
