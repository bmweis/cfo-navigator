"""Server-side renderer for stored FP&A Buddy answers (2026-10).

The live /tools/fpa-buddy page renders an answer in the browser with a small
hand-rolled `mdToHtml`/`mdInline` pair (webapp/app.py). The server-rendered
surfaces (/ask/history, the Buddy past-questions rows, the admin feedback
card) used to show the same stored text as `_esc(text)` inside a `<p>`, so
paragraph breaks, `---` rules, `**bold**` and `##` headings all appeared as
literal characters. This module is the server twin of the live function: same
block rules, same inline rules, same [n] marker contract, so a stored answer
reads in the history the way it did live.

Trust model: an answer is model output, never admin-authored. Everything is
HTML-escaped first and only the fixed tags below are ever emitted. Raw HTML in
an answer stays inert text. Markdown links are NOT turned into anchors here
(the restricted side of the split with `_render_original_content_markdown`);
only a `[n]` marker that resolves inside the turn's own persisted citation
snapshot becomes a link, and only to an http(s) URL.

Not python-markdown: this mirrors the live JS rule for rule so the two cannot
drift in what they accept, and a parity test runs both on the same fixtures.
"""
from __future__ import annotations

import html
import re

_HEADING = re.compile(r"^(#{1,4})\s+(.*)$")
_OL = re.compile(r"^\d+\.\s+(.*)$")
_UL = re.compile(r"^[-*]\s+(.*)$")
_HR = re.compile(r"^(?:-{3,}|\*{3,}|_{3,})$")


def _esc(s: str) -> str:
    return html.escape(s, quote=True)


def _inline(s: str, cites: list[dict]) -> str:
    s = html.escape(s, quote=False)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"__([^_]+)__", r"<strong>\1</strong>", s)
    s = re.sub(r"(^|[^*])\*([^*\n]+)\*(?!\*)", r"\1<em>\2</em>", s)
    s = re.sub(r"(^|[^_])_([^_\n]+)_(?!_)", r"\1<em>\2</em>", s)

    def _cite(m: re.Match) -> str:
        i = int(m.group(1))
        if 1 <= i <= len(cites):
            c = cites[i - 1]
            url = str(c.get("url") or "")
            if re.match(r"https?://", url, re.I):
                return (f'<sup class="cite"><a href="{_esc(url)}" target="_blank" '
                        f'rel="noopener" title="{_esc(c.get("title") or "")}">[{i}]</a></sup>')
        return m.group(0)

    # Last, so injected markup is never re-processed; a literal [2026] stays text.
    return re.sub(r"\[(\d{1,2})\](?!\()", _cite, s)


def render_answer_markdown(text: str, cites: list[dict] | None = None) -> str:
    cites = cites or []
    out: list[str] = []
    para: list[str] = []
    list_type: str | None = None

    def close_list() -> None:
        nonlocal list_type
        if list_type:
            out.append(f"</{list_type}>")
            list_type = None

    def flush_para() -> None:
        nonlocal para
        if para:
            out.append("<p>" + "<br>".join(para) + "</p>")
            para = []

    for line in (text or "").split("\n"):
        t = line.strip()
        h, ol, ul = _HEADING.match(t), _OL.match(t), _UL.match(t)
        if _HR.match(t):
            flush_para(); close_list()
            out.append("<hr>")
        elif h:
            flush_para(); close_list()
            lvl = min(len(h.group(1)) + 2, 6)
            out.append(f"<h{lvl}>{_inline(h.group(2), cites)}</h{lvl}>")
        elif ol:
            flush_para()
            if list_type != "ol":
                close_list(); out.append("<ol>"); list_type = "ol"
            out.append(f"<li>{_inline(ol.group(1), cites)}</li>")
        elif ul:
            flush_para()
            if list_type != "ul":
                close_list(); out.append("<ul>"); list_type = "ul"
            out.append(f"<li>{_inline(ul.group(1), cites)}</li>")
        elif t == "":
            flush_para(); close_list()
        else:
            close_list()
            para.append(_inline(t, cites))
    flush_para(); close_list()
    return "".join(out)
