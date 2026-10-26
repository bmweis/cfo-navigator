"""Primary use, public display (issue #624, PR 2).

Directory card shows the primary only, the profile badge is the primary with
one quiet "Also used for" line, the Compare header leads with each vendor's
Primary use, and an empty primary renders what it did before. The directory
filter still matches any tag.
"""
import json
import os
import re
import tempfile

import pytest

from linklib import tool_labels


@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib
    import webapp.app as appmod
    importlib.reload(appmod)
    from linklib.db import Library
    lib = Library(db)
    ids = {}
    ids["solo"] = lib.add_tool("Soloco", "d", "https://solo.example", ["ERP"], approved=1, primary_category="ERP")
    ids["multi"] = lib.add_tool("Stripeish", "d", "https://stripeish.example",
                                ["Billing Solutions", "Tax Management"], approved=1,
                                primary_category="Tax Management")
    ids["empty"] = lib.add_tool("Legacyco", "d", "https://legacy.example", ["FP&A", "ERP"], approved=1)
    lib.close()
    yield appmod, ids
    if os.path.exists(db):
        os.remove(db)


def _client(appmod):
    from fastapi.testclient import TestClient
    return TestClient(appmod.app, raise_server_exceptions=True)


def _all_tools(html):
    m = re.search(r"ALL_TOOLS\s*=\s*(\[.*?\]);\s*\n", html, re.S)
    assert m, "ALL_TOOLS not found"
    return {t["name"]: t for t in json.loads(m.group(1))}


def _slug(appmod, name):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    try:
        return next(t["slug"] for t in lib.list_tools() if t["name"] == name)
    finally:
        lib.close()


# --- directory ---------------------------------------------------------------

def test_directory_payload_carries_primary_and_full_set(env):
    appmod, _ = env
    tools = _all_tools(_client(appmod).get("/tools/software").text)
    assert tools["Stripeish"]["primary_category"] == "Tax Management"
    assert sorted(tools["Stripeish"]["categories"]) == ["Billing Solutions", "Tax Management"]
    assert tools["Legacyco"]["primary_category"] == ""


def test_directory_card_js_renders_primary_only_with_fallback(env):
    appmod, _ = env
    html = _client(appmod).get("/tools/software").text
    assert "t.primary_category ? [t.primary_category] : (t.categories || [])" in html


def test_directory_filter_still_matches_any_tag(env):
    appmod, _ = env
    html = _client(appmod).get("/tools/software").text
    # filtering and search read the full set, not the primary
    assert "var cats = t.categories || [];" in html
    assert "(t.categories || []).join(' ')" in html


# --- profile -----------------------------------------------------------------

def test_profile_multi_tag_shows_primary_badge_and_also_used_line(env):
    appmod, _ = env
    html = _client(appmod).get(f"/tools/software/{_slug(appmod, 'Stripeish')}").text
    assert '<span class="tp-cat-pill">Tax Management</span>' in html
    assert f'{tool_labels.ALSO_USED_FOR}: Billing Solutions' in html
    assert '<span class="tp-cat-pill">Billing Solutions</span>' not in html


def test_profile_single_tag_has_no_also_used_line(env):
    appmod, _ = env
    html = _client(appmod).get(f"/tools/software/{_slug(appmod, 'Soloco')}").text
    assert '<span class="tp-cat-pill">ERP</span>' in html
    assert '<span class="tp-also-used">' not in html


def test_profile_empty_primary_falls_back_to_full_set(env):
    appmod, _ = env
    html = _client(appmod).get(f"/tools/software/{_slug(appmod, 'Legacyco')}").text
    assert '<span class="tp-cat-pill">ERP</span>' in html
    assert '<span class="tp-cat-pill">FP&amp;A</span>' in html
    assert '<span class="tp-also-used">' not in html


# --- compare -----------------------------------------------------------------

def test_compare_header_shows_each_primary(env):
    appmod, ids = env
    html = _client(appmod).get(f"/tools/software/compare?ids={ids['solo']},{ids['multi']}").text
    assert f'{tool_labels.PRIMARY_USE}:</span> <span class="cmp-primary-value">ERP</span>' in html
    assert f'{tool_labels.PRIMARY_USE}:</span> <span class="cmp-primary-value">Tax Management</span>' in html


def test_compare_empty_primary_renders_as_before(env):
    appmod, ids = env
    r = _client(appmod).get(f"/tools/software/compare?ids={ids['empty']},{ids['solo']}")
    assert r.status_code == 200
    assert r.text.count('<div class="cmp-primary">') == 1  # only Soloco's


# --- rendering (Chromium; skips where unavailable, as CI does) ---------------

def test_directory_card_shows_primary_only_in_browser(env, tmp_path):
    appmod, _ = env
    pw = pytest.importorskip("playwright.sync_api")
    html = _client(appmod).get("/tools/software").text
    p = pw.sync_playwright().start()
    try:
        try:
            b = p.chromium.launch()
        except Exception:
            pytest.skip("no Chromium")
        page = b.new_page(viewport={"width": 390, "height": 800})
        page.route("**/*", lambda route: route.abort() if not route.request.url.startswith(("data:", "about:", "http://t.test")) else route.continue_())
        page.route("http://t.test/**", lambda route: route.fulfill(body=html, content_type="text/html"))
        page.goto("http://t.test/tools/software")
        page.wait_for_selector(".tool-card")
        cards = {c.query_selector(".tool-name").inner_text().strip(): [e.inner_text() for e in c.query_selector_all(".tool-cat")]
                 for c in page.query_selector_all(".tool-card")}
        assert cards["Stripeish"] == ["Tax Management"]
        assert cards["Soloco"] == ["ERP"]
        assert sorted(cards["Legacyco"]) == ["ERP", "FP&A"]
        assert page.evaluate("document.documentElement.scrollWidth") <= 390
        b.close()
    finally:
        p.stop()
