"""Both public directories show one full page of 12 cards.

The Communities directory carried its own page-size literal (10) while the
Software directory used 12, so page 1 of Communities rendered 10 cards and
left a ragged last row. Both pages now read one Python constant.
"""
import os
import tempfile

import pytest


@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib
    import webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    client = TestClient(appmod.app)
    yield client, appmod
    if os.path.exists(db):
        os.remove(db)


def _launch():
    try:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        try:
            return pw, pw.chromium.launch()
        except Exception:
            # The sandbox's pre-baked browser can lag the installed Playwright.
            import glob
            found = glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome")
            if not found:
                raise
            return pw, pw.chromium.launch(executable_path=found[0])
    except Exception:
        return None


def test_both_directories_read_the_shared_constant(env):
    client, appmod = env
    assert appmod._DIRECTORY_PAGE_SIZE == 12
    comm = client.get("/tools/communities").text
    soft = client.get("/tools/software").text
    assert "var COMM_PAGE_SIZE = 12;" in comm
    assert "var PAGE_SIZE = 12;" in soft


def test_community_page_one_renders_twelve_cards(env):
    client, appmod = env
    lib = appmod._lib()
    try:
        for i in range(15):
            lib.add_community(f"Community {i:02d}", f"https://c{i}.example.com", "CFOs", "Free", [], approved=1)
    finally:
        lib.close()
    html = client.get("/tools/communities").text
    launched = _launch()
    if launched is None:
        pytest.skip("no Chromium available")
    pw, browser = launched
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.set_content(html, wait_until="domcontentloaded")
        page.wait_for_selector(".comm-card")
        assert len(page.query_selector_all(".comm-card")) == 12
    finally:
        browser.close()
        pw.stop()
