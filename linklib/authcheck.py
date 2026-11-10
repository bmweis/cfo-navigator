"""Health checks for the subscriber-auth cookies (LINKLIB_COOKIE_<DOMAIN>, for
every domain named by a feed in preferred_sites.opml — see
linklib.extract._opml_feed_domains).

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
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse, urlsplit

import requests

from .db import Library
from .extract import looks_like_bot_challenge

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


# Closed list of reasons a probe can report. code -> plain-language text.
REASONS = {
    "accepted": "Cookie accepted",
    "no_post": "No recent post found to test",
    "bad_header": "Variable has a line break or unsupported character",
    "unreachable": "Could not reach the site (timeout or DNS)",
    "bot_challenge": "Bot challenge (Cloudflare or similar)",
    "rejected": "Rejected: HTTP 401 or 403",
    "login_redirect": "Redirected to login",
    "not_sent": "Cookie not sent (test post is on another host)",
    "paywall": "Paywall text on a post page",
    "unexpected": "Unexpected response",
}

_LOGIN_PATH = re.compile(r"/(log-?in|sign-?in|sign-?up|auth|subscribe|checkout)(/|$)", re.I)


def _host_path(url: str) -> tuple[str, str]:
    """Host and path only, never a query string or fragment."""
    sp = urlsplit(url or "")
    return (sp.netloc or "").lower(), sp.path or "/"


def classify_probe(page, post_url: str, cookie_sent: bool) -> tuple[bool, str]:
    """(ok, reason_code) for a probe fetch. `ok` follows the original rule
    exactly (full text and not paywalled; any failed fetch is False), so the
    status colours did not change; only the reason is new."""
    ok = bool(page.content) and not page.blocked
    err = page.fetch_error or ""
    if err == "invalid header value":
        return ok, "bad_header"
    if err and not page.http_status:
        return ok, "unreachable"
    if err:
        if page.challenge:
            return ok, "bot_challenge"
        if page.http_status in (401, 403):
            return ok, "rejected"
        return ok, "unexpected"
    final_host, final_path = _host_path(page.final_url)
    post_host, post_path = _host_path(post_url)
    if page.final_url and _LOGIN_PATH.search(final_path) and not _LOGIN_PATH.search(post_path):
        return ok, "login_redirect"
    if looks_like_bot_challenge(page.raw_html):
        return ok, "bot_challenge"
    if page.blocked:
        return ok, "paywall" if cookie_sent else "not_sent"
    if not page.content:
        return ok, "unexpected"
    return ok, "accepted"


def paywall_comparison(with_cookie, without_cookie) -> dict:
    """Compare the page fetched with the cookie to the same URL fetched once
    without it. Returns the matched marker label, both sizes and one plain
    line. Reads only sizes, status and the marker name, never the body."""
    from .extract import PAYWALL_MARKER_LABELS
    label = PAYWALL_MARKER_LABELS.get(with_cookie.paywall_marker, with_cookie.paywall_marker)
    a, b = with_cookie.body_bytes, without_cookie.body_bytes
    if without_cookie.fetch_error and not without_cookie.http_status:
        line = "Without the cookie: could not fetch the page again"
        same = None
    elif without_cookie.http_status != with_cookie.http_status:
        line = (f"Without the cookie: HTTP {without_cookie.http_status or 'error'} instead of "
                f"HTTP {with_cookie.http_status} (cookie had an effect)")
        same = False
    else:
        same_marker = without_cookie.paywall_marker == with_cookie.paywall_marker
        close = a > 0 and abs(a - b) <= max(1, int(a * 0.02))
        same = bool(same_marker and close)
        if same:
            line = "Same page without the cookie: identical (cookie ignored)"
        else:
            how = "longer" if b > a else "shorter" if b < a else "same-size"
            wording = "same paywall wording" if same_marker else (
                "no paywall wording" if not without_cookie.paywall_marker else "different paywall wording")
            line = (f"Without the cookie: {b:,} bytes vs {a:,} with it, {how}, {wording} "
                    f"(cookie had an effect)")
    wo = PAYWALL_MARKER_LABELS.get(without_cookie.paywall_marker, without_cookie.paywall_marker)
    present = ("present with and without the cookie" if wo and wo == label
               else "present with the cookie only" if not wo
               else f"different wording without the cookie ({wo})")
    if same is not None:
        line += f". Matched wording: {label} ({present}); response {a:,} bytes with, {b:,} without"
    return {"paywall_marker": label, "paywall_marker_without_cookie": wo,
            "bytes_with_cookie": a, "bytes_without_cookie": b,
            "text_chars_with_cookie": len(with_cookie.content or ""),
            "text_chars_without_cookie": len(without_cookie.content or ""),
            "same_without_cookie": same, "compare_text": line}


def check_auth_cookies(lib: Library, opml_path: str) -> dict:
    """Probe each configured auth-cookie domain and persist a health record.
    Returns the status dict. No-op (empty) when no cookies are configured.

    Each record also carries a reason (closed list, REASONS), the HTTP status,
    the final URL's host and path (no query), and what the app sent: cookie
    NAMES, header byte length, and whether the header was sent to the test
    post's host at all. Cookie values are never read into the record."""
    from .extract import _auth_cookies, _cookie_for, cookie_summary, fetch_page

    # A domain whose feeds are all unticked as Subscriber is not checked, and
    # (because the whole record is rewritten below) its stored status is dropped.
    skip = unsubscribed_domains(lib)
    domains = [(d, c) for d, c in _auth_cookies().items() if d not in skip]
    status: dict = {}
    now = datetime.now(timezone.utc).isoformat()
    for dom, cookie in domains:
        summ = cookie_summary(cookie)
        sent_info = {"cookie_names": summ["names"], "cookie_name_count": summ["name_count"],
                     "cookie_bytes": summ["bytes"], "cookie_quoted": summ["quoted"]}
        url, title = _recent_post_url(dom, opml_path)
        if not url:
            status[dom] = {"ok": None, "checked_at": now, "title": "",
                           "detail": "no recent post found to test",
                           "reason": "no_post", "reason_text": REASONS["no_post"],
                           "http_status": 0, "final_host": "", "final_path": "",
                           "post_host": "", "cookie_sent": False, **sent_info}
            continue
        sent = bool(_cookie_for(url))
        page = fetch_page(url)
        ok, code = classify_probe(page, url, sent)
        if not summ["sendable"]:
            code = "bad_header"
        extra: dict = {}
        if code == "paywall":
            # One extra request, only on this branch, never carrying the cookie.
            extra = paywall_comparison(page, fetch_page(url, send_cookie=False))
        fh, fp = _host_path(page.final_url)
        text = REASONS[code]
        if code == "paywall":
            text += f" (cookie sent, {page.body_bytes:,} bytes)"
        if code == "unexpected":
            if page.http_status:
                text += f" (HTTP {page.http_status}" + ("" if page.content else ", empty body") + ")"
        if ok and code == "accepted":
            detail = f"full text fetched ({len(page.content):,} chars)"
        else:
            detail = text
        if extra:
            detail += " · " + extra["compare_text"]
        status[dom] = {"ok": ok, "checked_at": now, "title": title, "detail": detail,
                       "reason": code, "reason_text": text,
                       "http_status": page.http_status, "final_host": fh, "final_path": fp,
                       "post_host": _host_path(url)[0], "cookie_sent": sent, **sent_info, **extra}
    lib.set_setting(STATUS_KEY, json.dumps(status))
    return status


def _feed_domain(url: str) -> str:
    host = (urlsplit(url or "").netloc or "").lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def unsubscribed_domains(lib: Library) -> set[str]:
    """Domains that feeds name and where no feed has Subscriber ticked.

    The Subscriber box on /admin/reader/feeds is the decision about whether a
    source is paid. A domain with at least one ticked feed is checked; a domain
    whose feeds are all unticked is not. A domain with no feed row at all is
    not in this set (nothing was decided), so it is checked as before."""
    named: set[str] = set()
    ticked: set[str] = set()
    for f in lib.list_feeds():
        hosts = {_feed_domain(f["xml_url"]), _feed_domain(f.get("html_url") or "")} - {""}
        named |= hosts
        if f.get("has_active_subscription"):
            ticked |= hosts
    return named - ticked


def get_auth_status(lib: Library) -> dict:
    """The stored record, minus any domain whose feeds are all unticked as
    Subscriber. The box takes effect at once, before the next Re-check."""
    try:
        status = json.loads(lib.get_setting(STATUS_KEY) or "{}")
    except Exception:
        return {}
    skip = unsubscribed_domains(lib)
    return {d: s for d, s in status.items() if d not in skip} if skip else status


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
