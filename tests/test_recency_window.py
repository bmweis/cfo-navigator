"""Phase 2 (2026-11-27): the recency window. A plain lookup shows the newest period plus one year back; history
questions, a named year and unreadable periods are untouched. Periods are the real strings seen in production;
publishers and figures are invented."""
import datetime
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from linklib import benchmark_prestep as bp  # noqa: E402
from linklib import figures as fg  # noqa: E402
from linklib.db import Library  # noqa: E402
from tests import figures_fixtures as fx  # noqa: E402

M = "ARR growth rate"


@pytest.mark.parametrize("period,year", [
    ("Q2 2024 to Q2 2025", 2025), ("Q2 2020 to Q2 2021", 2021), ("CY-24", 2024), ("FY2025", 2025),
    ("H1 2024 end", 2024), ("2026E", 2026), ("Pre-2020", 2020), ("2025 H1", 2025), ("1H2025", 2025),
    ("12 months ending Mar 2023", 2023), ("2010-1H21", 2021), ("2026 (through Q2)", 2026),
    ("2019 start", 2019), ("2023", 2023), ("", None), ("LTM", None), ("n/a", None)])
def test_period_end_year(period, year):
    assert fg.period_end_year(period) == year


def test_year_num_is_unchanged():
    assert fg._year_num("Q2 2024 to Q2 2025") == 2024      # the first year; callers depend on it


@pytest.fixture
def lib(tmp_path):
    return Library(str(tmp_path / "lib.db"))


def seed(lib):
    fx.seed_registry(lib)
    plan = {("ha-2025", "Alpha", "2025"): [("Q2 2024 to Q2 2025", "20"), ("Q2 2023 to Q2 2024", "18"),
                                          ("Q2 2022 to Q2 2023", "17"), ("Q2 2020 to Q2 2021", "40")],
            ("jc-2026", "Jurassic", "2026"): [("FY2025", "21"), ("FY2024", "19")],
            ("bm-2026", "Bench", "2026"): [("CY-24", "22")], ("cm-2025", "Chart", "2025"): [("H1 2024", "23")],
            ("kb-2026", "Key", "2026"): [("2026E", "25")],
            ("bs-2021", "Bessemer", "2021"): [("2021", "115")], ("em-2023", "Emergence", "2023"): [("2023", "30")]}
    for (slug, src, yr), pts in plan.items():
        fx.load_report(lib, slug, [fx.frow(metric=M, band="$20M-$50M", denominator="percent year over year",
                                           data_period=p, value=v, unit="%", source=src, report=f"{src} {yr}", year=yr)
                                   for p, v in pts])


def periods(res):
    return {r["data_period"] for r in res["rows"]}


def test_plain_lookup_keeps_current_period_plus_one_year(lib):
    seed(lib)
    res = fg.find_figures(lib, M, band="$20M-$50M", period_window=True)
    got = periods(res)
    assert "Q2 2024 to Q2 2025" in got and "Q2 2023 to Q2 2024" in got and "FY2025" in got and "FY2024" in got
    assert "CY-24" in got and "H1 2024" in got and "2026E" in got            # estimate of the newest edition kept
    assert not got & {"Q2 2022 to Q2 2023", "Q2 2020 to Q2 2021", "2021", "2023"}
    assert res["period_window"]["anchor"] == 2025 and res["period_window"]["kept_from"] == 2024
    assert res["period_window"]["left_out"] == 4 and not res["period_window"]["widened"]


def test_window_is_off_unless_asked_for(lib):
    seed(lib)
    res = fg.find_figures(lib, M, band="$20M-$50M")
    assert "2021" in periods(res) and res["period_window"] is None


def test_history_and_named_year_and_all_editions_are_untouched(lib):
    seed(lib)
    assert "2021" in periods(fg.find_figures(lib, M, band="$20M-$50M", period_window=True, all_editions=True))
    assert "2021" in periods(fg.find_figures(lib, M, band="$20M-$50M", period_window=True, year="2021"))


def test_estimate_does_not_set_the_anchor(lib):
    seed(lib)
    res = fg.find_figures(lib, M, band="$20M-$50M", period_window=True)
    assert res["period_window"]["anchor"] == 2025            # 2026E is an estimate


def test_unparseable_periods_are_kept(lib):
    seed(lib)
    fx.load_report(lib, "odd", [fx.frow(metric=M, band="$20M-$50M", denominator="percent year over year",
                                        data_period="n/a", value="12", unit="%", source="Odd", report="Odd 2020", year="2020")])
    assert "n/a" in periods(fg.find_figures(lib, M, band="$20M-$50M", period_window=True))


def test_widens_once_when_fewer_than_three_reports(lib):
    fx.seed_registry(lib)
    for slug, per in [("a", "FY2025"), ("b", "FY2025"), ("c", "FY2024"), ("d", "FY2023"), ("e", "FY2021")]:
        fx.load_report(lib, slug, [fx.frow(metric=M, band="$20M-$50M", denominator="percent year over year", data_period=per,
                                           value="20", unit="%", source=slug, report=slug.upper() + " 2025", year="2025")])
    pw = fg.find_figures(lib, M, band="$20M-$50M", period_window=True)["period_window"]
    assert pw["widened"] is False and pw["left_out"] == 2      # 3 reports in 2024-2025
    # only two reports inside the window -> widens by one year, once
    lib2 = lib
    fx.seed_registry(lib2)
    for slug, per in [("f", "FY2025"), ("g", "FY2024"), ("h", "FY2023"), ("i", "FY2022")]:
        fx.load_report(lib2, slug, [fx.frow(metric="Gross margin", band="$20M-$50M", denominator="revenue", data_period=per,
                                            value="70", unit="%", source=slug, report=slug.upper() + " 2025", year="2025")])
    pw2 = fg.find_figures(lib2, "Gross margin", band="$20M-$50M", period_window=True)["period_window"]
    assert pw2["widened"] is True and pw2["kept_from"] == 2023 and pw2["left_out"] == 1


def test_no_readable_actual_year_means_no_window():
    rows = [{"data_period": "2026E", "report_slug": "a", "metric": "x"}, {"data_period": "n/a", "report_slug": "b", "metric": "x"}]
    assert fg.apply_period_window(rows) == (rows, None)


def test_the_window_runs_before_the_cap(lib):
    """The newest rows are not crowded out: with the window on, none of the old rows reach the candidates."""
    seed(lib)
    sub = bp.Sub(intent="benchmark", text="What is typical ARR growth at $20M-$50M?", metrics=[M], band="$20M-$50M")
    bp.gather(lib, sub)
    assert sub.window_left_out == 4 and sub.window_from == 2024
    assert not {r["data_period"] for r in sub.candidates} & {"2021", "Q2 2020 to Q2 2021"}
    assert bp.older_data_clause([sub]) == "Older data (before 2024) left out: 4 figures."


def test_history_question_turns_the_window_off(lib):
    seed(lib)
    sub = bp.Sub(intent="benchmark", text="How has ARR growth changed?", metrics=[M], band="$20M-$50M", year="all")
    bp.gather(lib, sub, over_time=True)
    assert sub.window_left_out == 0 and bp.older_data_clause([sub]) == ""
    assert "Q2 2020 to Q2 2021" in {r["data_period"] for r in sub.candidates}


@pytest.mark.parametrize("q", [
    "How has ARR growth changed?", "How have retention rates moved", "What was NRR last year?", "NRR in the prior year",
    "ARR growth a year ago", "NRR compared to 2023", "NRR vs 2022", "NRR versus 2022", "back in 2021 what was growth",
    "What was NRR in 2023?", "growth used to be higher", "the trend in NRR", "NRR since 2020", "NRR 3 years ago"])
def test_history_phrases_are_over_time(q):
    assert bp.asks_about_past(q), q


@pytest.mark.parametrize("q", [
    "What is typical ARR growth for SaaS at $20M-$50M?", "What is NRR used to measure?",
    f"What is NRR in {datetime.date.today().year}?", "NRR at $20-30M ARR", "median gross margin"])
def test_plain_questions_are_not(q):
    assert not bp.asks_about_past(q), q


def test_history_of_a_metric_still_counts_as_history():
    # Chosen: "history" asks about the past even in a definition-shaped sentence (showing more is the safe error).
    assert bp.asks_about_past("what is the history of the SaaS metric")
