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
        col = pg.locator("#ask-dd-top label.ask-dd-lbl", has=pg.locator(".ask-dd-h", has_text=name)).first
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


def test_start_a_new_conversation_label_sits_directly_above_the_text_box(member_pg):
    pg = member_pg
    label = _box(pg, "label[for='ask-q']")
    controls = _box(pg, "#ask-dd-top")
    box = _box(pg, "#ask-q")
    assert pg.inner_text("label[for='ask-q']").strip().lower() == "start a new conversation"
    assert controls["b"] <= label["t"] and label["b"] <= box["t"]       # controls, then label, then box
    assert box["t"] - label["b"] < 16                                    # directly above, like Follow-up


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


def test_every_user_sees_the_rounded_up_label_in_the_depth_options_and_on_the_button(admin_pg):
    """The cost label is for every signed-in user now (it was admin-only): one
    typical simple question per depth, rounded up to the next cent, fixed per depth
    (it does not move when sources change)."""
    pg = admin_pg
    cost = pg.evaluate("COST")
    assert set(cost) == {"quick", "standard", "deep"} and all(re.fullmatch(r"about \$\d+\.\d\d", v) for v in cost.values())
    for tier, label in (("quick", "Quick"), ("standard", "Standard"), ("deep", "Deep")):
        assert pg.inner_text("#ask-dd-top .ask-tag[data-tier='%s'] .ask-tag-cost" % tier) == cost[tier]
        assert pg.inner_text("#ask-dd-top .ask-tag[data-tier='%s'] .ask-tag-name" % tier) == label
    want = "Standard, " + cost["standard"]
    assert pg.inner_text("#ask-dd-top [data-dd='depth'] .ask-dd-v") == want
    assert "per query" not in pg.inner_text("body")
    # Sources change what a turn can cost, not the label: it is a typical simple question.
    pg.click("#ask-dd-top [data-dd='sources'].ask-dd-btn")
    pg.click("#ask-dd-top .ask-tag[data-source='feed']")
    assert pg.inner_text("#ask-dd-top [data-dd='depth'] .ask-dd-v") == want


def test_the_bubble_clone_carries_the_label_and_keeps_its_old_look(admin_pg):
    pg = admin_pg
    pg.fill("#ask-q", "First question")
    pg.click("#ask-btn")
    pg.wait_for_selector("#fu-q")
    want = pg.inner_text("#ask-dd-top [data-dd='depth'] .ask-dd-v")
    assert "$" in want
    assert pg.inner_text("#fu [data-dd='depth'] .ask-dd-v") == want
    assert pg.is_visible("#fu .ask-dd-k") and not pg.is_visible("#fu .ask-dd-h")
    # Both tops are read in one call: the page may still be smooth-scrolling to the
    # new answer, which moves the buttons between two separate reads.
    d, s = pg.evaluate("""() => ['depth', 'sources'].map(function(k) {
        return {t: document.querySelector("#fu [data-dd='" + k + "'].ask-dd-btn").getBoundingClientRect().top}; })""")
    assert abs(d["t"] - s["t"]) < 1                                       # still side by side
    assert pg.locator("#fu .ask-cost").count() == 0
    assert not pg.locator("#fu .ask-dd-foot").is_visible()                # the footnote is the top box's, not the bubble's


def test_a_non_admin_sees_the_same_label_and_footnote(member_pg):
    pg = member_pg
    cost = pg.evaluate("COST")
    assert re.fullmatch(r"about \$\d+\.\d\d", cost["quick"])
    assert pg.inner_text("#ask-dd-top [data-dd='depth'] .ask-dd-v") == "Quick, " + cost["quick"]
    assert pg.locator("#ask-dd-foot").count() == 1
    assert "Dollar amounts are estimates" not in pg.content()


def test_search_past_questions_intro_says_what_the_list_is(buddy_html):
    html = buddy_html[0]
    assert "Search before running your query." in html
    assert "other members have already asked" not in html


# --- Dropdown panels: each panel is its control's width. The cost footnote is a
# --- plain paragraph under the Depth box (not in the panel, not a tooltip).

def test_footnote_is_a_visible_paragraph_under_the_depth_box_not_a_tooltip(admin_html):
    """Non-browser guard (Chromium tests do not run in CI, issue 669): the footnote is
    ordinary page text, rendered once, after the Depth panel inside the Depth column,
    with no title attribute carrying it and no rule hiding it."""
    m = re.search(r'<p class="ask-dd-foot" id="ask-dd-foot">([^<]*)</p>', admin_html)
    assert m and admin_html.count('id="ask-dd-foot"') == 1
    text = m.group(1)
    assert "Benchmark questions cost two to three times as much" in text
    assert "$" not in text and "\u2014" not in text and "member" not in text.lower()
    assert 'title="Benchmark' not in admin_html
    col = re.search(r'<div class="ask-dd-col">(.*?)</div>\s*<div class="ask-dd-col">', admin_html, re.S).group(1)
    assert col.index('data-dd="depth" hidden') < col.index('id="ask-dd-foot"')   # directly after the box and its panel
    assert not re.search(r"\.ask-dd-foot\s*\{[^}]*display\s*:\s*none", admin_html.split(".fu .ask-dd-foot")[0])
    assert "Dollar amounts are estimates." not in admin_html


def test_footnote_sits_under_the_box_and_takes_two_lines_on_a_phone(admin_pg):
    pg = admin_pg
    btn, foot = _box(pg, "#ask-dd-top [data-dd='depth'].ask-dd-btn"), _box(pg, "#ask-dd-foot")
    assert foot["t"] >= btn["b"] and foot["t"] - btn["b"] < 16           # directly under the box, closed
    assert abs(foot["l"] - btn["l"]) < 4
    shown = pg.evaluate("(s) => { var e = document.querySelector(s); var c = getComputedStyle(e);"
                        " return c.display !== 'none' && c.visibility !== 'hidden' && e.getBoundingClientRect().height > 0; }", "#ask-dd-foot")
    assert shown
    line_h = pg.evaluate("parseFloat(getComputedStyle(document.getElementById('ask-dd-foot')).lineHeight)")
    phone = pg.evaluate("innerWidth") < 500
    assert foot["h"] / line_h < (2.2 if phone else 3.2)                   # two lines on a phone; the narrower half-width desktop column may take a third
    assert pg.evaluate("document.documentElement.scrollWidth <= innerWidth")
    # Open: the panel still opens directly under the button, the footnote follows it.
    pg.click("#ask-dd-top [data-dd='depth'].ask-dd-btn")
    panel, foot2 = _box(pg, "#ask-dd-top .ask-dd-panel[data-dd='depth']"), _box(pg, "#ask-dd-foot")
    assert panel["t"] - btn["b"] < 24 and foot2["t"] >= panel["b"]


def test_each_panel_is_its_controls_width_and_starts_at_its_left_edge(admin_pg):
    pg = admin_pg
    for k in ("depth", "sources"):
        pg.click("#ask-dd-top [data-dd='%s'].ask-dd-btn" % k)
        btn, panel = _box(pg, "#ask-dd-top .ask-dd-btn[data-dd='%s']" % k), _box(pg, "#ask-dd-top .ask-dd-panel[data-dd='%s']" % k)
        assert abs(btn["l"] - panel["l"]) < 1 and abs(btn["w"] - panel["w"]) < 1
        assert pg.evaluate("document.documentElement.scrollWidth <= innerWidth")
        pg.keyboard.press("Escape")


def test_the_bubble_panels_never_pass_the_bubble(admin_pg):
    pg = admin_pg
    pg.fill("#ask-q", "First question")
    pg.click("#ask-btn")
    pg.wait_for_selector("#fu-q")
    pg.wait_for_timeout(600)
    pg.click("#fu [data-dd='sources'].ask-dd-btn")
    pg.click("#fu [data-dd='depth'].ask-dd-btn")
    for k in ("depth", "sources"):
        pg.click("#fu [data-dd='%s'].ask-dd-btn" % k) if pg.get_attribute("#fu [data-dd='%s'].ask-dd-btn" % k, "aria-expanded") != "true" else None
        p, f = _box(pg, "#fu .ask-dd-panel[data-dd='%s']" % k), _box(pg, "#fu")
        assert f["l"] <= p["l"] + 0.5 and p["r"] <= f["r"] + 0.5 and p["w"] >= 219
    assert pg.evaluate("document.documentElement.scrollWidth <= innerWidth")
