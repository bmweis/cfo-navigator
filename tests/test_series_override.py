"""'Same series as' override on benchmark reports. Invented publishers and titles only."""
import importlib
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from figures_fixtures import frow, load_report, seed_registry  # noqa: E402
from linklib import figures as fg  # noqa: E402
from linklib.db import Library  # noqa: E402


@pytest.fixture
def lib(tmp_path):
    lb = Library(str(tmp_path / "lib.db"))
    seed_registry(lb)
    # One publisher renamed its report between editions.
    load_report(lb, "old", [frow(report="Alpha SaaS Benchmarks 2098", year="2098", source="Example Benchmarks", value="100")])
    load_report(lb, "new", [frow(report="Alpha Startup Survey 2099", year="2099", source="Example Benchmarks", value="104")])
    yield lb
    lb.close()


def _set(lib, slug, series):
    cur = lib.get_benchmark_report(slug)
    lib.save_benchmark_report_edit(slug, source=cur["source"], report=cur["report"], report_year=cur["report_year"],
                                   landing_url="", cited_source="", cited_source_metric="", series_override=series)


def _years(r):
    return sorted(x["report_year"] for x in r["rows"])


def test_no_override_stays_split(lib):
    # Current behaviour pinned: different names, two families, both are "newest".
    assert _years(fg.find_figures(lib, "Net revenue retention")) == ["2098", "2099"]


def test_same_override_makes_one_family(lib):
    _set(lib, "old", "Alpha benchmarks")
    _set(lib, "new", "alpha  Benchmarks")  # case and spacing do not matter
    r = fg.find_figures(lib, "Net revenue retention")
    assert _years(r) == ["2099"]
    assert any("Older editions exist and were left out" in n and "Alpha SaaS Benchmarks 2098" in n for n in r["notes"])
    assert _years(fg.find_figures(lib, "Net revenue retention", all_editions=True)) == ["2098", "2099"]


def test_displayed_names_stay_the_stored_titles(lib):
    _set(lib, "old", "Alpha benchmarks")
    _set(lib, "new", "Alpha benchmarks")
    r = fg.find_figures(lib, "Net revenue retention", all_editions=True)
    assert {x["report"] for x in r["rows"]} == {"Alpha SaaS Benchmarks 2098", "Alpha Startup Survey 2099"}
    rep = lib.get_benchmark_report("new")
    assert rep["report"] == "Alpha Startup Survey 2099" and rep["report_override"] == ""


def test_reload_keeps_override(lib):
    _set(lib, "old", "Alpha benchmarks")
    load_report(lib, "old", [frow(report="Alpha SaaS Benchmarks 2098", year="2098", source="Example Benchmarks", value="101")])
    assert lib.get_benchmark_report("old")["series_override"] == "Alpha benchmarks"


def test_clearing_returns_to_derived_family(lib):
    _set(lib, "old", "Alpha benchmarks")
    _set(lib, "new", "Alpha benchmarks")
    _set(lib, "old", "")
    _set(lib, "new", "")
    assert _years(fg.find_figures(lib, "Net revenue retention")) == ["2098", "2099"]


def test_save_without_series_keeps_it(lib):
    _set(lib, "old", "Alpha benchmarks")
    cur = lib.get_benchmark_report("old")
    lib.save_benchmark_report_edit("old", source=cur["source"], report=cur["report"], report_year=cur["report_year"],
                                   landing_url="", cited_source="", cited_source_metric="")
    assert lib.get_benchmark_report("old")["series_override"] == "Alpha benchmarks"


def test_too_long_refused(lib):
    with pytest.raises(ValueError):
        _set(lib, "old", "x" * 200)


def test_family_members_lists_the_others(lib):
    _set(lib, "old", "Alpha benchmarks")
    _set(lib, "new", "Alpha benchmarks")
    reps = lib.list_benchmark_reports()
    key, members = fg.family_members(reps, lib.get_benchmark_report("old"))
    assert key == "alpha benchmarks"
    assert [(m["report_slug"], m["report_year"]) for m in members] == [("new", "2099")]
    assert "Alpha benchmarks" in fg.family_name_options(reps)


@pytest.fixture
def web(monkeypatch, tmp_path):
    monkeypatch.setenv("LINKLIB_DB", str(tmp_path / "lib.db"))
    monkeypatch.setenv("LINKLIB_SITES_OPML", str(tmp_path / "sites.opml"))
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SAVE_TOKEN", "savetok")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import webapp.app as appmod
    importlib.reload(appmod)
    lb = appmod._lib()
    try:
        load_report(lb, "old", [frow(report="Alpha SaaS Benchmarks 2098", year="2098")])
        load_report(lb, "new", [frow(report="Alpha Startup Survey 2099", year="2099")])
    finally:
        lb.close()
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    return appmod, c


def test_edit_page_shows_and_saves_series(web):
    appmod, c = web
    html = c.get("/admin/reader/figures/old/edit").text
    assert "Same series as" in html and 'name="series_override"' in html and "<datalist" in html
    assert "derived from the report title" in html
    form = {"source": "Example Benchmarks", "report": "Alpha SaaS Benchmarks 2098", "report_year": "2098",
            "landing_url": "", "cited_source": "", "cited_source_metric": "", "series_override": "Alpha benchmarks"}
    assert c.post("/admin/reader/figures/old/edit", data=form).status_code == 200
    html = c.get("/admin/reader/figures/old/edit").text
    assert 'value="Alpha benchmarks"' in html
