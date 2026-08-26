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
from datetime import datetime, timezone
from urllib.parse import urlsplit

from .db import Library

STATUS_KEY = "auth_cookie_status"


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
        from .queue import discover_sitemaps, fetch_sitemap_entries, _looks_like_post
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
