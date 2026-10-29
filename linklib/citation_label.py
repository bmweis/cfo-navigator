"""Chicago-style labels for a cited source: whose work it is, then the title, then the year.

    Mostly Metrics, *The Annual Planning Bible 2027 Edition* (2027)      (a report: italics)
    Tomasz Tunguz, "Why Revenue Isn't the Most Important Metric" (2013)  (an article: quotes)

A leaf module (no linklib imports). `make_label` builds the small dict stored on a
citation entry at the moment the sources are sent (`label` = {org, work, year, kind});
`label_text` / `label_html` render it. The JavaScript twin is `citeLabelHtml` in the
Buddy page script; tests/test_citation_labels.py runs both on the same fixtures.

Rules, in order:
- org: author, else source (publication or publisher), else the domain of the URL.
  A value that is itself a URL is replaced by its domain.
- work: the title. A title that is only a URL is dropped (the domain stands in as the
  org); a title identical to the org adds nothing and is dropped.
- year: the first four-digit year in the published or report year, else none.
- kind: "report" (reports, guides, books, digests of them) is italic; anything else
  is an article and is quoted.
A citation stored before labels existed has no `label`; it renders its title as before.
"""
from __future__ import annotations

import re
from html import escape
from urllib.parse import urlparse

_URL_LIKE = re.compile(r"^(?:https?://|www\.)\S+$|^[a-z0-9-]+(?:\.[a-z0-9-]+)+(?:/\S*)?$", re.I)
_YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")


def _clean(v) -> str:
    return " ".join(str(v or "").split())


def is_url_like(s: str) -> bool:
    s = _clean(s)
    return bool(s) and bool(_URL_LIKE.match(s))


def domain_of(url: str) -> str:
    u = _clean(url)
    if not u:
        return ""
    if "//" not in u:
        u = "//" + u
    host = (urlparse(u).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def make_label(*, author="", source="", title="", year="", url="", kind="article") -> dict:
    org = _clean(author) or _clean(source)
    if is_url_like(org):
        org = domain_of(org)
    if not org:
        org = domain_of(url)
    work = _clean(title)
    if is_url_like(work):
        work = ""
    if work and org and work.casefold() == org.casefold():
        work = ""
    m = _YEAR.search(str(year or ""))
    return {"org": org, "work": work, "year": m.group(1) if m else "",
            "kind": "report" if kind == "report" else "article"}


def _parts(c: dict) -> dict:
    lab = c.get("label") if isinstance(c, dict) else None
    if isinstance(lab, dict):
        return {"org": _clean(lab.get("org")), "work": _clean(lab.get("work")),
                "year": _clean(lab.get("year")), "kind": "report" if lab.get("kind") == "report" else "article"}
    # Stored before labels existed: the title as it was, a bare URL title shown as its domain.
    t = _clean((c or {}).get("title"))
    if is_url_like(t) or not t:
        t = domain_of((c or {}).get("url") or t)
    return {"org": "", "work": t, "year": "", "kind": "plain"}


def label_text(c: dict, markdown: bool = False) -> str:
    """Plain text. With markdown=True a report title is *italic* (for MCP callers)."""
    p = _parts(c)
    work = p["work"]
    if work and p["kind"] == "report":
        work = f"*{work}*" if markdown else work
    elif work and p["kind"] == "article":
        work = f'"{work}"'
    out = ", ".join(x for x in (p["org"], work) if x)
    if p["year"] and out:
        out += f" ({p['year']})"
    return out


def label_html(c: dict) -> str:
    p = _parts(c)
    work = escape(p["work"])
    if work and p["kind"] == "report":
        work = f"<i>{work}</i>"
    elif work and p["kind"] == "article":
        work = f"&quot;{work}&quot;"
    out = ", ".join(x for x in (escape(p["org"]), work) if x)
    if p["year"] and out:
        out += f" ({escape(p['year'])})"
    return out
