"""Benchmark figures: parse and validate the zip Brian hands over (issue #738).

A figures zip holds MANIFEST.tsv plus one CSV per report. This module is
HTML-free and writes nothing to the database: the admin page
(/admin/reader/figures) renders its preview from `parse_zip()`, and
`Library.replace_benchmark_report()` does the writing, one report per
transaction. The metric-registry seed TSV is parsed here too. See
ARCHITECTURE.md ("Benchmark figures") for the flow.

MANIFEST.tsv: tab-separated, header exactly
    report_slug  file  rows  bytes  sha256
one line per CSV. report_slug must equal the CSV's filename stem.

Each CSV: UTF-8, header exactly the 16 COLUMNS below, every row 16 fields.
Values are stored verbatim. Anything off is rejected, never coerced: one bad
file is refused on its own and the other files can still load.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import zipfile
from dataclasses import dataclass, field

from .restore_status import now_iso

COLUMNS = ("source", "report", "report_year", "data_period", "band_basis", "band",
           "segment", "metric", "denominator", "statistic", "value", "unit", "n",
           "page", "read_from", "note")
MANIFEST_NAME = "MANIFEST.tsv"
MANIFEST_COLUMNS = ("report_slug", "file", "rows", "bytes", "sha256")

# Guards against a wrong or hostile file. The real zip is about 0.7 MB.
MAX_ZIP_BYTES = 50 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 300 * 1024 * 1024
MAX_MEMBERS = 2000

AUDIT_FILE = "figures-audit.jsonl"

_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_PLAIN_NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d+)?|\.\d+)$")
_THOUSANDS = re.compile(r"^[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?$")
# Junk a macOS archiver adds; not data, ignored with a note.
_IGNORED = re.compile(r"(^|/)(__MACOSX/|\.DS_Store$)")


def parse_value_num(value: str) -> float | None:
    """A float only when `value` is unambiguously one plain number, else None.

    Allowed, each at most once: surrounding parentheses (negative), a leading
    `$`, a trailing `%`, `x` or `M`, and comma thousands separators in the
    standard 1,234,567 shape. Ranges, words, dashes, unicode minus signs and
    anything else give None. The stored `value` is never changed; this only
    fills the value_num convenience column."""
    s = (value or "").strip()
    if not s:
        return None
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1].strip()
    if s.startswith("$"):
        s = s[1:]
    if s and s[-1] in "%xM":
        s = s[:-1]
    if not s or s != s.strip():
        return None
    if negative and s[0] in "+-":
        return None
    if "," in s:
        if not _THOUSANDS.match(s):
            return None
        s = s.replace(",", "")
    if not _PLAIN_NUMBER.match(s):
        return None
    num = float(s)
    return -num if negative else num


@dataclass
class FileResult:
    report_slug: str
    file: str
    manifest_rows: int | None = None
    parsed_rows: int | None = None
    sha_ok: bool | None = None
    sha256: str = ""
    rows: list[dict] = field(default_factory=list)   # dicts of COLUMNS + row_no + value_num
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def null_value_num(self) -> int:
        return sum(1 for r in self.rows if r["value_num"] is None)

    def meta(self) -> dict:
        first = self.rows[0] if self.rows else {}
        return {"report_slug": self.report_slug, "source": first.get("source", ""),
                "report": first.get("report", ""), "report_year": first.get("report_year", ""),
                "row_count": self.manifest_rows or 0, "sha256": self.sha256}


@dataclass
class ZipResult:
    zip_sha256: str = ""
    files: list[FileResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)   # whole-zip problems
    notes: list[str] = field(default_factory=list)

    @property
    def loadable(self) -> list[FileResult]:
        return [f for f in self.files if f.ok] if not self.errors else []


def _decode(data: bytes) -> str | None:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None


def _parse_manifest(text: str, out: ZipResult) -> list[dict]:
    lines = [ln for ln in text.splitlines()]
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        out.errors.append("MANIFEST.tsv is empty.")
        return []
    header = tuple(c.strip() for c in lines[0].split("\t"))
    if header != MANIFEST_COLUMNS:
        out.errors.append("MANIFEST.tsv header must be exactly: " + ", ".join(MANIFEST_COLUMNS)
                          + f". Found: {', '.join(header)}.")
        return []
    entries: list[dict] = []
    for i, ln in enumerate(lines[1:], start=2):
        cells = ln.split("\t")
        if len(cells) != len(MANIFEST_COLUMNS):
            out.errors.append(f"MANIFEST.tsv line {i} has {len(cells)} fields, not {len(MANIFEST_COLUMNS)}.")
            continue
        entries.append({"line": i, **{k: v.strip() for k, v in zip(MANIFEST_COLUMNS, cells)}})
    return entries


def _check_csv(fr: FileResult, text: str) -> None:
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        header = next(reader)
    except StopIteration:
        fr.errors.append("The CSV is empty.")
        return
    except csv.Error as exc:
        fr.errors.append(f"The CSV could not be read: {exc}.")
        return
    if tuple(header) != COLUMNS:
        fr.errors.append("Wrong header. It must be exactly: " + ", ".join(COLUMNS) + ".")
        return
    rows: list[dict] = []
    try:
        for row_no, cells in enumerate(reader, start=1):
            if len(cells) != len(COLUMNS):
                fr.errors.append(f"Row {row_no} has {len(cells)} fields, not {len(COLUMNS)}.")
                return
            rec = dict(zip(COLUMNS, cells))
            rec["row_no"] = row_no
            rec["value_num"] = parse_value_num(rec["value"])
            rows.append(rec)
    except csv.Error as exc:
        fr.errors.append(f"The CSV could not be read near row {len(rows) + 1}: {exc}.")
        return
    fr.parsed_rows = len(rows)
    if fr.manifest_rows is not None and fr.parsed_rows != fr.manifest_rows:
        fr.errors.append(f"The manifest says {fr.manifest_rows:,} rows; the file has {fr.parsed_rows:,}.")
        return
    fr.rows = rows
    if not rows:
        fr.notes.append("No data rows.")
        return
    for col in ("source", "report", "report_year"):
        vals = {r[col] for r in rows}
        if len(vals) > 1:
            fr.notes.append(f"{col} differs across rows ({len(vals)} values); the report record uses row 1.")
    if fr.null_value_num:
        fr.notes.append(f"{fr.null_value_num:,} of {len(rows):,} values are not plain numbers "
                        "(value_num left empty; value is stored as written).")


def parse_zip(data: bytes) -> ZipResult:
    out = ZipResult(zip_sha256=hashlib.sha256(data).hexdigest())
    if len(data) > MAX_ZIP_BYTES:
        out.errors.append(f"The zip is over {MAX_ZIP_BYTES // (1024 * 1024)} MB.")
        return out
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        out.errors.append("This is not a zip file.")
        return out
    with zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > MAX_MEMBERS:
            out.errors.append(f"The zip holds more than {MAX_MEMBERS:,} files.")
            return out
        if sum(i.file_size for i in infos) > MAX_UNCOMPRESSED_BYTES:
            out.errors.append("The zip unpacks to more than "
                              f"{MAX_UNCOMPRESSED_BYTES // (1024 * 1024)} MB.")
            return out
        ignored = [i.filename for i in infos if _IGNORED.search(i.filename)]
        if ignored:
            out.notes.append(f"Ignored {len(ignored)} macOS archive file(s) such as __MACOSX/ and .DS_Store.")
        members = {i.filename: i for i in infos if not _IGNORED.search(i.filename)}
        if MANIFEST_NAME not in members:
            out.errors.append("MANIFEST.tsv is missing from the top of the zip.")
            return out
        mtext = _decode(zf.read(members[MANIFEST_NAME]))
        if mtext is None:
            out.errors.append("MANIFEST.tsv is not UTF-8 text.")
            return out
        entries = _parse_manifest(mtext, out)
        if out.errors:
            return out
        seen_slugs: dict[str, int] = {}
        seen_files: set[str] = set()
        for e in entries:
            fr = FileResult(report_slug=e["report_slug"], file=e["file"])
            out.files.append(fr)
            slug, fname = e["report_slug"], e["file"]
            if not _SLUG.match(slug):
                fr.errors.append("report_slug must be letters, digits, dot, dash or underscore.")
            stem = os.path.splitext(os.path.basename(fname))[0]
            if slug != stem:
                fr.errors.append(f"report_slug must equal the file name without .csv ({stem!r}).")
            if not fname.lower().endswith(".csv"):
                fr.errors.append("The file must be a .csv.")
            if slug in seen_slugs:
                fr.errors.append(f"Duplicate report_slug (also on manifest line {seen_slugs[slug]}).")
            seen_slugs.setdefault(slug, e["line"])
            if fname in seen_files:
                fr.errors.append("Listed twice in the manifest.")
            seen_files.add(fname)
            try:
                fr.manifest_rows = int(e["rows"])
                want_bytes = int(e["bytes"])
            except ValueError:
                fr.errors.append("rows and bytes must be whole numbers.")
                want_bytes = None
            want_sha = e["sha256"].lower()
            if not _SHA.match(want_sha):
                fr.errors.append("sha256 must be 64 hex characters.")
            if fname not in members:
                fr.errors.append("Listed in the manifest but missing from the zip.")
            if fr.errors:
                continue
            raw = zf.read(members[fname])
            fr.sha256 = hashlib.sha256(raw).hexdigest()
            fr.sha_ok = fr.sha256 == want_sha
            if want_bytes is not None and len(raw) != want_bytes:
                fr.errors.append(f"The manifest says {want_bytes:,} bytes; the file has {len(raw):,}.")
            if not fr.sha_ok:
                fr.errors.append("sha256 does not match the manifest.")
            if fr.errors:
                continue
            text = _decode(raw)
            if text is None:
                fr.errors.append("The file is not UTF-8 text.")
                continue
            _check_csv(fr, text)
        for name in sorted(members):
            if name != MANIFEST_NAME and name not in seen_files:
                fr = FileResult(report_slug=os.path.splitext(os.path.basename(name))[0], file=name)
                fr.errors.append("In the zip but not in the manifest.")
                out.files.append(fr)
    return out


def append_audit(db_path: str, record: dict) -> None:
    """One JSON line per load, on the volume next to the database (beside
    restore-audit.jsonl), so the history survives a restore that swaps the
    database out."""
    rec = {"at": now_iso(), **record}
    path = os.path.join(os.path.dirname(os.path.abspath(db_path)), AUDIT_FILE)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")


# --- Metric registry seed -----------------------------------------------------

@dataclass
class RegistryRow:
    line: int
    canonical_metric: str
    card_url: str
    aliases_line: str
    aliases: list[str]


_CONTAINS = re.compile(r"^\s*contains\s*:\s*", re.I)
_SOURCES = re.compile(r"\bsources?\s*:", re.I)


def split_aliases(contains_line: str) -> list[str]:
    """'Contains: a, B, c. sources: x' -> ['a', 'b', 'c'] (lowercase, in order,
    no duplicates). The sources tail is dropped."""
    s = _CONTAINS.sub("", contains_line or "")
    m = _SOURCES.search(s)
    if m:
        s = s[:m.start()]
    out: list[str] = []
    for part in s.split(","):
        a = " ".join(part.strip().strip(" .;|").split()).lower()
        if a and a not in out:
            out.append(a)
    return out


def parse_registry_tsv(text: str) -> tuple[list[RegistryRow], list[str]]:
    """(rows, errors). Three tab-separated columns per line: canonical_metric,
    card_url, the Contains line. An optional header line starting with
    canonical_metric is skipped. Any error means nothing should be written."""
    rows: list[RegistryRow] = []
    errors: list[str] = []
    seen: set[str] = set()
    for i, ln in enumerate((text or "").splitlines(), start=1):
        if not ln.strip():
            continue
        cells = ln.split("\t")
        if i == 1 and cells and cells[0].strip().lower() == "canonical_metric":
            continue
        if len(cells) != 3:
            errors.append(f"Line {i} has {len(cells)} fields, not 3.")
            continue
        name, url, contains = (c.strip() for c in cells)
        if not name:
            errors.append(f"Line {i} has no canonical_metric.")
            continue
        if name.lower() in seen:
            errors.append(f"Line {i} repeats {name!r}.")
            continue
        seen.add(name.lower())
        rows.append(RegistryRow(i, name, url, contains, split_aliases(contains)))
    if not rows and not errors:
        errors.append("The file has no rows.")
    return rows, errors
