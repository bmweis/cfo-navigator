"""Medium-platform fetch tier — Phase 1 build, following three diagnostic
rounds (see CLAUDE.md's Medium-platform investigation bullets and
scripts/medium_platform_scale_check.py) that validated the approach against
real production data before any of this shipped.

Tried BEFORE the Wayback fallback for a URL on a recognized Medium-platform
host (linklib.pipeline._finish_backfill_after_direct_failure) — Wayback is
currently unreliable due to archive.org-side 429 rate-limiting (see
linklib.wayback's module docstring), and this tier's hit rate on the
diagnostic sample was strong enough to justify going first.

Host recognition is suffix-based, not an exact-string list — the second
diagnostic round found author subdomains (pt.medium.com,
samirkaji.medium.com) slipping through an exact-match carve-out to a
guaranteed-403 live-refetch. `is_medium_platform_host()` checks
`medium.com` itself, any `*.medium.com` subdomain, and a short,
hand-confirmed set of custom domains that are actually Medium underneath
(bothsidesofthetable.com) — structured so a newly-discovered custom domain
is a one-line data addition to `_MEDIUM_CUSTOM_DOMAINS`, never a logic
change.

Candidate search reuses the exact Exa integration linklib.domain_migration
already uses (same endpoint, same EXA_API_KEY/exa_enabled kill switch, same
plain requests.post to Exa's REST API) — but deliberately UNRESTRICTED (no
includeDomains), since Medium articles resolve to many different hosts
(Inc.com, TechCrunch, a custom Substack, medium.com itself), unlike the
domain-migration tier's single confirmed destination. Every candidate is
validated through linklib.domain_migration._titles_match() (reused, not
reimplemented, 0.7 word-overlap threshold) before acceptance — the exact
check that would have caught (and, once reused here, does catch) the first
diagnostic spike's real false-positive match.

Phase 2 (2026-08, "wrap-up sprint" item 1) — fetch-by-URL before
search-by-title. Production evidence from the manual-review corrected-URL
workflow (see CLAUDE.md's manual-review/URL-correction bullets) surfaced
~20 stuck articles whose exact, human-confirmed URL is already known (the
corrected URL itself lives on a recognized blocked host) — for those,
searching Exa by title is strictly worse than just asking Exa to fetch that
exact URL: a generic title can find the wrong candidate or no candidate at
all, and there's no ambiguity to resolve when the URL is already exact.
`fetch_content_by_url()` calls Exa's `/contents` endpoint (not `/search`) for
the article's own current URL — tried first in
`linklib.pipeline._try_medium_platform`, falling through to the existing
search-by-title flow on a miss or a too-thin result. A URL-fetch success
needs no title-match validation (there's no candidate to disambiguate
between — see `_try_medium_platform`), unlike a search-by-title hit.

That same production evidence also surfaced a host
(`shockwaveinnovations.com`) that's Cloudflare-blocked exactly like the
Medium-platform hosts but genuinely isn't Medium underneath — it doesn't
belong in `_MEDIUM_CUSTOM_DOMAINS` (that set means "confirmed running on
Medium's publishing platform," a factual claim this host doesn't meet), but
it does belong in the same "Exa's /contents endpoint can bypass this host's
block" bucket the whole fetch-by-URL path exists for. `_OTHER_BLOCKED_HOSTS`
holds it, kept honestly separate from the Medium-specific set;
`is_recognized_blocked_host()` is the union of the two and is what the
fetch-by-URL path (and the same-domain carve-out below it) actually gates
on — `is_medium_platform_host()` itself is unchanged and still means
exactly what it always meant.
"""
from __future__ import annotations

import os
from urllib.parse import urlsplit

import requests

# Custom domains confirmed to actually be Medium underneath (not a
# medium.com subdomain, so the suffix check below doesn't catch them).
# Deliberately short and hand-curated, same discipline as
# linklib.pipeline._DEFUNCT_SERVICE_DOMAINS/_DOMAIN_MIGRATIONS — each entry
# requires a live confirmation before being added.
#
#   bothsidesofthetable.com — Mark Suster's blog, confirmed running on
#     Medium's publishing platform via its URL slug format (a Medium
#     post-ID hash suffix, e.g. "-a61764e87626") — Phase 0 investigation.
_MEDIUM_CUSTOM_DOMAINS = frozenset({
    "bothsidesofthetable.com",
})

# Hosts confirmed Cloudflare-blocked the same way the Medium-platform hosts
# are, so they're worth the same Exa-based fetch-by-URL recovery path — but
# NOT confirmed to actually be Medium underneath, so they're kept out of
# _MEDIUM_CUSTOM_DOMAINS rather than mislabeled. Each entry requires a live
# confirmation before being added, same discipline as _MEDIUM_CUSTOM_DOMAINS
# and linklib.pipeline._DEFUNCT_SERVICE_DOMAINS/_DOMAIN_MIGRATIONS.
#
#   shockwaveinnovations.com — 5 articles with rich live content stuck as
#     too-thin from bad Wayback snapshots; live page loads fine in a
#     browser but 403s this tool's fetcher (Cloudflare fingerprint block,
#     same as the Medium-platform hosts) — 2026-08 wrap-up sprint.
_OTHER_BLOCKED_HOSTS = frozenset({
    "shockwaveinnovations.com",
})

_EXA_SEARCH_URL = "https://api.exa.ai/search"
_EXA_CONTENTS_URL = "https://api.exa.ai/contents"
_TIMEOUT = 10.0

# Same forgiving middle ground as linklib.domain_migration's own threshold
# comment — reused via _titles_match() below, not reimplemented here.
_TITLE_MATCH_CANDIDATES = 5


def _host(url: str) -> str:
    """Same host-normalization every other domain-matching path in this
    codebase uses (linklib.pipeline._defunct_service_domain,
    Library.content_refetch_failure_domains, scripts/
    medium_platform_scale_check.py)."""
    host = (urlsplit(url or "").netloc or "").lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host


def is_medium_platform_host(url: str) -> bool:
    """True if `url`'s host is medium.com itself, any medium.com
    subdomain (an author's own `<handle>.medium.com`, or Medium's own
    `link.medium.com` short-link redirector — both already covered by the
    suffix check, no separate entry needed), or a confirmed custom domain
    in `_MEDIUM_CUSTOM_DOMAINS`."""
    host = _host(url)
    if not host:
        return False
    if host == "medium.com" or host.endswith(".medium.com"):
        return True
    return host in _MEDIUM_CUSTOM_DOMAINS


def is_recognized_blocked_host(url: str) -> bool:
    """True for a Medium-platform host (is_medium_platform_host) OR a
    non-Medium host in _OTHER_BLOCKED_HOSTS — the actual gate for this
    tier's fetch-by-URL/search-by-title recovery path and its same-domain
    carve-out, honestly named for what it means ("Exa can plausibly recover
    this blocked host"), distinct from is_medium_platform_host's narrower,
    factual "this really is Medium underneath" claim."""
    host = _host(url)
    if not host:
        return False
    return is_medium_platform_host(url) or host in _OTHER_BLOCKED_HOSTS


def fetch_content_by_url(lib, url: str) -> tuple[str, float]:
    """Direct Exa content fetch for a known, exact URL — Exa's `/contents`
    endpoint, not `/search`. Used when the article's own current URL
    (post manual-URL-correction, see CLAUDE.md's manual-review bullets) is
    already on a recognized blocked host: there's no candidate to
    disambiguate, so no title-match validation is needed the way
    find_medium_candidate's search results need one. Returns
    (text, cost_usd) — the fetched text ("" on any miss/failure), and the
    real compute_exa_cost() figure for the call (2026-09, Exa cost-tracking
    foundation). EXA_PRICING only models the "search" endpoint tier
    (compute_exa_cost's own documented fallback), so a /contents call is
    billed at those same rates rather than adding a new, unmodeled pricing
    row for a single caller — see EXA_PRICING's own comment on adding rows
    only once a caller needs one; this stays a deliberate approximation,
    not a claim that /contents and /search cost the same in reality.
    cost_usd is 0.0 whenever Exa was never actually billed (no
    EXA_API_KEY, Exa disabled via the admin toggle, a blank url, a network
    failure, or a non-200/malformed response) — never raises, same
    best-effort contract as find_medium_candidate/find_migrated_url. `lib`
    may be None (falls back to "enabled"), matching agent._web_provider's
    own convention."""
    api_key = os.environ.get("EXA_API_KEY")
    if not api_key or not url:
        return "", 0.0
    if lib is not None and not lib.get_exa_enabled():
        return "", 0.0

    try:
        resp = requests.post(
            _EXA_CONTENTS_URL,
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
            json={"urls": [url], "text": True},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return "", 0.0

    from .pricing import compute_exa_cost
    results = data.get("results") or []
    cost = compute_exa_cost("contents", num_results=len(results))
    for r in results:
        if isinstance(r, dict) and (r.get("text") or "").strip():
            return r.get("text") or "", cost
    return "", cost


def find_medium_candidate(lib, title: str, author: str = "") -> tuple[str, str, float]:
    """Search Exa (no domain restriction — see module docstring) for the
    article by title (+ author if available), validating candidates in
    order through linklib.domain_migration._titles_match() and returning
    the first that clears the threshold. Returns (candidate_url,
    candidate_text, cost_usd) — candidate_text is Exa's own returned
    contents.text for that hit, needed by the caller's same-domain
    validation path (see linklib.pipeline._try_medium_platform) since
    re-fetching a Medium-platform candidate would just re-hit the same
    block; cost_usd is the real compute_exa_cost() figure for the call
    (2026-09, Exa cost-tracking foundation), billed on real results
    delivered same as every other Exa search path in this codebase.
    Returns ("", "", 0.0) on any miss or failure — never raises, same
    best-effort contract as find_migrated_url. `lib` may be None (falls
    back to "enabled"), matching agent._web_provider's own convention."""
    from .domain_migration import _titles_match

    api_key = os.environ.get("EXA_API_KEY")
    if not api_key or not title.strip():
        return "", "", 0.0
    if lib is not None and not lib.get_exa_enabled():
        return "", "", 0.0

    query = f"{title} {author}".strip() if author else title
    try:
        resp = requests.post(
            _EXA_SEARCH_URL,
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
            json={"query": query, "numResults": _TITLE_MATCH_CANDIDATES,
                  "contents": {"text": True}},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return "", "", 0.0

    from .pricing import compute_exa_cost
    results = data.get("results") or []
    cost = compute_exa_cost("search", num_results=len(results))
    for r in results:
        if not isinstance(r, dict):
            continue
        url = r.get("url") or ""
        if url and _titles_match(title, r.get("title") or ""):
            return url, (r.get("text") or ""), cost
    return "", "", cost
