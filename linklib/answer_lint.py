"""Flag-only lint for an answer's claims (retest fixes, Part C3, 2026-10).

The answer rules (linklib.answer_rules) say: no cause unless a cited source states it,
no advice, no "practical read". They are instructions, so a breach is possible; this
finds the likely ones afterwards. It flags and never rewrites: the flags are stored in
ask_questions.lint_flags and shown on the admin report row.

A flag is a trigger phrase ("driven by", "reflects", "likely", "almost certainly",
"good threshold", "should") within about 12 words of a number, in a sentence that
carries no [n] citation marker. "practical read" is flagged wherever it appears (the
rules ban it outright). Known false positives are listed in tests/test_answer_lint.py.
"""
from __future__ import annotations

import re

WINDOW_WORDS = 12
TRIGGERS = ("driven by", "reflects", "reflecting", "likely", "almost certainly", "good threshold", "should")
ALWAYS = ("practical read",)
# An inference bolted on after a figure, flagged even in a sentence that carries an [n]: the
# citation covers the number, not the conclusion drawn from it. Flag only, never stripped.
INFERENCE = (
    ("meaning", re.compile(r"(?:—|,)\s*meaning\b", re.I)),
    ("which means", re.compile(r"\bwhich means\b", re.I)),
    ("attractive to investors", re.compile(r"\battractive to investors\b", re.I)),
    ("is the floor for", re.compile(r"\bis the floor for\b", re.I)),
    # A prediction about what stored medians cover or what a kind of company spends, after a figure.
    ("will understate", re.compile(r"\bwill (?:understate|overstate)\b", re.I)),
    ("typically spends", re.compile(r"\btypically spends?\b", re.I)),
    # Characterization the rules ban. Any sentence, cited or not; "material" also matches the noun
    # ("source material"), a known false positive.
    ("meaningful", re.compile(r"\bmeaningful\b", re.I)),
    ("material", re.compile(r"\bmaterial\b", re.I)),
    ("significant", re.compile(r"\bsignificant\b", re.I)),
    ("sharp", re.compile(r"\bsharp\b", re.I)),
    ("steep", re.compile(r"\bsteep\b", re.I)),
)
# Characterization that still gets past the strip. Flag only, any sentence, cited or not; never stripped.
# Known false positives: "stable" in "a stable value", "toward the" in a spatial or procedural sentence,
# "healthy" in a quoted metric name ("healthy ARR growth" a report prints), "attractive" in a report title.
CHARACTERIZATION = (
    ("high end of", re.compile(r"\bhigh end of\b", re.I)),
    ("low end of", re.compile(r"\blow end of\b", re.I)),
    ("relatively stable", re.compile(r"\brelatively stable\b", re.I)),
    ("stable", re.compile(r"\bstable\b(?<!relatively stable)", re.I)),
    ("structurally", re.compile(r"\bstructurally\b", re.I)),
    ("toward the", re.compile(r"\btoward the\b", re.I)),
    ("healthy", re.compile(r"\bhealthy\b", re.I)),
    ("attractive", re.compile(r"\battractive\b(?! to investors)", re.I)),
    ("rule of thumb", re.compile(r"\brule[- ]of[- ]thumb\b", re.I)),
    # An inference stated as agreement ("consistent with the headcount contraction"). Known false positive:
    # "consistent with the report's definition" and other plain statements of fit.
    ("consistent with", re.compile(r"\bconsistent with\b", re.I)),
    # Same inference with an adverb ("directionally consistent"). Same known false positive as above.
    ("directionally consistent", re.compile(r"\bdirectionally consistent\b", re.I)),
)
# A trend claim in a sentence with no figure: nothing shown supports it. Flag only. Stripping is not
# safe here (the same words open true sentences with a number in the next clause), so the admin row shows it.
TREND = (
    ("broad-based", re.compile(r"\bbroad-based\b", re.I)),
    ("across all", re.compile(r"\bacross all\b", re.I)),
    ("compression is", re.compile(r"\bcompression is\b", re.I)),
)
# A change worked out in the answer ("a drop of 61", "down 5 points", "12% higher") with no
# "derived, not in report" in the sentence. A change a report states, with a citation, is flagged
# too (known false positive): the lint cannot tell who computed it.
DERIVED_CHANGE = re.compile(
    r"\b(?:a\s+)?(?:drop|decline|fall|decrease|increase|rise|gain)\s+of\s+\$?\d"
    r"|\b(?:down|up)\s+\$?\d[\d.,]*\s*(?:%|points?|pts|pp|percentage points)?"
    r"|\b\d[\d.,]*%\s+(?:(?:higher|lower|more|less)\b|(?:above|below)\b(?!\s*(?:\$|\d|the\s+\$)))", re.I)
DERIVED_LABEL = "derived, not in report"
# A computed shift line is labelled this way instead (figures.compute_shifts).
DERIVED_LABELS = (DERIVED_LABEL, "derived from stored reports")

_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
_MARKER = re.compile(r"\[\d+\]")
_NUMBER = re.compile(r"\$?\d[\d,]*(?:\.\d+)?%?|\d+x")
_WORD = re.compile(r"\S+")


def _numbers_near(words: list[str], start: int, end: int) -> bool:
    lo, hi = max(0, start - WINDOW_WORDS), min(len(words), end + WINDOW_WORDS)
    return any(_NUMBER.search(w) for w in words[lo:start] + words[end:hi])


# "up 6 points" / "down 3.5 pp": a claimed change that must match a computed shift line.
_SHIFT_CLAIM = re.compile(r"\b(?:up|down|rose|fell|increased|decreased|gained|dropped)\s+(?:by\s+)?(\d+(?:\.\d+)?)\s*(?:percentage\s+points?|points?|pts|pp)\b", re.I)
# Two numbers in one table cell ("33% + 40%").
_TWO_VALUES_CELL = re.compile(r"\d[\d.,]*\s*%?\s*\+\s*\d[\d.,]*\s*%?|\d[\d.,]*\s*%\s*/\s*\d[\d.,]*\s*%?")

# A number that names a group or a period, not a figure: "top-10", "top 10", "Q2", "10-company".
_LABEL_NUMBERS = re.compile(r"\btop[- ]\d+\b|\bQ\d\b|\b\d+-company\b|\b[A-Za-z]+-\d+\b", re.I)

_ROW_MARKER_AFTER_PIPE = re.compile(r"^\s*\|.*\|\s*(?:\[\d{1,2}\]\s*)+$")


_WHAT_SHIFTED = re.compile(r"^\s*(?:#{1,6}\s*|\*\*)?what shifted\b", re.I)
_LEFT_OUT = re.compile(r"^\s*(?:\*\*|_)?left out\b", re.I)
_HEADING = re.compile(r"^\s*(?:#{1,6}\s+\S|\*\*[^*]+\*\*\s*$)")


_BAND_TEXT = re.compile(r"[<>]?\$\d[\d.,]*\s*[MKBmkb]?\s*(?:-|\u2013|to)\s*\$?\d[\d.,]*\s*[MKBmkb]?(?:\s*(?:ARR|revenue))?|\$\d[\d.,]*\s*[MKBmkb]\+?(?:\s*(?:ARR|revenue))?", re.I)
_SCOPE_SENTENCE = re.compile(r"\b(?:no|not|none|without|neither|nor|never|cannot|does not|do not|has no|have no|is no)\b", re.I)
_INFERENCE = re.compile(r"\b(?:may|might|could|likely|probably|would)\b[^.\n]{0,50}?\b(?:run|be|sit|come in|land)?\s*(?:somewhat |slightly |a bit |much )?(?:higher|lower|above|below)\b", re.I)


def inference_problems(text: str) -> list[dict]:
    """Flag-only. A sentence with no figure that infers where the user's own company would land against the medians
    ("a sales-heavy company may run higher on S&M than these medians show"). Table rows and sentences with a
    figure are not judged. Known false positive: a hedge about a stored figure ("may be higher in the 2023 report")
    with no number in the sentence."""
    out: list[dict] = []
    for line in (text or "").split("\n"):
        if line.strip().startswith("|"):
            continue
        for sent in _SENTENCE.split(line):
            if _NUMBER.search(re.sub(r"\[\d+\]", "", sent)):
                continue
            if _INFERENCE.search(sent) and re.search(r"\b(?:your|yours|a|an|the)\b[^.]{0,40}\b(?:company|business|team|org|motion|model|firm)\b|\byou\b", sent, re.I):
                out.append({"phrase": "inference about the user's own situation with no source", "snippet": sent.strip()[:200]})
    return out


def what_shifted_problems(text: str, shifts: list | None) -> list[dict]:
    """Flags for the "What shifted" section: it may hold only the computed lines. Any number in it that
    no computed line carries is flagged (recorded, never stripped); so is a section written when there
    were no computed lines at all. Known false positive: a computed figure the model rounds ("26.5" for 27)."""
    if shifts is None:
        return []
    allowed: set[str] = set()
    for x in shifts:
        allowed.update(n.strip("$,%").rstrip(".") for n in _NUMBER.findall(str(x.get("line", ""))))
        if "size" in x:
            allowed.add(f"{float(x['size']):g}")
    out: list[dict] = []
    inside = False
    for line in (text or "").split("\n"):
        if _WHAT_SHIFTED.match(line):
            inside = True
            if not shifts:
                out.append({"phrase": "what shifted without computed lines", "snippet": line.strip()[:200]})
            continue
        if inside and (_HEADING.match(line) or _LEFT_OUT.match(line)):
            inside = False
        if not inside or not line.strip() or not shifts:
            continue
        scope = _YEAR.sub(" ", _BAND_TEXT.sub(" ", _LABEL_NUMBERS.sub(" ", re.sub(r"\[\d+\]", "", line))))
        if _SCOPE_SENTENCE.search(scope) and not re.search(r"%|\bpoints?\b|\bpts\b", scope):
            continue        # "The archive has no mix data before 2022 and none split to $20-$30M ARR": a scope caveat, not a figure
        for n in _NUMBER.findall(scope):
            if n.strip("$,%").rstrip(".") not in allowed:
                out.append({"phrase": "number in what shifted with no computed line", "snippet": line.strip()[:200]})
                break
    return out


_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
# "down from 200 in Q2 2024", "fell from 78% to 73%", "increased from 20 to 30": a change the model worked out.
_CHANGE_FROM = re.compile(
    r"\b(?:down|up|fell|fall(?:s|en)?|rose|risen?|increased?|decreased?|declined?|dropped?|grew|grown|shr[ua]nk|gained|climbed|slipped|"
    r"moved|changed|shifted)\b[^.\n|]{0,60}?\bfrom\b"
    r"|\bfrom\s+\$?\d[\d.,]*\s*%?[^.\n|]{0,40}?\bto\s+\$?\d", re.I)


def change_claim_problems(text: str, shifts: list | None) -> list[dict]:
    """Flag-only, whole answer. A sentence that states a change over time between two figures ("down from
    200 in Q2 2024", "fell from 78% to 73%") when no computed shift line carries those two figures: the model
    never works a change out itself, only a computed line (derived from stored reports) is a change. Skipped:
    table rows, the "What shifted" section (its own check), a sentence that carries a derived label, and a
    sentence whose figures are both the ends of a computed shift. `shifts` None = not known, no check."""
    if shifts is None:
        return []
    pairs = [(str((x.get("from") or {}).get("value", "")).strip().rstrip("%").replace(",", ""),
              str((x.get("to") or {}).get("value", "")).strip().rstrip("%").replace(",", "")) for x in shifts]
    out: list[dict] = []
    inside = False
    for line in (text or "").split("\n"):
        if _WHAT_SHIFTED.match(line):
            inside = True
            continue
        if inside and (_HEADING.match(line) or _LEFT_OUT.match(line)):
            inside = False
        if inside or line.strip().startswith("|"):
            continue
        for sent in _SENTENCE.split(line):
            low = sent.lower()
            if not _CHANGE_FROM.search(sent) or any(lab in low for lab in DERIVED_LABELS):
                continue
            clean = _YEAR.sub(" ", _LABEL_NUMBERS.sub(" ", re.sub(r"\[\d+\]", "", sent)))
            nums = [n.strip("$,%").rstrip(".").replace(",", "") for n in _NUMBER.findall(clean)]
            if len(nums) < 2:
                continue
            if any(a in nums and b in nums for a, b in pairs):
                continue
            out.append({"phrase": "change written by the model with no computed line", "snippet": sent.strip()[:200]})
    return out


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


_LEFT_OUT_CELL = re.compile(r"^left out\b", re.I)


def left_out_cell_problems(text: str) -> list[dict]:
    """A table row with a cell that says "left out": the answer rules keep such a figure out of the table (the
    closing line names it). Flag only."""
    return [{"phrase": "left out in a table cell", "snippet": line.strip()[:200]}
            for line in (text or "").split("\n")
            if line.strip().startswith("|") and any(_LEFT_OUT_CELL.match(c) for c in _cells(line))]


def table_order_problems(text: str) -> list[dict]:
    """Flag-only. Inside one table, consecutive rows that carry a readable size band (and name the same line
    item, or have the band as their first cell) must run smallest band to largest. Rows with no band are not
    judged. Order is code's job where code writes the table; this catches the model's tables."""
    from linklib import figures as fg
    out: list[dict] = []
    prev = None
    for line in (text or "").split("\n") + [""]:
        if not line.strip().startswith("|"):
            prev = None
            continue
        cells = _cells(line)
        if not cells or set("".join(cells)) <= set("-: "):
            continue
        # A band is a money range ("$20-50M ARR", "<$100M ARR"); a bare number or a percent in another column
        # ("131", "26.9%") parses as one but is a value, and ordering rows by it is the false positive id 53 showed.
        band = next((c for c in cells if "$" in c and fg.parse_band(c) is not None), None)
        if band is None:
            continue
        key = fg.band_sort_key(band)
        label = cells[0].lower() if fg.parse_band(cells[0]) is None else ""
        if prev is not None and (label == prev[1]) and key < prev[0]:
            out.append({"phrase": "table rows out of size order", "snippet": line.strip()[:200]})
        prev = (key, label)
    return out


_ROW_NUM = re.compile(r"(?<![\w.])\$?\d[\d,]*(?:\.\d+)?%?")


def label_problems(text: str, rows: list[dict] | None) -> list[dict]:
    """Flag-only label check on table rows. A row that names a sent report's publisher and year and
    shows a figure that report does not print for that metric, but another sent edition of the same
    publisher does, has the wrong edition label ("High Alpha 2025 | 200" when 200 is the 2024 figure).
    Nothing is changed; the admin row shows it. Rows with no sent figure to compare are not judged."""
    if not rows:
        return []
    by_pub: dict[str, dict[str, set[str]]] = {}
    for r in rows:
        pub = str(r.get("source") or "").strip().lower()
        yr = str(r.get("report_year") or "").strip()
        if not pub or not yr:
            continue
        val = str(r.get("value") or "").strip().rstrip("%")
        by_pub.setdefault(pub, {}).setdefault(yr, set()).add(val)
        # A cell the same report is printed in elsewhere is that report's figure too, under either name:
        # "High Alpha 2025, also printed in Jurassic Capital 2026" is a right label for the one cell.
        for other in r.get("also_printed_in") or []:
            m = re.search(r"(?<!\d)((?:19|20)\d{2})(?!\d)", str(other))
            name = str(other).split(",")[0].strip().lower()
            if name and m:
                by_pub.setdefault(name, {}).setdefault(m.group(1), set()).add(val)
        # A republisher's row that cites its original counts for the original's edition it names.
        cited = str(r.get("cited_source") or "")
        cm = re.search(r"(?<!\d)((?:19|20)\d{2})(?!\d)", cited)
        series = str(r.get("series") or "").strip().lower()
        if cited and cm and series and series != pub:
            by_pub.setdefault(series, {}).setdefault(cm.group(1), set()).add(val)
    out: list[dict] = []
    for line in (text or "").split("\n"):
        if not line.strip().startswith("|"):
            continue
        low = line.lower()
        nums = {n.strip("$,%").replace(",", "") for n in _ROW_NUM.findall(re.sub(r"\[\d+\]", "", line))}
        for pub, years in by_pub.items():
            if pub not in low:
                continue
            named = [y for y in years if re.search(rf"(?<!\d){re.escape(y)}(?!\d)", line)]
            if not named:
                continue
            for y in named:
                other = set().union(*(v for yy, v in years.items() if yy != y)) if len(years) > 1 else set()
                mine = years[y]
                shown = nums & (mine | other)
                if shown and not (shown & mine) and (shown & other):
                    out.append({"phrase": "table label does not match the figure",
                                "snippet": line.strip()[:200]})
                    break
    return out


_PCT_CELL = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*%")


def function_share_problems(text: str, rows: list[dict] | None) -> list[dict]:
    """Flag-only label check on a headcount mix table row: the first cell names a function and a percent in the row
    is a share the sent figures print for a DIFFERENT function and for no row of this one ("CS and support | 20%" when 20%
    is Sales and CS and support is 13%). Compares only against the sent rows; a share that no sent row prints is not
    judged. Nothing is changed."""
    if not rows:
        return []
    from . import figures as fg
    shares: dict[str, set[float]] = {}
    for r in rows:
        if fg.row_table_kind(r) != "headcount_share" or r.get("value_num") is None:
            continue
        fn = fg.function_key(fg.share_function(r))
        if fn:
            shares.setdefault(fn, set()).add(round(float(r["value_num"]), 1))
    if len(shares) < 2:
        return []
    out: list[dict] = []
    for line in (text or "").split("\n"):
        if not line.strip().startswith("|"):
            continue
        cells = _cells(line)
        if not cells:
            continue
        fn = fg.function_key(re.sub(r"\[\d+\]|\*", "", cells[0]))
        if fn not in shares:
            continue
        for m in _PCT_CELL.finditer(re.sub(r"\[\d+\]", "", " ".join(cells[1:]))):
            v = round(float(m.group(1)), 1)
            if v in shares[fn]:
                continue
            others = [g for g, vals in shares.items() if g != fn and v in vals]
            if others:
                out.append({"phrase": "function label does not match its share",
                            "snippet": f"{cells[0]} shows {m.group(1)}%, which the sent figures print for {others[0]}, not {fn}: {line.strip()[:160]}"})
                break
    return out


_PCT_NEAR = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_FWORD = re.compile(r"[A-Za-z&][A-Za-z&/]*")
# A sentence about spend, expense or a percent of revenue or ARR is not a headcount mix claim ("sales at 38% of revenue").
_SPEND_BASIS = re.compile(r"\bof\s+(?:total\s+)?(?:revenue|arr)\b|\bspend\b|\bexpenses?\b|\bopex\b|\bcost of revenue\b", re.I)
_CANNOT_SHOW = re.compile(r"(?:mix )?table (?:cannot|can't|can not|does not|doesn't) show\b[^.\n]*", re.I)


def mix_prose_problems(text: str, rows: list[dict] | None, mix_rendered: bool = True) -> list[dict]:
    """Flag-only. When the system builds the headcount mix table, a sentence outside any table that pairs a function with
    a percent the sent figures do not print for THAT function ("20% sales" when Sales is 13 and 19 and CS is 20), and a
    sentence saying the table cannot show something it does (a Derived people column when a total is sent). Nothing
    is changed. A pair the sent rows print for the function is not flagged: repeating a row is another rule's job."""
    if not rows or not mix_rendered:
        return []
    from . import figures as fg
    shares: dict[str, set[float]] = {}
    for r in rows:
        if fg.row_table_kind(r) != "headcount_share" or r.get("value_num") is None or not fg.mix_function_ok(r):
            continue
        fk = fg.function_key(fg.share_function(r))
        if fk:
            shares.setdefault(fk, set()).add(round(float(r["value_num"]), 1))
    out: list[dict] = []
    for line in (text or "").split("\n"):
        if line.strip().startswith("|") or not line.strip():
            continue
        plain = re.sub(r"\[\d+\]|\*", "", line)
        spend_line = bool(_SPEND_BASIS.search(plain))
        for m in ([] if spend_line else _PCT_NEAR.finditer(plain)):
            v = round(float(m.group(1)), 1)
            after = _FWORD.findall(plain[m.end():m.end() + 60])[:4]
            before = _FWORD.findall(plain[max(m.start() - 60, 0):m.start()])[-4:]
            hit = None
            cands = [after[:k] for k in (3, 2, 1)] + [before[-k:] for k in (3, 2, 1)]
            for words in cands:
                fk = fg.function_key(" ".join(words)) if words else ""
                if fk in shares:
                    hit = (fk, " ".join(words))
                    break
            if hit and v not in shares[hit[0]]:
                out.append({"phrase": "function share in prose does not match the table",
                            "snippet": f"{m.group(1)}% next to {hit[1]}; the sent figures print {sorted(shares[hit[0]])} for it: {line.strip()[:160]}"})
                break
        if any(fg.is_total_fte_row(r) for r in rows) and _CANNOT_SHOW.search(plain) \
                and re.search(r"head ?count|people|count", plain, re.I):
            out.append({"phrase": "says the table cannot show what it shows", "snippet": line.strip()[:200]})
    return out


def lint_answer(text: str, shifts: list | None = None, rows: list | None = None) -> list[dict]:
    """[{phrase, snippet}] for each likely breach; [] for a clean answer. `shifts` (the computed shift lines
    the turn was given; [] when none) turns on the check that a "up N points" claim matches one of them."""
    flags: list[dict] = (list(what_shifted_problems(text, shifts)) + change_claim_problems(text, shifts) + label_problems(text, rows)
                         + left_out_cell_problems(text) + table_order_problems(text) + function_share_problems(text, rows) + mix_prose_problems(text, rows) + inference_problems(text))
    # A citation marker after a table row's closing pipe breaks the row in a renderer that does not repair it.
    for line in (text or "").split("\n"):
        if _ROW_MARKER_AFTER_PIPE.match(line):
            flags.append({"phrase": "marker after table row", "snippet": line.strip()[:200]})
    for line in (text or "").split("\n"):
        if line.strip().startswith("|") and _TWO_VALUES_CELL.search(line):
            flags.append({"phrase": "two values in one cell", "snippet": line.strip()[:200]})
    for sent in _SENTENCE.split(text or ""):
        sent = sent.strip()
        if not sent:
            continue
        if shifts is not None:
            for m in _SHIFT_CLAIM.finditer(sent):
                claimed = float(m.group(1))
                if not any(abs(claimed - float(x.get("size", -1))) < 0.1 for x in shifts):
                    flags.append({"phrase": "shift claim without a computed line", "snippet": sent[:200]})
                    break
        words = _WORD.findall(sent)
        low_words = [w.lower().strip(".,;:()\"'") for w in words]
        joined = " ".join(low_words)
        for phrase in ALWAYS:
            if phrase in joined:
                flags.append({"phrase": phrase, "snippet": sent[:200]})
        for phrase, rx in INFERENCE:
            if rx.search(sent):
                flags.append({"phrase": phrase, "snippet": sent[:200]})
        for phrase, rx in CHARACTERIZATION:
            if rx.search(sent) and not (phrase == "stable" and re.search(r"relatively stable", sent, re.I)):
                flags.append({"phrase": phrase, "snippet": sent[:200]})
        if not _NUMBER.search(sent):
            for phrase, rx in TREND:
                if rx.search(sent):
                    flags.append({"phrase": phrase, "snippet": sent[:200]})
        if DERIVED_CHANGE.search(sent) and not any(lab in sent.lower() for lab in DERIVED_LABELS) and not sent.lstrip().startswith("|"):
            flags.append({"phrase": "derived change without label", "snippet": sent[:200]})
        if _MARKER.search(sent):
            continue
        for phrase in TRIGGERS:
            n = len(phrase.split())
            for i in range(len(low_words) - n + 1):
                if low_words[i:i + n] == phrase.split() and _numbers_near(words, i, i + n):
                    flags.append({"phrase": phrase, "snippet": sent[:200]})
                    break
    return flags
