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

KINDS = ("headcount", "mix", "spend", "margins")
PLACEHOLDER = re.compile(r"\{\{\s*table\s*:\s*([A-Za-z_]+)\s*\}\}")
_PARTIAL_TAIL = re.compile(r"\{\{[^}]*$")
SPEND_ROW_CAP = 16
MARGIN_ROW_CAP = 12
COUNT_ROW_CAP = 12
MIX_COLS_CAP = 4
HEADER_MAX = 40       # characters of a mix column header before the period moves under the table

_ITEM_ORDER = ("sm", "rd", "ga", "cs", "opex")
_ITEM_LABEL = {"sm": "S&M", "rd": "R&D", "ga": "G&A", "cs": "Customer success", "opex": "Total opex"}


def placeholder(kind: str) -> str:
    return "{{table:" + kind + "}}"


def _cell(s) -> str:
    """One value or a short label: no pipes, no line breaks, no stray spaces."""
    return re.sub(r"\s+", " ", str(s if s is not None else "")).replace("|", "/").strip()


_COUNT_UNITS = ("count", "fte", "ftes", "employees", "people", "headcount")
_LONG_DECIMAL = re.compile(r"(?<![\d.])([-+]?\d[\d,]*)\.(\d{3,})")


def _unit(r: dict) -> str:
    u = (r.get("unit") or "").strip()
    if "%" in u or u.lower() in ("percent", "pct"):
        return "%"
    if u.lower() in _COUNT_UNITS:
        return ""
    return " " + u if u else ""


def round_text(v: str) -> str:
    """Never more digits than a report prints: a number with three or more decimals (74.5783946473) is shown to
    one decimal (74.6). One or two decimals are left as stored. Display only; the stored figure is unchanged."""
    def one(m: re.Match) -> str:
        try:
            return f"{round(float(m.group(1).replace(',', '') + '.' + m.group(2)), 1):.1f}"
        except ValueError:
            return m.group(0)
    return _LONG_DECIMAL.sub(one, v)


def value_text(r: dict) -> str:
    v = round_text(_cell(r.get("value")))
    if "%" in v:
        return v
    return v + _unit(r)


_PLAIN_BAND = re.compile(r"^(?:under |over |<|>)?\$\d[\d.,]*[mkb]?(?:\s*(?:-|to)\s*\$?\d[\d.,]*[mkb]?)?\+?$", re.I)


def _money(x: float) -> str:
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if x >= div:
            n = x / div
            return (str(int(n)) if float(n).is_integer() else f"{n:g}") + suf
    return str(int(x))


def band_display(b: str) -> str:
    """One spelling for a size band in a table: "$20M-$50M" and "$20-50M" both read "$20-50M"; "Under $1M" reads
    "<$1M". Only a plain money band is respelled; anything else (a caveat in the text, a segment, "All") is
    shown as printed. Display only; the stored band is unchanged."""
    t = _cell(b)
    if not t or not _PLAIN_BAND.match(t):
        return t
    p = fg.parse_band(t)
    if p is None or p["kind"] != "money":
        return t
    lo, hi = p["lo"], p["hi"]
    if hi == float("inf"):
        return f"${_money(lo)}+"
    if lo == 0:
        return f"<${_money(hi)}"
    if lo == hi:
        return t
    a, bb = _money(lo), _money(hi)
    return f"${a[:-1]}-{bb}" if a[-1] == bb[-1] else f"${a}-${bb}"


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
        txt = band_display(b)
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


def _dropped(rows: list[dict], kept: list[dict]) -> list[dict]:
    keep = {id(r) for r in kept}
    return [{"figure_key": r.get("figure_key"), "report": r.get("report_slug"), "metric": _cell(r.get("metric")),
             "band": _cell(r.get("band"))} for r in rows if id(r) not in keep][:20]


ORDER_NOTE = "Rows run public first, then by size band, newest edition first."


def select_rows(rows: list[dict], cap: int, within) -> list[dict]:
    """The rows a capped table keeps, chosen BEFORE the display sort so the cap never cuts the newest data.
    Rule: every publisher's newest edition per band comes first, taking one row from each publisher-and-band group
    in turn (public, band-less groups ahead of banded ones, then the more recent edition), then the next row of
    each group, and so on; rows of an older edition fill what is left. `within` orders the rows inside one group.
    Returns at most `cap` rows; the caller sorts them for display."""
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        groups.setdefault((fg._edition_key(r)[0], fg._band_key(r.get("band"))), []).append(r)
    ranked: list[tuple] = []
    for g in groups.values():
        newest = min(g, key=_edition_rank).get("report_slug")
        cur = sorted([r for r in g if r.get("report_slug") == newest], key=within)
        old = sorted([r for r in g if r.get("report_slug") != newest], key=lambda r: (_edition_rank(r), within(r)))
        for i, r in enumerate(cur):
            ranked.append((0, i, _size_rank(r)[0] != 0) + _edition_rank(r) + _size_rank(r)[1:3] + (str(r.get("source") or ""), str(r.get("figure_key")), id(r), r))
        for i, r in enumerate(old):
            ranked.append((1,) + _edition_rank(r) + (i,) + (str(r.get("source") or ""), str(r.get("figure_key")), id(r), r))
    ranked.sort(key=lambda t: t[:-2])
    return [t[-1] for t in ranked[:cap]]


def build_spend(rows: list[dict], cite) -> tuple[str, dict]:
    """(markdown, info). rows: the sent spend rows; cite(row) -> the [n] of the row's report document."""
    kept = select_rows(rows, SPEND_ROW_CAP, lambda r: (_item_of(r)[0], fg.statistic_rank(r.get("statistic"))))
    shown = sorted(kept, key=lambda r: (_item_of(r)[0],) + row_order_key(r))
    capped = max(len(rows) - len(shown), 0)
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
    return md, {"rows": len(shown), "capped": capped, "ranges": len(rng), "dropped": _dropped(rows, kept)}


def margin_sort_key(r: dict) -> tuple:
    """Public (band-less) rows first, then bands smallest to largest by upper then lower edge, the newest edition
    first inside a band, gross margin before another margin, then metric name and statistic."""
    m = _cell(r.get("metric")).lower()
    return _size_rank(r) + _edition_rank(r) + ("gross" not in m, m, fg.statistic_rank(r.get("statistic")),
                                              str(r.get("source") or ""), str(r.get("figure_key")))


def build_margins(rows: list[dict], cite) -> tuple[str, dict]:
    kept = select_rows(rows, MARGIN_ROW_CAP, lambda r: ("gross" not in _cell(r.get("metric")).lower(), _cell(r.get("metric")).lower(),
                                                       fg.statistic_rank(r.get("statistic"))))
    shown = sorted(kept, key=margin_sort_key)
    capped = max(len(rows) - len(shown), 0)
    show_seg = len({(_cell(r.get("segment")).lower()) for r in shown}) > 1
    head = ["Metric", "Band"] + (["Segment"] if show_seg else []) + ["Statistic", "Value", "Basis", "Report and period"]
    body = []
    for r in shown:
        row = [_cell(r.get("metric")) or "Margin", _band_text(r)]
        if show_seg:
            row.append(_cell(r.get("segment")) or "All")
        row += [statistic_text(r), value_text(r), _cell(r.get("denominator")) or "not stated", _report_text(r, cite(r))]
        body.append(row)
    note = "\n\n" + ORDER_NOTE
    return ("**Margins**\n\n" + _table(head, body) + note,
            {"rows": len(shown), "capped": capped, "ranges": 0, "dropped": _dropped(rows, kept), "sort": "public first, band upper then lower edge, newest edition first, gross before other margins"})


# --- headcount counts and headcount mix --------------------------------------------------------------

def _is_delta(r: dict) -> bool:
    return (r.get("data_period") or "").strip().lower().startswith("change")


def _prefer(rows: list[dict]) -> dict:
    """Of rows that print one figure, the original publisher's row before a republisher's (cited_source set)."""
    return sorted(rows, key=lambda r: (bool(r.get("cited_source")), str(r.get("source") or ""), str(r.get("figure_key"))))[0]


def count_rows(rows: list[dict]) -> list[dict]:
    """Total headcount (FTE) rows: a number of people, never a change row, one per edition, band, period, statistic
    and denominator. Different denominators or statistics are different rows."""
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        if fg.row_table_kind(r) != "headcount_count" or not fg.is_total_fte_row(r) or _is_delta(r) or r.get("value_num") is None:
            continue
        k = (fg._edition_key(r), fg._band_key(r.get("band")), fg._year_num(r.get("data_period")),
             (r.get("statistic") or "").strip().lower(), fg._denom_key(r.get("denominator")))
        groups.setdefault(k, []).append(r)
    return [_prefer(g) for g in groups.values()]


def build_headcount(rows: list[dict], cite) -> tuple[str, dict]:
    picked = sorted(count_rows(rows), key=row_order_key)
    shown, capped = picked[:COUNT_ROW_CAP], max(len(picked) - COUNT_ROW_CAP, 0)
    body = [[_report_text(r, cite(r)), _band_text(r), statistic_text(r), value_text(r), _cell(r.get("denominator")) or "not stated"]
            for r in shown]
    md = "**Total headcount (a number of people)**\n\n" + _table(["Report and period", "Band", "Statistic", "Value", "Basis"], body)
    return md, {"rows": len(shown), "capped": capped, "ranges": 0}


def mix_share_rows(rows: list[dict]) -> list[dict]:
    return [r for r in rows if fg.row_table_kind(r) == "headcount_share" and not fg.is_total_fte_row(r)
            and fg.mix_function_ok(r) and not _is_delta(r) and r.get("value_num") is not None]


def _function_name(r: dict) -> str:
    f = fg.share_function(r).strip()
    return (f[:1].upper() + f[1:]) if f and f[0].islower() else f


def _find_total(share: dict, totals: list[dict]):
    """The same edition's total headcount for a share's band and period (a republisher's share matches its
    original's). Same statistic first, then the median; None when there is no total for that band and period."""
    k = (fg._edition_key(share), fg._band_key(share.get("band")), fg._year_num(share.get("data_period")))
    cands = [t for t in totals
             if (fg._edition_key(t), fg._band_key(t.get("band")), fg._year_num(t.get("data_period"))) == k
             and (t.get("report_slug") == share.get("report_slug") or share.get("cited_source") or t.get("cited_source"))]
    if not cands:
        return None
    st = (share.get("statistic") or "").strip().lower()
    return (next((t for t in cands if (t.get("statistic") or "").strip().lower() == st), None)
            or next((t for t in cands if (t.get("statistic") or "").strip().lower() == "median"), None) or cands[0])


def build_mix(rows: list[dict], cite) -> tuple[str, dict]:
    """Functions down, one edition per value column (newest first, exact band before adjacent), functions in the
    order the report prints them, then a derived people-count column for the first column that has a matching
    total. An older edition, a column past the cap and a row whose function the data does not name are listed in
    one line under the table; none of them is added as a row or merged."""
    share = mix_share_rows(rows)
    unnamed = [r for r in rows if fg.row_table_kind(r) == "headcount_share" and not fg.is_total_fte_row(r)
               and not _is_delta(r) and not fg.mix_function_ok(r)]
    cols: dict[tuple, list[dict]] = {}
    for r in share:
        cols.setdefault((fg._edition_key(r), fg._band_key(r.get("band")), fg._year_num(r.get("data_period"))), []).append(r)

    def col_rank(item):
        k, rs = item
        exact = min((0 if r.get("match") == "exact" else 1) for r in rs)
        return (exact, -k[0][1], -k[2], str(k[0][0]), str(k[1]))
    ordered = sorted(cols.items(), key=col_rank)
    # one column per publisher edition: when a publisher prints two bands, only the closest stays a column
    seen_ed: set = set()
    chosen, older = [], []
    for k, rs in ordered:
        if k[0][0] in seen_ed:                       # one column per publisher: its newest edition, closest band
            older.append((k, rs))
        else:
            seen_ed.add(k[0][0])
            chosen.append((k, rs))
    extra_cols = chosen[MIX_COLS_CAP:]
    chosen = chosen[:MIX_COLS_CAP]

    totals = count_rows(rows)
    col_data = []                       # (header, {fkey: row})
    fnames: dict[str, str] = {}
    forder: list[str] = []
    allowed_extra: list[str] = []
    for k, rs in chosen:
        by_f: dict[str, list[dict]] = {}
        for r in sorted(rs, key=lambda r: (str(r.get("report_slug")), r.get("figure_key") if isinstance(r.get("figure_key"), int) else 0)):
            by_f.setdefault(fg.function_key(fg.share_function(r)), []).append(r)
        picked = {f: _prefer(g) for f, g in by_f.items()}
        head_row = _prefer(list(picked.values()))
        for f, g in by_f.items():
            vals = {value_text(x) for x in g}
            if len(vals) > 1:
                allowed_extra.append("conflict:" + f)
        for f, r in sorted(picked.items(), key=lambda kv: (kv[1].get("figure_key") if isinstance(kv[1].get("figure_key"), int) else 0)):
            if f not in fnames:
                fnames[f] = _function_name(r)
                forder.append(f)
        col_data.append((head_row, picked))

    if not col_data:
        return "", {"rows": 0, "capped": 0, "ranges": 0}

    # the derived column: the first value column with a matching total
    derived_idx, derived_total = None, None
    for i, (hr, picked) in enumerate(col_data):
        t = _find_total(hr, totals)
        if t is not None:
            derived_idx, derived_total = i, t
            break

    head = ["Function"]
    col_notes: list[str] = []
    reprint_only: list[str] = []
    for hr, picked in col_data:
        label = " ".join(x for x in (_cell(hr.get("source")), _cell(hr.get("report_year"))) if x)
        period = _cell(hr.get("data_period"))
        # A header is the report, its edition and its period; a long period, the band, "adjacent band" and the
        # statistic go in one line under the table so the header stays short.
        short = f"{label} ({period})" if period and len(label) + len(period) + 3 <= HEADER_MAX else label
        n = cite(hr)
        head.append(short + (f" [{n}]" if n else ""))
        details = ", ".join(x for x in (period if short == label else "", _band_text(hr), statistic_text(hr)) if x)
        col_notes.append(f"{label}: {details}")
        # A function only a reprint prints (a row from another report than the column's own) is named once.
        own = [f for f, r in picked.items() if r.get("report_slug") == hr.get("report_slug")]
        extra = [fnames[f] for f, r in picked.items() if r.get("report_slug") != hr.get("report_slug") and f not in own]
        if extra:
            other = next((r for r in picked.values() if r.get("report_slug") != hr.get("report_slug")), None)
            reprint_only.append(f"{', '.join(extra)} ({_cell((other or {}).get('source'))} {_cell((other or {}).get('report_year'))} prints "
                                f"{'it' if len(extra) == 1 else 'them'}; {label} does not)")
    if derived_idx is not None:
        head.append("Derived people")
    body = []
    for f in forder:
        row = [fnames[f]]
        for i, (hr, picked) in enumerate(col_data):
            r = picked.get(f)
            if r is None:
                row.append("")
                continue
            cell = value_text(r)
            if r.get("report_slug") != hr.get("report_slug"):
                n = cite(r)
                cell += f" [{n}]" if n else ""
            row.append(cell)
        if derived_idx is not None:
            r = col_data[derived_idx][1].get(f)
            if r is None:
                row.append("")
            else:
                cnt = int(r["value_num"] * derived_total["value_num"] / 100 + 0.5)
                allowed_extra.append(str(cnt))
                row.append(str(cnt))
        body.append(row)
    md = "**Headcount mix (percent of headcount)**\n\n" + _table(head, body)
    if derived_idx is not None:
        dn = cite(derived_total)
        md += (f"\n\nDerived people = share x total headcount ({value_text(derived_total)}, {_report_text(derived_total, dn)}), "
               "rounded; derived, not in the report.")
    out_notes = ["Column details: " + "; ".join(col_notes)]
    if reprint_only:
        out_notes.append("Only in a reprint: " + "; ".join(reprint_only))
    if older or extra_cols:
        names = []
        for k, rs in older + extra_cols:
            r = rs[0]
            names.append(f"{_cell(r.get('source'))} {_cell(r.get('report_year'))} ({_cell(r.get('data_period'))}, {_band_text(r)})".replace("  ", " "))
        out_notes.append("Not shown, another edition or band: " + "; ".join(names))
    if unnamed:
        out_notes.append("Not shown, the data does not name the function: " + "; ".join(sorted({_cell(r.get("metric")) for r in unnamed})[:6]))
    if out_notes:
        md += "\n\n" + ". ".join(out_notes) + "."
    return md, {"rows": len(forder), "capped": 0, "ranges": 0, "columns": len(col_data), "derived": derived_idx is not None,
                "allowed_extra": allowed_extra, "not_shown": len(older) + len(extra_cols), "unnamed": len(unnamed)}


_COGS = re.compile(r"cost of revenue|\bcogs\b|cost of goods|\bhosting\b|cost of (?:subscription|services)|gross (?:margin|profit)", re.I)


def is_margin_row(r: dict) -> bool:
    """Gross margin, cost of revenue and its parts are results, not spend lines: they go in the margins table."""
    kind = fg.row_table_kind(r)
    return kind == "margin" or (kind == "spend_share" and bool(_COGS.search(r.get("metric") or "")))


def rows_for(kind: str, rows: list[dict]) -> list[dict]:
    if kind == "margins":
        return [r for r in rows if is_margin_row(r)]
    if kind == "headcount":
        return count_rows(rows)
    if kind == "mix":
        return mix_share_rows(rows)
    return [r for r in rows if fg.row_table_kind(r) == "spend_share" and not is_margin_row(r)]


def kinds_present(rows: list[dict]) -> list[str]:
    return [k for k in KINDS if rows_for(k, rows)]


_BUILD = {}


def _builder(kind: str):
    return {"headcount": build_headcount, "mix": build_mix, "spend": build_spend, "margins": build_margins}[kind]


def not_in_any_table(rows: list[dict]) -> list[dict]:
    """Sent rows no code-built table took (a kind of figure none of the tables covers), for the admin row."""
    taken = {id(r) for k in KINDS for r in rows_for(k, rows)}
    return [{"figure_key": r.get("figure_key"), "report": r.get("report_slug"), "metric": _cell(r.get("metric")),
             "kind": fg.row_table_kind(r)} for r in rows if id(r) not in taken][:20]


def render_tables(text: str, rows: list[dict], cite) -> tuple[str, dict]:
    """Replace each table placeholder in `text` with its table; returns (text, info).
    Fallbacks (recorded in info["fallback"], never raised): a kind with rows and no placeholder is appended once
    at the end; a repeated placeholder is dropped after its first use; an unknown or row-less placeholder is
    removed. Nothing the model wrote outside a placeholder is touched."""
    info: dict = {"rendered": {}, "fallback": []}
    present = kinds_present(rows)
    done: set = set()

    def one(kind: str) -> str:
        # The mix builder needs the sent totals too (for the derived people column); it picks its own share rows.
        md, meta = _builder(kind)(rows if kind == "mix" else rows_for(kind, rows), lambda r, _c=cite: _c(r))
        info["rendered"][kind] = {k: v for k, v in meta.items() if k not in ("allowed_extra", "dropped")}
        if meta.get("dropped"):
            info.setdefault("cap_dropped", []).extend(dict(d, kind=kind) for d in meta["dropped"])
        bad = selfcheck(md, rows, extra=meta.get("allowed_extra"))
        if kind == "mix":
            bad += mix_selfcheck(md, rows)
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
    stray = not_in_any_table(rows)
    if stray:
        info["not_in_table"] = stray
    return out, info


_NUMERIC_CELL = re.compile(r"[-+]?\d[\d.,]*(?: ?%| [A-Za-z$]+)?")


def selfcheck(markdown: str, rows: list[dict], extra=None) -> list[str]:
    """Flag-only: every number-looking cell of a rendered table equals a sent row's own value (rounded the way the
    table rounds it) or a count the table derived from two sent rows."""
    values = {value_text(r) for r in rows} | {str(x) for x in (extra or [])}
    bad: list[str] = []
    for line in (markdown or "").split("\n"):
        if not line.startswith("|") or set(line.replace("|", "").strip()) <= set("-: "):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells and cells[0] in ("Line item", "Metric", "Function", "Report and period"):
            continue
        for c in cells[1:] if cells and cells[0] == "Function" else cells:
            c2 = re.sub(r"\s*\[\d+\]$", "", c)
            if _NUMERIC_CELL.fullmatch(c2) and c2 not in values:
                bad.append(f"value not in a sent row: {c2} ({' '.join(cells)[:60]})")
    return bad


def mix_selfcheck(markdown: str, rows: list[dict]) -> list[str]:
    """Flag-only: each function row's share cells equal a sent share row for that same function. Catches a label
    sitting on another function's number."""
    sent: dict[str, set] = {}
    for r in mix_share_rows(rows):
        sent.setdefault(fg.function_key(fg.share_function(r)), set()).add(value_text(r))
    bad: list[str] = []
    for line in (markdown or "").split("\n"):
        if not line.startswith("|") or set(line.replace("|", "").strip()) <= set("-: "):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells[0] == "Function":
            continue
        want = sent.get(fg.function_key(cells[0]))
        for c in cells[1:]:
            c2 = re.sub(r"\s*\[\d+\]$", "", c)
            if c2.endswith("%") and (want is None or c2 not in want):
                bad.append(f"function label does not match its share: {cells[0]} {c2}")
    return bad


_DERIVED_TAG = "(derived from stored reports)"


def cite_shifts(text: str, shifts: list[dict], cite) -> tuple[str, int]:
    """Add the [n] of each computed shift's two reports to the sentence that prints it. The model writes the
    shift lines in order, each ending "(derived from stored reports)"; a line already carrying a marker is left
    alone, and a report that was not sent gets none. Returns (text, lines changed)."""
    if not shifts or _DERIVED_TAG not in (text or ""):
        return text, 0
    lines = text.split("\n")
    changed, idx = 0, 0
    for i, ln in enumerate(lines):
        if _DERIVED_TAG not in ln or ln.lstrip().startswith("|") or idx >= len(shifts):
            continue
        sh = shifts[idx]
        idx += 1
        if re.search(r"\[\d{1,2}\]", ln):
            continue
        marks = []
        for end in ("from", "to"):
            e = sh.get(end) or {}
            row = {"report_slug": e.get("report_slug", ""), "source": e.get("source", ""), "report": e.get("report", ""),
                   "report_year": e.get("year", "")}
            n = cite(row) if row["report_slug"] else None
            if n and f"[{n}]" not in marks:
                marks.append(f"[{n}]")
        if marks:
            lines[i] = ln.replace(_DERIVED_TAG, " ".join(marks) + " " + _DERIVED_TAG, 1).replace("  ", " ")
            changed += 1
    return "\n".join(lines), changed


# --- the "What shifted" lines, built in code ---------------------------------------------------------------

SHIFTS_PLACEHOLDER = re.compile(r"\{\{\s*shifts\s*\}\}", re.I)
SHIFT_LINES = 2
_WHAT_SHIFTED = re.compile(r"^\s*(?:#{1,6}\s*|\*\*)?what shifted\b", re.I)
_SECTION_END = re.compile(r"^\s*(?:#{1,6}\s+\S|\*\*[^*]+\*\*\s*$|(?:\*\*|_)?left out\b)", re.I)
_DERIVED_SUFFIX = "; derived from stored reports"


def shift_line(sh: dict, cite) -> str:
    """One computed shift as a sentence: the exact computed text, the [n] of both endpoint reports, the derived tag."""
    base = str(sh.get("line", "")).strip()
    if base.endswith(_DERIVED_SUFFIX):
        base = base[: -len(_DERIVED_SUFFIX)]
    marks = []
    for end in ("from", "to"):
        e = sh.get(end) or {}
        row = {"report_slug": e.get("report_slug", ""), "source": e.get("source", ""), "report": e.get("report", ""),
               "report_year": e.get("year", "")}
        n = cite(row) if row["report_slug"] else None
        if n and f"[{n}]" not in marks:
            marks.append(f"[{n}]")
    base = base[:1].upper() + base[1:]
    return " ".join(x for x in (base, " ".join(marks), _DERIVED_TAG) if x) + "."


def render_shifts(text: str, shifts: list[dict], cite, max_lines: int = SHIFT_LINES) -> tuple[str, dict]:
    """Put the computed shift lines into the answer in code, so the model never retypes a computed number.
    The lines go at the {{shifts}} placeholder. Fallbacks, recorded in info["fallback"]: no placeholder and a
    "What shifted" section: the model's lines that carry a number are replaced by the computed ones (a sentence of cause
    with no number stays); no placeholder and no section: one section is appended before the closing "Left out"
    line; a placeholder with no computed shifts is removed. Returns (text, info)."""
    info: dict = {"fallback": [], "lines": 0}
    text = text or ""
    lines_md = [shift_line(sh, cite) for sh in (shifts or [])[:max_lines]]
    block = "\n\n".join(lines_md)
    if SHIFTS_PLACEHOLDER.search(text):
        first = True

        def one(_m):
            nonlocal first
            if first and block:
                first = False
                return "\n\n" + block + "\n\n"
            info["fallback"].append("repeated or empty {{shifts}} placeholder removed")
            return ""
        out = SHIFTS_PLACEHOLDER.sub(one, text)
        info["lines"] = len(lines_md)
        return re.sub(r"\n{3,}", "\n\n", out).strip(), info
    if not lines_md:
        return text, info
    src = text.split("\n")
    start = next((i for i, ln in enumerate(src) if _WHAT_SHIFTED.match(ln)), None)
    if start is None:
        info["fallback"].append("missing placeholder: What shifted section appended")
        at = next((i for i, ln in enumerate(src) if re.match(r"^\s*(?:\*\*|_)?left out\b", ln, re.I)), len(src))
        out = src[:at] + ["**What shifted**", "", block, ""] + src[at:]
        info["lines"] = len(lines_md)
        return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip(), info
    end = next((i for i in range(start + 1, len(src)) if _SECTION_END.match(src[i])), len(src))
    kept = [ln for ln in src[start + 1:end] if ln.strip() and not re.search(r"\d", ln)]
    replaced = sum(1 for ln in src[start + 1:end] if ln.strip() and re.search(r"\d", ln))
    info["fallback"].append(f"missing placeholder: {replaced} model-written line(s) with a number replaced by the computed lines")
    out = src[:start + 1] + ["", block, ""] + [x for ln in kept for x in (ln, "")] + src[end:]
    info["lines"] = len(lines_md)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip(), info
