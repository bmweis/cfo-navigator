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
            max_tokens=1000,  # room for a fuller answer-bearing summary + scope JSON
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
    except Exception:
        return None


_TOOL_DESC_PROMPT = """You are drafting a short vendor description for the CFO Toolbox, a
directory read by finance leaders at high-growth tech companies.

Write a description of the tool named below. Follow these rules exactly:
1. Say what the tool does — plainly and specifically, not a tagline.
2. Note how it differs from competitors, or its core strengths — capability-focused.
3. No marketing language: no "powerful," "seamless," "game-changing," "best-in-class,"
   or similar adjective stacking. No exclamation points.
4. 1-2 sentences, roughly 25-45 words total.
5. Do not mention or guess whether the company has been acquired by another company —
   leave that out entirely, even if you believe you know.

Return ONLY the description as plain text — no quotes, no markdown, no preamble.

Tool name: {name}
Tool URL: {url}

{content_block}
"""


@dataclass
class ToolDescriptionDraft:
    description: str
    low_confidence: bool = False   # page fetch failed; drafted from name/URL alone
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


def generate_tool_description(name: str, url: str, model: str = DEFAULT_MODEL) -> ToolDescriptionDraft | None:
    """Draft a CFO Toolbox description for a vendor from its name + URL, or None
    if the SDK/key is unavailable or the call fails. Fetches the URL's page text
    (best-effort, same fetch as article extraction) as grounding; when that fetch
    comes back empty, `low_confidence=True` flags the draft as based on the
    model's own knowledge rather than the live page, so the caller can warn
    whoever reviews it. Never infers acquisition status — see rule 5 above."""
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
        f"Page content (fetched from the URL):\n{page.content[:6000]}" if not low_confidence
        else "(Could not fetch page content — draft from your own knowledge of this "
             "company/product if you have it, keeping to the rules above.)"
    )

    try:
        client = Anthropic()
        resp = client.messages.create(
            model=model,
            max_tokens=200,
            messages=[{"role": "user",
                       "content": _TOOL_DESC_PROMPT.format(name=name, url=url, content_block=content_block)}],
        )
        text = "".join(block.text for block in resp.content if getattr(block, "type", None) == "text").strip()
        text = text.strip('"').strip()

        from .pricing import compute_cost
        usage = getattr(resp, "usage", None)
        in_tok = getattr(usage, "input_tokens", 0) or 0
        out_tok = getattr(usage, "output_tokens", 0) or 0
        cache_w = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cache_r = getattr(usage, "cache_read_input_tokens", 0) or 0
        cost = compute_cost(model, in_tok, out_tok, cache_w, cache_r)

        return ToolDescriptionDraft(
            description=text, low_confidence=low_confidence, model=model,
            input_tokens=in_tok, output_tokens=out_tok, cost_usd=cost,
        )
    except Exception:
        return None


# The twelve fields generate_community_profile drafts (excludes low_confidence,
# which is computed from the fetch, and updated_at, which is set on save) —
# shared with webapp/app.py so the "existing draft as context" block and the
# JSON response stay in lockstep with what the form actually submits.
COMMUNITY_PROFILE_FIELDS = [
    "ideal_member", "anti_fit", "value_prop", "format_reality", "engagement_level",
    "sponsor_relationship_note", "application_friction", "cost_value_verdict",
    "notable_members", "founded_year", "public_criticism", "verdict_summary",
]

_COMMUNITY_PROFILE_PROMPT = """You are drafting a deep, opinionated profile of a peer community for the
CFO Toolbox's Communities directory, read by finance leaders deciding whether a
community is worth their time and money. This is not directory metadata (cost,
region, access are handled elsewhere) — it's the qualitative read: who it's
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
7. Every other field is 1-3 plain-prose sentences, no markdown, no quotes.

Return STRICT JSON only (no prose, no markdown fences) with exactly these keys:

  "ideal_member": who this community is actually for.
  "anti_fit": who should probably skip it.
  "value_prop": the primary thing members get out of it.
  "format_reality": the actual cadence and mix of in-person vs. virtual.
  "engagement_level": how much active participation membership expects or rewards.
  "sponsor_relationship_note": whether sponsor presence (if any) reads as
     value-add or a sales funnel for members — a qualitative read, distinct from
     the factual sponsor name/sponsorship type recorded elsewhere.
  "application_friction": the real barrier to entry, not just the access-model
     label (e.g. "invite-only in name, but any VP with a LinkedIn intro gets in").
  "cost_value_verdict": whether the price is justified by what members report
     getting out of it.
  "notable_members": publicly known alumni/members, or null.
  "founded_year": four-digit year, or null.
  "public_criticism": any visible/reported drawback, or null.
  "verdict_summary": one short "best for X, not for Y" line.

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
    application_friction: str = ""
    cost_value_verdict: str = ""
    notable_members: str = ""
    founded_year: int | None = None
    public_criticism: str = ""
    verdict_summary: str = ""
    low_confidence: bool = False   # page fetch failed; drafted from name/URL alone
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


def generate_community_profile(name: str, url: str, existing: dict | None = None,
                               model: str = DEFAULT_MODEL) -> CommunityProfileDraft | None:
    """Draft all twelve qualitative Community Profile fields from a community's
    name + URL in one Claude call, mirroring generate_tool_description exactly
    (same page-fetch grounding, same low_confidence rule) but sized for the
    larger field count. `existing`, when given, feeds back any already-drafted
    or admin-edited fields as context so a regenerate refines rather than
    starts from scratch. Never auto-saved — same review contract as the tool
    description draft. Returns None if the SDK/key is unavailable or the call
    fails."""
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
        f"Page content (fetched from the URL):\n{page.content[:6000]}" if not low_confidence
        else "(Could not fetch page content — draft from your own knowledge of this "
             "community if you have it, keeping to the rules above.)"
    )
    existing = existing or {}
    existing_lines = "\n".join(
        f"  {field}: {existing[field]}" for field in COMMUNITY_PROFILE_FIELDS
        if str(existing.get(field) or "").strip()
    )
    existing_block = (
        f"\nExisting draft (refine using the content above rather than just repeating it):\n{existing_lines}\n"
        if existing_lines else ""
    )

    try:
        client = Anthropic()
        resp = client.messages.create(
            model=model,
            max_tokens=1600,
            messages=[{"role": "user",
                       "content": _COMMUNITY_PROFILE_PROMPT.format(
                           name=name, url=url, existing_block=existing_block, content_block=content_block)}],
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
            application_friction=str(data.get("application_friction", "")).strip(),
            cost_value_verdict=str(data.get("cost_value_verdict", "")).strip(),
            notable_members=str(data.get("notable_members") or "").strip(),
            founded_year=founded_year,
            public_criticism=str(data.get("public_criticism") or "").strip(),
            verdict_summary=str(data.get("verdict_summary", "")).strip(),
            low_confidence=low_confidence, model=model,
            input_tokens=in_tok, output_tokens=out_tok, cost_usd=cost,
        )
    except Exception:
        return None
