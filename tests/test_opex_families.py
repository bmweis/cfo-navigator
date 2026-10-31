"""Opex by function: family detection on stored names and two-basis grouping
(follow-up to the 751 to 753 retest, 2026-10-31).

Invented fixtures only ("Example Benchmarks", "Sample Survey Co"): no third-party
figures are committed. Shapes follow what the MCP probe of production showed: old
rows named "S&M % of Revenue", newer rows named "S&M expense" with the basis in the
denominator column, an ARR-basis report that prints "Sales and marketing", and a
decoy measure that merely contains the same words."""
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
    "Expenses as a % of Revenue\thttps://cards.example.test/exp\tContains: expenses as a percent of revenue, "
    "opex as a percent of revenue, opex ratio\n"
    "Operating Expenses\thttps://cards.example.test/opex\tContains: opex, operating expenses\n"
)
SETTINGS = {"max_library": 8, "source_chars": 1800, "global_chars": 16000, "max_feed": 5, "max_web": 4}


@pytest.fixture
def lib(tmp_path):
    db = Library(str(tmp_path / "t.db"))
    db.seed_voice_prompts()
    rows, err = fg.parse_registry_tsv(REG)
    assert not err
    db.seed_metric_registry(rows)
    # Old edition, revenue basis, basis written in the metric name.
    load_report(db, "ssc-2023", [
        frow(source="Sample Survey Co", report="Sample Survey 2023", year="2023", data_period="CY-22",
             basis="Revenue", band="$20M-$30M", metric=m, denominator="percent of revenue", value="40", unit="percent")
        for m in ("S&M % of Revenue", "R&D % of Revenue", "G&A % of Revenue")])
    # Newest edition: short names, basis only in the denominator column, ARR bands.
    load_report(db, "eb-2025", [
        frow(report="Example Benchmarks 2025", year="2025", data_period="CY-24", basis="ARR", band="$20M-$50M",
             metric=m, denominator="percent of GAAP revenue", value=v, unit="percent")
        for m, v in (("S&M expense", "37"), ("R&D expense", "28"), ("G&A expense", "16"))])
    # ARR-basis report.
    load_report(db, "ec-2026", [
        frow(report="Example Capital 2026", year="2026", data_period="CY-25", basis="ARR", band="$20M-$30M",
             metric=m, denominator="percent of ARR", value="30", unit="percent")
        for m in ("Sales and marketing", "Research and development", "General and administrative")])
    # A row whose basis cannot be told.
    load_report(db, "ex-2024", [
        frow(report="Example Guide 2024", year="2024", basis="ARR", band="$20M-$30M", metric="S&M expense",
             denominator="", value="35", unit="percent", n="")])
    # Decoys: same words, different measure.
    load_report(db, "decoy-2025", [
        frow(report="Decoy Report 2025", year="2025", basis="ARR", band="$20M-$50M",
             metric="Marketing Expenses per $ of New Logo ARR", denominator="dollars of new logo ARR", value="1.2", unit="x"),
        frow(report="Decoy Report 2025", year="2025", basis="ARR", band="$20M-$50M",
             metric="S&M efficiency (magic number)", denominator="", value="0.8", unit="x"),
        frow(report="Decoy Report 2025", year="2025", basis="ARR", band="$20M-$50M",
             metric="S&M expense", denominator="US dollars", value="9000000", unit="USD"),
        frow(report="Decoy Report 2025", year="2025", basis="ARR", band="$20M-$50M",
             metric="S&M headcount as % of employees", denominator="percent of employees", value="22", unit="percent")])
    return db


def _metrics(res):
    return {r["metric"] for r in res["rows"]}


def test_short_name_resolves_to_its_family(lib):
    res = fg.find_figures(lib, "S&M as a percent of revenue", band="$25M")
    assert "S&M expense" in _metrics(res)
    assert {r["report_slug"] for r in res["rows"]} >= {"eb-2025", "ssc-2023", "ec-2026"}


def test_revenue_request_returns_arr_group_flagged_other_basis(lib):
    res = fg.find_figures(lib, "S&M as a percent of revenue", band="$25M")
    by = {r["report_slug"]: r for r in res["rows"]}
    assert by["ssc-2023"]["basis_group"] == "revenue" and not by["ssc-2023"]["other_basis"]
    assert by["eb-2025"]["basis_group"] == "revenue"
    assert by["ec-2026"]["basis_group"] == "arr" and by["ec-2026"]["other_basis"] is True
    assert any("percent of ARR" in g for g in by["ec-2026"]["gaps"])
    assert [g["basis"] for g in res["groups"]] == ["revenue", "arr", "unstated"]
    assert [g["asked"] for g in res["groups"]] == [True, False, False]
    assert any("other basis" in n.lower() for n in res["notes"])
    # asked basis first
    order = [r["basis_group"] for r in res["rows"]]
    assert order == sorted(order, key={"revenue": 0, "arr": 1, "unstated": 2}.get)


def test_arr_request_puts_arr_group_first(lib):
    res = fg.find_figures(lib, "S&M as a percent of ARR", band="$25M")
    assert res["rows"][0]["basis_group"] == "arr"
    assert [g["basis"] for g in res["groups"]][0] == "arr"


def test_unstated_basis_is_a_third_group(lib):
    res = fg.find_figures(lib, "S&M as a percent of revenue")
    un = [r for r in res["rows"] if r["report_slug"] == "ex-2024"]
    assert un and un[0]["basis_group"] == "unstated" and un[0]["other_basis"] is False


def test_no_row_is_converted_or_averaged(lib):
    res = fg.find_figures(lib, "S&M as a percent of revenue", band="$25M")
    vals = {(r["report_slug"], r["value"]) for r in res["rows"]}
    assert ("ec-2026", "30") in vals and ("eb-2025", "37") in vals and ("ssc-2023", "40") in vals
    assert all(r["unit"] == "percent" for r in res["rows"])
    assert all(r["denominator"] in ("percent of revenue", "percent of GAAP revenue", "percent of ARR", "")
               for r in res["rows"])
    assert fg.summarize(res["rows"])["refused"] is True      # bases differ, so no combined number


def test_decoys_are_not_pulled_into_the_family(lib):
    for q in ("S&M as a percent of revenue", "Sales and marketing expense", "S&M expense"):
        res = fg.find_figures(lib, q)
        assert not any(r["report_slug"] == "decoy-2025" for r in res["rows"]), q
    assert fg.expense_line_of("Marketing Expenses per $ of New Logo ARR", "percent of ARR") == ""
    assert fg.expense_line_of("S&M efficiency (magic number)", "") == ""
    assert fg.expense_line_of("S&M expense", "US dollars") == ""
    assert fg.expense_line_of("S&M headcount as % of employees", "percent of employees") == ""


def test_function_families_stay_apart(lib):
    res = fg.find_figures(lib, "R&D as a percent of revenue")
    assert _metrics(res) <= {"R&D % of Revenue", "R&D expense", "Research and development"}
    assert _metrics(res)


def test_opex_by_function_returns_all_functions(lib):
    res = fg.find_figures(lib, "operating expenses by function", band="$25M")
    ms = _metrics(res)
    assert {"S&M expense", "R&D expense", "G&A expense"} <= ms
    assert not any(r["report_slug"] == "decoy-2025" for r in res["rows"])


def test_older_name_overlap_does_not_outrank_newer_exact(lib):
    res = fg.find_figures(lib, "S&M as a percent of revenue", band="$25M")
    rev = [r for r in res["rows"] if r["basis_group"] == "revenue" and r["match"] == "exact"]
    years = [fg._year_num(r["report_year"]) for r in rev]
    assert years == sorted(years, reverse=True)
    assert rev[0]["report_year"] == "2025"
    # a related_metric row never sits ahead of an exact or adjacent row of its group
    ranks = [fg._MATCH_RANK[r["match"]] for r in rev]
    assert ranks == sorted(ranks)


def test_prestep_detects_the_family_and_lookup_sends_both_bases(lib):
    q = "How much do $20M-$30M companies spend on S&M as a percent of revenue? Give me the benchmark."
    sig = bp.detect(lib, q)
    assert sig["metrics"] and len(sig["metrics"]) == 1
    ps = bp.plan_turn(lib, q, tier="standard", settings=SETTINGS, model="claude-sonnet-4-6", remaining_usd=None)
    docs = [d for s in ps.subs for d in s.docs]
    body = "\n".join(d["body"] for d in docs)
    assert "S&M expense" in body and "Sales and marketing" in body
    titles = [d["title"] for d in docs]
    # asked basis first: revenue-basis reports, then the ARR one, then the unstated one
    assert titles.index("Example Capital 2026" if False else next(t for t in titles if "Example Capital" in t)) \
        > titles.index(next(t for t in titles if "Example Benchmarks 2025" in t))
    assert titles.index(next(t for t in titles if "Example Guide" in t)) == len(titles) - 1
    assert "OTHER BASIS" in body
    assert "Decoy Report" not in body


def test_answer_rules_state_the_two_basis_shape():
    low = ANSWER_RULES.lower()
    assert "other basis" in low and "never convert" in low
    assert "two small headers" in low or "basis column" in low
