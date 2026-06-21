"""Your preferred sources — parsed from your Feedly subscriptions OPML.

These are the trusted sites the Q&A agent is allowed to pull NEW (not-yet-saved)
articles from via web search, so answers draw on your saved library AND fresh
material from the same voices you already follow.

Edit preferred_sites.opml (or point LINKLIB_SITES_OPML elsewhere) to curate.
"""
from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from functools import lru_cache
from urllib.parse import urlparse

DEFAULT_OPML = os.environ.get(
    "LINKLIB_SITES_OPML",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "preferred_sites.opml"),
)


@lru_cache(maxsize=8)
def preferred_domains(opml_path: str = DEFAULT_OPML) -> tuple[str, ...]:
    try:
        root = ET.parse(opml_path).getroot()
    except Exception:
        return ()
    domains = set()
    for o in root.iter("outline"):
        url = o.get("htmlUrl") or o.get("xmlUrl") or ""
        if url:
            netloc = urlparse(url).netloc.replace("www.", "")
            if netloc:
                domains.add(netloc)
    return tuple(sorted(domains))
