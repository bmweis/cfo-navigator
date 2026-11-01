"""Org structure and spend profile (PR 759 brief, Parts A, B, D1 and E). Invented fixtures only:
publishers are fictional ("Example Benchmarks", "Sample Survey Co") and every figure is made up."""
import json
import pathlib
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import org_spend_fixtures as F  # noqa: E402

from linklib import agent, figures as fg  # noqa: E402
from linklib import benchmark_prestep as bp  # noqa: E402

pytestmark = pytest.mark.usefixtures("anthropic_env")


@pytest.fixture
def anthropic_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")


@pytest.fixture
def lib(tmp_path):
    return F.make_lib(tmp_path / "t.db")


def plan(db, q=F.QUESTION_35, payload=F.SPLIT_35):
    return bp.plan_turn(db, q, tier="standard", settings=F.SETTINGS, model="claude-sonnet-4-6",
                        remaining_usd=None, client_factory=F.fake_client(payload))


def lookups(ps):
    return [s for s in ps.subs if s.intent != "other"]


# --- Part A: one lookup per metric family ------------------------------------------

# The question names headcount and expenses by function only, so no shape label is involved.
Q_A = ("What is total headcount and what are operating expenses by function at $20-$30M ARR, and what "
       "should our hiring plan look like for the next two years given our sales-led enterprise motion?")


def test_a_two_names_for_total_headcount_are_one_part(lib):
    ps = plan(lib, Q_A)
    heads = [s for s in lookups(ps) if any(fg.name_key(m) == fg.name_key("Total employees") for m in s.metrics)]
    assert len(heads) == 1, [(s.metrics, s.band) for s in ps.subs]
    assert heads[0].merged == 1
    assert set(heads[0].metrics) >= {"total employees", "Headcount: Total"}
    assert heads[0].year == "all"
    assert ps.log()["subs"][0]["merged"] in (0, 1)
    assert any(x["merged"] == 1 for x in ps.log()["subs"])


def test_a_headcount_by_function_is_not_merged_with_total_headcount(lib):
    payload = {"subqueries": [
        F._part("benchmark", "Total employees", "Total employees at $20-$30M ARR"),
        F._part("benchmark", "Headcount: Sales", "Sales headcount at $20-$30M ARR")], "rest_of_question": ""}
    ps = plan(lib, "Total headcount and sales headcount at $20-$30M ARR, plus anything else on hiring plans for us?", payload)
    assert len(lookups(ps)) == 2


def test_a_different_basis_is_a_different_part(lib):
    payload = {"subqueries": [
        F._part("benchmark", "S&M % of Revenue", "S&M as a percent of revenue at $20-$30M ARR"),
        F._part("benchmark", "S&M % of ARR", "S&M as a percent of ARR at $20-$30M ARR")], "rest_of_question": ""}
    ps = plan(lib, "S&M as a percent of revenue and as a percent of ARR at $20-$30M ARR, and also what to hire next?", payload)
    assert len([s for s in ps.subs if s.intent != "other"]) >= 2


# --- Part A2/B: library hits across parts ------------------------------------------

class _Msgs:
    def __init__(self, split):
        self.split, self.captured = split, {}

    def create(self, **kw):
        if kw.get("max_tokens") == bp.SPLIT_MAX_TOKENS:
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(self.split))],
                                   usage=SimpleNamespace(input_tokens=300, output_tokens=200))
        self.captured.update(kw)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="x", citations=None)],
                               usage=SimpleNamespace(input_tokens=1, output_tokens=1, cache_creation_input_tokens=0,
                                                     cache_read_input_tokens=0), stop_reason="end_turn")


def run_turn(lib, monkeypatch, q=F.QUESTION_35, split=F.SPLIT_35):
    msgs = _Msgs(split)
    client = SimpleNamespace(messages=msgs)
    monkeypatch.setattr(agent, "_get_client", lambda: client)
    orig = bp.run_split
    monkeypatch.setattr(bp, "run_split", lambda qq, c, m, client_factory=None: orig(qq, c, m, client_factory=lambda: client))
    ans = agent.answer_question(lib, q, effort="standard", use_library=True, use_feed=False, use_web=False)
    assert not ans.failed, ans.error
    blocks = msgs.captured["messages"][-1]["content"]
    docs = [b for b in blocks if b.get("type") == "document"]
    return ans, [b for b in docs if "Stored benchmark figures" not in b["source"]["data"]], docs


def test_b_no_article_is_sent_twice_across_parts(lib, monkeypatch):
    F.add_articles(lib, n=60)
    ans, lib_docs, _ = run_turn(lib, monkeypatch)
    titles = [d["title"] for d in lib_docs]
    assert len(titles) == len(set(titles))


def test_b_a_part_with_figures_sends_half_the_articles_and_the_rest_part_all(lib, monkeypatch):
    F.add_articles(lib, n=60)
    ans, lib_docs, _ = run_turn(lib, monkeypatch)
    subs = ans.prestep["subs"]
    for s in subs:
        if s["intent"] != "other" and s["figure_rows"]:
            assert s["library_hits"] <= F.SETTINGS["max_library"] // 2
    other = [s for s in subs if s["intent"] == "other"][0]
    assert other["library_hits"] == F.SETTINGS["max_library"]
    assert len(lib_docs) <= 3 * (F.SETTINGS["max_library"] // 2) + F.SETTINGS["max_library"]
    assert any(s["library_hits_left_out"] for s in subs if s["intent"] != "other")


# --- Part D1: both bases survive the cap -------------------------------------------

def _rows(n_rev, n_arr):
    rows = []
    for i in range(n_rev):
        rows.append({"source": f"Pub {i % 5}", "basis_group": "revenue", "k": f"r{i}"})
    for i in range(n_arr):
        rows.append({"source": f"Pub {i % 5}", "basis_group": "arr", "k": f"a{i}"})
    return rows


def test_d1_the_arr_basis_keeps_a_minimum_when_revenue_rows_outnumber_it_ten_to_one():
    rows = _rows(100, 10)
    kept = bp.pick_rows(rows, 40)
    assert len(kept) == 40
    assert sum(1 for r in kept if r["basis_group"] == "arr") >= bp.BASIS_RESERVE
    assert sum(1 for r in kept if r["basis_group"] == "revenue") >= 40 - 10


def test_d1_a_basis_with_fewer_rows_than_the_reserve_keeps_them_all():
    kept = bp.pick_rows(_rows(100, 3), 40)
    assert sum(1 for r in kept if r["basis_group"] == "arr") == 3


def test_d1_a_row_cap_larger_than_the_rows_changes_nothing():
    rows = _rows(5, 2)
    assert bp.pick_rows(rows, 40) == rows


# --- Part E: org structure and spend profile are defined shapes --------------------

def test_e_phrases_pick_the_shape_terms_in_order():
    assert fg.detect_shape_terms("what does the org structure look like") == [fg.HEADCOUNT_FAMILY_LABEL]
    both = fg.detect_shape_terms("org structure and spend profile for us")
    assert both == [fg.HEADCOUNT_FAMILY_LABEL, fg.BY_FUNCTION_LABEL, fg.COGS_FAMILY_LABEL]
    assert fg.detect_shape_terms("what is our NRR") == []
    assert fg.detect_shape_terms("How much should S&M spend be at $20M ARR") == []     # bare "spend" is not a trigger


def test_e_the_literal_question_makes_headcount_opex_and_cost_of_revenue_parts(lib):
    sig = bp.detect(lib, F.QUESTION)
    assert sig["metrics"][0] == fg.HEADCOUNT_FAMILY_LABEL
    assert set(sig["terms"][:3]) == {fg.HEADCOUNT_FAMILY_LABEL, fg.BY_FUNCTION_LABEL, fg.COGS_FAMILY_LABEL}
    assert sig["needs_split"]
    payload = {"subqueries": [
        F._part("benchmark", "Total employees", "Headcount by function"),
        F._part("benchmark", "Operating expenses by function", "Opex by function"),
        F._part("benchmark", "Gross margin", "Cost of revenue and gross margin"),
        F._part("other", "", "the rest", band="", segment="", year="")], "rest_of_question": "the rest"}
    ps = plan(lib, F.QUESTION, payload)
    assert len(ps.subs) <= bp.MAX_SUBQUERIES
    metrics = [m for s in ps.subs for m in s.metrics]
    assert fg.HEADCOUNT_FAMILY_LABEL in metrics and fg.COGS_FAMILY_LABEL in metrics
    assert ps.subs[0].metrics == [fg.HEADCOUNT_FAMILY_LABEL]
    hc = ps.subs[0]
    body = "\n".join(d["body"] for d in hc.docs)
    for name in ("Headcount: Total", "Headcount: Engineering", "Headcount: Sales", "Headcount: G&A", "Headcount: Customer Success"):
        assert name in body, name


def _cogs_body(ps):
    sub = next(s for s in ps.subs if fg.COGS_FAMILY_LABEL in s.metrics)
    # Figures documents are merged across parts (one per report) and ride on one part, so read the turn's documents.
    return "\n".join(d["body"] for x in ps.subs for d in x.docs), sub


def test_e_cogs_printed_is_returned_as_printed(lib):
    ps = plan(lib, F.QUESTION)
    body, sub = _cogs_body(ps)
    assert "Cost of revenue % of revenue" in body and "Hosting % of revenue" in body and "Subscription COGS" in body
    assert "Gross Margin %" in body                       # gross margin is its own row


def test_e_only_gross_margin_printed_returns_gross_margin_and_no_computed_cogs(tmp_path):
    db = F.make_lib(tmp_path / "gm.db", cogs_printed=False)
    ps = plan(db, F.QUESTION)
    body, sub = _cogs_body(ps)
    assert "Gross Margin %" in body
    assert "Cost of revenue" not in body and "COGS" not in body
    assert "100" not in body.replace("$100", "")  # nothing like 100 minus gross margin appears in the figures
    assert not any(r.get("derived") for r in sub.candidates)


def test_e_neither_printed_returns_no_cogs_rows(tmp_path):
    db = F.make_lib(tmp_path / "none.db", cogs_printed=False, gm_printed=False)
    ps = plan(db, F.QUESTION)
    sub = next(s for s in ps.subs if fg.COGS_FAMILY_LABEL in s.metrics)
    assert sub.rows == 0


def test_e_both_printed_returns_both(tmp_path):
    db = F.make_lib(tmp_path / "both.db")
    body, _ = _cogs_body(plan(db, F.QUESTION))
    assert "Cost of revenue" in body and "Gross Margin" in body


def test_e_a_ratio_or_growth_name_is_not_in_the_headcount_family():
    assert not fg.headcount_family_row("ARR per FTE")
    assert not fg.headcount_family_row("Headcount growth")
    assert fg.headcount_family_row("Engineering % of headcount")
    assert not fg.cogs_family_row("COGS per customer", "", "USD")


# --- Part E4, D3, E2: the rules reach the prompt -----------------------------------

def test_e4_the_shape_rules_reach_the_lookup_notes_of_a_shape_turn(lib):
    ps = plan(lib, F.QUESTION, {"subqueries": [F._part("benchmark", "Total employees", "x"),
                                               F._part("benchmark", "Gross margin", "y")], "rest_of_question": ""})
    for phrase in ("ANSWER SHAPE", "Total row", "not printed", "never leave one out", "(c) What changed over time"):
        assert phrase in ps.plan_text, phrase


def test_e4_a_turn_without_the_shape_labels_gets_no_shape_rules(lib):
    ps = plan(lib, Q_A_SIMPLE, {"subqueries": [F._part("benchmark", "Total employees", "x"),
                                               F._part("benchmark", "Operating expenses by function", "y")],
                                "rest_of_question": "hiring"})
    assert "ANSWER SHAPE" not in ps.plan_text


Q_A_SIMPLE = "What is total headcount and what are operating expenses by function at $20-$30M ARR, and what should we hire next?"


def test_d3_and_e2_rules_are_on_every_turn():
    from linklib.answer_rules import ANSWER_RULES
    assert 'Say "exact band" only for a figure whose band is the band asked or sits inside it' in ANSWER_RULES
    assert "Never work one out from the other" in ANSWER_RULES
    assert "100 minus gross margin" in ANSWER_RULES
    assert 'write "not printed" for COGS' in ANSWER_RULES
    assert "Good: \"COGS: not printed." in ANSWER_RULES
