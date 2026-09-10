"""Health checks for the subscriber-auth cookies (LINKLIB_COOKIE_<DOMAIN>, per
linklib.extract._COOKIE_DOMAINS).

A paid-newsletter cookie eventually expires; when it does, fetches quietly fall
back to previews and the Ask corpus stops getting full text. To make that
visible, `check_auth_cookies` probes one recent post per configured domain and
records whether the cookie still returns full content. The web app reads the
stored status and shows a refresh banner when anything has gone stale.

Status is persisted in the settings KV under `auth_cookie_status` as JSON:
    {"<domain>": {"ok": bool, "checked_at": iso, "title": str, "detail": str}}
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse, urlsplit

import requests

from .db import Library

STATUS_KEY = "auth_cookie_status"

# -- sitemap discovery, for _recent_post_url's fallback path ----------------
#
# Originally lived in linklib/queue.py (the Archive Queue's historical
# sitemap sweep), which authcheck._recent_post_url reused for its own
# "find something recent to probe" fallback. Retired along with the queue
# (PR 3) — this is the one real caller left, so the small set of functions
# it actually needs moved here directly rather than surviving as a new
# single-consumer shared module.

_SITEMAP_UA = "Mozilla/5.0 (compatible; CFONavigator/1.0; +https://bmweis.com)"
_SITEMAP_TIMEOUT = 12

# Path fragments that are almost never article posts — skip to cut noise.
_NON_POST = ("/tag/", "/tags/", "/category/", "/categories/", "/author/",
             "/authors/", "/page/", "/about", "/contact", "/privacy",
             "/terms", "/feed", "/archive", "/search", "/subscribe", "/wp-content/")


def _http_get(url: str):
    return requests.get(url, timeout=_SITEMAP_TIMEOUT,
                        headers={"User-Agent": _SITEMAP_UA}, allow_redirects=True)


def _parse_lastmod(s: str | None):
    if not s:
        return None
    s = s.strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        pass
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _looks_like_post(url: str) -> bool:
    low = url.lower().rstrip("/")
    return not any(frag in low for frag in _NON_POST)


def discover_sitemaps(site_url: str) -> list[str]:
    """Candidate sitemap URLs for a site: robots.txt declarations first, then
    the common conventional paths. Order matters — the caller uses the first
    that yields entries."""
    if not urlparse(site_url).scheme:
        site_url = "https://" + site_url
    p = urlparse(site_url)
    root = f"{p.scheme}://{p.netloc}"
    found: list[str] = []
    try:
        r = _http_get(urljoin(root, "/robots.txt"))
        if r.ok:
            for line in r.text.splitlines():
                if line.lower().startswith("sitemap:"):
                    sm = line.split(":", 1)[1].strip()
                    if sm:
                        found.append(sm)
    except Exception:
        pass
    for path in ("/sitemap.xml", "/sitemap_index.xml", "/sitemap-index.xml",
                 "/wp-sitemap.xml", "/sitemap/sitemap-index.xml"):
        found.append(urljoin(root, path))
    seen: set[str] = set()
    out: list[str] = []
    for u in found:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _parse_sitemap_xml(content: bytes) -> tuple[str, list[dict]]:
    """Parse sitemap XML bytes. Returns (kind, entries) where kind is
    'sitemapindex' (entries are {'url'} sub-sitemaps) or 'urlset' (entries are
    {'url', 'lastmod'}). Returns ('', []) on parse failure."""
    try:
        root = ET.fromstring(content)
    except Exception:
        return "", []
    kind = _localname(root.tag)
    entries: list[dict] = []
    for node in root:
        loc = None
        lastmod = None
        for child in node:
            ln = _localname(child.tag)
            if ln == "loc":
                loc = (child.text or "").strip()
            elif ln == "lastmod":
                lastmod = _parse_lastmod(child.text)
        if loc:
            entries.append({"url": loc, "lastmod": lastmod})
    return kind, entries


def fetch_sitemap_entries(sitemap_url: str, *, _depth: int = 0,
                          _budget: list[int] | None = None) -> list[dict]:
    """Return [{'url', 'lastmod'}] from a sitemap, recursing one level into a
    sitemap index. Best-effort — returns [] on any network/parse error."""
    if _budget is None:
        _budget = [50]
    if _depth > 2:
        return []
    try:
        r = _http_get(sitemap_url)
        if not r.ok:
            return []
    except Exception:
        return []
    kind, entries = _parse_sitemap_xml(r.content)
    if kind == "sitemapindex":
        out: list[dict] = []
        for sm in entries:
            if _budget[0] <= 0:
                break
            _budget[0] -= 1
            out.extend(fetch_sitemap_entries(sm["url"], _depth=_depth + 1, _budget=_budget))
        return out
    return entries


def _host(url: str) -> str:
    h = (urlsplit(url).netloc or "").lower().split(":")[0]
    return h[4:] if h.startswith("www.") else h


def _recent_post_url(domain: str, opml_path: str) -> tuple[str, str]:
    """Find a recent post URL for `domain` to probe. Tries the OPML feed first,
    then falls back to the site's sitemap (so the probe still works when the RSS
    feed URL is wrong — e.g. a beehiiv custom domain). Returns (url, title) or
    ("", "")."""
    # 1) RSS feed from the OPML.
    try:
        from .feed import parse_opml, _fetch_feed
        for f in parse_opml(opml_path):
            if _host(f.html_url) == domain or _host(f.xml_url) == domain:
                try:
                    items = _fetch_feed(f)
                except Exception:
                    items = []
                for it in items:
                    if it.get("url"):
                        return it["url"], it.get("title", "")
    except Exception:
        pass
    # 2) Sitemap fallback — find a post-like URL on the domain.
    try:
        for sm in discover_sitemaps(f"https://{domain}"):
            entries = fetch_sitemap_entries(sm)
            posts = [e for e in entries if _looks_like_post(e["url"])]
            if not posts:
                continue
            dated = [e for e in posts if e.get("lastmod")]
            best = max(dated, key=lambda e: e["lastmod"]) if dated else posts[0]
            return best["url"], ""
    except Exception:
        pass
    return "", ""


def check_auth_cookies(lib: Library, opml_path: str) -> dict:
    """Probe each configured auth-cookie domain and persist a health record.
    Returns the status dict. No-op (empty) when no cookies are configured."""
    from .extract import _auth_cookies, fetch_page

    domains = list(_auth_cookies().keys())
    status: dict = {}
    now = datetime.now(timezone.utc).isoformat()
    for dom in domains:
        url, title = _recent_post_url(dom, opml_path)
        if not url:
            status[dom] = {"ok": None, "checked_at": now, "title": "",
                           "detail": "no recent post found to test"}
            continue
        page = fetch_page(url)
        if page.content and not page.blocked:
            status[dom] = {"ok": True, "checked_at": now, "title": title,
                           "detail": f"full text fetched ({len(page.content):,} chars)"}
        else:
            status[dom] = {"ok": False, "checked_at": now, "title": title,
                           "detail": "got a preview/paywall — cookie missing or expired"}
    lib.set_setting(STATUS_KEY, json.dumps(status))
    return status


def get_auth_status(lib: Library) -> dict:
    try:
        return json.loads(lib.get_setting(STATUS_KEY) or "{}")
    except Exception:
        return {}


def stale_domains(status: dict) -> list[str]:
    """Domains whose last probe found an expired/missing cookie (ok is False)."""
    return [d for d, s in (status or {}).items() if s.get("ok") is False]


def status_age_seconds(status: dict) -> float | None:
    """Seconds since the oldest record was checked, or None if empty."""
    times = []
    for s in (status or {}).values():
        try:
            times.append(datetime.fromisoformat(s["checked_at"]))
        except Exception:
            pass
    if not times:
        return None
    return (datetime.now(timezone.utc) - min(times)).total_seconds()
