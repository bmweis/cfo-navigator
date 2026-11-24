#!/usr/bin/env python3
"""Read-only audit of the benchmark figures: the counts the MCP cannot return.

Answers, in files you can paste or upload: how many rows, which raw metric strings
exist and whether each resolves to a canonical metric, which denominators, statistics,
units, `read_from` values and bands are stored, which bands the parser cannot place,
which reports hold duplicate or conflicting cells (and which cells, in conflicts.csv), and which editions exist per report
family. Run it after each ingest.

It uses the app's own functions (`linklib.figures`: resolve_metric, parse_band,
_family, denominator_basis, is_illustrative, dedupe_cells), so what it reports is what
FP&A Buddy would do. Rows from worked examples (is_illustrative) are left out of every
count; how many there are is reported in summary.txt.

Strictly read-only: the database is opened with SQLite `mode=ro` plus
`PRAGMA query_only`, only benchmark_* and metric_* tables are read, and the only files
written are the output files in --out-dir (which must not exist or be empty).
`--db` must be an absolute path (a relative one silently resolves against whatever
directory the shell is in).

Usage (production, over `railway ssh`):
    python -m scripts.audit_benchmark_alignment --db /data/library.db --out-dir /tmp/alignment-audit

Files written: metrics.csv, canonical.csv, denominators.csv, statistics.csv,
read_from.csv, units.csv, band_basis.csv, bands.csv, duplicates.csv, conflicts.csv (one row
per conflicting cell: the cell key, then the figure ids, values, pages and read_from of the rows
that disagree), editions.csv, summary.txt (the same text is printed to stdout).
"""
from __future__ import annotations

import argparse
import csv
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from linklib import figures as fg  # noqa: E402

TABLES = ("benchmark_reports", "benchmark_figures", "metric_registry", "metric_aliases", "metric_name_map")
TOP_DENOMINATOR_METRICS = 40


class _Shim:
    """linklib.figures only reaches for `.conn`; this keeps the script off the Library
    class, whose constructor can write (migrations, seeding)."""

    def __init__(self, conn):
        self.conn = conn


def _die(msg: str) -> int:
    print(msg, file=sys.stderr)
    return 2


def open_readonly(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{quote(path)}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _write_csv(out_dir: str, name: str, header: list[str], rows) -> None:
    with open(os.path.join(out_dir, name), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def _parsed_text(band) -> str:
    p = fg.parse_band(band)
    if p is None:
        return ""
    return ", ".join(f"{k}={'inf' if v == float('inf') else (int(v) if isinstance(v, float) and v == int(v) else v)}"
                     for k, v in p.items())


def _join(vals, limit=None) -> str:
    s = ";".join(sorted(v for v in vals if v))
    return s[:limit] if limit else s


def build(conn: sqlite3.Connection, out_dir: str) -> str:
    shim = _Shim(conn)
    idx = fg._load_metric_index(shim)
    all_rows = [dict(r) for r in conn.execute("SELECT * FROM benchmark_figures ORDER BY id")]
    for r in all_rows:
        r["figure_key"] = r["id"]
        r["gaps"] = []
    illustrative = [r for r in all_rows if fg.is_illustrative(r)]
    rows = [r for r in all_rows if not fg.is_illustrative(r)]

    # --- metrics.csv: one row per raw metric string -------------------------------------
    by_metric: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_metric[r["metric"]].append(r)
    resolved: dict[str, list[dict]] = {m: fg.resolve_metric(shim, m, idx) for m in by_metric}
    metric_out = []
    for m, rs in by_metric.items():
        res = resolved[m]
        if not res:
            to, via = "", "none"
        elif len(res) > 1:
            to, via = "ambiguous:" + ";".join(sorted(x["canonical"] for x in res)), "ambiguous_alias"
        else:
            to, via = res[0]["canonical"], res[0]["match"]
        metric_out.append((m, len(rs), len({x["report_slug"] for x in rs}), to, via,
                           min(x["report_slug"] for x in rs)))
    metric_out.sort(key=lambda t: (-t[1], t[0]))
    _write_csv(out_dir, "metrics.csv",
               ["metric", "rows", "reports", "resolves_to", "resolved_via", "sample_report_slug"], metric_out)

    # --- canonical.csv: one row per registry metric (an ambiguous string counts under every owner)
    owners: dict[str, list[str]] = defaultdict(list)       # canonical -> raw strings
    for m, res in resolved.items():
        for x in res:
            owners[x["canonical"]].append(m)
    canon_out = []
    for c in sorted(idx["canon"].values()):
        raws = owners.get(c, [])
        rs = [r for m in raws for r in by_metric[m]]
        n_alias = sum(1 for owners_ in idx["claims"].values() if c in owners_)
        canon_out.append((c, len(raws), len({r["report_slug"] for r in rs}), len(rs), n_alias))
    _write_csv(out_dir, "canonical.csv", ["canonical_metric", "raw_strings", "reports", "rows", "aliases"], canon_out)

    # --- denominators.csv: the 40 canonical metrics with the most rows -------------------
    top = sorted(canon_out, key=lambda t: (-t[3], t[0]))[:TOP_DENOMINATOR_METRICS]
    den_out = []
    for c, _raws, _reps, nrows, _al in top:
        if not nrows:
            continue
        groups: dict[str, list[dict]] = defaultdict(list)
        for m in owners[c]:
            for r in by_metric[m]:
                groups[r["denominator"]].append(r)
        for d, rs in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            den_out.append((c, d, len(rs), len({x["report_slug"] for x in rs}), fg.denominator_basis(d)))
    den_out.sort(key=lambda t: (t[0], -t[2]))
    _write_csv(out_dir, "denominators.csv", ["canonical_metric", "denominator", "rows", "reports", "basis"], den_out)

    # --- plain value counts ----------------------------------------------------------------
    for col, fname in (("statistic", "statistics.csv"), ("read_from", "read_from.csv"),
                       ("unit", "units.csv"), ("band_basis", "band_basis.csv")):
        cnt = Counter(r[col] for r in rows)
        _write_csv(out_dir, fname, ["value", "rows"], sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0])))

    # --- bands.csv ---------------------------------------------------------------------------
    by_band: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_band[r["band"]].append(r)
    band_out = []
    for b, rs in by_band.items():
        placed = fg.parse_band(b) is not None
        band_out.append((b, len(rs), len({x["report_slug"] for x in rs}),
                         _join({x["source"] for x in rs}, 120), _parsed_text(b), "yes" if placed else "no",
                         _join({x["band_basis"] for x in rs})))
    band_out.sort(key=lambda t: (-t[1], t[0]))
    _write_csv(out_dir, "bands.csv",
               ["band", "rows", "reports", "sources", "parsed", "placed", "band_basis_values"], band_out)

    # --- duplicates.csv: find_figures collapses with figures.dedupe_cells, whose cell key is
    # (report, data_period, band_basis, band, segment, metric, denominator, statistic, unit);
    # the same cell + the same value is an exact duplicate, the same cell + a different value
    # is a conflict. The function itself is called, so the key cannot drift.
    by_report: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_report[r["report_slug"]].append(r)
    dup_out = []
    for slug in sorted(by_report):
        rs = by_report[slug]
        _kept, removed, conflicts = fg.dedupe_cells([dict(r, gaps=[]) for r in rs])
        dup_out.append((slug, len(rs), removed, conflicts))
    _write_csv(out_dir, "duplicates.csv",
               ["report_slug", "rows", "exact_duplicate_cells", "conflicting_cells"], dup_out)

    # --- conflicts.csv: every conflicting cell (same cell key, different values), so each can be
    # checked against the report. One row per cell; figure_ids, values, pages and read_from are
    # ";"-joined in the same order (lowest figure id first). Same cell key as dedupe_cells.
    conf_out = []
    for slug in sorted(by_report):
        for grp in fg.cell_conflicts([dict(r, gaps=[]) for r in by_report[slug]]):
            grp = sorted(grp, key=lambda x: x["id"])
            f = grp[0]
            conf_out.append((slug, f["data_period"], f["band_basis"], f["band"], f["segment"], f["metric"],
                             f["denominator"], f["statistic"], f["unit"], len(grp),
                             ";".join(str(g["id"]) for g in grp), ";".join(str(g["value"]) for g in grp),
                             ";".join(str(g["page"]) for g in grp), ";".join(str(g["read_from"]) for g in grp)))
    _write_csv(out_dir, "conflicts.csv",
               ["report_slug", "data_period", "band_basis", "band", "segment", "metric", "denominator",
                "statistic", "unit", "differing_rows", "figure_ids", "values", "pages", "read_from"], conf_out)

    # --- editions.csv: effective names (an admin override wins, as in the app) --------------
    ed_out = []
    for rep in conn.execute("SELECT * FROM benchmark_reports"):
        rep = dict(rep)
        src = rep.get("source_override") or rep["source"]
        name = rep.get("report_override") or rep["report"]
        year = rep.get("year_override") or rep["report_year"]
        fam = fg._family({"source": src, "report": name, "series_override": rep.get("series_override") or ""})
        ed_out.append((rep["report_slug"], src, name, year, fg.family_label(fam),
                       len(by_report.get(rep["report_slug"], [])), rep["loaded_at"]))
    ed_out.sort(key=lambda t: (t[4], t[3], t[0]))
    _write_csv(out_dir, "editions.csv",
               ["report_slug", "source", "report", "report_year", "family", "rows", "loaded_at"], ed_out)

    # --- summary.txt ------------------------------------------------------------------------
    L = ["Benchmark alignment audit (read-only)"]
    L.append("Table rows: " + ", ".join(
        f"{t}={conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]}" for t in TABLES))
    L.append(f"Figure rows counted: {len(rows)} (illustrative rows left out: {len(illustrative)})")
    distinct = ", ".join(f"{c}={len({r[c] for r in rows})}" for c in
                         ("metric", "denominator", "statistic", "unit", "read_from", "band", "band_basis",
                          "segment", "data_period"))
    L.append("Distinct values: " + distinct)
    unresolved = [t for t in metric_out if t[4] == "none"]
    ambiguous = [t for t in metric_out if t[4] == "ambiguous_alias"]
    L.append(f"Metric strings: {len(metric_out)}; do not resolve: {len(unresolved)} "
             f"({sum(t[1] for t in unresolved)} rows); ambiguous: {len(ambiguous)} ({sum(t[1] for t in ambiguous)} rows)")
    unplaced = [t for t in band_out if t[5] == "no"]
    L.append(f"Bands: {len(band_out)}; unplaced: {len(unplaced)} ({sum(t[1] for t in unplaced)} rows)")
    L.append(f"Reports: {len(ed_out)}; duplicate rows: {sum(t[2] for t in dup_out)}; "
             f"conflicting cells: {sum(t[3] for t in dup_out)}")
    L.append("Ten largest unresolved metric strings (rows):")
    for t in unresolved[:10]:
        L.append(f"  {t[1]:>6}  {t[0]}")
    if not unresolved:
        L.append("  none")
    L.append("Files: metrics, canonical, denominators, statistics, read_from, units, band_basis, bands, "
             "duplicates, conflicts, editions (.csv)")
    text = "\n".join(L) + "\n"
    with open(os.path.join(out_dir, "summary.txt"), "w", encoding="utf-8") as f:
        f.write(text)
    return text


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", required=True, help="absolute path to library.db, e.g. /data/library.db")
    ap.add_argument("--out-dir", required=True, help="directory for the output files (must not exist or be empty)")
    args = ap.parse_args(argv)

    if not os.path.isabs(args.db):
        return _die(f"--db must be an absolute path (got {args.db!r}); use /data/library.db in production.")
    if not os.path.isfile(args.db):
        return _die(f"Database file not found at {args.db}. Nothing was created.")
    out_dir = os.path.abspath(args.out_dir)
    if os.path.exists(out_dir) and (not os.path.isdir(out_dir) or os.listdir(out_dir)):
        return _die(f"Output directory {out_dir} is not empty (or not a directory). Name a new or empty one.")

    conn = open_readonly(args.db)
    try:
        have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing = [t for t in TABLES if t not in have]
        if missing:
            return _die("This database has no benchmark figure tables (missing: " + ", ".join(missing) + ").")
        os.makedirs(out_dir, exist_ok=True)
        print(build(conn, out_dir), end="")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
