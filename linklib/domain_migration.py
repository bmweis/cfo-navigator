"""Known-domain-migration fetch tier — Phase 5b follow-up #2.

Tried BEFORE the Wayback Machine fallback (linklib.wayback), for a URL whose
host is a confirmed migrated domain (linklib.pipeline._DOMAIN_MIGRATIONS —
e.g. pointsandfigures.com -> jeffreycarter.substack.com, avc.com -> avc.xyz).
This is NOT a general search fallback: it's only trusted because the
destination domain has already been confirmed (by a human, with a live
check) as the legitimate continuation of that specific source — see
_DOMAIN_MIGRATIONS' own comment in linklib/pipeline.py for what "confirmed"
means there.

Reuses the same Exa integration FP&A Buddy's retrieve_exa() already uses
(linklib.agent) — same endpoint, same EXA_API_KEY / exa_enabled kill switch,
a plain requests.post to Exa's REST API — restricted via includeDomains to
just the one destination domain, searching for the article's stored TITLE
(not the dead URL, which by definition doesn't resolve on the new domain) so
a real candidate can be found.

A hit isn't accepted blind: find_migrated_url() only returns a candidate
whose own title is plausibly the same article (see _titles_match) — a page
merely existing on the new domain isn't enough. The caller
(linklib.pipeline) still runs the candidate through the identical
extract.assess_extraction_quality() sanity check a direct fetch or a Wayback
snapshot has to clear — this module only finds a candidate URL, it never
decides the content is good.
"""
from __future__ import annotations

import os
import re

import requests

_EXA_SEARCH_URL = "https://api.exa.ai/search"
_TIMEOUT = 10.0

# A migrated post's title is sometimes lightly reformatted by the new
# platform (e.g. Substack re-cases/re-punctuates a title on import), so an
# exact-string match would miss real hits — but "a page merely exists" isn't
# a real check either. 0.7 word-overlap is a forgiving middle ground: high
# enough to reject an unrelated post on the same blog, low enough to survive
# minor re-punctuation/re-casing.
_TITLE_MATCH_THRESHOLD = 0.7


def _normalize_title(title: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    t = re.sub(r"[^\w\s]", " ", (title or "").lower())
    return re.sub(r"\s+", " ", t).strip()


def _titles_match(a: str, b: str) -> bool:
    """True if two titles are plausibly the same article — word-overlap
    ratio, not exact match (see module docstring for why)."""
    na, nb = _normalize_title(a), _normalize_title(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    words_a, words_b = set(na.split()), set(nb.split())
    if not words_a or not words_b:
        return False
    overlap = len(words_a & words_b) / max(len(words_a), len(words_b))
    return overlap >= _TITLE_MATCH_THRESHOLD


def find_migrated_url(lib, new_domain: str, title: str) -> tuple[str | None, float]:
    """Search Exa, restricted to `new_domain`, for the given article title.
    Returns (url_or_None, cost_usd) — the best title-matching result's URL
    (or None on a miss), and the real compute_exa_cost() figure for the
    call (2026-09, Exa cost-tracking foundation — the same "no estimation"
    discipline linklib.agent.retrieve_exa already follows: billed on real
    results delivered, not the number requested). cost_usd is 0.0 whenever
    Exa was never actually billed — a missing EXA_API_KEY, Exa disabled via
    the admin toggle, a blank title, a network failure, or a non-200/
    malformed response — never raises, same best-effort contract as
    linklib.wayback and linklib.agent.retrieve_exa. `lib` may be None
    (falls back to "enabled"), matching agent._web_provider's own
    convention."""
    api_key = os.environ.get("EXA_API_KEY")
    if not api_key or not title.strip() or not new_domain:
        return None, 0.0
    if lib is not None and not lib.get_exa_enabled():
        return None, 0.0

    try:
        resp = requests.post(
            _EXA_SEARCH_URL,
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
            json={"query": title, "numResults": 5, "includeDomains": [new_domain]},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return None, 0.0

    from .pricing import compute_exa_cost
    results = data.get("results") or []
    cost = compute_exa_cost("search", num_results=len(results))
    for r in results:
        if not isinstance(r, dict):
            continue
        url = r.get("url") or ""
        if url and _titles_match(title, r.get("title") or ""):
            return url, cost
    return None, cost
