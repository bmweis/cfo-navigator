"""Part A of the turn-20 follow-up: each turn's wall time is recorded, the admin
report shows it, a stored successful answer is reachable on the past-questions
list, and a page-side failure leaves a server log line. Invented data."""
import importlib
import logging
import os
import tempfile

import pytest

from linklib.db import Library
from tests.ask_stub import ok_answer_question


@pytest.fixture
def site(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("linklib.agent.answer_question", ok_answer_question)
    lib = Library(db)
    lib.create_user("boss", "supersecret", role="admin")
    lib.create_user("reader", "supersecret", role="user")
    lib.close()
    import webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient

    def client(user):
        c = TestClient(appmod.app)
        assert c.post("/login", data={"username": user, "password": "supersecret"},
                      follow_redirects=False).status_code in (302, 303)
        return c
    yield client, db
    for ext in ("", "-shm", "-wal"):
        if os.path.exists(db + ext):
            os.remove(db + ext)


Q = "What does a company at 25 million in ARR spend on each function?"


def test_turn_duration_is_recorded_and_returned(site):
    client, db = site
    r = client("reader").post("/ask", json={"question": Q, "sources": ["library"]})
    assert r.status_code == 200
    assert isinstance(r.json()["duration_ms"], int)
    lib = Library(db)
    row = lib.conn.execute("SELECT duration_ms FROM ask_questions").fetchone()
    lib.close()
    assert row["duration_ms"] == r.json()["duration_ms"]


def test_slow_turn_is_logged(site, monkeypatch, caplog):
    client, _ = site
    import webapp.ask_orchestrator as ao
    monkeypatch.setattr(ao, "SLOW_TURN_MS", 0)
    with caplog.at_level(logging.WARNING, logger="webapp.ask_orchestrator"):
        client("reader").post("/ask", json={"question": Q, "sources": ["library"]})
    assert any("slow Buddy turn" in m for m in caplog.messages)


def test_admin_report_shows_duration_and_reader_sees_the_answer_in_the_list(site):
    client, db = site
    client("reader").post("/ask", json={"question": Q, "sources": ["library"]})
    lib = Library(db)
    lib.conn.execute("UPDATE ask_questions SET duration_ms=73500, stop_reason='max_tokens'")
    lib.conn.commit()
    lib.close()
    report = client("boss").get("/admin/fpa-buddy/report").text
    assert "73.5s" in report
    # The stored successful turn (not failed, not private, not hidden) is on the list.
    page = client("reader").get("/tools/fpa-buddy").text
    assert "What does a company at 25 million" in page


def test_client_error_beacon_is_logged_and_needs_sign_in(site, caplog):
    client, _ = site
    anon = client("reader")
    with caplog.at_level(logging.WARNING, logger="webapp.ask"):
        resp = anon.post("/ask/client-error",
                         json={"stage": "fetch", "name": "TypeError", "message": "Load failed",
                               "elapsed_ms": 71000, "online": True, "visibility": "hidden", "effort": "standard"})
    assert resp.status_code == 204
    line = [m for m in caplog.messages if "failure shown to a user" in m]
    assert line and "stage=fetch" in line[0] and "TypeError" in line[0] and "71000" in line[0]
    from fastapi.testclient import TestClient
    import webapp.app as appmod
    assert TestClient(appmod.app).post("/ask/client-error", json={}).status_code in (401, 302, 303, 403)
