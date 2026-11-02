"""Per-report edit page on /admin/reader/figures, and the list that links to it.
Invented publishers and example.test URLs only."""
import importlib
import pathlib
import re
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from figures_fixtures import frow, load_report  # noqa: E402
from linklib import figures as fg  # noqa: E402

GM = "Net revenue retention"


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("LINKLIB_DB", str(tmp_path / "lib.db"))
    monkeypatch.setenv("LINKLIB_SITES_OPML", str(tmp_path / "sites.opml"))
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SAVE_TOKEN", "savetok")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import webapp.app as appmod
    importlib.reload(appmod)
    lib = appmod._lib()
    try:
        load_report(lib, "eb-2099", [frow()])
        load_report(lib, "cap-2099", [frow(source="Example Capital", report="Example Capital 2099", band="$10M-$20M")])
    finally:
        lib.close()
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    yield appmod, c


def _form(**kw):
    base = {"source": "Example Benchmarks", "report": "Example Benchmarks 2099", "report_year": "2099",
            "landing_url": "", "cited_source": "", "cited_source_metric": ""}
    base.update(kw)
    return base


def _reports(appmod):
    lib = appmod._lib()
    try:
        return {r["report_slug"]: r for r in lib.list_benchmark_reports()}
    finally:
        lib.close()


def test_edit_page_has_labelled_fields_and_read_only_slug(env):
    appmod, c = env
    html = c.get("/admin/reader/figures/eb-2099/edit").text
    for label in ("Source", "Report title", "Year", "Landing URL", "Republished from", "Only for metrics containing"):
        assert re.search(rf'<label for="rf-[a-z]+"[^>]*>{label}</label>', html), label
    assert 'class="page page-standard"' in html and 'class="page page-form' not in html
    assert "<code>eb-2099</code>" in html and 'name="report_slug"' not in html
    assert c.get("/admin/reader/figures/nope/edit").status_code == 404


def test_saving_names_what_changed_and_shows_the_effective_value(env):
    appmod, c = env
    r = c.post("/admin/reader/figures/eb-2099/edit",
               data=_form(report="Example Benchmarks Annual Report", report_year="2098",
                          cited_source="Example Alpha 2025 SaaS Benchmarks", cited_source_metric="headcount"))
    assert r.status_code == 200
    assert "Saved. Changed: Report title, Year, Republished from, Only for metrics containing." in r.text
    assert 'value="Example Benchmarks Annual Report"' in r.text
    again = c.post("/admin/reader/figures/eb-2099/edit", data=_form(
        report="Example Benchmarks Annual Report", report_year="2098",
        cited_source="Example Alpha 2025 SaaS Benchmarks", cited_source_metric="headcount"))
    assert "Nothing changed." in again.text


def test_bad_values_are_refused_and_nothing_is_written(env):
    appmod, c = env
    for bad in (_form(report_year="98"), _form(landing_url="ftp://x"), _form(report=" "),
                _form(cited_source_metric="headcount")):
        r = c.post("/admin/reader/figures/eb-2099/edit", data=bad)
        assert r.status_code == 400
    rep = _reports(appmod)["eb-2099"]
    assert rep["report_override"] == rep["year_override"] == rep["landing_url_manual"] == rep["cited_source"] == ""


def test_name_year_and_url_edits_survive_a_reload_of_the_zip(env):
    appmod, c = env
    c.post("/admin/reader/figures/eb-2099/edit", data=_form(
        source="Example Benchmarks Inc", report="Renamed Report", report_year="2098",
        landing_url="https://r.example.test/x"))
    lib = appmod._lib()
    try:
        load_report(lib, "eb-2099", [frow(), frow(band="$30M-$50M")])      # the zip is loaded again
    finally:
        lib.close()
    rep = _reports(appmod)["eb-2099"]
    assert (rep["source"], rep["report"], rep["report_year"]) == ("Example Benchmarks Inc", "Renamed Report", "2098")
    assert rep["landing_url_manual"] == "https://r.example.test/x" and rep["row_count"] == 2
    assert rep["report_loaded"] == "Example Benchmarks 2099"      # what the file said is still known


def test_typing_the_file_value_back_clears_the_override(env):
    appmod, c = env
    c.post("/admin/reader/figures/eb-2099/edit", data=_form(report="Renamed"))
    c.post("/admin/reader/figures/eb-2099/edit", data=_form())
    assert _reports(appmod)["eb-2099"]["report_override"] == ""


def test_an_edited_name_and_year_reach_the_figures_buddy_cites(env):
    appmod, c = env
    c.post("/admin/reader/figures/eb-2099/edit", data=_form(
        source="Example Benchmarks Inc", report="Renamed Report", report_year="2098"))
    lib = appmod._lib()
    try:
        load_report(lib, "eb-2099", [frow()])                       # reload does not undo it
        row = [r for r in fg.find_figures(lib, GM, band="$15M")["rows"] if r["report_slug"] == "eb-2099"][0]
        cov = fg.coverage(lib, GM)
    finally:
        lib.close()
    assert (row["source"], row["report"], row["report_year"]) == ("Example Benchmarks Inc", "Renamed Report", "2098")
    assert [r["report"] for r in cov["reports"] if r["report_slug"] == "eb-2099"] == ["Renamed Report"]


def test_republished_from_saved_on_the_edit_page_behaves_like_the_inline_save(env):
    appmod, c = env
    lib = appmod._lib()
    try:
        load_report(lib, "ha-2025", [frow(source="Example Alpha", report="Example Alpha 2025 SaaS Benchmarks",
                                          year="2025", band="$10M-$20M")])
    finally:
        lib.close()
    c.post("/admin/reader/figures/cap-2099/edit", data=_form(
        source="Example Capital", report="Example Capital 2099", cited_source="Example Alpha 2025 SaaS Benchmarks"))
    lib = appmod._lib()
    try:
        res = fg.find_figures(lib, GM, band="$15M")
    finally:
        lib.close()
    assert [r["report_slug"] for r in res["rows"] if r["report_slug"] in ("ha-2025", "cap-2099")] == ["ha-2025"]
    ha = [r for r in res["rows"] if r["report_slug"] == "ha-2025"][0]
    assert any("Example Capital" in x for x in ha["also_printed_in"])


def test_list_has_edit_links_effective_values_and_no_inline_inputs(env):
    appmod, c = env
    c.post("/admin/reader/figures/eb-2099/edit", data=_form(
        report="Renamed Report", cited_source="Example Alpha 2025 SaaS Benchmarks", cited_source_metric="headcount"))
    html = c.get("/admin/reader/figures").text
    table = html[html.index('<table class="bk-stack fg-reports"'):html.index("</table>")]
    assert 'href="/admin/reader/figures/eb-2099/edit"' in table and "<th" in table
    assert re.search(r"<th[^>]*>Actions</th>", table)
    assert "Renamed Report" in table
    assert "Example Alpha 2025 SaaS Benchmarks, metrics containing headcount" in table
    assert "<input" not in table and "<form" not in table
    # a report with nothing republished says so with a dash, never blank
    cap_row = table[table.index("cap-2099"):]
    assert 'data-label="Republished from"' in cap_row and "&mdash;" in cap_row
    for label in ("Report", "Source", "Year", "Rows stored", "Loaded", "Landing URL", "Republished from", "Actions"):
        assert f'data-label="{label}"' in table
    assert "max-width:700px" in html and ".fg-reports td[data-label]::before" in html


def test_old_inline_save_routes_are_gone(env):
    appmod, c = env
    assert c.post("/admin/reader/figures/landing", data={"report_slug": "eb-2099", "landing_url": "https://x.example.test"}).status_code in (404, 405)
    assert c.post("/admin/reader/figures/cited-source", data={"report_slug": "eb-2099", "cited_source": "x"}).status_code in (404, 405)


def test_edit_routes_need_an_admin_session(env):
    appmod, c = env
    from fastapi.testclient import TestClient
    anon = TestClient(appmod.app)
    assert anon.get("/admin/reader/figures/eb-2099/edit", follow_redirects=False).status_code in (302, 303, 307)
    assert anon.post("/admin/reader/figures/eb-2099/edit", data=_form()).status_code == 401
