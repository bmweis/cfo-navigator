"""Generic "expense as a percent of revenue" questions, all-sizes rows, and Sales/Marketing
as two lines (follow-up to PRs 751 to 754, 2026-10-31).

Invented fixtures only ("Example Benchmarks", "Sample Survey Co", ...): no third-party
figures are committed. Shapes follow the MCP probe of production: an alias that names no
function and matches nothing stored, newer rows stored under function names, a 2026 report
with no size band split by financing segment, a report with a wide "<$100M" band, and a
report that prints Sales and Marketing as two categories."""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from figures_fixtures import frow, load_report  # noqa: E402

from linklib import benchmark_prestep as bp
from linklib import figures as fg
from linklib.answer_rules import ANSWER_RULES
from linklib.db import Library

REG = (
    "canonical_metric\tcard_url\tcontains\n"
    "Expenses as a % of Revenue\thttps://cards.example.test/e\tContains: expenses as a percent of revenue, "
    "expense as a percent of revenue, opex as a percent of revenue\n"
    "Total Employees\thttps://cards.example.test/h\tContains: total employees, headcount\n"
)
SETTINGS = {"max_library": 8, "source_chars": 1800, "global_chars": 16000, "max_feed": 5, "max_web": 4}
QUESTION = ("I run a company with $20-$30M in annual revenue. How has that changed over the past few years? "
            "It would be helpful to see total employees (full and part-time) and expense as a percent of revenue.")


def _pct(report, year, period, band, metric, denom, value, source="Example Benchmarks", basis="ARR", segment="All"):
    return frow(source=source, report=report, year=year, data_period=period, basis=basis, band=band, segment=segment,
                metric=metric, denominator=denom, value=value, unit="percent")


@pytest.fixture
def lib(tmp_path):
    db = Library(str(tmp_path / "t.db"))
    db.seed_voice_prompts()
    rows, err = fg.parse_registry_tsv(REG)
    assert not err
    db.seed_metric_registry(rows)
    load_report(db, "ssc-2023", [
        _pct("Sample Survey 2023", "2023", "CY-22", "$20M-$30M", m, "percent of revenue", "40",
             source="Sample Survey Co", basis="Revenue")
        for m in ("S&M % of Revenue", "R&D % of Revenue", "G&A % of Revenue")]
        + [frow(source="Sample Survey Co", report="Sample Survey 2023", year="2023", data_period="CY-22",
                basis="Revenue", band="$20M-$30M", metric="Total employees (full and part-time)", value="150",
                unit="count")])
    load_report(db, "eb-2025", [
        _pct("Example Benchmarks 2025", "2025", "CY-24", "$20M-$50M", m, "percent of GAAP revenue", v)
        for m, v in (("S&M expense", "35"), ("R&D expense", "24"), ("G&A expense", "14"))])
    load_report(db, "ej-2026", [
        _pct("Example Jurassic 2026", "2026", "CY-25", "all", "S&M spend", "percent of ARR", v,
             source="Example Jurassic", basis="none", segment=s)
        for s, v in (("Bootstrapped", "14"), ("Equity-backed", "27"))])
    load_report(db, "ei-2026", [
        _pct("Example Scaling 2026", "2026", "CY-25", b, "S&M spend", "percent of revenue", v, source="Example ICONIQ")
        for b, v in (("<$100M", "58.9"), ("$100M+", "31.4"))])
    load_report(db, "esc-2026", [
        _pct("Example Spending 2026", "2026", "CY-25", "$20M-$30M", m, "percent of ARR", "20", source="Example Capital Co")
        for m in ("Sales", "Marketing", "R&D", "G&A")])
    return db


def _plan(db, q=QUESTION):
    def _no_split():
        raise RuntimeError("no split call in this test")
    return bp.plan_turn(db, q, tier="standard", settings=SETTINGS, model="claude-sonnet-4-6",
                        remaining_usd=None, client_factory=_no_split)


def _body(ps):
    return "\n".join(d["body"] for s in ps.subs for d in s.docs)


def test_literal_question_reaches_every_function_family(lib):
    ps = _plan(lib)
    body = _body(ps)
    for name in ("S&M % of Revenue", "R&D % of Revenue", "G&A % of Revenue", "S&M expense", "R&D expense",
                 "G&A expense", "S&M spend"):
        assert name in body, name
    titles = [d["title"] for s in ps.subs for d in s.docs]
    assert any("Example Spending 2026" in t for t in titles) and any("Example Benchmarks 2025" in t for t in titles)
    notes = " ".join(n for s in ps.subs for n in s.notes)
    assert "No stored figure was found" not in notes


def test_headcount_is_not_displaced(lib):
    sig = bp.detect(lib, QUESTION)
    assert sig["terms"][0] == "total employees" and len(sig["terms"]) <= bp.MAX_SUBQUERIES
    assert "Total employees (full and part-time)" in _body(_plan(lib))


def test_generic_phrase_alone_is_a_family_request_without_a_registry_alias(tmp_path):
    db = Library(str(tmp_path / "n.db"))        # no registry rows at all
    load_report(db, "eb-2025", [_pct("Example Benchmarks 2025", "2025", "CY-24", "$20M-$50M", m,
                                     "percent of GAAP revenue", "30") for m in ("S&M expense", "R&D expense")])
    assert fg.detect_expense_families("What are operating expenses as a percent of revenue?") == [fg.GENERIC_EXPENSE_LABEL]
    assert fg.detect_expense_families("total operating expenses as a percent of revenue") == []
    assert fg.detect_expense_families("cost as a percent of revenue") == []
    res = fg.find_figures(db, fg.GENERIC_EXPENSE_LABEL)
    assert {r["metric"] for r in res["rows"]} == {"S&M expense", "R&D expense"}


def test_generic_phrase_does_not_catch_other_measures(lib):
    for q in ("S&M efficiency", "expense per employee", "revenue per employee", "cost of revenue"):
        assert fg.expense_family_request(q) == [], q


def test_sales_and_marketing_stay_two_lines(lib):
    res = fg.find_figures(lib, "S&M as a percent of ARR", band="$25M")
    esc = [r for r in res["rows"] if r["report_slug"] == "esc-2026"]
    assert {r["metric"] for r in esc} == {"Sales", "Marketing"}
    assert len(esc) == 2 and all(r["value"] == "20" for r in esc)
    assert "never add them into one s&m figure" in ANSWER_RULES.lower()


def test_all_sizes_rows_are_labelled_capped_and_after_the_exact_rows(lib):
    res = fg.find_figures(lib, fg.GENERIC_EXPENSE_LABEL, band="$25M")
    ctx = [r for r in res["rows"] if r["band_context"]]
    assert {r["report_slug"] for r in ctx} == {"ej-2026"}
    assert {r["segment"] for r in ctx} == {"Bootstrapped", "Equity-backed"}
    for r in ctx:
        assert r["match"] == "adjacent_band"       # an existing flag, not a new one
        assert any("band not stated or does not apply" in g for g in r["gaps"])
        assert any("not the size you asked" in g for g in r["gaps"])
    assert res["rows"][-1]["band_context"] and res["rows"][-len(ctx)]["band_context"]
    assert not any(r["band_context"] for r in res["rows"][:-len(ctx)])
    assert fg.MATCH_ORDER[1] == "adjacent_band" and "all_sizes" not in fg.MATCH_ORDER


def test_all_sizes_rows_are_capped_at_four_newest_edition_only(lib):
    for i in range(6):
        load_report(lib, f"extra-{i}", [
            _pct(f"Example Extra {i} 2026", "2026", "CY-25", "all", "S&M spend", "percent of ARR", str(10 + i),
                 source=f"Example Publisher {i}", basis="none")])
    load_report(lib, "extra-old", [_pct("Example Extra 0 2024", "2024", "CY-23", "all", "S&M spend",
                                        "percent of ARR", "9", source="Example Publisher 0", basis="none")])
    res = fg.find_figures(lib, "S&M spend", band="$25M")
    ctx = [r for r in res["rows"] if r["band_context"]]
    assert len(ctx) == fg.ALL_SIZES_CAP == 4
    assert all(r["report_year"] == "2026" for r in ctx)
    assert any("not sent (cap 4)" in n for n in res["notes"])


def test_no_size_asked_changes_nothing(lib):
    res = fg.find_figures(lib, fg.GENERIC_EXPENSE_LABEL)
    assert not any(r["band_context"] for r in res["rows"])
    assert {r["report_slug"] for r in res["rows"]} >= {"ej-2026", "ei-2026"}


def test_prestep_sends_the_context_rows_labelled(lib):
    body = _body(_plan(lib))
    assert "band not stated or does not apply" in body
    assert "Bootstrapped" in body and "Equity-backed" in body
    ps = _plan(lib)
    assert ps.subs[0].match_mix.get("exact", 0) >= 10


def test_answer_rules_state_the_newest_data_period_rule():
    low = ANSWER_RULES.lower()
    assert "only when something the user asked for is missing in every stored figure" in low
    assert "newest year found for each publisher" in low and "data period" in low
    assert "band not stated or does not apply" in low
    assert "two lines" in low
