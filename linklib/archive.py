"""Parser for the Feedly "Download your data" archive.

The export is NOT the API JSON shape — it's Netscape bookmark HTML, one file
per board under `my boards/`, plus a `read/` folder of read-history files.

Each saved item looks like:
    <DT><A HREF="https://..." ADD_DATE="1538537774">Title</A>

Per item we get: URL, title, and save date (epoch SECONDS). No summaries,
notes, or per-item tags — the board (the file) is the tag. Board labels carry
a two-level taxonomy in the <H1> ("Feedly - Finance: KPIs"), which we split
into parent + sub tags (["Finance", "KPIs"]).
"""
from __future__ import annotations

import glob
import os
import re
from datetime import datetime, timezone
from typing import Iterator, Optional

from .db import Article


def _epoch_s_to_iso(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def _board_tags(h1_text: str) -> list[str]:
    """'Feedly - Finance: KPIs' -> ['Finance', 'KPIs']; single-level -> one tag."""
    label = re.sub(r"^\s*Feedly\s*-\s*", "", h1_text or "").strip()
    if not label:
        return []
    if ": " in label:
        parent, sub = label.split(": ", 1)
        return [parent.strip(), sub.strip()]
    return [label]


def _parse_bookmark_file(path: str, fallback_tags: list[str]) -> Iterator[Article]:
    from bs4 import BeautifulSoup

    with open(path, encoding="utf-8") as f:
        soup = BeautifulSoup(f.read(), "html.parser")

    h1 = soup.find("h1")
    tags = _board_tags(h1.get_text()) if h1 else fallback_tags

    for a in soup.find_all("a"):
        href = (a.get("href") or "").strip()
        if not href.startswith("http"):
            continue
        title = re.sub(r"\s+", " ", a.get_text(" ", strip=True)) or href
        yield Article(
            url=href,
            title=title,
            tags=list(tags),
            saved_at=_epoch_s_to_iso(a.get("add_date")),
        )


def _filename_label(path: str) -> str:
    base = os.path.basename(path)
    return re.sub(r"^board-", "", re.sub(r"-bookmarks\.html$", "", base)).strip()


def iter_boards(archive_root: str, exclude: Optional[set[str]] = None) -> Iterator[Article]:
    """Yield Articles from every board file under `my boards/`.

    `exclude` is matched case-insensitively against the board's filename label
    (e.g. "Created", "Unsaved").
    """
    exclude_lc = {e.strip().lower() for e in (exclude or set()) if e.strip()}
    board_dir = os.path.join(archive_root, "my boards")
    for path in sorted(glob.glob(os.path.join(board_dir, "*.html"))):
        if _filename_label(path).lower() in exclude_lc:
            continue
        yield from _parse_bookmark_file(path, fallback_tags=["Uncategorized"])


def iter_read_history(archive_root: str) -> Iterator[Article]:
    """Yield Articles from the read-history folder, tagged 'Read History' + month."""
    read_dir = os.path.join(archive_root, "read")
    for path in sorted(glob.glob(os.path.join(read_dir, "*.html"))):
        m = re.search(r"(\d{4}-\d{2})", os.path.basename(path))
        month = m.group(1) if m else "unknown"
        for art in _parse_bookmark_file(path, fallback_tags=["Read History"]):
            art.tags = ["Read History", month]
            yield art


def iter_archive(archive_root: str, include_read: bool = False,
                 exclude: Optional[set[str]] = None) -> Iterator[Article]:
    yield from iter_boards(archive_root, exclude=exclude)
    if include_read:
        yield from iter_read_history(archive_root)
