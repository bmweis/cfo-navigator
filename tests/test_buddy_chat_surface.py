"""FP&A Buddy: a live conversation behaves like a chat.

Live (a thread on screen): no entry card, no similar-questions card, no past-question
lists; a visible "New conversation" control and a "Past questions" link. Fresh page:
unchanged. The similar-questions card is its own box under the entry card and never
changes the height of the example panel. Recent conversations sits above All questions.

Browser tests stub window.fetch and skip where no Chromium exists, as CI does.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import pytest  # noqa: E402
from test_buddy_thread_open import STUB, _ask, _display, live, page_html  # noqa: E402,F401

SIM_PATCH = """
(function() {
  var orig = window.fetch;
  window.fetch = function(url, opts) {
    if (String(url) === '/ask/similar')
      return Promise.resolve({ok: true, json: function() { return Promise.resolve({suggestions: [
        {question: 'Earlier similar question', date: '2026-10-01', chips_html: '', answer_html: '<p>Earlier.</p>', resume_id: ''}]}); }});
    return orig(url, opts);
  };
})();
"""


def _vis(pg, sel):
    return pg.evaluate("s => { var e = document.querySelector(s); if (!e) return false;"
                       " var r = e.getBoundingClientRect(); return getComputedStyle(e).display !== 'none' && r.height > 0; }", sel)


def test_fresh_page_has_entry_card_and_both_lists(live):
    pg = live()
    assert _vis(pg, ".fpa-intro-area-question")
    assert _vis(pg, "#past-questions")
    assert not _vis(pg, "#fpa-live-actions")


def test_running_turn_hides_the_entry_card_and_lists(live):
    pg = live()
    pg.evaluate("window.fetch = (function(f){ return function(u,o){ if (String(u)==='/ask') return new Promise(function(){}); return f(u,o); }; })(window.fetch); 0")
    pg.fill("#ask-q", "A question")
    pg.click("#ask-btn")
    pg.wait_for_selector("#ask-thread .ask-loading")
    assert not _vis(pg, ".fpa-intro-area-question")
    assert not _vis(pg, "#past-questions")
    assert not _vis(pg, "#ask-recent")
    assert _vis(pg, "#ask-new")


def test_finished_conversation_is_chat_only_then_new_conversation_returns(live):
    pg = live()
    _ask(pg, "First question")
    pg.wait_for_selector("#fu")
    assert not _vis(pg, ".fpa-intro-area-question")
    assert not _vis(pg, "#past-questions")
    assert not _vis(pg, "#ask-sim")
    assert pg.get_attribute("#fpa-live-actions a", "href") == "/tools/fpa-buddy/questions"
    pg.click("#ask-new")
    pg.wait_for_function("!document.querySelector('#ask-thread').children.length")
    assert _vis(pg, ".fpa-intro-area-question")
    assert _vis(pg, "#past-questions")
    assert not _vis(pg, "#fpa-live-actions")


def test_failed_first_turn_brings_the_entry_card_back_with_the_question(live):
    pg = live()
    pg.evaluate("window.__fail = true; window.__askRecover = {pollMs: 40, graceMs: 40, maxMs: 300}; 0")
    pg.fill("#ask-q", "Will fail")
    pg.click("#ask-btn")
    pg.wait_for_selector("#ask-thread .ask-loading", state="detached", timeout=20000)
    pg.wait_for_function("document.querySelector('#ask-q').value === 'Will fail'")
    assert _vis(pg, ".fpa-intro-area-question")
    assert pg.input_value("#ask-q") == "Will fail"


def test_recent_conversations_sits_above_all_questions(live):
    pg = live()
    order = pg.evaluate("""() => {
      var r = document.getElementById('ask-recent'), a = document.getElementById('pq-all');
      return !!(r.compareDocumentPosition(a) & Node.DOCUMENT_POSITION_FOLLOWING);
    }""")
    assert order is True


@pytest.mark.parametrize("width", [1280, 390])
def test_suggestions_are_their_own_box_and_do_not_resize_the_example(live, width):
    pg = live(width=width)
    pg.evaluate(SIM_PATCH)
    before = pg.evaluate("document.querySelector('.fpa-intro-area-example').getBoundingClientRect().height")
    pg.fill("#ask-q", "A new question")
    pg.click("#ask-btn")
    pg.wait_for_selector("#ask-sim:not([hidden])")
    assert pg.evaluate("!document.querySelector('.ask-card').contains(document.getElementById('ask-sim'))")
    card = pg.evaluate("document.querySelector('.ask-card').getBoundingClientRect().bottom")
    sim = pg.evaluate("document.getElementById('ask-sim').getBoundingClientRect().top")
    assert sim >= card
    after = pg.evaluate("document.querySelector('.fpa-intro-area-example').getBoundingClientRect().height")
    assert abs(after - before) < 0.5
    pg.click("#ask-anyway")
    pg.wait_for_selector("#fu")
