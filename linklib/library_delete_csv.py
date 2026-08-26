"""CSV parsing for the /admin/library/bulk-delete export/preview/confirm
round trip (one-off cleanup batches — Brian deciding a specific list of
articles isn't needed, distinct from the Purge tool's narrow "essentially
nothing was ever saved" scope in linklib/purge_csv.py).

Mirrors linklib/purge_csv.py's shape and discipline closely: kept separate
from linklib.db so parsing/normalization can be unit-tested without a
database, one bad row never aborts the rest of the file, and a missing/
misnamed required header column fails the whole file up front.

One real difference from purge_csv/manual_review_csv: those two both check
a row's article_id against a FIXED, precomputed candidate set (purge
candidates, or the needs-manual-review list) — membership in that set is
itself part of what makes a row valid. Bulk delete has no such set: any URL
that currently resolves to a real article is a valid deletion target, so
there's nothing to precompute a dict of ahead of time. Resolution is a
live per-row callback (`resolve_url`) instead — Library.get_article_by_url
in production, a fake dict-backed function in tests — so this module still
never touches a database directly.

Required columns: url (the match key — a URL, not an article_id, since
that's what a one-off cleanup list like Brian's naturally comes as) and
confirm_delete (the only column this import path reads to decide anything;
title/current_id are round-tripped on export for the admin's own reference
but re-resolved from the live database on import, same "whatever's in the
database now is authoritative" rule as every other CSV round trip here).

Three-way split, same convention as purge_csv:
  - confirmed: confirm_delete is a recognized "yes" marker
    (yes/y/1/x/delete/confirm, case-insensitive) AND the url currently
    resolves to a real article.
  - skipped: confirm_delete blank, or a recognized "no" marker (no/n/0) —
    a normal no-op, not an error.
  - errors: confirm_delete present but not a recognized marker, OR the url
    doesn't resolve to any article right now (never saved, already
    deleted, or a typo), OR the same url appears more than once in the
    file (only the first occurrence is honored — a duplicate is flagged
    rather than silently deleted twice, which would just be a no-op the
    second time but is worth surfacing as a likely copy/paste mistake).

A hard MAX_DELETE_PER_RUN cap fails the WHOLE import (never a silent
truncation — see CLAUDE.md's "no silent caps" standard) if confirmed
exceeds it. The caller (webapp/app.py) adds a SECOND, independent guard on
top: the confirm screen requires the admin to type the exact confirmed
count before the delete actually runs — same two-guard discipline as the
Purge flow.
"""
from __future__ import annotations

import csv
import io
from typing import Callable, Optional

REQUIRED_COLUMNS = ("url", "confirm_delete")

# Same file-size sanity ceiling as purge_csv.MAX_ROWS/manual_review_csv.MAX_ROWS.
MAX_ROWS = 2000

# Per-run cap on how many articles one commit can actually delete — same
# number and reasoning as purge_csv.MAX_PURGE_PER_RUN: high enough that a
# genuine one-off cleanup batch isn't hobbled, low enough that a
# "confirm_delete=yes" accidentally filled down an entire column can't
# take out a large chunk of the library in one commit.
MAX_DELETE_PER_RUN = 50

_YES_MARKERS = {"yes", "y", "1", "x", "delete", "confirm"}
_NO_MARKERS = {"no", "n", "0", ""}


def parse_library_delete_csv(
    data: bytes, resolve_url: Callable[[str], Optional[dict]]
) -> tuple[list[dict], list[dict], list[dict]]:
    """Parses an uploaded deletion-confirmation CSV into
    (confirmed, skipped, errors).

    `resolve_url(url)` returns {"id", "title", "word_count"} for a url
    that currently matches a real article, or None otherwise.

    confirmed: list of {"article_id", "title", "url", "word_count"}, ready
    to show on the preview screen and hand to Library.purge_article (the
    same write-then-read-back delete method the Purge tool uses — this
    tool targets a different candidate set, not a different delete
    mechanism).
    skipped: list of {"line", "url", "reason"}.
    errors: list of {"line", "raw", "reason"}.

    Raises ValueError if the file has no header row, is missing a required
    column, or the confirmed count exceeds MAX_DELETE_PER_RUN — all fatal
    for the whole upload, same convention as purge_csv.
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
            f"Expected a header row including at least: url, confirm_delete."
        )

    confirmed: list[dict] = []
    skipped: list[dict] = []
    errors: list[dict] = []
    seen_urls: set[str] = set()

    for line_num, raw_row in enumerate(reader, start=2):  # start=2: line 1 was the header
        if not any(cell.strip() for cell in raw_row):
            continue  # blank line, not worth reporting
        if len(confirmed) + len(skipped) + len(errors) >= MAX_ROWS:
            errors.append({"line": line_num, "raw": ",".join(raw_row),
                            "reason": f"File exceeds the {MAX_ROWS}-row import limit; row not read."})
            continue

        def cell(col: str) -> str:
            idx = header_map.get(col)
            if idx is None or idx >= len(raw_row):
                return ""
            return raw_row[idx].strip()

        raw_display = ",".join(raw_row)
        url = cell("url")
        confirm_raw = cell("confirm_delete")
        confirm_norm = confirm_raw.strip().lower()

        if not url:
            errors.append({"line": line_num, "raw": raw_display, "reason": "url is blank."})
            continue

        if confirm_norm in _NO_MARKERS:
            skipped.append({"line": line_num, "url": url,
                             "reason": "confirm_delete left blank or marked no."})
            continue
        if confirm_norm not in _YES_MARKERS:
            errors.append({"line": line_num, "raw": raw_display,
                            "reason": f"confirm_delete '{confirm_raw}' isn't recognized "
                                      f"(use yes/y/1/x to confirm, or leave blank to skip)."})
            continue

        if url in seen_urls:
            errors.append({"line": line_num, "raw": raw_display,
                            "reason": "url appears more than once in this file; only the first "
                                      "occurrence is used."})
            continue

        match = resolve_url(url)
        if match is None:
            errors.append({"line": line_num, "raw": raw_display,
                            "reason": f"url '{url}' doesn't match any article currently in the "
                                      f"library (never saved, already deleted, or a typo)."})
            continue

        seen_urls.add(url)
        confirmed.append({"article_id": match["id"], "title": match.get("title") or "",
                           "url": url, "word_count": match.get("word_count", 0)})

    if len(confirmed) > MAX_DELETE_PER_RUN:
        raise ValueError(
            f"This file confirms {len(confirmed)} article(s) for deletion, over the "
            f"{MAX_DELETE_PER_RUN}-per-run cap. Split the confirmations into smaller "
            f"batches and import them one at a time."
        )

    return confirmed, skipped, errors
