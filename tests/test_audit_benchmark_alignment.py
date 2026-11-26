"""The benchmark alignment audit script: read-only, refuses unsafe arguments, and writes
the output files. All data is invented (tests/figures_fixtures.py)."""
import csv
import hashlib
import os
import sqlite3

import pytest

from linklib.db import Library
from scripts import audit_benchmark_alignment as aud
from tests import figures_fixtures as fx


def _build(tmp_path):
    path = str(tmp_path / "t.db")
    lib = Library(path)
    fx.seed_registry(lib)
    fx.load_report(lib, "eb-2099", [
        fx.frow(metric="NRR", value="104"),                                   # alias
        fx.frow(metric="Net revenue retention", value="104", band="$30M-$50M"),
        fx.frow(metric="Mystery ratio", value="3", denominator="% of revenue"),  # unresolved
        fx.frow(metric="net new mrr", value="5"),                              # ambiguous alias
        fx.frow(metric="Burn multiple", value="1.4", unit="x", band="All"),    # band the parser cannot place
        fx.frow(metric="Burn multiple", value="1.4", unit="x", band="All"),    # exact duplicate
        fx.frow(metric="Cash Conversion Score", value="201", band="$10M-$20M"),
        fx.frow(metric="Cash Conversion Score", value="200", band="$10M-$20M"),  # conflicting value
        fx.frow(metric="Burn multiple", value="9", segment="Illustrative example company"),  # illustrative
    ])
    fx.load_report(lib, "eb-2098", [fx.frow(report="Example Benchmarks 2098", year="2098", metric="NRR")])
    lib.close()
    return path


def _run(tmp_path, capsys=None):
    path = _build(tmp_path)
    out = tmp_path / "out"
    assert aud.main(["--db", path, "--out-dir", str(out)]) == 0
    return path, out


def _csv(out, name):
    with open(out / name, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# --- read-only proof and refusals ---------------------------------------------------

def _fingerprint(path):
    st = os.stat(path)
    return hashlib.sha256(open(path, "rb").read()).hexdigest(), st.st_mtime_ns


def test_script_does_not_change_the_database_or_its_folder(tmp_path):
    """Production condition: the running app holds the WAL-mode database open, so its -wal and
    -shm files already exist. (With no other connection open, SQLite itself leaves those two
    files behind on any read-only open of a WAL database; the script cannot prevent that.)"""
    path = _build(tmp_path)
    app = Library(path)                      # stands in for the running app
    try:
        before, listing = _fingerprint(path), sorted(os.listdir(tmp_path))
        assert any(n.endswith("-wal") for n in listing)
        assert aud.main(["--db", path, "--out-dir", str(tmp_path / "out")]) == 0
        assert _fingerprint(path) == before
        assert sorted(os.listdir(tmp_path)) == sorted(listing + ["out"])
    finally:
        app.close()


def test_connection_rejects_writes(tmp_path):
    conn = aud.open_readonly(_build(tmp_path))
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("CREATE TABLE x(a)")
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("UPDATE benchmark_figures SET value='0'")


def test_refuses_non_empty_out_dir(tmp_path, capsys):
    path = _build(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    (out / "keep.txt").write_text("x")
    assert aud.main(["--db", path, "--out-dir", str(out)]) != 0
    assert "not empty" in capsys.readouterr().err
    assert os.listdir(out) == ["keep.txt"]


def test_accepts_an_existing_empty_out_dir(tmp_path):
    path = _build(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    assert aud.main(["--db", path, "--out-dir", str(out)]) == 0


def test_refuses_relative_db(tmp_path, capsys, monkeypatch):
    _build(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert aud.main(["--db", "t.db", "--out-dir", str(tmp_path / "out")]) != 0
    assert "absolute" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_missing_db_exits_nonzero_and_is_not_created(tmp_path, capsys):
    missing = tmp_path / "nope.db"
    assert aud.main(["--db", str(missing), "--out-dir", str(tmp_path / "out")]) != 0
    assert "not found" in capsys.readouterr().err
    assert not missing.exists() and not (tmp_path / "out").exists()


def test_db_without_figure_tables_is_refused_plainly(tmp_path, capsys):
    p = tmp_path / "e.db"
    sqlite3.connect(p).close()
    assert aud.main(["--db", str(p), "--out-dir", str(tmp_path / "out")]) != 0
    assert "benchmark" in capsys.readouterr().err


# --- outputs -------------------------------------------------------------------------

def test_all_files_written_with_headers(tmp_path):
    _, out = _run(tmp_path)
    heads = {
        "metrics.csv": ["metric", "rows", "reports", "resolves_to", "resolved_via", "sample_report_slug"],
        "canonical.csv": ["canonical_metric", "raw_strings", "reports", "rows", "aliases"],
        "denominators.csv": ["canonical_metric", "denominator", "rows", "reports", "basis", "group", "tier"],
        "statistics.csv": ["value", "rows"], "read_from.csv": ["value", "rows"],
        "units.csv": ["value", "rows"], "band_basis.csv": ["value", "rows"],
        "bands.csv": ["band", "rows", "reports", "sources", "parsed", "placed", "band_basis_values"],
        "duplicates.csv": ["report_slug", "rows", "exact_duplicate_cells", "conflicting_cells"],
        "conflicts.csv": ["report_slug", "data_period", "band_basis", "band", "segment", "metric", "denominator",
                          "statistic", "unit", "differing_rows", "figure_ids", "values", "pages", "read_from"],
        "editions.csv": ["report_slug", "source", "report", "report_year", "family", "rows", "loaded_at"],
    }
    for name, cols in heads.items():
        with open(out / name, newline="", encoding="utf-8") as f:
            assert next(csv.reader(f)) == cols, name
    assert (out / "summary.txt").read_text().strip()


def test_metrics_resolution_and_illustrative_exclusion(tmp_path):
    _, out = _run(tmp_path)
    m = {r["metric"]: r for r in _csv(out, "metrics.csv")}
    assert m["NRR"]["resolves_to"] == "Net revenue retention" and m["NRR"]["resolved_via"] == "alias"
    assert m["NRR"]["rows"] == "2" and m["NRR"]["reports"] == "2" and m["NRR"]["sample_report_slug"] == "eb-2098"
    assert m["Net revenue retention"]["resolved_via"] == "canonical"
    assert m["Mystery ratio"]["resolves_to"] == "" and m["Mystery ratio"]["resolved_via"] == "none"
    amb = m["net new mrr"]
    assert amb["resolves_to"] == "ambiguous:MRR;Net New ARR" and amb["resolved_via"] == "ambiguous_alias"
    assert m["Burn multiple"]["rows"] == "2"          # the illustrative third row is not counted
    assert [int(r["rows"]) for r in _csv(out, "metrics.csv")] == sorted(
        (int(r["rows"]) for r in _csv(out, "metrics.csv")), reverse=True)


def test_canonical_includes_zero_row_metrics(tmp_path):
    _, out = _run(tmp_path)
    c = {r["canonical_metric"]: r for r in _csv(out, "canonical.csv")}
    assert c["Capital Consumption Ratio"]["rows"] == "0" and c["Capital Consumption Ratio"]["raw_strings"] == "0"
    assert c["Net revenue retention"]["rows"] == "3" and c["Net revenue retention"]["raw_strings"] == "2"
    assert int(c["Net revenue retention"]["aliases"]) >= 2


def test_denominators_basis(tmp_path):
    _, out = _run(tmp_path)
    d = _csv(out, "denominators.csv")
    mystery = [r for r in d if r["denominator"] == "% of revenue"]
    assert not mystery                                  # unresolved metrics have no canonical group
    assert all(r["canonical_metric"] for r in d)


def test_bands_placement(tmp_path):
    _, out = _run(tmp_path)
    b = {r["band"]: r for r in _csv(out, "bands.csv")}
    assert b["All"]["placed"] == "no" and b["All"]["parsed"] == ""
    assert b["$10M-$20M"]["placed"] == "yes" and "10000000" in b["$10M-$20M"]["parsed"]
    assert b["All"]["sources"] == "Example Benchmarks" and b["All"]["band_basis_values"] == "ARR"


def test_duplicates_and_conflicts(tmp_path):
    _, out = _run(tmp_path)
    d = {r["report_slug"]: r for r in _csv(out, "duplicates.csv")}
    assert d["eb-2099"]["exact_duplicate_cells"] == "1" and d["eb-2099"]["conflicting_cells"] == "1"
    assert d["eb-2098"]["exact_duplicate_cells"] == "0"


def test_editions_sorted_by_family_then_year(tmp_path):
    _, out = _run(tmp_path)
    e = _csv(out, "editions.csv")
    assert [r["report_slug"] for r in e] == ["eb-2098", "eb-2099"]
    assert e[0]["family"] == e[1]["family"] and e[0]["rows"] == "1" and e[1]["rows"] == "8"


def test_summary_is_short_and_names_the_key_counts(tmp_path, capsys):
    _, out = _run(tmp_path)
    text = (out / "summary.txt").read_text()
    assert len(text.splitlines()) < 60
    assert "illustrative" in text.lower() and "Mystery ratio" in text and "ambiguous" in text.lower()
    assert capsys.readouterr().out.strip() == text.strip()


def test_conflicts_csv_lists_each_conflicting_cell_only(tmp_path):
    """One exact duplicate (Burn multiple, band All) and one conflict (Cash Conversion Score 201 vs 200):
    only the conflict is in conflicts.csv, with the cell key and each differing row's id, value, page and read_from."""
    _, out = _run(tmp_path)
    rows = _csv(out, "conflicts.csv")
    assert len(rows) == 1
    r = rows[0]
    assert r["report_slug"] == "eb-2099" and r["metric"] == "Cash Conversion Score"
    assert r["band"] == "$10M-$20M" and r["statistic"] == "median" and r["unit"] == "percent"
    assert r["differing_rows"] == "2" and r["values"] == "201;200" and r["pages"] == "14;14" and r["read_from"] == "table;table"
    ids = [int(x) for x in r["figure_ids"].split(";")]
    assert ids == sorted(ids) and len(ids) == 2
    assert "conflicts" in (out / "summary.txt").read_text()


def test_conflicts_use_the_same_cell_key_as_dedupe_cells(tmp_path):
    """The count in duplicates.csv and the rows in conflicts.csv come from the one cell definition."""
    _, out = _run(tmp_path)
    d = {r["report_slug"]: int(r["conflicting_cells"]) for r in _csv(out, "duplicates.csv")}
    per_slug = {}
    for r in _csv(out, "conflicts.csv"):
        per_slug[r["report_slug"]] = per_slug.get(r["report_slug"], 0) + 1
    assert {k: v for k, v in d.items() if v} == per_slug


def test_editions_csv_uses_the_series_override_family(tmp_path):
    path = _build(tmp_path)
    conn = sqlite3.connect(path)
    conn.execute("UPDATE benchmark_reports SET series_override='Example Benchmarks Series' WHERE report_slug IN ('eb-2099','eb-2098')")
    conn.commit()
    conn.close()
    out = tmp_path / "out"
    assert aud.main(["--db", path, "--out-dir", str(out)]) == 0
    fams = {r["report_slug"]: r["family"] for r in _csv(out, "editions.csv")}
    assert fams["eb-2099"] == fams["eb-2098"] == "series: example benchmarks series", fams
