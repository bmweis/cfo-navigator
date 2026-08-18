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

_EXA_SEARCH_URL = "https://api.exa.ai/search"
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


def find_medium_candidate(lib, title: str, author: str = "") -> tuple[str, str]:
    """Search Exa (no domain restriction — see module docstring) for the
    article by title (+ author if available), validating candidates in
    order through linklib.domain_migration._titles_match() and returning
    the first that clears the threshold. Returns (candidate_url,
    candidate_text) — candidate_text is Exa's own returned contents.text
    for that hit, needed by the caller's same-domain validation path (see
    linklib.pipeline._try_medium_platform) since re-fetching a Medium-
    platform candidate would just re-hit the same block. Returns ("", "")
    on any miss or failure — never raises, same best-effort contract as
    find_migrated_url. `lib` may be None (falls back to "enabled"),
    matching agent._web_provider's own convention."""
    from .domain_migration import _titles_match

    api_key = os.environ.get("EXA_API_KEY")
    if not api_key or not title.strip():
        return "", ""
    if lib is not None and not lib.get_exa_enabled():
        return "", ""

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
        return "", ""

    for r in (data.get("results") or []):
        if not isinstance(r, dict):
            continue
        url = r.get("url") or ""
        if url and _titles_match(title, r.get("title") or ""):
            return url, (r.get("text") or "")
    return "", ""
