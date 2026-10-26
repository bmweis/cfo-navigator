"""Benchmark digest bundles: parse and validate the file Brian hands over.

A bundle is plain text, several digests separated by a line holding only
`=====`. This module is HTML-free and writes nothing; `pipeline.ingest_text`
does the writing, and `/admin/reader/digests` renders the preview from the
rows this returns. See ARCHITECTURE.md ("Benchmark digests") for the flow.

Format, per digest (header lines first, then CONTENT runs to the next
separator or the end of the file):

    TITLE: <text>
    URL: <report landing URL>?digest=<slug>
    SOURCE: <text>
    PUBLISHED: <YYYY | YYYY-MM | YYYY-MM-DD>
    TAGS: benchmark-digest            (optional; benchmark-digest is always added)
    SUMMARY: <one paragraph, single line>
    CONTENT:
    <markdown, multiple lines>

Errors refuse the whole bundle (no partial loads). Warnings (amber) never do.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from .db import DIGEST_TAG, normalize_url

SUMMARY_TARGET = 700
SUMMARY_MAX = 1000
CONTENT_TARGET = 6000
CONTENT_MAX = 12000

# One bundle is a few dozen digests. These caps only stop a wrong file (a
# whole report pasted by mistake) from being parsed and rendered.
MAX_BUNDLE_CHARS = 3_000_000
MAX_DIGESTS = 200

_SEPARATOR = re.compile(r"^=====\s*$")
_HEADER_KEYS = ("TITLE", "URL", "SOURCE", "PUBLISHED", "TAGS", "SUMMARY")
_REQUIRED = ("TITLE", "URL", "SOURCE", "PUBLISHED", "SUMMARY")
_HEADER_LINE = re.compile(r"^([A-Z]+):[ \t]*(.*)$")
_PUBLISHED = re.compile(r"^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?$")
_YEAR_IN_TITLE = re.compile(r"\b(?:19|20)\d{2}\b")


@dataclass
class DigestRow:
    index: int                      # 1-based position in the bundle
    title: str = ""
    url: str = ""                   # as written
    norm_url: str = ""              # after normalize_url; the row's identity
    source: str = ""
    published: str = ""             # as written
    published_at: str = ""          # ISO 8601, UTC midnight
    tags: list[str] = field(default_factory=list)
    summary: str = ""
    content: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def expand_published(value: str) -> str | None:
    """YYYY, YYYY-MM or YYYY-MM-DD to ISO 8601 at UTC midnight on the first
    day of the period. None when it isn't a real date."""
    m = _PUBLISHED.match((value or "").strip())
    if not m:
        return None
    year, month, day = int(m.group(1)), int(m.group(2) or 1), int(m.group(3) or 1)
    try:
        d = date(year, month, day)
    except ValueError:
        return None
    return f"{d.isoformat()}T00:00:00+00:00"


def canonical_tags(tags) -> list[str]:
    """Trimmed, de-duplicated tags with exactly one DIGEST_TAG in its canonical
    spelling. A `Benchmark-Digest` in the file is folded into it: the skip
    guards compare the tag by its exact spelling."""
    out = {t.strip() for t in tags if t and t.strip() and t.strip().lower() != DIGEST_TAG}
    out.add(DIGEST_TAG)
    return sorted(out, key=str.lower)


def split_bundle(text: str) -> list[str]:
    """Chunks between `=====` lines. Windows line endings are tolerated, and
    chunks that are only whitespace (a trailing blank, a doubled separator)
    are dropped."""
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿").split("\n")
    chunks: list[list[str]] = [[]]
    for line in lines:
        if _SEPARATOR.match(line):
            chunks.append([])
        else:
            chunks[-1].append(line)
    return ["\n".join(c) for c in chunks if "\n".join(c).strip()]


def _parse_chunk(index: int, chunk: str) -> DigestRow:
    row = DigestRow(index=index)
    fields: dict[str, str] = {}
    lines = chunk.split("\n")
    content_lines: list[str] | None = None
    for n, line in enumerate(lines):
        if content_lines is not None:
            content_lines.append(line)
            continue
        if not line.strip():
            continue
        if line.strip() == "CONTENT:":
            content_lines = []
            continue
        m = _HEADER_LINE.match(line)
        if not m or m.group(1) not in _HEADER_KEYS:
            row.errors.append(
                f"Unrecognized header line: {line.strip()[:60]!r}. "
                "Headers are TITLE, URL, SOURCE, PUBLISHED, TAGS and SUMMARY, each on one line "
                "(the summary too), then CONTENT: on its own line.")
            continue
        key, value = m.group(1), m.group(2).strip()
        if key in fields:
            row.errors.append(f"{key} appears twice.")
            continue
        fields[key] = value

    row.title = fields.get("TITLE", "")
    row.url = fields.get("URL", "")
    row.source = fields.get("SOURCE", "")
    row.published = fields.get("PUBLISHED", "")
    row.summary = fields.get("SUMMARY", "")
    row.content = "\n".join(content_lines or []).strip()

    for key in _REQUIRED:
        if not fields.get(key):
            row.errors.append(f"Missing {key}.")
    if content_lines is None:
        row.errors.append("Missing CONTENT: line.")
    elif not row.content:
        row.errors.append("CONTENT is empty.")

    row.tags = canonical_tags(fields.get("TAGS", "").split(","))

    if row.url:
        if not re.match(r"^https?://\S+$", row.url, re.IGNORECASE):
            row.errors.append("URL must start with http:// or https:// and contain no spaces.")
        else:
            row.norm_url = normalize_url(row.url)
            if "digest=" not in row.norm_url:
                row.warnings.append(
                    "The URL has no ?digest=<slug>. Two digests of one report would then "
                    "share a row, and the second would overwrite the first.")
    if row.published:
        iso = expand_published(row.published)
        if iso is None:
            row.errors.append("PUBLISHED must be YYYY, YYYY-MM or YYYY-MM-DD, and a real date.")
        else:
            row.published_at = iso

    n = len(row.summary)
    if n > SUMMARY_MAX:
        row.errors.append(f"Summary is {n:,} characters. The limit is {SUMMARY_MAX:,}.")
    elif n > SUMMARY_TARGET:
        row.warnings.append(f"Summary: {n:,} characters. Aim for {SUMMARY_TARGET:,}.")
    n = len(row.content)
    if n > CONTENT_MAX:
        row.errors.append(f"Content is {n:,} characters. The limit is {CONTENT_MAX:,}.")
    elif n > CONTENT_TARGET:
        row.warnings.append(f"Content: {n:,} characters. Aim for {CONTENT_TARGET:,}.")
    if row.title and not _YEAR_IN_TITLE.search(row.title):
        row.warnings.append(
            "The title has no 4-digit year. Retrieval sees only the title and the start of the "
            "body, never the URL or the published date, so a year in the title is how a 2025 "
            "report gets told apart from a 2024 one.")
    from .embeddings import DOCUMENT_MAX_CHARS
    embedded = len(row.title) + len(row.summary) + len(row.content) + sum(len(t) + 2 for t in row.tags)
    if embedded > DOCUMENT_MAX_CHARS:
        row.warnings.append(
            f"Search embeds only the first {DOCUMENT_MAX_CHARS:,} characters of title, tags, "
            "summary and content together, so the end of this digest is not searchable by meaning.")
    return row


def parse_bundle(text: str) -> tuple[list[DigestRow], list[str]]:
    """(rows, bundle_errors). `bundle_errors` are problems with the file as a
    whole (empty, too large, too many digests); per-row problems live on each
    row. Duplicate normalized URLs inside the bundle are flagged on the later
    row, naming both titles."""
    if not (text or "").strip():
        return [], ["The bundle is empty."]
    if len(text) > MAX_BUNDLE_CHARS:
        return [], [f"The bundle is {len(text):,} characters. The limit is {MAX_BUNDLE_CHARS:,}."]
    chunks = split_bundle(text)
    if not chunks:
        return [], ["No digests found. Digests are separated by a line holding only =====."]
    if len(chunks) > MAX_DIGESTS:
        return [], [f"The bundle holds {len(chunks)} digests. The limit is {MAX_DIGESTS}."]
    rows = [_parse_chunk(i, c) for i, c in enumerate(chunks, 1)]
    first_by_url: dict[str, DigestRow] = {}
    for row in rows:
        if not row.norm_url:
            continue
        prior = first_by_url.get(row.norm_url)
        if prior is None:
            first_by_url[row.norm_url] = row
        else:
            row.errors.append(
                f"Same URL as digest {prior.index} (\"{prior.title}\") after normalizing: "
                f"\"{row.title}\" and \"{prior.title}\" would be one row.")
    return rows, []


def plan_rows(rows: list[DigestRow], lookup) -> list[dict]:
    """Preview status for each row. `lookup(norm_url)` returns the existing
    article dict or None. Statuses: new, overwrite (id), or error (reason).
    An existing row at the URL that is not already a digest is an error: the
    overwrite method replaces content, and this tool must never replace a real
    saved article."""
    out = []
    for row in rows:
        entry = {"row": row, "status": "new", "article_id": None, "error": ""}
        if row.errors:
            entry["status"] = "error"
            entry["error"] = " ".join(row.errors)
        elif row.norm_url:
            existing = lookup(row.norm_url)
            if existing is not None:
                if DIGEST_TAG in (existing.get("tags") or []):
                    entry["status"] = "overwrite"
                    entry["article_id"] = existing["id"]
                else:
                    entry["status"] = "error"
                    entry["error"] = (f"Article #{existing['id']} already uses this URL and is not a "
                                      "digest. Digests never overwrite a saved article.")
        out.append(entry)
    return out
