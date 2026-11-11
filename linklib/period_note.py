"""The site's note on 2020 to 2022 data, appended to a Buddy answer by code.

The model never writes or sees it. It is added after the answer body when a printed table row or a computed
shift endpoint has a data period that overlaps 2020 to 2022. Nothing stored changes and nothing is excluded.
The edition year (`report_year`) is never used: a 2023 edition can hold 2022 data, and that is read from
`data_period` only.
"""
from __future__ import annotations

import re

NOTE_KEY = "buddy_period_note"
WINDOW = (2020, 2022)

# Stored copy wins once it exists (even empty, which means Off). The default is used only while nothing has been saved,
# and nothing writes it into settings, so a later edit to this text never reverts what an admin saved.
DEFAULT_NOTE = (
    "Figures covering 2020 to 2022 may reflect unusual conditions, with cheap capital and then a reset, so they might "
    "have been a temporary blip. Later data points to a reversion to the mean, so read these as context rather than a baseline."
)
LABEL = "Note:"

_YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
_SHORT_RANGE = re.compile(r"(?<!\d)((?:19|20)\d{2})\s*/\s*(\d{2})(?!\d)")          # 2021/22
_RANGE = re.compile(r"(?<!\d)((?:19|20)\d{2})\s*(?:-|–|to|through)\s*((?:19|20)\d{2})(?!\d)", re.I)


def period_years(period) -> tuple[int, int] | None:
    """(first year, last year) a data period covers, or None when it cannot be read.

    "Q2 2022", "FY2021", "2025 survey", "LTM to Jan-Feb 2026", "LTM, submitted July 2025", "2026E" and ISO dates
    are one point: the last year printed (a trailing-period or quarter reads at its end). "2021 to 2022",
    "2021-2022" and "2021/22" are a range. A "change ..." period is a delta between two periods, not data, and
    never triggers.
    """
    s = str(period or "").strip()
    if not s or s.lower().startswith("change"):
        return None
    m = _SHORT_RANGE.search(s)
    if m:
        lo = int(m.group(1))
        hi = int(m.group(1)[:2] + m.group(2))
        return (lo, max(lo, hi))
    if not s.lower().startswith("ltm"):
        m = _RANGE.search(s)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            return (min(a, b), max(a, b))
    years = [int(y) for y in _YEAR.findall(s)]
    if not years:
        return None
    return (years[-1], years[-1])


def in_window(period) -> bool:
    yrs = period_years(period)
    return bool(yrs) and yrs[0] <= WINDOW[1] and yrs[1] >= WINDOW[0]


def triggering_periods(table_periods, shifts) -> list[str]:
    """Distinct data-period strings (in order seen) of printed table rows and computed shift endpoints in the window."""
    seen: list[str] = []
    cands = list(table_periods or [])
    for sh in shifts or []:
        for end in ("from", "to"):
            cands.append((sh.get(end) or {}).get("period"))
    for p in cands:
        p = str(p or "").strip()
        if p and in_window(p) and p not in seen:
            seen.append(p)
    return seen


def note_text(lib) -> str:
    """The note to append: stored text if one was saved (empty = Off), else the default."""
    stored = lib.conn.execute("SELECT value FROM settings WHERE key=?", (NOTE_KEY,)).fetchone()
    return (stored[0] if stored else DEFAULT_NOTE).strip()


def apply_note(text: str, note: str, periods: list[str]) -> tuple[str, dict]:
    """(text with the note appended once, admin info). Nothing is appended when there is no note or no trigger."""
    info = {"shown": False, "periods": periods, "off": not note}
    line = f"{LABEL} {note}"
    if not note or not periods or (text or "").rstrip().endswith(line):
        return text, info
    out = (text or "").rstrip() + "\n\n" + line
    info["shown"] = True
    return out, info
