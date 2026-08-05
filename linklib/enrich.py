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
from dataclasses import dataclass, field

DEFAULT_MODEL = os.environ.get("LINKLIB_ENRICH_MODEL", "claude-opus-5")

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
            max_tokens=2000,  # room for a fuller answer-bearing summary + scope JSON, plus
                              # headroom for Opus 5's on-by-default adaptive thinking (max_tokens
                              # caps thinking + response together)
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

Return STRICT JSON only (no prose, no markdown fences) with exactly these
keys: "description", "summary".

Tool name: {name}
Tool URL: {url}

{content_block}
"""


@dataclass
class ToolDescriptionDraft:
    description: str
    summary: str = ""
    low_confidence: bool = False   # page fetch failed; drafted from name/URL alone
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


def generate_tool_description(name: str, url: str, model: str = DEFAULT_MODEL) -> ToolDescriptionDraft | None:
    """Draft a CFO Toolbox description (full profile-page write-up) plus a
    short summary (directory card / search) for a vendor from its name +
    URL, or None if the SDK/key is unavailable or the call fails. Fetches
    the URL's page text (best-effort, same fetch as article extraction) as
    grounding; when that fetch comes back empty, `low_confidence=True`
    flags the draft as based on the model's own knowledge rather than the
    live page, so the caller can warn whoever reviews it. Never infers
    acquisition status — see rule 4 above."""
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
             "company/product if you have it, keeping to the rules above.)"
    )

    try:
        client = Anthropic()
        resp = client.messages.create(
            model=model,
            max_tokens=1600,  # room for an 8-12 sentence description, plus headroom
                              # for Opus 5's on-by-default adaptive thinking
            messages=[{"role": "user",
                       "content": _TOOL_DESC_PROMPT.format(name=name, url=url, content_block=content_block)}],
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

        return ToolDescriptionDraft(
            description=str(data.get("description", "")).strip(),
            summary=str(data.get("summary", "")).strip(),
            low_confidence=low_confidence, model=model,
            input_tokens=in_tok, output_tokens=out_tok, cost_usd=cost,
        )
    except Exception:
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

Vendor: {name} ({url})
Description: {description}
{competitors_block}

Respond with JSON only: {{"differentiation_note": "..."}}"""


@dataclass
class ToolDifferentiationDraft:
    differentiation_note: str
    low_confidence: bool = False
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


def generate_tool_differentiation(name: str, url: str, description: str,
                                   competitor_names: list[str] | None = None,
                                   model: str = DEFAULT_MODEL) -> ToolDifferentiationDraft | None:
    """Draft the profile page's "Bottom line" callout — a first pass for
    Brian to review/edit in the admin edit form, never auto-saved (standing
    principle: AI drafts into the form, nothing publishes without an
    explicit human review-and-save). Grounded in the tool's own description
    plus its curated competitor list when available; `low_confidence=True`
    when there's no competitor context to compare against, since "how this
    differs" is weaker without something to differ from. Returns None if the
    SDK/key is unavailable or the call fails."""
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
            max_tokens=1200,  # headroom for Opus 5's on-by-default adaptive thinking (max_tokens
                              # caps thinking + response together) — the 1-2 sentence output itself
                              # needs very little, but a starved budget (previously 400) let thinking
                              # alone exhaust it, leaving zero tokens for the JSON response (issue:
                              # generate-differentiation silently returning None on empty/unparseable output)
            messages=[{"role": "user",
                       "content": _TOOL_DIFFERENTIATION_PROMPT.format(
                           name=name, url=url, description=description, competitors_block=competitors_block)}],
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
            differentiation_note=str(data.get("differentiation_note", "")).strip(),
            low_confidence=low_confidence, model=model,
            input_tokens=in_tok, output_tokens=out_tok, cost_usd=cost,
        )
    except Exception:
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
            max_tokens=500,
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
    except Exception:
        return None


# Feature comparison data (search overhaul Phase 4b, extended in the
# automated-research follow-up to also draft agent_taxonomy_note in the same
# call) — drafts standalone-vs-bundled availability rows for tool_features
# plus a whole-tool agent-taxonomy summary. Unlike the sentinel-per-field
# NEEDS_VERIFICATION used elsewhere, confidence here is a boolean in the JSON
# response ("confident"): a feature row (or the agent-taxonomy verdict) is
# already one semantic unit, so there's no need for a string sentinel
# embedded in a value — see the tool_features table comment in linklib/db.py
# for the same reasoning.
_TOOL_FEATURES_PAGE_GUESSES = ("pricing", "solutions", "product", "products", "platform", "features", "ai", "agents")
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


def _fetch_feature_grounding(url: str) -> tuple[str, list[tuple[str, str, str]]]:
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
            (label.capitalize(), f"{base}/{label}") for label in _TOOL_FEATURES_PAGE_GUESSES
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


_TOOL_FEATURES_PROMPT = """You are researching a vendor listed in the CFO Toolbox's Software
directory, to power a side-by-side comparison matrix against other tools. Budget
and depth are not a constraint here — read the full page content provided below
carefully and be as thorough and specific as the material supports.

PART 1 — Agent taxonomy. Write a thorough summary (aim for 3-6 sentences, more
if there's real material to cover) of whether and how AI agents are involved in
this product. Ground this strictly in the page content below. If the vendor
names ANY specific agents anywhere in the content (e.g. "Aura," "Ember," a
"Contract Review Agent," a "flux agent") — find and name ALL of them, not just
the first one you notice; a reader comparing tools needs the complete roster
of named agents, not a sample. For each named agent, note what it actually
does if the content says so. Distinguish: a fully independent agent that runs
a workflow end-to-end, an agent-assisted feature where AI helps but a human
stays in the loop, or no real agent framing at all (generic "AI-powered"
marketing language without actual agent behavior described doesn't count as
agentic — say so plainly rather than overstating it). If the content gives no
genuine signal either way, say that rather than guessing, and set
"agent_taxonomy_confident" to false.

PART 2 — Features. Identify 8 to 15 of the product's most notable features or
capabilities — cast a wide net across everything the fetched pages describe
rather than stopping at the first handful. For each one, classify whether it's
available as a standalone purchase/add-on, only bundled into a broader plan or
tier, or both — grounded strictly in the page content provided below (or your
own reliable knowledge of the product, if the fetch came back thin). Write a
substantive note for each feature (what it does, not just its name) whenever
the content supports it. Never invent a specific pricing tier or feature you
can't support. Set "confident" to true only if the standalone/bundled
classification is clearly supported by the content provided or your own solid
knowledge — false if you're inferring or guessing. Leave out a feature
entirely if you're not even confident it exists.

Return STRICT JSON only (no prose, no markdown fences) with exactly this
shape:
{{"agent_taxonomy": {{"summary": "...", "confident": true|false}},
  "features": [
    {{"feature_name": "...", "standalone_available": true|false,
      "bundled_only": true|false,
      "notes": "short note, e.g. which tier it's on, or an empty string",
      "confident": true|false}}
]}}

Product name: {name}
Product URL: {url}
Existing directory description: {description}

{content_block}
"""


@dataclass
class ToolFeatureDraft:
    feature_name: str
    standalone_available: bool = False
    bundled_only: bool = False
    notes: str = ""
    source_url: str = ""
    needs_verification: bool = True


@dataclass
class ToolFeaturesResult:
    features: list[ToolFeatureDraft] = field(default_factory=list)
    agent_taxonomy_note: str = ""
    agent_taxonomy_needs_verification: bool = True
    low_confidence: bool = False   # no page content could be fetched at all
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


def generate_tool_features(name: str, url: str, description: str = "",
                           model: str = DEFAULT_MODEL) -> ToolFeaturesResult | None:
    """Draft standalone-vs-bundled feature rows plus an agent-taxonomy
    summary for a Software entry, in one Claude call. Grounds on the
    homepage plus its real Product/Solutions-type nav pages
    (_fetch_feature_grounding — falls back to guessed paths only if nav
    discovery finds nothing). Every returned field is a first-pass draft:
    the caller is expected to write it with needs_verification set from the
    "confident" flags and source='llm_enrichment', never auto-confirmed.
    Returns None if the SDK/key is unavailable or the call fails — same
    contract as generate_tool_description."""
    try:
        from anthropic import Anthropic
    except ImportError:
        return None
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None

    content_block, fetched = _fetch_feature_grounding(url)
    low_confidence = not fetched
    if not fetched:
        content_block = (
            f"(Could not fetch any page content for {url} — draft from your own "
            f"knowledge of {name} if you have it, keeping to the rules above.)"
        )
    # Batch-level source_url: prefer whichever fetched page is most likely to
    # actually carry tier/bundling info, pricing first.
    priority = {"Pricing": 0, "Solutions": 1, "Product": 2, "Homepage": 3}
    source_url = min(fetched, key=lambda t: priority.get(t[0], 9))[1] if fetched else ""

    prompt = _TOOL_FEATURES_PROMPT.format(
        name=name, url=url, description=description.strip() or "(none provided)",
        content_block=content_block,
    )

    try:
        client = Anthropic()
        resp = client.messages.create(
            model=model,
            max_tokens=6000,  # headroom for Opus 5's on-by-default adaptive thinking
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

        features = []
        raw_features = data.get("features") if isinstance(data, dict) else None
        if isinstance(raw_features, list):
            for item in raw_features:
                if not isinstance(item, dict):
                    continue
                feature_name = str(item.get("feature_name", "")).strip()
                if not feature_name:
                    continue
                features.append(ToolFeatureDraft(
                    feature_name=feature_name,
                    standalone_available=bool(item.get("standalone_available")),
                    bundled_only=bool(item.get("bundled_only")),
                    notes=str(item.get("notes") or "").strip(),
                    source_url=source_url,
                    needs_verification=not bool(item.get("confident")),
                ))

        agent_taxonomy = data.get("agent_taxonomy") if isinstance(data, dict) else None
        agent_taxonomy = agent_taxonomy if isinstance(agent_taxonomy, dict) else {}

        return ToolFeaturesResult(
            features=features,
            agent_taxonomy_note=str(agent_taxonomy.get("summary") or "").strip(),
            agent_taxonomy_needs_verification=not bool(agent_taxonomy.get("confident")),
            low_confidence=low_confidence, model=model,
            input_tokens=in_tok, output_tokens=out_tok, cost_usd=cost,
        )
    except Exception:
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
        f"Page content (fetched from the URL):\n{page.content[:15000]}" if not low_confidence
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
            max_tokens=6000,  # headroom for Opus 5's on-by-default adaptive thinking
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
            low_confidence=low_confidence, model=model,
            input_tokens=in_tok, output_tokens=out_tok, cost_usd=cost,
        )
    except Exception:
        return None


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
            max_tokens=1200,  # headroom for Opus 5's on-by-default adaptive thinking
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
    except Exception:
        return None


# The 11 narrative community-profile fields eligible for a voice-rewrite pass
# (scripts/import_community_profiles.py) — a style pass only, never a content
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
            max_tokens=4000,  # headroom for Opus 5's on-by-default adaptive thinking
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
    except Exception:
        return None
