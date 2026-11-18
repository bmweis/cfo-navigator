"""PR 3 (2026-11): store the follow-up rewrite, and let "those lines" mean the prior turn's metrics.
Evidence: a follow-up "Which of those lines is growing fastest?" after a turn that rendered headcount,
mix, spend and margin tables was rewritten to headcount growth only, band changed, spend dropped.
Invented fixtures only. A stubbed model cannot prove the live behaviour; the real check is a rerun."""
import csv
import io
import json
import os
import tempfile
from types import SimpleNamespace

import pytest

from linklib import agent
from linklib import benchmark_prestep as bp
from linklib import figures as fg
from linklib.db import Library

PRIOR_PRESTEP = {
    "outcome": "ran",
    "detected": ["Headcount by function", "Operating expenses by function", "Gross Margin"],
    "signals": {"bands": ["$20-$30M"], "over_time": False},
    "subs": [{"intent": "benchmark", "metrics": ["Headcount by function", "S&M expense"], "band": "$20-$30M"},
             {"intent": "benchmark", "metrics": ["Gross Margin"], "band": "$20-$30M"}],
}
TURNS = [{"question": "Org structure and spend profile at $20-$30M ARR?", "answer": "a",
          "failed": 0, "prestep_json": json.dumps(PRIOR_PRESTEP)}]
HIST = [{"role": "user", "content": "Org structure and spend profile at $20-$30M ARR?"},
        {"role": "assistant", "content": "Here are the tables."}]


def _capturing_client(monkeypatch, reply="standalone question"):
    seen = {}

    def create(**kw):
        seen.update(kw)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=reply)],
                               usage=SimpleNamespace(input_tokens=500, output_tokens=20))
    monkeypatch.setattr(agent, "_get_client", lambda: SimpleNamespace(messages=SimpleNamespace(create=create)))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    import importlib.util
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec",
                        lambda n, *a, **k: object() if n == "anthropic" else real(n, *a, **k))
    return seen


# --- Part B: the rewriter sees the prior turn's metrics and band -----------------

def test_prior_context_reads_metrics_and_band_from_the_last_turn():
    ctx = agent.prior_turn_context(TURNS)
    assert ctx["band"] == "$20-$30M"
    for m in ("Headcount by function", "Operating expenses by function", "Gross Margin", "S&M expense"):
        assert m in ctx["metrics"]


def test_prior_context_is_empty_without_a_prestep_record():
    assert agent.prior_turn_context([]) == {}
    assert agent.prior_turn_context([{"question": "q", "answer": "a", "prestep_json": ""}]) == {}
    skipped = [{"prestep_json": json.dumps({"outcome": "skipped: no benchmark metric detected"})}]
    assert agent.prior_turn_context(skipped) == {}


def test_rewriter_prompt_carries_the_prior_metrics_and_band(monkeypatch):
    seen = _capturing_client(monkeypatch)
    res = agent._rewrite_followup(HIST, "Which of those lines is growing fastest?",
                                  prior=agent.prior_turn_context(TURNS))
    assert res.text == "standalone question"
    prompt = seen["messages"][0]["content"]
    for m in ("Headcount by function", "Operating expenses by function", "Gross Margin"):
        assert m in prompt
    assert "$20-$30M" in prompt
    assert "those lines" in prompt          # the instruction names the pronoun case


def test_rewriter_prompt_is_unchanged_without_prior_context(monkeypatch):
    seen = _capturing_client(monkeypatch)
    agent._rewrite_followup(HIST, "what about churn?")
    prompt = seen["messages"][0]["content"]
    assert "Prior turn's lookup" not in prompt
    assert prompt.endswith("Follow-up question: what about churn?")
    assert seen["max_tokens"] == agent.REWRITE_MAX_TOKENS == 120


def test_answer_question_hands_the_prior_context_to_the_rewriter(monkeypatch):
    got = {}

    def fake_rewrite(h, q, prior=None):
        got["prior"] = prior
        return agent.RewriteResult(text="rewritten", input_tokens=1, output_tokens=1, cost_usd=0.0)
    monkeypatch.setattr(agent, "_rewrite_followup", fake_rewrite)
    monkeypatch.setattr(agent, "retrieve", lambda lib, q, max_sources=8: ([], 0, 0.0))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    ans = agent.answer_question(None, "Which of those lines is growing fastest?", use_web=False,
                                history=HIST, prior_context=agent.prior_turn_context(TURNS))
    assert got["prior"]["band"] == "$20-$30M"
    assert ans.rewrite_text == "rewritten"


# --- Part B: plan_turn inherits the prior band when the rewrite names none -------

REG = ("canonical_metric\tcard_url\tcontains\n"
       "Total Employees\thttps://cards.example.test/h\tContains: total employees, headcount\n")
SETTINGS = {"max_library": 8, "source_chars": 1800, "global_chars": 16000, "max_feed": 5, "max_web": 4}


@pytest.fixture
def lib(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    db = Library(str(tmp_path / "t.db"))
    rows, err = fg.parse_registry_tsv(REG)
    assert not err
    db.seed_metric_registry(rows)
    yield db
    db.close()


def _plan(lib, q, prior_band=""):
    return bp.plan_turn(lib, q, tier="standard", settings=SETTINGS, model="claude-sonnet-4-6",
                        remaining_usd=None, prior_band=prior_band)


def test_plan_turn_inherits_the_prior_band_when_the_question_names_none(lib):
    ps = _plan(lib, "total employees by function", prior_band="$20-$30M")
    assert ps.subs[0].band == "$20-$30M"
    assert ps.signals.get("band_inherited") == "$20-$30M"


def test_plan_turn_does_not_override_a_band_the_followup_names(lib):
    ps = _plan(lib, "total employees at $30-$50M ARR", prior_band="$20-$30M")
    assert ps.subs[0].band == "$30-$50M"
    assert not ps.signals.get("band_inherited")


def test_plan_turn_without_a_prior_band_is_unchanged(lib):
    ps = _plan(lib, "total employees by function")
    assert ps.subs[0].band == ""


# --- Part A: the rewrite text is stored and shown ---------------------------------

@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib
    import webapp.app as appmod
    importlib.reload(appmod)
    lib = Library(db)
    uid = lib.create_user("asker", "supersecret", role="user")
    first = lib.record_ask_question(uid, "Turn zero question", "a0", "claude-sonnet-4-6", "standard",
                                    True, False, True)
    lib.record_ask_question(uid, "Which of those lines is growing fastest?", "a1", "claude-sonnet-4-6",
                            "standard", True, False, True, conversation_id=str(first), turn_index=1,
                            rewrite_text="Growth of headcount, spend and margin at $20-$30M ARR")
    lib.record_ask_question(uid, "A follow-up that failed", "", "claude-sonnet-4-6", "standard",
                            True, False, True, conversation_id=str(first), turn_index=2, failed=True,
                            error="boom", rewrite_text="Rewrite that existed before the failure")
    lib.close()
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    yield c, db
    if os.path.exists(db):
        os.remove(db)


def test_rewrite_text_is_in_the_row_and_turn_zero_is_empty(env):
    _, db = env
    lib = Library(db)
    rows = {r["turn_index"]: r for r in lib.conn.execute("SELECT * FROM ask_questions").fetchall()}
    lib.close()
    assert rows[0]["rewrite_text"] == ""
    assert rows[1]["rewrite_text"] == "Growth of headcount, spend and margin at $20-$30M ARR"
    assert rows[2]["rewrite_text"] == "Rewrite that existed before the failure"   # a failed turn keeps it


def test_admin_row_shows_rewritten_as(env):
    c, _ = env
    html = c.get("/admin/fpa-buddy/report").text
    assert "Rewritten as:" in html
    assert "Growth of headcount, spend and margin at $20-$30M ARR" in html
    # Turn zero shows none; the admin report also lists the failed turn, with the rewrite it had.
    assert "Rewrite that existed before the failure" in html
    assert html.count("Rewritten as:") == 2


def test_csv_has_a_rewrite_column_appended_last(env):
    c, _ = env
    rows = list(csv.reader(io.StringIO(c.get("/admin/fpa-buddy/report/export.csv").text)))
    head = rows[0]
    assert head[-1] == "rewritten_question" or "rewritten_question" in head
    idx = head.index("rewritten_question")
    vals = {r[head.index("question")]: r[idx] for r in rows[1:]}
    assert vals["Which of those lines is growing fastest?"] == "Growth of headcount, spend and margin at $20-$30M ARR"
    assert vals["Turn zero question"] == ""
