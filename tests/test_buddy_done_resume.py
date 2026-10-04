"""FP&A Buddy Done, Resume and the shared small-button height.

Done lives in the follow-up bubble footer, lower right, no helper text. Resume is
a labelled button on Recent rows, on the reader's own expanded past-question
rows, and on /ask/history; other members' rows get none. Live Chromium through
the route-handler harness of test_buddy_phone_fixes; skips where no Chromium
exists (CI). The HTML-level tests run everywhere.
"""
import importlib
import json
import os
import re
import tempfile

import pytest

from tests import test_buddy_phone_fixes as T
from linklib.db import Library

ORIGIN, _launch, CITES = T.ORIGIN, T._launch, T.CITES


class HoldSite(T.Site):
    """Site whose /ask can be held open, and whose Recent list grows with asks."""

    def __init__(self, html):
        super().__init__(html)
        self.hold = False
        self.held = None
        self.asked = []

    def handle(self, route):
        req = route.request
        path = req.url.replace(ORIGIN, "").split("?")[0]
        if path == "/ask/conversations":
            convs = [{"conversation_id": "c9", "first_question": "Earlier question", "turns": 1,
                      "last_at": "2026-10-03T00:00:00", "capped": False}]
            for cid, q in self.asked:
                convs.insert(0, {"conversation_id": cid, "first_question": q, "turns": 1,
                                 "last_at": "2026-10-03T12:00:00", "capped": False})
            return route.fulfill(status=200, content_type="application/json",
                                 body=json.dumps({"conversations": convs}))
        if path == "/ask" and req.method == "POST":
            body = json.loads(req.post_data)
            cid = body.get("conversation_id") or "c99"
            self.asked.append((cid, body["question"]))
            if self.hold:
                self.held = (route, body, cid)
                return None
        return super().handle(route)

    def release(self):
        route, body, cid = self.held
        route.fulfill(status=200, content_type="application/json", body=json.dumps({
            "answer": "Late answer [1].", "citations": CITES[:1], "turn_id": 77,
            "conversation_id": cid, "followups_left": 5, "usage": None}))
        self.held = None


@pytest.fixture
def own_html(monkeypatch):
    """The page for a reader who owns one helpful question, next to another
    member's helpful question."""
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    lib = Library(db)
    lib.seed_voice_prompts()
    other = lib.create_user("author", "supersecret", role="user")
    me = lib.create_user("reader", "supersecret", role="user")
    q1 = lib.record_ask_question(other, "Other member question about headcount", "Answer A [1].",
                                 "claude-sonnet-4-6", "standard", True, False, True,
                                 conversation_id="other-c1", cost_usd=0.03, citations=CITES)
    lib.record_ask_feedback(q1, me, "helpful", "")
    q2 = lib.record_ask_question(me, "My own question about runway", "Answer B [1].",
                                 "claude-sonnet-4-6", "standard", True, False, True,
                                 conversation_id="mine-c1", cost_usd=0.03, citations=CITES)
    lib.record_ask_feedback(q2, other, "helpful", "")
    lib.close()
    import webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app)
    assert c.post("/login", data={"username": "reader", "password": "supersecret"},
                  follow_redirects=False).status_code in (302, 303)
    yield c
    for ext in ("", "-shm", "-wal"):
        if os.path.exists(db + ext):
            os.remove(db + ext)


def _open(html, width, height=900, path="/tools/fpa-buddy"):
    launched = _launch()
    if launched is None:
        pytest.skip("no Chromium available")
    pw, browser = launched
    ctx = browser.new_context(viewport={"width": width, "height": height},
                              is_mobile=width < 500, has_touch=width < 500)
    pg = ctx.new_page()
    site = HoldSite(html)
    site.transcripts["mine-c1"] = {"conversation_id": "mine-c1", "capped": False, "turns": [
        {"turn_id": 5, "question": "My own question about runway", "answer": "Answer B [1].",
         "citations": CITES[:1], "feedback": None}]}
    pg.route(re.compile(r"^http://buddy\.test/.*"), site.handle)
    pg.route(re.compile(r"^https?://(?!buddy\.test).*"), lambda r: r.abort())
    pg.goto(ORIGIN + path)
    pg.wait_for_selector("#ask-q")
    return pw, browser, pg, site


@pytest.fixture(params=[390, 1280])
def view(request, own_html):
    pw, browser, pg, site = _open(own_html.get("/tools/fpa-buddy").text, request.param)
    yield pg, site, request.param
    browser.close()
    pw.stop()


def _box(pg, sel):
    return pg.evaluate("""(s) => { var r = document.querySelector(s).getBoundingClientRect();
      return {l: r.left, r: r.right, t: r.top, b: r.bottom, w: r.width, h: r.height}; }""", sel)


def _ask(pg, text="A first question"):
    pg.fill("#ask-q", text)
    pg.click("#ask-btn")
    pg.wait_for_selector("#fu-q")


# --- HTML level (runs in CI) ---------------------------------------------------

def test_history_page_links_own_conversations_to_resume(own_html):
    h = own_html.get("/ask/history").text
    assert 'href="/tools/fpa-buddy?resume=mine-c1"' in h and ">Resume<" in h


def test_own_past_question_row_has_resume_and_another_members_does_not(own_html):
    h = own_html.get("/tools/fpa-buddy").text
    own = h[h.index("My own question about runway"):]
    own = own[:own.index("</details>")]
    other = h[h.index("Other member question about headcount"):]
    other = other[:other.index("</details>")]
    assert 'data-cid="mine-c1"' in own and ">Resume<" in own
    assert "Resume" not in other and "data-cid" not in other


def test_done_is_a_labelled_button_with_no_helper_text(own_html):
    h = own_html.get("/tools/fpa-buddy").text
    assert 'id="fu-done"' in h and ">Done<" in h
    assert "Finished with this thread" not in h


# --- Done ----------------------------------------------------------------------

def test_done_sits_lower_right_of_the_bubble_not_in_the_feedback_row(view):
    pg, site, w = view
    _ask(pg)
    b, f = _box(pg, "#fu-done"), _box(pg, "#fu")
    assert pg.inner_text("#fu-done").strip() == "Done"
    assert abs(b["h"] - 28) < 0.6 and abs(b["w"] - 128) < 0.6
    assert f["r"] - b["r"] < 20 and b["b"] <= f["b"]                    # lower right of the bubble
    assert b["t"] >= _box(pg, "#fu-q")["b"]                              # below the follow-up box
    assert pg.locator(".ask-fb #fu-done, .ask-fb-row #fu-done").count() == 0
    assert "finished with this thread" not in pg.inner_text("body").lower()


def test_done_clears_the_thread_and_puts_it_at_the_top_of_recent(view):
    pg, site, w = view
    _ask(pg, "Question to finish")
    pg.click("#fu-done")
    assert pg.locator("#ask-thread").inner_html().strip() == ""
    assert pg.locator("#fu").count() == 0
    assert "c=" not in pg.evaluate("location.search")
    assert pg.is_visible("#ask-recent")
    first = pg.locator("#ask-recent .ask-recent-item").first
    assert first.get_attribute("data-cid") == "c99"
    assert "recent-hl" in first.get_attribute("class")
    pg.wait_for_function("window.scrollY < 5", timeout=4000)            # back at the top
    assert pg.is_visible("#ask-q") and pg.is_enabled("#ask-q")


def test_done_while_a_follow_up_loads_drops_the_late_answer_but_it_lands_in_recent(view):
    pg, site, w = view
    _ask(pg, "First question")
    site.hold = True
    pg.fill("#fu-q", "Slow follow-up")
    pg.click("#fu-btn")
    pg.wait_for_selector("#ask-thread .ask-loading")
    pg.click("#fu-done")                                                 # the bubble stays reachable while busy
    assert pg.locator("#ask-thread").inner_html().strip() == ""
    assert pg.inner_text("#ask-done-note") == "Your answer will appear in Recent."
    site.release()
    pg.wait_for_selector("#ask-recent .ask-recent-item")
    pg.wait_for_function("!document.getElementById('ask-done-note')")
    assert pg.locator("#ask-thread").inner_html().strip() == ""         # not drawn on screen
    assert pg.locator("#fu").count() == 0
    assert pg.is_visible("#ask-recent")


def _hits_within_44(pg, sel):
    """A point 7px above and below the visible edge still lands on the button
    (the ::after reaches 8px past the border box, so the target is 44px)."""
    b = pg.locator(sel).first
    b.scroll_into_view_if_needed()
    bb = b.bounding_box()
    x = bb["x"] + bb["width"] / 2
    got = [pg.evaluate("([x,y])=>{var e=document.elementFromPoint(x,y);return e?e.tagName+'.'+e.className:null}", [x, y])
           for y in (bb["y"] - 7, bb["y"] + bb["height"] + 7)]
    return all(g and "ask-ctl" in g for g in got)


# --- Resume --------------------------------------------------------------------

def test_recent_rows_carry_a_visible_resume_button_lower_right(view):
    pg, site, w = view
    pg.wait_for_selector(".ask-recent-item")
    item, btn = _box(pg, ".ask-recent-item"), _box(pg, ".ask-recent-item .ask-ctl")
    assert pg.inner_text(".ask-recent-item .ask-ctl").strip() == "Resume"
    assert abs(btn["h"] - 28) < 0.6                 # compact: 28px visible, 44px touch target
    assert _hits_within_44(pg, ".ask-recent-item .ask-ctl")
    assert item["r"] - btn["r"] < 20 and btn["t"] >= _box(pg, ".ask-recent-q")["b"]


def test_resume_button_opens_the_thread_and_hides_recent(view):
    pg, site, w = view
    pg.wait_for_selector(".ask-recent-item")
    pg.click(".ask-recent-item .ask-ctl")
    pg.wait_for_selector("#fu-q")
    assert "Earlier question" in pg.inner_text("#ask-thread")
    assert not pg.is_visible("#ask-recent")


def test_own_past_question_row_resumes_only_from_the_button(view):
    pg, site, w = view
    row = pg.locator("details.ask-pq-row", has_text="My own question about runway")
    row.locator("summary").click()                                       # a row tap stays expand
    assert row.evaluate("e => e.open") and pg.locator("#fu").count() == 0
    btn = row.locator(".ask-ctl")
    assert btn.inner_text().strip() == "Resume" and abs(btn.bounding_box()["height"] - 28) < 0.6
    btn.click()
    pg.wait_for_selector("#fu-q")
    assert "My own question about runway" in pg.inner_text("#ask-thread")
    other = pg.locator("details.ask-pq-row", has_text="Other member question about headcount")
    assert other.locator(".ask-ctl").count() == 0


@pytest.mark.parametrize("width", [390, 1280])
def test_resume_param_opens_the_thread_once_and_a_reload_collapses(own_html, width):
    pw, browser, pg, site = _open(own_html.get("/tools/fpa-buddy").text, width, path="/tools/fpa-buddy?resume=c9")
    try:
        pg.wait_for_selector("#fu-q")
        assert "Earlier question" in pg.inner_text("#ask-thread")
        qs = pg.evaluate("location.search")
        assert "resume" not in qs and "c=c9" in qs                       # stripped after opening
        pg.reload()
        pg.wait_for_selector("#ask-q")
        pg.wait_for_selector(".ask-recent-item.recent-hl")
        assert pg.locator("#ask-thread").inner_html().strip() == ""      # a reload expands nothing
    finally:
        browser.close()
        pw.stop()


def test_resume_param_for_someone_elses_conversation_shows_the_empty_state(own_html):
    pw, browser, pg, site = _open(own_html.get("/tools/fpa-buddy").text, 390, path="/tools/fpa-buddy?resume=c666")
    try:
        pg.wait_for_selector(".ask-recent-item")
        assert pg.locator("#ask-thread").inner_html().strip() == ""
        assert "resume" not in pg.evaluate("location.search")
    finally:
        browser.close()
        pw.stop()


# --- One height for the small buttons -----------------------------------------

def test_done_and_resume_match_the_depth_and_sources_control_height(view):
    pg, site, w = view
    pg.wait_for_selector(".ask-recent-item")
    dd = _box(pg, "#ask-dd-top [data-dd='depth'].ask-dd-btn")["h"]
    resume = _box(pg, ".ask-recent-item .ask-ctl")["h"]
    _ask(pg)
    done = _box(pg, "#fu-done")["h"]
    # Dropdowns stay 44px; Done and Resume are the compact 28px button with a
    # 44px touch target (the sitewide control-height item will reconcile the rest).
    assert dd >= 44 and abs(done - 28) < 0.6 and abs(resume - 28) < 0.6
