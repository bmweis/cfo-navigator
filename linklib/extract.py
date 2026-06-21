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

import requests

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; linklib/1.0)"}


def fetch_fulltext(url: str, timeout: int = 20) -> str:
    """Return cleaned article text, or "" on any failure."""
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=timeout)
        resp.raise_for_status()
        html = resp.text
    except Exception:
        return ""

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
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
            tag.decompose()
        return soup.get_text(" ", strip=True)
    except Exception:
        return ""
