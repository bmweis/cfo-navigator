"""FP&A Buddy top box: visible headers, controls above the text box, an icon send
button inside the box, and the dollar estimate in the Depth options.

Live Chromium through the route-handler harness of test_buddy_phone_fixes (no
server, no model); skips where no Chromium exists (CI). Geometry is measured, not
read from the CSS.
"""
import importlib
import os
import re
import tempfile

import pytest

from tests import test_buddy_phone_fixes as T
from linklib.db import Library

ORIGIN, _launch, Site = T.ORIGIN, T._launch, T.Site
buddy_html = T.buddy_html   # a signed-in non-admin reader


@pytest.fixture
def admin_html(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    monkeypatch.setenv("EXA_API_KEY", "test-key")
    lib = Library(db)
    lib.seed_voice_prompts()
    lib.create_user("boss", "supersecret", role="admin")
    lib.close()
    import webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app)
    assert c.post("/login", data={"username": "boss", "password": "supersecret"},
                  follow_redirects=False).status_code in (302, 303)
    yield c.get("/tools/fpa-buddy").text
    for ext in ("", "-shm", "-wal"):
        if os.path.exists(db + ext):
            os.remove(db + ext)


def _page(html, width, height=900):
    launched = _launch()
    if launched is None:
        pytest.skip("no Chromium available")
    pw, browser = launched
    ctx = browser.new_context(viewport={"width": width, "height": height},
                              is_mobile=width < 500, has_touch=width < 500)
    pg = ctx.new_page()
    site = Site(html)
    pg.route(re.compile(r"^http://buddy\.test/.*"), site.handle)
    pg.route(re.compile(r"^https?://(?!buddy\.test).*"), lambda r: r.abort())
    pg.goto(ORIGIN + "/tools/fpa-buddy")
    pg.wait_for_selector("#ask-q")
    return pw, browser, pg


def _box(pg, sel):
    return pg.evaluate("""(s) => { var r = document.querySelector(s).getBoundingClientRect();
      return {l: r.left, r: r.right, t: r.top, b: r.bottom, w: r.width, h: r.height}; }""", sel)


@pytest.fixture(params=[390, 1280])
def admin_pg(request, admin_html):
    pw, browser, pg = _page(admin_html, request.param)
    yield pg
    browser.close()
    pw.stop()


@pytest.fixture(params=[390, 1280])
def member_pg(request, buddy_html):
    pw, browser, pg = _page(buddy_html[0], request.param)
    yield pg
    browser.close()
    pw.stop()


def test_depth_and_sources_have_visible_headers_that_are_real_labels(admin_pg):
    pg = admin_pg
    for name in ("Depth", "Sources"):
        col = pg.locator("#ask-dd-top label.ask-dd-col", has=pg.locator(".ask-dd-h", has_text=name)).first
        assert col.is_visible()
        assert col.locator(".ask-dd-h").inner_text().strip().lower() == name.lower()
        assert col.locator("button.ask-dd-btn").count() == 1          # the label wraps its control
    d, s = _box(pg, "#ask-dd-top [data-dd='depth'].ask-dd-btn"), _box(pg, "#ask-dd-top [data-dd='sources'].ask-dd-btn")
    dh, sh = _box(pg, "#ask-dd-top .ask-dd-col:nth-child(1) .ask-dd-h"), _box(pg, "#ask-dd-top .ask-dd-col:nth-child(2) .ask-dd-h")
    assert dh["b"] <= d["t"] and sh["b"] <= s["t"]                      # header above its control
    if pg.viewport_size["width"] > 640:
        assert abs(d["t"] - s["t"]) < 1 and d["r"] <= s["l"]            # two columns
    else:
        assert d["b"] <= sh["t"] and abs(d["l"] - s["l"]) < 1           # stacked
    # The closed button shows only the value (no "Depth:" prefix in the top box).
    assert not pg.is_visible("#ask-dd-top .ask-dd-k")


def test_controls_sit_between_the_label_and_the_text_box(member_pg):
    pg = member_pg
    label = _box(pg, "label[for='ask-q']")
    controls = _box(pg, "#ask-dd-top")
    box = _box(pg, "#ask-q")
    assert label["b"] <= controls["t"] and controls["b"] <= box["t"]


def test_a_depth_panel_opens_directly_under_its_own_button(admin_pg):
    pg = admin_pg
    pg.click("#ask-dd-top [data-dd='depth'].ask-dd-btn")
    btn, panel = _box(pg, "#ask-dd-top [data-dd='depth'].ask-dd-btn"), _box(pg, "#ask-dd-top .ask-dd-panel[data-dd='depth']")
    assert panel["t"] >= btn["b"] and panel["t"] - btn["b"] < 24
    assert panel["b"] <= _box(pg, "#ask-q")["t"]
    if pg.viewport_size["width"] <= 640:                                 # Sources header stays below it
        assert panel["b"] <= _box(pg, "#ask-dd-top .ask-dd-col:nth-child(2) .ask-dd-h")["t"]


def test_send_is_an_up_arrow_icon_inside_the_box_with_a_44px_hit_area(member_pg):
    pg = member_pg
    b = _box(pg, "#ask-btn")
    assert pg.get_attribute("#ask-btn", "aria-label") == "Ask" and pg.get_attribute("#ask-btn", "title") == "Ask"
    assert pg.inner_text("#ask-btn").strip() == ""
    assert abs(b["w"] - 44) < 1 and abs(b["h"] - 44) < 1
    dot = _box(pg, "#ask-btn .fu-send-dot")
    assert abs(dot["w"] - 36) < 1
    t = _box(pg, "#ask-q")
    assert t["l"] <= b["l"] and b["r"] <= t["r"] and t["t"] <= b["t"] and b["b"] <= t["b"]
    assert t["r"] - b["r"] < 12 and t["b"] - b["b"] < 12                 # bottom-right corner
    assert pg.evaluate("getComputedStyle(document.getElementById('ask-q')).fontSize") == "16px"
    assert pg.is_disabled("#ask-btn")
    pg.fill("#ask-q", "A question")
    assert pg.is_enabled("#ask-btn")
    pg.fill("#ask-q", "   ")
    assert pg.is_disabled("#ask-btn")


def test_send_is_disabled_while_a_question_runs_and_enter_still_does_not_send(member_pg):
    pg = member_pg
    pg.fill("#ask-q", "First line")
    pg.press("#ask-q", "Enter")                                          # a newline, not a send
    assert pg.evaluate("document.getElementById('ask-q').value") == "First line\n"
    assert pg.locator("#ask-thread .ask-q-bubble").count() == 0
    pg.click("#ask-btn")
    pg.wait_for_selector("#fu-q")
    assert pg.locator("#ask-thread .ask-q-bubble").count() == 1


def test_admin_sees_the_estimate_in_the_depth_options_and_on_the_button(admin_pg):
    pg = admin_pg
    cost, exa = pg.evaluate("[COST, EXA_UNIT]")
    assert exa > 0 and set(cost) == {"quick", "standard", "deep"}
    for tier, label in (("quick", "Quick"), ("standard", "Standard"), ("deep", "Deep")):
        want = "($%.3f)" % (cost[tier] + exa)                              # Web is on by default
        assert pg.inner_text("#ask-dd-top .ask-tag[data-tier='%s'] .ask-tag-cost" % tier) == want
        assert pg.inner_text("#ask-dd-top .ask-tag[data-tier='%s'] .ask-tag-name" % tier) == label
    assert pg.inner_text("#ask-dd-top [data-dd='depth'] .ask-dd-v") == "Standard ($%.3f)" % (cost["standard"] + exa)
    assert "per query" not in pg.inner_text("body")
    assert pg.inner_text(".ask-cost-note").strip() == "Dollar amounts are estimates."
    # Turning Web off removes the Exa call from every figure.
    pg.click("#ask-dd-top [data-dd='sources'].ask-dd-btn")
    pg.click("#ask-dd-top .ask-tag[data-source='web']")
    assert pg.inner_text("#ask-dd-top [data-dd='depth'] .ask-dd-v") == "Standard ($%.3f)" % cost["standard"]
    pg.click("#ask-dd-top .ask-tag[data-source='feed']")                  # the RSS feed makes no Exa call
    assert pg.inner_text("#ask-dd-top [data-dd='depth'] .ask-dd-v") == "Standard ($%.3f)" % cost["standard"]
    pg.click("#ask-dd-top .ask-tag[data-source='web']")
    assert pg.inner_text("#ask-dd-top [data-dd='depth'] .ask-dd-v") == "Standard ($%.3f)" % (cost["standard"] + exa)


def test_the_bubble_clone_carries_the_estimate_and_keeps_its_old_look(admin_pg):
    pg = admin_pg
    pg.fill("#ask-q", "First question")
    pg.click("#ask-btn")
    pg.wait_for_selector("#fu-q")
    want = pg.inner_text("#ask-dd-top [data-dd='depth'] .ask-dd-v")
    assert "$" in want
    assert pg.inner_text("#fu [data-dd='depth'] .ask-dd-v") == want
    assert pg.is_visible("#fu .ask-dd-k") and not pg.is_visible("#fu .ask-dd-h")
    d, s = _box(pg, "#fu [data-dd='depth'].ask-dd-btn"), _box(pg, "#fu [data-dd='sources'].ask-dd-btn")
    assert abs(d["t"] - s["t"]) < 1                                       # still side by side
    assert pg.locator("#fu .ask-cost").count() == 0


def test_a_member_sees_names_only_and_no_dollar_note(member_pg):
    pg = member_pg
    assert pg.evaluate("COST") == {}
    assert pg.inner_text("#ask-dd-top [data-dd='depth'] .ask-dd-v") == "Quick"
    for t in pg.locator("#ask-dd-top .ask-tag[data-tier] .ask-tag-cost").all():
        assert t.inner_text() == ""
    assert pg.locator(".ask-cost-note").count() == 0
    assert "$" not in pg.inner_text("#ask-dd-top")


def test_search_past_questions_intro_says_what_the_list_is(buddy_html):
    html = buddy_html[0]
    assert "Questions members rated helpful. Check here before spending a query re-asking one." in html
    assert "other members have already asked" not in html
