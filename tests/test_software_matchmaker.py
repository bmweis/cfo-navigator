"""Chat Matchmaker (Phase 2): the same conversational pattern as the
Communities matchmaker (#210), applied to Software at /tools/software/find.
No existing quiz to replace here — this is a new build, sharing the
matchmaker_questions table (kind='software') and the same anonymous-session-
then-per-user rate-limit design.

No ANTHROPIC_API_KEY in tests, so POST /tools/software/find/chat takes the
deterministic no-key fallback path in linklib.matchmaker.answer_software_question
(cost 0, a fixed placeholder message), same pattern as
test_communities_matchmaker.py.
"""
import pathlib
import sys
import tempfile, os

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Library


@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    lib = Library(db)
    uid = lib.create_user("member1", "supersecret", role="user")
    lib.close()
    yield appmod, uid
    if os.path.exists(db):
        os.remove(db)


def _client(appmod):
    from fastapi.testclient import TestClient
    return TestClient(appmod.app, raise_server_exceptions=True)


def test_find_page_renders_without_login(env):
    appmod, _ = env
    c = _client(appmod)
    r = c.get("/tools/software/find")
    assert r.status_code == 200
    assert "Software matchmaker" in r.text


def test_software_directory_links_to_matchmaker_when_signed_in(env):
    # Anonymous visitors see a sign-in prompt instead (tests/test_matchmaker_auth_gating.py) —
    # the matchmaker link itself only shows once signed in.
    appmod, _ = env
    c = _client(appmod)
    c.post("/login", data={"username": "member1", "password": "supersecret"}, follow_redirects=False)
    r = c.get("/tools/software")
    assert r.status_code == 200
    assert '<a href="/tools/software/find" style="font-weight:500;">Software matchmaker' in r.text


def test_chat_requires_question(env):
    appmod, _ = env
    c = _client(appmod)
    r = c.post("/tools/software/find/chat", json={"question": ""})
    assert r.status_code == 400


def test_chat_anonymous_turn_recorded_as_software_kind(env):
    appmod, _ = env
    c = _client(appmod)
    r = c.post("/tools/software/find/chat", json={"question": "Looking for close automation software"})
    assert r.status_code == 200
    d = r.json()
    assert not d.get("capped")

    lib = Library(os.environ["LINKLIB_DB"])
    turns = lib.list_matchmaker_conversation_turns(d["conversation_id"])
    lib.close()
    assert len(turns) == 1
    assert turns[0]["kind"] == "software"
    assert turns[0]["user_id"] is None
    assert turns[0]["cost_usd"] == 0.0


def test_chat_follow_up_continues_conversation(env):
    appmod, _ = env
    c = _client(appmod)
    r1 = c.post("/tools/software/find/chat", json={"question": "q1"})
    cid = r1.json()["conversation_id"]
    r2 = c.post("/tools/software/find/chat", json={"question": "q2", "conversation_id": cid})
    assert r2.status_code == 200
    assert r2.json()["conversation_id"] == cid

    lib = Library(os.environ["LINKLIB_DB"])
    turns = lib.list_matchmaker_conversation_turns(cid)
    lib.close()
    assert [t["question"] for t in turns] == ["q1", "q2"]


def test_chat_unknown_conversation_id_is_404(env):
    appmod, _ = env
    c = _client(appmod)
    r = c.post("/tools/software/find/chat", json={"question": "q", "conversation_id": "999999"})
    assert r.status_code == 404


def test_chat_conversation_owned_by_different_session_is_403(env):
    appmod, _ = env
    c1 = _client(appmod)
    r1 = c1.post("/tools/software/find/chat", json={"question": "q1"})
    cid = r1.json()["conversation_id"]

    c2 = _client(appmod)   # a fresh client = a fresh anonymous cfo_visitor cookie
    r2 = c2.post("/tools/software/find/chat", json={"question": "q2", "conversation_id": cid})
    assert r2.status_code == 403


def test_chat_follow_up_cap_enforced(env):
    from linklib.matchmaker import MAX_FOLLOWUPS
    appmod, _ = env
    c = _client(appmod)
    r = c.post("/tools/software/find/chat", json={"question": "q0"})
    cid = r.json()["conversation_id"]
    for i in range(1, MAX_FOLLOWUPS + 1):
        r = c.post("/tools/software/find/chat", json={"question": f"q{i}", "conversation_id": cid})
        assert r.status_code == 200
    r = c.post("/tools/software/find/chat", json={"question": "one too many", "conversation_id": cid})
    assert r.json()["capped"] is True


def test_software_and_communities_matchmaker_share_one_budget(env):
    """Per the design (matchmaker_questions is one shared table/cap across
    kinds, not a separate budget per matchmaker), spend in one matchmaker
    counts against the same session's cap in the other."""
    appmod, _ = env
    lib = Library(os.environ["LINKLIB_DB"])
    lib.set_default_matchmaker_cap(0.05)
    lib.close()

    c = _client(appmod)
    c.post("/tools/software/find/chat", json={"question": "q1"})   # $0 via no-key fallback

    lib = Library(os.environ["LINKLIB_DB"])
    session_cookie = c.cookies.get("cfo_visitor")
    lib.record_matchmaker_question(session_cookie, "community", "prior", "prior answer", "m", cost_usd=1.00)
    lib.close()

    r = c.post("/tools/software/find/chat", json={"question": "q2"})
    assert r.json()["capped"] is True


def test_build_software_context_includes_tool_fields(tmp_path):
    from linklib.matchmaker import _build_software_context
    lib = Library(str(tmp_path / "t.db"))
    tool_id = lib.add_tool("Numeric", "Close automation for accounting teams.",
                           "https://numeric.io", ["Accounting"], approved=1,
                           summary="Close checklists, reconciliations, flux analysis.")
    lib.close()

    lib = Library(str(tmp_path / "t.db"))
    ctx = _build_software_context(lib)
    lib.close()
    assert "Numeric" in ctx
    assert "numeric" in ctx   # slug
    assert "Close checklists, reconciliations, flux analysis." in ctx
    assert "Accounting" in ctx
