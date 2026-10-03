"""FP&A Buddy: every past question is a collapsed row until the reader opens it.

The rule: an answer is expanded only when the reader just asked it or tapped its
row; a plain load and a ?c= reload expand nothing; a follow-up folds the earlier
turns to one-line rows and keeps only the newest open. Live Chromium at 390px
through the same route-handler harness as test_buddy_phone_fixes; skips where no
Chromium exists (CI).
"""
from tests import test_buddy_phone_fixes as T

ORIGIN, _ask, _open = T.ORIGIN, T._ask, T._open
# Re-bound by assignment so pytest finds the fixtures here.
buddy_html, phone = T.buddy_html, T.phone


def test_past_question_rows_are_collapsed_on_load_and_open_on_a_tap(phone):
    pg, _ = phone
    _open(pg)
    rows = pg.locator("#past-questions .ask-pq-row")
    assert rows.count() >= 1
    assert pg.locator("#past-questions .ask-pq-row[open]").count() == 0
    assert not pg.is_visible("#past-questions .ask-hist-answer")
    assert pg.is_visible("#past-questions .ask-pq-sum")
    pg.locator("#past-questions .ask-pq-sum").first.tap()
    assert pg.is_visible("#past-questions .ask-hist-answer")
    assert pg.is_visible("#past-questions .ask-hist-answer ~ ul")
    pg.locator("#past-questions .ask-pq-sum").first.tap()
    assert not pg.is_visible("#past-questions .ask-hist-answer")


def test_past_question_row_opens_from_the_keyboard(phone):
    pg, _ = phone
    _open(pg)
    pg.locator("#past-questions .ask-pq-sum").first.focus()
    pg.keyboard.press("Enter")
    assert pg.is_visible("#past-questions .ask-hist-answer")
    pg.keyboard.press("Space")
    assert not pg.is_visible("#past-questions .ask-hist-answer")


def test_a_reload_with_c_expands_nothing_and_highlights_the_row(phone):
    pg, _ = phone
    pg.goto(ORIGIN + "/tools/fpa-buddy?c=c9")
    pg.wait_for_selector(".ask-recent-item")
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
    assert row.count() == 1 and row.get_attribute("aria-expanded") == "false"
    assert "First question" in row.inner_text()
    row.tap()
    assert row.get_attribute("aria-expanded") == "true"
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
    box = pg.evaluate("(() => { const r = document.querySelector('.fu-sum').getBoundingClientRect();"
                      " return [r.height, document.getElementById('fu').getBoundingClientRect().width - r.width]; })()")
    assert box[0] >= 44 and box[1] < 30
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
    assert box["width"] >= 44 and box["height"] >= 44
    assert pg.is_disabled("#fu-btn")                       # empty field
    pg.fill("#fu-q", "Something")
    assert not pg.is_disabled("#fu-btn")
