"""FP&A Buddy server-side conversation persistence (#92, #96).

Covers the three layers: the storage contract (list_conversation_turns /
list_recent_conversations — ordering, scoping, the legacy '' guard), the
changed POST /ask contract (history rebuilt from ask_questions rows, the
follow-up cap counted from DB rows, ownership 404/403, client-supplied
history ignored), and the two resume endpoints (/ask/conversations list +
per-conversation transcript with citations and feedback state).
"""
import pathlib
import sys
import tempfile, os
import json

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Library
from linklib.agent import MAX_FOLLOWUPS


# ---------------------------------------------------------------------------
# Storage layer
# ---------------------------------------------------------------------------

@pytest.fixture
def lib(tmp_path):
    lib = Library(str(tmp_path / "t.db"))
    yield lib
    lib.close()


def _record_conversation(lib, user_id, n_turns, prefix="q"):
    """Record a conversation the way POST /ask does: first turn with "" (the
    row self-assigns str(id)), follow-ups with the assigned id."""
    cid = ""
    for i in range(n_turns):
        row_id = lib.record_ask_question(
            user_id, f"{prefix}{i}", f"answer {prefix}{i}", "m", "standard",
            True, False, True, conversation_id=cid, turn_index=i)
        if not cid:
            cid = str(row_id)
    return cid


def test_list_conversation_turns_ordered(lib):
    cid = _record_conversation(lib, 1, 3)
    turns = lib.list_conversation_turns(cid)
    assert [t["question"] for t in turns] == ["q0", "q1", "q2"]
    assert [t["turn_index"] for t in turns] == [0, 1, 2]


def test_list_conversation_turns_empty_id_never_matches_legacy_rows(lib):
    # Legacy rows predating conversation_id store '' — an empty lookup must
    # return nothing, not every legacy row in the table.
    lib.conn.execute(
        "INSERT INTO ask_questions (conversation_id, turn_index, user_id, question,"
        " answer, model, effort, use_library, use_feed, use_web, created_at)"
        " VALUES ('', 0, 1, 'legacy q', 'legacy a', 'm', 'standard', 1, 0, 1, '2024-01-01')")
    lib.conn.commit()
    assert lib.list_conversation_turns("") == []


def test_list_conversation_turns_feedback_join(lib):
    cid = _record_conversation(lib, 1, 2)
    turns = lib.list_conversation_turns(cid)
    lib.record_ask_feedback(turns[0]["id"], 1, "inaccurate", comment="stale numbers")
    lib.record_ask_feedback(turns[0]["id"], 2, "helpful")   # someone else's rating

    mine = lib.list_conversation_turns(cid, feedback_user_id=1)
    assert (mine[0]["fb_rating"], mine[0]["fb_comment"]) == ("inaccurate", "stale numbers")
    assert mine[1]["fb_rating"] is None
    # The join is per-user — user 2 sees their own rating, not user 1's.
    theirs = lib.list_conversation_turns(cid, feedback_user_id=2)
    assert theirs[0]["fb_rating"] == "helpful"


def test_list_recent_conversations_grouping_and_scoping(lib):
    cid_a = _record_conversation(lib, 1, 3, prefix="alpha")
    cid_b = _record_conversation(lib, 1, 1, prefix="beta")
    _record_conversation(lib, 2, 1, prefix="other")   # another user's
    # A legacy solo row (conversation_id='') is not resumable — excluded.
    lib.conn.execute(
        "INSERT INTO ask_questions (conversation_id, turn_index, user_id, question,"
        " answer, model, effort, use_library, use_feed, use_web, created_at)"
        " VALUES ('', 0, 1, 'legacy q', 'legacy a', 'm', 'standard', 1, 0, 1, '2024-01-01')")
    lib.conn.commit()

    convos = lib.list_recent_conversations(1)
    assert {c["conversation_id"] for c in convos} == {cid_a, cid_b}
    by_id = {c["conversation_id"]: c for c in convos}
    assert by_id[cid_a]["turns"] == 3
    assert by_id[cid_a]["first_question"] == "alpha0"
    assert by_id[cid_b]["turns"] == 1
    assert lib.list_recent_conversations(1, limit=1) == [convos[0]]


# ---------------------------------------------------------------------------
# POST /ask + the resume endpoints
# ---------------------------------------------------------------------------

@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SAVE_TOKEN", "tok-secret")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    # POST /ask uses a stub answer (a missing key is a failed turn now) so it still
    # records the turn, never a real API call from the test suite.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from tests.ask_stub import ok_answer_question
    monkeypatch.setattr("linklib.agent.answer_question", ok_answer_question)
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    lib = Library(db)
    uid = lib.create_user("member1", "supersecret", role="user", name="Member One")
    other = lib.create_user("member2", "supersecret", role="user")
    lib.close()
    yield appmod, uid, other
    if os.path.exists(db):
        os.remove(db)


def _client(appmod):
    from fastapi.testclient import TestClient
    return TestClient(appmod.app, raise_server_exceptions=True)


def _login(appmod, username, password):
    c = _client(appmod)
    c.post("/login", data={"username": username, "password": password}, follow_redirects=False)
    return c


def _seed_conversation(user_id, n_turns, citations=None, prefix="q"):
    lib = Library(os.environ["LINKLIB_DB"])
    try:
        cid = ""
        turn_ids = []
        for i in range(n_turns):
            row_id = lib.record_ask_question(
                user_id, f"{prefix}{i}", f"answer {prefix}{i} [1]", "m", "standard",
                True, False, True, conversation_id=cid, turn_index=i,
                citations=citations)
            turn_ids.append(row_id)
            if not cid:
                cid = str(row_id)
        return cid, turn_ids
    finally:
        lib.close()


def test_followup_history_rebuilt_from_db(env, monkeypatch):
    """The server passes answer_question a history rebuilt from the
    conversation's recorded rows — the client sends only conversation_id."""
    appmod, uid, _ = env
    cid, _ = _seed_conversation(uid, 2)

    captured = {}
    import linklib.agent as agent

    def fake_answer(lib, question, **kwargs):
        captured["history"] = kwargs.get("history")
        captured["question"] = question
        return agent.Answer(text="stub answer", model="m")

    monkeypatch.setattr(agent, "answer_question", fake_answer)
    c = _login(appmod, "member1", "supersecret")
    d = c.post("/ask", json={"question": "and at Series A?", "conversation_id": cid}).json()

    assert captured["question"] == "and at Series A?"
    assert captured["history"] == [
        {"role": "user", "content": "q0"},
        {"role": "assistant", "content": "answer q0 [1]"},
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "answer q1 [1]"},
    ]
    assert d["conversation_id"] == cid
    # The new turn was recorded onto the same conversation with the next index.
    lib = Library(os.environ["LINKLIB_DB"])
    try:
        turns = lib.list_conversation_turns(cid)
        assert len(turns) == 3 and turns[-1]["turn_index"] == 2
    finally:
        lib.close()


def test_client_supplied_history_is_ignored(env, monkeypatch):
    """A fabricated history payload (the #96 vector) has no effect: without a
    conversation_id the turn is a fresh conversation, uncapped and answered
    with empty history."""
    appmod, uid, _ = env
    captured = {}
    import linklib.agent as agent

    def fake_answer(lib, question, **kwargs):
        captured["history"] = kwargs.get("history")
        return agent.Answer(text="stub answer", model="m")

    monkeypatch.setattr(agent, "answer_question", fake_answer)
    forged = [{"role": "user", "content": f"fake {i}"} for i in range(20)]
    c = _login(appmod, "member1", "supersecret")
    d = c.post("/ask", json={"question": "fresh question", "history": forged}).json()
    assert captured["history"] == []
    assert "capped" not in d
    assert d["followups_left"] == MAX_FOLLOWUPS


def test_unknown_and_foreign_conversation_rejected(env):
    appmod, uid, other = env
    cid, _ = _seed_conversation(uid, 1)
    c1 = _login(appmod, "member1", "supersecret")
    assert c1.post("/ask", json={"question": "q", "conversation_id": "999999"}).status_code == 404
    c2 = _login(appmod, "member2", "supersecret")
    assert c2.post("/ask", json={"question": "q", "conversation_id": cid}).status_code == 403


def test_followup_cap_counted_from_db_rows(env):
    """A conversation at the 7-turn limit is capped from recorded rows alone —
    no client history involved, and no API call is attempted."""
    appmod, uid, _ = env
    cid, _ = _seed_conversation(uid, 1 + MAX_FOLLOWUPS)
    c = _login(appmod, "member1", "supersecret")
    d = c.post("/ask", json={"question": "one more?", "conversation_id": cid}).json()
    assert d["capped"] is True
    lib = Library(os.environ["LINKLIB_DB"])
    try:   # nothing new was recorded past the cap
        assert len(lib.list_conversation_turns(cid)) == 1 + MAX_FOLLOWUPS
    finally:
        lib.close()


def test_token_only_access_is_one_shot(env):
    """Token auth still answers, but records nothing and owns no
    conversations: no conversation_id comes back, and sending one is
    rejected before it can read someone else's transcript."""
    appmod, uid, _ = env
    cid, _ = _seed_conversation(uid, 1)
    c = _client(appmod)
    headers = {"X-Save-Token": "tok-secret"}
    d = c.post("/ask", json={"question": "q"}, headers=headers).json()
    assert d["conversation_id"] is None and d["turn_id"] is None
    r = c.post("/ask", json={"question": "q", "conversation_id": cid}, headers=headers)
    assert r.status_code == 403


def test_conversations_list_requires_auth_and_scopes(env):
    appmod, uid, other = env
    cid, _ = _seed_conversation(uid, 2, prefix="mine")
    _seed_conversation(other, 1, prefix="theirs")
    assert _client(appmod).get("/ask/conversations").status_code == 401

    d = _login(appmod, "member1", "supersecret").get("/ask/conversations").json()
    assert [c["conversation_id"] for c in d["conversations"]] == [cid]
    entry = d["conversations"][0]
    assert entry["first_question"] == "mine0"
    assert entry["turns"] == 2
    assert entry["capped"] is False
    # Token-only access resumes nothing — empty list, not an error.
    tok = _client(appmod).get("/ask/conversations", headers={"X-Save-Token": "tok-secret"})
    assert tok.json() == {"conversations": []}


def test_conversations_list_capped_flag(env):
    appmod, uid, _ = env
    cid, _ = _seed_conversation(uid, 1 + MAX_FOLLOWUPS)
    d = _login(appmod, "member1", "supersecret").get("/ask/conversations").json()
    assert d["conversations"][0]["capped"] is True


CITATIONS = [
    {"n": 1, "title": "Saved piece", "url": "https://ex.com/a", "type": "library", "article_id": 42},
    {"n": 2, "title": "Web hit", "url": "https://ex.com/c", "type": "web"},
]


def test_transcript_endpoint(env):
    appmod, uid, other = env
    cid, turn_ids = _seed_conversation(uid, 2, citations=CITATIONS)
    c = _login(appmod, "member1", "supersecret")
    c.post("/ask/feedback", json={"question_id": turn_ids[0], "rating": "inaccurate",
                                  "comment": "stale benchmark"})

    assert _client(appmod).get(f"/ask/conversations/{cid}").status_code == 401
    assert c.get("/ask/conversations/999999").status_code == 404
    assert _login(appmod, "member2", "supersecret").get(
        f"/ask/conversations/{cid}").status_code == 403

    d = c.get(f"/ask/conversations/{cid}").json()
    assert d["conversation_id"] == cid
    assert d["capped"] is False
    assert d["followups_left"] == 1 + MAX_FOLLOWUPS - 2
    assert [t["question"] for t in d["turns"]] == ["q0", "q1"]
    assert d["turns"][0]["turn_id"] == turn_ids[0]
    # citations_json comes back parsed, ready to re-render [n] markers.
    assert d["turns"][0]["citations"] == CITATIONS
    # Feedback state rides along: rated turn carries it, unrated is null.
    assert d["turns"][0]["feedback"] == {"rating": "inaccurate", "comment": "stale benchmark"}
    assert d["turns"][1]["feedback"] is None


def test_transcript_capped_conversation(env):
    appmod, uid, _ = env
    cid, _ = _seed_conversation(uid, 1 + MAX_FOLLOWUPS)
    d = _login(appmod, "member1", "supersecret").get(f"/ask/conversations/{cid}").json()
    assert d["capped"] is True and d["followups_left"] == 0
    assert len(d["turns"]) == 1 + MAX_FOLLOWUPS


def test_follow_up_cap_message_says_start_a_new_conversation(env):
    """The cap text matches the label above the box ("Start a new conversation")."""
    appmod, uid, _ = env
    cid, _ = _seed_conversation(uid, 1 + MAX_FOLLOWUPS)
    c = _login(appmod, "member1", "supersecret")
    d = c.post("/ask", json={"question": "one more?", "conversation_id": cid}).json()
    assert d["capped"] is True
    assert d["answer"] == ("We've reached the limit for this conversation. "
                           "Start a new conversation to keep going.")
