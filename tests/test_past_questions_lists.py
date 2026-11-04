"""Past-questions lists on /tools/fpa-buddy: collapsed, short, with a way to see more.

The search box and its Search and Helpful only buttons are always visible. "All questions (N)" and
"Recent conversations (N)" load collapsed, show at most 6 rows (a search shows up to 10), and link to the
rest: "See all questions" is the paginated shared list at /tools/fpa-buddy/questions (10 a page, filters kept),
"See all conversations" is /ask/history, the asker's own paginated history.
"""
import importlib
import json
import os
import re
import tempfile

import pytest

from linklib.db import Library
from tests.test_buddy_phone_fixes import ORIGIN, _launch

N_PUBLIC = 25


@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    lib = Library(db)
    lib.seed_voice_prompts()
    author = lib.create_user("author", "supersecret", role="user")
    lib.create_user("reader", "supersecret", role="user")
    for i in range(N_PUBLIC):
        qid = lib.record_ask_question(author, f"Example question number {i:02d} about stage planning",
                                      f"Answer {i} [1].", "claude-sonnet-4-6", "standard", True, False, True,
                                      cost_usd=0.03, conversation_id=f"c{i}", turn_index=0,
                                      citations=[{"n": 1, "title": "T", "url": "https://example.com/a", "type": "library"}])
        if i % 2 == 0:
            lib.record_ask_feedback(qid, author, "helpful", "")
    lib.record_ask_question(author, "A private planning question", "Hidden [1].", "claude-sonnet-4-6", "standard",
                            True, False, True, conversation_id="cp", turn_index=0, is_private=True)
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


def _rows(html):
    return len(re.findall(r'<details class="ask-pq-row"', html))


def test_all_questions_list_loads_collapsed_with_a_count(env):
    html = env.get("/tools/fpa-buddy").text
    m = re.search(r'<details id="pq-all"([^>]*)>', html)
    assert m and "open" not in m.group(1)
    assert f"All questions ({N_PUBLIC})" in html          # the private one is not counted for another user


def test_expanded_list_shows_at_most_six_rows_and_a_see_all_link(env):
    html = env.get("/tools/fpa-buddy").text
    assert _rows(html) == 6
    assert 'href="/tools/fpa-buddy/questions"' in html and "See all questions" in html


def test_search_box_and_buttons_are_not_inside_a_disclosure(env):
    html = env.get("/tools/fpa-buddy").text
    sec = html[html.index('<section id="past-questions"'):html.index("</section>", html.index('<section id="past-questions"'))]
    form_at = sec.index("<form")
    before = sec[:form_at]
    assert before.count("<details") == before.count("</details>")      # the form is not inside any <details>
    assert 'name="pq"' in sec and ">Search</button>" in sec and ">Helpful only</button>" in sec


def test_a_search_opens_the_list_and_returns_matches_without_opening_it_first(env):
    html = env.get("/tools/fpa-buddy?pq=stage").text
    assert re.search(r'<details id="pq-all" class="ask-pq-list" open>', html)
    assert _rows(html) == 10
    assert "See all matches" in html and "pq=stage" in html.split("See all matches")[0].rsplit("<a ", 1)[1]
    html2 = env.get("/tools/fpa-buddy?pq=number+03").text
    assert _rows(html2) == 1 and "See all matches" not in html2


def test_helpful_only_opens_the_list(env):
    html = env.get("/tools/fpa-buddy?helpful=1").text
    assert re.search(r'<details id="pq-all"[^>]* open>', html) and "Helpful questions only (13)" in html


def test_full_list_paginates_ten_a_page_and_keeps_filters(env):
    p1 = env.get("/tools/fpa-buddy/questions").text
    assert _rows(p1) == 10 and "Next" in p1 and "Previous" not in p1 and f"All questions ({N_PUBLIC})" in p1
    p3 = env.get("/tools/fpa-buddy/questions?page=3").text
    assert _rows(p3) == 5 and "Previous" in p3 and "Next &rarr;" not in p3
    assert _rows(env.get("/tools/fpa-buddy/questions?page=99").text) == 5          # clamped to the last page
    h = env.get("/tools/fpa-buddy/questions?helpful=1").text
    assert "Helpful questions only (13)" in h and _rows(h) == 10 and "helpful=1&page=2" in h
    s = env.get("/tools/fpa-buddy/questions?pq=stage").text
    assert "pq=stage" in s and _rows(s) == 10


def test_full_list_needs_sign_in_and_hides_private_questions(env):
    from fastapi.testclient import TestClient
    import webapp.app as appmod
    anon = TestClient(appmod.app)
    r = anon.get("/tools/fpa-buddy/questions", follow_redirects=False)
    assert r.status_code in (302, 303, 401) and "/login" in r.headers.get("location", "/login")
    assert "A private planning question" not in env.get("/tools/fpa-buddy/questions?pq=private").text


def test_see_all_conversations_target_is_the_paginated_history(env):
    r = env.get("/ask/history")
    assert r.status_code == 200 and "Older" not in r.text    # reader has none; the page itself exists and paginates


def test_recent_conversations_loads_collapsed_with_a_count_and_a_see_all_link(env):
    got = _launch()
    if not got:
        pytest.skip("no Chromium available")
    pw, b = got
    try:
        html = env.get("/tools/fpa-buddy").text
        convos = [{"conversation_id": f"c{i}", "first_question": f"Earlier question {i}", "turns": 1,
                   "last_at": "2026-10-03T00:00:00", "capped": False} for i in range(5)]
        page = b.new_page(viewport={"width": 390, "height": 844})

        def handle(route):
            path = route.request.url.replace(ORIGIN, "").split("?")[0]
            if path == "/tools/fpa-buddy":
                return route.fulfill(status=200, content_type="text/html", body=html)
            if path == "/ask/conversations":
                return route.fulfill(status=200, content_type="application/json", body=json.dumps({"conversations": convos}))
            return route.fulfill(status=404, body="")
        page.route("**/*", handle)
        page.goto(ORIGIN + "/tools/fpa-buddy")
        page.wait_for_selector("#recent-list")
        assert page.evaluate("document.getElementById('recent-list').open") is False
        assert "Recent conversations (5)" in page.text_content("#recent-list > summary")
        assert page.is_visible("#recent-items .ask-recent-item") is False
        page.click("#recent-list > summary")
        assert page.locator("#recent-items .ask-recent-item").count() == 5
        assert page.get_attribute("#recent-list a[href='/ask/history']", "href") == "/ask/history"
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        # The all-questions list also loads collapsed and opens on a tap.
        assert page.evaluate("document.getElementById('pq-all').open") is False
        page.close()
    finally:
        b.close()
        pw.stop()
