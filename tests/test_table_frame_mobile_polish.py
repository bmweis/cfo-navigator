"""Framed admin tables on a phone and in landscape (Refs 655, B1).

Four things measured in real Chromium, from the numbers recorded before the fix:
- the three bare tables Brian saw (pending submissions on Software and
  Communities, the contact deletion history) drew their own border, so the
  right border scrolled out of view at 390px; they now sit in a .table-frame;
- in the stacked-card layout the checkbox was a row of its own, every cell kept
  a 1px top border (the media rule meant to remove it lost on specificity) and
  cards were separated by a 2px rule;
- the sticky Name pair was 37% (Software) and 44.5% (Communities) of an 874px
  viewport; the checkbox now lives inside the Name cell and the pinned column
  is a named 230px;
- bulk select still works with the merged cell.

Measured engine: Chromium only (skips where none exists, as in CI)."""
import importlib
import os
import tempfile

import pytest


@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    client = TestClient(appmod.app)
    client.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    lib = appmod.Library(db)
    for n, u in (("Abacum", "https://abacum.example"), ("Accruals.ai", "https://accruals.example"),
                 ("Beyond the Books", "https://btb.example")):
        lib.add_tool(n, f"{n} helps finance teams.", u, ["FP&A"], approved=1)
    lib.add_tool("Pending Tool", "Waiting.", "https://pending-tool.example", ["FP&A"], approved=0)
    for n, u in (("AFP", "https://afp.example"), ("Beyond the Books", "https://btbc.example"),
                 ("CFO Alliance", "https://alliance.example")):
        lib.add_community(n, u, "Finance leaders", "Free", ["Peer"], approved=1)
    lib.add_community("Pending Community", "https://pending-comm.example", "Controllers", "Paid", ["Peer"], approved=0)
    lib.record_contact_audit(None, "delete", 1, "test")
    lib.close()
    yield client, appmod, db
    if os.path.exists(db):
        os.remove(db)


def _browser():
    try:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
    except Exception:
        return None
    try:
        return pw, pw.chromium.launch()
    except Exception:
        pw.stop()
        return None


def _page(env, tmp_path, route, width, height=900):
    launched = _browser()
    if launched is None:
        pytest.skip("Chromium not installed in this environment")
    pw, browser = launched
    client, appmod, db = env
    f = tmp_path / "t.html"
    f.write_text(client.get(route).text, encoding="utf-8")
    page = browser.new_page(viewport={"width": width, "height": height})
    page.goto(f.as_uri())
    page.wait_for_timeout(150)
    return pw, browser, page


@pytest.mark.parametrize("route,heading", [
    ("/admin/tools/communities", "Pending submissions"),
    ("/admin/tools/software", "Pending submissions"),
    ("/admin/inbox/contact-submissions", "Deletion history"),
])
def test_bare_tables_keep_their_right_border_inside_the_frame_at_390(env, tmp_path, route, heading):
    pw, browser, page = _page(env, tmp_path, route, 390)
    try:
        got = page.evaluate("""(heading) => {
            const h = [...document.querySelectorAll('h2')].find(e => e.textContent.trim() === heading);
            const wrap = h.nextElementSibling; const t = wrap.querySelector('table');
            const vw = document.documentElement.clientWidth;
            return {isFrame: wrap.classList.contains('table-frame'),
                    frameRight: Math.round(wrap.getBoundingClientRect().right), vw,
                    frameBorder: getComputedStyle(wrap).borderRightWidth,
                    tableBorder: getComputedStyle(t).borderRightWidth,
                    tableWider: t.offsetWidth > wrap.clientWidth}; }""", heading)
        assert got["isFrame"], got
        assert got["frameRight"] <= got["vw"], got
        assert got["frameBorder"] == "1px" and got["tableBorder"] == "0px", got
    finally:
        browser.close()
        pw.stop()


@pytest.mark.parametrize("route", ["/admin/tools/software", "/admin/tools/communities"])
def test_stacked_card_has_no_checkbox_only_row_and_no_hairlines(env, tmp_path, route):
    pw, browser, page = _page(env, tmp_path, route, 390)
    try:
        got = page.evaluate("""() => {
            const t = document.querySelector('#cmp-scroll-wrap table');
            const rows = [...t.tBodies[0].rows];
            const cells = [...rows[0].cells].filter(c => getComputedStyle(c).display !== 'none');
            return {
              checkboxOnly: cells.filter(c => c.querySelector('input[type=checkbox]') && !c.innerText.trim()).length,
              topBorders: cells.filter(c => getComputedStyle(c).borderTopWidth !== '0px').length,
              rowBorder: getComputedStyle(rows[0]).borderBottomWidth,
              lastRowBorder: getComputedStyle(rows[rows.length - 1]).borderBottomWidth}; }""")
        assert got["checkboxOnly"] == 0, got
        assert got["topBorders"] == 0, got
        assert got["rowBorder"] == "1px", got
        assert got["lastRowBorder"] == "0px", got
    finally:
        browser.close()
        pw.stop()


@pytest.mark.parametrize("route", ["/admin/tools/software", "/admin/tools/communities"])
def test_sticky_name_is_a_narrow_share_of_a_landscape_phone(env, tmp_path, route):
    pw, browser, page = _page(env, tmp_path, route, 874, 700)
    try:
        page.evaluate("""() => document.querySelectorAll('input[type=checkbox][onchange*=toggleColumn]')
            .forEach(c => { if (!c.checked) c.click(); })""")
        got = page.evaluate("""() => {
            const w = document.getElementById('cmp-scroll-wrap');
            const sticky = [...w.querySelector('table').tHead.rows[0].cells].filter(c => getComputedStyle(c).position === 'sticky');
            const width = sticky.reduce((a, c) => a + c.getBoundingClientRect().width, 0);
            return {count: sticky.length, share: width / innerWidth, scrolls: w.scrollWidth > w.clientWidth}; }""")
        assert got["scrolls"], got
        assert got["count"] == 1, got
        assert got["share"] <= 0.30, got
    finally:
        browser.close()
        pw.stop()


@pytest.mark.parametrize("route,key", [("/admin/tools/software", "software"), ("/admin/tools/communities", "communities")])
def test_bulk_select_still_works_with_the_checkbox_inside_the_name_cell(env, tmp_path, route, key):
    pw, browser, page = _page(env, tmp_path, route, 1280)
    try:
        total = page.evaluate(f"document.querySelectorAll('.{key}-row-cb').length")
        assert total >= 3
        page.locator(f"#cmp-scroll-wrap thead input[type=checkbox]").first.click()
        assert page.evaluate(f"document.querySelectorAll('.{key}-row-cb:checked').length") == total
        assert f"({total})" in page.locator(f"#{key}-bulk-btn").inner_text()
        page.locator(f".{key}-row-cb").first.click()
        assert page.evaluate(f"document.querySelectorAll('.{key}-row-cb:checked').length") == total - 1
        assert f"({total - 1})" in page.locator(f"#{key}-bulk-btn").inner_text()
        # the name link sits in the same cell as the checkbox
        same = page.evaluate(f"""() => {{ const cb = document.querySelector('.{key}-row-cb');
            return !!cb.closest('td').querySelector('a'); }}""")
        assert same
    finally:
        browser.close()
        pw.stop()


def test_named_sticky_width_constant_and_single_sticky_class(env):
    client, appmod, db = env
    assert appmod._COL_WIDTH_NAME_STICKY == 230
    for route in ("/admin/tools/software", "/admin/tools/communities"):
        html = client.get(route).text
        assert "admin-sticky-col-1" not in html and "admin-sticky-col-2" not in html
