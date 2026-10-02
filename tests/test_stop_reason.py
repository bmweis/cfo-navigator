"""stop_reason measurement (2026-10): how often a model answer is cut off by
max_tokens. Measurement only; no behavior change.

- ask_questions and matchmaker_questions carry a stop_reason column, added by
  the idempotent migration (fresh DB, existing DB, re-run), written from the
  existing record calls (so the MCP answer paths, which use the same calls,
  are covered).
- Enrichment call sites log one WARNING per max_tokens stop.
"""
import logging
import os
import pathlib
import sqlite3
import sys
import tempfile
import types

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import agent, enrich, matchmaker
from linklib.db import Library
from linklib.stop_reason import stop_reason_of, warn_if_max_tokens


def _cols(db_path, table):
    c = sqlite3.connect(db_path)
    try:
        return {r[1]: r for r in c.execute(f"PRAGMA table_info({table})")}
    finally:
        c.close()


@pytest.fixture
def dbpath():
    p = tempfile.mktemp(suffix=".db")
    yield p
    if os.path.exists(p):
        os.remove(p)


# -- migration ----------------------------------------------------------------

def test_fresh_database_has_the_column_on_both_tables(dbpath):
    Library(dbpath).close()
    for table in ("ask_questions", "matchmaker_questions"):
        col = _cols(dbpath, table)["stop_reason"]
        assert col[2] == "TEXT" and col[3] == 1 and col[4] == "''"   # NOT NULL DEFAULT ''


def _strip_column(dbpath, table):
    """Rebuild `table` without stop_reason, as an existing production DB has it."""
    c = sqlite3.connect(dbpath)
    c.execute(f"ALTER TABLE {table} DROP COLUMN stop_reason")
    c.commit()
    c.close()


def test_existing_database_gets_the_column_and_rerun_is_idempotent(dbpath):
    lib = Library(dbpath)
    uid = lib.create_user("m", "supersecret", role="user", name="M")
    lib.record_ask_question(uid, "Old q?", "Old a.", "claude-sonnet-4-6", "standard", True, False, True)
    lib.record_matchmaker_question("sess", "software", "Old mm q?", "Old mm a.", "claude-sonnet-4-6")
    lib.close()
    _strip_column(dbpath, "ask_questions")
    _strip_column(dbpath, "matchmaker_questions")
    assert "stop_reason" not in _cols(dbpath, "ask_questions")

    Library(dbpath).close()            # migration adds it
    Library(dbpath).close()            # second boot: no error, still one column
    for table in ("ask_questions", "matchmaker_questions"):
        assert "stop_reason" in _cols(dbpath, table)
        c = sqlite3.connect(dbpath)
        assert c.execute(f"SELECT stop_reason FROM {table}").fetchall() == [("",)]   # existing rows read ''
        c.close()


# -- recording ----------------------------------------------------------------

def test_record_calls_persist_stop_reason_and_default_to_blank(dbpath):
    lib = Library(dbpath)
    uid = lib.create_user("m", "supersecret", role="user", name="M")
    a = lib.record_ask_question(uid, "q?", "a", "m", "standard", True, False, True, stop_reason="max_tokens")
    b = lib.record_ask_question(uid, "q2?", "a", "m", "standard", True, False, True)
    c = lib.record_matchmaker_question("s", "software", "q?", "a", "m", stop_reason="max_tokens")
    d = lib.record_matchmaker_question("s", "software", "q2?", "a", "m")
    q = lambda t, i: lib.conn.execute(f"SELECT stop_reason FROM {t} WHERE id=?", (i,)).fetchone()[0]
    assert (q("ask_questions", a), q("ask_questions", b)) == ("max_tokens", "")
    assert (q("matchmaker_questions", c), q("matchmaker_questions", d)) == ("max_tokens", "")
    # The documented counting query runs and counts only the cut-off turn.
    n = lib.conn.execute("SELECT model, COUNT(*) FROM ask_questions WHERE stop_reason='max_tokens' "
                         "GROUP BY model").fetchall()
    assert [tuple(r) for r in n] == [("m", 1)]
    lib.close()


# -- answer paths carry it through --------------------------------------------

def _resp(stop_reason):
    return types.SimpleNamespace(
        content=[types.SimpleNamespace(type="text", text="An answer that stops here", citations=None)],
        usage=types.SimpleNamespace(input_tokens=10, output_tokens=20,
                                    cache_creation_input_tokens=0, cache_read_input_tokens=0),
        stop_reason=stop_reason)


class _Client:
    def __init__(self, resp):
        self.messages = types.SimpleNamespace(create=lambda **kw: resp)


@pytest.mark.parametrize("reason", ["max_tokens", "end_turn"])
def test_buddy_answer_carries_stop_reason(dbpath, monkeypatch, reason):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setattr(agent, "_get_client", lambda: _Client(_resp(reason)))
    lib = Library(dbpath)
    lib.seed_voice_prompts()
    ans = agent.answer_question(lib, "What is CAC payback?", use_library=False, use_feed=False, use_web=False)
    lib.close()
    assert ans.stop_reason == reason


@pytest.mark.parametrize("reason", ["max_tokens", "end_turn"])
def test_matchmaker_answer_carries_stop_reason(dbpath, monkeypatch, reason):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setattr(matchmaker, "_get_client", lambda: _Client(_resp(reason)))
    lib = Library(dbpath)
    lib.seed_voice_prompts()
    ans = matchmaker.answer_software_question(lib, "Which tool?")
    lib.close()
    assert ans.stop_reason == reason


def test_ask_orchestrator_writes_it_to_the_row(dbpath, monkeypatch):
    """run_ask is what POST /ask and the MCP ask_fpa_buddy tool both call."""
    from webapp import ask_orchestrator
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setattr(agent, "_get_client", lambda: _Client(_resp("max_tokens")))
    lib = Library(dbpath)
    lib.seed_voice_prompts()
    uid = lib.create_user("m", "supersecret", role="user", name="M")
    ask_orchestrator.run_ask(lib, user_id=uid, question="What is CAC payback?", effort="quick",
                             use_library=False, use_feed=False, use_web=False)
    rows = lib.conn.execute("SELECT stop_reason FROM ask_questions").fetchall()
    lib.close()
    assert [r[0] for r in rows] == ["max_tokens"]


# -- the helpers ---------------------------------------------------------------

def test_stop_reason_of_is_always_a_string():
    assert stop_reason_of(types.SimpleNamespace(stop_reason="max_tokens")) == "max_tokens"
    assert stop_reason_of(types.SimpleNamespace()) == ""
    assert stop_reason_of(types.SimpleNamespace(stop_reason=None)) == ""
    assert stop_reason_of(types.SimpleNamespace(stop_reason=object())) == ""   # e.g. a mock


def test_warn_logs_only_on_max_tokens(caplog):
    with caplog.at_level(logging.WARNING, logger="linklib.stop_reason"):
        warn_if_max_tokens(_resp("end_turn"), "site.a")
        assert caplog.records == []
        warn_if_max_tokens(_resp("max_tokens"), "site.b")
    assert [r.getMessage() for r in caplog.records] == ["stop_reason=max_tokens call_site=site.b"]


def test_enrichment_call_site_logs_one_warning_per_max_tokens_stop(monkeypatch, caplog):
    import anthropic
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    resp = _resp("max_tokens")
    resp.content = [types.SimpleNamespace(type="text", text="not json")]   # parse fails; the warning must precede it
    monkeypatch.setattr(anthropic, "Anthropic", lambda *a, **k: _Client(resp))
    with caplog.at_level(logging.WARNING, logger="linklib.stop_reason"):
        enrich.enrich("A title", "Some text")
    ours = [r.getMessage() for r in caplog.records if r.name == "linklib.stop_reason"]
    assert ours == ["stop_reason=max_tokens call_site=enrich.enrich"]


def test_every_enrichment_generation_call_has_a_warning():
    """Each messages.create in enrich.py (bar the 64-token connectivity test)
    is followed by a warn_if_max_tokens line, so no call site is left uncounted."""
    src = pathlib.Path(enrich.__file__).read_text().split("\n")
    created = [i for i, l in enumerate(src) if "resp = client.messages.create(" in l]
    assert len(created) == 10                      # 9 generation calls + test_model_connection
    warned = [l for l in src if "warn_if_max_tokens(resp," in l]
    assert len(warned) == 9
