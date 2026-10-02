"""/admin/fpa-buddy/report: the Question column was pinned near 100px at
half-desktop widths. Cause (measured, Chromium): the Asker column used
_COL_WIDTH_NAME (280px, calibrated for software and community names) to hold
a short username; with Date 140, a nowrap Settings badge 186 and Cost 79 that
left Question 164px at 900px. Asker now uses the short-label width.
Measured engine: Chromium only."""
import importlib
import os
import tempfile

import pytest


def _browser():
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


def _seed(appmod, db):
    lib = appmod.Library(db)
    for q in ("How do I build a driver-based headcount plan for a 200-person SaaS company?",
              "What is NRR?"):
        lib.record_ask_question(1, q, "a", "claude-sonnet-5", "standard", True, True, True, cost_usd=0.12)
    lib.close()


def test_asker_header_uses_the_short_label_width(admin_client):
    client, appmod, db = admin_client
    _seed(appmod, db)
    html = client.get("/admin/fpa-buddy/report").text
    assert f"width:{appmod._COL_WIDTH_VENDOR}px;\">Asker</th>" in html
    assert f"width:{appmod._COL_WIDTH_NAME}px;\">Asker</th>" not in html


@pytest.mark.parametrize("width,minimum", [(900, 260), (800, 170)])
def test_question_column_keeps_room_at_half_desktop_widths(admin_client, tmp_path, width, minimum):
    launched = _browser()
    if launched is None:
        pytest.skip("Chromium not installed in this environment")
    pw, browser = launched
    try:
        client, appmod, db = admin_client
        _seed(appmod, db)
        f = tmp_path / "r.html"
        f.write_text(client.get("/admin/fpa-buddy/report").text, encoding="utf-8")
        page = browser.new_page(viewport={"width": width, "height": 900})
        page.goto(f.as_uri())
        w = page.evaluate("""() => [...document.querySelectorAll('th')]
            .find(t => t.textContent.trim() === 'Question').getBoundingClientRect().width""")
        page.close()
        assert w >= minimum, f"Question column is {w}px at {width}px viewport"
    finally:
        browser.close()
        pw.stop()
