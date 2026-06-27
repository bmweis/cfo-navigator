"""RSS/Atom feed reader backed by the OPML subscription list.

Fetches feeds concurrently, caches results for 30 minutes per feed, and
returns a flat list of FeedItem dicts sorted newest-first.
"""
from __future__ import annotations

import time
import threading
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional
import re

import requests
from bs4 import BeautifulSoup

FETCH_TIMEOUT = 8  # seconds per feed
CACHE_TTL = 1800   # 30 minutes
MAX_ITEMS_PER_FEED = 20
_UA = "Mozilla/5.0 (compatible; CFONavigator/1.0; +https://bmweis.com)"

# Domains known to be fully or substantially paywalled.
# Items whose URL contains one of these get paywalled=True.
PAYWALLED_DOMAINS = {
    "stratechery.com",         # members-only daily + weekly
    "blog.publiccomps.com",    # subscription newsletter
    "lookingforleverage.com",  # paid Substack — public RSS carries previews only
    "mostlymetrics.com",       # paid Substack — public RSS carries previews only
}

_cache: dict[str, tuple[float, list[dict]]] = {}
_cache_lock = threading.Lock()


# ---------------------------------------------------------------------------
# OPML parsing
# ---------------------------------------------------------------------------

@dataclass
class FeedMeta:
    name: str
    xml_url: str
    html_url: str
    category: str


def parse_opml(path: str) -> list[FeedMeta]:
    """Return all RSS outlines from the OPML file, preserving category."""
    tree = ET.parse(path)
    root = tree.getroot()
    feeds: list[FeedMeta] = []
    body = root.find("body")
    if body is None:
        return feeds
    for cat_outline in body:
        category = cat_outline.get("title") or cat_outline.get("text") or "Other"
        for item in cat_outline:
            xml_url = item.get("xmlUrl", "").strip()
            if not xml_url:
                continue
            feeds.append(FeedMeta(
                name=item.get("title") or item.get("text") or "",
                xml_url=xml_url,
                html_url=item.get("htmlUrl", ""),
                category=category,
            ))
    return feeds


# ---------------------------------------------------------------------------
# Feed fetching + parsing
# ---------------------------------------------------------------------------

def _parse_date(s: str | None) -> Optional[datetime]:
    if not s:
        return None
    s = s.strip()
    # RFC 2822 (RSS)
    try:
        return parsedate_to_datetime(s)
    except Exception:
        pass
    # ISO 8601 / Atom
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s[:25], fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            pass
    return None


def _text(el: Optional[ET.Element]) -> str:
    if el is None:
        return ""
    return (el.text or "").strip()


def _strip_html(s: str) -> str:
    if not s:
        return ""
    try:
        return BeautifulSoup(s, "html.parser").get_text(" ", strip=True)
    except Exception:
        return re.sub(r"<[^>]+>", " ", s).strip()


def _fetch_feed(meta: FeedMeta) -> list[dict]:
    """Fetch and parse one RSS or Atom feed into a list of item dicts."""
    # Skip private Feedly proxy URLs — they require auth
    if "feedly.com/web/" in meta.xml_url:
        return []
    try:
        resp = requests.get(
            meta.xml_url, timeout=FETCH_TIMEOUT,
            headers={"User-Agent": _UA},
            allow_redirects=True,
        )
        resp.raise_for_status()
    except Exception:
        return []

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError:
        return []

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    items: list[dict] = []

    # Atom feed
    if root.tag in ("{http://www.w3.org/2005/Atom}feed", "feed"):
        tag = lambda t: f"{{http://www.w3.org/2005/Atom}}{t}"  # noqa
        for entry in root.findall(tag("entry"))[:MAX_ITEMS_PER_FEED]:
            title = _text(entry.find(tag("title")))
            url = ""
            for link in entry.findall(tag("link")):
                if link.get("rel") in (None, "alternate") and link.get("href"):
                    url = link.get("href", "")
                    break
            pub = _parse_date(_text(entry.find(tag("published"))) or
                               _text(entry.find(tag("updated"))))
            summary_el = entry.find(tag("summary")) or entry.find(tag("content"))
            summary = _strip_html(_text(summary_el))[:300]
            if title and url:
                items.append({
                    "title": title, "url": url,
                    "source": meta.name, "category": meta.category,
                    "html_url": meta.html_url,
                    "published_at": pub.isoformat() if pub else "",
                    "pub_dt": pub,
                    "summary": summary,
                })
        return items

    # RSS 2.0
    channel = root.find("channel")
    if channel is None:
        channel = root
    for item in channel.findall("item")[:MAX_ITEMS_PER_FEED]:
        title = _text(item.find("title"))
        url = _text(item.find("link")) or _text(item.find("guid"))
        pub = _parse_date(_text(item.find("pubDate")))
        desc = _text(item.find("description"))
        summary = _strip_html(desc)[:300]
        if title and url:
            items.append({
                "title": title, "url": url,
                "source": meta.name, "category": meta.category,
                "html_url": meta.html_url,
                "published_at": pub.isoformat() if pub else "",
                "pub_dt": pub,
                "summary": summary,
            })
    return items


def _cached_fetch(meta: FeedMeta) -> list[dict]:
    now = time.time()
    with _cache_lock:
        cached = _cache.get(meta.xml_url)
        if cached and now - cached[0] < CACHE_TTL:
            return cached[1]
    items = _fetch_feed(meta)
    with _cache_lock:
        _cache[meta.xml_url] = (now, items)
    return items


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_feed_items(
    opml_path: str,
    category: str = "",
    max_total: int = 100,
) -> tuple[list[dict], list[str]]:
    """Fetch all (or one category's) feeds and return (items, categories).

    Items are sorted newest-first. Items without a date sink to the bottom.
    Returns the full category list alongside items so the UI can render tabs.
    """
    feeds = parse_opml(opml_path)
    categories = list(dict.fromkeys(f.category for f in feeds))

    if category:
        feeds = [f for f in feeds if f.category == category]

    all_items: list[dict] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_cached_fetch, f): f for f in feeds}
        for fut in as_completed(futures):
            try:
                all_items.extend(fut.result())
            except Exception:
                pass

    # Sort: items with dates newest-first, undated items last
    # Use .get() — cached dicts may have had pub_dt popped on a prior call
    dated = [i for i in all_items if i.get("pub_dt")]
    undated = [i for i in all_items if not i.get("pub_dt")]
    dated.sort(key=lambda i: i.get("pub_dt"), reverse=True)

    result = []
    for item in (dated + undated)[:max_total]:
        d = dict(item)  # shallow copy so we don't mutate the cached dict
        d["paywalled"] = any(dom in d["url"] for dom in PAYWALLED_DOMAINS)
        d.pop("pub_dt", None)
        result.append(d)

    return result, categories
