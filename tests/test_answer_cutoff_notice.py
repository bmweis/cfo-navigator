"""FP&A Buddy: Deep's answer ceiling is 4,000 tokens, and an answer the model
stopped at max_tokens carries a plain cut-off notice wherever answers render.

The notice is derived from the stored ask_questions.stop_reason at render time.
It is never appended to the stored answer text (follow-up history and citations
read that text)."""
import importlib
import os
import tempfile
import types

import pytest

from linklib import agent
from linklib.db import Library
from webapp.ask_orchestrator import ANSWER_CUTOFF_NOTICE, answer_cutoff_notice

ANSWER = "Here is the first half of a long answer that stops here"


def _resp(stop_reason):
    return types.SimpleNamespace(
        content=[types.SimpleNamespace(type="text", text=ANSWER, citations=None)],
        usage=types.SimpleNamespace(input_tokens=10, output_tokens=20,
                                    cache_creation_input_tokens=0, cache_read_input_tokens=0),
        stop_reason=stop_reason)


class _Client:
    def __init__(self, resp):
        self.messages = types.SimpleNamespace(create=lambda **kw: resp)


@pytest.fixture
def site(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    lib = Library(db)
    lib.seed_voice_prompts()
    uid = lib.create_user("reader", "supersecret", role="user")
    lib.create_user("boss", "supersecret", role="admin")
    lib.close()
    import webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient

    def client(user="reader"):
        c = TestClient(appmod.app)
        assert c.post("/login", data={"username": user, "password": "supersecret"},
                      follow_redirects=False).status_code in (302, 303)
        return c
    yield client, db, uid
    for ext in ("", "-shm", "-wal"):
        if os.path.exists(db + ext):
            os.remove(db + ext)


def _record(db, uid, stop_reason, q="What is CAC payback?"):
    lib = Library(db)
    rid = lib.record_ask_question(uid, q, ANSWER, "claude-opus-4-8", "deep", True, False, False,
                                  stop_reason=stop_reason)
    lib.conn.execute("UPDATE ask_questions SET conversation_id=? WHERE id=?", (str(rid), rid))
    lib.conn.commit()
    lib.close()
    return rid


# -- the limit -----------------------------------------------------------------

def test_deep_ceiling_is_4000_and_the_other_tiers_are_unchanged():
    s = agent.EFFORT_SETTINGS
    assert s["deep"]["max_tokens"] == 4000
    assert s["quick"]["max_tokens"] == 700
    assert s["standard"]["max_tokens"] == 1500


def test_deep_model_and_tier_shape_are_untouched():
    deep = agent.EFFORT_SETTINGS["deep"]
    assert deep["model"] == "claude-opus-4-8"
    assert set(agent.EFFORT_SETTINGS) == {"quick", "standard", "deep"}


# -- the notice helper ----------------------------------------------------------

def test_only_max_tokens_gives_a_notice():
    assert answer_cutoff_notice("max_tokens") == ANSWER_CUTOFF_NOTICE
    for other in ("end_turn", "", None, "stop_sequence", "tool_use"):
        assert answer_cutoff_notice(other) == ""


def test_notice_wording_and_voice():
    assert ANSWER_CUTOFF_NOTICE == ("This answer was cut off at the length limit. "
                                    "Ask a narrower question, or ask a follow-up to continue.")
    assert "—" not in ANSWER_CUTOFF_NOTICE and " - " not in ANSWER_CUTOFF_NOTICE
    from linklib.voice_review import mechanical_findings, typography_findings_plain
    assert not mechanical_findings(ANSWER_CUTOFF_NOTICE)
    assert not typography_findings_plain(ANSWER_CUTOFF_NOTICE)


# -- live path: POST /ask ---------------------------------------------------------

def _ask(c):
    return c.post("/ask", json={"question": "What is CAC payback?", "effort": "quick",
                                "sources": ["library"]})


def test_ask_response_carries_the_notice_when_cut_off(site, monkeypatch):
    client, db, uid = site
    monkeypatch.setattr(agent, "_get_client", lambda: _Client(_resp("max_tokens")))
    d = _ask(client()).json()
    assert d["notice"] == ANSWER_CUTOFF_NOTICE
    assert d["answer"] == ANSWER                      # not in the answer text
    lib = Library(db)
    stored = lib.conn.execute("SELECT answer, stop_reason FROM ask_questions").fetchone()
    lib.close()
    assert stored[0] == ANSWER and ANSWER_CUTOFF_NOTICE not in stored[0]
    assert stored[1] == "max_tokens"


@pytest.mark.parametrize("reason", ["end_turn", ""])
def test_ask_response_has_no_notice_when_it_finished(site, monkeypatch, reason):
    client, db, uid = site
    monkeypatch.setattr(agent, "_get_client", lambda: _Client(_resp(reason)))
    assert _ask(client()).json()["notice"] == ""


def test_a_failed_turn_is_unaffected(site, monkeypatch):
    client, db, uid = site

    class _Boom:
        class messages:
            @staticmethod
            def create(**kw):
                raise Exception("upstream 500")
    monkeypatch.setattr(agent, "_get_client", lambda: _Boom())
    r = _ask(client())
    assert r.status_code == 502 and r.json()["failed"] is True
    assert "notice" not in r.json()


def test_page_script_renders_the_notice_under_the_answer(site):
    client, db, uid = site
    html = client().get("/tools/fpa-buddy").text
    assert "function cutoffHtml(d)" in html
    # Live path and resume path both put it between the answer and its sources.
    assert "mdToHtml(d.answer) + cutoffHtml(d) + srcListHtml(d)" in html
    assert "mdToHtml(t.answer) + cutoffHtml(t) + srcListHtml(t)" in html


# -- resume transcript ------------------------------------------------------------

def test_transcript_turns_carry_the_notice(site):
    client, db, uid = site
    cut = _record(db, uid, "max_tokens")
    done = _record(db, uid, "end_turn", q="Another question?")
    c = client()
    t1 = c.get(f"/ask/conversations/{cut}").json()["turns"][0]
    t2 = c.get(f"/ask/conversations/{done}").json()["turns"][0]
    assert t1["notice"] == ANSWER_CUTOFF_NOTICE and t1["answer"] == ANSWER
    assert t2["notice"] == ""


# -- server-rendered surfaces -------------------------------------------------------

def test_history_page_shows_the_notice_only_for_a_cut_off_answer(site):
    client, db, uid = site
    _record(db, uid, "max_tokens", q="Cut off question?")
    _record(db, uid, "end_turn", q="Finished question?")
    _record(db, uid, "", q="Legacy question?")
    html = client().get("/ask/history").text
    assert html.count(ANSWER_CUTOFF_NOTICE) == 1


def test_past_questions_list_shows_the_notice(site):
    client, db, uid = site
    _record(db, uid, "max_tokens", q="Cut off question?")
    html = client().get("/tools/fpa-buddy").text
    assert ANSWER_CUTOFF_NOTICE in html


def test_admin_feedback_card_shows_the_notice(site):
    client, db, uid = site
    rid = _record(db, uid, "max_tokens")
    lib = Library(db)
    lib.conn.execute("INSERT INTO ask_feedback(question_id, user_id, rating, comment, created_at) "
                     "VALUES (?,?,?,?,?)", (rid, uid, "not_helpful", "cut off", "2026-10-06"))
    lib.conn.commit()
    lib.close()
    html = client("boss").get("/admin/fpa-buddy/feedback").text
    assert ANSWER_CUTOFF_NOTICE in html


def test_render_helper_adds_nothing_without_the_reason():
    import webapp.app as appmod
    plain, _ = appmod._render_cited_answer(ANSWER, "[]")
    cut, _ = appmod._render_cited_answer(ANSWER, "[]", "max_tokens")
    assert ANSWER_CUTOFF_NOTICE not in plain
    assert ANSWER_CUTOFF_NOTICE in cut and ANSWER in cut


# -- MCP --------------------------------------------------------------------------

def test_mcp_tool_result_includes_the_notice(site, monkeypatch):
    """ask_fpa_buddy returns run_ask's dict unchanged, so the notice rides along."""
    client, db, uid = site
    from webapp import ask_orchestrator
    monkeypatch.setattr(agent, "_get_client", lambda: _Client(_resp("max_tokens")))
    lib = Library(db)
    out = ask_orchestrator.run_ask(lib, user_id=uid, question="What is CAC payback?", effort="quick",
                                   use_library=False, use_feed=False, use_web=False)
    lib.close()
    assert out["notice"] == ANSWER_CUTOFF_NOTICE
    import inspect, webapp.mcp_qa as mq
    assert "return run_ask(" in inspect.getsource(mq)


def test_tier_tooltips_read_the_ceiling_from_effort_settings(site):
    """The hover text used to hardcode '~2,500 tokens out'; it now reads the tier's own max_tokens."""
    client, db, uid = site
    html = client().get("/tools/fpa-buddy").text
    for tier, s in agent.EFFORT_SETTINGS.items():
        assert f"~{s['max_tokens']:,} tokens out" in html, tier
    assert "~2,500 tokens out" not in html
