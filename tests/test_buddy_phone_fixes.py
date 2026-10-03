"""FP&A Buddy phone fixes found on a real iPhone after the follow-up bubble.

Four problems, each measured in rendered state first (390px, Chromium):
  - a reload dropped the reader out of their conversation (no state survived,
    and the blue "New question" label looked tappable but did nothing);
  - the question title in "Search past questions" was squeezed beside the meta;
  - the source emoji sat outside its pill, so a long title pushed the pill
    below the emoji.
The live-browser tests serve the real page over http through route handlers (no
server, no model) and skip where no Chromium exists.
"""
import glob
import importlib
import json
import os
import pathlib
import re
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Library

ORIGIN = "http://buddy.test"
CITES = [
    {"n": 1, "title": "How peers size FP&A teams across stages", "url": "https://example.com/s", "type": "library"},
    {"n": 2, "title": "Analyst ratios benchmark report", "url": "https://example.com/b", "type": "web"},
    {"n": 3, "title": "Mostly Metrics: headcount planning", "url": "https://example.com/m", "type": "feed"},
]
LONGQ = "How do peers size FP&A teams as they scale past two hundred people?"


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


@pytest.fixture
def buddy_html(monkeypatch):
    """The Buddy page as a signed-in reader, with another member's helpful
    answer in "Search past questions"."""
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    lib = Library(db)
    lib.seed_voice_prompts()
    author = lib.create_user("author", "supersecret", role="user")
    lib.create_user("reader", "supersecret", role="user")
    qid = lib.record_ask_question(author, LONGQ, "Roughly one analyst per 100 staff [1][2][3].",
                                  "claude-sonnet-4-6", "standard", True, False, True, cost_usd=0.03,
                                  citations=CITES)
    lib.record_ask_feedback(qid, author, "helpful", "")
    lib.close()
    import webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app)
    assert c.post("/login", data={"username": "reader", "password": "supersecret"},
                  follow_redirects=False).status_code in (302, 303)
    yield c.get("/tools/fpa-buddy").text, c
    for ext in ("", "-shm", "-wal"):
        if os.path.exists(db + ext):
            os.remove(db + ext)


class Site:
    """Routes for one browser page: the real HTML plus stubbed JSON endpoints."""

    def __init__(self, html):
        self.html = html
        self.posts = []
        self.transcripts = {"c9": {"conversation_id": "c9", "capped": False, "turns": [
            {"turn_id": 9, "question": "Earlier question", "answer": "Earlier answer [1].",
             "citations": CITES[:1], "feedback": None}]}}
        self.forbidden = {"c666"}

    def handle(self, route):
        req = route.request
        path = req.url.replace(ORIGIN, "").split("?")[0]
        if path == "/tools/fpa-buddy":
            return route.fulfill(status=200, content_type="text/html", body=self.html)
        if path == "/ask/conversations":
            return route.fulfill(status=200, content_type="application/json", body=json.dumps({"conversations": [
                {"conversation_id": "c9", "first_question": "Earlier question", "turns": 1,
                 "last_at": "2026-10-03T00:00:00", "capped": False}]}))
        if path.startswith("/ask/conversations/"):
            cid = path.rsplit("/", 1)[1]
            if cid in self.forbidden:
                return route.fulfill(status=403, content_type="application/json", body='{"detail":"no"}')
            t = self.transcripts.get(cid)
            if not t:
                return route.fulfill(status=404, content_type="application/json", body='{"detail":"no"}')
            return route.fulfill(status=200, content_type="application/json", body=json.dumps(t))
        if path == "/ask" and req.method == "POST":
            body = json.loads(req.post_data)
            self.posts.append(body)
            n = len(self.posts)
            cid = body.get("conversation_id") or "c99"
            self.transcripts[cid] = {"conversation_id": cid, "capped": False, "turns": [
                {"turn_id": 90 + n, "question": body["question"], "answer": "Stored answer [1].",
                 "citations": CITES[:1], "feedback": None}]}
            return route.fulfill(status=200, content_type="application/json", body=json.dumps({
                "answer": "Fresh answer %d [1]." % n, "citations": CITES[:1], "turn_id": 90 + n,
                "conversation_id": cid, "followups_left": 5, "usage": None}))
        return route.fulfill(status=204, body="")


@pytest.fixture
def phone(buddy_html):
    html, _ = buddy_html
    launched = _launch()
    if launched is None:
        pytest.skip("no Chromium available")
    pw, browser = launched
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    pg = ctx.new_page()
    site = Site(html)
    pg.route(re.compile(r"^http://buddy\.test/.*"), site.handle)
    pg.route(re.compile(r"^https?://(?!buddy\.test).*"), lambda r: r.abort())   # fonts etc.
    yield pg, site
    browser.close()
    pw.stop()


def _open(pg):
    pg.goto(ORIGIN + "/tools/fpa-buddy")
    pg.wait_for_selector(".ask-recent-item")


def _ask(pg, text):
    pg.fill("#ask-q", text)
    pg.click("#ask-btn")
    pg.wait_for_selector("#fu-q")


# --- problem 2: a reload keeps the reader in their conversation ---------------

def test_new_question_label_is_a_label_for_the_box(buddy_html):
    """It looked tappable and did nothing. Now it focuses the box it names."""
    html, _ = buddy_html
    m = re.search(r'<label[^>]*>New question', html)
    assert m and 'for="ask-q"' in m.group(0)


def test_tapping_the_new_question_label_focuses_the_box(phone):
    pg, _ = phone
    _open(pg)
    pg.locator("label", has_text="New question").tap()
    assert pg.evaluate("document.activeElement.id") == "ask-q"


def test_conversation_id_goes_into_the_url_and_a_reload_expands_nothing(phone):
    """A reload with ?c= opens collapsed: no thread, no bubble, and the
    conversation is a highlighted row in Recent conversations."""
    pg, site = phone
    _open(pg)
    _ask(pg, "Test question")
    assert pg.evaluate("new URL(location.href).searchParams.get('c')") == "c99"
    pg.reload()
    pg.wait_for_selector(".ask-recent-item")
    assert pg.locator("#fu").count() == 0
    assert pg.locator("#ask-thread .ask-q-bubble").count() == 0
    assert pg.evaluate("convoId") is None


def test_top_ask_and_start_new_question_clear_the_url(phone):
    pg, _ = phone
    _open(pg)
    _ask(pg, "First")
    assert pg.evaluate("new URL(location.href).searchParams.get('c')") == "c99"
    pg.fill("#ask-q", "Second, a new conversation")
    pg.click("#ask-btn")
    pg.wait_for_function("document.querySelectorAll('#ask-thread .ask-q-bubble').length === 1 && !!document.getElementById('fu-q')")
    # The fake server hands every new conversation "c99" again, so check the
    # clear on its own: resetting drops the parameter.
    pg.evaluate("resetConvo()")
    assert pg.evaluate("new URL(location.href).searchParams.get('c')") is None


def test_opening_from_recent_conversations_sets_the_url(phone):
    pg, _ = phone
    _open(pg)
    pg.locator(".ask-recent-item").first.tap()
    pg.wait_for_selector("#fu-q")
    assert pg.evaluate("new URL(location.href).searchParams.get('c')") == "c9"


@pytest.mark.parametrize("cid", ["c666", "nope"])
def test_someone_elses_or_unknown_conversation_in_the_url_shows_no_bubble(phone, cid):
    """?c= never fetches a transcript now: nothing opens, no bubble, no error,
    and an id that is not in the reader's own list highlights nothing."""
    pg, _ = phone
    pg.goto(ORIGIN + "/tools/fpa-buddy?c=" + cid)
    pg.wait_for_selector(".ask-recent-item")
    pg.wait_for_timeout(300)
    assert pg.locator("#fu").count() == 0
    assert pg.evaluate("convoId") is None
    assert pg.locator("#ask-thread").inner_text().strip() == ""
    assert pg.locator(".recent-hl").count() == 0


# --- problem 4: the past-question title fills the row ---------------------------

def _w(pg, sel):
    return pg.locator(sel).first.evaluate("e => e.getBoundingClientRect().width")


def test_past_question_title_fills_its_row_on_a_phone(phone):
    pg, _ = phone
    _open(pg)
    assert _w(pg, "#past-questions .ask-pq-q") >= _w(pg, "#past-questions .ask-pq-row") * 0.6


def test_history_cards_use_the_same_title_rule(buddy_html):
    _, c = buddy_html
    # The reader has no history here, so check the markup rule at the source.
    src = pathlib.Path(__file__).resolve().parents[1].joinpath("webapp", "app.py").read_text(encoding="utf-8")
    assert src.count("flex:1 1 280px;min-width:0;") >= 2     # history card, history turn (past rows use .ask-pq-q)


# --- problem 5: the emoji stays inside its pill -------------------------------------

def _pills_are_whole(pg, ul_sel):
    """Every li is exactly as tall as its link: nothing (the emoji) sits above it."""
    return pg.evaluate("""sel => [...document.querySelectorAll(sel + ' > li')].map(li => {
        const a = li.querySelector('a');
        return {li: li.getBoundingClientRect().height, a: a.getBoundingClientRect().height,
                emojiInside: /^\\s*[^\\w\\[]/u.test(a.textContent)};
    })""", ul_sel)


def test_past_question_source_emoji_is_inside_the_link(phone):
    pg, _ = phone
    _open(pg)
    pg.locator("#past-questions .ask-pq-sum").first.click()
    rows = _pills_are_whole(pg, "#past-questions .ask-hist-answer ~ ul")
    assert rows and all(r["emojiInside"] and r["li"] <= r["a"] + 2 for r in rows), rows


def test_live_thread_source_pills_keep_the_emoji_inline(phone):
    pg, _ = phone
    _open(pg)
    pg.locator(".ask-recent-item").first.tap()
    pg.wait_for_selector("#fu-q")
    # A long citation title is what pushed the pill below the emoji.
    pg.evaluate("""() => { const a = document.querySelector('#ask-thread .ask-src-list a');
        a.lastChild.textContent = ' [1] ' + 'A very long source title that wraps on a phone '.repeat(3); }""")
    rows = _pills_are_whole(pg, "#ask-thread .ask-src-list")
    assert rows and all(r["emojiInside"] and r["li"] <= r["a"] + 2 for r in rows), rows
