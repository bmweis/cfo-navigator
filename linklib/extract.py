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
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup, Comment

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


# Phrases a bot-challenge/anti-scraper interstitial shows instead of the real
# page — Cloudflare's "Just a moment..."/Turnstile challenge, generic
# "verify you are human" CAPTCHA walls, PerimeterX/Akamai/DataDome bot-manager
# pages. These all return HTTP 200 (fetch_page's raise_for_status never fires),
# so a naive "status succeeded" check would happily overwrite content_html with
# an interstitial instead of logging a failure. Distinct from _PAYWALL_MARKERS
# (a real publisher's own "subscribe to keep reading" copy) — a bot challenge
# means the actual page was never reached at all, from any account.
_BOT_CHALLENGE_MARKERS = (
    "just a moment...",             # Cloudflare's own interstitial title
    "checking your browser before accessing",
    "cf-browser-verification",
    "cf_chl_opt",                   # Cloudflare Turnstile challenge script hook
    "verify you are human",
    "verify that you are a human",
    "please verify you are a human",
    "attention required! | cloudflare",
    "press and hold",               # PerimeterX/Akamai "press and hold" challenge
    "/distil_r_captcha.html",       # Imperva/Distil bot-block redirect
    "captcha-delivery.com",         # DataDome
    "are you a robot",
)


def looks_like_bot_challenge(html: str) -> bool:
    """Heuristic: is this page a bot-challenge/anti-scraper interstitial
    (Cloudflare, PerimeterX, DataDome, a generic CAPTCHA wall) rather than
    the real article? See _BOT_CHALLENGE_MARKERS for the phrase list."""
    low = (html or "").lower()
    return any(m in low for m in _BOT_CHALLENGE_MARKERS)


# Below this many words, extracted "content" is more likely a stub/teaser/
# interstitial than a real article body — used only as the last-resort
# sanity floor in assess_extraction_quality(), after the more specific
# paywall/bot-challenge marker checks have already had a chance to name the
# real reason.
_MIN_CONTENT_WORDS = 60


def assess_extraction_quality(html: str, plain_content: str, blocked: bool) -> tuple[bool, str]:
    """Decide whether a fetch actually got the real article, or a look-alike
    that would otherwise pass a bare HTTP-status check. Returns (ok, reason):
    `ok=True, reason=""` when the content looks real; `ok=False` with a
    reason in {'paywall', 'bot-challenge', 'too-thin'} otherwise. Checked in
    that order so a page that happens to trip more than one heuristic (e.g. a
    thin bot-challenge page with almost no words) still logs the most
    specific, most useful reason rather than the vaguest one.

    Deliberately conservative: this only decides whether it's SAFE to store a
    result, never mutates anything itself — see linklib.pipeline.
    backfill_article_content for the caller that treats `ok=False` as
    "leave the existing content alone, log why."
    """
    if blocked:
        return False, "paywall"
    if looks_like_bot_challenge(html):
        return False, "bot-challenge"
    if len((plain_content or "").split()) < _MIN_CONTENT_WORDS:
        return False, "too-thin"
    return True, ""


@dataclass
class PageData:
    title: str
    content: str
    blocked: bool = False    # looked like a logged-out paywall (cookie missing/expired)
    published: str = ""      # true publish date (ISO) parsed from the page, if found
    raw_html: str = ""       # the fetched page's raw HTML — kept only so a caller that
                              # wants real structure (paragraphs/images/links, not the
                              # plain-text `content` the ingest/search/enrichment
                              # pipeline depends on) can run its own extraction over it
                              # without a second HTTP fetch. See extract_reader_html().


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
                    published=_extract_published(html),
                    raw_html=html)


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


_BLOCK_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote",
               "pre", "figcaption", "br", "div"}


def _extract_content(html: str) -> str:
    """Plain-text extraction — the contract the ingest/search/enrichment pipeline
    depends on (`articles.content`, FTS5 indexing, the Claude enrichment prompt,
    `looks_paywalled()`'s length check). Must stay plain text; a caller that wants
    real structure (paragraphs as actual tags, images, links) should use
    `extract_reader_html()` instead, over the same fetched HTML."""
    # Preferred path: trafilatura (handles boilerplate removal well). Not a
    # declared dependency — `pip install trafilatura` is opt-in (see module
    # docstring) — so in practice this almost always falls through to the BS4
    # path below; both must produce properly paragraph-broken text, not one
    # flattened blob.
    try:
        import trafilatura
        extracted = trafilatura.extract(html, include_comments=False, include_tables=False)
        if extracted:
            return extracted.strip()
    except Exception:
        pass

    # Fallback: strip chrome, then join text a block at a time so paragraph
    # breaks survive as "\n\n" — plain soup.get_text(" ", strip=True) collapses
    # the entire page into one line, which is what a caller splitting on "\n\n"
    # (e.g. the Reader's paragraph renderer) would otherwise see as a single
    # giant paragraph.
    try:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
            tag.decompose()
        blocks = []
        for el in soup.find_all(_BLOCK_TAGS):
            # Skip a block whose own ancestor is also one of our block tags
            # (e.g. an <li> inside a <ul> we're not separately selecting, or a
            # <p> nested in another <div> we already picked up) so text isn't
            # duplicated across an outer and inner block.
            if el.find_parent(_BLOCK_TAGS):
                continue
            text = el.get_text(" ", strip=True)
            if text:
                blocks.append(text)
        if blocks:
            return "\n\n".join(blocks)
        # No recognizable block structure at all (rare) — fall back to a flat
        # pull rather than returning nothing.
        return soup.get_text(" ", strip=True)
    except Exception:
        return ""


# Tags kept verbatim (minus a stripped-down attribute set) when building
# structured Reader HTML — everything else is either dropped with its content
# (chrome/script-y tags) or unwrapped (its children kept, the wrapper dropped).
_READER_JUNK_TAGS = ("script", "style", "nav", "header", "footer", "aside", "noscript",
                     "iframe", "form", "button", "svg", "input", "select", "textarea",
                     "object", "embed", "video", "audio", "canvas",
                     "head", "title", "meta", "link")  # only matters when root falls all
                                                        # the way back to the whole `soup`
                                                        # (no <article>/<body> found) —
                                                        # otherwise these never appear
                                                        # under root in the first place
_READER_KEEP_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li",
                     "blockquote", "img", "a", "strong", "em", "b", "i", "br",
                     "figure", "figcaption", "code", "pre"}
# Only these attributes survive on a kept tag, and only for these specific tags —
# everything else (class, style, id, data-*, on*) is stripped so a source site's
# CSS/JS can never bleed into the Reader pane, which supplies its own styling.
_READER_KEEP_ATTRS = {"img": ("src", "alt"), "a": ("href",)}


def extract_reader_html(html: str, base_url: str) -> str:
    """Best-effort structured extraction for the in-app Reader pane: preserves
    paragraph/heading/list structure, images (absolute src), and hyperlinks
    (absolute href, opened in a new tab) as real HTML — unlike `_extract_content()`,
    whose plain-text contract the ingest/search/enrichment pipeline depends on and
    which this deliberately does not touch. Only meaningful against HTML from a
    fresh fetch (`PageData.raw_html`) — a saved article's cached `content` in the
    DB is already plain text from ingest time, so this can't retroactively recover
    images/links for it. No readability-style content-density scoring (that's what
    trafilatura would add, if ever installed as a real dependency) — this only
    strips known chrome and keeps what's left, same heuristic ceiling as
    `_extract_content()`'s own fallback. Returns "" on any failure or if nothing
    usable survives."""
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return ""

    for c in soup.find_all(string=lambda s: isinstance(s, Comment)):
        c.extract()
    for tag in soup(_READER_JUNK_TAGS):
        tag.decompose()

    root = soup.find("article") or soup.body or soup

    for tag in root.find_all(True):
        if tag.name not in _READER_KEEP_TAGS:
            tag.unwrap()
            continue
        keep = _READER_KEEP_ATTRS.get(tag.name, ())
        for attr in list(tag.attrs):
            if attr not in keep:
                del tag[attr]
        if tag.name == "img":
            src = tag.get("src", "")
            if not src:
                tag.decompose()
                continue
            tag["src"] = urljoin(base_url, src)
            tag["loading"] = "lazy"
        elif tag.name == "a":
            href = tag.get("href", "")
            if href:
                tag["href"] = urljoin(base_url, href)
                tag["target"] = "_blank"
                tag["rel"] = "noopener"

    # Drop now-empty leftovers (e.g. a <p> that only ever wrapped an ad div we
    # unwrapped down to nothing) so they don't render as dead vertical space.
    for tag in root.find_all(["p", "li", "blockquote"]):
        if not tag.get_text(strip=True) and not tag.find("img"):
            tag.decompose()

    out = "".join(str(c) for c in root.contents).strip()
    return out if ("<p" in out or "<h" in out or "<ul" in out or "<img" in out) else ""
