"""Chat Matchmaker (Phase 1): replaces the old quiz-based Recommender at
/tools/communities/find with a free-type conversation. Public, no login
required (same as the quiz it replaced) — rate limiting keys off the
anonymous cfo_visitor session cookie for the common signed-out case, falling
back to a per-user dollar cap when the visitor happens to be signed in.

No ANTHROPIC_API_KEY in tests, so POST /tools/communities/find/chat takes the
deterministic no-key fallback path in linklib.matchmaker.answer_communities_question
(cost 0, a fixed placeholder message) — the same pattern test_ask_conversations.py
uses for POST /ask, never a real API call from the test suite.
"""
import pathlib
import sys
import tempfile, os

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Library


@pytest.fixture
def lib(tmp_path):
    lib = Library(str(tmp_path / "t.db"))
    yield lib
    lib.close()


# ---------------------------------------------------------------------------
# Storage layer
# ---------------------------------------------------------------------------

def test_record_and_list_matchmaker_conversation_turns(lib):
    row1 = lib.record_matchmaker_question("sess-1", "community", "q1", "a1", "m")
    cid = str(row1)
    lib.record_matchmaker_question("sess-1", "community", "q2", "a2", "m",
                                   conversation_id=cid, turn_index=1)
    turns = lib.list_matchmaker_conversation_turns(cid)
    assert [t["question"] for t in turns] == ["q1", "q2"]
    assert turns[0]["user_id"] is None
    assert turns[0]["session_id"] == "sess-1"


def test_list_matchmaker_conversation_turns_empty_id_returns_nothing(lib):
    lib.record_matchmaker_question("sess-1", "community", "q1", "a1", "m")
    assert lib.list_matchmaker_conversation_turns("") == []


def test_matchmaker_cap_default_and_override(lib):
    assert lib.get_default_matchmaker_cap() == lib._DEFAULT_MATCHMAKER_CAP_USD
    lib.set_default_matchmaker_cap(3.5)
    assert lib.get_default_matchmaker_cap() == 3.5

    uid = lib.create_user("m1", "supersecret", role="user")
    assert lib.get_effective_matchmaker_cap(uid) == 3.5   # inherits default
    lib.set_user_matchmaker_cap(uid, 1.0)
    assert lib.get_effective_matchmaker_cap(uid) == 1.0
    lib.set_user_matchmaker_cap(uid, None)
    assert lib.get_effective_matchmaker_cap(uid) == 3.5   # cleared, back to default


def test_matchmaker_cost_this_month_scopes_by_session_and_excludes_logged_in(lib):
    uid = lib.create_user("m1", "supersecret", role="user")
    lib.record_matchmaker_question("sess-a", "community", "q", "a", "m", cost_usd=0.10)
    lib.record_matchmaker_question("sess-b", "community", "q", "a", "m", cost_usd=0.20)
    lib.record_matchmaker_question("sess-a", "community", "q", "a", "m",
                                   user_id=uid, cost_usd=5.00)  # logged-in row, same cookie

    assert lib.matchmaker_cost_this_month_session("sess-a") == pytest.approx(0.10)
    assert lib.matchmaker_cost_this_month_session("sess-b") == pytest.approx(0.20)
    assert lib.matchmaker_cost_this_month(uid) == pytest.approx(5.00)

    # This feature's spend never touches FP&A Buddy's own cap tracking.
    assert lib.ask_cost_this_month(uid) == 0.0


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

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
    r = c.get("/tools/communities/find")
    assert r.status_code == 200
    assert "Community Matchmaker" in r.text
    assert "cfo_visitor" not in r.text  # cookie is httponly, never in the page body


def test_chat_requires_question(env):
    appmod, _ = env
    c = _client(appmod)
    r = c.post("/tools/communities/find/chat", json={"question": ""})
    assert r.status_code == 400


def test_chat_anonymous_turn_recorded_with_no_key_fallback(env):
    appmod, _ = env
    c = _client(appmod)
    r = c.post("/tools/communities/find/chat", json={"question": "Looking for a CFO peer group"})
    assert r.status_code == 200
    d = r.json()
    assert "conversation_id" in d
    assert not d.get("capped")

    lib = Library(os.environ["LINKLIB_DB"])
    turns = lib.list_matchmaker_conversation_turns(d["conversation_id"])
    lib.close()
    assert len(turns) == 1
    assert turns[0]["user_id"] is None
    assert turns[0]["session_id"]   # cfo_visitor cookie was assigned and recorded
    assert turns[0]["cost_usd"] == 0.0   # no-key fallback spends nothing


def test_chat_follow_up_continues_conversation(env):
    appmod, _ = env
    c = _client(appmod)
    r1 = c.post("/tools/communities/find/chat", json={"question": "q1"})
    cid = r1.json()["conversation_id"]
    r2 = c.post("/tools/communities/find/chat", json={"question": "q2", "conversation_id": cid})
    assert r2.status_code == 200
    assert r2.json()["conversation_id"] == cid

    lib = Library(os.environ["LINKLIB_DB"])
    turns = lib.list_matchmaker_conversation_turns(cid)
    lib.close()
    assert [t["question"] for t in turns] == ["q1", "q2"]


def test_chat_unknown_conversation_id_is_404(env):
    appmod, _ = env
    c = _client(appmod)
    r = c.post("/tools/communities/find/chat", json={"question": "q", "conversation_id": "999999"})
    assert r.status_code == 404


def test_chat_conversation_owned_by_different_session_is_403(env):
    appmod, _ = env
    c1 = _client(appmod)
    r1 = c1.post("/tools/communities/find/chat", json={"question": "q1"})
    cid = r1.json()["conversation_id"]

    c2 = _client(appmod)   # a fresh client = a fresh anonymous cfo_visitor cookie
    r2 = c2.post("/tools/communities/find/chat", json={"question": "q2", "conversation_id": cid})
    assert r2.status_code == 403


def test_chat_follow_up_cap_enforced(env):
    from linklib.matchmaker import MAX_FOLLOWUPS
    appmod, _ = env
    c = _client(appmod)
    r = c.post("/tools/communities/find/chat", json={"question": "q0"})
    cid = r.json()["conversation_id"]
    for i in range(1, MAX_FOLLOWUPS + 1):
        r = c.post("/tools/communities/find/chat", json={"question": f"q{i}", "conversation_id": cid})
        assert r.status_code == 200
    r = c.post("/tools/communities/find/chat", json={"question": "one too many", "conversation_id": cid})
    assert r.json()["capped"] is True

    lib = Library(os.environ["LINKLIB_DB"])
    turns = lib.list_matchmaker_conversation_turns(cid)
    lib.close()
    assert len(turns) == MAX_FOLLOWUPS + 1   # the capped attempt was never recorded


def test_chat_anonymous_session_cap_enforced(env):
    appmod, uid = env
    lib = Library(os.environ["LINKLIB_DB"])
    lib.set_default_matchmaker_cap(0.05)
    lib.close()

    c = _client(appmod)
    r1 = c.post("/tools/communities/find/chat", json={"question": "q1"})
    assert r1.status_code == 200
    assert not r1.json().get("capped")   # no-key fallback costs $0, so still under cap

    # Manually record spend against this session (simulating a real API call
    # that cost money) to exercise the cap check on the next turn.
    lib = Library(os.environ["LINKLIB_DB"])
    session_cookie = c.cookies.get("cfo_visitor")
    lib.record_matchmaker_question(session_cookie, "community", "prior", "prior answer", "m", cost_usd=1.00)
    lib.close()

    r2 = c.post("/tools/communities/find/chat", json={"question": "q2"})
    assert r2.json()["capped"] is True


def test_chat_logged_in_user_cap_tracked_separately_from_session(env):
    appmod, uid = env
    lib = Library(os.environ["LINKLIB_DB"])
    lib.set_user_matchmaker_cap(uid, 0.05)
    lib.close()

    c = _client(appmod)
    c.post("/login", data={"username": "member1", "password": "supersecret"}, follow_redirects=False)

    r1 = c.post("/tools/communities/find/chat", json={"question": "q1"})
    assert r1.status_code == 200

    lib = Library(os.environ["LINKLIB_DB"])
    cid = r1.json()["conversation_id"]
    turns = lib.list_matchmaker_conversation_turns(cid)
    lib.close()
    assert turns[0]["user_id"] == uid   # recorded against the logged-in user, not just the cookie


def test_admin_users_page_shows_matchmaker_cap_controls(env):
    appmod, uid = env
    c = _client(appmod)
    r = c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    r = c.get("/admin/users")
    assert r.status_code == 200
    assert "Matchmaker" in r.text
    assert f"/admin/users/{uid}/matchmaker-cap" in r.text
    assert "/admin/users/matchmaker-cap-default" in r.text


def test_admin_matchmaker_cap_default_updates_setting(env):
    appmod, _ = env
    c = _client(appmod)
    c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    r = c.post("/admin/users/matchmaker-cap-default", data={"cap": "4.25"}, follow_redirects=False)
    assert r.status_code == 303

    lib = Library(os.environ["LINKLIB_DB"])
    cap = lib.get_default_matchmaker_cap()
    lib.close()
    assert cap == 4.25


def test_admin_matchmaker_cap_per_user_override_set_and_clear(env):
    appmod, uid = env
    c = _client(appmod)
    c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)

    r = c.post(f"/admin/users/{uid}/matchmaker-cap", data={"cap": "1.5"}, follow_redirects=False)
    assert r.status_code == 303
    lib = Library(os.environ["LINKLIB_DB"])
    assert lib.get_effective_matchmaker_cap(uid) == 1.5
    lib.close()

    r = c.post(f"/admin/users/{uid}/matchmaker-cap", data={"cap": ""}, follow_redirects=False)
    assert r.status_code == 303
    lib = Library(os.environ["LINKLIB_DB"])
    default = lib.get_default_matchmaker_cap()
    assert lib.get_effective_matchmaker_cap(uid) == default
    lib.close()


def test_old_quiz_routes_are_gone(env):
    appmod, _ = env
    c = _client(appmod)
    assert c.get("/tools/communities/find/results").status_code == 404
