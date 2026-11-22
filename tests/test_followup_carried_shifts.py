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
    assert "computed on the earlier answer in this conversation" in notes
    assert SHIFT_LINES[0]["line"] in notes and SHIFT_LINES[1]["line"] in notes
    assert "$20-$30M ARR" in notes
    assert "write {{shifts}} on a line of its own" in notes     # one rule for fresh and carried lines


def test_followup_with_its_own_shifts_does_not_carry(monkeypatch, tmp_path):
    from linklib import benchmark_prestep as bp
    own = bp.Prestep(outcome="ran")
    own.active = True
    own.shifts = [{"line": "FRESH LINE", "kind": "points", "size": 5.0, "threshold": 3.0, "directional": False}]
    own.plan_text = "COMPUTED SHIFTS (fresh)\n  1. FRESH LINE"
    seen = _run(monkeypatch, tmp_path, agent.prior_turn_context([_turn(ID76)]), prestep=own)
    notes = _notes(seen)
    assert "FRESH LINE" in notes
    assert "[computed on the earlier answer" not in notes
    assert SHIFT_LINES[0]["line"] not in notes


def test_no_carry_without_prior_shifts(monkeypatch, tmp_path):
    p = {k: v for k, v in ID76.items() if k != "shifts"}
    seen = _run(monkeypatch, tmp_path, agent.prior_turn_context([_turn(p)]))
    assert "[computed on the earlier answer" not in _notes(seen)


# --- 2026-11-19: surface computed changes (ask_questions ids 78 and 79) -----------------------------------
# Real ids 78 (org-structure question) and 79 (its "which of those lines is growing fastest?" follow-up) were read
# over the read-only MCP. Third-party report text never goes in the repo, so these fixtures keep the SHAPE of those
# rows (four own lines on turn 0 with gross margin third; two headcount lines on the follow-up, one of them the
# same change turn 0 stored in lower case) with invented publishers and figures.

def _sh(metric, line, kind, size, thr, frm=None, to=None, **extra):
    d = {"metric": metric, "band": "$20-50M", "statistic": "median", "line": line, "kind": kind, "size": size,
         "threshold": thr, "directional": False}
    if frm:
        d["from"] = {"report_slug": frm[0], "source": "Example Benchmarks", "report": "Example Report", "year": frm[1],
                     "period": frm[2], "value": frm[3]}
        d["to"] = {"report_slug": to[0], "source": "Example Benchmarks", "report": "Example Report", "year": to[1],
                   "period": to[2], "value": to[3]}
    d.update(extra)
    return d


T0 = [
    _sh("S&M expense", "S&M expense at $20-50M (median): down 19 points, from 54% to 35%; derived from stored reports",
        "points", 19.0, 3.0, ("ex-23", "2023", "2021", "54"), ("ex-23", "2023", "2022", "35")),
    _sh("employees", "employees at $20-50M (median): down 41.8 percent, from 225 to 131; derived from stored reports",
        "relative", 41.8, 10.0, ("ex-25", "2025", "Q2 2022", "225"), ("ex-25", "2025", "Q2 2025", "131")),
    _sh("Subscription gross margin", "Subscription gross margin at $20-50M (median): up 6 points, from 80% to 86%, a 2-year span; derived from stored reports",
        "points", 6.0, 3.0, ("ex-23", "2023", "2022", "80"), ("ex-25", "2025", "CY-24", "86")),
    _sh("R&D expense", "R&D expense at $10-25M (median): down 5 points, from 34% to 29%; derived from stored reports",
        "points", 5.0, 3.0, ("ex-22", "2022", "2021", "34"), ("ex-25", "2025", "2024", "29")),
]
# What turn 0 stored BEFORE endpoints were kept: the first five keys only.
T0_OLD = [{k: x[k] for k in ("line", "kind", "size", "threshold", "directional")} for x in T0]
FOLLOW_OWN = [
    _sh("Employees", "Employees at $20-50M (median): down 41.8 percent, from 225 to 131; derived from stored reports",
        "relative", 41.8, 10.0, ("ex-25", "2025", "Q2 2022", "225"), ("ex-25", "2025", "Q2 2025", "131")),
    _sh("Employees", "Employees at $20-50M (median): down 27 percent, from 226 to 165; derived from stored reports",
        "relative", 27.0, 10.0, ("ex-o2", "2022", "Q2 2022", "226"), ("ex-o3", "2023", "Q2 2023", "165")),
]


def test_merge_keeps_own_first_drops_the_duplicate_and_marks_carried():
    from linklib import benchmark_prestep as bp
    for prior in (T0, T0_OLD):          # with endpoints, and a turn stored before endpoints were kept
        merged = bp.merge_shifts(FOLLOW_OWN, prior)
        assert merged[:2] == FOLLOW_OWN and not any(x.get("carried") for x in merged[:2])
        carried = [x for x in merged if x.get("carried")]
        assert [x["line"].split(" at ")[0].lower() for x in carried] == ["s&m expense", "subscription gross margin", "r&d expense"]
        assert len(merged) == 5          # the headcount change turn 0 also stored is not printed twice


def test_merge_caps_at_six_with_own_lines_first():
    from linklib import benchmark_prestep as bp
    own = [_sh(f"M{i}", f"M{i} line {i}", "points", 9.0, 3.0) for i in range(4)]
    merged = bp.merge_shifts(own, T0)
    assert len(merged) == 6 and merged[:4] == own
    assert [bool(x.get("carried")) for x in merged] == [False] * 4 + [True] * 2


def test_merge_with_nothing_carried_returns_own_unchanged():
    from linklib import benchmark_prestep as bp
    assert bp.merge_shifts(FOLLOW_OWN, None) == FOLLOW_OWN
    assert bp.merge_shifts(FOLLOW_OWN, []) == FOLLOW_OWN


def test_stored_shift_entries_keep_their_endpoints():
    from linklib import benchmark_prestep as bp
    ps = bp.Prestep(outcome="ran")
    ps.active = True
    ps.shifts = bp.merge_shifts(FOLLOW_OWN, T0)
    logged = ps.log()["shifts"]
    assert logged[0]["from"]["period"] == "Q2 2022" and logged[0]["to"]["value"] == "131"
    assert logged[2]["carried"] is True and logged[2]["metric"]
    assert set(logged[0]) >= {"line", "kind", "size", "threshold", "directional", "from", "to"}


def test_render_prints_four_lines_so_the_third_ranked_gross_margin_line_is_not_cut():
    from linklib import table_render as tr
    text, info = tr.render_shifts("## What shifted\n\n{{shifts}}", T0, lambda row: 1)
    assert info["lines"] == 4
    assert "Subscription gross margin" in text and "R&D expense" in text


def test_carried_lines_are_code_rendered_cited_and_labelled():
    from linklib import table_render as tr
    from linklib import benchmark_prestep as bp
    merged = bp.merge_shifts(FOLLOW_OWN[:1], T0)
    text, info = tr.render_shifts("{{shifts}}", merged, lambda row: {"ex-23": 3, "ex-25": 4}.get(row["report_slug"]),
                                  max_lines=bp.MAX_MERGED_SHIFT_LINES)
    lines = [ln for ln in text.split("\n\n") if ln]
    assert len(lines) == 4
    assert "computed on the earlier answer" not in lines[0]
    assert all("(computed on the earlier answer in this conversation)" in ln for ln in lines[1:])
    assert "[3]" in lines[1] and "[4]" in lines[1 + 1]
    assert all(ln.endswith("(derived from stored reports).") for ln in lines)


def test_a_line_stored_without_endpoints_still_prints_uncited_and_labelled():
    from linklib import table_render as tr
    from linklib import benchmark_prestep as bp
    merged = bp.merge_shifts([], T0_OLD)
    text, _ = tr.render_shifts("{{shifts}}", merged, lambda row: 9, max_lines=6)
    assert "[9]" not in text and text.count("computed on the earlier answer in this conversation") == 4


def _answer(monkeypatch, tmp_path, prior_context, model_text, prestep=None):
    seen = {}

    def create(**kw):
        seen.update(kw)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=model_text)], stop_reason="end_turn",
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
    ans = agent.answer_question(lib, "Which of those lines is growing fastest?", use_web=False,
                                history=[{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}],
                                prior_context=prior_context)
    return ans, seen


def test_followup_with_no_lookup_prints_the_carried_lines_in_code(monkeypatch, tmp_path):
    ans, seen = _answer(monkeypatch, tmp_path, {"metrics": ["S&M expense"], "band": "", "shifts": T0},
                        "## What shifted\n\n{{shifts}}\n\nR&D and G&A have no computed change.")
    assert "{{shifts}}" not in ans.text
    assert ans.text.count("(computed on the earlier answer in this conversation)") == 4
    assert "Subscription gross margin" in ans.text and "down 19 points" in ans.text
    assert "first six of these lines" in _notes(seen)       # carried lines raise the cap from four to six


# --- the follow-up lookup: own lines merged with the carried ones inside the pre-step ----------------------------

def test_shift_notes_merges_carried_lines_into_the_pre_step(monkeypatch):
    from linklib import benchmark_prestep as bp
    monkeypatch.setattr(bp.fg, "usable_segment", lambda lib, seg: "")
    monkeypatch.setattr(bp, "metric_segment", lambda *a, **k: "")
    monkeypatch.setattr(bp.fg, "find_figures", lambda *a, **k: {"rows": [{"report_slug": "x", "figure_key": 1}]})
    out = {"shifts": [dict(x, unit="percent") for x in FOLLOW_OWN], "suppressed": 0, "skipped": [], "unpaired": {}}
    monkeypatch.setattr(bp.fg, "compute_shifts", lambda rows, rules=None, **k: out)
    monkeypatch.setattr(bp.fg, "rank_shifts", lambda shifts, cap, **k: (list(shifts), {"quartile_left_out": 0, "over_cap": 0}))
    lib = SimpleNamespace(shift_rules=lambda: {})
    ps = bp.Prestep(outcome="ran")
    sub = bp.Sub(intent="benchmark", text="employees", metrics=["employees"], band="$20-50M")
    block = bp._shift_notes(lib, ps, [sub], T0)
    assert [bool(x.get("carried")) for x in ps.shifts] == [False, False, True, True, True]
    assert "[computed on the earlier answer in this conversation]" in block
    assert "first six of these lines" in block
    # no carried lines: exactly as before
    ps2 = bp.Prestep(outcome="ran")
    assert "[computed on the earlier answer" not in bp._shift_notes(lib, ps2, [sub], None)


# --- the rewriter and the rule ------------------------------------------------------------------------------------

def test_rewriter_is_told_to_name_the_lines_the_earlier_answer_showed():
    low = agent.REWRITE_SYSTEM.lower()
    assert "those lines" in low and "name the specific lines" in low
    assert agent.REWRITE_MAX_TOKENS == 120          # unchanged
    block = agent._prior_block({"metrics": ["Operating expenses by function"], "band": "$20-$30M"})
    assert "specific line items the previous answer's tables showed" in block


def test_answer_rules_say_which_lines_to_lead_with_and_ban_no_change_claims():
    from linklib import answer_rules
    r = answer_rules.ANSWER_RULES
    assert "lead with the lines that have a computed" in r and "no computed change" in r
    assert "never call it flat, stable or unchanged" in r


def test_lint_flags_no_change_exists_claims_only_when_shifts_exist():
    from linklib.answer_lint import lint_answer
    bad = "No source in the archive prints a year-over-year rate of change by expense line. The archive does not contain a time-series of S&M."
    flags = [f["phrase"] for f in lint_answer(bad, shifts=T0)]
    assert "no change exists claim with computed shifts" in flags
    assert "no change exists claim with computed shifts" not in [f["phrase"] for f in lint_answer(bad, shifts=[])]
    ok = "R&D has no computed change. S&M is down 19 points."
    assert "no change exists claim with computed shifts" not in [f["phrase"] for f in lint_answer(ok, shifts=T0)]
    assert "line called flat or unchanged" in [f["phrase"] for f in lint_answer("G&A was flat across editions.", shifts=T0)]
    assert "line called flat or unchanged" not in [f["phrase"] for f in lint_answer("G&A was flat across editions.", shifts=[])]
