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
import itertools
import re

_HEADING = re.compile(r"^(#{1,4})\s+(.*)$")
_OL = re.compile(r"^\d+\.\s+(.*)$")
_UL = re.compile(r"^[-*]\s+(.*)$")
_HR = re.compile(r"^(?:-{3,}|\*{3,}|_{3,})$")


def _esc(s: str) -> str:
    return html.escape(s, quote=True)


_SCOPE_COUNTER = itertools.count(1)


def new_scope() -> str:
    """A fresh id prefix for one answer, so [n] markers and source rows of different
    answers on one page never share an id."""
    return f"s{next(_SCOPE_COUNTER)}"


def _inline(s: str, cites: list[dict], scope: str = "") -> str:
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
            if scope:
                # Sources list (2026-10): a marker jumps to its row in the answer's own
                # "Sources (N)" disclosure and opens it. The row carries the link out.
                rid = f"src-{scope}-{i}"
                return (f'<sup class="cite"><a href="#{rid}" data-cite="{rid}" '
                        f'title="{_esc(c.get("title") or "")}">[{i}]</a></sup>')
            if re.match(r"https?://", url, re.I):
                return (f'<sup class="cite"><a href="{_esc(url)}" target="_blank" '
                        f'rel="noopener" title="{_esc(c.get("title") or "")}">[{i}]</a></sup>')
        return m.group(0)

    # Last, so injected markup is never re-processed; a literal [2026] stays text.
    return re.sub(r"\[(\d{1,2})\](?!\()", _cite, s)


_TABLE_ROW = re.compile(r"^\|.*\|$")
# A citation marker written after a row's closing pipe ("| 72% | FY2025 |[1]") would make the row
# plain text. The marker is moved into the row's last cell, so the row stays a row.
_MARKER_AFTER_ROW = re.compile(r"^(\|.*?)\s*\|\s*((?:\[\d{1,2}\]\s*)+)$")
# A line holding only invisible characters is a blank line, never an empty paragraph.
_INVISIBLE = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff\u00a0]")


def move_row_markers(line: str) -> str:
    t = line.strip()
    if not t.startswith("|"):
        return line
    m = _MARKER_AFTER_ROW.match(t)
    if not m:
        return line
    return f"{m.group(1)} {m.group(2).strip()} |"
_TABLE_SEP_CELL = re.compile(r"^:?-{1,}:?$")


def _table_cells(t: str) -> list[str]:
    """Cells of one `| a | b |` row. A backslash-pipe is a literal pipe in a cell."""
    body = t[1:-1].replace("\\|", "\x00")
    return [c.strip().replace("\x00", "|") for c in body.split("|")]


def _try_table(lines: list[str], i: int, cites: list[dict], scope: str = "") -> tuple[str, int] | None:
    """A markdown table starting at lines[i], or None. A table is a header row,
    a separator row (`|---|---|`) with the same number of cells, then body rows
    with the same number of cells. Anything else (no separator, ragged rows)
    returns None, so the caller renders the lines as ordinary text and nothing
    is dropped or thrown."""
    head = lines[i].strip()
    if i + 1 >= len(lines) or not _TABLE_ROW.match(head):
        return None
    sep = lines[i + 1].strip()
    if not _TABLE_ROW.match(sep):
        return None
    heads, seps = _table_cells(head), _table_cells(sep)
    n = len(heads)
    if n < 2 or len(seps) != n or not all(_TABLE_SEP_CELL.match(c) for c in seps):
        return None
    body: list[list[str]] = []
    j = i + 2
    while j < len(lines) and _TABLE_ROW.match(lines[j].strip()):
        row = _table_cells(lines[j].strip())
        if len(row) != n:
            return None
        body.append(row)
        j += 1
    out = ['<div class="table-frame ask-table" style="overflow-x:auto;overflow-y:hidden;"><table><thead><tr>']
    out += [f"<th>{_inline(c, cites, scope)}</th>" for c in heads]
    out.append("</tr></thead><tbody>")
    for row in body:
        # data-label carries the column name for the stacked phone layout (CSS ::before).
        out.append("<tr>" + "".join(f'<td data-label="{_esc(heads[k])}">{_inline(c, cites, scope)}</td>'
                                    for k, c in enumerate(row)) + "</tr>")
    out.append("</tbody></table></div>")
    return "".join(out), j


def render_answer_markdown(text: str, cites: list[dict] | None = None, scope: str = "") -> str:
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

    lines = [move_row_markers(x) for x in (text or "").split("\n")]
    i = 0
    while i < len(lines):
        t = _INVISIBLE.sub("", lines[i]).strip() if not lines[i].strip().startswith("|") else lines[i].strip()
        tbl = _try_table(lines, i, cites, scope) if t.startswith("|") else None
        if tbl:
            flush_para(); close_list()
            out.append(tbl[0])
            i = tbl[1]
            continue
        i += 1
        h, ol, ul = _HEADING.match(t), _OL.match(t), _UL.match(t)
        if _HR.match(t):
            flush_para(); close_list()
            out.append("<hr>")
        elif h:
            flush_para(); close_list()
            lvl = min(len(h.group(1)) + 2, 6)
            out.append(f"<h{lvl}>{_inline(h.group(2), cites, scope)}</h{lvl}>")
        elif ol:
            flush_para()
            if list_type != "ol":
                close_list(); out.append("<ol>"); list_type = "ol"
            out.append(f"<li>{_inline(ol.group(1), cites, scope)}</li>")
        elif ul:
            flush_para()
            if list_type != "ul":
                close_list(); out.append("<ul>"); list_type = "ul"
            out.append(f"<li>{_inline(ul.group(1), cites, scope)}</li>")
        elif t == "":
            flush_para(); close_list()
        else:
            close_list()
            para.append(_inline(t, cites, scope))
    flush_para(); close_list()
    return "".join(out)


# --- "Go deeper" line ---------------------------------------------------------
# Synthesize, then point to the source: an answer that cites stored benchmark
# figures ends with one line naming each report it used, linked to the report's
# landing page (the same URL its citation chip carries). Built from the turn's
# own citations, never written by the model, so a link can't be invented. The
# live page has a JS twin (goDeeperHtml); the server surfaces call this.
FIGURES_TITLE_PREFIX = "Figures: "


def go_deeper_items(cites: list[dict] | None) -> list[dict]:
    """[{name, html, url}] for each distinct benchmark report the answer cited, in
    citation order. `name` is the plain Chicago-style label, `html` the same with the
    report title in italics; url is "" for a report with no landing URL (named, not
    linked). A non-http(s) url is treated as no link. A report is a citation that
    carries `figures`, or (stored before labels existed) a "Figures: " title."""
    from linklib.citation_label import label_html, label_text
    out: list[dict] = []
    seen: set[str] = set()
    for c in cites or []:
        if not isinstance(c, dict):
            continue
        title = str(c.get("title") or "")
        legacy = title.startswith(FIGURES_TITLE_PREFIX)
        if not (c.get("figures") or legacy):
            continue
        if legacy and not c.get("label"):
            name = title[len(FIGURES_TITLE_PREFIX):].strip()
            name_html = _esc(name)
        else:
            name, name_html = label_text(c), label_html(c)
        if not name or name in seen:
            continue
        seen.add(name)
        url = str(c.get("url") or "")
        out.append({"name": name, "html": name_html, "url": url if re.match(r"https?://", url, re.I) else ""})
    return out


def go_deeper_html(cites: list[dict] | None) -> str:
    items = go_deeper_items(cites)
    if not items:
        return ""
    parts = []
    for it in items:
        if it["url"]:
            parts.append(f'<a href="{_esc(it["url"])}" target="_blank" rel="noopener">{it["html"]}</a>')
        else:
            parts.append(it["html"])
    return ('<p class="ask-godeeper" style="margin:8px 0 0;font-size:13px;color:var(--ink-soft);">'
            "Go deeper: " + ", ".join(parts) + "</p>")



# --- "Sources (N)" disclosure (2026-10) ----------------------------------------
# One list under every answer, collapsed, one source per row with the same structure:
# [n], a fixed-width type icon, then the label with a hanging indent. The JS twin is
# srcListHtml() on the live page; webapp/app.py holds the CSS (.ask-src-*).
SOURCE_ICONS = {"library": "&#128218;", "feed": "&#128240;", "web": "&#127760;"}


def sources_disclosure_html(cites: list[dict] | None, scope: str) -> str:
    from linklib.citation_label import label_html
    cites = [c for c in (cites or []) if isinstance(c, dict)]
    if not cites:
        return ""
    rows = []
    for c in cites:
        n = c.get("n")
        n = str(n) if n is not None else ""
        label = label_html(c) or _esc(c.get("url") or "")
        url = str(c.get("url") or "")
        tags = ""
        try:
            if c.get("article_id") is not None:
                tags += f'<span class="ask-src-own">archive #{int(c["article_id"])}</span>'
        except (TypeError, ValueError):
            pass
        if c.get("own_content"):
            tags += '<span class="ask-src-own">(own writing)</span>'
        if c.get("type") == "web" and c.get("trusted") is False:
            tags += '<span class="ask-src-open">Open web</span>'
        if re.match(r"https?://", url, re.I):
            body = f'<a href="{_esc(url)}" target="_blank" rel="noopener">{label}</a>'
        else:
            body = f'<span>{label}</span>'
            tags += '<span class="ask-src-open">no link on file</span>'
        rows.append(
            f'<li id="src-{scope}-{_esc(n)}" class="ask-src-row" tabindex="-1">'
            f'<span class="ask-src-n">[{_esc(n)}]</span>'
            f'<span class="ask-src-ic" aria-hidden="true">{SOURCE_ICONS.get(c.get("type"), "")}</span>'
            f'<span class="ask-src-body">{body}{tags}</span></li>')
    return (f'<details class="ask-src-disc"><summary>Sources ({len(cites)})</summary>'
            f'<ul class="ask-src-list">{"".join(rows)}</ul></details>')
