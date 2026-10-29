"""Retest fix, Part A (2026-10): a Buddy answer must not be lost when the phone drops it.

Server side: the page's random request id is stored with the turn, GET
/ask/result/<id> hands back the stored payload to the asker only, and a resubmit
of the same id never runs or bills a second turn. Page side (Chromium): when the
response never arrives, the page polls and renders the stored answer.
WebKit is not available in this sandbox; the iPhone check is Brian's."""
import importlib
import json
import os
import pathlib
import re
import sys
import tempfile
import threading

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from ask_stub import ok_answer_question  # noqa: E402

from linklib.db import Library


@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    lib = Library(db)
    lib.seed_voice_prompts()
    lib.create_user("asker", "supersecret", role="user")
    lib.create_user("other", "supersecret", role="user")
    lib.close()
    import webapp.app as appmod
    importlib.reload(appmod)
    import linklib.agent as agent
    monkeypatch.setattr(agent, "answer_question", ok_answer_question)
    from fastapi.testclient import TestClient

    def client(user):
        c = TestClient(appmod.app)
        assert c.post("/login", data={"username": user, "password": "supersecret"},
                      follow_redirects=False).status_code in (302, 303)
        return c
    yield appmod, client, db
    for ext in ("", "-shm", "-wal"):
        if os.path.exists(db + ext):
            os.remove(db + ext)


RID = "req-0123456789abcdef"


def _rows(db):
    lib = Library(db)
    try:
        return [dict(r) for r in lib.conn.execute("SELECT * FROM ask_questions ORDER BY id")]
    finally:
        lib.close()


def test_request_id_is_stored_and_result_returns_the_same_payload(env):
    _, client, db = env
    c = client("asker")
    posted = c.post("/ask", json={"question": "q one", "request_id": RID}).json()
    assert posted["answer"] == "stub answer"
    rows = _rows(db)
    assert len(rows) == 1 and rows[0]["request_id"] == RID
    got = c.get(f"/ask/result/{RID}").json()
    assert got.pop("status") == "done"
    assert got == posted


def test_duplicate_request_id_does_not_run_or_bill_a_second_turn(env, monkeypatch):
    _, client, db = env
    import linklib.agent as agent
    calls = []

    def counting(*a, **k):
        calls.append(1)
        return ok_answer_question(*a, **k)
    monkeypatch.setattr(agent, "answer_question", counting)
    c = client("asker")
    first = c.post("/ask", json={"question": "q one", "request_id": RID}).json()
    second = c.post("/ask", json={"question": "q one", "request_id": RID}).json()
    assert second == first
    assert len(calls) == 1
    assert len(_rows(db)) == 1


def test_another_users_id_is_unknown_same_as_a_missing_id(env):
    _, client, _ = env
    client("asker").post("/ask", json={"question": "q one", "request_id": RID})
    other = client("other")
    assert other.get(f"/ask/result/{RID}").json() == {"status": "unknown"}
    assert other.get("/ask/result/never-existed-id-12345").json() == {"status": "unknown"}
    # and a different user posting the same id gets their own turn, not the asker's
    again = other.post("/ask", json={"question": "q two", "request_id": RID}).json()
    assert again["turn_id"] is not None


def test_running_state_and_resubmit_while_running(env, monkeypatch):
    _, client, db = env
    import linklib.agent as agent
    started, release = threading.Event(), threading.Event()

    def slow(*a, **k):
        started.set()
        release.wait(5)
        return ok_answer_question(*a, **k)
    monkeypatch.setattr(agent, "answer_question", slow)
    c = client("asker")
    c2 = client("asker")
    out = {}
    t = threading.Thread(target=lambda: out.update(r=c.post("/ask", json={"question": "q", "request_id": RID}).json()))
    t.start()
    assert started.wait(5)
    assert c2.get(f"/ask/result/{RID}").json() == {"status": "running"}
    dup = c2.post("/ask", json={"question": "q", "request_id": RID})
    assert dup.status_code == 202 and dup.json() == {"status": "running"}
    release.set()
    t.join(10)
    assert c2.get(f"/ask/result/{RID}").json()["status"] == "done"
    assert len(_rows(db)) == 1


def test_failed_turn_reads_failed_and_is_not_rerun(env, monkeypatch):
    _, client, db = env
    import linklib.agent as agent
    from linklib.agent import Answer
    calls = []

    def boom(lib, question, **k):
        calls.append(1)
        return Answer(text="", model="stub-model", failed=True, error="x")
    monkeypatch.setattr(agent, "answer_question", boom)
    c = client("asker")
    r = c.post("/ask", json={"question": "q", "request_id": RID})
    assert r.status_code == 502
    got = c.get(f"/ask/result/{RID}").json()
    assert got["status"] == "failed" and got["failed"] is True
    again = c.post("/ask", json={"question": "q", "request_id": RID})
    assert again.status_code == 502 and len(calls) == 1


def test_bad_or_missing_request_id_is_ignored(env):
    _, client, db = env
    c = client("asker")
    assert c.post("/ask", json={"question": "q", "request_id": "x"}).status_code == 200
    assert c.post("/ask", json={"question": "q2"}).status_code == 200
    assert [r["request_id"] for r in _rows(db)] == ["", ""]
    assert c.get("/ask/result/x").json() == {"status": "unknown"}


def test_result_requires_login(env):
    appmod, _, _ = env
    from fastapi.testclient import TestClient
    r = TestClient(appmod.app).get(f"/ask/result/{RID}", follow_redirects=False)
    assert r.status_code in (302, 303, 401)


# ---- the page (Chromium) ---------------------------------------------------------

from test_buddy_phone_fixes import ORIGIN, Site, buddy_html, phone  # noqa: E402,F401

Q = "How do peers size FP&A teams?"
STORED = {"status": "done", "answer": "Recovered answer [1].", "citations": [
    {"n": 1, "title": "A source", "url": "https://example.com/s", "type": "library"}],
    "turn_id": 77, "conversation_id": "c77", "followups_left": 5, "usage": None,
    "go_deeper": [], "sources": [], "feed_sources": [], "web_sources": []}


def _fast(pg):
    pg.add_init_script("window.__askRecover={pollMs:60,graceMs:400,maxMs:3000}")


def _go(pg, ask_handler, result_handler):
    _fast(pg)
    pg.route(re.compile(r"^http://buddy\.test/ask$"), ask_handler)
    pg.route(re.compile(r"^http://buddy\.test/ask/result/.*"), result_handler)
    pg.goto(ORIGIN + "/tools/fpa-buddy")
    pg.fill("#ask-q", Q)
    pg.click("#ask-btn")


def test_dropped_connection_recovers_the_stored_answer(phone):
    pg, _ = phone
    seen = {"ids": [], "polls": 0}

    def ask(r):
        seen["ids"].append(json.loads(r.request.post_data)["request_id"])
        r.abort()                          # the server stored it; the phone lost the reply

    def result(r):
        seen["polls"] += 1
        rid = r.request.url.rsplit("/", 1)[1]
        assert rid == seen["ids"][0]       # the poll asks for the id the submit sent
        body = {"status": "running"} if seen["polls"] < 3 else STORED
        r.fulfill(status=200, content_type="application/json", body=json.dumps(body))
    _go(pg, ask, result)
    pg.wait_for_selector("#ask-thread .ask-answer >> text=Recovered answer", timeout=8000)
    assert seen["polls"] >= 3
    assert "Something went wrong" not in pg.inner_text("#ask-thread")


def test_edge_timeout_page_recovers_too(phone):
    pg, _ = phone
    _go(pg, lambda r: r.fulfill(status=524, content_type="text/html", body="<html>timeout</html>"),
        lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(STORED)))
    pg.wait_for_selector("#ask-thread .ask-answer >> text=Recovered answer", timeout=8000)


def test_unknown_after_grace_shows_the_failure_line_and_restores_the_question(phone):
    pg, _ = phone
    _go(pg, lambda r: r.abort(),
        lambda r: r.fulfill(status=200, content_type="application/json", body='{"status":"unknown"}'))
    pg.wait_for_selector("#ask-thread .ask-answer span[style*='alert']", timeout=8000)
    assert "Something went wrong. Try again." in pg.inner_text("#ask-thread")
    assert pg.input_value("#ask-q") == Q


def test_failed_state_renders_the_plain_failure_message(phone):
    pg, _ = phone
    _go(pg, lambda r: r.abort(),
        lambda r: r.fulfill(status=200, content_type="application/json",
                            body='{"status":"failed","failed":true,"detail":"Couldn\'t answer that just now. Try again, or pick another depth."}'))
    pg.wait_for_selector("#ask-thread .ask-answer span[style*='alert']", timeout=8000)
    assert "Couldn't answer that just now" in pg.inner_text("#ask-thread")
    assert pg.input_value("#ask-q") == Q


def test_still_working_is_shown_while_running(phone):
    pg, _ = phone
    _go(pg, lambda r: r.abort(),
        lambda r: r.fulfill(status=200, content_type="application/json", body='{"status":"running"}'))
    pg.wait_for_selector("#ask-thread >> text=Still working", timeout=5000)


def test_the_submit_sends_a_request_id(phone):
    pg, site = phone
    pg.goto(ORIGIN + "/tools/fpa-buddy")
    pg.fill("#ask-q", Q)
    pg.click("#ask-btn")
    pg.wait_for_selector("#ask-thread .ask-answer >> text=Fresh answer", timeout=8000)
    assert re.match(r"^[A-Za-z0-9_-]{8,64}$", site.posts[0]["request_id"])
