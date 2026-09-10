"""CSV parsing for the /admin/reader/backfill-content article-purge
export/import round trip (durability follow-up, 2026-08).

Mirrors linklib/manual_review_csv.py's shape and discipline exactly, same
reasoning: kept separate from linklib.db so parsing/normalization can be
unit-tested without a database, one bad row never aborts the rest of the
file, and a missing/misnamed required header column fails the whole file
up front.

Required columns: article_id (the stable match key) and confirm_purge (the
only column this import path reads to decide anything — the rest of the
exported columns are round-tripped for the admin's own reference, same
"whatever's in the database now is authoritative" rule as the manual-review
CSV: `current_candidates` is built fresh from
Library.articles_eligible_for_purge() by the caller, never read back off
the CSV itself).

Three-way split, same convention as manual_review_csv:
  - confirmed: confirm_purge is a recognized "yes" marker
    (yes/y/1/x/purge/confirm, case-insensitive) AND article_id is still a
    genuine purge candidate right now.
  - skipped: confirm_purge blank, or a recognized "no" marker
    (no/n/0) — a normal no-op, not an error (most exported rows are left
    blank while the admin is still deciding).
  - errors: confirm_purge present but not a recognized marker (typo
    protection — an unrecognized non-blank value is treated as a mistake
    worth surfacing, not silently skipped), OR article_id no longer a
    purge candidate (already fixed by a backfill since the CSV was
    exported, or never was a candidate).

A hard MAX_PURGE_PER_RUN cap fails the WHOLE import (never a silent
truncation — see CLAUDE.md's "no silent caps" standard) if confirmed
exceeds it, so a fat-fingered "confirm everything" pass can't wipe a large
chunk of the library in one commit. The caller (webapp/app.py) adds a
SECOND, independent guard on top: the confirm screen requires the admin to
type the exact confirmed count before the delete actually runs.
"""
from __future__ import annotations

import csv
import io

REQUIRED_COLUMNS = ("article_id", "confirm_purge")

# Same file-size sanity ceiling as manual_review_csv.MAX_ROWS — this list
# realistically tops out in the low hundreds even for a large stale corpus.
MAX_ROWS = 2000

# Per-run cap on how many articles one commit can actually delete —
# independent of MAX_ROWS (a huge CSV with only a few "yes" rows is fine;
# a small CSV where every row is marked "yes" past this count is not).
# Brian's own number, flagged rather than silently picked, same convention
# as Library._MANUAL_REVIEW_ATTEMPT_THRESHOLD's docstring: high enough that
# a genuine cleanup pass isn't hobbled, low enough that "confirm_purge=yes"
# accidentally filled down an entire column can't take out the library in
# one commit.
MAX_PURGE_PER_RUN = 50

_YES_MARKERS = {"yes", "y", "1", "x", "purge", "confirm"}
_NO_MARKERS = {"no", "n", "0", ""}


def parse_purge_confirmations_csv(data: bytes, current_candidates: dict[int, dict]
                                   ) -> tuple[list[dict], list[dict], list[dict]]:
    """Parses an uploaded purge-confirmation CSV into (confirmed, skipped, errors).

    `current_candidates` is {article_id: {"title", "url", "word_count"}} for
    every article CURRENTLY eligible for purge — the caller builds this
    fresh from Library.articles_eligible_for_purge() right before parsing,
    never from the uploaded file's own columns.

    confirmed: list of {"article_id", "title", "url", "word_count"}, ready
    to show on the preview screen and hand to Library.purge_article.
    skipped: list of {"line", "article_id", "reason"}.
    errors: list of {"line", "raw", "reason"}.

    Raises ValueError if the file has no header row, is missing a required
    column, or the confirmed count exceeds MAX_PURGE_PER_RUN — all fatal
    for the whole upload, same convention as manual_review_csv/overhead_csv.
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
            f"Expected a header row including at least: article_id, confirm_purge."
        )

    confirmed: list[dict] = []
    skipped: list[dict] = []
    errors: list[dict] = []

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
        article_id_raw = cell("article_id")
        confirm_raw = cell("confirm_purge")
        confirm_norm = confirm_raw.strip().lower()

        try:
            article_id = int(article_id_raw)
        except ValueError:
            errors.append({"line": line_num, "raw": raw_display,
                            "reason": f"article_id '{article_id_raw}' isn't a number."})
            continue

        if confirm_norm in _NO_MARKERS:
            skipped.append({"line": line_num, "article_id": article_id,
                             "reason": "confirm_purge left blank or marked no."})
            continue
        if confirm_norm not in _YES_MARKERS:
            errors.append({"line": line_num, "raw": raw_display,
                            "reason": f"confirm_purge '{confirm_raw}' isn't recognized "
                                      f"(use yes/y/1/x to confirm, or leave blank to skip)."})
            continue

        candidate = current_candidates.get(article_id)
        if candidate is None:
            errors.append({"line": line_num, "raw": raw_display,
                            "reason": f"article_id {article_id} is no longer a purge candidate "
                                      f"(already backfilled with real content, or never was one)."})
            continue

        confirmed.append({"article_id": article_id, "title": candidate.get("title") or "",
                          "url": candidate.get("url") or "",
                          "word_count": candidate.get("word_count", 0)})

    if len(confirmed) > MAX_PURGE_PER_RUN:
        raise ValueError(
            f"This file confirms {len(confirmed)} article(s) for purge, over the "
            f"{MAX_PURGE_PER_RUN}-per-run cap. Split the confirmations into smaller "
            f"batches and import them one at a time."
        )

    return confirmed, skipped, errors
