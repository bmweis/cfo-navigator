"""FP&A Buddy: the top box is always a new question; follow-ups live in a bubble.

The page used to turn its one Ask button into "Ask follow-up" after the first
answer, so a new question and a follow-up shared a box and it was never clear
which one a click would do. Now the top box starts a fresh conversation every
time, and a sticky follow-up bubble appears under the latest reply (and only
once a conversation exists). Depth and sources stay one shared state: the
bubble's chips are clones of the top controls.

The live-browser tests stub window.fetch (no server, no model) and skip where
no Chromium exists.
"""
import glob
import importlib
import os
import pathlib
import shutil
import subprocess
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


def test_top_box_is_labelled_as_a_new_question_and_nothing_is_docked(page_html):
    assert "New question" in page_html and "starts a new conversation" in page_html
    assert 'onclick="doAsk()"' in page_html          # top button never says follow-up
    assert 'id="fu"' not in page_html                # the bubble is built by JS, on demand
    assert "ask-capped" not in page_html             # the limit message lives in the bubble


def test_rendered_script_is_valid_javascript(page_html, tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    start = page_html.index("var COST =")
    end = page_html.index("</script>", start)
    f = tmp_path / "buddy.js"
    f.write_text(page_html[start:end], encoding="utf-8")
    r = subprocess.run([node, "--check", str(f)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


# --- live browser -----------------------------------------------------------

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


STUB = """
window.__calls = [];
window.__next = {followups_left: 5, answer: 'First answer [1].'};
window.fetch = function(url, opts) {
  if (String(url).indexOf('/ask/conversations') === 0)
    return Promise.resolve({ok: true, json: function() { return Promise.resolve({conversations: []}); }});
  var body = JSON.parse(opts.body);
  window.__calls.push(body);
  var n = window.__calls.length;
  var d = Object.assign({
    answer: 'Answer ' + n + '.', citations: [{n: 1, title: 'T', url: 'https://example.com/a', type: 'library'}],
    turn_id: n, conversation_id: 'c1', followups_left: 5, usage: null
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
    pg = browser.new_page(viewport={"width": 1280, "height": 900})
    pg.add_init_script(STUB)
    pg.goto(f.as_uri())
    yield pg
    browser.close()
    pw.stop()


def _settle(pg):
    """Wait out the smooth scroll the page starts after each answer."""
    pg.evaluate("""() => new Promise(res => { var last = -1, n = 0;
      (function tick() { var y = window.scrollY; n = (y === last) ? n + 1 : 0; last = y;
        if (n >= 5) res(); else requestAnimationFrame(tick); })(); })""")


def _ask(pg, text):
    pg.fill("#ask-q", text)
    pg.click("#ask-btn")


def test_first_answer_brings_the_bubble_and_top_button_stays_ask(live):
    pg = live
    assert pg.locator("#fu").count() == 0
    _ask(pg, "First question")
    pg.wait_for_selector("#fu-q")
    assert pg.locator("#ask-btn").inner_text() == "Ask"
    assert pg.locator("#ask-btn").is_enabled()
    # The bubble is the last child of the thread: under the latest reply.
    assert pg.evaluate("document.getElementById('ask-thread').lastElementChild.id") == "fu"
    assert pg.evaluate("getComputedStyle(document.getElementById('fu')).position") == "sticky"
    first = pg.evaluate("window.__calls[0]")
    assert first["conversation_id"] is None
    # Sources are the top controls only: no depth tier, no cloned chips.
    assert first["sources"] == ["library", "web"]


def test_follow_up_continues_the_conversation_and_top_box_starts_a_new_one(live):
    pg = live
    _ask(pg, "First question")
    pg.wait_for_selector("#fu-q")
    pg.fill("#fu-q", "A follow-up")
    pg.click("#fu-btn")
    pg.wait_for_function("document.querySelectorAll('#ask-thread .ask-answer').length === 2")
    pg.wait_for_selector("#fu-q")
    assert pg.evaluate("window.__calls[1].conversation_id") == "c1"
    assert pg.evaluate("window.__calls[1].question") == "A follow-up"
    # Top box: a fresh conversation, the old thread is cleared first.
    _ask(pg, "Something else entirely")
    pg.wait_for_function("window.__calls.length === 3")
    pg.wait_for_selector("#fu-q")
    assert pg.evaluate("window.__calls[2].conversation_id") is None
    assert pg.locator("#ask-thread .ask-answer").count() == 1


def test_bubble_chips_and_top_chips_share_one_state(live):
    pg = live
    _ask(pg, "First question")
    pg.wait_for_selector("#fu-q")
    _settle(pg)
    pg.click("#fu .ask-tag[data-tier='deep']")
    assert pg.evaluate("selectedTier") == "deep"
    assert "active" in pg.get_attribute(".fpa-intro-area-controls .ask-tag[data-tier='deep']", "class")
    assert "active" not in pg.get_attribute(".fpa-intro-area-controls .ask-tag[data-tier='standard']", "class")
    pg.click("#fu summary")
    pg.click("#fu .ask-tag[data-source='feed']")
    assert "active" in pg.get_attribute(".fpa-intro-area-controls .ask-tag[data-source='feed']", "class")
    pg.fill("#fu-q", "Again")
    pg.click("#fu-btn")
    pg.wait_for_function("window.__calls.length === 2")
    second = pg.evaluate("window.__calls[1]")
    assert second["effort"] == "deep"
    assert sorted(second["sources"]) == ["feed", "library", "web"]
    # Re-rendered bubble reflects the state.
    pg.wait_for_selector("#fu-q")
    assert "active" in pg.get_attribute("#fu .ask-tag[data-tier='deep']", "class")


def test_typing_in_the_bubble_collapses_controls_to_a_summary(live):
    pg = live
    _ask(pg, "First question")
    pg.wait_for_selector("#fu-q")
    assert pg.is_visible("#fu .fu-meta") and not pg.is_visible("#fu .fu-sum")
    pg.focus("#fu-q")
    assert not pg.is_visible("#fu .fu-meta") and pg.is_visible("#fu .fu-sum")
    assert "Saved archive" in pg.inner_text("#fu .fu-sum")


def test_limit_state_replaces_the_bubble_input_and_offers_a_new_question(live):
    pg = live
    pg.evaluate("window.__next = {followups_left: 0}")
    _ask(pg, "Last one")
    pg.wait_for_selector("#fu .fu-limit")
    assert pg.is_disabled("#fu-q") and pg.is_disabled("#fu-btn")
    assert pg.evaluate("getComputedStyle(document.getElementById('fu')).position") == "static"
    assert pg.inner_text("#fu-btn") == "Limit reached"
    assert pg.locator("#ask-btn").is_enabled()          # a new question is always possible
    pg.click("#fu .fu-limit a")
    assert pg.locator("#fu").count() == 0
    assert pg.locator("#ask-thread .ask-answer").count() == 0


def test_model_text_links_stay_inert_on_the_live_page(live):
    pg = live
    pg.evaluate("window.__next = {answer: 'See [the memo](https://evil.example/x) now [1].'}")
    _ask(pg, "Q")
    pg.wait_for_selector("#fu-q")
    assert pg.locator("a[href^='https://evil.example']").count() == 0
    assert "[the memo](https://evil.example/x)" in pg.inner_text("#ask-thread .ask-answer")
    # A resolved [n] marker still links, to the citation snapshot's own URL.
    assert pg.locator("#ask-thread sup.cite a[href='https://example.com/a']").count() == 1
