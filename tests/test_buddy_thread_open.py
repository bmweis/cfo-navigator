"""FP&A Buddy: what the page does while a conversation is open.

Three things change when a thread appears, and all three undo when it clears:
the illustrative example is hidden (and the form takes the full width),
"Search past questions" folds to one line, and the new conversation shows up
in "Recent conversations" without a reload. The same file also pins the
bubble visibility rules from the earlier review that only had indirect
coverage (a first turn that hits the cap, a failed first ask, one bubble that
always sits under the latest reply).

Live-browser tests stub window.fetch (no server, no model) and skip where no
Chromium exists, as CI does.
"""
import glob
import importlib
import os
import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Library


@pytest.fixture
def page_html(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    seed = Library(db)
    seed.seed_voice_prompts()
    author = seed.create_user("author", "supersecret", role="user")
    qid = seed.record_ask_question(author, "How do peers size FP&A?", "About one analyst per 100 staff [1].",
                                   "claude-sonnet-4-6", "standard", True, False, True, cost_usd=0.03,
                                   citations=[{"n": 1, "title": "Sizing", "url": "https://example.com/s", "type": "library"}])
    seed.record_ask_feedback(qid, author, "helpful", "")
    seed.close()
    import webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app)
    assert c.post("/login", data={"username": "admin", "password": "adminpass"},
                  follow_redirects=False).status_code in (302, 303)
    yield c.get("/tools/fpa-buddy").text
    for ext in ("", "-shm", "-wal"):
        if os.path.exists(db + ext):
            os.remove(db + ext)


def _launch():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    pw = sync_playwright().start()
    try:
        return pw, pw.chromium.launch()
    except Exception:
        pass
    for path in glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome"):
        try:
            return pw, pw.chromium.launch(executable_path=path)
        except Exception:
            continue
    pw.stop()
    return None


# window.__convos is the server's list; __ask_count / __list_count count calls.
STUB = """
window.__calls = [];
window.__list_count = 0;
window.__convos = [];
window.__next = {};
window.__fail = false;
window.fetch = function(url, opts) {
  var s = String(url);
  if (s === '/ask/conversations') {
    window.__list_count++;
    return Promise.resolve({ok: true, json: function() { return Promise.resolve({conversations: window.__convos}); }});
  }
  if (s.indexOf('/ask/conversations/') === 0)
    return Promise.resolve({ok: true, json: function() { return Promise.resolve({
      conversation_id: 'c9', capped: false, turns: [
        {turn_id: 11, question: 'Earlier question', answer: 'Earlier answer.', citations: [], feedback: null}]}); }});
  if (window.__fail) return Promise.reject(new Error('boom'));
  var body = JSON.parse(opts.body);
  window.__calls.push(body);
  var n = window.__calls.length;
  var d = Object.assign({
    answer: 'Answer ' + n + '.', citations: [], turn_id: n,
    conversation_id: body.conversation_id || 'c1', followups_left: 5, usage: null
  }, window.__next);
  window.__next = {};
  return Promise.resolve({ok: true, json: function() { return Promise.resolve(d); }});
};
"""


@pytest.fixture
def live(page_html, tmp_path):
    launched = _launch()
    if launched is None:
        pytest.skip("no Chromium available")
    pw, browser = launched
    f = tmp_path / "buddy.html"
    f.write_text(page_html, encoding="utf-8")
    pages = []

    def open_page(width=1280, height=900, hash_="", query=""):
        pg = browser.new_page(viewport={"width": width, "height": height})
        pg.add_init_script(STUB)
        pg.goto(f.as_uri() + query + hash_)
        pages.append(pg)
        return pg

    yield open_page
    browser.close()
    pw.stop()


def _ask(pg, text, conv=None):
    if conv:
        pg.evaluate("c => { window.__next = {conversation_id: c}; }", conv)
    pg.fill("#ask-q", text)
    pg.click("#ask-btn")
    pg.wait_for_selector("#ask-thread .ask-loading", state="detached")


def _display(pg, sel):
    return pg.evaluate("s => getComputedStyle(document.querySelector(s)).display", sel)


def _pq_open(pg):
    return pg.evaluate("document.getElementById('past-questions').open")


def _page_cols(pg):
    return pg.evaluate("""() => {
      var l = document.querySelector('.fpa-intro-layout').getBoundingClientRect();
      var q = document.querySelector('.fpa-intro-area-question').getBoundingClientRect();
      return {layout: l.width, question: q.width};
    }""")


# --- the empty state is untouched -------------------------------------------

def test_empty_state_shows_the_example_and_the_past_questions_open(live):
    pg = live()
    assert _display(pg, ".fpa-intro-area-example") != "none"
    assert pg.evaluate("document.getElementById('fpa-page').classList.contains('fpa-thread-open')") is False
    assert _pq_open(pg) is True
    # Two columns: the question box is about half the layout.
    cols = _page_cols(pg)
    assert cols["question"] < cols["layout"] * 0.6


def test_empty_state_past_questions_cannot_be_folded(live):
    pg = live()
    # Even a keyboard toggle (Enter on the summary) leaves it open.
    pg.focus("#past-questions > summary")
    pg.keyboard.press("Enter")
    pg.wait_for_timeout(100)
    assert _pq_open(pg) is True
    assert pg.locator("#past-questions .ask-hist-answer").count() == 1


# --- a thread open: example gone, past questions folded ----------------------

def test_first_answer_hides_the_example_and_folds_past_questions(live):
    pg = live()
    _ask(pg, "First question")
    pg.wait_for_selector("#fu")
    assert pg.evaluate("document.getElementById('fpa-page').classList.contains('fpa-thread-open')") is True
    assert _display(pg, ".fpa-intro-area-example") == "none"
    assert _pq_open(pg) is False
    # One line: the summary is all that shows.
    assert pg.evaluate("document.getElementById('past-questions').getBoundingClientRect().height") < 48
    assert pg.is_visible("#past-questions > summary")
    assert not pg.is_visible("#past-questions .ask-hist-answer")
    # And the form is one full-width column now.
    cols = _page_cols(pg)
    assert cols["question"] > cols["layout"] * 0.95


def test_the_folded_line_opens_and_closes_on_a_tap(live):
    pg = live()
    _ask(pg, "First question")
    pg.wait_for_selector("#fu")
    pg.click("#past-questions > summary")
    assert _pq_open(pg) is True
    assert pg.is_visible("#past-questions .ask-pq-row")
    pg.click("#past-questions > summary")
    assert _pq_open(pg) is False


def test_a_second_top_question_does_not_refold_what_the_reader_opened(live):
    pg = live()
    _ask(pg, "First question")
    pg.wait_for_selector("#fu")
    pg.click("#past-questions > summary")
    _ask(pg, "Second question", conv="c2")
    pg.wait_for_selector("#fu")
    assert _pq_open(pg) is True


def test_starting_a_new_question_brings_the_example_and_open_list_back(live):
    pg = live()
    pg.evaluate("window.__next = {followups_left: 0}")
    _ask(pg, "Last one")
    pg.wait_for_selector("#fu .fu-limit")
    assert _display(pg, ".fpa-intro-area-example") == "none"
    pg.click("#fu .fu-limit a")
    assert pg.locator("#ask-thread .ask-answer").count() == 0
    assert _display(pg, ".fpa-intro-area-example") != "none"
    assert _pq_open(pg) is True
    assert pg.evaluate("document.getElementById('fpa-page').classList.contains('fpa-thread-open')") is False


def test_opening_a_recent_conversation_does_the_same(live):
    pg = live()
    pg.evaluate("""() => { window.__convos = [{conversation_id: 'c9', first_question: 'Earlier question',
      turns: 1, last_at: new Date().toISOString(), capped: false}]; loadRecent(); }""")
    pg.wait_for_selector(".ask-recent-item")
    pg.click(".ask-recent-item")
    pg.wait_for_selector("#fu-q")
    assert _display(pg, ".fpa-intro-area-example") == "none"
    assert _pq_open(pg) is False


def test_a_phone_gets_the_same_rules(live):
    pg = live(width=390, height=844)
    assert _display(pg, ".fpa-intro-area-example") != "none"
    assert _pq_open(pg) is True
    _ask(pg, "First question")
    pg.wait_for_selector("#fu")
    assert _display(pg, ".fpa-intro-area-example") == "none"
    assert _pq_open(pg) is False
    assert pg.evaluate("document.documentElement.scrollWidth") <= 390


def test_a_search_the_reader_just_ran_stays_open_when_the_thread_arrives(live):
    pg = live(query="?pq=peers")
    _ask(pg, "First question")
    pg.wait_for_selector("#fu")
    assert _pq_open(pg) is True


def test_the_search_form_carries_the_open_conversation(live):
    pg = live()
    _ask(pg, "First question")
    pg.wait_for_selector("#fu")
    pg.evaluate("""() => { var f = document.querySelector('#past-questions form');
      f.dispatchEvent(new Event('submit', {cancelable: true})); }""")
    assert pg.evaluate("document.querySelector('#past-questions form input[name=c]').value") == "c1"


def test_the_search_form_adds_nothing_without_a_conversation(live):
    pg = live()
    pg.evaluate("""() => { var f = document.querySelector('#past-questions form');
      f.dispatchEvent(new Event('submit', {cancelable: true})); }""")
    assert pg.locator("#past-questions form input[name=c]").count() == 0


# --- Recent conversations refresh in place ----------------------------------

def test_a_new_conversation_appears_in_recent_without_a_reload_or_a_second_request(live):
    pg = live()
    assert pg.evaluate("window.__list_count") == 1          # the one load-time fetch
    _ask(pg, "Brand new question")
    pg.wait_for_selector("#fu")
    assert pg.evaluate("window.__list_count") == 1          # updated in place, not refetched
    items = pg.locator("#ask-recent .ask-recent-item")
    assert items.count() == 1
    assert "Brand new question" in items.first.inner_text()
    assert "1 turn" in items.first.inner_text()
    assert pg.locator("#ask-recent .ask-section-label").inner_text() == "Recent conversations"


def test_a_follow_up_moves_its_conversation_to_the_top_with_the_new_count(live):
    pg = live()
    pg.evaluate("""() => { window.__convos = [{conversation_id: 'old', first_question: 'An older one',
      turns: 3, last_at: new Date(Date.now() - 86400000).toISOString(), capped: false}]; loadRecent(); }""")
    pg.wait_for_selector(".ask-recent-item")
    _ask(pg, "Fresh question", conv="c2")
    pg.wait_for_selector("#fu")
    pg.fill("#fu-q", "And a follow-up")
    pg.click("#fu-btn")
    pg.wait_for_function("window.__calls.length === 2")
    pg.wait_for_function("!document.querySelector('.ask-loading')")
    cids = pg.evaluate("Array.from(document.querySelectorAll('#ask-recent .ask-recent-item')).map(e => e.dataset.cid)")
    assert cids == ["c2", "old"]
    first = pg.locator("#ask-recent .ask-recent-item").first.inner_text()
    assert "Fresh question" in first and "2 turns" in first     # label stays the first question


def test_the_list_never_grows_past_the_servers_five(live):
    pg = live()
    for i in range(7):
        _ask(pg, "Question %d" % i, conv="c%d" % (10 + i))
        pg.wait_for_selector("#fu")
    cids = pg.evaluate("Array.from(document.querySelectorAll('#ask-recent .ask-recent-item')).map(e => e.dataset.cid)")
    assert cids == ["c16", "c15", "c14", "c13", "c12"]


def test_a_conversation_that_reaches_its_limit_is_marked_at_limit(live):
    pg = live()
    pg.evaluate("window.__next = {followups_left: 0}")
    _ask(pg, "Last one")
    pg.wait_for_selector("#fu .fu-limit")
    assert "at limit" in pg.locator("#ask-recent .ask-recent-item").first.inner_text()


def test_the_refreshed_list_is_what_start_a_new_question_reveals(live):
    pg = live()
    assert pg.evaluate("getComputedStyle(document.getElementById('ask-recent')).display") == "none"
    _ask(pg, "Brand new question")
    pg.wait_for_selector("#fu")
    # Hidden while the thread is open, as before.
    assert pg.evaluate("getComputedStyle(document.getElementById('ask-recent')).display") == "none"
    pg.evaluate("resetConvo()")
    assert pg.evaluate("getComputedStyle(document.getElementById('ask-recent')).display") == "block"
    assert "Brand new question" in pg.inner_text("#ask-recent")


def test_a_capped_turn_that_was_not_recorded_adds_nothing(live):
    pg = live()
    pg.evaluate("window.__next = {capped: true, conversation_id: ''}")
    _ask(pg, "Over the cap")
    assert pg.locator("#ask-recent .ask-recent-item").count() == 0


# --- bubble visibility, the cases only indirectly covered before ------------

def test_a_first_turn_that_hits_the_cap_shows_the_limit_state_without_a_conversation(live):
    pg = live()
    pg.evaluate("window.__next = {capped: true, conversation_id: ''}")
    _ask(pg, "Over the cap")
    pg.wait_for_selector("#fu .fu-limit")
    assert pg.is_disabled("#fu-q") and pg.is_disabled("#fu-btn")
    assert pg.evaluate("convoId") is None


def test_a_failed_first_ask_leaves_no_bubble(live):
    pg = live()
    pg.evaluate("window.__fail = true")
    pg.fill("#ask-q", "Will fail")
    pg.click("#ask-btn")
    pg.wait_for_selector("#ask-thread .ask-answer")
    pg.wait_for_selector("#ask-thread .ask-loading", state="detached")
    assert pg.locator("#fu, #fu-q").count() == 0
    assert "Something went wrong" in pg.inner_text("#ask-thread")


def test_there_is_one_bubble_and_it_always_follows_the_latest_reply(live):
    pg = live()
    _ask(pg, "First question")
    pg.wait_for_selector("#fu")
    for text in ("Follow-up one", "Follow-up two"):
        n = pg.evaluate("window.__calls.length")
        pg.fill("#fu-q", text)
        pg.click("#fu-btn")
        pg.wait_for_function("n => window.__calls.length === n + 1", arg=n)
        pg.wait_for_function("!document.querySelector('.ask-loading')")
        assert pg.locator("#fu").count() == 1
        assert pg.evaluate("document.getElementById('ask-thread').lastElementChild.id") == "fu"
    assert pg.locator("#ask-thread .ask-q-bubble").count() == 3
