"""Six admin grids carried a bare `1fr` track (or `2fr 1fr`) around cells that
hold form controls. A bare `1fr` track has an implicit minimum of `auto`
(the grid item's min-content), so one cell with a wide unbreakable child
widens its track and pushes the whole grid past its container. This is the
same CSS Grid blowout the overhead-spend forms hit (see
tests/test_overhead_spend_grid_regression.py), found latent in:

  .tool-form-cols   (Software/Community edit and add pages, sitewide CSS)
  .qe-row           (Software directory Quick edit panel)
  .users-top-grid   (/admin/users, top row)
  the Users add-member form grid
  the Resources form Coverage/Pricing grid
  the Third-party content form Source/venue and Date label grid

Fix: every track is `minmax(0,...)`. The behavior test injects a 1,200px
unbreakable child into each cell of the REAL rendered page and asserts no
cell ends up wider than the grid, in real Chromium (skips where no Chromium
exists, as in CI). Measured engine: Chromium only; WebKit is unverified here.
"""
import importlib
import os
import tempfile

import pytest


@pytest.fixture
def admin_client(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    client = TestClient(appmod.app)
    client.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    yield client, appmod, db
    if os.path.exists(db):
        os.remove(db)


def _launch_chromium():
    """(playwright, chromium) or None. Stops Playwright when the launch fails:
    a started sync Playwright leaves an asyncio loop running in this thread,
    which breaks every later test that calls asyncio.run() (the MCP tests)."""
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


# Each entry: (label, url, JS expression returning the grid element).
_GRIDS = [
    ("tool-form-cols", "/admin/tools/software/new",
     "document.querySelector('.tool-form-cols')"),
    ("users-top-grid", "/admin/users",
     "document.querySelector('.users-top-grid')"),
    ("users-add-member", "/admin/users",
     "document.querySelector('form[action=\"/admin/users/create\"]')"),
    ("resources-coverage-pricing", "/admin/tools/resources/new",
     "document.querySelector('select[name=coverage]').parentElement.parentElement"),
    ("third-party-venue-date", "/admin/thought-leadership/third-party/new",
     "document.querySelector('input[name=venue]').parentElement.parentElement"),
]

_PROBE = """([getter, w]) => {
  const grid = eval(getter);
  if (!grid) return {missing: true};
  const kids = [...grid.children];
  kids.forEach(k => {
    const d = document.createElement('div');
    d.style.cssText = 'white-space:nowrap;width:max-content';
    d.textContent = 'x'.repeat(200);
    d.style.fontSize = '8px'; d.style.display = 'block';
    const s = document.createElement('span');
    s.style.cssText = 'display:inline-block;width:1200px;height:4px';
    d.appendChild(s);
    k.appendChild(d);
  });
  // Measure against the grid's PARENT: a blown-out track can also grow the
  // grid box itself (shrink-to-fit ancestors), so comparing a cell to its own
  // grid hides the bug.
  const bound = (grid.parentElement || document.documentElement).getBoundingClientRect();
  const g = grid.getBoundingClientRect();
  const over = [g, ...kids.map(k => k.getBoundingClientRect())]
    .map(r => Math.round(r.right - bound.right)).filter(x => x > 1);
  return {gridW: Math.round(g.width), parentW: Math.round(bound.width), over, tracks: getComputedStyle(grid).gridTemplateColumns};
}"""


@pytest.mark.parametrize("label,url,getter", _GRIDS)
@pytest.mark.parametrize("width", [1280, 390])
def test_wide_cell_content_cannot_widen_a_grid_track(admin_client, tmp_path, label, url, getter, width):
    launched = _launch_chromium()
    if launched is None:
        pytest.skip("Chromium not installed in this environment")
    pw, browser = launched
    try:
        client, appmod, db = admin_client
        resp = client.get(url)
        assert resp.status_code == 200, (url, resp.status_code)
        f = tmp_path / "p.html"
        f.write_text(resp.text, encoding="utf-8")
        page = browser.new_page(viewport={"width": width, "height": 900})
        page.goto(f.as_uri())
        out = page.evaluate(_PROBE, [getter, width])
        page.close()
        assert not out.get("missing"), f"{label}: grid not found on {url}"
        assert out["over"] == [], f"{label} @ {width}px: the grid or a cell overflows its parent by {out['over']}px; tracks={out['tracks']}"
    finally:
        browser.close()
        pw.stop()


def test_quick_edit_row_cannot_be_widened_by_a_cell(admin_client, tmp_path):
    """.qe-row is built client-side when Quick edit opens, so only its CSS is in
    the served page. Reuse the real CSS with a synthetic row."""
    launched = _launch_chromium()
    if launched is None:
        pytest.skip("Chromium not installed in this environment")
    pw, browser = launched
    try:
        client, appmod, db = admin_client
        lib = appmod.Library(db)
        lib.add_tool("Acme", "Does things.", "https://acme.example", ["FP&A"], approved=1)
        lib.close()
        resp = client.get("/tools/software")
        assert ".qe-row" in resp.text
        html = resp.text.replace("</body>", '<div id="host" style="width:340px"><div class="qe-row"><div><input style="width:100%"></div><div><input style="width:100%"></div></div></div></body>')
        f = tmp_path / "q.html"
        f.write_text(html, encoding="utf-8")
        page = browser.new_page(viewport={"width": 390, "height": 900})
        page.goto(f.as_uri())
        out = page.evaluate(_PROBE, ["document.querySelector('#host .qe-row')", 390])
        page.close()
        assert out["over"] == [], out
    finally:
        browser.close()
        pw.stop()


@pytest.mark.parametrize("needle", [
    ".tool-form-cols{display:grid;grid-template-columns:minmax(0,2fr) minmax(0,1fr)",
    ".qe-row{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr)",
])
def test_css_declares_zero_minimum_tracks(admin_client, needle):
    client, appmod, db = admin_client
    src = open(appmod.__file__, encoding="utf-8").read().replace("{{", "{").replace("}}", "}")
    assert needle in src
