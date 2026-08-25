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

# 0 is NOT a §8 hierarchy tier (§8 starts at 1) — it's this module's own
# sentinel for "the model's cited source_url doesn't match any URL we
# actually fetched as grounding," resolved by _match_source_tier below.
# Surfaced as a real, labeled state (never a bare "tier 0") specifically
# because a live first test (Mercury/Neobanking) showed it appearing in
# output with no explanation, reading as an undocumented §8 tier rather
# than what it actually is — a citation-verification gap worth a human's
# attention, not a hierarchy level to compare against 1-4.
UNCITED_TIER = 0

_TIER_LABELS = {
    1: "Changelog / release notes",
    2: "Help center / documentation",
    3: "Product page",
    4: "Press release",
    UNCITED_TIER: "Uncited (source URL not in fetched grounding set)",
}


def _domain_of(url: str) -> str:
    host = urlsplit(url if "://" in url else f"https://{url}").netloc
    return host[4:] if host.startswith("www.") else host


def _normalize_url(url: str) -> str:
    """Loose equality for matching a model-cited source_url against a
    fetched grounding hit's URL — case-insensitive scheme/host, no
    trailing slash, no fragment. NOT used for the Exa/domain-restriction
    logic above (only for the citation-matching problem below); a
    genuinely different URL (a different path, a paraphrased/hallucinated
    one) still correctly falls through to UNCITED_TIER."""
    parts = urlsplit(url.strip())
    path = parts.path.rstrip("/")
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}{path}{('?' + parts.query) if parts.query else ''}"


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


# §7's don't-collapse/unify test, verbatim from docs/FEATURE_TAXONOMY.md —
# used as few-shot grounding for BOTH Phase 3 prompts below (clustering and
# per-cluster judgment), rather than a paraphrase, so the model reasons off
# the same worked examples a human reviewer would be checking against.
_UNIFY_TEST_EXCERPT = """When two vendors' capabilities look similar, decide at the level of what
job actually gets done:

- Same job, different depth or mechanism -> ONE feature. The AI flag and
  link note carry the difference. Worked example: Automated Flux Analysis
  & Summaries is one feature across ERP, FP&A, and Close; FloQast links
  TRUE with AI=No (variance fields, manual commentary), Numeric and Ledge
  link TRUE with AI=Yes (drafted narrative).
- Different job -> DIFFERENT features, even when the vendor language
  rhymes. Worked example: Real-time Spreadsheet Sync (links to your
  existing spreadsheets) vs. Automated Working Paper Generation (generates
  live-formula workpapers). Both are "spreadsheet integration"; they are
  different features.
- Never match on shared buzzwords ("AI," "variance," "real-time"). Match
  on the outcome."""


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
SPECIFIC URL from the content below that supports the claim — copy that
URL EXACTLY as it appears in the "--- Section (URL) ---" header above the
section you're citing, character for character; never paraphrase, shorten,
or invent a URL. If the content only weakly supports a claim, or you are
inferring rather than reading it directly, set that feature's "confident"
to false.

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


def _salvage_json_array(raw: str, array_key: str) -> tuple[list, bool]:
    """Best-effort recovery from a response that got cut off mid-generation
    — a truncated tool-heavy vendor (Mercury, in the first real Neobanking
    test) can exceed even a generous max_tokens ceiling by construction,
    since Phase 0 deliberately put no cap on how many features the model
    may propose, and the same is true of a large roster's clustering/
    judgment output. Rather than losing everything to one unterminated
    string at the tail of the response, this finds the named array and
    decodes as many COMPLETE JSON values from it as possible using
    json.JSONDecoder.raw_decode's incremental parsing, stopping cleanly at
    the first truncated/malformed element.

    Returns (items, truncated). truncated is True whenever anything had to
    be salvaged this way at all — even if every element up to the cutoff
    parsed fine, the response as a whole was still incomplete. A hard
    failure with zero recoverable items still comes back as ([], True),
    never raises — matching this module's "a bad response degrades, it
    doesn't crash the run" convention elsewhere (research_vendor_domain's
    per-tier best-effort degrade). Generalized over array_key so every
    scan-tool prompt in this module (origination drafting's "features",
    clustering's "clusters", judgment's "groups") shares one salvage
    implementation rather than three near-identical copies."""
    start = raw.find(f'"{array_key}"')
    if start == -1:
        return [], True
    bracket = raw.find('[', start)
    if bracket == -1:
        return [], True

    decoder = json.JSONDecoder()
    idx = bracket + 1
    length = len(raw)
    items: list = []
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
        items.append(obj)
        idx = end
    return items, truncated


def _salvage_feature_objects(raw: str) -> tuple[list[dict], bool]:
    """Origination drafting's own salvage call — kept as a named function
    (rather than inlining _salvage_json_array(raw, "features") at every
    call site) since it's asserted against directly in
    tests/test_feature_scan.py. Filters out any salvaged item that isn't a
    dict, matching the original (pre-generalization) contract exactly."""
    items, truncated = _salvage_json_array(raw, "features")
    return [i for i in items if isinstance(i, dict)], truncated


def _anthropic_available() -> bool:
    """Cheap up-front check so a scan-tool entry point (draft/cluster/judge/
    orchestrate) can skip straight to None/an empty result without wasting
    an Exa call or building a prompt first when the SDK just isn't
    installed. _call_claude does its own `from anthropic import Anthropic`
    regardless — that's what actually raises and gets caught by each
    caller's outer try/except, so this is a fast-fail convenience, not the
    only thing guarding against a missing SDK."""
    try:
        import anthropic
    except ImportError:
        return False
    return hasattr(anthropic, "Anthropic")


def _call_claude(prompt: str, model: str, max_tokens: int):
    """Shared low-level Anthropic call — every scan-tool prompt in this
    module (origination drafting, clustering, cluster judgment) goes
    through this one function. Returns (raw_text, in_tok, out_tok, cache_w,
    cache_r). Lets any API/network exception propagate — callers turn that
    into a None return via their own outer try/except, same as every other
    generate_*() function in this codebase."""
    from anthropic import Anthropic
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


def _parse_json_array(raw: str, array_key: str) -> tuple[list, bool]:
    """Full JSON parse first; a truncated/malformed response falls back to
    _salvage_json_array rather than raising, so one bad response never
    loses everything that WAS fully generated before the cutoff."""
    try:
        val = json.loads(raw).get(array_key)
        return (val if isinstance(val, list) else []), False
    except json.JSONDecodeError:
        return _salvage_json_array(raw, array_key)


def _call_and_parse_array(prompt: str, model: str, max_tokens: int, retry_max_tokens: int,
                           array_key: str, log_label: str):
    """Shared call + parse + one-retry-on-truncation pattern (the Mercury/
    Neobanking fix), generalized so clustering and cluster-judgment reuse
    the exact same resilience machinery as origination drafting rather than
    three copies of it. Only one retry: if the retry truncates again too,
    its result is used anyway rather than looping (or silently doubling
    cost) forever — same reasoning as the original single-call-site version.

    Returns (items, truncated, in_tok, out_tok, cache_w, cache_r)."""
    raw, in_tok, out_tok, cache_w, cache_r = _call_claude(prompt, model, max_tokens)
    items, truncated = _parse_json_array(raw, array_key)
    if truncated:
        _logger.warning(
            "%s: response truncated at max_tokens=%d (salvaged %d item(s)) — retrying once "
            "at max_tokens=%d.", log_label, max_tokens, len(items), retry_max_tokens,
        )
        raw2, in_tok2, out_tok2, cache_w2, cache_r2 = _call_claude(prompt, model, retry_max_tokens)
        items2, truncated2 = _parse_json_array(raw2, array_key)
        in_tok += in_tok2
        out_tok += out_tok2
        cache_w += cache_w2
        cache_r += cache_r2
        # Prefer the retry's result outright — even if it truncated again, a
        # bigger ceiling almost always recovers strictly more complete items
        # than the first attempt did, so there's no reason to merge the two.
        items, truncated = items2, truncated2
    return items, truncated, in_tok, out_tok, cache_w, cache_r


@dataclass
class ProposedFeature:
    name: str
    definition: str = ""
    availability: str = "native"
    ai_enabled: bool = False
    confident: bool = False
    source_url: str = ""
    source_tier: int = UNCITED_TIER   # resolved from the grounding hit the model cited;
                                       # UNCITED_TIER (0) if the cited URL matches no fetched hit
    source_tier_label: str = ""       # human-readable label — always set, so a caller never has
                                       # to re-derive "what does tier N mean" from a bare int
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
    if not _anthropic_available():
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

    try:
        raw_features, truncated, in_tok, out_tok, cache_w, cache_r = _call_and_parse_array(
            prompt, model, _ORIGINATION_MAX_TOKENS, _RETRY_MAX_TOKENS, "features",
            log_label=f"draft_tool_features_for_category({category_name}/{tool_name})",
        )
        raw_features = [f for f in raw_features if isinstance(f, dict)]
        cost = compute_cost(model, in_tok, out_tok, cache_w, cache_r)

        # Keyed on the NORMALIZED hit URL so a model-cited URL that differs
        # only by trailing slash/case/fragment from the real grounding hit
        # still resolves to that hit's real tier, rather than incorrectly
        # falling through to UNCITED_TIER on a trivial formatting mismatch.
        url_to_tier = {_normalize_url(h.url): h.tier for h in hits}
        features: list[ProposedFeature] = []
        for f in raw_features:
            if not isinstance(f, dict):
                continue
            name = str(f.get("name", "")).strip()
            if not name:
                continue
            source_url = str(f.get("source_url", "")).strip()
            tier = url_to_tier.get(_normalize_url(source_url), UNCITED_TIER) if source_url else UNCITED_TIER
            features.append(ProposedFeature(
                name=name,
                definition=str(f.get("definition", "")).strip(),
                availability=f.get("availability") if f.get("availability") in ("native", "add_on") else "native",
                ai_enabled=bool(f.get("ai_enabled")),
                confident=bool(f.get("confident")),
                source_url=source_url,
                source_tier=tier,
                source_tier_label=_TIER_LABELS.get(tier, _TIER_LABELS[UNCITED_TIER]),
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


# ============================================================================
# Phase 3 (docs/FEATURE_TAXONOMY.md §10) — roster-wide accumulation, the §7
# don't-collapse/unify merge across the FULL accumulated set, and the
# feature_review_queue write. Approved shape (2026-08, Brian's sign-off):
#
# - Clustering is INCREMENTAL, folded into the per-tool research loop —
#   NOT one Claude call over the whole roster's candidate list (that
#   design shipped first and broke on the first real live run; see the
#   "Incremental clustering" section below for the full post-mortem and
#   why a bigger max_tokens ceiling wasn't the right fix). Still not
#   embeddings-similarity (would judge on surface wording — exactly what
#   §7 warns against: "never match on shared buzzwords") and still not
#   full pairwise comparison. Each incremental step is still a LOOSE
#   grouping pass — a candidate is matched to an existing representative
#   on any plausible match — because the real decision happens
#   per-cluster next, same as before.
# - Per-cluster judgment (judge_cluster) makes the actual merge-or-split
#   call, few-shot off §7's own worked examples, with an explicit
#   CONSERVATIVE bias instructed directly in the prompt: split, don't
#   merge, when genuinely uncertain. A false split is a cheap, visible
#   queue-review fix; a false merge silently buries a real distinction
#   inside a link note where it's much easier to miss.
# - Every failure mode in this pipeline (a clustering match call fails
#   outright, a judgment call fails outright, a model's output drops/
#   duplicates an index) degrades toward MORE separate features, never
#   toward losing research or silently over-merging — the same
#   conservative-bias philosophy applied structurally, not just in the
#   prompts. `OriginationSummary.clustering_degraded` makes a structural
#   clustering failure VISIBLE rather than indistinguishable from "ran
#   cleanly and genuinely found no overlap" — the exact ambiguity that
#   let the original whole-batch bug reach the queue silently.
# - Queue-write wiring reuses Library.add_feature_review_queue_item's
#   existing payload shape exactly (see approve_feature_review_queue_item's
#   docstring in linklib/db.py) — zero new surface for the approval UI.
# ============================================================================

_MATCH_MAX_TOKENS = 8000
_MATCH_RETRY_MAX_TOKENS = 16000
_JUDGE_MAX_TOKENS = 8000
_JUDGE_RETRY_MAX_TOKENS = 16000


def _format_candidate_line(i: int, tool_name: str, feature: ProposedFeature) -> str:
    definition = (feature.definition or "")[:200]
    return f"{i}. [{tool_name}] {feature.name} — {definition}"


def _validate_partition(groups: list, n: int) -> list[list[int]]:
    """Repairs a model's grouping output into a guaranteed-valid partition
    of range(n): every index 0..n-1 appears in EXACTLY one group. A model
    can produce a duplicate index (claimed by two groups) or a missing one
    (dropped entirely) — silently accepting either would mean duplicating
    or losing a real candidate. Repair, both conservative-bias-consistent:
    a duplicate keeps its FIRST group membership, dropped from any later
    one; a missing index becomes its own singleton group appended at the
    end (never dropped outright — an unmatched candidate is still a real
    candidate)."""
    seen: set[int] = set()
    repaired: list[list[int]] = []
    for group in groups:
        cleaned = [i for i in group if isinstance(i, int) and 0 <= i < n and i not in seen]
        seen.update(cleaned)
        if cleaned:
            repaired.append(cleaned)
    missing = [i for i in range(n) if i not in seen]
    repaired.extend([i] for i in missing)
    return repaired


@dataclass
class CandidateFeature:
    """One ProposedFeature accumulated across a category's whole roster —
    the input unit for clustering and judgment. verified_as_of is carried
    down from the ToolOriginationDraft it came from (Phase 2 stamps it
    per-tool, not per-feature), since a merged feature's queue payload
    needs a verified_as_of per contributing link."""
    tool_id: int
    tool_name: str
    feature: ProposedFeature
    verified_as_of: str = ""


# --- Incremental clustering (2026-08 fix, replaces a one-shot whole-roster
# clustering call) --------------------------------------------------------
# The first real live run (Neobanking, 364 candidates) produced ZERO
# merges despite obvious, repeated near-verbatim duplicates across the
# roster (e.g. "Accounting software sync" appearing 7 times). Root cause,
# confirmed from the logged call behavior: the original one-shot
# cluster_candidate_features() asked the model to emit ONE JSON array
# covering all 364 indices in a single response. Its OUTPUT size scaled
# with TOTAL roster candidate count — a fundamentally harder single-shot
# reasoning task than any per-tool drafting call (which only ever reasons
# about one vendor's own content in isolation). The first attempt's
# _salvage_json_array recovered ZERO items (not "some, then a cutoff" —
# the "clusters" key/array was never even reached), meaning Opus 5's
# on-by-default adaptive thinking consumed the ENTIRE max_tokens budget
# just reasoning about 364 items, before writing a single output token.
# Doubling the ceiling on retry is not a scalable fix for a task whose
# required reasoning length grows with roster size — a bigger category
# would just hit the same wall again.
#
# Fixed by making clustering INCREMENTAL, folded into originate_category_
# features' existing per-tool loop: as each tool's candidates are drafted,
# check them against the RUNNING set of distinct-capability representatives
# found so far (one representative per cluster), not against the whole
# roster's history. This bounds every call's OUTPUT to this ONE tool's
# candidate count (~30-50 entries, a trivial null/int array) regardless of
# how large the representative set (or total roster) grows — representative
# count only ever appears as INPUT context, which the model's context
# window holds comfortably without competing against max_tokens the way
# response generation does. match_candidates_to_representatives() below is
# the whole mechanism; originate_category_features() drives it tool by tool.


_MATCH_PROMPT = """You are checking new candidate features against a list of distinct
capabilities already found for the SAME CFO Toolbox category, "{category}",
earlier in this same origination run. §7 of the Feature Taxonomy rules doc
is the test for whether a new candidate describes the SAME underlying
capability as an existing one:

{unify_test}

Existing distinct capabilities found so far (0-indexed):
{representative_list}

New candidates to check against that list (0-indexed, SEPARATELY from the
list above):
{new_candidate_list}

For each new candidate, decide: does it plausibly describe the SAME job as
one of the existing capabilities above? If so, give that existing
capability's index. If it's genuinely new — no existing capability
plausibly matches — say so. When genuinely uncertain, prefer "new" over
guessing a match: a human reviewing the queue can merge two near-duplicate
proposals easily; a false match could bury a real distinction inside a
link note where it's much harder to catch.

Return STRICT JSON only (no prose, no markdown fences):
{{"matches": [2, null, null, 0]}}
— exactly {n_new} entries, one per new candidate above, in the same order.
Each entry is either an integer index into the existing-capabilities list,
or null if genuinely new."""


def _validate_matches(raw_matches: list, n_new: int, n_reps: int) -> list[int | None]:
    """Normalizes a match-array response into exactly n_new entries, each
    either a valid representative index or None ("new"). Short/long/
    malformed/out-of-range entries all degrade toward None — the same
    conservative-bias fallback as the rest of this module: never guess a
    match from bad data, never lose a candidate by dropping it (a missing
    entry just means "treat as new," not "vanish")."""
    result: list[int | None] = []
    for i in range(n_new):
        v = raw_matches[i] if i < len(raw_matches) else None
        # bool is a subclass of int in Python — guard so a stray true/false
        # in the response can't get misread as representative index 1/0.
        if isinstance(v, int) and not isinstance(v, bool) and 0 <= v < n_reps:
            result.append(v)
        else:
            result.append(None)
    return result


def match_candidates_to_representatives(
    new_candidates: list[CandidateFeature], representatives: list[CandidateFeature],
    category_name: str, model: str = DEFAULT_MODEL,
) -> tuple[list[int | None] | None, float]:
    """Incremental clustering step — checks ONE tool's newly drafted
    candidates against the representative set of distinct capabilities
    found so far in this run, rather than re-clustering the whole roster
    in one call (see the section header above for why the whole-batch
    design broke on a real 364-candidate run). This keeps the call's
    OUTPUT bounded by len(new_candidates), never by the total roster or
    representative count.

    Returns ([], 0.0) with no call at all when there's nothing to check
    (no new candidates) or nothing to check against yet (no representatives
    — the first tool to contribute candidates has nothing to compare to,
    so every one of its candidates is trivially new).

    Returns (None, 0.0) only if the SDK/key is unavailable or the call
    fails outright (network/auth error) — callers should fall back to
    treating every one of this tool's candidates as newly distinct (no
    merge), the same conservative-bias default the rest of this module
    uses on any clustering-stage failure."""
    if not new_candidates:
        return [], 0.0
    if not representatives:
        return [None] * len(new_candidates), 0.0
    if not _anthropic_available() or not os.environ.get("ANTHROPIC_API_KEY"):
        return None, 0.0

    representative_list = "\n".join(
        _format_candidate_line(i, c.tool_name, c.feature) for i, c in enumerate(representatives)
    )
    new_candidate_list = "\n".join(
        _format_candidate_line(i, c.tool_name, c.feature) for i, c in enumerate(new_candidates)
    )
    prompt = _MATCH_PROMPT.format(
        category=category_name, unify_test=_UNIFY_TEST_EXCERPT,
        representative_list=representative_list, new_candidate_list=new_candidate_list,
        n_new=len(new_candidates),
    )

    try:
        raw_matches, _truncated, in_tok, out_tok, cache_w, cache_r = _call_and_parse_array(
            prompt, model, _MATCH_MAX_TOKENS, _MATCH_RETRY_MAX_TOKENS, "matches",
            log_label=f"match_candidates_to_representatives({category_name})",
        )
    except Exception as e:
        _logger.warning("match_candidates_to_representatives() failed for %s: %s: %s",
                         category_name, type(e).__name__, e)
        return None, 0.0

    cost = compute_cost(model, in_tok, out_tok, cache_w, cache_r)
    matches = _validate_matches(raw_matches, len(new_candidates), len(representatives))
    return matches, cost


_JUDGE_PROMPT = """Category: "{category}". Below are {n} candidate features drafted
independently for different vendors, which a prior loose clustering pass
grouped together as PLAUSIBLY describing the same job. Make the real call
now, per §7 of the Feature Taxonomy rules doc:

{unify_test}

When genuinely uncertain whether two candidates describe the same job,
prefer keeping them SEPARATE. A human reviewing the queue can merge two
near-duplicate proposals easily; recovering a real distinction that was
silently merged away is much harder. Only merge candidates you are
CONFIDENT describe the same job by the test above.

The clustering pass groups loosely — this cluster may actually contain
more than one real distinct feature. You may partition it into as many
final groups as the candidates actually warrant.

Candidates (0-indexed, within this cluster only):
{candidate_list}

For each final group with MORE THAN ONE candidate, propose ONE canonical
name (sentence case, outcome-oriented, no vendor branding, never the word
"AI" in any form) and definition synthesizing all its candidates'
descriptions — do not just copy one vendor's wording verbatim. A group
with exactly one candidate needs no canonical name.

Return STRICT JSON only (no prose, no markdown fences):
{{"groups": [
  {{"indices": [0, 2], "merged": true, "canonical_name": "...", "canonical_definition": "...", "reasoning": "..."}},
  {{"indices": [1], "merged": false}}
]}}
Every index from 0 to {max_index} must appear in EXACTLY one group."""


@dataclass
class FeatureGroupDecision:
    indices: list[int]
    merged: bool = False
    canonical_name: str = ""
    canonical_definition: str = ""
    reasoning: str = ""


def judge_cluster(category_name: str, cluster_candidates: list[CandidateFeature],
                   model: str = DEFAULT_MODEL) -> tuple[list[FeatureGroupDecision] | None, float]:
    """Per-cluster merge-or-split judgment (§7). A cluster of 0-1
    candidates short-circuits to a free (no API call) unmerged decision —
    there's nothing to judge. Conservative bias is instructed directly in
    the prompt, not just implied, per the approved Phase 3 design.

    Returns (None, 0.0) only if the SDK/key is unavailable or the call
    fails outright; callers should fall back to treating every candidate
    in the cluster as its own separate feature — the same conservative-
    bias-consistent default cluster_candidate_features' docstring
    describes for its own failure case."""
    n = len(cluster_candidates)
    if n == 0:
        return [], 0.0
    if n == 1:
        return [FeatureGroupDecision(indices=[0], merged=False)], 0.0
    if not _anthropic_available() or not os.environ.get("ANTHROPIC_API_KEY"):
        return None, 0.0

    candidate_list = "\n".join(
        _format_candidate_line(i, c.tool_name, c.feature) for i, c in enumerate(cluster_candidates)
    )
    prompt = _JUDGE_PROMPT.format(
        category=category_name, unify_test=_UNIFY_TEST_EXCERPT, n=n,
        candidate_list=candidate_list, max_index=n - 1,
    )

    try:
        raw_groups, _truncated, in_tok, out_tok, cache_w, cache_r = _call_and_parse_array(
            prompt, model, _JUDGE_MAX_TOKENS, _JUDGE_RETRY_MAX_TOKENS, "groups",
            log_label=f"judge_cluster({category_name})",
        )
    except Exception as e:
        _logger.warning("judge_cluster() failed for %s: %s: %s", category_name, type(e).__name__, e)
        return None, 0.0

    cost = compute_cost(model, in_tok, out_tok, cache_w, cache_r)
    raw_groups = [g for g in raw_groups if isinstance(g, dict)]
    partitioned = _validate_partition([g.get("indices") or [] for g in raw_groups], n)

    # Re-associate each repaired index-group with whichever raw group
    # proposed it (by exact index-set match) so a real merge decision's
    # canonical name/definition/reasoning survives the repair pass; a
    # group _validate_partition had to invent (a dropped/duplicated index)
    # has no original decision behind it, so it defaults to unmerged.
    by_indices = {tuple(sorted(g.get("indices") or [])): g for g in raw_groups}
    decisions = []
    for indices in partitioned:
        raw = by_indices.get(tuple(sorted(indices)))
        if raw is not None and len(indices) > 1:
            decisions.append(FeatureGroupDecision(
                indices=indices, merged=bool(raw.get("merged")),
                canonical_name=str(raw.get("canonical_name", "")).strip(),
                canonical_definition=str(raw.get("canonical_definition", "")).strip(),
                reasoning=str(raw.get("reasoning", "")).strip(),
            ))
        else:
            decisions.append(FeatureGroupDecision(indices=indices, merged=False))
    return decisions, cost


@dataclass
class OriginationSummary:
    category_id: int
    category_name: str
    tools_researched: int = 0
    tools_failed: int = 0
    candidates_total: int = 0
    clusters_found: int = 0
    features_queued: int = 0
    features_merged: int = 0   # queued features backed by >1 tool's link
    features_split: int = 0    # queued features backed by exactly 1 tool's link
    queue_item_ids: list[int] = field(default_factory=list)   # empty when dry_run=True — nothing written
    queued_payloads: list[dict] = field(default_factory=list)   # always populated, dry_run or not —
                                                                  # what WAS (or WOULD BE) queued, for
                                                                  # the caller to print/inspect either way
    clustering_degraded: bool = False   # True if ANY per-tool clustering match call structurally
                                          # failed and fell back to "treat as all-new" for that
                                          # tool — makes a real failure visible instead of reading
                                          # identically to "clustering ran cleanly, found no
                                          # overlap" (the exact ambiguity the Neobanking bug hid
                                          # behind). See clustering_degraded_tools for which ones.
    clustering_degraded_tools: list[str] = field(default_factory=list)
    exa_cost_usd: float = 0.0
    claude_cost_usd: float = 0.0


def originate_category_features(
    lib, category_id: int, category_name: str, tool_roster: list[dict],
    existing_feature_names: list[str] | None = None, model: str = DEFAULT_MODEL,
    dry_run: bool = False,
) -> OriginationSummary | None:
    """Phase 3 orchestration (docs/FEATURE_TAXONOMY.md §10, origination
    mode): runs Phase 2's per-tool drafting across a category's WHOLE
    roster, clusters the accumulated candidates, applies the §7 don't-
    collapse/unify judgment per multi-member cluster, and writes the
    result to feature_review_queue (source='scan') via
    Library.add_feature_review_queue_item — never a direct table write,
    per the rules doc §9. tool_roster is [{"id", "name", "url"}, ...].

    dry_run=True runs the FULL research/cluster/judge pipeline (the
    expensive, real-API-cost part — there's no cheaper way to preview what
    this would propose, unlike a backfill script previewing already-known
    rows for free) but skips the queue write itself; every payload that
    WOULD have been written is still populated in the returned summary's
    queued_payloads for the caller to print/inspect.
    scripts/originate_category_features.py's preview/--apply convention
    threads through here rather than gating at the script layer alone, so
    a library caller gets the same guarantee.

    Returns None only if EVERY tool's research failed (nothing to
    cluster/judge/queue) — a partial-failure run (some tools succeeded)
    still returns a summary covering what did work, with tools_failed
    counting the rest."""
    roster_size = len(tool_roster)
    candidates: list[CandidateFeature] = []
    clusters: list[list[int]] = []          # global indices into `candidates`, one list per
                                             # distinct capability found so far
    representative_idx: list[int] = []      # each cluster's representative — a global index into
                                             # `candidates`, parallel to `clusters`
    tools_researched = 0
    tools_failed = 0
    exa_cost = 0.0
    claude_cost = 0.0
    clustering_degraded = False
    clustering_degraded_tools: list[str] = []

    for tool in tool_roster:
        draft = draft_tool_features_for_category(
            tool["name"], tool["url"], category_name,
            existing_feature_names=existing_feature_names, roster_size=roster_size, model=model,
        )
        if draft is None:
            tools_failed += 1
            continue
        tools_researched += 1
        exa_cost += draft.exa_cost_usd
        claude_cost += draft.cost_usd

        new_candidates = [
            CandidateFeature(tool_id=tool["id"], tool_name=tool["name"], feature=f,
                              verified_as_of=draft.verified_as_of)
            for f in draft.features
        ]
        if not new_candidates:
            continue

        start_idx = len(candidates)
        candidates.extend(new_candidates)
        new_global_indices = list(range(start_idx, len(candidates)))

        # Incremental clustering (2026-08 fix — see the module's Phase 3
        # section header for the full post-mortem): check this tool's new
        # candidates against the RUNNING representative set, not the whole
        # roster's history. Bounds every match call's output to this one
        # tool's candidate count regardless of total roster size.
        representatives = [candidates[i] for i in representative_idx]
        matches, match_cost = match_candidates_to_representatives(
            new_candidates, representatives, category_name, model=model,
        )
        claude_cost += match_cost

        if matches is None:
            # Structural failure (SDK/key unavailable, or the call itself
            # raised) — conservative-bias fallback: every one of this
            # tool's candidates becomes its own new cluster, same as a
            # clean "no matches" result would, but FLAGGED so a real
            # failure doesn't read as "genuinely found no overlap."
            clustering_degraded = True
            clustering_degraded_tools.append(tool["name"])
            _logger.warning(
                "originate_category_features(): incremental clustering match failed for "
                "%s/%s — falling back to treating all %d of its candidates as new (unmerged).",
                category_name, tool["name"], len(new_candidates),
            )
            matches = [None] * len(new_candidates)

        for local_i, gi in enumerate(new_global_indices):
            match = matches[local_i] if local_i < len(matches) else None
            if match is not None and 0 <= match < len(clusters):
                clusters[match].append(gi)
            else:
                clusters.append([gi])
                representative_idx.append(gi)

    if not candidates:
        return None

    queue_item_ids: list[int] = []
    queued_payloads: list[dict] = []
    features_merged = 0
    features_split = 0

    for cluster_indices in clusters:
        cluster_candidates = [candidates[i] for i in cluster_indices]
        decisions, judge_cost = judge_cluster(category_name, cluster_candidates, model=model)
        claude_cost += judge_cost
        if decisions is None:
            decisions = [FeatureGroupDecision(indices=[i], merged=False)
                         for i in range(len(cluster_candidates))]

        for decision in decisions:
            group = [cluster_candidates[i] for i in decision.indices
                     if 0 <= i < len(cluster_candidates)]
            if not group:
                continue

            # De-dupe by tool_id — two candidates from the SAME tool
            # landing in one merge group (that tool itself proposed
            # near-duplicate features) would otherwise silently overwrite
            # one link with the other via upsert_tool_feature_link's
            # UNIQUE(tool_id, feature_id); keep the first, log the rest.
            seen_tools: set[int] = set()
            deduped = []
            for c in group:
                if c.tool_id in seen_tools:
                    _logger.warning(
                        "originate_category_features(): dropped duplicate tool_id=%s within "
                        "one merge group for %s (%s) — that tool proposed more than one "
                        "candidate judged into the same group.",
                        c.tool_id, category_name, c.feature.name,
                    )
                    continue
                seen_tools.add(c.tool_id)
                deduped.append(c)
            group = deduped
            if not group:
                continue

            is_merged = decision.merged and len(group) > 1
            name = (decision.canonical_name if is_merged else "") or group[0].feature.name
            definition = (decision.canonical_definition if is_merged else "") or group[0].feature.definition

            links = [{
                "tool_id": c.tool_id,
                "availability": c.feature.availability,
                "ai_enabled": int(c.feature.ai_enabled),
                "verified_as_of": c.verified_as_of,
                "note": c.feature.note,
                "source_url": c.feature.source_url,
            } for c in group]

            articulation = (
                f"Merged across {len(group)} tools by the roster-wide §7 unify test. "
                f"{decision.reasoning}".strip()
                if is_merged else ""
            )

            payload = {
                "category_id": category_id,
                "feature": {"name": name, "definition": definition, "pointer_note": ""},
                "links": links,
            }
            queued_payloads.append(payload)
            if not dry_run:
                item_id = lib.add_feature_review_queue_item(
                    source="scan",
                    proposal_type=("new_feature+link" if len(links) == 1
                                   else f"new_feature+{len(links)} links"),
                    payload=payload,
                    category_id=category_id,
                    articulation=articulation,
                )
                queue_item_ids.append(item_id)
            if len(group) > 1:
                features_merged += 1
            else:
                features_split += 1

    return OriginationSummary(
        category_id=category_id, category_name=category_name,
        tools_researched=tools_researched, tools_failed=tools_failed,
        candidates_total=len(candidates), clusters_found=len(clusters),
        features_queued=len(queued_payloads), features_merged=features_merged,
        features_split=features_split, queue_item_ids=queue_item_ids,
        queued_payloads=queued_payloads,
        clustering_degraded=clustering_degraded, clustering_degraded_tools=clustering_degraded_tools,
        exa_cost_usd=exa_cost, claude_cost_usd=claude_cost,
    )


# --- Framework remap (2026-08, scripts/remap_queue_to_framework.py) --------
# A DIFFERENT job from everything above: origination mode (judge_cluster,
# match_candidates_to_representatives) invents its own groupings from
# scratch as it goes. This is matching already-queued feature_review_queue
# proposals against a FIXED, human-defined bucket list Brian reviewed and
# approved — the groupings are given, only the per-item assignment (or
# "belongs to none of them") is a judgment call. Batched (not one call for
# the whole queue) for the same reason match_candidates_to_representatives
# bounds its own call to one tool's candidates at a time: a response array
# scales with item count, and Opus 5's on-by-default adaptive thinking can
# consume an entire max_tokens budget reasoning about a large batch before
# writing a single output token (the exact failure the incremental-
# clustering fix above exists to avoid) — see this module's Phase 3 section
# header. Unlike that fix, batch size here is a simple fixed chunk (the
# framework itself is small and fixed, so there's no running-representative-
# set growth problem to solve), not a structural redesign.

@dataclass
class FrameworkBucket:
    """One human-defined target bucket from a framework file (see
    scripts/seed_data/neobanking_feature_framework.json for the shape). name
    is the exact canonical feature name to write back to a matched queue
    item's payload — never paraphrased by the model. hint is prompt-only
    disambiguation guidance, not stored anywhere."""
    index: int
    group: str
    name: str
    hint: str = ""


@dataclass
class QueueItemCandidate:
    """One pending feature_review_queue row, reduced to what the framework-
    match prompt needs — mirrors CandidateFeature's shape but keyed by queue
    item id rather than tool_id/feature, since a remap operates on already-
    queued proposals (each already representing 1+ tool links via its own
    payload["links"]), not on freshly drafted per-tool candidates."""
    item_id: int
    name: str
    definition: str


def _format_bucket_line(b: FrameworkBucket) -> str:
    hint = f" — {b.hint}" if b.hint else ""
    return f"{b.index}. [{b.group}] {b.name}{hint}"


def _format_queue_item_line(i: int, c: QueueItemCandidate) -> str:
    definition = (c.definition or "")[:220]
    return f"{i}. (queue #{c.item_id}) {c.name} — {definition}"


_FRAMEWORK_MATCH_PROMPT = """You are mapping existing draft feature proposals for the CFO Toolbox's "{category}"
category onto a FIXED, human-approved target feature list. A person has
already reviewed the raw scan output and decided exactly what buckets this
category's feature list should have — your only job is deciding which
bucket (if any) each proposal below actually belongs in, using the same
same-job test §7 of the Feature Taxonomy rules doc uses for merging:

{unify_test}

Match on what the capability actually DOES, not on surface wording — a
proposal phrased very differently from a bucket's name can still be an
exact match, and a proposal that echoes a bucket's name in different words
can still be about a genuinely different job. Some proposals will
correctly belong to NONE of the buckets below (e.g. a retail/consumer-perk,
point-of-sale, or BaaS-partner-facing capability that isn't relevant to a
CFO buyer) — that's an expected, correct outcome, not a failure to find a
match. When genuinely uncertain between two plausible buckets, or between a
bucket and "none," prefer the interpretation a human reviewer would find
easiest to correct: a wrong "none" just needs picking up in review; a wrong
match can bury a real distinction inside a merged feature where it's much
harder to catch. So when truly torn, prefer "none" over guessing.

Target buckets (fixed — 0-indexed, do not invent new ones):
{bucket_list}

Proposals to map (0-indexed, SEPARATELY from the bucket list above):
{item_list}

Return STRICT JSON only (no prose, no markdown fences):
{{"matches": [4, null, 12, 4]}}
— exactly {n_items} entries, one per proposal above, in the same order.
Each entry is either an integer index into the target-buckets list, or null
if the proposal genuinely belongs to none of them."""

_FRAMEWORK_MATCH_MAX_TOKENS = 4000
_FRAMEWORK_MATCH_RETRY_MAX_TOKENS = 8000
_FRAMEWORK_MATCH_DEFAULT_BATCH_SIZE = 50


def _validate_framework_matches(raw_matches: list, n_items: int, n_buckets: int) -> list[int | None]:
    """Same conservative-bias normalization as _validate_matches: a short/
    long/malformed/out-of-range response degrades entry-by-entry toward
    None ("no match — needs a human look"), never toward a guessed match,
    and a missing entry never silently drops an item."""
    result: list[int | None] = []
    for i in range(n_items):
        v = raw_matches[i] if i < len(raw_matches) else None
        if isinstance(v, int) and not isinstance(v, bool) and 0 <= v < n_buckets:
            result.append(v)
        else:
            result.append(None)
    return result


def match_items_to_framework(
    items: list[QueueItemCandidate], buckets: list[FrameworkBucket], category_name: str,
    model: str = DEFAULT_MODEL, batch_size: int = _FRAMEWORK_MATCH_DEFAULT_BATCH_SIZE,
) -> tuple[list[int | None] | None, float]:
    """Matches every item in `items` against the fixed `buckets` list, in
    chunks of `batch_size` (each batch sees the FULL bucket list every time
    — only the item side is chunked, since the bucket list is small and
    fixed and every batch needs the complete target set to match against).

    Returns (matches, total_cost_usd) where matches is a list parallel to
    `items`, each entry an index into `buckets` or None. Returns (None, 0.0)
    only if the SDK/key is unavailable OR any batch's call fails outright —
    a partial result (some batches succeeded, one didn't) is deliberately
    NOT returned as a mix of real matches and silent Nones, since a caller
    can't tell "genuinely no match" from "this batch's call errored" in
    that shape; better to surface the failure and let the caller retry the
    whole thing than to silently under-map a fraction of the queue."""
    if not items:
        return [], 0.0
    if not _anthropic_available() or not os.environ.get("ANTHROPIC_API_KEY"):
        return None, 0.0

    bucket_list = "\n".join(_format_bucket_line(b) for b in buckets)
    total_cost = 0.0
    all_matches: list[int | None] = []

    for start in range(0, len(items), batch_size):
        batch = items[start:start + batch_size]
        item_list = "\n".join(_format_queue_item_line(i, c) for i, c in enumerate(batch))
        prompt = _FRAMEWORK_MATCH_PROMPT.format(
            category=category_name, unify_test=_UNIFY_TEST_EXCERPT,
            bucket_list=bucket_list, item_list=item_list, n_items=len(batch),
        )
        try:
            raw_matches, _truncated, in_tok, out_tok, cache_w, cache_r = _call_and_parse_array(
                prompt, model, _FRAMEWORK_MATCH_MAX_TOKENS, _FRAMEWORK_MATCH_RETRY_MAX_TOKENS,
                "matches", log_label=f"match_items_to_framework({category_name}, batch {start})",
            )
        except Exception as e:
            _logger.warning("match_items_to_framework() failed for %s at batch offset %d: %s: %s",
                             category_name, start, type(e).__name__, e)
            return None, total_cost

        total_cost += compute_cost(model, in_tok, out_tok, cache_w, cache_r)
        all_matches.extend(_validate_framework_matches(raw_matches, len(batch), len(buckets)))

    return all_matches, total_cost


_DEFINITION_SYNTH_PROMPT = """The CFO Toolbox's "{category}" category has a confirmed target feature named
"{bucket_name}". The candidate definitions below were independently drafted
for different vendors and have all been mapped onto this ONE bucket by an
earlier matching step. Write ONE synthesized definition for "{bucket_name}"
that reflects what this capability means across the roster, following
docs/FEATURE_TAXONOMY.md §3: outcome-oriented, plain language, never the
word "AI" in any form, no vendor branding or product names. Do not just
copy one vendor's wording verbatim — genuinely synthesize.

Candidate definitions being merged into this one bucket:
{definition_list}

Return STRICT JSON only (no prose, no markdown fences):
{{"definition": "..."}}"""

_DEFINITION_SYNTH_MAX_TOKENS = 1200   # MIN_GENERATE_MAX_TOKENS floor (_checked_max_tokens) — a
                                       # short definition response doesn't need much of this, but
                                       # Opus 5's on-by-default adaptive thinking shares the same
                                       # budget, so a thinner ceiling can starve the response text.


def synthesize_bucket_definition(
    bucket_name: str, contributing_definitions: list[str], category_name: str,
    model: str = DEFAULT_MODEL,
) -> tuple[str, float]:
    """Best-effort synthesis of one merged bucket's definition text from
    every contributing item's own definition — the bucket NAME is always the
    framework's fixed canonical name (never synthesized), only the
    definition needs merging across multiple vendors' independently drafted
    text. Falls back to the single longest non-empty contributing
    definition (zero cost, no call) whenever the SDK/key is unavailable or
    the call fails outright — same conservative "never block the run on a
    synthesis-step failure" degrade as every other best-effort call in this
    module; a slightly-less-polished definition is a far smaller cost than
    losing the whole remap to a transient API error."""
    fallback = max(
        (d.strip() for d in contributing_definitions if d and d.strip()), key=len, default="",
    )
    if not _anthropic_available() or not os.environ.get("ANTHROPIC_API_KEY"):
        return fallback, 0.0
    definition_list = "\n".join(f"- {d.strip()}" for d in contributing_definitions if d and d.strip())
    if not definition_list:
        return fallback, 0.0

    prompt = _DEFINITION_SYNTH_PROMPT.format(
        category=category_name, bucket_name=bucket_name, definition_list=definition_list,
    )
    try:
        raw, in_tok, out_tok, cache_w, cache_r = _call_claude(prompt, model, _DEFINITION_SYNTH_MAX_TOKENS)
    except Exception as e:
        _logger.warning("synthesize_bucket_definition() failed for %r: %s: %s",
                         bucket_name, type(e).__name__, e)
        return fallback, 0.0

    cost = compute_cost(model, in_tok, out_tok, cache_w, cache_r)
    try:
        definition = str(json.loads(raw).get("definition", "")).strip()
    except json.JSONDecodeError:
        definition = ""
    return (definition or fallback), cost
