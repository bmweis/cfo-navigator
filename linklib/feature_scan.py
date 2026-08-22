"""Feature Taxonomy scan tool (docs/FEATURE_TAXONOMY.md §10) — Phase 2:
per-tool, vendor-domain-scoped research + Claude drafting for origination
mode. Approved shape (2026-08 Phase 0/1 investigation, Brian's sign-off):

- Exa search restricted to the vendor's OWN domain (includeDomains=[domain])
  — deliberately NOT linklib.agent.retrieve_exa's OPML-trusted-sites
  allowlist. A vendor's changelog/docs/product pages almost always live on
  the vendor's own domain, and §8's sourcing hierarchy needs exactly that,
  not Brian's curated third-party site list.
- §8's sourcing hierarchy (changelogs > help centers > product pages > press
  releases > independent reviews > vendor-authored comparisons) is
  approximated by running one domain-restricted Exa search per tier query
  in turn and tagging each hit with an inferred tier from its URL path,
  since Exa's search index doesn't return a source-tier of its own. Tiers
  5-6 are inherently mostly off the vendor's own domain (a third-party
  review site, an independent analyst writeup) and are out of scope for a
  domain-restricted search by construction — a future freshness-mode pass
  could widen to an unrestricted search for those tiers if it turns out to
  matter in practice; flagged here rather than silently narrowed.
- No queue writes here. Phase 2 is the per-tool drafting function only,
  meant to be run manually against 1-2 tools per category with the output
  printed/inspected for quality, not queued. Phase 3 (out of scope in this
  module) accumulates this across a category's whole roster, applies the
  §7 don't-collapse/unify merge across that FULL accumulated set (per §10's
  now-explicit origination-mode rule), and writes to feature_review_queue
  via Library.add_feature_review_queue_item — never a direct table write.

Reuses linklib.enrich's Anthropic-call idiom (try/except ImportError, a
DEFAULT_MODEL constant, a *Draft dataclass, JSON-only prompt, real
pricing.compute_cost accounting) and linklib.agent's Exa-call idiom (a
best-effort call that degrades to no hits on any failure, real
pricing.compute_exa_cost accounting) rather than inventing new conventions
for either half.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import urlsplit

import requests

from .enrich import DEFAULT_MODEL, _checked_max_tokens
from .pricing import compute_cost, compute_exa_cost

_logger = logging.getLogger(__name__)

EXA_SEARCH_URL = "https://api.exa.ai/search"
EXA_TIMEOUT_SECONDS = 15.0

# §8 sourcing hierarchy, approximated by biasing each search query toward a
# tier and tagging hits by URL path keyword. Iterated in hierarchy order so
# grounding content is presented to Claude strongest-first (matching the
# prompt's own "changelogs first" instruction), and so a URL that would
# match more than one tier's keywords keeps the stronger (lower-numbered)
# tier it was found under first.
_SOURCE_TIER_QUERIES = [
    (1, "changelog release notes new features",
     ("changelog", "release-notes", "releases", "whats-new", "updates")),
    (2, "help center documentation user guide",
     ("help", "docs", "support", "documentation", "kb", "guide")),
    (3, "product features pricing plans",
     ("product", "features", "pricing", "solutions")),
    (4, "press release announcement",
     ("press", "news", "blog")),
]

_TIER_LABELS = {
    1: "Changelog / release notes",
    2: "Help center / documentation",
    3: "Product page",
    4: "Press release",
    0: "Unclassified",
}


def _domain_of(url: str) -> str:
    host = urlsplit(url if "://" in url else f"https://{url}").netloc
    return host[4:] if host.startswith("www.") else host


def _infer_tier(url: str, requested_tier: int) -> int:
    path = urlsplit(url).path.lower()
    for tier, _query, keywords in _SOURCE_TIER_QUERIES:
        if any(kw in path for kw in keywords):
            return tier
    return requested_tier


@dataclass
class GroundingHit:
    title: str
    url: str
    text: str
    tier: int   # §8 hierarchy tier this hit was found/classified under (1-4; 0 = unclassified)


def research_vendor_domain(tool_name: str, tool_url: str, max_results_per_query: int = 3
                           ) -> tuple[list[GroundingHit], float]:
    """§8-ordered, vendor-domain-scoped Exa search for one tool.

    Returns (hits, cost_usd). Best-effort by design, matching
    agent.retrieve_exa's contract: no EXA_API_KEY, a network failure, or a
    malformed response all degrade silently rather than raising — a scan
    across a whole roster shouldn't die because one tool's domain search
    failed.

    Runs one Exa call per §8 tier query (changelog, help center, product,
    press), each restricted to the tool's own domain via includeDomains,
    then dedupes by URL — the loop runs in hierarchy order, so a URL
    surfaced by both the changelog and product-page query keeps its
    stronger tier-1 classification.
    """
    api_key = os.environ.get("EXA_API_KEY")
    domain = _domain_of(tool_url)
    if not api_key or not domain:
        return [], 0.0

    seen_urls: set[str] = set()
    hits: list[GroundingHit] = []
    total_cost = 0.0

    for tier, query_bias, _keywords in _SOURCE_TIER_QUERIES:
        payload = {
            "query": f"{tool_name} {query_bias}",
            "numResults": max_results_per_query,
            "includeDomains": [domain],
            "contents": {"text": True},
        }
        try:
            resp = requests.post(
                EXA_SEARCH_URL,
                headers={"x-api-key": api_key, "Content-Type": "application/json"},
                json=payload,
                timeout=EXA_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            continue

        results = data.get("results") or []
        total_cost += compute_exa_cost("search", num_results=len(results))
        for r in results:
            if not isinstance(r, dict):
                continue
            url = r.get("url")
            text = (r.get("text") or "").strip()
            if not url or url in seen_urls or not text:
                continue
            seen_urls.add(url)
            hits.append(GroundingHit(
                title=r.get("title") or url, url=url, text=text[:6000],
                tier=_infer_tier(url, tier),
            ))

    hits.sort(key=lambda h: h.tier)
    return hits, total_cost


# Compact restatement of the rules doc's naming/curation/designation rules
# (§3, §4, §5, §6) for the prompt — not the whole document, since most of
# §1/§2/§7-11 is either schema/process context the model doesn't need to
# produce one tool's draft, or (§7's merge test) something the CALLER
# applies across the accumulated roster, not something one tool's drafting
# call can do on its own with no visibility into the rest of the roster.
_RULES_EXCERPT = """- A feature names the job done from the BUYER's perspective, never the
  vendor's branding or implementation (no product names, no trademarked
  terms).
- Never use "AI," "AI-powered," "AI-driven," or "agentic" anywhere in a
  feature NAME — AI-ness is the separate ai_enabled flag below, never part
  of the name.
- Prefer short names; put a qualifier, comparison point, or scope caveat in
  the definition field, not folded into the name.
- Sentence case: capitalize only the first word (plus any acronym/proper
  noun that's always capitalized): "Automated journal entry creation," not
  "Automated Journal Entry Creation."
- Only propose a feature if it's a genuine differentiator (vendors in this
  category split on it), a piece of table stakes worth confirming (everyone
  credible has it, buyers want the baseline verified), or a genuine
  standout (a novel capability). Skip marketing filler and micro-features
  nobody actually decides on.
- Product features only — never vendor services (onboarding, support
  staffing) or commercial terms (pricing model, contract terms).
- availability is exactly "native" (included in the standard offering) or
  "add_on" (costs more: paid module, upgrade, or higher-tier gating).
- ai_enabled is true only when the capability is delivered via an
  intelligent/learning/agentic approach rather than deterministic rules —
  a buyer-relevant "smart vs. rules-based" signal, deliberately coarse.
- Ground every claim in the vendor content provided below. A claim you
  cannot point to a specific line/section for should be marked
  confident=false, not asserted as true."""


_ORIGINATION_PROMPT = """You are researching a vendor for the CFO Toolbox's Feature Taxonomy. This is
ORIGINATION MODE: the "{category}" category has no curated feature list
yet, so you are proposing candidate features grounded in this ONE vendor's
real content below. A later step reconciles your proposals against every
other vendor's in the same category before anything reaches human review —
your job here is just this one vendor's honest, well-sourced draft.

Rules (follow exactly):
{rules_excerpt}

Existing curated feature names already confirmed for "{category}" (if this
vendor's content maps to one of these, reuse the EXACT SAME name rather
than inventing a near-duplicate under different wording): {existing_names}

Propose every candidate feature this vendor's content genuinely supports
that clears the bar above — {roster_note}. Do not cap yourself at any
particular count; a later human review is the actual curation gate, not
you.

For each candidate feature, decide availability, ai_enabled, and cite the
SPECIFIC URL from the content below that supports the claim. If the
content only weakly supports a claim, or you are inferring rather than
reading it directly, set that feature's "confident" to false.

Vendor: {tool_name} ({tool_url})

--- Vendor content ---
{content_block}
--- end vendor content ---

Return STRICT JSON only (no prose, no markdown fences):
{{"features": [{{"name": "...", "definition": "...", "availability": "native"|"add_on",
"ai_enabled": true|false, "confident": true|false, "source_url": "...", "note": "..."}}, ...]}}

If the content above gives no genuine signal to propose ANY feature for
this vendor, return {{"features": []}}."""


# A vendor with a genuinely large feature surface (the "no cap on proposed
# feature count" Phase 0 rule is deliberate — see the prompt below) can
# produce a long JSON response; Opus 5's on-by-default adaptive thinking
# shares this same budget with the response text, so a thin ceiling starves
# the response before the model finishes the JSON. 8000 is sized well above
# generate_community_profile's 6000 (23 fields drafted in one call) even
# though this call drafts fewer named fields per feature, since an
# uncapped-length array is the whole point here. _RETRY_MAX_TOKENS is the
# one-shot retry ceiling used only when the first attempt still truncated —
# see _salvage_feature_objects and the retry logic in
# draft_tool_features_for_category below.
_ORIGINATION_MAX_TOKENS = 8000
_RETRY_MAX_TOKENS = 16000


def _salvage_feature_objects(raw: str) -> tuple[list[dict], bool]:
    """Best-effort recovery from a response that got cut off mid-generation
    — a truncated tool-heavy vendor (Mercury, in the first real Neobanking
    test) can exceed even a generous max_tokens ceiling by construction,
    since Phase 0 deliberately put no cap on how many features the model
    may propose. Rather than losing the whole tool's research to one
    unterminated string at the tail of the response, this finds the
    "features" array and decodes as many COMPLETE JSON objects from it as
    possible using json.JSONDecoder.raw_decode's incremental parsing,
    stopping cleanly at the first truncated/malformed element.

    Returns (features, truncated). truncated is True whenever anything had
    to be salvaged this way at all — even if every element up to the cutoff
    parsed fine, the response as a whole was still incomplete. A hard
    failure with zero recoverable objects still comes back as ([], True),
    never raises — matching this module's "a bad response degrades, it
    doesn't crash the run" convention elsewhere (research_vendor_domain's
    per-tier best-effort degrade)."""
    start = raw.find('"features"')
    if start == -1:
        return [], True
    bracket = raw.find('[', start)
    if bracket == -1:
        return [], True

    decoder = json.JSONDecoder()
    idx = bracket + 1
    length = len(raw)
    features: list[dict] = []
    truncated = False
    while idx < length:
        while idx < length and raw[idx] in " \t\n\r,":
            idx += 1
        if idx >= length or raw[idx] == "]":
            break
        try:
            obj, end = decoder.raw_decode(raw, idx)
        except json.JSONDecodeError:
            truncated = True
            break
        if isinstance(obj, dict):
            features.append(obj)
        idx = end
    return features, truncated


@dataclass
class ProposedFeature:
    name: str
    definition: str = ""
    availability: str = "native"
    ai_enabled: bool = False
    confident: bool = False
    source_url: str = ""
    source_tier: int = 0   # resolved from the grounding hit the model cited, 0 if unresolved
    note: str = ""


@dataclass
class ToolOriginationDraft:
    tool_name: str
    tool_url: str
    category_name: str
    features: list[ProposedFeature] = field(default_factory=list)
    grounding_sources: list[GroundingHit] = field(default_factory=list)
    low_confidence: bool = False   # no vendor-domain content could be found/fetched at all
    truncated: bool = False        # the response was cut off mid-generation (even after the
                                    # one retry) and features were recovered via
                                    # _salvage_feature_objects rather than a clean full parse —
                                    # the recovered list may be missing whatever the model
                                    # would have proposed after the cutoff point
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0          # Claude cost only — see exa_cost_usd for the search cost
    exa_cost_usd: float = 0.0
    verified_as_of: str = ""       # date this research actually ran — a sensible default for
                                    # the eventual tool_feature_links.verified_as_of; the queue
                                    # payload it feeds (Phase 3, not built here) still lets an
                                    # admin adjust it at edit-then-approve time like any field.


def draft_tool_features_for_category(
    tool_name: str, tool_url: str, category_name: str,
    existing_feature_names: list[str] | None = None,
    roster_size: int = 0,
    model: str = DEFAULT_MODEL,
) -> ToolOriginationDraft | None:
    """Origination-mode research + drafting for ONE tool in ONE category
    (docs/FEATURE_TAXONOMY.md §10). Returns None if the SDK/key is
    unavailable or the Claude call fails outright — never raises.

    Grounds on research_vendor_domain()'s vendor-domain-scoped Exa search
    (§8 sourcing hierarchy tiers 1-4 — see module docstring for why tiers
    5-6 are out of scope for a domain-restricted search). When Exa finds
    nothing (no EXA_API_KEY, a blocked/empty domain, or a genuinely thin
    web presence), the draft still runs against the model's own knowledge
    with low_confidence=True, matching generate_tool_description's
    existing convention for a failed-fetch grounding gap — a
    low_confidence draft is still returned, not skipped, so the caller can
    decide how much weight to give it.

    roster_size is the category's FULL tool count (not how many tools
    you've researched so far in a run) — below 4, the prompt drops the
    differentiator criterion per the rules doc's thin-roster rule, since
    "vendors split on it" isn't a meaningful test with too few vendors to
    compare. Pass 0 (or omit) when the roster size isn't known yet — the
    prompt then applies the normal (non-thin) framing.

    Makes NO writes of any kind — this is the drafting function only.
    Phase 3 (out of scope here) accumulates this across a category's whole
    roster, applies the §7 don't-collapse merge across that full
    accumulated set, and writes to feature_review_queue."""
    try:
        from anthropic import Anthropic
    except ImportError:
        return None
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None

    hits, exa_cost = research_vendor_domain(tool_name, tool_url)
    low_confidence = not hits
    if hits:
        content_block = "\n\n".join(
            f"--- {_TIER_LABELS.get(h.tier, 'Unclassified')} ({h.url}) ---\n{h.text}"
            for h in hits
        )[:60000]
    else:
        content_block = (
            "(No vendor-domain content could be found or fetched — draft from your own "
            "knowledge of this vendor if you have it, keeping to the rules above, and set "
            "every feature's \"confident\" to false unless you are genuinely certain.)"
        )

    existing_names = ", ".join(existing_feature_names or []) or (
        "(none yet — this is the first tool researched for this category)"
    )
    if roster_size and roster_size < 4:
        roster_note = (
            f"this category has only {roster_size} tools in its roster so far, which is too "
            "few to meaningfully judge whether vendors 'split on' a capability — SKIP the "
            "differentiator criterion and propose only table-stakes-worth-confirming and "
            "standout features"
        )
    else:
        roster_note = (
            "including candidates that may turn out to be differentiators once compared "
            "against the rest of the category's roster"
        )

    prompt = _ORIGINATION_PROMPT.format(
        category=category_name, rules_excerpt=_RULES_EXCERPT,
        existing_names=existing_names, roster_note=roster_note,
        tool_name=tool_name, tool_url=tool_url, content_block=content_block,
    )

    def _call(max_tokens: int):
        """One Anthropic call. Returns (raw_text, in_tok, out_tok, cache_w,
        cache_r). Lets any API/network exception propagate — the outer
        try/except below is what turns that into a None return, same as
        every other generate_*() function in this codebase."""
        client = Anthropic()
        resp = client.messages.create(
            model=model,
            max_tokens=_checked_max_tokens(max_tokens),
            messages=[{"role": "user", "content": prompt}],
        )
        raw = "".join(block.text for block in resp.content if getattr(block, "type", None) == "text")
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        usage = getattr(resp, "usage", None)
        return (
            raw,
            getattr(usage, "input_tokens", 0) or 0,
            getattr(usage, "output_tokens", 0) or 0,
            getattr(usage, "cache_creation_input_tokens", 0) or 0,
            getattr(usage, "cache_read_input_tokens", 0) or 0,
        )

    def _parse(raw: str) -> tuple[list[dict], bool]:
        """Full JSON parse first; a truncated/malformed response (the
        Mercury/Neobanking case — a feature-rich vendor's uncapped-length
        proposal list ran past max_tokens and got cut off mid-string) falls
        back to _salvage_feature_objects rather than raising, so one bad
        response never loses a whole tool's research."""
        try:
            return (json.loads(raw).get("features") or []), False
        except json.JSONDecodeError:
            return _salvage_feature_objects(raw)

    try:
        raw, in_tok, out_tok, cache_w, cache_r = _call(_ORIGINATION_MAX_TOKENS)
        raw_features, truncated = _parse(raw)

        # One retry at a higher ceiling if the FIRST attempt truncated — a
        # generous but still-finite max_tokens can still not be enough for
        # a genuinely large roster's worth of features on some vendors.
        # Only one retry: if it truncates again too, use whatever it
        # salvaged rather than looping (or silently doubling cost) forever.
        # Token/cost accounting below sums BOTH calls when a retry happens,
        # since both were real spend against this tool's research.
        if truncated:
            _logger.warning(
                "draft_tool_features_for_category(): response for %s/%s truncated at "
                "max_tokens=%d (salvaged %d feature(s)) — retrying once at max_tokens=%d.",
                category_name, tool_name, _ORIGINATION_MAX_TOKENS, len(raw_features), _RETRY_MAX_TOKENS,
            )
            raw2, in_tok2, out_tok2, cache_w2, cache_r2 = _call(_RETRY_MAX_TOKENS)
            raw_features2, truncated2 = _parse(raw2)
            in_tok += in_tok2
            out_tok += out_tok2
            cache_w += cache_w2
            cache_r += cache_r2
            # Prefer the retry's result outright — even if it truncated
            # again, a bigger ceiling almost always recovers strictly more
            # complete features than the first attempt did, so there's no
            # reason to merge the two rather than just taking the better one.
            raw_features, truncated = raw_features2, truncated2

        cost = compute_cost(model, in_tok, out_tok, cache_w, cache_r)

        url_to_tier = {h.url: h.tier for h in hits}
        features: list[ProposedFeature] = []
        for f in raw_features:
            if not isinstance(f, dict):
                continue
            name = str(f.get("name", "")).strip()
            if not name:
                continue
            source_url = str(f.get("source_url", "")).strip()
            features.append(ProposedFeature(
                name=name,
                definition=str(f.get("definition", "")).strip(),
                availability=f.get("availability") if f.get("availability") in ("native", "add_on") else "native",
                ai_enabled=bool(f.get("ai_enabled")),
                confident=bool(f.get("confident")),
                source_url=source_url,
                source_tier=url_to_tier.get(source_url, 0),
                note=str(f.get("note", "")).strip(),
            ))

        return ToolOriginationDraft(
            tool_name=tool_name, tool_url=tool_url, category_name=category_name,
            features=features, grounding_sources=hits, low_confidence=low_confidence,
            truncated=truncated, model=model, input_tokens=in_tok, output_tokens=out_tok,
            cost_usd=cost, exa_cost_usd=exa_cost, verified_as_of=date.today().isoformat(),
        )
    except Exception as e:
        _logger.warning("draft_tool_features_for_category() failed for %s/%s: %s: %s",
                         category_name, tool_name, type(e).__name__, e)
        return None
