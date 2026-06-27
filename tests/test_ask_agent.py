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


def test_system_prompt_has_persona_and_no_verbatim_guardrail():
    s = agent._build_system(use_library=True, use_feed=False, use_web=True).lower()
    assert "advisor" in s
    assert "verbatim" in s              # the monetization guardrail
    assert "saved library" in s         # library-first grounding
    assert "clarifying question" in s   # engages, doesn't one-shot
