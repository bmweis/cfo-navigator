"""FP&A Buddy: Depth and Sources are two dropdowns, not chips plus a popover.

The bubble's old Sources popover was absolutely positioned above its pill: on a
phone it covered the follow-up input and button and ran past the bubble's right
edge, and the top box's three chips wrapped to a second row. Now both places hold
the same block: two buttons in one row showing the current value, and the open
panel sits in page flow under that row at the row's full width.

Live-browser tests (window.fetch stubbed, no server, no model); they skip where
no Chromium exists. All geometry is measured, never read from the CSS.
"""
import pytest

from tests import test_buddy_followup_bubble as _bub
from tests.test_buddy_followup_bubble import STUB, _ask, _launch, _settle

page_html = _bub.page_html   # reuse the logged-in page fixture

SIZES = {"phone": {"width": 390, "height": 844}, "desktop": {"width": 1280, "height": 900}}


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
    yield pg
    browser.close()
    pw.stop()


def _box(pg, sel):
    return pg.evaluate("""(s) => { var r = document.querySelector(s).getBoundingClientRect();
      return {l: r.left, r: r.right, t: r.top, b: r.bottom, w: r.width, h: r.height}; }""", sel)


def test_top_buttons_share_one_row_and_show_the_current_value(live):
    pg = live
    d, s = _box(pg, "#ask-dd-top [data-dd='depth'].ask-dd-btn"), _box(pg, "#ask-dd-top [data-dd='sources'].ask-dd-btn")
    assert abs(d["t"] - s["t"]) < 1 and d["r"] <= s["l"] + 1
    assert pg.inner_text("#ask-dd-top [data-dd='depth'] .ask-dd-v") == "Standard"
    assert pg.inner_text("#ask-dd-top [data-dd='sources'] .ask-dd-v") == "2 of 3"
    assert pg.is_hidden("#ask-dd-top .ask-dd-panel[data-dd='sources']")


def test_button_labels_fit_without_truncation_on_a_phone(live):
    pg = live
    for dd in ("depth", "sources"):
        assert pg.evaluate("""(d) => { var v = document.querySelector("#ask-dd-top [data-dd='" + d + "'] .ask-dd-v");
          return v.scrollWidth <= v.clientWidth; }""", dd)


def test_top_panel_is_full_row_width_and_in_flow(live):
    pg = live
    pg.click("#ask-dd-top [data-dd='sources'].ask-dd-btn")
    row, panel = _box(pg, "#ask-dd-top .ask-dd-row"), _box(pg, "#ask-dd-top .ask-dd-panel[data-dd='sources']")
    assert abs(panel["l"] - row["l"]) < 1 and abs(panel["r"] - row["r"]) < 1
    assert panel["t"] >= row["b"]
    assert panel["b"] <= _box(pg, "#ask-btn")["t"]          # pushes Ask down, never covers it
    assert panel["r"] <= pg.evaluate("window.innerWidth")


def test_sources_panel_stays_open_while_ticking_and_closes_outside_or_escape(live):
    pg = live
    pg.click("#ask-dd-top [data-dd='sources'].ask-dd-btn")
    pg.click("#ask-dd-top .ask-tag[data-source='feed']")
    pg.click("#ask-dd-top .ask-tag[data-source='library']")
    assert pg.is_visible("#ask-dd-top .ask-dd-panel[data-dd='sources']")
    assert pg.inner_text("#ask-dd-top [data-dd='sources'] .ask-dd-v") == "2 of 3"
    assert sorted(pg.evaluate("activeSources()")) == ["feed", "web"]
    pg.click("#ask-q")
    assert pg.is_hidden("#ask-dd-top .ask-dd-panel[data-dd='sources']")
    pg.click("#ask-dd-top [data-dd='sources'].ask-dd-btn")
    pg.keyboard.press("Escape")
    assert pg.is_hidden("#ask-dd-top .ask-dd-panel[data-dd='sources']")


def test_picking_a_depth_closes_the_panel_and_updates_the_button(live):
    pg = live
    pg.click("#ask-dd-top [data-dd='depth'].ask-dd-btn")
    pg.click("#ask-dd-top .ask-tag[data-tier='deep']")
    assert pg.evaluate("selectedTier") == "deep"
    assert pg.is_hidden("#ask-dd-top .ask-dd-panel[data-dd='depth']")
    assert pg.inner_text("#ask-dd-top [data-dd='depth'] .ask-dd-v") == "Deep"


def test_only_one_panel_is_open_at_a_time(live):
    pg = live
    pg.click("#ask-dd-top [data-dd='sources'].ask-dd-btn")
    pg.click("#ask-dd-top [data-dd='depth'].ask-dd-btn")
    assert pg.is_hidden("#ask-dd-top .ask-dd-panel[data-dd='sources']")
    assert pg.is_visible("#ask-dd-top .ask-dd-panel[data-dd='depth']")


def test_bubble_panel_never_covers_the_input_or_ask_button_or_passes_the_bubble(live):
    pg = live
    _ask(pg, "First question")
    pg.wait_for_selector("#fu-q")
    _settle(pg)
    pg.click("#fu .ask-dd-btn[data-dd='sources']")
    bubble, panel = _box(pg, "#fu"), _box(pg, "#fu .ask-dd-panel[data-dd='sources']")
    q, btn = _box(pg, "#fu-q"), _box(pg, "#fu-btn")
    assert panel["t"] >= max(q["b"], btn["b"]) - 0.5
    assert panel["l"] >= bubble["l"] - 0.5 and panel["r"] <= bubble["r"] + 0.5
    assert panel["b"] <= bubble["b"] + 0.5


def test_bubble_choices_and_top_buttons_share_one_state(live):
    pg = live
    _ask(pg, "First question")
    pg.wait_for_selector("#fu-q")
    _settle(pg)
    pg.click("#fu .ask-dd-btn[data-dd='depth']")
    pg.click("#fu .ask-tag[data-tier='quick']")
    assert pg.inner_text("#ask-dd-top [data-dd='depth'] .ask-dd-v") == "Quick"
    assert pg.inner_text("#fu [data-dd='depth'] .ask-dd-v") == "Quick"
    pg.click("#fu .ask-dd-btn[data-dd='sources']")
    pg.click("#fu .ask-tag[data-source='feed']")
    assert pg.inner_text("#ask-dd-top [data-dd='sources'] .ask-dd-v") == "3 of 3"
    pg.fill("#fu-q", "Again")
    pg.click("#fu-btn")
    pg.wait_for_function("window.__calls.length === 2")
    second = pg.evaluate("window.__calls[1]")
    assert second["effort"] == "quick" and sorted(second["sources"]) == ["feed", "library", "web"]


def test_no_horizontal_page_overflow(live):
    pg = live
    pg.click("#ask-dd-top [data-dd='sources'].ask-dd-btn")
    assert pg.evaluate("document.documentElement.scrollWidth") <= pg.evaluate("window.innerWidth")


def test_tapping_ask_follow_up_after_typing_is_not_moved_by_the_controls_returning(live):
    """Blur used to re-expand the controls; the sticky bubble then grew upward and
    the button moved out from under the tap, so the follow-up never sent."""
    pg = live
    pg.evaluate("window.__next = {answer: 'Long paragraph of answer text. '.repeat(400)}")
    _ask(pg, "First question")
    pg.wait_for_selector("#fu-q")
    _settle(pg)
    pg.evaluate("window.scrollTo(0, 400)")           # mid-answer: the bubble is stuck to the viewport bottom
    pg.fill("#fu-q", "Again")
    pg.click("#fu-btn")
    pg.wait_for_function("window.__calls.length === 2")


def test_compact_summary_stays_until_a_tap_outside_or_on_the_summary(live):
    pg = live
    _ask(pg, "First question")
    pg.wait_for_selector("#fu-q")
    pg.focus("#fu-q")
    assert pg.is_hidden("#fu .fu-meta") and pg.is_visible("#fu .fu-sum")
    pg.evaluate("document.getElementById('fu-q').blur()")
    assert pg.is_hidden("#fu .fu-meta")              # blur alone does not expand it
    pg.click("#fu .fu-sum")
    assert pg.is_visible("#fu .fu-meta")
    pg.focus("#fu-q")
    pg.click("h1, .site-main", position={"x": 5, "y": 5})
    assert pg.is_visible("#fu .fu-meta")
