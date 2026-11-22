"""FP&A Buddy: every past question is a collapsed row until the reader opens it.

The rule: an answer is expanded only when the reader just asked it or tapped its
row; a plain load and a ?c= reload expand nothing; a follow-up folds the earlier
turns to one-line rows and keeps only the newest open. Live Chromium at 390px
through the same route-handler harness as test_buddy_phone_fixes; skips where no
Chromium exists (CI).
"""
from tests import test_buddy_phone_fixes as T

ORIGIN, _ask, _open = T.ORIGIN, T._ask, T._open
T_wait_recent = T._wait_recent
# Re-bound by assignment so pytest finds the fixtures here.
buddy_html, phone = T.buddy_html, T.phone

import re

import pytest


@pytest.fixture
def desktop(buddy_html):
    """A 1280px mouse context (no touch): selection and double-click are pointer behaviour."""
    html, _ = buddy_html
    launched = T._launch()
    if launched is None:
        pytest.skip("no Chromium available")
    pw, browser = launched
    pg = browser.new_context(viewport={"width": 1280, "height": 800}).new_page()
    pg.route(re.compile(r"^http://buddy\.test/.*"), T.Site(html).handle)
    pg.route(re.compile(r"^https?://(?!buddy\.test).*"), lambda r: r.abort())
    yield pg
    browser.close()
    pw.stop()


LONG_Q = ("What does the typical organization structure and spend profile look like for a high "
          "performing mid-market SaaS business with a sales-led motion and twenty to thirty million "
          "in annual revenue? How has that changed over the past few years?")


def _fold_first_turn(pg, first):
    """Ask `first`, then a follow-up, so turn one folds into its row."""
    _open(pg)
    _ask(pg, first)
    pg.fill("#fu-q", "Second question")
    pg.click("#fu-btn")
    pg.wait_for_function("document.querySelectorAll('#ask-thread .ask-q-bubble').length === 2 && !document.querySelector('.ask-loading')")
    pg.wait_for_timeout(900)          # the page smooth-scrolls to the new answer; measure after it settles


def test_past_question_rows_are_collapsed_on_load_and_open_on_a_tap(phone):
    pg, _ = phone
    _open(pg)
    rows = pg.locator("#past-questions .ask-pq-row")
    assert rows.count() >= 1
    assert pg.locator("#past-questions .ask-pq-row.open").count() == 0
    assert not pg.is_visible("#past-questions .ask-hist-answer")
    assert pg.is_visible("#past-questions .ask-pq-toggle")
    pg.locator("#past-questions .ask-pq-toggle").first.tap()
    assert pg.is_visible("#past-questions .ask-hist-answer")
    assert pg.is_visible("#past-questions .ask-src-disc > summary")      # sources: collapsed disclosure
    assert pg.locator("#past-questions .ask-src-disc[open]").count() == 0
    pg.locator("#past-questions .ask-pq-toggle").first.tap()
    assert not pg.is_visible("#past-questions .ask-hist-answer")


def test_past_question_row_opens_from_the_keyboard(phone):
    pg, _ = phone
    _open(pg)
    pg.locator("#past-questions .ask-pq-toggle").first.focus()
    pg.keyboard.press("Enter")
    assert pg.is_visible("#past-questions .ask-hist-answer")
    pg.keyboard.press("Space")
    assert not pg.is_visible("#past-questions .ask-hist-answer")


def test_a_reload_with_c_expands_nothing_and_highlights_the_row(phone):
    pg, _ = phone
    pg.goto(ORIGIN + "/tools/fpa-buddy?c=c9")
    T_wait_recent(pg)
    assert pg.locator("#ask-thread").inner_text().strip() == ""
    assert pg.locator("#fu").count() == 0
    assert pg.locator("#ask-thread .ask-answer").count() == 0
    assert not pg.is_visible("#past-questions .ask-hist-answer")
    assert pg.locator(".ask-recent-item.recent-hl").get_attribute("data-cid") == "c9"
    assert pg.is_visible(".ask-recent-item.recent-hl")


def test_a_follow_up_folds_earlier_turns_and_keeps_only_the_newest_open(phone):
    pg, site = phone
    _open(pg)
    _ask(pg, "First question")
    pg.fill("#fu-q", "Second question")
    pg.click("#fu-btn")
    pg.wait_for_function("document.querySelectorAll('#ask-thread .ask-q-bubble').length === 2 && !document.querySelector('.ask-loading')")
    # Nothing deleted: both turns are in the thread.
    assert pg.locator("#ask-thread .ask-q-bubble").count() == 2
    answers = pg.locator("#ask-thread .ask-answer")
    assert not answers.nth(0).is_visible()
    assert answers.nth(1).is_visible()
    row = pg.locator("#ask-thread .ask-turn-row")
    toggle = row.locator(".ask-turn-toggle")
    assert row.count() == 1 and toggle.get_attribute("aria-expanded") == "false"
    assert toggle.get_attribute("aria-label") == "Show answer"
    assert "First question" in row.inner_text()
    toggle.tap()
    assert toggle.get_attribute("aria-expanded") == "true"
    assert toggle.get_attribute("aria-label") == "Hide answer"
    assert answers.nth(0).is_visible() and answers.nth(1).is_visible()
    # A third turn folds the second too, and a tapped-open turn folds back to its row.
    pg.fill("#fu-q", "Third question")
    pg.click("#fu-btn")
    pg.wait_for_function("document.querySelectorAll('#ask-thread .ask-q-bubble').length === 3 && !document.querySelector('.ask-loading')")
    assert pg.locator("#ask-thread .ask-turn-row").count() == 2
    assert pg.locator("#ask-thread .ask-answer:visible").count() >= 1
    assert pg.locator("#ask-thread .ask-answer").nth(2).is_visible()
    assert not pg.locator("#ask-thread .ask-answer").nth(1).is_visible()


def test_phone_inputs_are_16px_and_the_bubble_has_44px_targets(phone):
    """16px stops iOS zooming on focus. Targets are at least 44px and nothing
    inside the bubble extends past it."""
    pg, _ = phone
    _open(pg)
    _ask(pg, "First question")
    pg.fill("#fu-q", "Second")
    fs = lambda sel: pg.evaluate("s => getComputedStyle(document.querySelector(s)).fontSize", sel)
    assert fs("#ask-q") == fs("#fu-q") == fs("#past-questions input[name=pq]") == "16px"
    assert pg.evaluate("document.getElementById('fu-q').getBoundingClientRect().height") >= 44
    pg.focus("#fu-q")
    # Focus no longer swaps the controls for a summary: they stay, inside the bubble.
    box = pg.evaluate("(() => { const r = document.querySelector('#fu .fu-meta').getBoundingClientRect();"
                      " return [r.height, document.getElementById('fu').getBoundingClientRect().width - r.width]; })()")
    assert box[0] >= 28 and box[1] < 30
    over = pg.evaluate("""(() => { const f = document.getElementById('fu').getBoundingClientRect();
        return [...document.querySelectorAll('#fu *')].filter(e => { const r = e.getBoundingClientRect();
        return r.width && (r.right > f.right + 0.5 || r.left < f.left - 0.5); }).length; })()""")
    assert over == 0


def test_the_send_button_is_an_icon_with_a_44px_hit_area_and_a_disabled_state(phone):
    pg, _ = phone
    _open(pg)
    _ask(pg, "First question")
    btn = pg.locator("#fu-btn")
    assert btn.get_attribute("aria-label") == "Ask follow-up" and btn.get_attribute("title") == "Ask follow-up"
    assert btn.locator("svg").count() == 1 and btn.inner_text().strip() == ""
    box = btn.bounding_box()
    assert abs(box["height"] - 28) < 0.6 and abs(box["width"] - 28) < 0.6      # one button height, 44px hit area below
    assert pg.evaluate("parseFloat(getComputedStyle(document.getElementById('fu-btn'), '::after').height)") >= 44
    assert pg.is_disabled("#fu-btn")                       # empty field
    pg.fill("#fu-q", "Something")
    assert not pg.is_disabled("#fu-btn")


def test_the_row_is_a_container_and_only_the_toggle_is_a_button(phone):
    pg, _ = phone
    _fold_first_turn(pg, LONG_Q)
    assert pg.locator("#ask-thread .ask-turn-row").evaluate("e => e.tagName") == "DIV"
    assert pg.locator("#ask-thread .ask-turn-rq").evaluate("e => e.closest('button')") is None
    toggle = pg.locator("#ask-thread .ask-turn-toggle")
    assert toggle.evaluate("e => e.tagName") == "BUTTON" and toggle.get_attribute("type") == "button"
    assert toggle.locator(".ask-pq-rc").count() == 1
    # Full question stays visible, no clamp, collapsed or not.
    q = pg.locator("#ask-thread .ask-turn-rq")
    assert q.inner_text() == LONG_Q
    assert q.evaluate("e => e.scrollHeight <= e.clientHeight + 1")


def test_toggle_is_44px_centered_and_has_a_focus_ring(phone):
    pg, _ = phone
    _fold_first_turn(pg, LONG_Q)                       # wraps to several lines at 390px
    row = pg.locator("#ask-thread .ask-turn-row").bounding_box()
    tg = pg.locator("#ask-thread .ask-turn-toggle").bounding_box()
    assert tg["width"] >= 44 and tg["height"] >= 44
    assert row["height"] > 80                          # the question really wraps
    assert abs((tg["y"] + tg["height"] / 2) - (row["y"] + row["height"] / 2)) <= 1.5
    pg.keyboard.press("Tab")                            # land on something, then focus the toggle by keyboard
    pg.locator("#ask-thread .ask-turn-toggle").focus()
    pg.keyboard.press("Shift+Tab"); pg.keyboard.press("Tab")
    outline = pg.locator("#ask-thread .ask-turn-toggle").evaluate("e => getComputedStyle(e).outlineStyle + '|' + getComputedStyle(e).outlineWidth")
    assert outline.startswith("solid|2px")


def test_toggle_works_from_the_keyboard_and_the_label_follows(phone):
    pg, _ = phone
    _fold_first_turn(pg, "First question")
    toggle = pg.locator("#ask-thread .ask-turn-toggle")
    answers = pg.locator("#ask-thread .ask-answer")
    toggle.focus()
    pg.keyboard.press("Enter")
    assert toggle.get_attribute("aria-label") == "Hide answer" and answers.nth(0).is_visible()
    pg.keyboard.press("Space")
    assert toggle.get_attribute("aria-label") == "Show answer" and not answers.nth(0).is_visible()


def test_clicking_or_selecting_the_question_text_never_toggles_and_it_can_be_copied(desktop):
    pg = desktop
    _fold_first_turn(pg, LONG_Q)
    toggle = pg.locator("#ask-thread .ask-turn-toggle")
    answer = pg.locator("#ask-thread .ask-answer").nth(0)
    q = pg.locator("#ask-thread .ask-turn-rq")
    box = q.bounding_box()
    y = box["y"] + 10
    # Drag across the text: it selects, it does not toggle.
    pg.mouse.move(box["x"] + 2, y); pg.mouse.down(); pg.mouse.move(box["x"] + 300, y, steps=6); pg.mouse.up()
    assert len(pg.evaluate("window.getSelection().toString()")) > 5
    assert toggle.get_attribute("aria-expanded") == "false" and not answer.is_visible()
    # Double and triple click select words and the paragraph, still no toggle.
    pg.mouse.dblclick(box["x"] + 60, y)
    assert pg.evaluate("window.getSelection().toString().trim()") != ""
    assert toggle.get_attribute("aria-expanded") == "false"
    pg.mouse.click(box["x"] + 60, y, click_count=3)
    assert LONG_Q.startswith(pg.evaluate("window.getSelection().toString().trim()")[:40])
    assert toggle.get_attribute("aria-expanded") == "false" and not answer.is_visible()
    # Clicking the empty part of the row does not toggle either; only the button does.
    row = pg.locator("#ask-thread .ask-turn-row").bounding_box()
    pg.mouse.click(row["x"] + 2, row["y"] + row["height"] / 2)
    assert toggle.get_attribute("aria-expanded") == "false"
    toggle.click()
    assert toggle.get_attribute("aria-expanded") == "true" and answer.is_visible()
    toggle.click()
    assert toggle.get_attribute("aria-expanded") == "false" and not answer.is_visible()
