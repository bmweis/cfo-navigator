"""Metric-name map loader and the audit's resolved_by column (Build A). Invented data only."""
import csv
import importlib

import pytest

from linklib import figures as fg
from linklib.db import Library
from scripts import audit_benchmark_alignment as aud
from tests import figures_fixtures as fx

HEADER = "raw_name\tcanonical_metric\tstatus\n"


@pytest.fixture
def lib(tmp_path):
    lb = Library(str(tmp_path / "t.db"))
    fx.seed_registry(lb)
    yield lb
    lb.close()


def _plan(lib, body):
    rows, errors = fg.parse_name_map_tsv(HEADER + body)
    assert not errors, errors
    return fg.plan_name_map(lib, rows)


# --- classify_metric_name: all six resolved_by values -------------------------------------

@pytest.mark.parametrize("raw,via,canon", [
    ("Net revenue retention", "canonical", ["Net revenue retention"]),
    ("NRR", "alias", ["Net revenue retention"]),
    ("net new mrr", "ambiguous_alias", ["MRR", "Net New ARR"]),
    ("Net Revenue Retention (LTM)", "name_key_family", ["Net revenue retention"]),
    ("Totally unknown thing", "none", []),
])
def test_classify_each_resolved_by(lib, raw, via, canon):
    assert fg.classify_metric_name(lib, raw) == (via, canon)


def test_classify_name_map_value(lib):
    lib.apply_name_map([("odd nrr label", "Net revenue retention", "safe")])
    assert fg.classify_metric_name(lib, "Odd NRR label") == ("name_map", ["Net revenue retention"])


def test_find_figures_and_classifier_agree_on_a_family_name(lib):
    fx.load_report(lib, "eb-2099", [fx.frow(metric="Net Revenue Retention (LTM)", value="104")])
    assert fg.classify_metric_name(lib, "Net Revenue Retention (LTM)")[0] == "name_key_family"
    assert [r["value"] for r in fg.find_figures(lib, "NRR", band="$10M-$20M")["rows"]] == ["104"]


# --- parse and plan -------------------------------------------------------------------------

def test_parse_requires_header_and_valid_status():
    assert fg.parse_name_map_tsv("a\tb\tc\n")[1]
    rows, errors = fg.parse_name_map_tsv(HEADER + "x\t\tmaybe\n")
    assert errors and "status" in errors[0]
    _, errors = fg.parse_name_map_tsv(HEADER + "x\t\tsafe\n")
    assert errors and "no canonical" in errors[0]
    _, errors = fg.parse_name_map_tsv(HEADER + "x\tNet revenue retention\tdecision\n")
    assert errors and "must be empty" in errors[0]
    _, errors = fg.parse_name_map_tsv(HEADER + "x\t\tdecision\nX\t\tkeep_separate\n")
    assert errors and "twice" in errors[0]
    rows, errors = fg.parse_name_map_tsv(HEADER + "x\t\tdecision\nX \t\tdecision\n")
    assert not errors and len(rows) == 1


def test_plan_accepts_safe_row_and_stores_registry_spelling(lib):
    plan = _plan(lib, "Odd NRR Label\tNET REVENUE RETENTION\tsafe\n")
    assert plan["errors"] == [] and plan["new"] == 1
    assert plan["writes"] == [("odd nrr label", "Net revenue retention", "safe")]


def test_plan_refuses_unknown_canonical(lib):
    assert "not a canonical metric" in _plan(lib, "x\tNo such metric\tsafe\n")["errors"][0]


def test_plan_refuses_a_short_alias_with_a_hint(lib):
    err = _plan(lib, "x\tNRR\tsafe\n")["errors"][0]
    assert "alias" in err and "Net revenue retention" in err


def test_plan_refuses_a_name_already_an_alias_of_another_metric(lib):
    err = _plan(lib, "NRR\tBurn multiple\tsafe\n")["errors"][0]
    assert "already an alias" in err and "not overwritten" in err
    err = _plan(lib, "MRR\tBurn multiple\tsafe\n")["errors"][0]
    assert "already the canonical name" in err


def test_load_is_idempotent(lib):
    plan = _plan(lib, "a\tNet revenue retention\tsafe\nb\t\tkeep_separate\nc\t\tdecision\n")
    assert (plan["new"], plan["changed"], plan["unchanged"]) == (3, 0, 0)
    assert lib.apply_name_map(plan["writes"]) == 3
    again = _plan(lib, "a\tNet revenue retention\tsafe\nb\t\tkeep_separate\nc\t\tdecision\n")
    assert (again["new"], again["changed"], again["unchanged"], again["writes"]) == (0, 0, 3, [])
    changed = _plan(lib, "c\tBurn multiple\tsafe\n")
    assert (changed["new"], changed["changed"]) == (0, 1)


def test_reader_ignores_keep_separate_and_decision(lib):
    lib.apply_name_map([("keepme", "", "keep_separate"), ("decideme", "", "decision")])
    # Rows written by hand with a canonical but a non-safe status must not map either.
    lib.conn.execute("INSERT INTO metric_name_map VALUES ('sneaky', 'Burn multiple', 'decision')")
    lib.conn.execute("INSERT INTO metric_name_map VALUES ('legacy', 'Burn multiple', '')")
    lib.conn.commit()
    for raw in ("keepme", "decideme", "sneaky", "legacy"):
        assert fg.resolve_metric(lib, raw) == []
    lib.apply_name_map([("sneaky", "Burn multiple", "safe")])
    assert fg.resolve_metric(lib, "sneaky") == [{"canonical": "Burn multiple", "match": "name_map"}]


def test_existing_resolution_is_unchanged(lib):
    assert fg.resolve_metric(lib, "Net revenue retention") == [{"canonical": "Net revenue retention", "match": "canonical"}]
    assert fg.resolve_metric(lib, "net dollar retention") == [{"canonical": "Net revenue retention", "match": "alias"}]
    assert [r["match"] for r in fg.resolve_metric(lib, "capital efficiency")] == ["ambiguous_alias"] * 2
    assert fg.resolve_metric(lib, "MRR") == [{"canonical": "MRR", "match": "canonical"}]


def test_a_mapping_takes_effect_on_the_next_lookup_without_a_reload(lib):
    fx.load_report(lib, "eb-2099", [fx.frow(metric="Weird retention label", value="107")])
    assert fg.find_figures(lib, "NRR", band="$10M-$20M")["rows"] == []
    lib.apply_name_map([("weird retention label", "Net revenue retention", "safe")])
    assert [r["value"] for r in fg.find_figures(lib, "NRR", band="$10M-$20M")["rows"]] == ["107"]


# --- audit: metrics.csv ---------------------------------------------------------------------

def test_audit_metrics_csv_has_resolved_by_and_richer_columns(tmp_path):
    path = str(tmp_path / "a.db")
    lb = Library(path)
    fx.seed_registry(lb)
    fx.load_report(lb, "eb-2099", [
        fx.frow(metric="Net revenue retention"), fx.frow(metric="NRR"),
        fx.frow(metric="net new mrr"), fx.frow(metric="Net Revenue Retention (LTM)", denominator="% of ARR"),
        fx.frow(metric="Mystery ratio"), fx.frow(metric="Mystery ratio"),
        fx.frow(metric="Odd label"),
    ])
    lb.apply_name_map([("odd label", "Burn multiple", "safe")])
    lb.close()
    out = tmp_path / "out"
    assert aud.main(["--db", path, "--out-dir", str(out)]) == 0
    with open(out / "metrics.csv", newline="", encoding="utf-8") as f:
        rows = {r["raw"]: r for r in csv.DictReader(f)}
    assert {r["resolved_by"] for r in rows.values()} == {
        "canonical", "alias", "ambiguous_alias", "name_key_family", "none", "name_map"}
    assert rows["Net Revenue Retention (LTM)"]["canonical"] == "Net revenue retention"
    assert rows["Net Revenue Retention (LTM)"]["name_key"] == "net retention revenue"
    assert rows["Net Revenue Retention (LTM)"]["denominator_top"] == "% of ARR"
    assert rows["Mystery ratio"]["rows"] == "2" and rows["Mystery ratio"]["raw_lower"] == "mystery ratio"
    assert rows["Mystery ratio"]["sample_1"].startswith("eb-2099 | ") and rows["Mystery ratio"]["unit_top"] == "percent"
    summary = (out / "summary.txt").read_text()
    assert "resolved_by none): 1 " in summary and "Old exact-phrase count" in summary
    assert "(none + name_key_family" in summary and "2 (" in summary.split("Old exact-phrase count")[1]
    assert str(out) in summary and "/tmp output is lost" in summary


# --- routes ---------------------------------------------------------------------------------

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
    lb.close()
    return appmod


def _client(appmod, login=True):
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app)
    if login:
        c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    return c


def _rows(appmod):
    lb = appmod._lib()
    try:
        return [tuple(r) for r in lb.conn.execute("SELECT * FROM metric_name_map ORDER BY raw_name_lower")]
    finally:
        lb.close()


def test_routes_preview_writes_nothing_then_load_writes_once(env):
    tsv = HEADER + "odd label\tnet revenue retention\tsafe\nkeep me\t\tkeep_separate\n"
    c = _client(env)
    r = c.post("/admin/reader/figures/name-map-preview", files={"tsv": ("m.tsv", tsv.encode(), "text/plain")})
    assert r.status_code == 200 and "2 new, 0 changed, 0 unchanged" in r.text and "Load 2 mappings" in r.text
    assert _rows(env) == []
    r = c.post("/admin/reader/figures/name-map-load", data={"tsv_text": tsv})
    assert r.status_code == 200 and "2 new" in r.text
    assert _rows(env) == [("keep me", None, "keep_separate"), ("odd label", "Net revenue retention", "safe")]
    r = c.post("/admin/reader/figures/name-map-preview", files={"tsv": ("m.tsv", tsv.encode(), "text/plain")})
    assert "0 new, 0 changed, 2 unchanged" in r.text and "Nothing would change" in r.text
    lb = env._lib()
    try:
        n = lb.conn.execute("SELECT COUNT(*) FROM archive_audit_log WHERE detail LIKE 'metric name map load%'").fetchone()[0]
    finally:
        lb.close()
    assert n == 1


def test_routes_refuse_bad_rows_and_write_nothing(env):
    c = _client(env)
    tsv = HEADER + "ok\tNet revenue retention\tsafe\nbad\tNRR\tsafe\n"
    r = c.post("/admin/reader/figures/name-map-preview", files={"tsv": ("m.tsv", tsv.encode(), "text/plain")})
    assert r.status_code == 400 and "not a canonical metric" in r.text and "Load " not in r.text
    r = c.post("/admin/reader/figures/name-map-load", data={"tsv_text": tsv})
    assert r.status_code == 400 and "Nothing was written" in r.text
    assert _rows(env) == []


def test_routes_are_admin_session_only(env):
    tsv = (HEADER + "a\t\tdecision\n").encode()
    anon = _client(env, login=False)
    for path, kw in (("/admin/reader/figures/name-map-preview", {"files": {"tsv": ("m.tsv", tsv, "text/plain")}}),
                     ("/admin/reader/figures/name-map-load", {"data": {"tsv_text": tsv.decode()}})):
        assert anon.post(path, **kw).status_code == 401
        assert anon.post(path, headers={"X-Save-Token": "savetok"}, **kw).status_code == 401
    assert _rows(env) == []
