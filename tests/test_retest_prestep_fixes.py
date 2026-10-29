"""Retest fixes, Part B (2026-10): the pre-step regression found by the retest.

Invented fixtures only ("Example Benchmarks", "Sample Survey Co"): no third-party
figures are committed. Each test names the retest symptom it pins down."""
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from figures_fixtures import frow, load_report  # noqa: E402

from linklib import benchmark_prestep as bp
from linklib import figures as fg
from linklib.db import Library

SETTINGS = {"max_library": 8, "source_chars": 1800, "global_chars": 16000, "max_feed": 5, "max_web": 4}

# Shaped like the real registry after metric-registry-seed-v3: the expense-ratio card gains
# "expense as a percent of revenue" style phrasings. Invented; the real file is Brian's.
REG = (
    "canonical_metric\tcard_url\tcontains\n"
    "Gross Margin\thttps://cards.example.test/gm\tContains: gm, gross margin %\n"
    "Expenses as a % of Revenue\thttps://cards.example.test/exp\tContains: expenses as a percent of revenue, "
    "expense as a percent of revenue, expense as a % of revenue, opex as a percent of revenue, opex ratio\n"
    "Operating Expenses\thttps://cards.example.test/opex\tContains: opex, operating expenses\n"
    "ARR per Head\thttps://cards.example.test/arrhead\tContains: arr per head, arr per fte\n"
)


@pytest.fixture
def lib(tmp_path):
    db = Library(str(tmp_path / "t.db"))
    db.seed_voice_prompts()
    rows, err = fg.parse_registry_tsv(REG)
    assert not err
    db.seed_metric_registry(rows)
    # Survey data with stated samples, at two sizes.
    load_report(db, "ex-2098", [
        frow(report="Example Benchmarks 2098", year="2098", band="$20M-$50M", metric="Gross Margin", value="79", unit="percent"),
        frow(report="Example Benchmarks 2098", year="2098", band="$20M-$50M", metric="Gross Margin %", value="81", unit="percent"),
        frow(report="Example Benchmarks 2098", year="2098", band="$20M-$50M", metric="Gross Margin % (LTM)", value="80", unit="percent"),
        frow(report="Example Benchmarks 2098", year="2098", band="$20M-$50M", metric="Total employees (full and part-time)", value="190", unit="count"),
        frow(report="Example Benchmarks 2098", year="2098", band="$20M-$50M", metric="S&M % of Revenue", value="38", unit="percent"),
        frow(report="Example Benchmarks 2098", year="2098", band="$20M-$50M", metric="R&D % of Revenue", value="22", unit="percent"),
        frow(report="Example Benchmarks 2098", year="2098", band="$20M-$50M", metric="G&A % of Revenue", value="13", unit="percent"),
    ])
    # A planning guide dated a year later: an illustrative company, no sample size.
    load_report(db, "guide-2099", [
        frow(source="Sample Planning Guide", report="Sample Planning Guide 2099 Edition", year="2099", n="",
             band="$20M-$30M", segment="Illustrative company scaling to $20M ARR", metric="Gross Margin",
             value="68", unit="percent", data_period="Illustrative"),
        frow(source="Sample Planning Guide", report="Sample Planning Guide 2099 Edition", year="2099", n="",
             band="$20M-$30M", metric="gross margin (example input)", value="70", unit="percent"),
        frow(source="Sample Planning Guide", report="Sample Planning Guide 2099 Edition", year="2099", n="",
             band="$20M-$30M", metric="Gross Margin", value="72", unit="percent", note="worked example"),
    ])
    yield db
    db.close()


class NoSplit:
    """A splitter client that fails the test if it is ever called."""
    def __call__(self):
        raise AssertionError("the splitter must not run for a one-metric question")


def _plan(lib, q, **k):
    return bp.plan_turn(lib, q, tier="standard", settings=SETTINGS, model="claude-sonnet-4-6",
                        remaining_usd=5.0, client_factory=k.pop("client_factory", NoSplit()), **k)


# --- 3 and 6: one metric, many names ---------------------------------------------------

def test_gross_margin_names_are_one_metric_and_make_no_splitter_call(lib):
    q = "What are typical gross margins for SaaS?"
    sig = bp.detect(lib, q)
    assert sig["n_metrics"] == 1
    assert "more than one metric" not in sig["split_reasons"] and not sig["needs_split"]
    ps = _plan(lib, q)
    assert ps.active and not ps.split_ran and ps.outcome == "ran"
    sub = ps.subs[0]
    assert sub.rows >= 3 and sub.match_mix.get("exact", 0) == sub.rows
    assert not any("no exact" in n.lower() for n in sub.notes)
    # nothing from the illustrative planning guide reached the model
    assert all("Sample Planning Guide" not in d["title"] for d in sub.docs)
    assert sub.illustrative_left_out == 3     # counted once each, however many names looked them up


def test_two_real_metrics_still_split(lib):
    assert bp.detect(lib, "What is typical gross margin and total employees at $25M ARR?")["needs_split"]


def test_distinct_metric_count_unit():
    found = [{"canonical": "Gross Margin", "term": "gross margin", "match": "alias"}]
    assert bp.distinct_metric_count(found, ["Gross Margin %", "Gross Margin % (LTM)", "gross margin (example input)"]) == 1
    assert bp.distinct_metric_count(found, ["Total employees"]) == 2
    # one ambiguous phrase naming two metrics is still one ask
    amb = [{"canonical": "A", "term": "x", "match": "ambiguous_alias"}, {"canonical": "B", "term": "x", "match": "ambiguous_alias"}]
    assert bp.distinct_metric_count(amb, []) == 1


# --- 4: generic segments ---------------------------------------------------------------

@pytest.mark.parametrize("seg", ["SaaS", "B2B SaaS", "software", "company", "SaaS company", "all", "B2B"])
def test_generic_segments_are_dropped(lib, seg):
    assert fg.usable_segment(lib, seg) == ""


def test_a_segment_the_figures_print_is_kept_and_an_unprinted_one_is_dropped(lib):
    load_report(lib, "ent", [frow(report="Enterprise Report 2099", year="2099", source="Ent Co",
                                   segment="Enterprise", metric="Burn multiple", value="1.3", unit="x")])
    assert fg.usable_segment(lib, "enterprise") == "enterprise"
    assert fg.usable_segment(lib, "Fintech") == ""


def test_a_saas_segment_from_the_splitter_does_not_turn_every_row_into_a_segment_mismatch(lib):
    sub = bp.Sub(intent="benchmark", text="q", metrics=["Gross Margin"], band="$25M", segment="SaaS")
    bp.lookup(lib, sub, "standard", {r["report_slug"]: r for r in lib.list_benchmark_reports()})
    assert sub.segment_dropped == "SaaS"
    assert sub.rows and set(sub.match_mix) <= {"exact", "adjacent_band"}


# --- 5: illustrative rows --------------------------------------------------------------

def test_illustrative_rows_are_excluded_by_default_and_reported(lib):
    res = fg.find_figures(lib, "Gross Margin")
    assert res["illustrative_left_out"] == 3          # the "(example input)" name is a variant of Gross Margin and is found with it
    assert all("Planning Guide" not in r["report"] for r in res["rows"])
    assert any("3 illustrative rows left out" in n for n in res["notes"])


def test_include_illustrative_returns_them(lib):
    res = fg.find_figures(lib, "Gross Margin", include_illustrative=True, all_editions=True)
    assert res["illustrative_left_out"] == 0
    assert any("Planning Guide" in r["report"] for r in res["rows"])


def test_a_report_name_containing_example_is_not_illustrative(lib):
    # "Example Benchmarks" is the publisher's name; only the data fields are checked.
    assert not fg.is_illustrative(frow(metric="Gross Margin"))
    assert fg.is_illustrative(frow(metric="gross margin (example input)"))
    assert fg.is_illustrative(frow(segment="Hypothetical company"))
    assert fg.is_illustrative(frow(data_period="Illustrative"))
    assert fg.is_illustrative(frow(note="sample company"))


def test_a_newer_guide_without_a_sample_cannot_outrank_survey_data_by_year(lib):
    res = fg.find_figures(lib, "Gross Margin", include_illustrative=True, all_editions=True)
    firsts = [r for r in res["rows"] if r["match"] == "exact"]
    assert firsts[0]["report"].startswith("Example Benchmarks")
    # and the pre-step's own ordering agrees
    sub = bp.Sub(intent="benchmark", text="q", metrics=["Gross Margin"])
    bp.lookup(lib, sub, "standard", {r["report_slug"]: r for r in lib.list_benchmark_reports()}, include_illustrative=True)
    titles = [d["title"] for d in sub.docs]
    assert titles[0].startswith("Figures: Example Benchmarks") and any("Planning Guide" in t for t in titles[1:])


# --- 1: why prestep_json was empty ----------------------------------------------------

ORG_Q = ("We are at $20-$30M in annual revenue. How should we structure the finance and go-to-market org, "
         "and what should we spend on headcount, sales and marketing, and R&D?")


def test_headcount_and_expense_lines_reach_the_stored_names(lib):
    sig = bp.detect(lib, ORG_Q)
    names = set(sig["terms"])
    assert "Total employees (full and part-time)" in names
    assert "S&M % of Revenue" in names and "R&D % of Revenue" in names
    assert sig["via_fts"]


def test_size_fields_and_unrelated_words_are_still_not_metrics(lib):
    load_report(lib, "sizes", [frow(report="Size Report 2099", year="2099", source="Size Co",
                                    metric="Annual R&D Spend", value="6.1", unit="dollars"),
                               frow(report="Size Report 2099", year="2099", source="Size Co",
                                    metric="Annual Revenue ($M)", value="31", unit="dollars")])
    names = " | ".join(bp.detect(lib, ORG_Q)["terms"]).lower()
    assert "annual r&d spend" not in names and "annual revenue" not in names


def test_every_turn_records_an_outcome(lib):
    ps = bp.plan_turn(lib, "How should I run quarterly planning?", tier="standard", settings=SETTINGS,
                      model="m", remaining_usd=None)
    assert not ps.active and ps.outcome.startswith("skipped")
    assert ps.log() == {"outcome": "skipped: no benchmark metric detected"}


def test_the_agent_records_skipped_error_and_library_off(lib, monkeypatch):
    import linklib.agent as agent
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    skipped = agent.answer_question(lib, "How should I run quarterly planning?", use_feed=False)
    assert skipped.prestep["outcome"].startswith("skipped: no benchmark")
    off = agent.answer_question(lib, "What is typical gross margin?", use_library=False, use_feed=False)
    assert off.prestep["outcome"] == "skipped: library source is off"

    def boom(*a, **k):
        raise KeyError("x")
    monkeypatch.setattr(bp, "plan_turn", boom)
    err = agent.answer_question(lib, "What is typical gross margin?", use_feed=False)
    assert err.prestep["outcome"] == "error: KeyError"
    assert json.dumps(err.prestep)   # serialisable, so it is stored


# --- 2: aliases (v3-shaped invented registry) -----------------------------------------

@pytest.mark.parametrize("phrase,expected", [
    ("expense as a percent of revenue", ["Expenses as a % of Revenue"]),
    ("opex as a percent of revenue", ["Expenses as a % of Revenue"]),        # the longest alias wins, not "opex"
    ("opex", ["Operating Expenses"]),
])
def test_longest_alias_wins(lib, phrase, expected):
    got = [d["canonical"] for d in fg.detect_metrics(lib, f"What is {phrase} at $25M ARR?")]
    assert got == expected


@pytest.mark.parametrize("phrase", ["total employees", "number of employees", "headcount", "FTEs"])
def test_headcount_words_resolve_through_stored_names_not_to_arr_per_head(lib, phrase):
    q = f"What is the typical {phrase} at $25M ARR?"
    assert "ARR per Head" not in [d["canonical"] for d in fg.detect_metrics(lib, q)]
    assert "Total employees (full and part-time)" in bp.detect(lib, q)["terms"]
