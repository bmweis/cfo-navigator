"""CSV parsing for the /admin/reader/backfill-content manual-review
export/import round trip (Phase 5b follow-up #2).

Mirrors linklib/overhead_csv.py's shape and discipline: kept separate from
linklib.db so the parsing/normalization logic can be unit-tested without a
database, one bad row never aborts the rest of the file, and a
missing/misnamed required header column fails the whole file up front
(silently guessing at column order is worse than asking for a fixed header).

Required column: article_id (the stable match key — the URL itself is what's
changing, so it can't be the key). corrected_url is the only column this
import path actually reads to decide anything; the rest of the exported
columns (title, current_url, reason, attempt_count, last_attempted_at) are
round-tripped for the admin's own reference but not re-validated on import —
whatever's in the database now is authoritative, not what an admin's local
CSV copy happened to say.

Three-way split, per the Phase 5b follow-up #2 spec (deliberately NOT the
overhead-CSV's two-way valid/skipped split):
  - updates: corrected_url present, different from the article's current
    URL, and a well-formed http(s) URL. These are staged, not yet applied.
  - skipped: corrected_url blank, or identical to the current URL — a
    normal no-op, not an error (most exported rows will be left blank by
    the admin while they're still working the list).
  - errors: corrected_url present but malformed, OR article_id doesn't match
    any known needs-manual-review article. Never applied.
"""
from __future__ import annotations

import csv
import io
from urllib.parse import urlsplit

REQUIRED_COLUMNS = ("article_id", "corrected_url")

# Hard ceiling on rows parsed from one file, same reasoning/value as
# overhead_csv.MAX_ROWS — this list realistically tops out in the dozens.
MAX_ROWS = 2000


def _is_well_formed_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.netloc)


def parse_manual_review_corrections_csv(data: bytes, current_urls: dict[int, str]
                                         ) -> tuple[list[dict], list[dict], list[dict]]:
    """Parses an uploaded corrections CSV into (updates, skipped, errors).

    `current_urls` is {article_id: current stored url} for every article
    currently in the needs-manual-review list — the caller
    (webapp/app.py) builds this straight from
    Library.list_articles_needing_manual_review() rather than this module
    touching the database directly.

    updates: list of {"article_id", "current_url", "corrected_url"}, ready
    to hand to Library.apply_article_url_correction.
    skipped: list of {"line", "article_id", "reason"} — blank or unchanged,
    not an error.
    errors: list of {"line", "raw", "reason"} — malformed URL or unrecognized
    article_id; never applied.

    Raises ValueError if the file has no header row, or the header is
    missing a required column — fatal for the whole upload, same convention
    as overhead_csv.parse_overhead_csv.
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
            f"Expected a header row including at least: article_id, corrected_url."
        )

    updates: list[dict] = []
    skipped: list[dict] = []
    errors: list[dict] = []

    for line_num, raw_row in enumerate(reader, start=2):  # start=2: line 1 was the header
        if not any(cell.strip() for cell in raw_row):
            continue  # blank line, not worth reporting
        if len(updates) + len(skipped) + len(errors) >= MAX_ROWS:
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
        corrected_url = cell("corrected_url")

        try:
            article_id = int(article_id_raw)
        except ValueError:
            errors.append({"line": line_num, "raw": raw_display,
                            "reason": f"article_id '{article_id_raw}' isn't a number."})
            continue
        if article_id not in current_urls:
            errors.append({"line": line_num, "raw": raw_display,
                            "reason": f"article_id {article_id} isn't a recognized "
                                      f"needs-manual-review article (already corrected, or never was one)."})
            continue

        current_url = current_urls[article_id]
        if not corrected_url or corrected_url == current_url:
            skipped.append({"line": line_num, "article_id": article_id,
                             "reason": "No corrected_url given." if not corrected_url
                             else "corrected_url is identical to the current URL."})
            continue
        if not _is_well_formed_url(corrected_url):
            errors.append({"line": line_num, "raw": raw_display,
                            "reason": f"corrected_url '{corrected_url}' isn't a well-formed http(s) URL."})
            continue

        updates.append({"article_id": article_id, "current_url": current_url,
                         "corrected_url": corrected_url})

    return updates, skipped, errors
