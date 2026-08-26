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
import re
from dataclasses import dataclass
from html import escape
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup, Comment

# Standard browser headers, used as the DEFAULT for every fetch (fetch_page,
# below) — not just the auth-cookie path it was originally added for
# ("impersonating the logged-in browser"). A Phase 5b follow-up investigation
# found the previous identifiable bot string ("Mozilla/5.0 (compatible;
# linklib/1.0)") provides zero benefit over declaring it honestly: three real
# 403s from the backfill's flagged domains (bothsidesofthetable.com,
# medium.com, pointsandfigures.com) returned the identical Cloudflare "Just a
# moment..." challenge with EITHER header set — confirmed with real requests
# against real failing URLs, not assumed. Cloudflare's bot management
# fingerprints the TLS handshake/connection behavior, not the UA string, so no
# UA swap alone gets past it (see looks_like_bot_challenge(), unchanged). Kept
# as the default anyway: no downside, and it may still help against a site
# doing a naive UA-string check somewhere in the wider ~4,500-article corpus
# outside this one flagged batch. Cloudflare cf_clearance is also IP-bound, so
# this helps reduce WAF friction but isn't a guarantee either way.
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
    fetch_error: str = ""    # populated only when the request itself failed (timeout,
                              # DNS, connection refused, non-2xx status) — see
                              # _describe_fetch_error(). Every other caller of fetch_page
                              # (ingest_url, the Reader's live-fetch path) has always
                              # swallowed this silently and still does — it's opt-in,
                              # read by linklib.pipeline.backfill_article_content so a
                              # content_refetch_log row records *why* a fetch failed
                              # (a 404, a timeout, a connection reset) instead of just
                              # "fetch-error" with no detail.


def _describe_fetch_error(exc: Exception) -> str:
    """A short, specific description of a request failure — the difference
    between "this URL is dead (404)" and "this host is timing out on every
    request" matters when deciding whether a batch of failures is a source-
    wide problem worth investigating before a full run repeats it across
    every article from that source."""
    if isinstance(exc, requests.exceptions.HTTPError):
        status = exc.response.status_code if exc.response is not None else "?"
        return f"HTTP {status}"
    if isinstance(exc, requests.exceptions.Timeout):
        return "timeout"
    if isinstance(exc, requests.exceptions.SSLError):
        return f"SSL error: {exc}"
    if isinstance(exc, requests.exceptions.ConnectionError):
        return f"connection error: {exc}"
    return f"{type(exc).__name__}: {exc}"


def _page_data_from_html(html: str) -> PageData:
    """Build a PageData from already-fetched HTML — the shared back half of
    fetch_page(), factored out so a caller with HTML from somewhere OTHER
    than a fresh live request (namely: a Wayback Machine snapshot, see
    linklib.wayback) can run through the exact same title/content/paywall/
    published-date extraction fetch_page() would have used, rather than a
    second, parallel implementation that could drift from it. `raw_html` is
    always populated (unlike fetch_page's failure path); `fetch_error` is
    always empty here — that field only means anything for an actual failed
    HTTP request, which this function never makes."""
    title = _extract_title(html)
    content = _extract_content(html)
    return PageData(title=title, content=content,
                    blocked=looks_paywalled(html, content),
                    published=_extract_published(html),
                    raw_html=html)


def _with_www(url: str) -> str:
    """Return `url` with a `www.` prefix added to its host, or "" if it
    already has one (or the host can't be parsed) — used only by
    fetch_page()'s bare-domain retry below."""
    parts = urlsplit(url)
    host = parts.netloc
    if not host or host.lower().startswith("www."):
        return ""
    return parts._replace(netloc="www." + host).geturl()


def fetch_page(url: str, timeout: int = 20) -> PageData:
    """Fetch a URL once and return both the page title and cleaned body text.

    Always returns a PageData; fields are empty strings on failure. Sends a
    standard browser User-Agent by default (see _BROWSER_HEADERS — a Phase 5b
    follow-up investigation confirmed the previous identifiable bot string
    provided no benefit against real 403s, so there's no reason to keep
    declaring it). For domains with a configured auth cookie
    (LINKLIB_AUTH_COOKIES), the same browser headers are sent plus the cookie,
    so subscriber-only full text is fetched instead of a preview. `blocked`
    flags a response that still looks paywalled — the signal that a
    configured cookie is missing or expired. `fetch_error` carries the reason
    for a request-layer failure (see _describe_fetch_error) — every existing
    caller already treats a failed fetch as "nothing usable" and ignores this
    field, so populating it changes nothing for them.

    Bare-domain `www.` retry (2026-08): confirmed via
    scripts/diagnose_reader_backfill_failures.py against production that
    codingvc.com refuses the connection at its bare domain while
    www.codingvc.com serves the same page fine — a real fetcher gap, not a
    data/URL-correction issue, since nothing about the stored URL is wrong.
    Scoped narrowly to connection-level failures only (DNS/refused/
    unreachable — a requests.exceptions.ConnectionError that isn't an
    HTTPError) — an HTTP-status failure (403, 404, ...) is never retried
    this way, since that's not a www problem: confirmed the same day on
    inc.com, which 403s identically on both the bare and www hosts. Only
    fires when the URL doesn't already have a `www.` host, and only as a
    fallback after the bare-domain attempt has already failed.
    """
    cookie = _cookie_for(url)
    headers = dict(_BROWSER_HEADERS)
    if cookie:
        headers["Cookie"] = cookie
    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
        resp.raise_for_status()
        html = resp.text
    except requests.exceptions.ConnectionError as exc:
        www_url = _with_www(url)
        if not www_url:
            return PageData(title="", content="", fetch_error=_describe_fetch_error(exc))
        try:
            resp = requests.get(www_url, headers=headers, timeout=timeout)
            resp.raise_for_status()
            html = resp.text
        except Exception as retry_exc:
            return PageData(title="", content="", fetch_error=_describe_fetch_error(retry_exc))
    except Exception as exc:
        return PageData(title="", content="", fetch_error=_describe_fetch_error(exc))

    return _page_data_from_html(html)


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
    """Best-effort page title: prefer the <title> tag, but fall back to
    og:title/twitter:title meta tags when it's missing or blank.

    Reproduced against real bookmarklet saves (2026-08): a site like Medium
    server-renders the article body and its social meta tags (og:title,
    twitter:title) for SEO/link-preview purposes, but leaves <title> empty
    or a placeholder until client-side JS sets document.title on hydration
    — a plain requests.get (no JS) never sees that update. Previously this
    returned "" in that case, which linklib.pipeline.ingest_url then stored
    the raw URL as the article's title. og:title/twitter:title are reliably
    present even when <title> isn't, so they're tried next rather than
    giving up. Returns "" only when none of the three are found — callers
    (ingest_url) decide the URL fallback for that genuinely titleless case."""
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return ""

    tag = soup.find("title")
    if tag:
        text = tag.get_text(strip=True)
        if text:
            return text

    for prop in ("og:title", "twitter:title"):
        meta = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
        if meta:
            content = (meta.get("content") or "").strip()
            if content:
                return content

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


# --- Medium-platform Exa-text -> Reader HTML -----------------------------
#
# Exa's Search API returns Medium-platform articles as plain markdown-ish
# text, not HTML (Medium's own markup is React-rendered client-side and
# opaque to a normal fetch, which is the entire reason the Medium fetch tier
# goes through Exa in the first place — see linklib/medium_platform.py).
# This turns that plain text into the same kind of structural HTML
# extract_reader_html() produces from real markup, so a Medium-sourced
# article reads identically to any other in the Reader — the Reader gives a
# consistent house reading experience regardless of publisher; the source is
# an input to normalize, not a style to preserve.
#
# Formatting rules (resolved with Brian, 2026-08-18): headings become h2/h3
# (capped — Exa text doesn't reliably distinguish deeper levels), emphasis
# markers (*/**/_/__) are stripped rather than converted to <em>/<strong>
# (goal is the cleanest possible read, not a markdown-parity render), links
# are stripped to their visible text only (the URL adds nothing in a
# plain-text extract and Medium's own link text is often already the
# reference), paragraphs split on blank lines. Strip and simplify, never
# restructure.
_MIN_READ_RE = re.compile(r"^\d+\s*min\s*read$", re.IGNORECASE)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
# [text](url) or bare markdown link syntax -> keep only the visible text.
_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
# Matched emphasis markers only (not stray asterisks/underscores elsewhere,
# e.g. "3*4" or a filename) — strip the markers, keep the wrapped text.
_EMPHASIS_RE = re.compile(r"(\*\*|__)(.+?)\1|(\*|_)(.+?)\3")

# Known Medium chrome lines that precede the real article body in Exa's
# extracted text — "Open in app", "Sign up", "Get app", a lone "Follow",
# member-paywall prompts, and the "N min read" byline line. Matched
# case-insensitively against the whole line, not substring — conservative on
# purpose: a real paragraph that happens to contain one of these words (e.g.
# a sentence mentioning "sign up for our newsletter") must never be dropped,
# only a line that IS one of these phrases in its entirety. Only applied as
# a leading strip (see _is_medium_chrome_paragraph below) — once a line
# doesn't match, stripping stops for good, so a real paragraph can never be
# eaten even if it happens to echo one of these phrases later in the piece.
_MEDIUM_CHROME_PHRASES = {
    "open in app",
    "sign up",
    "sign in",
    "get app",
    "get the app",
    "follow",
    "listen",
    "share",
}


def _normalize_chrome_check(line: str) -> str:
    return line.strip().strip(".").lower()


def _is_medium_chrome_paragraph(line: str) -> bool:
    norm = _normalize_chrome_check(line)
    if not norm:
        return True
    if norm in _MEDIUM_CHROME_PHRASES:
        return True
    if _MIN_READ_RE.match(norm):
        return True
    return False


def _strip_markdown_inline(text: str) -> str:
    text = _LINK_RE.sub(r"\1", text)
    text = _EMPHASIS_RE.sub(lambda m: m.group(2) or m.group(4) or "", text)
    return text


def paragraphs_html_from_text(text: str) -> str:
    """Turn Exa's plain-text Medium-article extract into Reader-consistent
    HTML: real <h2>/<h3>/<p> structure, a leading strip of known Medium
    navigation chrome, and markdown link/emphasis syntax reduced to plain
    text. Deliberately conservative — see the module comment above and
    _is_medium_chrome_paragraph's docstring for why the chrome strip only
    ever eats from the top and stops at the first line that isn't chrome.
    Returns "" on empty/whitespace-only input."""
    if not text or not text.strip():
        return ""

    raw_blocks = [b.strip() for b in re.split(r"\n\s*\n", text.strip())]
    raw_blocks = [b for b in raw_blocks if b]

    # Strip leading Medium chrome blocks (each block is normally one line,
    # but treat a multi-line block as chrome only if every line in it is
    # chrome, so a real paragraph that happens to start on the same line as
    # trailing chrome is never eaten).
    idx = 0
    while idx < len(raw_blocks):
        lines = raw_blocks[idx].splitlines()
        if lines and all(_is_medium_chrome_paragraph(ln) for ln in lines):
            idx += 1
            continue
        break
    blocks = raw_blocks[idx:]
    if not blocks:
        return ""

    # The "N min read" byline is an unambiguous regex match — no real
    # sentence is ever literally just that phrase — so unlike the fuzzy
    # chrome-phrase list above, it's safe to drop wherever it lands, not
    # just while still in the leading run. It typically follows the title
    # heading (title, then "9 min read", then the real body), which is
    # past the point the leading-chrome strip above already stopped at.
    blocks = [b for b in blocks if not (
        "\n" not in b and _MIN_READ_RE.match(_normalize_chrome_check(b)))]
    if not blocks:
        return ""

    html_parts = []
    for block in blocks:
        heading_match = _HEADING_RE.match(block)
        if heading_match:
            level = len(heading_match.group(1))
            tag = "h2" if level <= 2 else "h3"
            content = _strip_markdown_inline(heading_match.group(2)).strip()
            if content:
                html_parts.append(f"<{tag}>{escape(content)}</{tag}>")
            continue
        # A short standalone line in Title Case with no trailing punctuation
        # reads as a subheading even without markdown "#" markers — Medium's
        # own in-article section headers arrive this way in Exa's plain-text
        # extract, with no markdown syntax surviving the conversion.
        single_line = "\n" not in block
        if (single_line and len(block) <= 80 and not block.endswith((".", "!", "?", ":", ","))
                and block[:1].isupper()):
            content = _strip_markdown_inline(block).strip()
            if content:
                html_parts.append(f"<h3>{escape(content)}</h3>")
            continue
        content = _strip_markdown_inline(block).strip()
        if not content:
            continue
        # A blank line inside a kept paragraph block was already the
        # boundary that split blocks in the first place; a single newline
        # within a block is a soft wrap — join it into one flowing
        # paragraph rather than rendering a mid-sentence <br>.
        content = " ".join(line.strip() for line in content.splitlines() if line.strip())
        html_parts.append(f"<p>{escape(content)}</p>")

    return "".join(html_parts)
