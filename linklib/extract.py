"""Optional: fetch and clean the full text of a live article page.

The Feedly import does NOT require this — Feedly already returns a summary
(and often partial content) per entry. Use this only when you want fuller
body text in the index. It's best-effort: dead links, paywalls, and
bot-blocks are expected for older saves, so failures are swallowed and the
import keeps going.

Best extraction quality comes from `trafilatura` if installed:
    pip install trafilatura
Otherwise it falls back to a crude BeautifulSoup text pull.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; linklib/1.0)"}

# When sending an auth cookie we're impersonating the logged-in browser, so pair
# it with a realistic browser User-Agent + Accept to reduce WAF friction
# (Cloudflare cf_clearance is also IP-bound, so this helps but isn't a guarantee).
_BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _auth_cookies() -> dict[str, str]:
    """Per-domain auth cookies for fetching subscriber-only content (e.g. paid
    Substacks). Set LINKLIB_AUTH_COOKIES to a JSON object mapping a domain to its
    Cookie header value, kept in the host env so the secret never touches the repo:

        {"mostlymetrics.com": "substack.sid=...", "lookingforleverage.com": "..."}

    The full text is fetched only as enrichment/search input — the served surface
    stays summaries + citations, same as every other article.
    """
    raw = (os.environ.get("LINKLIB_AUTH_COOKIES") or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return {str(k).lower().lstrip("."): str(v) for k, v in data.items() if k and v}
    except Exception:
        return {}


def _cookie_for(url: str) -> str:
    """Return the configured Cookie header for `url`'s domain, or "" if none."""
    host = (urlsplit(url).netloc or "").lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    for dom, cookie in _auth_cookies().items():
        if host == dom or host.endswith("." + dom):
            return cookie
    return ""


# Phrases a logged-out reader sees on a paid post — used to tell "cookie worked"
# (full text) from "cookie missing/expired" (preview wall).
_PAYWALL_MARKERS = (
    "this post is for pa",            # "...paid subscribers" / "...paying subscribers"
    "this post is for free and paid",
    "available to paid subscribers",
    "become a paid subscriber",
    "subscribe to read",
    "this episode is for pa",
)


def looks_paywalled(html: str, content: str) -> bool:
    """Heuristic: does this page look like the logged-out preview of paid content?
    True when a known paywall phrase appears, or the body is suspiciously thin
    next to a subscribe prompt."""
    low = (html or "").lower()
    if any(m in low for m in _PAYWALL_MARKERS):
        return True
    return len(content or "") < 400 and ('class="paywall"' in low or "subscribe-widget" in low)


@dataclass
class PageData:
    title: str
    content: str
    blocked: bool = False    # looked like a logged-out paywall (cookie missing/expired)
    published: str = ""      # true publish date (ISO) parsed from the page, if found


def fetch_page(url: str, timeout: int = 20) -> PageData:
    """Fetch a URL once and return both the page title and cleaned body text.

    Always returns a PageData; fields are empty strings on failure. For domains
    with a configured auth cookie (LINKLIB_AUTH_COOKIES), the request is sent
    authenticated so subscriber-only full text is fetched instead of a preview.
    `blocked` flags a response that still looks paywalled — the signal that a
    configured cookie is missing or expired.
    """
    cookie = _cookie_for(url)
    headers = dict(_BROWSER_HEADERS if cookie else _HEADERS)
    if cookie:
        headers["Cookie"] = cookie
    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
        resp.raise_for_status()
        html = resp.text
    except Exception:
        return PageData(title="", content="")

    title = _extract_title(html)
    content = _extract_content(html)
    return PageData(title=title, content=content,
                    blocked=looks_paywalled(html, content),
                    published=_extract_published(html))


def fetch_fulltext(url: str, timeout: int = 20) -> str:
    """Return cleaned article text, or "" on any failure."""
    return fetch_page(url, timeout=timeout).content


def _extract_published(html: str) -> str:
    """Best-effort true publish date (ISO 8601) from the article page. Static-site
    sitemaps stamp every URL with the last build date, so the page's own metadata
    is the authoritative source. Returns "" if none found."""
    import re
    # 1) Meta tags: article:published_time (OG), or common date metas.
    meta_pat = re.compile(
        r'<meta[^>]+(?:property|name)=["\'](?:article:published_time|'
        r'og:article:published_time|datePublished|publish-date|date|'
        r'parsely-pub-date|sailthru\.date)["\'][^>]*\bcontent=["\']([^"\']+)["\']',
        re.IGNORECASE)
    # 2) <time datetime="..."> — the first one on a post is almost always the date.
    time_pat = re.compile(r'<time[^>]*\bdatetime=["\']([^"\']+)["\']', re.IGNORECASE)
    # 3) JSON-LD "datePublished": "..."
    ld_pat = re.compile(r'"datePublished"\s*:\s*"([^"]+)"', re.IGNORECASE)
    for pat in (meta_pat, ld_pat, time_pat):
        m = pat.search(html or "")
        if m:
            val = _normalize_date(m.group(1))
            if val:
                return val
    return ""


def _normalize_date(s: str) -> str:
    """Coerce a date/datetime string to an ISO string; "" if unparseable or absurd."""
    from datetime import datetime, timezone
    s = (s or "").strip()
    if not s:
        return ""
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        try:
            dt = datetime.strptime(s[:10], "%Y-%m-%d")
        except Exception:
            return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # guard against placeholder/build dates far in the future
    if dt.year < 1990 or dt > datetime.now(timezone.utc):
        return ""
    return dt.isoformat()


def _extract_title(html: str) -> str:
    try:
        soup = BeautifulSoup(html, "html.parser")
        tag = soup.find("title")
        if tag:
            return tag.get_text(strip=True)
    except Exception:
        pass
    return ""


def _extract_content(html: str) -> str:
    # Preferred path: trafilatura (handles boilerplate removal well).
    try:
        import trafilatura
        extracted = trafilatura.extract(html, include_comments=False, include_tables=False)
        if extracted:
            return extracted.strip()
    except Exception:
        pass

    # Fallback: strip tags.
    try:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
            tag.decompose()
        return soup.get_text(" ", strip=True)
    except Exception:
        return ""
