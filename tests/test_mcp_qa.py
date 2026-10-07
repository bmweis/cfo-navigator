"""Tests for MCP Phase 5: the two FP&A Buddy / Matchmaker proxy tools
(ask_fpa_buddy, ask_matchmaker).

Uses the same real-running-server pattern tests/test_mcp_toolbox.py and
tests/test_mcp_library.py establish (a TestClient's ASGI shortcut doesn't
run a real event loop the way FastMCP's session manager needs) — a real
uvicorn server, a real streamable-HTTP client, real tokens minted against a
temp DB.

No ANTHROPIC_API_KEY in these tests — both `answer_question()` and
`linklib.matchmaker._answer()` take their deterministic no-key fallback
path (a fixed placeholder answer, $0 cost), so cap/history/audit-trail
behavior is exercised exactly the same way the existing HTTP-route test
suites (tests/test_ask_conversations.py, tests/test_software_matchmaker.py,
tests/test_communities_matchmaker.py) already do — no real API call from
this test suite either.

The point of this file isn't to re-prove `answer_question()`/`_answer()`
work (that's the existing agent/matchmaker test suites' job) — it's to
prove the MCP proxy layer wires the caller's real identity through
correctly: cap enforcement, conversation continuity, and the
ask_questions/matchmaker_questions audit trail all fire under the
MCP-resolved user_id, exactly as they would for the same user on the web.
"""
import asyncio
import json
import os
import pathlib
import socket
import sys
import tempfile
import threading
import time

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Library
from linklib import matchmaker as _mm_module

_REAL_MATCHMAKER_ANSWER = _mm_module._answer  # captured before any test patches it


def _mint(lib: Library, username: str, role: str = "user", active: int = 1):
    lib.conn.execute(
        "INSERT INTO users (username, role, active, created_at) VALUES (?, ?, ?, '')",
        (username, role, active),
    )
    lib.conn.commit()
    user = lib.get_user(username)
    _, token = lib.create_api_token(user["id"], label="test")
    return user["id"], token


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live_server(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PUBLIC_BASE", "https://bmweis.com")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from tests.ask_stub import ok_answer_question
    monkeypatch.setattr("linklib.agent.answer_question", ok_answer_question)
    from tests.matchmaker_stub import ok_answer as _mm_ok
    monkeypatch.setattr("linklib.matchmaker._answer", _mm_ok)
    import importlib
    import webapp.app as appmod
    importlib.reload(appmod)

    lib = Library(db)
    member_id, member_token = _mint(lib, "member_one", "user")
    other_id, other_token = _mint(lib, "member_two", "user")
    admin_id, admin_token = _mint(lib, "admin_user", "admin")
    lib.close()

    import uvicorn
    port = _free_port()
    config = uvicorn.Config(appmod.app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    else:
        pytest.fail("server did not start in time")

    class Bundle:
        base_url = f"http://127.0.0.1:{port}"
        member = member_token
        member_id_ = member_id
        other = other_token
        other_id_ = other_id
        admin = admin_token
        admin_id_ = admin_id
        db_path = db

    try:
        yield Bundle
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        if os.path.exists(db):
            os.remove(db)


def _call_tool(base_url: str, token: str, tool_name: str, args: dict | None = None):
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async def go():
        headers = {"Authorization": f"Bearer {token}"}
        async with streamablehttp_client(f"{base_url}/mcp", headers=headers) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool(tool_name, args or {})

    return asyncio.run(go())


def _dict_result(result):
    assert not result.isError, result.content[0].text if result.content else result
    return json.loads(result.content[0].text)


def _error_text(result):
    assert result.isError
    return result.content[0].text if result.content else ""


# ---------------------------------------------------------------------------
# ask_fpa_buddy — auth
# ---------------------------------------------------------------------------

def test_ask_fpa_buddy_works_for_a_plain_member_token(live_server):
    d = _dict_result(_call_tool(live_server.base_url, live_server.member, "ask_fpa_buddy",
                                 {"question": "How should I think about runway?"}))
    assert "answer" in d
    assert d.get("capped") is not True


def test_ask_fpa_buddy_works_for_an_admin_token(live_server):
    d = _dict_result(_call_tool(live_server.base_url, live_server.admin, "ask_fpa_buddy",
                                 {"question": "How should I think about runway?"}))
    assert "answer" in d


def test_ask_fpa_buddy_rejects_unauthenticated(live_server):
    # A bad/unknown token is rejected at the transport level (webapp.app's
    # _mcp_auth_gate) with a bare HTTP 401, before any MCP tool result
    # exists — same convention test_mcp_library.py's own auth tests use.
    import httpx
    r = httpx.post(f"{live_server.base_url}/mcp",
                    headers={"authorization": "Bearer not-a-real-token"})
    assert r.status_code == 401


def test_ask_fpa_buddy_rejects_empty_question(live_server):
    result = _call_tool(live_server.base_url, live_server.member, "ask_fpa_buddy",
                         {"question": "   "})
    assert "question is required" in _error_text(result).lower()


def test_ask_fpa_buddy_rejects_invalid_effort(live_server):
    result = _call_tool(live_server.base_url, live_server.member, "ask_fpa_buddy",
                         {"question": "q", "effort": "extreme"})
    assert "invalid effort" in _error_text(result).lower()


def test_ask_fpa_buddy_rejects_invalid_sources(live_server):
    result = _call_tool(live_server.base_url, live_server.member, "ask_fpa_buddy",
                         {"question": "q", "sources": ["library", "bogus"]})
    assert "invalid sources" in _error_text(result).lower()


# ---------------------------------------------------------------------------
# ask_fpa_buddy — cap enforcement
# ---------------------------------------------------------------------------

def test_ask_fpa_buddy_capped_user_gets_capped_result_not_an_answer(live_server):
    lib = Library(live_server.db_path)
    lib.set_user_ask_cap(live_server.member_id_, 0.01)
    lib.record_ask_question(live_server.member_id_, "prior", "prior answer", "m", "standard",
                             True, False, True, cost_usd=5.00)
    lib.close()

    result = _call_tool(live_server.base_url, live_server.member, "ask_fpa_buddy",
                         {"question": "one more?"})
    d = _dict_result(result)   # capped is a normal (non-error) result, per Step 0
    assert d["capped"] is True
    assert "answer" in d


def test_ask_fpa_buddy_under_cap_gets_a_real_answer_and_recorded_cost(live_server):
    d = _dict_result(_call_tool(live_server.base_url, live_server.member, "ask_fpa_buddy",
                                 {"question": "What's a good NRR benchmark?"}))
    assert "capped" not in d   # a successful answer carries no capped key at all
    assert d["turn_id"] is not None

    lib = Library(live_server.db_path)
    turns = lib.list_conversation_turns(d["conversation_id"])
    lib.close()
    assert len(turns) == 1
    assert turns[0]["user_id"] == live_server.member_id_
    assert turns[0]["cost_usd"] == 0.0   # no-key fallback spends nothing


# ---------------------------------------------------------------------------
# ask_fpa_buddy — conversation continuity / audit trail
# ---------------------------------------------------------------------------

def test_ask_fpa_buddy_conversation_id_resumes_and_enforces_followup_cap(live_server):
    from linklib.agent import MAX_FOLLOWUPS

    d1 = _dict_result(_call_tool(live_server.base_url, live_server.member, "ask_fpa_buddy",
                                  {"question": "q0"}))
    cid = d1["conversation_id"]
    assert cid is not None

    for i in range(1, MAX_FOLLOWUPS + 1):
        d = _dict_result(_call_tool(live_server.base_url, live_server.member, "ask_fpa_buddy",
                                     {"question": f"q{i}", "conversation_id": cid}))
        assert d["conversation_id"] == cid
        assert d.get("capped") is not True

    d_capped = _dict_result(_call_tool(live_server.base_url, live_server.member, "ask_fpa_buddy",
                                        {"question": "one too many", "conversation_id": cid}))
    assert d_capped["capped"] is True

    lib = Library(live_server.db_path)
    turns = lib.list_conversation_turns(cid)
    lib.close()
    assert len(turns) == 1 + MAX_FOLLOWUPS   # the capped attempt was never recorded
    assert all(t["user_id"] == live_server.member_id_ for t in turns)


def test_ask_fpa_buddy_foreign_conversation_id_is_a_tool_error(live_server):
    d1 = _dict_result(_call_tool(live_server.base_url, live_server.member, "ask_fpa_buddy",
                                  {"question": "mine"}))
    cid = d1["conversation_id"]

    result = _call_tool(live_server.base_url, live_server.other, "ask_fpa_buddy",
                         {"question": "not mine", "conversation_id": cid})
    assert "different user" in _error_text(result).lower()


def test_ask_fpa_buddy_unknown_conversation_id_is_a_tool_error(live_server):
    result = _call_tool(live_server.base_url, live_server.member, "ask_fpa_buddy",
                         {"question": "q", "conversation_id": "999999"})
    assert "unknown conversation_id" in _error_text(result).lower()


# ---------------------------------------------------------------------------
# ask_matchmaker — auth + kind validation
# ---------------------------------------------------------------------------

def test_ask_matchmaker_works_for_a_plain_member_token_both_kinds(live_server):
    d_tools = _dict_result(_call_tool(live_server.base_url, live_server.member, "ask_matchmaker",
                                       {"kind": "tools", "question": "close automation software"}))
    assert "answer" in d_tools
    d_comm = _dict_result(_call_tool(live_server.base_url, live_server.member, "ask_matchmaker",
                                      {"kind": "communities", "question": "a CFO peer group"}))
    assert "answer" in d_comm
    assert "citations" not in d_tools and "citations" not in d_comm


def test_ask_matchmaker_rejects_invalid_kind(live_server):
    result = _call_tool(live_server.base_url, live_server.member, "ask_matchmaker",
                         {"kind": "software", "question": "q"})
    assert "invalid kind" in _error_text(result).lower()


def test_ask_matchmaker_rejects_empty_question(live_server):
    result = _call_tool(live_server.base_url, live_server.member, "ask_matchmaker",
                         {"kind": "tools", "question": ""})
    assert "question is required" in _error_text(result).lower()


def test_ask_matchmaker_rejects_unauthenticated(live_server):
    import httpx
    r = httpx.post(f"{live_server.base_url}/mcp",
                    headers={"authorization": "Bearer not-a-real-token"})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# ask_matchmaker — cap enforcement (shared budget across both kinds)
# ---------------------------------------------------------------------------

def test_ask_matchmaker_capped_user_gets_capped_result_not_an_answer(live_server):
    lib = Library(live_server.db_path)
    lib.set_user_matchmaker_cap(live_server.member_id_, 0.01)
    lib.record_matchmaker_question("some-other-session", "software", "prior", "prior answer", "m",
                                    user_id=live_server.member_id_, cost_usd=5.00)
    lib.close()

    d = _dict_result(_call_tool(live_server.base_url, live_server.member, "ask_matchmaker",
                                 {"kind": "communities", "question": "one more?"}))
    assert d["capped"] is True   # spend recorded under kind='software' still caps kind='communities'


def test_ask_matchmaker_under_cap_gets_a_real_answer_and_recorded_audit_row(live_server):
    d = _dict_result(_call_tool(live_server.base_url, live_server.member, "ask_matchmaker",
                                 {"kind": "tools", "question": "close automation"}))
    assert d.get("capped") is not True

    lib = Library(live_server.db_path)
    turns = lib.list_matchmaker_conversation_turns(d["conversation_id"])
    lib.close()
    assert len(turns) == 1
    assert turns[0]["kind"] == "software"
    assert turns[0]["user_id"] == live_server.member_id_
    assert turns[0]["cost_usd"] == 0.0


# ---------------------------------------------------------------------------
# ask_matchmaker — conversation continuity / audit trail
# ---------------------------------------------------------------------------

def test_ask_matchmaker_conversation_id_resumes_and_enforces_followup_cap(live_server):
    from linklib.matchmaker import MAX_FOLLOWUPS

    d1 = _dict_result(_call_tool(live_server.base_url, live_server.member, "ask_matchmaker",
                                  {"kind": "communities", "question": "q0"}))
    cid = d1["conversation_id"]

    for i in range(1, MAX_FOLLOWUPS + 1):
        d = _dict_result(_call_tool(live_server.base_url, live_server.member, "ask_matchmaker",
                                     {"kind": "communities", "question": f"q{i}",
                                      "conversation_id": cid}))
        assert d["conversation_id"] == cid
        assert d.get("capped") is not True

    d_capped = _dict_result(_call_tool(live_server.base_url, live_server.member, "ask_matchmaker",
                                        {"kind": "communities", "question": "one too many",
                                         "conversation_id": cid}))
    assert d_capped["capped"] is True

    lib = Library(live_server.db_path)
    turns = lib.list_matchmaker_conversation_turns(cid)
    lib.close()
    assert len(turns) == 1 + MAX_FOLLOWUPS


def test_ask_matchmaker_conversation_started_by_a_different_mcp_user_is_a_tool_error(live_server):
    d1 = _dict_result(_call_tool(live_server.base_url, live_server.member, "ask_matchmaker",
                                  {"kind": "tools", "question": "mine"}))
    cid = d1["conversation_id"]

    result = _call_tool(live_server.base_url, live_server.other, "ask_matchmaker",
                         {"kind": "tools", "question": "not mine", "conversation_id": cid})
    assert "different user or session" in _error_text(result).lower()


def test_ask_matchmaker_unknown_conversation_id_is_a_tool_error(live_server):
    result = _call_tool(live_server.base_url, live_server.member, "ask_matchmaker",
                         {"kind": "tools", "question": "q", "conversation_id": "999999"})
    assert "unknown conversation_id" in _error_text(result).lower()


# ---------------------------------------------------------------------------
# Open web (2026-10): the old "web" source value stays a trusted-sites search;
# only the explicit open_web parameter is unrestricted, and it defaults off.
# ---------------------------------------------------------------------------

def _last_scope(db_path):
    lib = Library(db_path)
    try:
        r = lib.conn.execute(
            "SELECT use_feed, use_web, web_scope FROM ask_questions ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return tuple(r)
    finally:
        lib.close()


def test_ask_fpa_buddy_default_sources_are_never_unrestricted(live_server):
    _dict_result(_call_tool(live_server.base_url, live_server.member, "ask_fpa_buddy",
                            {"question": "Burn multiple?"}))
    assert _last_scope(live_server.db_path) == (1, 0, "trusted")


def test_ask_fpa_buddy_old_web_source_value_is_trusted_not_open(live_server):
    _dict_result(_call_tool(live_server.base_url, live_server.member, "ask_fpa_buddy",
                            {"question": "Burn multiple?", "sources": ["library", "web"]}))
    assert _last_scope(live_server.db_path) == (1, 0, "trusted")


def test_ask_fpa_buddy_open_web_only_via_the_explicit_parameter(live_server):
    _dict_result(_call_tool(live_server.base_url, live_server.member, "ask_fpa_buddy",
                            {"question": "Burn multiple?", "open_web": True}))
    assert _last_scope(live_server.db_path) == (1, 1, "open")


def test_ask_fpa_buddy_feed_and_web_together_is_still_trusted_not_open(live_server):
    _dict_result(_call_tool(live_server.base_url, live_server.member, "ask_fpa_buddy",
                            {"question": "Burn multiple?", "sources": ["feed", "web"]}))
    assert _last_scope(live_server.db_path) == (1, 0, "trusted")


def test_ask_fpa_buddy_every_sources_combination_without_open_web_is_never_open(live_server):
    for src in (["library"], ["library", "feed", "web"], ["web"], ["feed"]):
        _dict_result(_call_tool(live_server.base_url, live_server.member, "ask_fpa_buddy",
                                {"question": "Burn multiple?", "sources": src}))
        use_feed, use_web, scope = _last_scope(live_server.db_path)
        assert use_web == 0 and scope != "open", src


def test_ask_fpa_buddy_failed_turn_is_a_tool_error_and_spend_is_recorded(live_server, monkeypatch):
    from linklib.agent import Answer

    def failing(lib, question, **kw):
        return Answer(text="", failed=True, error="upstream 500 secret-detail-xyz",
                      model="m", cost_usd=0.02)
    monkeypatch.setattr("linklib.agent.answer_question", failing)
    r = _call_tool(live_server.base_url, live_server.member, "ask_fpa_buddy",
                   {"question": "Burn multiple?"})
    msg = _error_text(r)
    assert "Couldn't answer that just now" in msg
    assert "secret-detail" not in msg and "upstream" not in msg
    lib = Library(live_server.db_path)
    try:
        assert lib.ask_cost_this_month(live_server.member_id_) == 0.02
        assert lib.list_recent_conversations(live_server.member_id_) == []
    finally:
        lib.close()


def test_ask_matchmaker_failed_turn_is_a_plain_tool_error_and_not_recorded(live_server, monkeypatch):
    """#703: a model-call failure is a tool error with a plain message, never
    answer text carrying the raw exception, and nothing is recorded."""
    raw = "upstream 529 overloaded"

    def boom():
        raise RuntimeError(raw)

    monkeypatch.setattr("linklib.matchmaker._answer", _REAL_MATCHMAKER_ANSWER)
    monkeypatch.setattr("linklib.matchmaker._get_client", boom)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    lib = Library(os.environ["LINKLIB_DB"])
    lib.seed_voice_prompts()
    lib.close()
    result = _call_tool(live_server.base_url, live_server.member, "ask_matchmaker",
                        {"kind": "tools", "question": "close automation software"})
    text = _error_text(result)
    # FastMCP prefixes a tool error with "Error executing tool <name>: ".
    assert text.endswith("Couldn't answer that just now. Try again in a moment.")
    assert raw not in text and "Answer call failed" not in text
    lib = Library(os.environ["LINKLIB_DB"])
    n = lib.conn.execute("SELECT COUNT(*) FROM matchmaker_questions").fetchone()[0]
    lib.close()
    assert n == 0
