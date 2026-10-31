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


SHORT_LINK_HOSTS = {"bit.ly", "t.co", "tinyurl.com", "lnkd.in", "ow.ly", "buff.ly", "goo.gl", "is.gd", "rebrand.ly"}


def is_short_link(url: str) -> bool:
    """True for a well-known link shortener host (flagged in the landing import preview)."""
    from urllib.parse import urlparse
    host = (urlparse(url or "").hostname or "").lower()
    return host in SHORT_LINK_HOSTS


def parse_landing_tsv(text: str) -> tuple[list[tuple[int, str, str]], list[str]]:
    """((line, report_slug, landing_url) rows, errors). Two tab-separated
    columns; an optional header line starting with report_slug is skipped.
    A non-http(s) URL, a wrong field count or a repeated slug is an error, and
    any error means nothing should be written."""
    import re
    rows: list[tuple[int, str, str]] = []
    errors: list[str] = []
    seen: set[str] = set()
    for i, ln in enumerate((text or "").splitlines(), start=1):
        if not ln.strip():
            continue
        cells = ln.split("\t")
        if i == 1 and cells[0].strip().lower() == "report_slug":
            continue
        if len(cells) != 2:
            errors.append(f"Line {i} has {len(cells)} fields, not 2.")
            continue
        slug, url = (c.strip() for c in cells)
        if not slug:
            errors.append(f"Line {i} has no report_slug.")
        elif not re.match(r"^https?://[^\s]+$", url, re.I):
            errors.append(f"Line {i} ({slug}): the URL must start with http:// or https:// and have no spaces.")
        elif slug.lower() in seen:
            errors.append(f"Line {i} repeats {slug!r}.")
        else:
            seen.add(slug.lower())
            rows.append((i, slug, url))
    if not rows and not errors:
        errors.append("The file has no rows.")
    return rows, errors


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


# =============================================================================
# Phase B (issue #738): the query layer. No model calls, no HTML, read-only.
# Everything below takes an open Library and returns plain dicts, so the admin
# MCP tools (webapp/mcp_figures.py) and FP&A Buddy's pre-step
# (linklib/benchmark_prestep.py) read the same stored figures the same way.
# =============================================================================
import statistics as _stats
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Severity order of a figure's `match` flag: the worst gap on a row names it,
# and `gaps` lists every gap in words. `different_statistic` and
# `different_segment` extend the brief's list: without them a p25 row served
# for a median question would have to be called one of the others.
MATCH_ORDER = ("exact", "adjacent_band", "different_segment", "different_statistic",
               "different_basis", "different_definition", "related_metric")
_MATCH_RANK = {m: i for i, m in enumerate(MATCH_ORDER)}

DEFAULT_LIMIT = 60
NO_LINK_NOTE = "no link on file"


def _lc(s) -> str:
    return " ".join(str(s or "").split()).casefold()


# --- landing URLs (B0) ---------------------------------------------------------

def strip_digest_param(url: str) -> str:
    """The URL without its `digest` query parameter (other parameters stay)."""
    p = urlsplit((url or "").strip())
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k != "digest"]
    return urlunsplit((p.scheme, p.netloc, p.path, urlencode(q), ""))


def _digest_slug_of(url: str) -> str:
    for k, v in parse_qsl(urlsplit(url or "").query, keep_blank_values=True):
        if k == "digest":
            return v
    return ""


def resolve_landings(lib) -> dict[str, dict]:
    """report_slug -> {"url", "how"} for every loaded report. Order: the
    admin-set URL, then landing_url, then the linked digest article (digest
    parameter removed), then the one digest article whose URL carries
    ?digest=<report_slug>. `how` is manual | landing_url | article |
    digest_slug | none (url is ""). Several candidates is "none": never a guess."""
    digests: dict[str, list[str]] = {}
    for r in lib.conn.execute("SELECT url FROM articles WHERE url LIKE '%digest=%'"):
        slug = _digest_slug_of(r["url"])
        if slug:
            digests.setdefault(slug, []).append(r["url"])
    out: dict[str, dict] = {}
    # A database opened read-only before the Phase B migration has no manual
    # column yet (scripts/report_figure_landings.py); read it as empty.
    has_manual = any(c[1] == "landing_url_manual"
                     for c in lib.conn.execute("PRAGMA table_info(benchmark_reports)"))
    manual_sql = "landing_url_manual" if has_manual else "'' AS landing_url_manual"
    for r in lib.conn.execute(
            f"SELECT report_slug, landing_url, {manual_sql}, article_id FROM benchmark_reports"):
        slug = r["report_slug"]
        url, how = "", "none"
        if (r["landing_url_manual"] or "").strip():
            url, how = r["landing_url_manual"].strip(), "manual"
        elif (r["landing_url"] or "").strip():
            url, how = strip_digest_param(r["landing_url"]), "landing_url"
        elif r["article_id"]:
            a = lib.conn.execute("SELECT url FROM articles WHERE id = ?", (r["article_id"],)).fetchone()
            if a and a["url"]:
                url, how = strip_digest_param(a["url"]), "article"
        if not url and len(digests.get(slug, [])) == 1:
            url, how = strip_digest_param(digests[slug][0]), "digest_slug"
        out[slug] = {"url": url, "how": how}
    return out


# --- metric resolution (B1) ----------------------------------------------------

def _load_metric_index(lib) -> dict:
    """canonical (lowercase) -> display name; alias (lowercase) -> set of
    canonical names. Aliases come from metric_aliases AND from each registry
    row's Contains line, because metric_aliases keeps one owner per alias (the
    seed's first-wins rule) while the Contains lines show every claimant. An
    alias a human edited (edited=1) is that metric's alone."""
    canon: dict[str, str] = {}
    card: dict[str, str] = {}
    claims: dict[str, set[str]] = {}
    for r in lib.conn.execute("SELECT canonical_metric, card_url, aliases_line FROM metric_registry"):
        c = r["canonical_metric"]
        canon[c.casefold()] = c
        card[c] = r["card_url"] or ""
        for a in split_aliases(r["aliases_line"]):
            claims.setdefault(a, set()).add(c)
    for r in lib.conn.execute("SELECT alias, canonical_metric, edited FROM metric_aliases"):
        a = r["alias"].casefold()
        if r["edited"]:
            claims[a] = {r["canonical_metric"]}
        else:
            claims.setdefault(a, set()).add(r["canonical_metric"])
    mapped: dict[str, set[str]] = {}
    for r in lib.conn.execute(
            "SELECT raw_name_lower, canonical_metric FROM metric_name_map WHERE canonical_metric IS NOT NULL AND canonical_metric <> ''"):
        mapped.setdefault(r["canonical_metric"], set()).add(r["raw_name_lower"].casefold())
    return {"canon": canon, "card": card, "claims": claims, "mapped": mapped}


def resolve_metric(lib, text: str, _index: dict | None = None) -> list[dict]:
    """[{canonical, match}] for a metric phrase. An exact canonical name beats
    any alias. An alias claimed by several metrics returns all of them with
    match "ambiguous_alias". An unknown phrase returns []."""
    idx = _index or _load_metric_index(lib)
    t = _lc(text)
    if not t:
        return []
    if t in idx["canon"]:
        return [{"canonical": idx["canon"][t], "match": "canonical"}]
    owners = sorted(idx["claims"].get(t, ()))
    if len(owners) == 1:
        return [{"canonical": owners[0], "match": "alias"}]
    if len(owners) > 1:
        return [{"canonical": o, "match": "ambiguous_alias"} for o in owners]
    for c, names in idx["mapped"].items():
        if t in names:
            return [{"canonical": c, "match": "name_map"}]
    return []


def _names_for(idx: dict, canonical: str) -> set[str]:
    names = {canonical.casefold()}
    names |= {a for a, owners in idx["claims"].items() if canonical in owners}
    names |= idx["mapped"].get(canonical, set())
    return names


def detect_metrics(lib, text: str) -> list[dict]:
    """Metric mentions in free text, by rules only: every registry name or
    alias that appears as whole words, longest phrase first, a phrase inside a
    longer one already found is dropped. [{canonical, term, match}] in order of
    appearance. No model call."""
    idx = _load_metric_index(lib)
    low = _lc(text)
    terms: dict[str, str] = {}
    for c in idx["canon"]:
        terms[c] = "canonical"
    for a in idx["claims"]:
        terms.setdefault(a, "alias")
    for c, names in idx["mapped"].items():
        for n in names:
            terms.setdefault(n, "name_map")
    found: list[tuple[int, int, str]] = []
    taken: list[tuple[int, int]] = []
    for term in sorted(terms, key=len, reverse=True):
        if len(term) < 3:
            continue
        m = re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", low)
        if not m:
            continue
        s, e = m.span()
        if any(s >= ts and e <= te for ts, te in taken):
            continue
        taken.append((s, e))
        found.append((s, e, term))
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for s, e, term in sorted(found):
        for r in resolve_metric(lib, term, idx):
            key = (r["canonical"], term)
            if key not in seen:
                seen.add(key)
                out.append({"canonical": r["canonical"], "term": term, "match": r["match"]})
    return out


# --- bands ---------------------------------------------------------------------

_BAND_NUM = re.compile(r"(\d+(?:\.\d+)?)\s*([kmb])?(?![a-z])", re.I)
_UNIT = {"k": 1e3, "m": 1e6, "b": 1e9}


def parse_band(text) -> dict | None:
    """Parse a printed band only so bands can be ordered and compared; the
    printed text is always kept verbatim elsewhere. {lo, hi, kind} where kind
    is money ($), pct (%) or count; hi may be inf. None when it cannot be
    read ("All", "n/a")."""
    s = str(text or "").strip().lower()
    if not s:
        return None
    s = s.replace(",", "").replace("–", "-").replace("—", "-").replace("−", "-")
    s = re.sub(r"\bto\b", "-", s)
    kind = "money" if "$" in s else ("pct" if "%" in s else "count")
    nums = [(float(m.group(1)), (m.group(2) or "").lower()) for m in _BAND_NUM.finditer(s)]
    if not nums:
        return None
    if kind == "count" and any(u for _, u in nums):
        kind = "money"
    if len(nums) >= 2:
        (a, ua), (b, ub) = nums[0], nums[1]
        ua = ua or ub
        lo, hi = a * _UNIT.get(ua, 1), b * _UNIT.get(ub, 1)
        if hi < lo:
            lo, hi = hi, lo
        return {"lo": lo, "hi": hi, "kind": kind}
    n, u = nums[0]
    v = n * _UNIT.get(u, 1)
    if re.search(r"(<|under|below|less than|up to)", s):
        return {"lo": 0.0, "hi": v, "kind": kind}
    if re.search(r"(>|over|above|more than|at least)|\+", s):
        return {"lo": v, "hi": float("inf"), "kind": kind}
    return {"lo": v, "hi": v, "kind": kind}


_BASIS = {"annual recurring revenue": "arr", "arr": "arr", "revenue": "revenue",
          "annual revenue": "revenue", "employees": "employees", "headcount": "employees",
          "fte": "employees", "ftes": "employees"}


def _basis_key(s) -> str:
    t = _lc(s)
    return _BASIS.get(t, t)


# --- editions ------------------------------------------------------------------

_YEAR_IN_NAME = re.compile(r"\b(?:19|20)\d{2}\b")


def _family(row: dict) -> tuple[str, str]:
    name = _YEAR_IN_NAME.sub("", row.get("report") or "")
    return (_lc(row.get("source")), _lc(name))


# A row from a worked example is not survey data. Matched on the fields a loader fills
# for an example company (data period, segment, note, metric), never on the report name.
_ILLUSTRATIVE = re.compile(r"illustrative|\bexample\b|worked example|hypothetical|sample company", re.I)


def is_illustrative(row: dict) -> bool:
    return any(_ILLUSTRATIVE.search(str(row.get(f) or "")) for f in ("data_period", "segment", "note", "metric"))


def _has_sample_size(row: dict) -> int:
    """0 when the report states a sample size (a survey), 1 when it does not."""
    n = str(row.get("n") or "").strip().lower()
    return 0 if n and n not in ("n/a", "na", "-", "none", "not stated") else 1


# Words that are not a segment: every report is a SaaS report, so the word narrows nothing.
GENERIC_SEGMENT_WORDS = {"saas", "b2b", "b2c", "software", "company", "companies", "startup", "startups",
                         "business", "businesses", "tech", "technology", "cloud", "all", "any", "the", "a", "an", "and"}


def usable_segment(lib, segment) -> str:
    """The segment to search with, or "". A generic word ("SaaS", "B2B SaaS", "software",
    "company") is dropped, and so is one no stored figure uses: pass a segment only when
    the question states one that the figures print."""
    seg = " ".join(str(segment or "").lower().replace("/", " ").split())
    if not seg:
        return ""
    words = re.findall(r"[a-z0-9]+", seg)
    if not words or all(w in GENERIC_SEGMENT_WORDS for w in words):
        return ""
    stored = {_lc(r[0]) for r in lib.conn.execute("SELECT DISTINCT segment FROM benchmark_figures")}
    return seg if _lc(seg) in stored else ""


def _year_num(y) -> int:
    m = re.search(r"\d{4}", str(y or ""))
    return int(m.group()) if m else -1


# --- find_figures --------------------------------------------------------------

_FTS_STOP = {"the", "of", "and", "for", "a", "an", "in", "to", "per", "by", "rate", "ratio", "metric"}


def _fts_metric_rows(lib, text: str, cap: int = 400) -> list[dict]:
    toks = [t for t in re.findall(r"[a-z0-9]+", _lc(text)) if len(t) > 2 and t not in _FTS_STOP]
    if not toks:
        return []
    q = "metric : (" + " AND ".join(f'"{t}"' for t in toks) + ")"
    try:
        rows = lib.conn.execute(
            """SELECT f.* FROM benchmark_figures_fts x JOIN benchmark_figures f ON f.id = x.rowid
               WHERE benchmark_figures_fts MATCH ? ORDER BY bm25(benchmark_figures_fts) LIMIT ?""",
            (q, cap)).fetchall()
    except Exception:
        return []
    return [dict(r) for r in rows]


def _row_out(r: dict, landings: dict, canonical: str) -> dict:
    land = landings.get(r["report_slug"], {"url": "", "how": "none"})
    return {
        "figure_key": r["id"], "report_slug": r["report_slug"], "source": r["source"], "report": r["report"],
        "report_year": r["report_year"], "data_period": r["data_period"],
        "band_basis": r["band_basis"], "band": r["band"], "segment": r["segment"],
        "metric": r["metric"], "canonical_metric": canonical, "denominator": r["denominator"],
        "statistic": r["statistic"], "value": r["value"], "value_num": r["value_num"],
        "unit": r["unit"], "n": r["n"], "page": r["page"],
        "link": land["url"], "link_note": "" if land["url"] else NO_LINK_NOTE,
        "match": "exact", "gaps": [],
    }


def _flag(row: dict, match: str, gap: str) -> None:
    row["gaps"].append(gap)
    if _MATCH_RANK[match] > _MATCH_RANK[row["match"]]:
        row["match"] = match


def find_figures(lib, metric: str, band=None, segment=None, statistic=None, year=None,
                 basis=None, denominator=None, all_editions: bool = False,
                 limit: int = DEFAULT_LIMIT, include_illustrative: bool = False,
                 basis_hint: str = "") -> dict:
    """Stored figures for a metric, exact matches first, every gap disclosed.

    Returns {rows, resolution, notes, total_matching, returned, truncated,
    unplaced_bands}. Each row carries the figure's own fields verbatim plus
    `match` (see MATCH_ORDER), `gaps` (plain-words list of how it differs from
    the request), `canonical_metric` and `link` (the report's landing URL, or
    "" with link_note "no link on file"). Within each report, rows that agree
    with a requested basis, denominator, segment or statistic are kept and the
    rest dropped; when none agree, the report's rows are kept and flagged.
    A requested band returns the containing band, or else the bands either
    side. The newest edition per publisher and report family is used unless
    all_editions=True or `year` is given.

    Expense families (S&M, R&D, G&A, customer success, total opex): rows are found by rules
    on the stored name and denominator and returned in basis groups (revenue, ARR, basis not
    stated), the asked basis first. The asked basis is read from `metric`, `denominator` and
    `basis_hint` (the user's own words). Each row carries `basis_group` and `other_basis`;
    nothing is converted, merged or averaged. `groups` lists the groups present."""
    idx = _load_metric_index(lib)
    landings = resolve_landings(lib)
    notes: list[str] = []
    resolution = resolve_metric(lib, metric, idx)
    ambiguous = len(resolution) > 1 and all(r["match"] == "ambiguous_alias" for r in resolution)
    if ambiguous:
        notes.append(f"'{metric}' is used for more than one metric ("
                     + ", ".join(r["canonical"] for r in resolution)
                     + "); figures for each are returned and none is assumed.")

    cand: list[tuple[dict, str]] = []     # (row, canonical)
    seen_ids: set[int] = set()
    related = False
    targets = [r["canonical"] for r in resolution]
    stored_names: list[str] | None = None
    for canonical in targets:
        names = _names_for(idx, canonical) | {_lc(metric)}
        # A stored name that differs from a known name only by a symbol or a parenthetical
        # ("Gross Margin %", "Gross Margin % (LTM)") is the same metric.
        if stored_names is None:
            stored_names = [r[0] for r in lib.conn.execute("SELECT DISTINCT lower(trim(metric)) FROM benchmark_figures")]
        keys = {k for k in (name_key(n) for n in names) if k}
        names |= {n for n in stored_names if n and name_key(n) in keys}
        marks = ",".join("?" for _ in names)
        for r in lib.conn.execute(
                f"SELECT * FROM benchmark_figures WHERE lower(trim(metric)) IN ({marks})", tuple(names)):
            if r["id"] not in seen_ids:
                seen_ids.add(r["id"])
                cand.append((dict(r), canonical))
    if not resolution:
        for r in lib.conn.execute("SELECT * FROM benchmark_figures WHERE lower(trim(metric)) = ?", (_lc(metric),)):
            seen_ids.add(r["id"])
            cand.append((dict(r), ""))
    # Expense families: "S&M expense", "Sales and marketing" and "S&M % of Revenue" are one
    # family, found by rules on the stored name and denominator (see expense_line_of).
    fam_keys = expense_family_request(metric)
    family_mode = False
    if fam_keys:
        for r in family_candidates(lib, fam_keys):
            if r["id"] not in seen_ids:
                seen_ids.add(r["id"])
                cand.append((r, ""))
                family_mode = True
        family_mode = family_mode or any(expense_line_of(r["metric"], r["denominator"], r["unit"]) for r, _ in cand)
        if family_mode:
            # A row found only by its name (not through the registry) must pass the family
            # rule too: a dollar-amount "S&M expense" is not a percent-of-revenue figure.
            cand = [(r, c) for r, c in cand
                    if c or expense_line_of(r["metric"], r["denominator"], r["unit"])]
    basis_ask = asked_basis(f"{metric} {basis_hint}", denominator) if family_mode else ""
    if not cand:
        for r in _fts_metric_rows(lib, metric):
            cand.append((r, targets[0] if len(targets) == 1 else ""))
        related = bool(cand)
        if related:
            notes.append(f"No figure is stored under '{metric}' or a registered name for it; these are "
                         "figures whose metric name contains the same words, which may measure something different.")

    illustrative_left_out = 0
    illustrative_ids: list[int] = []
    if not include_illustrative:
        kept_cand = [(r, c) for r, c in cand if not is_illustrative(r)]
        illustrative_ids = [r["id"] for r, _ in cand if is_illustrative(r)]
        illustrative_left_out = len(illustrative_ids)
        cand = kept_cand
        if illustrative_left_out:
            notes.append(f"{illustrative_left_out} illustrative rows left out (a worked example company, not survey data).")

    rows = [_row_out(r, landings, canon) for r, canon in cand]
    for row in rows:
        if related:
            _flag(row, "related_metric", "metric name only partly matches the request")
        if ambiguous:
            _flag(row, "different_definition", "the requested term names more than one metric")

    if year is not None and str(year).strip():
        want = _year_num(year)
        rows = [r for r in rows if _year_num(r["report_year"]) == want]
        if not rows:
            notes.append(f"No stored figure for that metric is from a {year} edition.")

    # Basis groups (family mode only): the revenue-basis and ARR-basis forms of one measure
    # are returned together, never merged or converted, the asked basis first. The match
    # flag is NOT changed (a row in the other group is kept as exact or adjacent_band);
    # `other_basis` and a plain-words gap say it.
    groups_out: list[dict] = []
    for r in rows:
        r["basis_group"] = ""          # only set for an expense-family row
        r["other_basis"] = False
        r["basis_rank"] = 0
    if family_mode:
        rank_map = dict(_BASIS_RANK_DEFAULT)
        if basis_ask:
            rank_map = {basis_ask: 0, ("arr" if basis_ask == "revenue" else "revenue"): 1, "unstated": 2}
        for r in rows:
            g = basis_group_of(r)
            r["basis_group"] = g
            r["basis_rank"] = rank_map[g]
            if basis_ask and g not in (basis_ask, "unstated"):
                r["other_basis"] = True
                r["gaps"].append(f"other basis: this figure is stated {BASIS_LABELS[g]}; you asked {BASIS_LABELS[basis_ask]}")
            elif g == "unstated":
                r["gaps"].append("basis not stated: the report does not say whether this is a percent of revenue or of ARR")
        present = sorted({r["basis_group"] for r in rows}, key=lambda g: rank_map[g])
        groups_out = [{"basis": g, "label": BASIS_LABELS[g], "asked": g == basis_ask,
                       "count": sum(1 for r in rows if r["basis_group"] == g)} for g in present]
        if basis_ask and any(g["basis"] not in (basis_ask, "unstated") for g in groups_out):
            other = [g for g in groups_out if g["basis"] not in (basis_ask, "unstated")][0]
            notes.append(f"Figures are grouped by basis. You asked {BASIS_LABELS[basis_ask]}; the other basis "
                         f"({other['label']}) is also on file and is returned flagged as other basis. "
                         "The two are never converted, merged or averaged.")
        elif len(groups_out) > 1:
            notes.append("Figures are grouped by basis (" + "; ".join(g["label"] for g in groups_out)
                         + "). The two are never converted, merged or averaged.")

    # Per-report refinement: keep rows that agree with a request, else flag.
    by_report: dict[str, list[dict]] = {}
    for r in rows:
        by_report.setdefault(r["report_slug"], []).append(r)
    asks = [("band_basis", basis, _basis_key, "different_basis", "basis"),
            ("denominator", None if family_mode else denominator, _lc, "different_basis", "denominator"),
            ("segment", segment, _lc, "different_segment", "segment"),
            ("statistic", statistic, _lc, "different_statistic", "statistic")]
    for slug, grp in by_report.items():
        for field_, want, norm, match, label in asks:
            if want is None or not str(want).strip():
                continue
            agree = [r for r in grp if norm(r[field_]) == norm(want)]
            if agree:
                grp[:] = agree
            else:
                for r in grp:
                    _flag(r, match, f"{label}: report prints '{r[field_] or 'not stated'}', you asked for '{want}'")
    rows = [r for grp in by_report.values() for r in grp]

    # Band: containing band, else the bands either side, per report and basis.
    unplaced = 0
    pb = parse_band(band) if band not in (None, "") else None
    if band not in (None, "") and pb is None:
        notes.append(f"The requested band '{band}' could not be read, so bands were not matched.")
    if pb is not None:
        groups: dict[tuple, list[dict]] = {}
        for r in rows:
            groups.setdefault((r["report_slug"], _basis_key(r["band_basis"])), []).append(r)
        kept: list[dict] = []
        for g in groups.values():
            placed = [(r, parse_band(r["band"])) for r in g]
            ok = [(r, b) for r, b in placed if b is not None and b["kind"] == pb["kind"]]
            unplaced += len(g) - len(ok)
            inside = [r for r, b in ok if b["lo"] <= pb["lo"] and pb["hi"] <= b["hi"]
                      or _lc(r["band"]) == _lc(band)]
            if inside:
                kept += inside
                continue
            overlap = [(r, b) for r, b in ok if b["lo"] <= pb["hi"] and pb["lo"] <= b["hi"]]
            if overlap:
                for r, _ in overlap:
                    _flag(r, "adjacent_band", f"band: report prints '{r['band']}', you asked for '{band}'")
                kept += [r for r, _ in overlap]
                continue
            below = [(r, b) for r, b in ok if b["hi"] <= pb["lo"]]
            above = [(r, b) for r, b in ok if b["lo"] >= pb["hi"]]
            pick = []
            if below:
                top = max(b["hi"] for _, b in below)
                pick += [r for r, b in below if b["hi"] == top]
            if above:
                bot = min(b["lo"] for _, b in above)
                pick += [r for r, b in above if b["lo"] == bot]
            for r in pick:
                _flag(r, "adjacent_band", f"band: report prints '{r['band']}', you asked for '{band}'")
            kept += pick
        rows = kept
        if unplaced:
            notes.append(f"{unplaced} figure(s) have a band that cannot be placed against a size "
                         "(for example 'All') and were left out of the band match.")

    # Editions.
    if not all_editions and not (year is not None and str(year).strip()):
        newest: dict[tuple, int] = {}
        for r in rows:
            f = _family(r)
            newest[f] = max(newest.get(f, -1), _year_num(r["report_year"]))
        older = sorted({(r["report"], r["report_year"]) for r in rows
                        if _year_num(r["report_year"]) < newest[_family(r)]})
        rows = [r for r in rows if _year_num(r["report_year"]) == newest[_family(r)]]
        if older:
            notes.append("Older editions exist and were left out: "
                         + "; ".join(f"{n} ({y})" for n, y in older)
                         + ". Ask for all editions to see change over time.")
        rep = {r["report_slug"]: r for r in lib.list_benchmark_reports()}
        for fam, y in newest.items():
            later = [x for x in rep.values() if _family(x) == fam and _year_num(x["report_year"]) > y]
            if later:
                notes.append(f"The newest {later[0]['report'] or fam[1]} on file ({later[0]['report_year']}) "
                             f"has no figure for this; the {y} edition is the newest that does.")

    rows.sort(key=lambda r: (r["basis_rank"], _MATCH_RANK[r["match"]],
                             _has_sample_size(r),   # a survey that states its sample before a guide that does not
                             -_year_num(r["report_year"]) if not all_editions else _year_num(r["report_year"]),
                             r["source"], r["report"], r["band_basis"],
                             (parse_band(r["band"]) or {"lo": 0})["lo"], r["statistic"]))
    total = len(rows)
    truncated = total > limit
    if truncated:
        notes.append(f"{total} figures match; showing the first {limit}. Narrow the request (band, segment, statistic, year).")
        if family_mode and len(groups_out) > 1:
            # Keep both bases in view: each group gets an equal share of the cap first.
            share = max(limit // len(groups_out), 1)
            by_g: dict[str, list[dict]] = {}
            for r in rows:
                by_g.setdefault(r["basis_group"], []).append(r)
            keep = {g: v[:share] for g, v in by_g.items()}
            spare = limit - sum(len(v) for v in keep.values())
            for g, v in by_g.items():
                extra = v[share:share + max(spare, 0)]
                keep[g] += extra
                spare -= len(extra)
            rows = [r for r in rows if r in keep[r["basis_group"]]]
        else:
            rows = rows[:limit]
    return {"rows": rows, "resolution": resolution, "notes": notes, "groups": groups_out, "total_matching": total,
            "returned": len(rows), "truncated": truncated, "unplaced_bands": unplaced,
            "illustrative_left_out": illustrative_left_out, "illustrative_ids": illustrative_ids}


# --- coverage ------------------------------------------------------------------

def coverage(lib, metric: str | None = None) -> dict:
    """What can be asked for: reports (with year, link status), bands, bases,
    segments, statistics and denominators present, optionally for one metric."""
    landings = resolve_landings(lib)
    where, args = "", ()
    resolution: list[dict] = []
    if metric:
        idx = _load_metric_index(lib)
        resolution = resolve_metric(lib, metric, idx)
        names = {_lc(metric)}
        for r in resolution:
            names |= _names_for(idx, r["canonical"])
        where = f"WHERE lower(trim(metric)) IN ({','.join('?' for _ in names)})"
        args = tuple(names)
    cur = lib.conn.execute(
        f"""SELECT report_slug, source, report, report_year, band_basis, band, segment,
                   denominator, statistic FROM benchmark_figures {where}""", args)
    reports: dict[str, dict] = {}
    sets = {k: set() for k in ("bands", "band_basis", "segments", "statistics", "denominators")}
    for r in cur:
        d = reports.setdefault(r["report_slug"], {
            "report_slug": r["report_slug"], "source": r["source"], "report": r["report"],
            "report_year": r["report_year"], "figures": 0,
            "link": landings.get(r["report_slug"], {}).get("url", "")})
        d["figures"] += 1
        sets["bands"].add(r["band"])
        sets["band_basis"].add(r["band_basis"])
        sets["segments"].add(r["segment"])
        sets["statistics"].add(r["statistic"])
        sets["denominators"].add(r["denominator"])
    return {"metric": metric, "resolution": resolution,
            "reports": sorted(reports.values(), key=lambda d: (d["source"], d["report_year"], d["report"])),
            **{k: sorted(v, key=lambda s: ((parse_band(s) or {"lo": 1e18})["lo"], s)) if k == "bands" else sorted(v)
               for k, v in sets.items()}}


# --- digests and metric cards -------------------------------------------------

def _article_out(a: dict) -> dict:
    return {"article_id": a["id"], "title": a.get("title") or "", "url": a.get("url") or "",
            "source": a.get("source") or "", "published_at": a.get("published_at") or "",
            "summary": a.get("summary") or "", "text": a.get("content") or ""}


def get_digest(lib, ref) -> dict | None:
    """The FULL stored text of a benchmark digest, by article id or report
    slug. Bypasses the Buddy prompt cut-off. None when nothing matches or the
    article is not a digest."""
    from .db import DIGEST_TAG
    s = str(ref).strip()
    art = None
    if s.isdigit():
        art = lib.get_article(int(s))
    if art is None and s:
        row = lib.conn.execute("SELECT article_id FROM benchmark_reports WHERE report_slug = ?", (s,)).fetchone()
        if row and row["article_id"]:
            art = lib.get_article(row["article_id"])
        if art is None:
            hits = [r["id"] for r in lib.conn.execute("SELECT id, url FROM articles WHERE url LIKE ?", (f"%digest={s}%",))
                    if _digest_slug_of(r["url"]) == s]
            if len(hits) == 1:
                art = lib.get_article(hits[0])
    if not art or DIGEST_TAG not in (art.get("tags") or []):
        return None
    return _article_out(art)


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", _lc(name)).strip("-")


def metric_card(lib, canonical: str, card_url: str = "") -> dict | None:
    """The combined metric card (workbook definition) for a canonical metric:
    the Archive article at the registry's card URL, else the digest article
    saas-metric-<name>."""
    art = lib.get_article_by_url(card_url) if card_url else None
    if art is None:
        slug = "saas-metric-" + _slugify(canonical)
        hits = [r["id"] for r in lib.conn.execute("SELECT id, url FROM articles WHERE url LIKE ?", (f"%digest={slug}%",))
                if _digest_slug_of(r["url"]) == slug]
        art = lib.get_article(hits[0]) if len(hits) == 1 else None
    return _article_out(art) if art else None


def metric_definition(lib, name: str) -> dict:
    """The workbook card and alias list for a metric. {"found": False} when
    the name is not in the registry. An alias used by several metrics returns
    every card, flagged ambiguous. `card` is None when the registry knows the
    metric but no card article is stored."""
    idx = _load_metric_index(lib)
    res = resolve_metric(lib, name, idx)
    if not res:
        return {"found": False, "name": name, "metrics": []}
    out = []
    for r in res:
        c = r["canonical"]
        out.append({"canonical_metric": c, "match": r["match"], "aliases": sorted(_names_for(idx, c) - {c.casefold()}),
                    "card_url": idx["card"].get(c, ""), "card": metric_card(lib, c, idx["card"].get(c, ""))})
    return {"found": True, "name": name, "ambiguous": len(res) > 1 and res[0]["match"] == "ambiguous_alias",
            "metrics": out, "precedence": "The workbook card governs; other sources define it differently, newest first."}


# --- cross-report summary ------------------------------------------------------

def summarize(rows: list[dict]) -> dict:
    """Median, min and max over rows, only when every row agrees on basis,
    denominator, band, segment, statistic and unit and has a numeric value.
    Otherwise refuses and says why. Any number returned is labelled
    "derived, not in report"."""
    rows = [r for r in rows]
    if len(rows) < 2:
        return {"refused": True, "reason": "fewer than two figures; nothing to summarize"}
    if any(r.get("value_num") is None for r in rows):
        return {"refused": True, "reason": "at least one figure has a value that is not a plain number"}
    for f in ("band_basis", "denominator", "band", "segment", "statistic", "unit", "canonical_metric"):
        if len({_lc(r.get(f)) for r in rows}) > 1:
            return {"refused": True, "reason": f"the figures differ in {f.replace('_', ' ')}, so a combined number would mix things that are not the same"}
    vals = [float(r["value_num"]) for r in rows]
    return {"refused": False, "label": "derived, not in report", "count": len(vals),
            "median": _stats.median(vals), "min": min(vals), "max": max(vals), "unit": rows[0].get("unit", "")}


# --- metric names found by full-text search (the trigger beyond the registry) --------

_NAME_STOP = set("""the and for are was were what whats how does did can could should would will with that this these
 those our your their from into than then have has had its not but any per about over under between against among
 as of at in on to by an a or is be we us it i you do""".split())
FTS_NAME_MAX = 4
FTS_NAME_POOL = 2000


def _stem(w: str) -> str:
    for suf in ("ies", "es", "s"):
        if len(w) > 4 and w.endswith(suf):
            return w[: -len(suf)] + ("y" if suf == "ies" else "")
    return w


# Spellings of the same expense line, folded to one short token on BOTH sides (the question
# and the stored name) so "sales and marketing", "S&M" and "S&M % of Revenue" meet.
_ABBR_PHRASES = (
    (re.compile(r"sales\s*(?:and|&)\s*marketing"), "sm"),
    (re.compile(r"general\s*(?:and|&)\s*administrative"), "ga"),
    (re.compile(r"research\s*(?:and|&)\s*development"), "rd"),
    (re.compile(r"(?<![a-z0-9])s\s*&\s*m(?![a-z0-9])"), "sm"),
    (re.compile(r"(?<![a-z0-9])g\s*&\s*a(?![a-z0-9])"), "ga"),
    (re.compile(r"(?<![a-z0-9])r\s*(?:&|and)\s*d(?![a-z0-9])"), "rd"),
)
_KEEP_SHORT = {"sm", "ga", "rd"}
# Headcount words that name the same stored metric (Total employees, ...). Deliberately
# small; "ARR per head" is a different metric and is not here.
_TOKEN_SYNONYMS = {"headcount": "employees", "ftes": "employees", "fte": "employees",
                   "staff": "employees", "workforce": "employees", "employees": "employees"}


def _sig_tokens(text: str) -> list[str]:
    t = _lc(text)
    for rx, rep in _ABBR_PHRASES:
        t = rx.sub(f" {rep} ", t)
    out = []
    for w in re.findall(r"[a-z0-9]+", t):
        if (len(w) <= 2 and w not in _KEEP_SHORT) or w in _NAME_STOP:
            continue
        out.append(_stem(_TOKEN_SYNONYMS.get(w, w)))
    return out


# Words that name a company's size or a denominator, not a metric. A stored name made only of
# these ("Annual Revenue ($M)", "Revenue in Year 1", "Annual R&D Spend") is a size field or
# a context phrase; a question that happens to contain the words is not asking for it.
_GENERIC_NAME_WORDS = {"annual", "revenue", "arr", "year", "spend", "spending", "total", "company",
                       "size", "stage", "growth", "cost", "costs", "amount"}
_PAREN = re.compile(r"\([^)]*\)")
_OPTIONAL_NAME_WORDS = {"total", "annual"}
# "S&M % of Revenue": the denominator is part of the name but a question asking for S&M
# spend at some revenue size rarely repeats it (and the caller masks the size phrase).
_DENOM_TAIL = re.compile(r"(?:\bas\s+an?\s+)?(?:%|percent(?:age)?)\s+of\s+(?:(?:total|net|annual)\s+)*(?:revenue|arr)\s*$")


def name_key(name: str) -> frozenset:
    """The significant words of a metric name with any parenthetical and symbols removed:
    "Gross Margin", "Gross Margin %" and "Gross Margin % (LTM)" share one key."""
    return frozenset(_sig_tokens(_PAREN.sub(" ", name or "")))


def _min_span(seq: list[str], need: list[str]) -> int:
    """Smallest window (in significant words) of `seq` that holds every word of `need`; 10**6 if none."""
    pos = {t: [i for i, x in enumerate(seq) if x == t] for t in need}
    if any(not v for v in pos.values()):
        return 10**6
    best = 10**6
    for start in sorted({i for v in pos.values() for i in v}):
        ends = []
        for t in need:
            nxt = [i for i in pos[t] if i >= start]
            if not nxt:
                break
            ends.append(min(nxt))
        else:
            best = min(best, max(ends) - start)
    return best


def detect_stored_metric_names(lib, text: str) -> list[str]:
    """Raw metric names in `benchmark_figures` that the question names, for
    metrics not in the registry. Deterministic, no model call.

    Candidates come from full-text search over the metric column using the
    question's words. A stored name counts only when:
    - its base name (a trailing "(full and part-time)" qualifier is ignored) has at least
      two significant words, and EVERY one of them appears in the question;
    - those words sit close together (within the name's length plus 3 significant words),
      so "annual" in one clause and "spend" in another do not make "Annual R&D Spend";
    - at least one of them is not a generic size or denominator word (annual, revenue, arr,
      year, spend, total, ...).
    The caller decides whether the question also carries benchmark context.
    Longest names first, at most FTS_NAME_MAX."""
    seq = _sig_tokens(text)
    toks = list(dict.fromkeys(seq))
    if len(toks) < 2:
        return []
    # The index tokenizes a stored "S&M % of Revenue" as s, m, revenue, so an abbreviation
    # token is searched as its letters too; the exact test below uses the folded tokens.
    pool_toks = []
    for t in toks:
        pool_toks += list(t) if t in _KEEP_SHORT else [t]
    pool_toks += [w for w in _TOKEN_SYNONYMS if _stem(_TOKEN_SYNONYMS[w]) in toks]
    q = "metric : (" + " OR ".join(f'"{t}"' for t in dict.fromkeys(pool_toks)) + ")"
    try:
        names = [r[0] for r in lib.conn.execute(
            """SELECT DISTINCT f.metric FROM benchmark_figures_fts x
               JOIN benchmark_figures f ON f.id = x.rowid
               WHERE benchmark_figures_fts MATCH ? LIMIT ?""", (q, FTS_NAME_POOL))]
    except Exception:
        return []
    have = set(toks)
    hits = []
    for n in names:
        nt = list(dict.fromkeys(_sig_tokens(_PAREN.sub(" ", n))))
        if len(nt) < 2:
            continue
        # Every word of the name must be in the question, except "total"/"annual", which
        # a question often leaves out ("headcount" for "Total employees").
        missing = [t for t in nt if t not in have]
        tail = _DENOM_TAIL.search(_lc(_PAREN.sub(" ", n)))
        optional = _OPTIONAL_NAME_WORDS | (set(_sig_tokens(tail.group(0))) if tail else set())
        if any(t not in optional for t in missing):
            continue
        # Leaving a qualifier out must not leave a generic word to carry the match
        # ("Annual R&D Spend" with "annual" dropped is "R&D spend", a size-field name).
        if missing and any(t in _GENERIC_NAME_WORDS or _stem(t) in _GENERIC_NAME_WORDS
                           for t in nt if t not in optional):
            continue
        if all(t in _GENERIC_NAME_WORDS or _stem(t) in _GENERIC_NAME_WORDS for t in nt if t in have):
            continue
        present = [t for t in nt if t in have]
        if _min_span(seq, present) > len(present) + 3:
            continue
        hits.append((len(nt), n))
    hits.sort(key=lambda h: (-h[0], h[1].casefold()))
    out: list[str] = []
    seen: set = set()
    for _, n in hits:
        # One name per metric: "Gross Margin", "Gross Margin %" and "Gross Margin % (LTM)" are
        # one, and must not crowd a second real metric out of the cap.
        k = name_key(n) or n.casefold()
        if k not in seen:
            seen.add(k)
            out.append(n)
    return out[:FTS_NAME_MAX]


# --- expense families and basis groups (opex by function, 2026-10) ---------------
#
# A function's spend ("S&M expense", "Sales and marketing", "S&M % of Revenue") is stored
# under many names, and the basis (revenue or ARR) is often only in the `denominator`
# column. Names are matched by RULES, never by a hand-filled list: about 8,600 distinct
# names are stored and a list would go stale with every load. The guard is a strict
# whitelist: after the function words are taken out, every remaining word of the name
# must be an expense or denominator word, so "Marketing Expenses per $ of New Logo ARR",
# "S&M efficiency" and "S&M headcount as % of employees" are a different measure and
# never join a family. A row also needs a percent form (a revenue or ARR denominator, a
# "%" in the name, or a percent unit), so a dollar amount is not a ratio family member.

FAMILY_LABELS = {"sm": "Sales and marketing expense", "rd": "Research and development expense",
                 "ga": "General and administrative expense", "cs": "Customer success expense",
                 "opex": "Total operating expense"}
FUNCTION_FAMILIES = ("sm", "rd", "ga", "cs")
BY_FUNCTION_LABEL = "Operating expenses by function"

_FAMILY_FOLD = (
    (re.compile(r"customer\s+(?:success|support)"), " cs "),
    (re.compile(r"total\s+operating\s+expenses?|operating\s+expenses?|opex|total\s+expenses?"), " opex "),
)
# Words that may stay in a family name. Anything else (per, new, logo, efficiency, magic,
# cac, payback, headcount, employees, mix, growth, ...) means a different measure.
_FAMILY_WORDS = {"expense", "expenses", "spend", "spending", "cost", "costs", "percent", "pct",
                 "percentage", "revenue", "arr", "gaap", "total", "net", "annual", "ending", "average",
                 "the", "of", "as", "a", "an", "in", "and", "is", "share"}
_REQUEST_EXTRA = {"by", "function", "functions", "functional", "department", "departments", "at",
                  "for", "size", "line", "lines", "operating", "show", "me", "what"}
_PCT_NAME = re.compile(r"%|percent|pct")
_REV_DENOM = re.compile(r"(?:%|percent(?:age)?)\s+of\s+(?:(?:total|net|annual|gaap|ending)\s+)*revenue|\bof\s+revenue\b|%\s*revenue")
_ARR_DENOM = re.compile(r"(?:%|percent(?:age)?)\s+of\s+(?:(?:total|net|annual|gaap|ending|average|ending-period)\s+)*arr\b|\bof\s+(?:ending\s+)?arr\b|annual\s+recurring\s+revenue")

BASIS_LABELS = {"revenue": "as a percent of revenue", "arr": "as a percent of ARR",
                "unstated": "basis not stated"}
_BASIS_RANK_DEFAULT = {"revenue": 0, "arr": 1, "unstated": 2}


def denominator_basis(*texts) -> str:
    """"revenue", "arr" or "" from a denominator (or a name that carries one). ARR is
    checked first: "annual recurring revenue" would otherwise read as revenue."""
    t = _lc(" ".join(str(x or "") for x in texts))
    if _ARR_DENOM.search(t):
        return "arr"
    if _REV_DENOM.search(t):
        return "revenue"
    return ""


def _family_tokens(text: str) -> tuple[list[str], list[str]]:
    """(function keys found, leftover words) for a name or request."""
    t = _PAREN.sub(" ", _lc(text))
    for rx, rep in _ABBR_PHRASES:
        t = rx.sub(f" {rep} ", t)
    for rx, rep in _FAMILY_FOLD:
        t = rx.sub(rep, t)
    words = re.findall(r"[a-z0-9]+", t)
    funcs = [w for w in words if w in _KEEP_SHORT or w in ("cs", "opex")]
    rest = [w for w in words if w not in _KEEP_SHORT and w not in ("cs", "opex")]
    return funcs, rest


def expense_line_of(name: str, denominator: str = "", unit: str = "") -> str:
    """The family key ("sm", "rd", "ga", "cs", "opex") of a stored row, or "" when the
    row is not a percent-form expense line. One function key per name."""
    funcs, rest = _family_tokens(name)
    if len(set(funcs)) != 1:
        return ""
    if any(w not in _FAMILY_WORDS for w in rest):
        return ""
    has_pct = bool(_PCT_NAME.search(_lc(name)) or denominator_basis(denominator, name)
                   or _PCT_NAME.search(_lc(unit)))
    if not has_pct:
        return ""
    # A denominator that is stated and is not revenue or ARR (dollars, employees, new logo
    # ARR) is another measure even when the name looks right.
    d = _lc(denominator)
    if d and not denominator_basis(d) and not _PCT_NAME.search(d):
        return ""
    if d and denominator_basis(d) == "" and _PCT_NAME.search(d):
        return ""      # "percent of employees"
    return funcs[0]


def expense_family_request(text: str) -> list[str]:
    """Family keys a metric phrase asks for, or [] when it is not a family request. Every
    word must be a function, expense or denominator word, so "S&M efficiency" is not one."""
    funcs, rest = _family_tokens(text)
    if not funcs:
        return []
    if any(w not in _FAMILY_WORDS and w not in _REQUEST_EXTRA for w in rest):
        return []
    low = _lc(text)
    if "opex" in funcs and len(set(funcs)) == 1 and re.search(r"\bby\s+(?:function|department)|functional", low):
        return list(FUNCTION_FAMILIES)
    if set(funcs) == {"opex"} and re.search(r"\bfunction", low):
        return list(FUNCTION_FAMILIES)
    seen: list[str] = []
    for f in funcs:
        if f not in seen:
            seen.append(f)
    return seen


def asked_basis(text: str, denominator: str | None = None) -> str:
    """The basis a request names: "revenue", "arr" or "" (neither, or both)."""
    d = denominator_basis(denominator)
    if d:
        return d
    t = _lc(text)
    arr, rev = bool(_ARR_DENOM.search(t)), bool(_REV_DENOM.search(t))
    if arr and not rev:
        return "arr"
    if rev and not arr:
        return "revenue"
    return ""


def detect_expense_families(text: str) -> list[str]:
    """Metric terms to look up for a question that asks about a function's spend, by rules.
    "Operating expenses by function" is one term; named functions are one term each. A
    question must carry an expense or percent cue, not just the letters S&M."""
    low = _lc(text)
    if not re.search(r"expense|spend|cost|opex|%|percent|budget|headcount|org", low):
        pass
    if re.search(r"\b(?:opex|operating\s+expenses?|expenses?|spend|costs?)\s+by\s+(?:function|department)", low) \
            or re.search(r"\bfunctional\s+(?:spend|opex|expenses?)", low):
        return [BY_FUNCTION_LABEL]
    funcs, _ = _family_tokens(text)
    if not funcs or not re.search(r"expense|spend|cost|opex|%|percent|budget|ratio|of\s+(?:revenue|arr)", low):
        return []
    out: list[str] = []
    for f in funcs:
        if f in FAMILY_LABELS and f != "opex" and FAMILY_LABELS[f] not in out:
            out.append(FAMILY_LABELS[f])
    return out


def basis_group_of(row: dict) -> str:
    return denominator_basis(row.get("denominator"), row.get("metric")) or "unstated"


def family_candidates(lib, keys: list[str]) -> list[dict]:
    """Every stored row whose name and denominator put it in one of the families."""
    keyset = set(keys)
    want = {"sm", "rd", "ga", "cs", "opex"} & keyset
    if not want:
        return []
    pairs = lib.conn.execute("SELECT DISTINCT metric, denominator, unit FROM benchmark_figures").fetchall()
    good = [(p[0], p[1], p[2]) for p in pairs if expense_line_of(p[0], p[1], p[2]) in want]
    out: list[dict] = []
    for m, d, u in good:
        for r in lib.conn.execute(
                "SELECT * FROM benchmark_figures WHERE metric = ? AND denominator = ? AND unit = ?", (m, d, u)):
            out.append(dict(r))
    return out
