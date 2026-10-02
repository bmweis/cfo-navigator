"""Sticky-column tables inside a `.table-frame` wrapper.

The sitewide rule `.site-main .table-frame>table{border:0;overflow:visible}`
was meant to drop the table's own border and clipping (a clipping table
breaks position:sticky) and let the wrapper carry the frame. Its specificity
was lower than the generic table rule's (the generic rule carries two :not()
terms), so it never applied: every framed table kept overflow:hidden and its
own 1px border (a double frame), and the sticky Name column on
/admin/tools/software and /admin/tools/communities scrolled away with the
rest of the row. Compare had a local patch for the overflow only.

Measured engine: Chromium only (skips where none exists, as in CI)."""
import importlib
import os
import tempfile

import pytest

_FRAMED_ROUTES = [
    "/admin/inbox/contact-submissions", "/admin/tools/software", "/admin/inbox/toolbox-intros",
    "/admin/thought-leadership/third-party", "/admin/thought-leadership/original",
    "/admin/tools/communities", "/admin/reader/feeds", "/admin/overhead-spend/details",
    "/admin/users", "/tools/software/compare?ids=1,2",
]


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
    lib.add_tool("Acme", "Does things.", "https://acme.example", ["FP&A"], approved=1)
    lib.add_tool("Beta", "Does more.", "https://beta.example", ["FP&A"], approved=1)
    lib.add_community("Guild", "https://guild.example", "CFOs", "Free", ["Peer"], approved=1)
    lib.close()
    yield client, appmod, db
    if os.path.exists(db):
        os.remove(db)


def _browser():
    try:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        return pw, pw.chromium.launch()
    except Exception:
        return None


@pytest.mark.parametrize("route", _FRAMED_ROUTES)
def test_framed_table_does_not_clip_or_double_its_border(env, tmp_path, route):
    launched = _browser()
    if launched is None:
        pytest.skip("Chromium not installed in this environment")
    pw, browser = launched
    try:
        client, appmod, db = env
        f = tmp_path / "t.html"
        f.write_text(client.get(route).text, encoding="utf-8")
        page = browser.new_page(viewport={"width": 900, "height": 900})
        page.goto(f.as_uri())
        got = page.evaluate("""() => { const t = document.querySelector('.table-frame > table');
            const cs = getComputedStyle(t);
            return {overflow: cs.overflow, border: cs.borderTopWidth}; }""")
        assert got == {"overflow": "visible", "border": "0px"}, (route, got)
    finally:
        browser.close()
        pw.stop()


@pytest.mark.parametrize("route", ["/admin/tools/software", "/admin/tools/communities"])
def test_sticky_name_column_stays_put_while_the_table_scrolls(env, tmp_path, route):
    launched = _browser()
    if launched is None:
        pytest.skip("Chromium not installed in this environment")
    pw, browser = launched
    try:
        client, appmod, db = env
        f = tmp_path / "t.html"
        f.write_text(client.get(route).text, encoding="utf-8")
        page = browser.new_page(viewport={"width": 900, "height": 900})
        page.goto(f.as_uri())
        got = page.evaluate("""() => {
            const w = document.getElementById('cmp-scroll-wrap');
            const el = [...w.querySelectorAll('.admin-sticky-col')]
              .filter(e => getComputedStyle(e).position === 'sticky').pop();
            if (w.scrollWidth <= w.clientWidth) return {noOverflow: true};
            w.scrollLeft = 120;
            const wl = w.getBoundingClientRect().left;
            return {scrolled: w.scrollLeft, offsetInWrap: Math.round(el.getBoundingClientRect().left - wl)}; }""")
        assert not got.get("noOverflow"), "table no longer overflows at 900px; pick a narrower viewport"
        assert got["scrolled"] > 0
        assert got["offsetInWrap"] >= 0, got
    finally:
        browser.close()
        pw.stop()
