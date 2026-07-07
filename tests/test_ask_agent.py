"""Tests for the Ask engine (linklib/agent.py): grounding, caps, persona prompt.

No API calls — these pin the deterministic plumbing around the model call:
how much archived text grounds an answer, the conversation cost guards, and
that the system prompt carries the advisor persona + no-verbatim guardrail.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import agent


def test_ground_body_combines_summary_and_archived_text():
    h = {"summary": "S" * 40, "content": "C" * 5000}
    b = agent._ground_body(h, 1000)
    assert len(b) <= 1000
    assert b.startswith("S" * 40)      # summary leads
    assert "C" in b                    # archived-text excerpt follows


def test_ground_body_summary_only_when_no_content():
    assert agent._ground_body({"summary": "abc", "content": ""}, 1000) == "abc"


def test_ground_body_content_only_when_no_summary():
    assert agent._ground_body({"summary": "", "content": "xyz"}, 1000) == "xyz"


def test_format_respects_global_cap():
    hits = [{"title": f"T{i}", "url": f"u{i}", "summary": "",
             "content": "x" * 1000, "tags": []} for i in range(20)]
    out = agent._format_all_sources(hits, [], source_chars=1000, global_chars=3000)
    assert out.count("x") <= 3000      # total grounding text bounded


def test_trim_history_filters_malformed_and_keeps_valid():
    hist = [{"role": "user", "content": "a"}, {"role": "bogus", "content": "b"},
            {"role": "assistant", "content": "c"}, {"bad": 1}, "nonsense"]
    t = agent._trim_history(hist)
    assert all(m["role"] in ("user", "assistant") for m in t)
    assert {"role": "user", "content": "a"} in t
    assert {"role": "assistant", "content": "c"} in t


def test_trim_history_char_budget(monkeypatch):
    monkeypatch.setattr(agent, "MAX_HISTORY_CHARS", 50)
    hist = [{"role": "user", "content": "x" * 100},
            {"role": "assistant", "content": "y" * 100},
            {"role": "user", "content": "z" * 10}]
    t = agent._trim_history(hist)
    assert t and t[-1]["content"] == "z" * 10      # most recent kept
    assert sum(len(m["content"]) for m in t) <= 100 + 50  # roughly bounded


def test_count_prior_questions_and_cap_threshold():
    hist = []
    for i in range(agent.MAX_FOLLOWUPS + 1):     # 7 questions = first + 6 follow-ups
        hist += [{"role": "user", "content": f"q{i}"}, {"role": "assistant", "content": "a"}]
    assert agent.count_prior_questions(hist) == agent.MAX_FOLLOWUPS + 1
    # an 8th would exceed the cap
    assert agent.count_prior_questions(hist) >= 1 + agent.MAX_FOLLOWUPS


# --- Follow-up query rewrite (history-aware retrieval) ----------------------
# answer_question is exercised without an API key (deleted below), so it stops
# at the early no-key return — but the rewrite decision and retrieval run
# before that, which is exactly the plumbing these tests pin.

_HIST = [{"role": "user", "content": "What are typical SaaS pricing benchmarks?"},
         {"role": "assistant", "content": "Benchmarks vary by segment..."}]


def _capture_retrieval(monkeypatch):
    """Stub retrieve() and record the question it was called with."""
    seen = {}

    def fake_retrieve(lib, question, max_sources=8):
        seen["question"] = question
        return []

    monkeypatch.setattr(agent, "retrieve", fake_retrieve)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return seen


def test_turn_one_skips_rewrite_entirely(monkeypatch):
    seen = _capture_retrieval(monkeypatch)

    def boom(*a, **k):
        raise AssertionError("rewrite must not run on turn one")

    monkeypatch.setattr(agent, "_rewrite_followup", boom)
    ans = agent.answer_question(None, "raw question", use_web=False, history=None)
    assert seen["question"] == "raw question"
    assert ans.rewrite_input_tokens == 0
    assert ans.rewrite_cost_usd == 0.0


def test_followup_retrieves_on_rewritten_query(monkeypatch):
    seen = _capture_retrieval(monkeypatch)
    rw = agent.RewriteResult(text="SaaS pricing benchmarks for Series A companies",
                             input_tokens=200, output_tokens=15, cost_usd=0.0003)
    monkeypatch.setattr(agent, "_rewrite_followup", lambda h, q: rw)

    ans = agent.answer_question(None, "what about for a Series A stage company?",
                                use_web=False, history=_HIST)
    # Retrieval sees the standalone rewrite, not the raw follow-up.
    assert seen["question"] == rw.text
    # The rewrite's real spend rides on the Answer and inside the turn total.
    assert ans.rewrite_input_tokens == 200
    assert ans.rewrite_output_tokens == 15
    assert ans.rewrite_cost_usd == 0.0003
    assert ans.cost_usd >= 0.0003


def test_rewrite_call_failure_falls_back_to_raw_question(monkeypatch):
    seen = _capture_retrieval(monkeypatch)
    monkeypatch.setattr(agent, "_rewrite_followup", lambda h, q: None)  # call never ran
    ans = agent.answer_question(None, "what about churn?", use_web=False, history=_HIST)
    assert seen["question"] == "what about churn?"
    assert ans.rewrite_cost_usd == 0.0     # nothing was spent


def test_rewrite_malformed_output_falls_back_but_records_spend(monkeypatch):
    seen = _capture_retrieval(monkeypatch)
    rw = agent.RewriteResult(text="", input_tokens=180, output_tokens=120, cost_usd=0.0008)
    monkeypatch.setattr(agent, "_rewrite_followup", lambda h, q: rw)
    ans = agent.answer_question(None, "what about churn?", use_web=False, history=_HIST)
    assert seen["question"] == "what about churn?"   # unusable text → raw question
    assert ans.rewrite_cost_usd == 0.0008            # ...but the call still cost money
    assert ans.cost_usd >= 0.0008


def test_rewrite_gets_trimmed_history_not_raw(monkeypatch):
    """The rewrite must see the same _trim_history-bounded turns as the prompt."""
    _capture_retrieval(monkeypatch)
    got = {}

    def spy(trimmed, q):
        got["history"] = trimmed
        return None

    monkeypatch.setattr(agent, "_rewrite_followup", spy)
    hist = _HIST + [{"role": "bogus", "content": "junk"}, "nonsense"]
    agent.answer_question(None, "follow-up?", use_web=False, history=hist)
    assert got["history"] == agent._trim_history(hist)


def test_clean_rewrite_output_validation():
    clean = agent._clean_rewrite_output
    assert clean("SaaS benchmarks for Series A") == "SaaS benchmarks for Series A"
    assert clean('"quoted question"') == "quoted question"
    assert clean("first line\nsecond line") == "first line"
    assert clean("\n\n  padded  \n") == "padded"
    assert clean("") == ""
    assert clean(None) == ""
    assert clean("x" * (agent.REWRITE_MAX_CHARS + 1)) == ""   # rambled → malformed


def test_system_prompt_has_persona_and_no_verbatim_guardrail():
    s = agent._build_system(use_library=True, use_feed=False, use_web=True).lower()
    assert "advisor" in s
    assert "verbatim" in s              # the monetization guardrail
    assert "saved library" in s         # library-first grounding
    assert "clarifying question" in s   # engages, doesn't one-shot
