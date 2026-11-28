"""Standard view, SV1: four optional name-map columns, the TSV export, standard_view and the `std`
stamp, and the audit's standard_view.csv. Invented data only (see tests/figures_fixtures.py)."""
import copy
import csv
import importlib

import pytest

from linklib import figures as fg
from linklib.db import Library
from linklib.standard_view import normalize_statistic, standard_view
from scripts import audit_benchmark_alignment as aud
from tests import figures_fixtures as fx

H3 = "raw_name\tcanonical_metric\tstatus\n"
H7 = "raw_name\tcanonical_metric\tstatus\tvariant\tform\tstatistic_std\tcohort\n"


@pytest.fixture
def lib(tmp_path):
    lb = Library(str(tmp_path / "t.db"))
    fx.seed_registry(lb)
    yield lb
    lb.close()


def _parse(text):
    return fg.parse_name_map_tsv(text)


def _plan(lib, text):
    rows, errors = _parse(text)
    assert not errors, errors
    return fg.plan_name_map(lib, rows)


def _load(lib, text):
    plan = _plan(lib, text)
    assert not plan["errors"], plan["errors"]
    lib.apply_name_map(plan["writes"])
    return plan


def _stored(lib, raw):
    r = lib.conn.execute("SELECT * FROM metric_name_map WHERE raw_name_lower = ?", (raw,)).fetchone()
    return dict(r) if r else None


# --- the four columns ------------------------------------------------------------------------

def test_four_columns_are_accepted_and_stored(lib):
    plan = _load(lib, H7 + "best nrr\tNet revenue retention\tsafe\tnew\tRetention\tTop_Quartile\tPerformance_Tier\n")
    assert plan["new"] == 1 and len(plan["writes"][0]) == 7
    r = _stored(lib, "best nrr")
    assert (r["variant"], r["form"], r["statistic_std"], r["cohort"]) == ("new", "retention", "top_quartile", "performance_tier")


def test_columns_may_come_in_any_order_and_a_subset(lib):
    _load(lib, "raw_name\tcanonical_metric\tstatus\tcohort\tvariant\nx nrr\tNet revenue retention\tsafe\tpublic\tmonthly\n")
    r = _stored(lib, "x nrr")
    assert (r["cohort"], r["variant"], r["form"], r["statistic_std"]) == ("public", "monthly", None, None)


def test_a_row_may_leave_extras_blank_and_a_stripped_trailing_cell_is_tolerated(lib):
    _load(lib, H7 + "a\tNet revenue retention\tsafe\n" + "b\tNet revenue retention\tsafe\tmrr\n")
    assert _stored(lib, "a")["variant"] is None and _stored(lib, "b")["variant"] == "mrr"


@pytest.mark.parametrize("line,needle", [
    ("x\tNet revenue retention\tsafe\t\tgrowth\t\t", "form 'growth' is not one of retention, churn"),
    ("x\tNet revenue retention\tsafe\t\t\tbest\t", "statistic_std 'best' is not one of"),
    ("x\tNet revenue retention\tsafe\t\t\t\tworld", "cohort 'world' is not one of"),
    ("x\tNet revenue retention\tsafe\t" + "v" * 41 + "\t\t\t", "limit is 40"),
    ("x\t\tkeep_separate\tmrr\t\t\t", "keep_separate, so variant must be empty"),
    ("x\t\tdecision\t\tchurn\t\t", "decision, so form must be empty"),
    ("x\t\tdecision\t\t\tmedian\t", "decision, so statistic_std must be empty"),
    ("x\t\tkeep_separate\t\t\t\tpublic", "keep_separate, so cohort must be empty"),
])
def test_each_refusal_names_its_reason(line, needle):
    rows, errors = _parse(H7 + line + "\n")
    assert rows == [] and any(needle in e for e in errors), errors


def test_a_variant_of_exactly_40_characters_is_accepted():
    rows, errors = _parse(H7 + "x\tNet revenue retention\tsafe\t" + "v" * 40 + "\t\t\t\n")
    assert not errors and rows[0]["extras"]["variant"] == "v" * 40


@pytest.mark.parametrize("header", [
    "raw_name\tcanonical_metric\tstatus\tcolour\n",
    "raw_name\tcanonical_metric\tstatus\tform\tform\n",
    "canonical_metric\traw_name\tstatus\n",
])
def test_a_bad_header_is_refused(header):
    assert _parse(header + "x\t\tdecision\n")[1]


def test_a_repeated_raw_name_with_different_extras_is_an_error():
    _, errors = _parse(H7 + "x\tNet revenue retention\tsafe\tmrr\t\t\t\nX\tNet revenue retention\tsafe\tnew\t\t\t\n")
    assert errors and "twice" in errors[0]


# --- backward compatible three-column files ---------------------------------------------------

def test_three_column_file_plans_and_loads_exactly_as_before(lib):
    plan = _plan(lib, H3 + "Odd NRR Label\tNET REVENUE RETENTION\tsafe\n")
    assert plan["writes"] == [("odd nrr label", "Net revenue retention", "safe")]
    lib.apply_name_map(plan["writes"])
    again = _plan(lib, H3 + "Odd NRR Label\tNET REVENUE RETENTION\tsafe\n")
    assert (again["new"], again["changed"], again["unchanged"], again["writes"]) == (0, 0, 1, [])


def test_three_column_file_leaves_stored_extras_alone(lib):
    _load(lib, H7 + "best nrr\tNet revenue retention\tsafe\tnew\tretention\ttop_quartile\tpublic\n")
    plan = _plan(lib, H3 + "best nrr\tNet revenue retention\tsafe\n")
    assert (plan["changed"], plan["unchanged"], plan["writes"]) == (0, 1, [])
    r = _stored(lib, "best nrr")
    assert (r["variant"], r["form"], r["statistic_std"], r["cohort"]) == ("new", "retention", "top_quartile", "public")


# --- a column-only change counts as changed -----------------------------------------------------

def test_a_row_whose_only_difference_is_a_new_column_value_is_changed(lib):
    _load(lib, H7 + "best nrr\tNet revenue retention\tsafe\t\t\t\t\n")
    plan = _plan(lib, H7 + "best nrr\tNet revenue retention\tsafe\t\t\ttop_quartile\t\n")
    assert (plan["new"], plan["changed"], plan["unchanged"]) == (0, 1, 0)
    lib.apply_name_map(plan["writes"])
    assert _stored(lib, "best nrr")["statistic_std"] == "top_quartile"
    # Clearing it through a file that carries the column is also a change.
    plan = _plan(lib, H7 + "best nrr\tNet revenue retention\tsafe\t\t\t\t\n")
    assert plan["changed"] == 1
    lib.apply_name_map(plan["writes"])
    assert _stored(lib, "best nrr")["statistic_std"] is None


# --- export ---------------------------------------------------------------------------------------

def test_export_round_trips_with_nothing_new_or_changed(lib):
    _load(lib, H7 + "best nrr\tNet revenue retention\tsafe\tnew\tretention\ttop_quartile\tperformance_tier\n"
               "plain\tBurn multiple\tsafe\t\t\t\t\nkeep me\t\tkeep_separate\t\t\t\t\nask later\t\tdecision\t\t\t\t\n")
    text = fg.export_name_map_tsv(lib)
    assert text.splitlines()[0] == "raw_name\tcanonical_metric\tstatus\tvariant\tform\tstatistic_std\tcohort"
    assert len(text.splitlines()) == 5 and "best nrr\tNet revenue retention\tsafe\tnew\tretention\ttop_quartile\tperformance_tier" in text
    plan = _plan(lib, text)
    assert (plan["new"], plan["changed"], plan["unchanged"], plan["errors"], plan["writes"]) == (0, 0, 4, [], [])


def test_export_of_an_empty_map_is_just_the_header(lib):
    assert fg.export_name_map_tsv(lib) == "raw_name\tcanonical_metric\tstatus\tvariant\tform\tstatistic_std\tcohort\n"


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("LINKLIB_DB", str(tmp_path / "lib.db"))
    monkeypatch.setenv("LINKLIB_SITES_OPML", str(tmp_path / "sites.opml"))
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SAVE_TOKEN", "savetok")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import webapp.app as appmod
    importlib.reload(appmod)
    lb = appmod._lib()
    fx.seed_registry(lb)
    lb.apply_name_map([("best nrr", "Net revenue retention", "safe", "new", "retention", "top_quartile", "public")])
    lb.close()
    return appmod


def _client(appmod, login=True):
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app)
    if login:
        c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    return c


def test_export_route_downloads_the_map_for_an_admin(env):
    r = _client(env).get("/admin/reader/figures/name-map-export")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/tab-separated-values")
    import re
    assert re.fullmatch(r'attachment; filename="metric-name-map-\d{4}-\d{2}-\d{2}\.tsv"', r.headers["content-disposition"])
    assert "best nrr\tNet revenue retention\tsafe\tnew\tretention\ttop_quartile\tpublic" in r.text


def test_export_route_is_admin_session_only(env):
    anon = _client(env, login=False)
    assert anon.get("/admin/reader/figures/name-map-export", headers={"X-Save-Token": "savetok"}).status_code == 401
    r = anon.get("/admin/reader/figures/name-map-export", follow_redirects=False)
    assert r.status_code in (302, 303, 307) and "best nrr" not in r.text


def test_figures_page_has_the_export_button_and_the_restore_line(env):
    t = _client(env).get("/admin/reader/figures").text
    assert 'href="/admin/reader/figures/name-map-export"' in t
    assert "Export before restoring an older backup; load it again after." in t


def test_routes_load_a_seven_column_file_and_report_changed(env):
    c = _client(env)
    tsv = H7 + "best nrr\tNet revenue retention\tsafe\tnew\tretention\tmedian\tpublic\n"
    r = c.post("/admin/reader/figures/name-map-preview", files={"tsv": ("m.tsv", tsv.encode(), "text/plain")})
    assert r.status_code == 200 and "0 new, 1 changed, 0 unchanged" in r.text
    bad = c.post("/admin/reader/figures/name-map-preview",
                 files={"tsv": ("m.tsv", (H7 + "x\tNet revenue retention\tsafe\t\tgrowth\t\t\n").encode(), "text/plain")})
    assert bad.status_code == 400 and "form 'growth'" in bad.text


# --- standard_view ----------------------------------------------------------------------------------

def _idx(lib, *mapped):
    if mapped:
        lib.apply_name_map(list(mapped))
    return fg._load_metric_index(lib)


def test_metric_and_resolved_by_for_each_way_a_name_resolves(lib):
    idx = _idx(lib, ("odd label", "Burn multiple", "safe"))
    cases = {
        "Net revenue retention": ("canonical", "Net revenue retention"),
        "NRR": ("alias", "Net revenue retention"),
        "Odd label": ("name_map", "Burn multiple"),
        "Net Revenue Retention (LTM)": ("name_key_family", "Net revenue retention"),
        "net new mrr": ("ambiguous_alias", ""),
        "Mystery ratio": ("none", ""),
    }
    for name, (via, metric) in cases.items():
        sv = standard_view(fx.frow(metric=name), idx)
        assert (sv["resolved_by"], sv["metric"]) == (via, metric), name


def test_the_stamped_fields_are_exactly_these_and_nothing_for_sv2(lib):
    sv = standard_view(fx.frow(), _idx(lib))
    assert set(sv) == {"metric", "resolved_by", "variant", "form", "statistic", "cohort", "conflict"}


def test_variant_form_and_cohort_come_from_the_name_map_row_only(lib):
    idx = _idx(lib, ("best nrr", "Net revenue retention", "safe", "new", "retention", "top_quartile", "performance_tier"))
    sv = standard_view(fx.frow(metric="Best NRR", statistic="other"), idx)
    assert (sv["metric"], sv["variant"], sv["form"], sv["cohort"]) == ("Net revenue retention", "new", "retention", "performance_tier")
    plain = standard_view(fx.frow(metric="NRR"), idx)
    assert (plain["variant"], plain["form"], plain["cohort"]) == ("", "", "")


@pytest.mark.parametrize("stored", ["", "other", "value", "reported", "Other", " Value "])
def test_an_empty_or_uninformative_stored_statistic_is_filled_by_the_override(lib, stored):
    idx = _idx(lib, ("best nrr", "Net revenue retention", "safe", "", "", "top_quartile", ""))
    sv = standard_view(fx.frow(metric="best nrr", statistic=stored), idx)
    assert (sv["statistic"], sv["conflict"]) == ("top_quartile", "")


def test_a_real_stored_statistic_is_kept_and_a_contradiction_is_recorded(lib):
    idx = _idx(lib, ("best nrr", "Net revenue retention", "safe", "", "", "top_quartile", ""))
    sv = standard_view(fx.frow(metric="best nrr", statistic="median"), idx)
    assert sv["statistic"] == "median"
    assert "name map says top_quartile, row prints median" in sv["conflict"]
    odd = standard_view(fx.frow(metric="best nrr", statistic="min"), idx)       # not on the list, still real
    assert odd["statistic"] == "min" and odd["conflict"]
    agree = standard_view(fx.frow(metric="best nrr", statistic="p75"), idx)     # p75 normalizes to top_quartile
    assert (agree["statistic"], agree["conflict"]) == ("top_quartile", "")


@pytest.mark.parametrize("stored,expect", [
    ("median", "median"), ("P50", "median"), ("Mean", "mean"), ("average", "mean"),
    ("top quartile", "top_quartile"), ("Top_Quartile", "top_quartile"), ("p75", "top_quartile"),
    ("bottom quartile", "bottom_quartile"), ("p25", "bottom_quartile"), ("p90", "p90"), ("top decile", "p90"),
    ("p10", "p10"), ("p95", "other_percentile"), ("80th percentile", "other_percentile"),
    ("target", "target"), ("Best-in-class target", "target"),
    ("other", "other"), ("min", "min"), ("", ""),
])
def test_normalize_statistic(stored, expect):
    assert normalize_statistic(stored) == expect


def test_no_value_is_converted_and_the_row_is_not_touched(lib):
    idx = _idx(lib, ("churn nrr", "Net revenue retention", "safe", "", "churn", "", ""))
    row = fx.frow(metric="churn nrr", value="8")
    before = copy.deepcopy(row)
    sv = standard_view(row, idx)
    assert sv["form"] == "churn" and row == before


def test_standard_view_is_deterministic(lib):
    idx = _idx(lib)
    row = fx.frow(metric="NRR")
    assert standard_view(row, idx) == standard_view(row, idx)


def test_an_override_for_a_different_metric_is_ignored(lib):
    idx = _idx(lib)
    idx["map_rows"]["nrr"] = {"canonical": "Burn multiple", "variant": "x", "form": "", "statistic_std": "", "cohort": ""}
    assert standard_view(fx.frow(metric="NRR"), idx)["variant"] == ""


def test_an_unmigrated_read_only_database_still_loads_the_index(tmp_path):
    import sqlite3
    path = str(tmp_path / "old.db")
    lb = Library(path)
    fx.seed_registry(lb)
    lb.apply_name_map([("odd", "Burn multiple", "safe")])
    lb.close()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript("ALTER TABLE metric_name_map RENAME TO m_old; CREATE TABLE metric_name_map "
                       "(raw_name_lower TEXT PRIMARY KEY, canonical_metric TEXT, status TEXT NOT NULL DEFAULT '');"
                       "INSERT INTO metric_name_map SELECT raw_name_lower, canonical_metric, status FROM m_old;")
    idx = fg._load_metric_index(aud._Shim(conn))
    assert idx["map_rows"]["odd"]["canonical"] == "Burn multiple" and idx["map_rows"]["odd"]["variant"] == ""
    assert standard_view(fx.frow(metric="odd"), idx)["resolved_by"] == "name_map"


# --- the stamp -----------------------------------------------------------------------------------------

def test_every_find_figures_row_carries_std_and_nothing_else_changes(lib, monkeypatch):
    fx.seed_standard(lib)
    lib.apply_name_map([("nrr (best)", "Net revenue retention", "safe", "new", "retention", "top_quartile", "public")])
    fx.load_report(lib, "eb-2099b", [fx.frow(report="Example Benchmarks 2099b", metric="NRR (best)", statistic="other")])
    with_std = [fg.find_figures(lib, m, band="$10M-$20M", all_editions=ae) for m in ("NRR", "Burn multiple")
                for ae in (False, True)]
    orig = fg._row_out
    monkeypatch.setattr(fg, "_row_out", lambda r, landings, canonical, idx=None: orig(r, landings, canonical))
    without = [fg.find_figures(lib, m, band="$10M-$20M", all_editions=ae) for m in ("NRR", "Burn multiple")
               for ae in (False, True)]
    total = 0
    for a, b in zip(with_std, without):
        for row in a["rows"]:
            assert isinstance(row["std"], dict) and row["canonical_metric"] is not None
            total += 1
        stripped = copy.deepcopy(a)
        for row in stripped["rows"]:
            del row["std"]
        assert stripped == b
    assert total >= 4


def test_canonical_metric_keeps_its_meaning(lib):
    fx.seed_standard(lib)
    rows = fg.find_figures(lib, "NRR", band="$10M-$20M")["rows"]
    assert rows and all(r["canonical_metric"] == "Net revenue retention" == r["std"]["metric"] for r in rows)


# --- the audit's standard_view.csv ------------------------------------------------------------------------

def test_audit_writes_standard_view_csv_and_a_summary_total(tmp_path):
    path = str(tmp_path / "a.db")
    lb = Library(path)
    fx.seed_registry(lb)
    fx.load_report(lb, "eb-2099", [
        fx.frow(metric="Net revenue retention"), fx.frow(metric="NRR"), fx.frow(metric="Mystery ratio"),
        fx.frow(metric="Mystery ratio", band="$20M-$30M"), fx.frow(metric="net new mrr"),
        fx.frow(metric="Best NRR", statistic="median"),
    ])
    fx.load_report(lb, "ssc-2099", [fx.frow(source="Sample Survey Co", report="Sample Survey 2099", metric="NRR")])
    lb.apply_name_map([("best nrr", "Net revenue retention", "safe", "", "", "top_quartile", "")])
    lb.close()
    out = tmp_path / "out"
    assert aud.main(["--db", path, "--out-dir", str(out)]) == 0
    with open(out / "standard_view.csv", newline="", encoding="utf-8") as f:
        rd = csv.DictReader(f)
        assert rd.fieldnames == ["report_slug", "rows", "stamped", "residue", "conflicts", "pct_stamped",
                                 "residue_samples", "conflict_samples"]
        rows = {r["report_slug"]: r for r in rd}
    eb = rows["eb-2099"]
    assert (eb["rows"], eb["stamped"], eb["residue"], eb["conflicts"]) == ("6", "3", "3", "1")
    assert eb["pct_stamped"] == "50.0"
    assert eb["residue_samples"] == "Mystery ratio (2);net new mrr (1)"
    assert eb["conflict_samples"].startswith("Best NRR | name map says top_quartile, row prints median")
    assert (rows["ssc-2099"]["stamped"], rows["ssc-2099"]["residue"], rows["ssc-2099"]["pct_stamped"]) == ("1", "0", "100.0")
    summary = (out / "summary.txt").read_text()
    assert "Standard view: stamped 4, residue 3, conflicts 1 (of 7 rows)" in summary
    assert "editions, standard_view (.csv)" in summary
