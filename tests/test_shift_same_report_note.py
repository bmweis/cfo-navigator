"""PR B (ask_questions id 85, 2026-11-20): a computed shift inside one report prints "same respondents and
definition" only when the stored rows support it. Endpoint shapes are the stored rows 1815 and 1803 of
benchmarkit-2023 (read_from TEXT with a blank n; read_from CHART with n=476; different notes; band basis ARR),
under an invented publisher ("Example Benchmarks"). No third-party report text is committed."""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from figures_fixtures import frow, load_report  # noqa: E402

from linklib import figures as fg
from linklib.db import Library

SM = "Sales and Marketing Expenses % of Revenue"
CLAIM = "same respondents and definition"


@pytest.fixture
def lib(tmp_path):
    db = Library(str(tmp_path / "t.db"))
    db.seed_voice_prompts()
    yield db
    db.close()


def _row(period, value, read_from, n, note, **kw):
    r = frow(report="Example Benchmarks 2023", year="2023", source="Example Benchmarks", basis="ARR", band="$20M-$50M",
             segment="", metric=SM, denominator="percent of revenue", statistic="median", value=value, unit="%",
             n=n, page="37", note=note, data_period=period, **kw)
    r["read_from"] = read_from
    return r


def _shift(lib, rows):
    load_report(lib, "ex-2023", rows)
    res = fg.find_figures(lib, SM, band="$20M-$50M", all_editions=True, recent_window=True, limit=500)
    out = fg.compute_shifts(res["rows"], lib.shift_rules())
    assert len(out["shifts"]) == 1, out
    return out


def _id85_rows():
    return [_row("2021", "54", "TEXT", "", "2021 value; 35% in 2022"),
            _row("2022", "35", "CHART", "476", "axis titled Company Size - Revenue")]


def test_text_endpoint_with_blank_n_and_a_chart_endpoint_gets_a_plain_note_not_the_claim(lib):
    out = _shift(lib, _id85_rows())
    line = out["shifts"][0]["line"]
    assert CLAIM not in line and "own history" not in line, line
    assert ("2021 figure stated in the report's text (no sample size); 2022 figure from its chart (n=476); "
            "band labelled ARR for 2021, revenue for 2022") in line, line
    assert "down 19 points" in line and "derived from stored reports" in line     # the line still prints, unchanged otherwise
    assert "wrong" not in line and "error" not in line


def test_a_large_one_year_move_is_recorded_as_an_outlier_and_the_line_still_prints(lib):
    out = _shift(lib, _id85_rows())
    assert fg.SHIFT_OUTLIER_POINTS_PER_YEAR == 15
    assert any(o.startswith("large move: Sales and Marketing Expenses % of Revenue at $20M-$50M") and "19 points over 1 year" in o
               for o in out["outliers"]), out["outliers"]


def test_control_pair_both_chart_with_n_and_a_small_move_keeps_the_old_wording(lib):
    out = _shift(lib, [_row("2021", "40", "CHART", "410", ""), _row("2022", "35", "CHART", "476", "")])
    assert CLAIM in out["shifts"][0]["line"], out["shifts"][0]["line"]
    assert not [o for o in out["outliers"] if o.startswith("large move")]


@pytest.mark.parametrize("a,b", [
    (_row("2021", "40", "TEXT", "410", ""), _row("2022", "35", "CHART", "476", "")),
    (_row("2021", "40", "CHART", "", ""), _row("2022", "35", "CHART", "476", "")),
    (_row("2021", "40", "CHART", "410", "note a"), _row("2022", "35", "CHART", "476", "note b")),
])
def test_each_trigger_alone_removes_the_claim(lib, a, b):
    assert CLAIM not in _shift(lib, [a, b])["shifts"][0]["line"]


def test_a_move_of_exactly_15_points_in_one_year_is_not_an_outlier(lib):
    out = _shift(lib, [_row("2021", "50", "CHART", "410", ""), _row("2022", "35", "CHART", "476", "")])
    assert not [o for o in out["outliers"] if o.startswith("large move")]
