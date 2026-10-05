"""A failed FP&A Buddy turn is a structured failure, never a public question.

The answer call raising used to come back as answer text ("(Answer call
failed: <raw exception>)") that run_ask recorded as an ordinary question. These
tests drive the real answer path with a failing model client and check what
each surface does with it."""
import importlib
import os
import tempfile

import pytest

from linklib import agent
from linklib.agent import Answer
from linklib.db import Library

Q = "What does a typical annual planning calendar look like for a PE backed company?"
RAW = "upstream 500 secret-detail-xyz"


class _Boom:
    class messages:
        @staticmethod
        def create(**kw):
            raise Exception(RAW)


@pytest.fixture
def site(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setenv("EXA_API_KEY", "fake-key")
    lib = Library(db)
    lib.seed_voice_prompts()
    lib.create_user("boss", "supersecret", role="admin")
    a = lib.create_user("author", "supersecret", role="user")
    b = lib.create_user("reader", "supersecret", role="user")
    lib.close()
    monkeypatch.setattr(agent, "_get_client", lambda: _Boom())
    # One Exa call already spent when the answer call fails.
    monkeypatch.setattr(agent, "retrieve_exa",
                        lambda q, opml, max_results=4, restrict=True: ([], 3, 0.007))
    import webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient

    def client(user):
        c = TestClient(appmod.app)
        assert c.post("/login", data={"username": user, "password": "supersecret"},
                      follow_redirects=False).status_code in (302, 303)
        return c
    yield client, db, a, b
    for ext in ("", "-shm", "-wal"):
        if os.path.exists(db + ext):
            os.remove(db + ext)


def _fail_ask(c, question=Q, **extra):
    return c.post("/ask", json={"question": question, "sources": ["library", "open_web"], **extra})


def _failed_rows(db):
    lib = Library(db)
    try:
        return [dict(r) for r in lib.conn.execute("SELECT * FROM ask_questions WHERE failed=1")]
    finally:
        lib.close()


def test_failed_turn_returns_plain_message_and_no_raw_error(site):
    client, db, a, b = site
    r = _fail_ask(client("author"))
    assert r.status_code == 502
    d = r.json()
    from webapp.ask_orchestrator import FAILED_TURN_MESSAGE
    assert d["detail"] == FAILED_TURN_MESSAGE
    assert d["failed"] is True
    assert "answer" not in d and "turn_id" not in d and "conversation_id" not in d
    body = r.text
    for leak in (RAW, "upstream", "Answer call failed", agent.DEFAULT_MODEL, "Traceback"):
        assert leak not in body


def test_spend_is_recorded_on_a_failed_row_and_counts_toward_the_cap(site):
    client, db, a, b = site
    d = _fail_ask(client("author")).json()
    rows = _failed_rows(db)
    assert len(rows) == 1
    row = rows[0]
    assert row["answer"] == "" and RAW in row["error"]
    assert row["cost_usd"] == pytest.approx(0.007) and row["exa_cost_usd"] == pytest.approx(0.007)
    assert row["user_id"] == a
    lib = Library(db)
    try:
        assert lib.ask_cost_this_month(a) == pytest.approx(0.007)
        assert lib.ask_cost_total() == pytest.approx(0.007)
    finally:
        lib.close()
    assert d["usage"]["spent"] == pytest.approx(0.01, abs=0.005)


def test_failed_row_is_in_no_user_visible_list(site):
    client, db, a, b = site
    _fail_ask(client("author"))
    lib = Library(db)
    fid = lib.conn.execute("SELECT id FROM ask_questions WHERE failed=1").fetchone()[0]
    # A stray rating on the failed row must not surface it under "helpful only".
    lib.record_ask_feedback(fid, b, "helpful")
    try:
        for viewer in (a, b):
            assert lib.list_public_ask_questions(viewer_id=viewer) == []
            assert lib.list_public_ask_questions(viewer_id=viewer, helpful_only=True) == []
            assert lib.similar_ask_candidates(viewer, False) == []
        assert lib.list_ask_questions(user_id=a) == []
        assert lib.count_ask_questions(user_id=a) == 0
        assert lib.list_recent_conversations(a) == []
        assert lib.list_conversation_turns(str(fid)) == []
    finally:
        lib.close()
    for who in ("author", "reader"):
        c = client(who)
        assert Q[:30] not in c.get("/tools/fpa-buddy").text
        assert Q[:30] not in c.get("/ask/history").text
        assert c.get("/ask/conversations").json()["conversations"] == []
        sug = c.post("/ask/similar", json={"question": Q}).json()["suggestions"]
        assert sug == []


def test_admin_sees_the_failed_row_labelled_with_the_raw_error(site):
    client, db, a, b = site
    _fail_ask(client("author"))
    admin = client("boss")
    html = admin.get("/tools/fpa-buddy").text
    assert "Failed" in html and RAW in html
    report = admin.get("/admin/fpa-buddy/report").text
    assert "Failed" in report and RAW in report
    # Suggestions are for reuse: never a failed row, even for an admin.
    assert admin.post("/ask/similar", json={"question": Q}).json()["suggestions"] == []
    lib = Library(db)
    try:
        rows = lib.list_public_ask_questions(see_private=True)
        assert len(rows) == 1 and rows[0]["failed"] == 1
    finally:
        lib.close()


def test_chip_for_failed_is_admin_only():
    import webapp.app as appmod
    assert appmod._ask_status_chips_html(failed=True, viewer_is_admin=False) == ""
    assert "Failed" in appmod._ask_status_chips_html(failed=True, viewer_is_admin=True)


def test_followup_after_a_failure_still_works_and_history_has_no_failed_turn(monkeypatch, site):
    client, db, a, b = site
    seen = []
    script = iter(["ok", "fail", "ok"])

    def fake(lib, question, model="", effort="standard", use_library=True, use_feed=True,
             use_web=False, opml_path=None, history=None):
        seen.append(list(history or []))
        if next(script) == "fail":
            return Answer(text="", failed=True, error=RAW, model="m", cost_usd=0.02)
        return Answer(text=f"answer to {question}", model="m", cost_usd=0.01)
    monkeypatch.setattr("linklib.agent.answer_question", fake)
    c = client("author")
    d1 = c.post("/ask", json={"question": "first", "sources": ["library"]}).json()
    cid = d1["conversation_id"]
    r2 = c.post("/ask", json={"question": "second", "sources": ["library"], "conversation_id": cid})
    assert r2.status_code == 502
    d3 = c.post("/ask", json={"question": "second", "sources": ["library"], "conversation_id": cid}).json()
    assert d3["conversation_id"] == cid and d3["followups_left"] == d1["followups_left"] - 1
    # The retry's history carries the one good turn, never the failed one.
    assert [h["content"] for h in seen[2]] == ["first", "answer to first"]
    lib = Library(db)
    try:
        turns = lib.list_conversation_turns(cid)
        assert [t["question"] for t in turns] == ["first", "second"]
        assert lib.ask_cost_this_month(a) == pytest.approx(0.04)   # the failed 0.02 still counts
    finally:
        lib.close()


def test_first_turn_failure_leaves_no_conversation_to_resume(site):
    client, db, a, b = site
    c = client("author")
    _fail_ask(c)
    assert c.get("/ask/conversations").json()["conversations"] == []


def test_run_ask_raises_a_structured_failure(site):
    client, db, a, b = site
    from webapp.ask_orchestrator import AskTurnFailed, run_ask
    lib = Library(db)
    try:
        with pytest.raises(AskTurnFailed) as ei:
            run_ask(lib, a, Q, use_library=False, use_feed=False, use_web=True,
                    opml_path="preferred_sites.opml")
        assert RAW not in ei.value.message
        assert ei.value.usage["spent"] == pytest.approx(0.01, abs=0.005)
    finally:
        lib.close()


def test_missing_key_and_sdk_states_are_failures_not_answer_text(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    lib = Library(str(tmp_path / "t.db"))
    lib.seed_voice_prompts()
    try:
        ans = agent.answer_question(lib, "q", use_library=False, use_feed=False)
    finally:
        lib.close()
    assert ans.failed and ans.text == "" and "ANTHROPIC_API_KEY" in ans.error


def test_raw_exception_is_logged_with_model_and_effort(site, caplog):
    client, db, a, b = site
    with caplog.at_level("ERROR", logger="linklib.agent"):
        _fail_ask(client("author"), effort="deep")
    rec = [r for r in caplog.records if "answer call failed" in r.getMessage()]
    assert rec and "effort=deep" in rec[0].getMessage() and "model=" in rec[0].getMessage()
    assert RAW in (rec[0].exc_text or "")


def test_ask_cli_exits_nonzero_on_a_failed_answer(monkeypatch, tmp_path, capsys):
    import sys
    from scripts import ask as ask_cli
    db = str(tmp_path / "t.db")
    Library(db).close()
    monkeypatch.setattr(ask_cli, "answer_question",
                        lambda lib, q, effort="standard": Answer(text="", failed=True, error=RAW))
    monkeypatch.setattr(sys, "argv", ["ask", "q", "--db", db])
    assert ask_cli.main() == 1
    out = capsys.readouterr()
    assert RAW in out.err and "stub" not in out.out and RAW not in out.out


def test_columns_exist_after_open_and_message_passes_voice_lint(tmp_path):
    lib = Library(str(tmp_path / "t.db"))
    cols = {r[1] for r in lib.conn.execute("PRAGMA table_info(ask_questions)")}
    lib.close()
    assert {"failed", "error"} <= cols
    from linklib.voice_review import mechanical_findings, typography_findings_plain
    from webapp.ask_orchestrator import FAILED_TURN_MESSAGE
    assert not mechanical_findings(FAILED_TURN_MESSAGE)
    assert not typography_findings_plain(FAILED_TURN_MESSAGE)
    assert "member" not in FAILED_TURN_MESSAGE.lower()
