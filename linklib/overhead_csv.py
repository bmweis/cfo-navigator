"""CSV parsing for the /admin/overhead-spend "Upload CSV" import path.

Kept separate from linklib.db so the parsing/normalization logic (header
matching, date-format coercion, amount cleanup) can be unit-tested without
touching a database or the webapp. The route layer (webapp/app.py) is
responsible for actually inserting rows via Library.add_manual_overhead,
using the same validation that already backs the manual "Add a charge" form
— this module only decides which rows are well-formed enough to try.

Required columns: vendor, date, amount. Optional: category, note — same
fields as the manual-entry form, same requiredness. A row that fails any
check is skipped and reported with a reason; one bad row never aborts the
rest of the batch. A missing/misnamed required column in the header, though,
fails the whole file up front (silently guessing at column order is worse
than asking the user to fix the header).
"""
from __future__ import annotations

import csv
import io
from datetime import datetime

REQUIRED_COLUMNS = ("vendor", "date", "amount")
OPTIONAL_COLUMNS = ("category", "note")

# Mirrors the maxlength attributes on the manual-entry form fields
# (webapp/app.py's /admin/overhead-spend "Add a charge" inputs) so a CSV row
# can't silently store something the manual form would never have allowed.
_MAX_VENDOR = 120
_MAX_CATEGORY = 60
_MAX_NOTE = 300

# Hard ceiling on rows parsed from one file — a stray gigantic paste
# shouldn't hang the request. Comfortably above any realistic receipt batch.
MAX_ROWS = 2000

_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y")


def _normalize_date(raw: str) -> str | None:
    """Returns YYYY-MM-DD, or None if raw matches neither accepted format."""
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _normalize_amount(raw: str) -> float | None:
    """Strips a leading $ and thousands separators, then parses as float."""
    cleaned = raw.strip().replace("$", "").replace(",", "")
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_overhead_csv(data: bytes) -> tuple[list[dict], list[dict]]:
    """Parses an uploaded CSV into (valid_rows, skipped_rows).

    valid_rows: list of {"vendor", "date", "amount", "category", "note"},
    ready to hand to Library.add_manual_overhead.

    skipped_rows: list of {"line", "raw", "reason"} — 1-indexed against the
    file including the header line, so "line" matches what a user sees if
    they open the CSV in a spreadsheet.

    Raises ValueError if the file has no header row, or the header is
    missing a required column — those are fatal for the whole upload rather
    than a per-row skip, since there's no safe way to guess which column is
    which.
    """
    text = data.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        raise ValueError("The file is empty.")

    header_map = {h.strip().lower(): i for i, h in enumerate(header) if h.strip()}
    missing = [c for c in REQUIRED_COLUMNS if c not in header_map]
    if missing:
        raise ValueError(
            f"Missing required column(s): {', '.join(missing)}. "
            f"Expected a header row with: vendor, date, amount, category (optional), note (optional)."
        )

    valid_rows: list[dict] = []
    skipped_rows: list[dict] = []

    for line_num, raw_row in enumerate(reader, start=2):  # start=2: line 1 was the header
        if not any(cell.strip() for cell in raw_row):
            continue  # blank line, not worth reporting as a failure
        if len(valid_rows) + len(skipped_rows) >= MAX_ROWS:
            skipped_rows.append({
                "line": line_num,
                "raw": ",".join(raw_row),
                "reason": f"File exceeds the {MAX_ROWS}-row import limit; row not read.",
            })
            continue

        def cell(col: str) -> str:
            idx = header_map.get(col)
            if idx is None or idx >= len(raw_row):
                return ""
            return raw_row[idx].strip()

        raw_display = ",".join(raw_row)
        vendor = cell("vendor")
        date_raw = cell("date")
        amount_raw = cell("amount")
        category = cell("category")
        note = cell("note")

        if not vendor:
            skipped_rows.append({"line": line_num, "raw": raw_display, "reason": "Vendor is required."})
            continue
        if len(vendor) > _MAX_VENDOR:
            skipped_rows.append({"line": line_num, "raw": raw_display,
                                  "reason": f"Vendor exceeds {_MAX_VENDOR} characters."})
            continue
        if not date_raw:
            skipped_rows.append({"line": line_num, "raw": raw_display, "reason": "Date is required."})
            continue
        date = _normalize_date(date_raw)
        if date is None:
            skipped_rows.append({"line": line_num, "raw": raw_display,
                                  "reason": f"Date '{date_raw}' isn't in YYYY-MM-DD or MM/DD/YYYY format."})
            continue
        if not amount_raw:
            skipped_rows.append({"line": line_num, "raw": raw_display, "reason": "Amount is required."})
            continue
        amount = _normalize_amount(amount_raw)
        if amount is None:
            skipped_rows.append({"line": line_num, "raw": raw_display,
                                  "reason": f"Amount '{amount_raw}' isn't a number."})
            continue
        if len(category) > _MAX_CATEGORY:
            skipped_rows.append({"line": line_num, "raw": raw_display,
                                  "reason": f"Category exceeds {_MAX_CATEGORY} characters."})
            continue
        if len(note) > _MAX_NOTE:
            skipped_rows.append({"line": line_num, "raw": raw_display,
                                  "reason": f"Note exceeds {_MAX_NOTE} characters."})
            continue

        valid_rows.append({
            "vendor": vendor,
            "date": date,
            "amount": amount,
            "category": category,
            "note": note,
        })

    return valid_rows, skipped_rows
