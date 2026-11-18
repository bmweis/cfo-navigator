"""PR 4 (2026-11): a follow-up keeps its band and gets the previous turn's computed shifts.
Evidence: ask_questions ids 76 and 77. Id 76 stored the band as "$20-$30m" (signals) and
"$20-$30M ARR" (subs), so prior_turn_context returned band "". Id 77 ("Which of those lines is
growing fastest?") ran no pre-step, so no COMPUTED SHIFTS lines reached the model and it said no
shift data exists. Invented-free: the band strings are the real stored ones; metrics are generic."""
import json
from types import SimpleNamespace

from linklib import agent

SHIFT_LINES = [
    {"line": "S&M expense at $20-$30M ARR (median): down 19 points, from 40% (2021) to 21% (2022); one series: Example Report",
     "kind": "points", "size": 19.0, "threshold": 3.0, "directional": False},
    {"line": "Total employees at $20-$30M ARR (median): down 41.8 percent, from 120 to 70; one series: Example Report",
     "kind": "relative", "size": 41.8, "threshold": 10.0, "directional": False},
]


def _turn(prestep: dict) -> dict:
    return {"question": "q", "answer": "a", "failed": 0, "prestep_json": json.dumps(prestep)}


ID76 = {"outcome": "ran", "detected": ["S&M expense"],
        "signals": {"bands": ["$20-$30m"]},
        "subs": [{"intent": "benchmark", "metrics": ["S&M expense"], "band": "$20-$30M ARR"}],
        "shifts": SHIFT_LINES}


# --- Part A ---------------------------------------------------------------------

def test_band_spellings_from_id_76_count_as_one_band():
    ctx = agent.prior_turn_context([_turn(ID76)])
    assert ctx["band"] == "$20-$30m"


def test_genuinely_different_bands_still_give_no_band():
    p = dict(ID76, signals={"bands": ["$20-$30m"]},
             subs=[{"intent": "benchmark", "metrics": ["S&M expense"], "band": "$30-$50M ARR"}])
    assert agent.prior_turn_context([_turn(p)])["band"] == ""


# --- Part B ---------------------------------------------------------------------

def test_prior_context_carries_the_stored_shift_lines():
    assert agent.prior_turn_context([_turn(ID76)])["shifts"] == SHIFT_LINES


def test_prior_turn_with_no_shifts_adds_nothing():
    p = {k: v for k, v in ID76.items() if k != "shifts"}
    assert "shifts" not in agent.prior_turn_context([_turn(p)])


def test_shifts_carry_from_the_nearest_earlier_turn_that_has_them():
    follow = _turn({"outcome": "skipped: no benchmark metric detected"})
    # last turn has no record of its own; metrics come from it, so give it a ran record without shifts
    last = _turn({k: v for k, v in ID76.items() if k != "shifts"})
    assert agent.prior_turn_context([_turn(ID76), last])["shifts"] == SHIFT_LINES
    assert agent.prior_turn_context([_turn(ID76), follow]) == {}   # last turn not a lookup: unchanged behaviour


def _run(monkeypatch, tmp_path, prior_context, prestep=None):
    seen = {}

    def create(**kw):
        seen.update(kw)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")], stop_reason="end_turn",
                               usage=SimpleNamespace(input_tokens=10, output_tokens=5))
    monkeypatch.setattr(agent, "_get_client", lambda: SimpleNamespace(messages=SimpleNamespace(create=create)))
    monkeypatch.setattr(agent, "retrieve", lambda lib, q, max_sources=8: ([], 0, 0.0))
    monkeypatch.setattr(agent, "_rewrite_followup", lambda h, q, prior=None: None)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    import importlib.util
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda n, *a, **k: object() if n == "anthropic" else real(n, *a, **k))
    from linklib import benchmark_prestep as bp
    monkeypatch.setattr(bp, "plan_turn", lambda *a, **k: prestep or bp.Prestep(outcome="skipped: no benchmark metric detected"))
    from linklib.db import Library
    lib = Library(str(tmp_path / "t.db"))
    lib.set_setting("voice_core", "Plain.")
    lib.set_setting("voice_fpa_buddy", "Plain.")
    agent.answer_question(lib, "Which of those lines is growing fastest?", use_web=False,
                              history=[{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}],
                              prior_context=prior_context)
    return seen


def _notes(seen) -> str:
    msgs = seen.get("messages") or []
    return "\n".join(b.get("text", "") for b in msgs[-1]["content"] if isinstance(b, dict)) if msgs else ""


def test_skipped_followup_gets_labelled_previous_shifts(monkeypatch, tmp_path):
    seen = _run(monkeypatch, tmp_path, agent.prior_turn_context([_turn(ID76)]))
    notes = _notes(seen)
    assert "computed on the previous answer in this conversation" in notes
    assert SHIFT_LINES[0]["line"] in notes and SHIFT_LINES[1]["line"] in notes
    assert "$20-$30M ARR" in notes
    assert "Do not use {{shifts}}" in notes


def test_followup_with_its_own_shifts_does_not_carry(monkeypatch, tmp_path):
    from linklib import benchmark_prestep as bp
    own = bp.Prestep(outcome="ran")
    own.active = True
    own.shifts = [{"line": "FRESH LINE", "kind": "points", "size": 5.0, "threshold": 3.0, "directional": False}]
    own.plan_text = "COMPUTED SHIFTS (fresh)\n  1. FRESH LINE"
    seen = _run(monkeypatch, tmp_path, agent.prior_turn_context([_turn(ID76)]), prestep=own)
    notes = _notes(seen)
    assert "FRESH LINE" in notes
    assert "computed on the previous answer" not in notes
    assert SHIFT_LINES[0]["line"] not in notes


def test_no_carry_without_prior_shifts(monkeypatch, tmp_path):
    p = {k: v for k, v in ID76.items() if k != "shifts"}
    seen = _run(monkeypatch, tmp_path, agent.prior_turn_context([_turn(p)]))
    assert "computed on the previous answer" not in _notes(seen)
