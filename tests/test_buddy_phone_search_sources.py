"""FP&A Buddy phone layout: the search row, and the Depth and Sources panels as overlays.

Live-browser tests (window.fetch stubbed, no server, no model); they skip where no
Chromium exists. All geometry is measured, never read from the CSS. Chromium only:
the sandbox has no WebKit.
"""
import pytest

from tests import test_buddy_followup_bubble as _bub
from tests.test_buddy_followup_bubble import STUB
from tests.test_buddy_followup_bubble import _launch as _launch_base


def _launch():
    """The shared launcher only globs chrome-linux/; this sandbox has chrome-linux64/."""
    got = _launch_base()
    if got is not None:
        return got
    import glob
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    for path in glob.glob("/opt/pw-browsers/chromium-*/chrome-linux*/chrome"):
        try:
            return pw, pw.chromium.launch(executable_path=path)
        except Exception:
            continue
    pw.stop()
    return None

page_html = _bub.page_html

SIZES = {"phone": {"width": 390, "height": 844}, "desktop": {"width": 1280, "height": 900}}
SEARCH = "#past-questions form"


@pytest.fixture(params=sorted(SIZES))
def live(request, page_html, tmp_path):
    launched = _launch()
    if launched is None:
        pytest.skip("no Chromium available")
    pw, browser = launched
    f = tmp_path / "buddy.html"
    f.write_text(page_html, encoding="utf-8")
    pg = browser.new_page(viewport=SIZES[request.param], has_touch=True)
    pg.add_init_script(STUB)
    pg.goto(f.as_uri())
    pg.size = request.param
    yield pg
    browser.close()
    pw.stop()


def _box(pg, sel):
    return pg.evaluate("""(s) => { var r = document.querySelector(s).getBoundingClientRect();
      return {l: r.left, r: r.right, t: r.top + window.scrollY, b: r.bottom + window.scrollY, w: r.width, h: r.height}; }""", sel)


def _row(pg):
    return (_box(pg, SEARCH), _box(pg, SEARCH + " input[name=pq]"),
            _box(pg, SEARCH + " button:nth-of-type(1)"), _box(pg, SEARCH + " button:nth-of-type(2)"))


def test_phone_search_input_is_full_width_and_buttons_share_the_next_line(live):
    pg = live
    form, inp, search, helpful = _row(pg)
    print(pg.size, "form", form["w"], "input", inp["w"], "search", search["w"], search["h"],
          "helpful", helpful["w"], helpful["h"], "tops", inp["t"], search["t"], helpful["t"])
    if pg.size != "phone":
        return
    assert abs(inp["w"] - form["w"]) < 1                    # full width of its container
    assert inp["b"] <= search["t"] + 1                      # buttons are on the next line
    assert abs(search["t"] - helpful["t"]) < 1              # side by side
    assert abs(search["w"] - helpful["w"]) < 1              # equal width
    assert abs(search["w"] * 2 + 8 - form["w"]) < 2         # and they fill that line


def test_search_row_heights_use_the_control_token(live):
    pg = live
    _, inp, search, helpful = _row(pg)
    assert abs(search["h"] - 28) < 1 and abs(helpful["h"] - 28) < 1
    assert inp["h"] >= 28


def _dd_hit(pg):
    return pg.evaluate("""() => { var b = document.querySelector("#ask-dd-top [data-dd='sources']");
      return b.getBoundingClientRect().height; }""")


def test_sources_panel_overlays_and_does_not_move_content_below(live):
    pg = live
    before = _box(pg, "#ask-q")
    pg.click("#ask-dd-top [data-dd='sources'].ask-dd-btn")
    panel = _box(pg, "#ask-dd-top .ask-dd-panel[data-dd='sources']")
    after = _box(pg, "#ask-q")
    assert abs(before["t"] - after["t"]) < 1                # nothing below moved
    assert panel["h"] > 0 and panel["t"] < after["b"]       # it sits over the text box
    assert pg.get_attribute("#ask-dd-top [data-dd='sources'].ask-dd-btn", "aria-controls")


def test_depth_panel_overlays_too(live):
    pg = live
    before = _box(pg, "#ask-q")
    pg.click("#ask-dd-top [data-dd='depth'].ask-dd-btn")
    assert abs(before["t"] - _box(pg, "#ask-q")["t"]) < 1


def test_sources_panel_closes_on_outside_click_escape_and_second_tap(live):
    pg = live
    sel = "#ask-dd-top .ask-dd-panel[data-dd='sources']"
    btn = "#ask-dd-top [data-dd='sources'].ask-dd-btn"
    pg.click(btn)
    assert pg.is_visible(sel)
    pg.click(btn)
    assert pg.is_hidden(sel)
    pg.click(btn)
    pg.keyboard.press("Escape")
    assert pg.is_hidden(sel)
    pg.click(btn)
    pg.mouse.click(5, 5)
    assert pg.is_hidden(sel)
    assert pg.get_attribute(btn, "aria-expanded") == "false"


def test_sources_panel_stays_inside_the_viewport_at_phone_width(live):
    pg = live
    vw, vh = pg.evaluate("[window.innerWidth, window.innerHeight]")
    # Scroll so the control sits near the bottom of the viewport: the panel must flip or scroll.
    pg.evaluate("""() => { var b = document.querySelector("#ask-dd-top [data-dd='sources']");
      window.scrollBy(0, b.getBoundingClientRect().top - (window.innerHeight - 70)); }""")
    pg.click("#ask-dd-top [data-dd='sources'].ask-dd-btn")
    r = pg.evaluate("""() => { var r = document.querySelector("#ask-dd-top .ask-dd-panel[data-dd='sources']").getBoundingClientRect();
      return {l: r.left, r: r.right, t: r.top, b: r.bottom}; }""")
    assert r["l"] >= -0.5 and r["r"] <= vw + 0.5
    assert r["t"] >= -0.5 and r["b"] <= vh + 0.5
