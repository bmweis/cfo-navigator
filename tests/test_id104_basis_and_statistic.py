"""Id 104 follow-up (2026-11-26): band basis, statistic intent, growth rows in the table.
Fixture shaped like ask_questions id 104; every publisher and figure is invented."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from linklib import figures as fg  # noqa: E402
from linklib import table_render as tr  # noqa: E402
from linklib.db import Library  # noqa: E402
from tests import figures_fixtures as fx  # noqa: E402

M = "ARR growth rate"


@pytest.fixture
def lib(tmp_path):
    return Library(str(tmp_path / "lib.db"))


def seed(lib):
    fx.seed_registry(lib)
    fx.load_report(lib, "ex-growth-2099", [
        fx.frow(metric=M, band="$20M-$50M", basis="ARR", denominator="percent year over year", statistic="median", value="15", unit="%"),
        fx.frow(metric=M, band="$20M-$50M", basis="ARR", denominator="percent year over year", statistic="p75", value="30", unit="%"),
        fx.frow(metric=M, band="$10M-$20M", basis="ARR", denominator="percent year over year", statistic="median", value="25", unit="%"),
        # an ACV price tier that contains a $20M-$50M ask when read as a size, and one that sits far below it
        fx.frow(metric=M, band="Greater than $250k", basis="other", segment="ACV band", denominator="percent of prior-year ARR", statistic="median", value="32", unit="%"),
        fx.frow(metric=M, band="$50-100k", basis="other", segment="by ACV", denominator="percent of prior-year ARR", statistic="median", value="16", unit="%"),
    ])


def test_acv_bands_are_not_matched_to_a_company_size(lib):
    seed(lib)
    res = fg.find_figures(lib, M, band="$20M-$50M")
    bands = {r["band"] for r in res["rows"]}
    assert "Greater than $250k" not in bands and "$50-100k" not in bands
    assert "$20M-$50M" in bands
    assert any("left out" in n and "company ARR or revenue" in n for n in res["notes"]), "never silent"


def test_arr_and_revenue_bases_stay_comparable():
    assert fg.size_basis_comparable("ARR") and fg.size_basis_comparable("Revenue") and fg.size_basis_comparable("")
    assert fg.size_basis_comparable("none") and not fg.size_basis_comparable("other") and not fg.size_basis_comparable("employees")


def test_asking_for_the_other_basis_still_returns_it(lib):
    seed(lib)
    res = fg.find_figures(lib, M, band="$20M-$50M", basis="other")
    assert any(r["band_basis"] == "other" for r in res["rows"])


def test_statistic_intent_words():
    assert fg.statistic_wants("What is typical ARR growth?")["best"] is False
    assert fg.statistic_wants("What is ARR growth?")["best"] is False
    assert fg.statistic_wants("What does good ARR growth look like?")["best"] is True
    assert fg.statistic_wants("best in class ARR growth")["best"] is True
    # a typical cue wins over "high performing"
    assert fg.statistic_wants("typical org structure for a high performing company")["best"] is False
    assert fg.statistic_wants("top quartile ARR growth")["best"] is True


def test_best_intent_leads_with_top_quartile_and_default_does_not():
    assert fg.statistic_rank("top_quartile", {"best": True}) < fg.statistic_rank("median", {"best": True})
    assert fg.statistic_rank("median") < fg.statistic_rank("top_quartile") and fg.statistic_rank("median") < fg.statistic_rank("other")


def _r(stat, key, **kw):
    r = {"figure_key": key, "report_slug": "ex", "source": "Ex", "report": "Ex 2099", "report_year": "2099", "data_period": "2098",
         "band": "$20M-$50M", "metric": M, "denominator": "percent year over year", "unit": "%", "statistic": stat,
         "value": "10", "value_num": 10.0, "match": "exact", "gaps": [], "segment": "", "series": "Ex", "cited_source": ""}
    r.update(kw)
    return r


def test_typical_table_shows_central_rows_only_and_keeps_them_when_alone():
    rows = [_r("median", 1), _r("top_quartile", 2), _r("other", 3), _r("target", 4), _r("mean", 5)]
    kept, gone = fg.apply_statistic_intent(rows, {})
    assert {r["figure_key"] for r in kept} == {1, 5} and gone == 3
    kept, gone = fg.apply_statistic_intent(rows, {"best": True})
    assert len(kept) == 5 and gone == 0
    only_q = [_r("top_quartile", 2)]
    assert fg.apply_statistic_intent(only_q, {})[0] == only_q, "never an empty table"
    only_mean = [_r("mean", 5), _r("p75", 6)]
    assert [r["figure_key"] for r in fg.apply_statistic_intent(only_mean, {})[0]] == [5], "typical still shows a mean with no median"


def test_a_percent_year_over_year_growth_row_reaches_a_table():
    r = _r("median", 7)
    assert fg.row_table_kind(r) == "spend_share"
    assert fg.row_table_kind(_r("median", 8, metric="Headcount growth", denominator="percent year over year")) != "spend_share"


def test_rank_shifts_leaves_out_quartile_unless_asked_or_best():
    sh = [{"line": "a", "statistic": "top_quartile", "metric": "ARR growth", "size": 9.0, "threshold": 3.0},
          {"line": "b", "statistic": "median", "metric": "ARR growth", "size": 5.0, "threshold": 3.0}]
    out, note = fg.rank_shifts(list(sh), 4)
    assert [s["line"] for s in out] == ["b"] and note["quartile_left_out"] == 1
    out, _ = fg.rank_shifts(list(sh), 4, best=True)
    assert [s["line"] for s in out][0] == "a"


def test_render_tables_drops_noncentral_spend_rows_for_a_typical_question():
    rows = [_r("median", 1), _r("top_quartile", 2)]
    text, info = tr.render_tables("{{table:spend}}", rows, lambda r: 1, wants={})
    assert "top_quartile" not in text and "top quartile" not in text.lower() and info.get("stat_left_out") == 1
    text, info = tr.render_tables("{{table:spend}}", rows, lambda r: 1, wants={"best": True})
    assert info.get("stat_left_out") is None
