"""/ask/history multi-turn card: one question once, text is inert, a real toggle button.

The card used to be a native <details>: the whole <summary> toggled, so a tap, drag or
double click on the question text opened or closed it, and the first turn inside the open
card printed its question again under the heading. Now (same standard as the Buddy
conversation row): the heading text is plain selectable text, the "N follow-ups" pill is a
real <button> at the lower right, and the first turn inside shows its answer only.
The server-level tests run in CI; the Chromium tests skip where no Chromium exists.
"""
import re

import pytest

from tests.test_ask_history_grouping import _member, _seed, env  # noqa: F401
from tests import test_buddy_phone_fixes as T

Q1, Q2, Q3 = "What is NRR?", "And for Series A?", "What about GRR?"


def _page(env):
    appmod, db = env
    _seed(db)
    return _member(appmod).get("/ask/history").text


def _card(html: str) -> str:
    """The multi-turn card's markup. Before the fix it was a <details class="convo">; now a div."""
    i = html.index('class="convo"')
    start = max(html.rfind("<div", 0, i), html.rfind("<details", 0, i))
    end = html.find('<div class="convo-end"></div>', start)
    if end == -1:
        end = html.index("</details>", start) + len("</details>")
    return html[start:end]


def test_the_first_question_appears_once_in_a_multi_turn_card(env):
    card = _card(_page(env))
    assert card.count(Q1) == 1, "the heading is the first question; the thread must not repeat it"
    assert Q2 in card and Q3 in card                     # follow-ups keep their own question lines
    assert "a1" in card                                  # the first turn still shows its answer


def test_the_card_is_not_a_details_and_the_pill_is_a_real_button(env):
    html = _page(env)
    card = _card(html)
    assert "<details" not in card and "<summary" not in card
    m = re.search(r'<button type="button" class="convo-toggle"[^>]*>', card)
    assert m, "the follow-up pill must be a real <button type=button>"
    tag = m.group(0)
    assert 'aria-expanded="false"' in tag and "aria-controls=" in tag
    assert "2 follow-ups" in card
    # Closed on load.
    body = re.search(r'<div class="convo-body"[^>]*>', card).group(0)
    assert "hidden" in body


def test_single_turn_cards_are_unchanged(env):
    html = _page(env)
    assert html.count("Standalone question") == 1
    assert html.count('class="convo-toggle"') == 1       # only the one multi-turn conversation


def _launch_page(html):
    launched = T._launch()
    if launched is None:
        pytest.skip("no Chromium available")
    pw, browser = launched
    return pw, browser, html


@pytest.fixture(params=["desktop", "phone"])
def pg(env, request):
    html = _page(env)
    launched = T._launch()
    if launched is None:
        pytest.skip("no Chromium available")
    pw, browser = launched
    if request.param == "phone":
        ctx = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
    else:
        ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    page = ctx.new_page()
    page.route(re.compile(r"^https?://(?!hist\.test).*"), lambda r: r.abort())
    page.route("http://hist.test/**", lambda r: r.fulfill(status=200, content_type="text/html", body=html))
    page.goto("http://hist.test/ask/history")
    yield page, request.param
    browser.close()
    pw.stop()


def test_clicking_or_selecting_the_heading_never_toggles_and_the_button_does(pg):
    page, kind = pg
    if kind == "phone":
        pytest.skip("selection and double click are pointer behaviour")
    toggle = page.locator(".convo-toggle")
    body = page.locator(".convo-body")
    q = page.locator(".convo-q")
    box = q.bounding_box()
    y = box["y"] + 8
    page.mouse.move(box["x"] + 1, y); page.mouse.down(); page.mouse.move(box["x"] + 60, y, steps=5); page.mouse.up()
    assert len(page.evaluate("window.getSelection().toString()")) >= 2
    assert toggle.get_attribute("aria-expanded") == "false" and not body.is_visible()
    page.mouse.dblclick(box["x"] + 20, y)
    page.mouse.click(box["x"] + 20, y, click_count=3)
    assert toggle.get_attribute("aria-expanded") == "false" and not body.is_visible()
    card = page.locator(".convo").bounding_box()
    page.mouse.click(card["x"] + 4, card["y"] + 4)        # empty card edge
    assert not body.is_visible()
    toggle.click()
    assert toggle.get_attribute("aria-expanded") == "true" and body.is_visible()
    assert page.locator(".convo").inner_text().count(Q1) == 1
    toggle.click()
    assert toggle.get_attribute("aria-expanded") == "false" and not body.is_visible()


def test_toggle_is_a_44px_target_at_the_lower_right_with_a_focus_ring(pg):
    page, _ = pg
    card = page.locator(".convo").bounding_box()
    tg = page.locator(".convo-toggle").bounding_box()
    assert tg["height"] >= 44 and tg["width"] >= 44
    assert abs((tg["x"] + tg["width"]) - (card["x"] + card["width"])) <= 22      # right edge
    assert (card["y"] + card["height"]) - (tg["y"] + tg["height"]) <= 12          # lower edge
    page.locator(".convo-toggle").focus()
    page.keyboard.press("Shift+Tab"); page.keyboard.press("Tab")
    ring = page.locator(".convo-toggle").evaluate("e => getComputedStyle(e).outlineStyle + '|' + getComputedStyle(e).outlineWidth")
    assert ring.startswith("solid|2px")


def test_toggle_works_from_the_keyboard(pg):
    page, _ = pg
    toggle = page.locator(".convo-toggle")
    toggle.focus()
    page.keyboard.press("Enter")
    assert toggle.get_attribute("aria-expanded") == "true" and page.locator(".convo-body").is_visible()
    page.keyboard.press("Space")
    assert toggle.get_attribute("aria-expanded") == "false" and not page.locator(".convo-body").is_visible()
