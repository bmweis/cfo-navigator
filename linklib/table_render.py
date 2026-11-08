"""Code-built figure tables for FP&A Buddy answers (spend and gross margin).

The model used to write these tables from the sent figure rows and kept getting them wrong (swapped labels,
an invented "Match note" column, rows out of size order, pasted prose in cells, two editions in one column).
Now the model writes a placeholder, {{table:spend}} or {{table:margins}}, where each table belongs, and this
module replaces it with a markdown pipe table built from the rows that were actually sent. Every cell comes
from a sent row's own fields; nothing is looked up, averaged or rewritten. The model never sees the table.

A leaf module apart from linklib.figures (pure functions, no I/O) so the agent and the tests share one copy.
Order: public (band-less) rows first, then bands smallest to largest by upper then lower edge, newest edition
first inside a band. Medians, means, targets and different bases are never merged into one range."""
from __future__ import annotations

import re

from . import figures as fg

KINDS = ("spend", "margins")
PLACEHOLDER = re.compile(r"\{\{\s*table\s*:\s*([A-Za-z_]+)\s*\}\}")
_PARTIAL_TAIL = re.compile(r"\{\{[^}]*$")
SPEND_ROW_CAP = 16
MARGIN_ROW_CAP = 12

_ITEM_ORDER = ("sm", "rd", "ga", "cs", "opex")
_ITEM_LABEL = {"sm": "S&M", "rd": "R&D", "ga": "G&A", "cs": "Customer success", "opex": "Total opex"}


def placeholder(kind: str) -> str:
    return "{{table:" + kind + "}}"


def _cell(s) -> str:
    """One value or a short label: no pipes, no line breaks, no stray spaces."""
    return re.sub(r"\s+", " ", str(s if s is not None else "")).replace("|", "/").strip()


def _unit(r: dict) -> str:
    u = (r.get("unit") or "").strip()
    return "%" if "%" in u or u.lower() in ("percent", "pct") else (" " + u if u else "")


def value_text(r: dict) -> str:
    v = _cell(r.get("value"))
    if "%" in v:
        return v
    return v + _unit(r)


def _basis_text(r: dict) -> str:
    g = r.get("basis_group")
    if g in ("revenue", "arr"):
        return {"revenue": "percent of revenue", "arr": "percent of ARR"}[g]
    d = _cell(r.get("denominator"))
    return d or "not stated"


def _is_target(r: dict) -> bool:
    return fg.is_estimate(r) or bool(re.search(r"\b(?:targets?|benchmark target|plan|goal)\b", (r.get("statistic") or "").lower()))


def statistic_text(r: dict) -> str:
    if _is_target(r):
        return "target"
    return _cell(r.get("statistic")) or "not stated"


def _band_text(r: dict) -> str:
    b = _cell(r.get("band"))
    if not b or fg.parse_band(b) is None and b.lower() in ("all", "n/a", "na", "none"):
        txt = "All sizes" if not b or b.lower() == "all" else b
    else:
        txt = b
    if r.get("match") == "adjacent_band" or r.get("band_context"):
        txt += " (adjacent band)" if b and fg.parse_band(b) is not None else " (all sizes, context)"
    return txt


def _edition_rank(r: dict) -> tuple:
    return (-fg._year_num(r.get("report_year")), -fg._year_num(r.get("data_period")))


def _size_rank(r: dict) -> tuple:
    """Band-less (public index) first, then smallest to largest by upper then lower edge."""
    b = (r.get("band") or "").strip()
    if not b or fg.parse_band(b) is None:
        return (0 if not b else 2, 0.0, 0.0, "")
    k = fg.band_sort_key(b)
    return (1, k[1], k[2], "")


def row_order_key(r: dict) -> tuple:
    return (_size_rank(r) + _edition_rank(r) + (fg.statistic_rank(r.get("statistic")),)
            + (str(r.get("source") or ""), str(r.get("figure_key"))))


def _item_of(r: dict) -> tuple[str, str]:
    """(sort key, label) for the line item a spend row is."""
    line = fg.expense_line_of(r.get("metric") or "", r.get("denominator") or "", r.get("unit") or "")
    if line:
        return (f"0{_ITEM_ORDER.index(line) if line in _ITEM_ORDER else 9}", _ITEM_LABEL.get(line, line))
    name = _cell(r.get("metric"))
    return ("1" + name.lower(), name)


def _report_text(r: dict, n: int | None) -> str:
    t = " ".join(x for x in (_cell(r.get("source")), _cell(r.get("report_year"))) if x)
    per = _cell(r.get("data_period"))
    if per:
        t += f", {per}"
    return t + (f" [{n}]" if n else "")


def _table(head: list[str], body: list[list[str]]) -> str:
    out = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    out += ["| " + " | ".join(row) + " |" for row in body]
    return "\n".join(out)


def _fmt_num(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else f"{x:.1f}".rstrip("0").rstrip(".")


def _ranges(rows: list[dict], cite) -> list[str]:
    """One short line per line item and (basis, statistic): the low and high of the sent points, each endpoint
    named with its source. Only points whose bands overlap the first, no target rows, at least two sources."""
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        if _is_target(r) or r.get("value_num") is None or r.get("match") not in ("exact", "adjacent_band"):
            continue
        if r.get("band_context") or not (r.get("band") or "").strip():
            continue
        groups.setdefault((_item_of(r)[1], _basis_text(r), statistic_text(r).lower()), []).append(r)
    lines: list[str] = []
    for key in sorted(groups, key=lambda k: (_item_of(groups[k][0])[0], k[1], k[2])):
        pts = sorted(groups[key], key=row_order_key)
        anchor = pts[0]
        pts = [p for p in pts if fg._bands_overlap(anchor.get("band"), p.get("band"))]
        # one point per source edition
        seen: set = set()
        uniq = []
        for p in pts:
            k2 = (p.get("series") or p.get("source"), p.get("report_year"))
            if k2 not in seen:
                seen.add(k2)
                uniq.append(p)
        if len(uniq) < 2:
            continue
        lo = min(uniq, key=lambda p: p["value_num"])
        hi = max(uniq, key=lambda p: p["value_num"])
        if lo["value_num"] == hi["value_num"]:
            continue
        item, basis, stat = key
        lines.append(f"- {item}, {stat}, {basis}: {value_text(lo)} to {value_text(hi)} ({len(uniq)} sources). "
                     f"Low: {_report_text(lo, cite(lo))}. High: {_report_text(hi, cite(hi))}.")
    return lines


def build_spend(rows: list[dict], cite) -> tuple[str, dict]:
    """(markdown, info). rows: the sent spend rows; cite(row) -> the [n] of the row's report document."""
    body_rows = sorted(rows, key=lambda r: (_item_of(r)[0],) + row_order_key(r))
    shown, capped = body_rows[:SPEND_ROW_CAP], max(len(body_rows) - SPEND_ROW_CAP, 0)
    bases = []
    for r in shown:
        b = _basis_text(r)
        if b not in bases:
            bases.append(b)
    head_txt = "Spend by function, " + (" and ".join(bases) if bases else "percent of revenue")
    body = [[_item_of(r)[1], _band_text(r), statistic_text(r), value_text(r), _basis_text(r), _report_text(r, cite(r))]
            for r in shown]
    md = f"**{head_txt}**\n\n" + _table(["Line item", "Band", "Statistic", "Value", "Basis", "Report and period"], body)
    rng = _ranges(shown, cite)
    if rng:
        md += "\n\nRanges across the sent points:\n" + "\n".join(rng)
    return md, {"rows": len(shown), "capped": capped, "ranges": len(rng)}


def build_margins(rows: list[dict], cite) -> tuple[str, dict]:
    ordered = sorted(rows, key=lambda r: (_cell(r.get("metric")).lower(),) + row_order_key(r))
    shown, capped = ordered[:MARGIN_ROW_CAP], max(len(ordered) - MARGIN_ROW_CAP, 0)
    # Public rows first for the whole table, then by metric, so a public index leads.
    shown.sort(key=lambda r: (_size_rank(r)[0] != 0, "gross" not in _cell(r.get("metric")).lower(),
                              _cell(r.get("metric")).lower()) + row_order_key(r))
    show_seg = len({(_cell(r.get("segment")).lower()) for r in shown}) > 1
    head = ["Metric", "Band"] + (["Segment"] if show_seg else []) + ["Statistic", "Value", "Basis", "Report and period"]
    body = []
    for r in shown:
        row = [_cell(r.get("metric")) or "Margin", _band_text(r)]
        if show_seg:
            row.append(_cell(r.get("segment")) or "All")
        row += [statistic_text(r), value_text(r), _cell(r.get("denominator")) or "not stated", _report_text(r, cite(r))]
        body.append(row)
    return "**Margins**\n\n" + _table(head, body), {"rows": len(shown), "capped": capped, "ranges": 0}


_COGS = re.compile(r"cost of revenue|\bcogs\b|cost of goods|\bhosting\b|cost of (?:subscription|services)|gross (?:margin|profit)", re.I)


def is_margin_row(r: dict) -> bool:
    """Gross margin, cost of revenue and its parts are results, not spend lines: they go in the margins table."""
    kind = fg.row_table_kind(r)
    return kind == "margin" or (kind == "spend_share" and bool(_COGS.search(r.get("metric") or "")))


def rows_for(kind: str, rows: list[dict]) -> list[dict]:
    if kind == "margins":
        return [r for r in rows if is_margin_row(r)]
    return [r for r in rows if fg.row_table_kind(r) == "spend_share" and not is_margin_row(r)]


def kinds_present(rows: list[dict]) -> list[str]:
    return [k for k in KINDS if rows_for(k, rows)]


def render_tables(text: str, rows: list[dict], cite) -> tuple[str, dict]:
    """Replace each table placeholder in `text` with its table; returns (text, info).
    Fallbacks (recorded in info["fallback"], never raised): a kind with rows and no placeholder is appended once
    at the end; a repeated placeholder is dropped after its first use; an unknown or row-less placeholder is
    removed. Nothing the model wrote outside a placeholder is touched."""
    info: dict = {"rendered": {}, "fallback": []}
    present = kinds_present(rows)
    done: set = set()

    def one(kind: str) -> str:
        fn = build_spend if kind == "spend" else build_margins
        md, meta = fn(rows_for(kind, rows), lambda r, _c=cite: _c(r))
        info["rendered"][kind] = meta
        bad = selfcheck(md, rows)
        if bad:
            info.setdefault("selfcheck", []).extend(bad)
        return md

    def sub(m: re.Match) -> str:
        kind = m.group(1).lower()
        if kind not in KINDS:
            info["fallback"].append(f"unknown placeholder: {m.group(0)}")
            return ""
        if kind not in present:
            info["fallback"].append(f"no rows for {kind}")
            return ""
        if kind in done:
            info["fallback"].append(f"repeated placeholder: {kind}")
            return ""
        done.add(kind)
        return "\n\n" + one(kind) + "\n\n"

    out = PLACEHOLDER.sub(sub, _PARTIAL_TAIL.sub("", text or ""))
    for kind in present:
        if kind not in done:
            info["fallback"].append(f"missing placeholder: {kind} table appended")
            out = out.rstrip() + "\n\n" + one(kind) + "\n"
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    return out, info


def selfcheck(markdown: str, rows: list[dict]) -> list[str]:
    """Flag-only: every Value, Statistic and Band cell of a rendered table equals a sent row's own value."""
    values = {value_text(r) for r in rows}
    bad: list[str] = []
    for line in (markdown or "").split("\n"):
        if not line.startswith("|") or set(line.replace("|", "").strip()) <= set("-: "):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells and cells[0] in ("Line item", "Metric"):
            continue
        txt = " ".join(cells)
        # locate the cells by header position: spend has Value at 3, margins at index of "Value"
        hdr_val = next((c for c in cells if re.fullmatch(r"[-+]?\d[\d.,]*(?: ?%| [A-Za-z$]+)?", c)), None)
        if hdr_val is not None and hdr_val not in values:
            bad.append(f"value not in a sent row: {hdr_val} ({txt[:60]})")
    return bad
