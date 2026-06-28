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


@dataclass
class PageData:
    title: str
    content: str


def fetch_page(url: str, timeout: int = 20) -> PageData:
    """Fetch a URL once and return both the page title and cleaned body text.

    Always returns a PageData; fields are empty strings on failure. For domains
    with a configured auth cookie (LINKLIB_AUTH_COOKIES), the request is sent
    authenticated so subscriber-only full text is fetched instead of a preview.
    """
    headers = dict(_HEADERS)
    cookie = _cookie_for(url)
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
    return PageData(title=title, content=content)


def fetch_fulltext(url: str, timeout: int = 20) -> str:
    """Return cleaned article text, or "" on any failure."""
    return fetch_page(url, timeout=timeout).content


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
