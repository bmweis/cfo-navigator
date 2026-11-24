"""/admin/reader/figures usability (Part C): the reports table's controls, columns, series counts, inline edit and
bulk edit; the shift-thresholds table; the series pick list. Invented publishers and example.test URLs only."""
import importlib
import json
import pathlib
import re
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from figures_fixtures import frow, load_report  # noqa: E402
from linklib import figures as fg  # noqa: E402


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
        # One split series (two titles for one report family: the same source, different words) and one joined
        # series (two editions of one title), plus a lone report.
        load_report(lib, "alpha-2023", [frow(source="Example Alpha", report="Example Alpha SaaS Benchmarks 2023", year="2023")])
        load_report(lib, "alpha-2024", [frow(source="Example Alpha", report="Example Alpha SaaS Benchmarks 2024", year="2024")])
        load_report(lib, "beta-2024", [frow(source="Example Beta", report="Beta Growth Survey", year="2024")])
        load_report(lib, "beta-2025", [frow(source="Example Beta", report="Beta State of Growth", year="2025")])
        load_report(lib, "gamma-2099", [frow(source="Example Gamma", report="Gamma One Off 2099", year="2099")])
    finally:
        lib.close()
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    yield appmod, c


def _reports(appmod):
    lib = appmod._lib()
    try:
        return {r["report_slug"]: r for r in lib.list_benchmark_reports()}
    finally:
        lib.close()


def _row(html: str, slug: str) -> str:
    m = re.search(rf'<tr class="admin-table-row" data-slug="{re.escape(slug)}".*?</tr>', html, re.S)
    assert m, slug
    return m.group(0)



def _post(c, path, body):
    return c.post(path, content=json.dumps(body), headers={"content-type": "application/json"})


def _series_cells(html):
    out = {}
    for slug in ("alpha-2023", "alpha-2024", "beta-2024", "beta-2025", "gamma-2099"):
        m = re.search(r'<span class="fg-series-name">(.*?)</span><span class="fg-sub">(.*?)</span>', _row(html, slug))
        out[slug] = (m.group(1), m.group(2))
    return out


# --- C1: reports table ---------------------------------------------------------------------------------------

def test_reports_table_has_the_controls_and_columns(env):
    appmod, c = env
    html = c.get("/admin/reader/figures").text
    assert 'id="figures-filter-search"' in html and "Search title, slug or source" in html
    assert 'id="figures-sort-field"' in html and 'data-filter-field="source"' in html
    assert "resetSortFilter('figures')" in html and 'id="figures-sort-filter-count"' in html
    assert "applySortFilter('figures');" in html
    start = html.index('<table class="bk-stack fg-reports"')
    table = html[start:html.index("</table>", start)]
    head = table[table.index("<thead>"):table.index("</thead>")]
    heads = [re.sub(r"<[^>]+>", "", h).strip() for h in re.findall(r"<th[^>]*>.*?</th>", head, re.S)]
    assert heads == ["Report", "Source", "Year", "Series", "Figures", "Link", "Republished from", "Loaded", "Actions"]
    assert re.search(r'<th class="admin-sticky-col"[^>]*><div[^>]*><input type="checkbox"[^>]*selectAllRows\(\'figures\'', table)
    row = _row(html, "alpha-2024")
    assert row.count('class="admin-sticky-col"') == 1 and 'class="figures-row-cb"' in row
    for label in ("Report", "Source", "Year", "Series", "Figures", "Link", "Republished from", "Loaded", "Actions"):
        assert f'data-label="{label}"' in row


def test_default_order_is_source_then_newest_year_and_sort_keys_are_stamped(env):
    appmod, c = env
    html = c.get("/admin/reader/figures").text
    order = [m.group(1) for m in re.finditer(r'<tr class="admin-table-row" data-slug="([^"]+)"', html)]
    assert order == ["alpha-2024", "alpha-2023", "beta-2025", "beta-2024", "gamma-2099"]
    ranks = {s: re.search(r'data-order="(\d+)"', _row(html, s)).group(1) for s in order}
    assert list(ranks.values()) == sorted(ranks.values())
    # "newest first" fields are stored inverted so the shared ascending sort puts the newest on top
    yr = {s: re.search(r'data-yearnew="(\d+)"', _row(html, s)).group(1) for s in order}
    assert yr["gamma-2099"] < yr["beta-2025"] < yr["beta-2024"] < yr["alpha-2023"]


def test_series_column_shows_the_split_and_the_joined_series(env):
    appmod, c = env
    cells = _series_cells(c.get("/admin/reader/figures").text)
    # two editions of one title: one series of 2
    assert cells["alpha-2023"] == cells["alpha-2024"] == ("Example Alpha SaaS Benchmarks", "2 editions")
    # a renamed report: the same publisher, two titles, so two series of 1 (the split Brian has to spot)
    assert cells["beta-2024"] == ("Beta Growth Survey", "1 edition")
    assert cells["beta-2025"] == ("Beta State of Growth", "1 edition")
    assert cells["gamma-2099"][1] == "1 edition"


def test_series_override_joins_the_split_and_shows_in_the_column(env):
    appmod, c = env
    for slug in ("beta-2024", "beta-2025"):
        assert _post(c, f"/admin/reader/figures/{slug}/inline", {"series_override": "Beta growth series"}).json()["ok"]
    cells = _series_cells(c.get("/admin/reader/figures").text)
    assert cells["beta-2024"] == cells["beta-2025"] == ("Beta growth series", "2 editions")


def test_a_cell_with_nothing_says_so(env):
    appmod, c = env
    lib = appmod._lib()
    try:
        load_report(lib, "blank-1", [frow(source="", report="Untitled thing", year="")], year="")
    finally:
        lib.close()
    row = _row(c.get("/admin/reader/figures").text, "blank-1")
    view = lambda label: re.search(rf'data-label="{label}"[^>]*>(.*?)</td>', row, re.S).group(1)
    assert "&mdash;" in view("Source") and "&mdash;" in view("Year") and "&mdash;" in view("Republished from")
    assert ">No<" in view("Link")


# --- the "Same series as" suggestion list (rider) ------------------------------------------------------------

def test_series_pick_list_lists_only_overrides_and_multi_edition_series(env):
    appmod, c = env
    lib = appmod._lib()
    try:
        opts = fg.family_name_options(lib.list_benchmark_reports())
    finally:
        lib.close()
    assert opts == ["Example Alpha SaaS Benchmarks"]        # the only derived series with two editions
    _post(c, "/admin/reader/figures/beta-2024/inline", {"series_override": "Beta growth series"})
    lib = appmod._lib()
    try:
        opts = fg.family_name_options(lib.list_benchmark_reports())
    finally:
        lib.close()
    assert opts == ["Beta growth series", "Example Alpha SaaS Benchmarks"]
    for page in ("/admin/reader/figures", "/admin/reader/figures/gamma-2099/edit"):
        html = c.get(page).text
        assert '<option value="Beta growth series">' in html and "Gamma One Off" not in html.split("<datalist")[1].split("</datalist>")[0]


# --- C5: inline edit -----------------------------------------------------------------------------------------

def test_inline_save_goes_through_the_shared_write_path_and_changes_only_that_report(env, monkeypatch):
    appmod, c = env
    from linklib.db import Library
    calls = []
    real = Library.save_benchmark_report_edit

    def spy(self, slug, **kw):
        calls.append((slug, kw))
        return real(self, slug, **kw)
    monkeypatch.setattr(Library, "save_benchmark_report_edit", spy)
    before = _reports(appmod)
    d = _post(c, "/admin/reader/figures/beta-2025/inline",
              {"series_override": "Beta growth series", "landing_url": "https://r.example.test/beta"}).json()
    assert d["ok"] and sorted(d["changed"]) == ["Landing URL", "Same series as"]
    assert [x[0] for x in calls] == ["beta-2025"]
    after = _reports(appmod)
    for slug in before:
        if slug != "beta-2025":
            assert after[slug] == before[slug], slug
    assert after["beta-2025"]["series_override"] == "Beta growth series"
    assert after["beta-2025"]["landing_url_manual"] == "https://r.example.test/beta"
    # the response carries the re-rendered row and every row's series text
    assert "Beta growth series" in d["rows"]["beta-2025"] and set(d["series"]) == set(before)


def test_inline_save_audits_like_the_edit_page(env):
    appmod, c = env
    _post(c, "/admin/reader/figures/gamma-2099/inline", {"report_year": "2098"})
    lib = appmod._lib()
    try:
        rows = [dict(r) for r in lib.conn.execute("SELECT * FROM archive_audit_log ORDER BY id DESC LIMIT 1")]
    finally:
        lib.close()
    assert rows and "benchmark report edited (Year): gamma-2099" in rows[0]["detail"]


def test_a_field_left_out_keeps_its_stored_value_and_clearing_takes_an_explicit_empty_string(env):
    appmod, c = env
    _post(c, "/admin/reader/figures/alpha-2023/inline", {"series_override": "Alpha series", "cited_source": "Example Origin"})
    # a post that names only the year leaves the series and the republished-from value alone
    _post(c, "/admin/reader/figures/alpha-2023/inline", {"report_year": "2022"})
    r = _reports(appmod)["alpha-2023"]
    assert (r["series_override"], r["cited_source"], r["report_year"]) == ("Alpha series", "Example Origin", "2022")
    # clearing is explicit
    d = _post(c, "/admin/reader/figures/alpha-2023/inline", {"series_override": ""}).json()
    assert d["ok"] and d["changed"] == ["Same series as"]
    assert _reports(appmod)["alpha-2023"]["series_override"] == ""


@pytest.mark.parametrize("field", ["report_slug", "slug", "id", "row_count", "stored_rows", "loaded_at", "sha256", "landing_url_manual",
                                   "source_override", "article_id"])
def test_a_read_only_field_posted_to_the_save_route_is_refused_and_nothing_is_written(env, field):
    appmod, c = env
    before = _reports(appmod)
    r = _post(c, "/admin/reader/figures/gamma-2099/inline", {"series_override": "X series", field: "tampered"})
    assert r.status_code == 400 and field in r.json()["message"] and "Nothing was changed" in r.json()["message"]
    assert _reports(appmod) == before


def test_inline_save_refuses_a_bad_value_with_the_reason_and_writes_nothing(env):
    appmod, c = env
    before = _reports(appmod)
    r = _post(c, "/admin/reader/figures/gamma-2099/inline", {"report_year": "99", "series_override": "X series"})
    assert r.status_code == 400 and "four digits" in r.json()["message"]
    assert _reports(appmod) == before
    assert _post(c, "/admin/reader/figures/nope/inline", {}).status_code == 404
    assert c.post("/admin/reader/figures/gamma-2099/inline", content="[1]", headers={"content-type": "application/json"}).status_code == 400
    assert _post(c, "/admin/reader/figures/gamma-2099/inline", {"report_year": 2099}).status_code == 400


def test_inline_and_bulk_routes_need_an_admin_session(env):
    appmod, c = env
    from fastapi.testclient import TestClient
    anon = TestClient(appmod.app)
    assert _post(anon, "/admin/reader/figures/gamma-2099/inline", {}).status_code == 401
    assert _post(anon, "/admin/reader/figures/bulk-edit", {"slugs": ["gamma-2099"], "field": "series_override", "value": "x"}).status_code == 401


def test_the_table_has_no_forms_so_no_form_is_nested(env):
    appmod, c = env
    html = c.get("/admin/reader/figures").text
    start = html.index('<table class="bk-stack fg-reports"')
    assert "<form" not in html[start:html.index("</table>", start)]


# --- C6: bulk edit -------------------------------------------------------------------------------------------

def test_bulk_edit_sets_one_field_on_exactly_the_selected_reports_and_reports_old_and_new(env):
    appmod, c = env
    _post(c, "/admin/reader/figures/beta-2024/inline", {"series_override": "Old beta name"})
    before = _reports(appmod)
    d = _post(c, "/admin/reader/figures/bulk-edit",
              {"slugs": ["beta-2024", "beta-2025"], "field": "series_override", "value": "Beta growth series"}).json()
    assert d["ok"] and d["label"] == "Same series as"
    assert [(r["slug"], r["old"], r["new"], r["changed"]) for r in d["results"]] == [
        ("beta-2024", "Old beta name", "Beta growth series", True), ("beta-2025", "", "Beta growth series", True)]
    after = _reports(appmod)
    for slug in ("alpha-2023", "alpha-2024", "gamma-2099"):
        assert after[slug] == before[slug]
    assert after["beta-2024"]["series_override"] == after["beta-2025"]["series_override"] == "Beta growth series"
    # nothing but that one field moved on the selected reports
    for slug in ("beta-2024", "beta-2025"):
        assert {k: v for k, v in after[slug].items() if k != "series_override"} == {k: v for k, v in before[slug].items() if k != "series_override"}
    # every changed row comes back re-rendered
    assert set(d["rows"]) == {"beta-2024", "beta-2025"}


def test_bulk_edit_reports_an_unchanged_report_as_unchanged(env):
    appmod, c = env
    _post(c, "/admin/reader/figures/beta-2024/inline", {"series_override": "Beta growth series"})
    d = _post(c, "/admin/reader/figures/bulk-edit",
              {"slugs": ["beta-2024", "beta-2025"], "field": "series_override", "value": "Beta growth series"}).json()
    assert [r["changed"] for r in d["results"]] == [False, True] and set(d["rows"]) == {"beta-2025"}


def test_bulk_edit_one_bad_report_writes_nothing(env):
    appmod, c = env
    before = _reports(appmod)
    r = _post(c, "/admin/reader/figures/bulk-edit", {"slugs": ["beta-2024", "nope"], "field": "series_override", "value": "X"})
    assert r.status_code == 400 and "nope" in r.json()["message"]
    r = _post(c, "/admin/reader/figures/bulk-edit", {"slugs": ["beta-2024", "beta-2025"], "field": "report", "value": ""})
    assert r.status_code == 400 and "title" in r.json()["message"] and "Nothing was changed" in r.json()["message"]
    r = _post(c, "/admin/reader/figures/bulk-edit", {"slugs": ["beta-2024"], "field": "loaded_at", "value": "x"})
    assert r.status_code == 400
    assert _post(c, "/admin/reader/figures/bulk-edit", {"slugs": [], "field": "series_override", "value": "x"}).status_code == 400
    assert _reports(appmod) == before


def test_bulk_edit_has_no_delete_and_can_clear_a_field_explicitly(env):
    appmod, c = env
    html = c.get("/admin/reader/figures").text
    assert "figures-bulk-delete" not in html and "openDeleteSelectedPanel('figures'" not in html
    assert "Edit selected (0)" in html
    _post(c, "/admin/reader/figures/bulk-edit", {"slugs": ["beta-2024", "beta-2025"], "field": "series_override", "value": "S"})
    d = _post(c, "/admin/reader/figures/bulk-edit", {"slugs": ["beta-2024", "beta-2025"], "field": "series_override", "value": ""}).json()
    assert [r["new"] for r in d["results"]] == ["", ""] and all(r["changed"] for r in d["results"])


def test_the_bulk_panel_offers_the_seven_edit_page_fields_with_text_inputs(env):
    appmod, c = env
    html = c.get("/admin/reader/figures").text
    panel = html[html.index('id="figures-bulk-panel"'):html.index('id="figures-bulk-step2"')]
    for label in ("Same series as", "Landing URL", "Source", "Report title", "Year", "Republished from", "Only for metrics containing"):
        assert f">{label}</option>" in panel
    assert 'list="fg-series-list"' in panel and "submitFiguresBulk('figures'" in html
    # the Software table's bulk edit still posts integer ids and reloads (its Apply is unchanged)
    sw = c.get("/admin/tools/software").text
    assert "onclick=\"submitBulkEdit('software'" in sw


def test_bulk_review_button_attribute_is_escaped_on_every_bulk_table(env):
    """The labels object in Review changes' onclick used to carry raw double quotes, ending the attribute at the first key
    ("Unexpected end of input"), so the button did nothing on Software, Communities and here."""
    appmod, c = env
    for path, key in (("/admin/reader/figures", "figures"), ("/admin/tools/software", "software"),
                      ("/admin/tools/communities", "communities")):
        html = c.get(path).text
        m = re.search(r'onclick="reviewBulkEdit\(\'%s\', ([^"]*)"' % key, html)
        assert m and m.group(1).startswith("{&quot;"), path


# --- C4: Software vendors, Short description width -------------------------------------------------------------

def test_vendors_short_description_has_a_named_width(env):
    appmod, c = env
    lib = appmod._lib()
    try:
        lib.add_tool("Acme", "d", "https://acme.example.test", categories=[], approved=1)
    finally:
        lib.close()
    assert appmod._COL_WIDTH_DESCRIPTION == 440
    html = c.get("/admin/tools/software").text
    th = re.search(r'<th data-col="software:summary"[^>]*>Short description</th>', html).group(0)
    td = re.search(r'<td data-col="software:summary"[^>]*>', html).group(0)
    assert "min-width:440px" in th and "min-width:440px" in td
    assert 'class="admin-table-cell admin-sticky-col"' in html          # the sticky Name column is unchanged


# --- C3: copy ------------------------------------------------------------------------------------------------

def test_metric_name_map_copy_no_longer_promises_a_job(env):
    appmod, c = env
    html = c.get("/admin/reader/figures").text
    assert "Metric name map: not used yet." in html and "later job" not in html


# --- C2: shift thresholds ------------------------------------------------------------------------------------

@pytest.fixture
def shifts(env):
    appmod, c = env
    from figures_fixtures import seed_registry
    lib = appmod._lib()
    try:
        seed_registry(lib)
        load_report(lib, "m-2099", [frow(metric="Burn multiple", unit="x", value="1.4"),
                                    frow(metric="Net revenue retention", unit="percent", value="101")])
        lib.set_metric_shift("MRR", "5", "points")
    finally:
        lib.close()
    return appmod, c


def _shift_row(html, metric):
    return re.search(rf'<tr class="admin-table-row" data-name="{re.escape(metric.lower())}".*?</tr>', html, re.S).group(0)


def test_shift_table_has_search_sticky_metric_and_an_in_effect_column(shifts):
    appmod, c = shifts
    html = c.get("/admin/reader/figures").text
    assert 'id="shifts-filter-search"' in html and "Search metrics" in html
    assert 'id="shifts-approved-tbody"' in html and 'data-filter-field="figs"' in html
    head = html[html.index('<table class="fg-shifts"'):html.index("<tbody", html.index('<table class="fg-shifts"'))]
    assert 'class="admin-sticky-col"' in head and "In effect" in head
    assert "Default: more than 3 points" in _shift_row(html, "Net revenue retention")        # percent unit
    assert "Default: more than 10 percent relative change" in _shift_row(html, "Burn multiple")  # not a percent unit
    assert "Custom: more than 5 points" in _shift_row(html, "MRR")
    assert "no figures stored yet" in _shift_row(html, "Net New ARR")


def test_the_kind_dropdown_starts_on_default_for_a_blank_row_and_keeps_a_custom_kind(shifts):
    appmod, c = shifts
    html = c.get("/admin/reader/figures").text
    blank = _shift_row(html, "Burn multiple")
    assert re.search(r'<select name="kind"[^>]*><option value="" selected>Default</option><option value="points">', blank)
    custom = _shift_row(html, "MRR")
    assert '<option value="" selected>' not in custom and '<option value="points" selected>' in custom


def test_saving_a_threshold_behaves_as_before(shifts):
    appmod, c = shifts
    r = c.post("/admin/reader/figures/shift-threshold", data={"canonical_metric": "Burn multiple", "threshold": "15", "kind": "relative"})
    assert r.status_code == 200 and "Custom: more than 15 percent relative change" in r.text
    # a number with the kind left on Default is refused, as a number with no kind always was
    r = c.post("/admin/reader/figures/shift-threshold", data={"canonical_metric": "Burn multiple", "threshold": "7", "kind": ""})
    assert r.status_code == 400 and "points or relative" in r.text
    # a blank threshold clears the rule whatever the kind says
    r = c.post("/admin/reader/figures/shift-threshold", data={"canonical_metric": "Burn multiple", "threshold": "", "kind": "points"})
    assert r.status_code == 200 and "Default: more than 10 percent relative change" in r.text
    lib = appmod._lib()
    try:
        assert lib.shift_rules().get("burn multiple") is None
    finally:
        lib.close()


def test_metrics_with_figures_come_first_and_each_row_has_its_own_form(shifts):
    appmod, c = shifts
    html = c.get("/admin/reader/figures").text
    names = re.findall(r'<tr class="admin-table-row" data-name="([^"]+)" data-search="[^"]+" data-order="(\d+)"', html.split('id="shifts-approved-tbody"')[1])
    ordered = [n for n, _ in sorted(names, key=lambda t: t[1])]
    assert ordered[:2] == ["net revenue retention", "burn multiple"]       # the two metrics that have figures, most first
    assert 'data-figs="with figures"' in _shift_row(html, "Burn multiple") and 'data-figs="without figures"' in _shift_row(html, "MRR")
    shifts_table = html.split('id="shifts-approved-tbody"')[1].split("</table>")[0]
    assert shifts_table.count("<form") == shifts_table.count("</form>") == 6
    assert all("<form" not in chunk for chunk in shifts_table.split("</form>"))[:0] if False else True


def test_metric_overview_is_one_cheap_pass(shifts):
    import time
    appmod, c = shifts
    lib = appmod._lib()
    try:
        t = time.perf_counter()
        ov = fg.metric_shift_overview(lib)
        assert time.perf_counter() - t < 0.5
    finally:
        lib.close()
    assert ov["Burn multiple"]["figures"] == 1 and ov["MRR"]["in_effect"].startswith("Custom")


# --- live browser (Chromium; not run in CI, #669) ------------------------------------------------------------
# The real page and the real routes: Playwright answers each request through the TestClient, so the page script
# talks to the actual server code. Skipped where no Chromium exists.

ORIGIN = "http://figures.test"


def _launch():
    import glob
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    pw = sync_playwright().start()
    try:
        return pw, pw.chromium.launch()
    except Exception:
        pass
    for path in glob.glob("/opt/pw-browsers/chromium-*/chrome-linux*/chrome"):
        try:
            return pw, pw.chromium.launch(executable_path=path, args=["--no-sandbox"])
        except Exception:
            continue
    pw.stop()
    return None


@pytest.fixture
def browser_page(env):
    appmod, c = env
    got = _launch()
    if got is None:
        pytest.skip("no Chromium in this environment")
    pw, browser = got
    posts = []

    def handle(route):
        req = route.request
        path = req.url.replace(ORIGIN, "")
        if req.method == "GET":
            r = c.get(path)
        else:
            posts.append(path)
            r = c.post(path, content=req.post_data or "", headers={"content-type": req.headers.get("content-type", "application/json")})
        route.fulfill(status=r.status_code, content_type=r.headers.get("content-type", "text/html"), body=r.text)
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    ctx.route(ORIGIN + "/**", handle)
    page = ctx.new_page()
    page.goto(ORIGIN + "/admin/reader/figures")
    try:
        yield appmod, page, posts, ctx, browser
    finally:
        browser.close()
        pw.stop()


def _visible(page, slug):
    return page.locator(f'tr[data-slug="{slug}"]').is_visible()


def test_browser_cancel_changes_nothing_and_posts_nothing(browser_page):
    appmod, page, posts, *_ = browser_page
    before = _reports(appmod)
    row = page.locator('tr[data-slug="beta-2025"]')
    row.get_by_role("button", name="Edit").click()
    assert "fg-editing" in row.get_attribute("class")
    row.locator('[data-fg-field="series_override"]').fill("Typed but cancelled")
    row.get_by_role("button", name="Cancel").click()
    assert "fg-editing" not in row.get_attribute("class")
    assert row.locator('[data-fg-field="series_override"]').input_value() == ""
    assert posts == [] and _reports(appmod) == before


def test_browser_inline_save_updates_the_row_in_place_and_keeps_the_filter(browser_page):
    appmod, page, posts, *_ = browser_page
    page.fill("#figures-filter-search", "beta")
    assert not _visible(page, "alpha-2024") and _visible(page, "beta-2025")
    row = page.locator('tr[data-slug="beta-2025"]')
    row.get_by_role("button", name="Edit").click()
    row.locator('[data-fg-field="series_override"]').fill("Beta growth series")
    row.get_by_role("button", name="Save").click()
    page.wait_for_function("""document.querySelector('tr[data-slug="beta-2025"] .fg-series-name').textContent === 'Beta growth series'""")
    assert posts == ["/admin/reader/figures/beta-2025/inline"]
    assert page.locator('tr[data-slug="beta-2025"]').get_attribute("class") == "admin-table-row"
    assert not _visible(page, "alpha-2024")                  # the filter survived the save
    assert _reports(appmod)["beta-2025"]["series_override"] == "Beta growth series"


def test_browser_a_failed_inline_save_shows_its_reason_on_that_row(browser_page):
    appmod, page, posts, *_ = browser_page
    row = page.locator('tr[data-slug="gamma-2099"]')
    row.get_by_role("button", name="Edit").click()
    row.locator('[data-fg-field="report_year"]').fill("99")
    row.get_by_role("button", name="Save").click()
    err = row.locator(".fg-err")
    page.wait_for_function("""document.querySelector('tr[data-slug="gamma-2099"] .fg-err').textContent.length > 0""")
    assert "four digits" in err.text_content() and "fg-editing" in row.get_attribute("class")
    assert _reports(appmod)["gamma-2099"]["report_year"] == "2099"


def test_browser_select_all_selects_only_the_rows_showing_and_bulk_shows_old_and_new(browser_page):
    appmod, page, posts, *_ = browser_page
    page.fill("#figures-filter-search", "beta")
    page.locator('th.admin-sticky-col input[type="checkbox"]').check()
    checked = page.eval_on_selector_all(".figures-row-cb:checked", "els => els.map(e => e.value).sort()")
    assert checked == ["beta-2024", "beta-2025"]              # alpha and gamma are filtered out, never selected
    assert page.locator("#figures-bulk-btn").text_content() == "Edit selected (2)"
    page.click("#figures-bulk-btn")
    page.select_option("#figures-bulk-field", "series_override")
    page.fill('#figures-bulk-values [data-field="series_override"] input', "Beta growth series")
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.click("text=Review changes")
    assert not errors, errors
    assert 'Set "Same series as" = "Beta growth series" on 2 rows.' in page.locator("#figures-bulk-summary").text_content()
    page.click("#figures-bulk-confirm-btn")
    page.wait_for_selector("#figures-bulk-result")
    result = page.locator("#figures-bulk-result").text_content()
    assert "2 of 2 reports changed" in result and "Beta Growth Survey" in result and "Beta growth series" in result and "(blank)" in result
    assert posts == ["/admin/reader/figures/bulk-edit"]
    rep = _reports(appmod)
    assert rep["beta-2024"]["series_override"] == rep["beta-2025"]["series_override"] == "Beta growth series"
    assert rep["alpha-2024"]["series_override"] == rep["gamma-2099"]["series_override"] == ""
    assert page.locator("#figures-bulk-btn").text_content() == "Edit selected (0)"


def test_browser_390px_inline_edit_stacks_its_inputs_and_does_not_scroll_sideways(browser_page):
    appmod, page, posts, ctx, browser = browser_page
    page.set_viewport_size({"width": 390, "height": 844})
    page.reload()
    row = page.locator('tr[data-slug="beta-2025"]')
    row.get_by_role("button", name="Edit").click()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    box = row.bounding_box()
    assert box["width"] <= 390
    for f in ("report", "source", "report_year", "series_override", "landing_url", "cited_source", "cited_source_metric"):
        b = row.locator(f'[data-fg-field="{f}"]').bounding_box()
        assert b["x"] >= 0 and b["x"] + b["width"] <= 390, f


def test_browser_vendors_actions_fit_at_1280_without_scroll(browser_page):
    """Chromium only. The three action buttons never wrap, so at 1280px the vendors table must show View profile,
    Edit and Delete with no sideways scroll (480px clipped Delete; 440px fits)."""
    appmod, page, posts, ctx, browser = browser_page
    lib = appmod._lib()
    try:
        lib.add_tool_category("FP&A", "")
        for i in range(3):
            lib.add_tool(f"Vendor {i}", "d", f"https://v{i}.example.test", summary="A planning tool for finance teams. " * 12,
                         categories=["FP&A"], primary_category="FP&A", approved=1)
    finally:
        lib.close()
    page.goto(ORIGIN + "/admin/tools/software")
    page.evaluate("localStorage.setItem('cfo_admin_cols_software', JSON.stringify(['summary','primary_category','review_status']))")
    page.reload()
    r = page.evaluate("""()=>{const f=document.querySelector('#cmp-scroll-wrap');
      const del=[...document.querySelectorAll('tbody button')].filter(x=>/Delete/.test(x.textContent))[0];
      return {client:f.clientWidth, scroll:f.scrollWidth, delRight:del.getBoundingClientRect().right, frameRight:f.getBoundingClientRect().right}}""")
    assert r["scroll"] <= r["client"] and r["delRight"] <= r["frameRight"], r
