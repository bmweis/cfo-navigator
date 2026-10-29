"""Part E of the Buddy follow-up (2026-10-28): why the right rows did not reach the answer.

Invented data only ("Example Benchmarks 2099"). The shape reproduced here: a question
that carries the asker's own size ("$20-$30M in annual revenue"), a ratio denominator
("as a percent of revenue") and a broader ask (org structure) was detected as the
metrics "Annual Revenue ($M)", "Annual R&D Spend" and "Revenue in Year 1", the band was
read as "$30M", less-close rows filled the figures block, and the broader half was never
searched for articles."""
import json
import sys
import pathlib

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import agent, benchmark_prestep as bp, figures as fg
from linklib.db import Library
from figures_fixtures import frow, load_report
import figures_fixtures as fx
import test_benchmark_prestep as tbp

SETTINGS = {"max_library": 8, "source_chars": 1800, "global_chars": 16000, "max_feed": 5, "max_web": 4}
Q = ("We are a B2B SaaS company with $20-$30M in annual revenue, mid-market/enterprise, sales-led. "
     "What is a typical ARR per employee and total employees (full and part-time), and what do we spend on "
     "S&M, R&D and G&A as a percent of revenue? And how should we structure the org?")

REG = ("canonical_metric\tcard_url\tcontains\n"
       "ARR per employee\thttps://cards.example.test/arrfte\tContains: ARR per FTE, revenue per employee\n"
       "Revenue\thttps://cards.example.test/rev\tContains: revenue, annual revenue\n")


@pytest.fixture
def lib(tmp_path):
    db = Library(str(tmp_path / "t.db"))
    db.seed_voice_prompts()
    rows, err = fg.parse_registry_tsv(REG)
    assert not err
    db.seed_metric_registry(rows)
    load_report(db, "ex-2098", [
        frow(report="Example Benchmarks 2098", year="2098", band="$20M-$50M", metric="ARR per employee", value="150000"),
        frow(report="Example Benchmarks 2098", year="2098", band="$20M-$50M", metric="Annual Revenue ($M)", value="31"),
        frow(report="Example Benchmarks 2098", year="2098", band="$20M-$50M", metric="Revenue in Year 1", value="1.2"),
        frow(report="Example Benchmarks 2098", year="2098", band="$20M-$50M", metric="Annual R&D Spend", value="6.1"),
        frow(report="Example Benchmarks 2098", year="2098", band="$20M-$50M",
             metric="Total employees (full and part-time)", value="190"),
    ])
    load_report(db, "jc-2099", [
        frow(source="Jurassic Example", report="Jurassic Example 2099", year="2099", band="$20M-$50M",
             metric="ARR per employee", value="181905"),
    ])
    yield db
    db.close()


# --- 1. detection --------------------------------------------------------------------

def test_the_size_phrase_and_the_spend_word_are_not_metrics(lib):
    sig = bp.detect(lib, Q)
    names = " | ".join(sig["terms"]).lower()
    assert "annual revenue" not in names and "year 1" not in names and "r&d spend" not in names
    assert "arr per employee" in sig["terms"]
    assert "Total employees (full and part-time)" in sig["terms"]
    assert "Revenue" not in sig["metrics"]


def test_total_employees_without_its_qualifier_finds_the_stored_name(lib):
    sig = bp.detect(lib, "What is the median total employees for a company at $25M ARR?")
    assert "Total employees (full and part-time)" in sig["terms"]


def test_a_metric_word_outside_a_size_or_denominator_phrase_still_counts(lib):
    assert "Revenue" in bp.detect(lib, "What is the median revenue for peers?")["metrics"]
    assert "Revenue" not in bp.detect(lib, "What do peers spend on marketing as a percent of revenue?")["metrics"]
    assert "Revenue" not in bp.detect(lib, "What do peers spend with $20-$30M in annual revenue?")["metrics"]


@pytest.mark.parametrize("text,band", [
    ("$20-$30M in annual revenue", "$20-$30M"), ("a $20M-$30M company", "$20M-$30M"),
    ("between $5M to $25M", "$5M to $25M"), ("at $25M ARR", "$25M"), ("over $50M", "over $50M"),
])
def test_bands_are_read_whole(lib, text, band):
    assert bp.detect(lib, "median " + text + " ARR per employee")["bands"][0].lower().startswith(band.lower())


def test_the_band_of_the_single_lookup_is_the_whole_range(lib):
    ps = bp.plan_turn(lib, "Median ARR per employee for a $20-$30M company?", tier="standard",
                      settings=SETTINGS, model="claude-sonnet-4-6", remaining_usd=None)
    assert ps.subs[0].band == "$20-$30M"


def test_words_far_apart_do_not_make_a_stored_name(lib):
    q = "annual planning for the team, and later what we spend"
    assert "Annual R&D Spend" not in fg.detect_stored_metric_names(lib, q)


def test_a_stored_name_made_of_size_words_alone_is_not_detected(lib):
    assert fg.detect_stored_metric_names(lib, "annual revenue in year one spend") == []


# --- 3/4. noise and ordering ---------------------------------------------------------

def _lookup(lib, metrics, band="$25M", year="", over_time=False):
    sub = bp.Sub(intent="benchmark", text="q", metrics=metrics, band=band, year=year)
    bp.lookup(lib, sub, "standard", {r["report_slug"]: r for r in lib.list_benchmark_reports()}, over_time=over_time)
    return sub


def test_less_close_rows_are_not_sent_when_a_closer_one_exists(lib):
    load_report(lib, "noise", [
        frow(report="Noise Report 2099", year="2099", source="Noise Co", band="$20M-$50M", segment="Consumer",
             metric="ARR per employee", value=str(100 + i), statistic=f"stat{i}") for i in range(30)])
    # "Enterprise" is a segment the figures print (a generic word like "B2B" or "All" is dropped).
    load_report(lib, "ent", [
        frow(report="Enterprise Report 2099", year="2099", source="Ent Co", band="$20M-$50M", segment="Enterprise",
             metric="ARR per employee", value="170000")])
    sub = bp.Sub(intent="benchmark", text="q", metrics=["ARR per employee"], band="$25M", segment="Enterprise")
    bp.lookup(lib, sub, "standard", {r["report_slug"]: r for r in lib.list_benchmark_reports()})
    assert set(sub.match_mix) <= {"exact", "adjacent_band"} and sub.rows == sum(sub.match_mix.values()) > 0
    assert all("Noise" not in d["title"] for d in sub.docs)
    assert any("less-close" in n for n in sub.notes)


def test_with_no_close_row_at_most_the_cap_is_sent_and_the_note_says_so(lib):
    load_report(lib, "far", [
        frow(report="Far Report 2099", year="2099", source="Far Co", band="$20M-$50M", segment="Consumer",
             metric="Net revenue retention", value=str(100 + i), statistic=f"stat{i}") for i in range(40)])
    load_report(lib, "ent", [
        frow(report="Enterprise Report 2099", year="2099", source="Ent Co", band="$20M-$50M", segment="Enterprise",
             metric="Burn multiple", value="1.3")])
    sub = bp.Sub(intent="benchmark", text="q", metrics=["Net revenue retention"], band="$25M", segment="Enterprise")
    bp.lookup(lib, sub, "standard", {r["report_slug"]: r for r in lib.list_benchmark_reports()})
    assert 0 < sub.rows <= bp.RELATED_CAP
    assert not (set(sub.match_mix) & set(bp.CLOSE_MATCHES))
    assert any("no exact or adjacent-band match" in n for n in sub.notes)


def test_a_newer_bracketing_row_is_not_crowded_out_by_older_rows(lib):
    load_report(lib, "old-many", [
        frow(report="Old Report 2090", year="2090", source="Old Co", band="$20M-$50M", metric="Old metric",
             value=str(i), statistic=f"statistic number {i}") for i in range(120)])
    load_report(lib, "new-one", [
        frow(report="New Report 2099", year="2099", source="New Co", band="$20M-$50M", metric="New metric", value="7")])
    sub = _lookup(lib, ["Old metric", "New metric"], band="$25M")
    titles = [d["title"] for d in sub.docs]
    assert titles and titles[0] == "Figures: New Report 2099 (2099)"


# --- 2. the report row ---------------------------------------------------------------

def test_the_admin_report_row_shows_what_each_part_searched(lib):
    from webapp.app import _prestep_report_html
    ps = bp.plan_turn(lib, "Median ARR per employee for a $20-$30M company?", tier="standard",
                      settings=SETTINGS, model="claude-sonnet-4-6", remaining_usd=None)
    html = _prestep_report_html({"prestep_json": json.dumps(ps.log()), "prestep_cost_usd": 0})
    assert "searched: Median ARR per employee for a $20-$30M company?" in html
    assert "exact" in html and "figure rows (" in html


# --- the broader half of the question ------------------------------------------------

@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    monkeypatch.setattr(agent, "_client", None)
    monkeypatch.setattr("linklib.db.Library.vector_search_available", lambda self: False)


def _two_benchmark_parts():
    return json.dumps({"subqueries": [
        {"intent": "benchmark", "metric": "ARR per employee", "band": "$20-$30M", "segment": "", "year": "",
         "text": "median ARR per employee at $25M"},
        {"intent": "benchmark", "metric": "Total employees (full and part-time)", "band": "$20-$30M", "segment": "",
         "year": "", "text": "total employees at $25M"}],
        "rest_of_question": "How should we structure the org?"})


def test_the_rest_of_the_question_is_searched_for_articles(lib, monkeypatch):
    fx.add_digest(lib, "org-1", text="How to structure the org: functions, spans and layers at a mid-size SaaS company.",
                  title="Org structure digest")
    c = tbp.FakeClient(split_text=_two_benchmark_parts())
    monkeypatch.setattr(agent, "_get_client", lambda: c)
    ans = agent.answer_question(lib, Q, use_feed=False)
    docs = [b["title"] for b in c.answer_calls()[0]["messages"][-1]["content"] if b.get("type") == "document"]
    assert any("Org structure digest" in t for t in docs), docs
    assert [s["intent"] for s in ans.prestep["subs"]][-1] == "other"


def test_figures_never_displace_the_articles(lib, monkeypatch):
    """Separate budgets: even with every figures part at its full character budget the
    articles retrieved for the broader half are all still sent."""
    for i in range(6):
        fx.add_digest(lib, f"org-{i}", text=f"How to structure the org part {i}: functions, spans and layers.",
                      title=f"Org structure digest {i}")
    c = tbp.FakeClient(split_text=_two_benchmark_parts())
    monkeypatch.setattr(agent, "_get_client", lambda: c)
    agent.answer_question(lib, Q, use_feed=False)
    docs = [b["title"] for b in c.answer_calls()[0]["messages"][-1]["content"] if b.get("type") == "document"]
    assert sum(1 for t in docs if t.startswith("Org structure digest")) == 6
    assert any(t.startswith("Figures:") for t in docs)


# --- the fourth slot is reserved for the rest of the question ------------------------

def _four_benchmark_parts():
    subs = [{"intent": "benchmark", "metric": m, "band": "$20-$30M", "segment": "", "year": "", "text": f"{m} at $25M"}
            for m in ("ARR per employee", "Total employees (full and part-time)", "Net revenue retention", "Gross margin")]
    return json.dumps({"subqueries": subs, "rest_of_question": "How should we structure the org?"})


def test_four_benchmark_parts_become_three_plus_other_and_the_other_is_searched(lib, monkeypatch):
    fx.add_digest(lib, "org-1", text="How to structure the org: functions, spans and layers at a mid-size SaaS company.",
                  title="Org structure digest")
    c = tbp.FakeClient(split_text=_four_benchmark_parts())
    monkeypatch.setattr(agent, "_get_client", lambda: c)
    ans = agent.answer_question(lib, Q, use_feed=False)
    intents = [s["intent"] for s in ans.prestep["subs"]]
    assert intents == ["benchmark", "benchmark", "benchmark", "other"], intents
    assert len(intents) <= bp.MAX_SUBQUERIES
    other = ans.prestep["subs"][-1]
    assert "structure the org" in other["text"] and "Gross margin" in other["text"]   # nothing dropped
    docs = [b["title"] for b in c.answer_calls()[0]["messages"][-1]["content"] if b.get("type") == "document"]
    assert any("Org structure digest" in t for t in docs), docs


def test_reserve_other_slot_leaves_three_or_fewer_parts_alone():
    subs = [bp.Sub(intent="benchmark", text="a", metrics=["a"]) for _ in range(3)]
    assert bp._reserve_other_slot(subs, "rest") is subs
