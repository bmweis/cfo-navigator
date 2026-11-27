"""Row hygiene, Phase 1 (2026-11-27, ask_questions id 106 "What is typical net dollar retention?").
Fixtures use the real id 106 band, segment, basis and period strings with INVENTED publishers and figures;
they are not production's stored rows."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from linklib import figures as fg  # noqa: E402
from linklib import table_render as tr  # noqa: E402
from linklib.db import Library  # noqa: E402
from tests import figures_fixtures as fx  # noqa: E402

M = "Net revenue retention"


@pytest.fixture
def lib(tmp_path):
    return Library(str(tmp_path / "lib.db"))


def _seed(lib, with_size=True):
    fx.seed_registry(lib)
    rows = []
    if with_size:
        rows += [fx.frow(metric=M, band="$20M-$50M", basis="ARR", value="104", statistic="median"),
                 fx.frow(metric=M, band="<$100M", basis="ARR", segment="All", value="101", statistic="median")]
    rows += [
        fx.frow(metric=M, band="<$25", basis="arpa", segment="ARPA per month", value="56", statistic="median"),
        fx.frow(metric=M, band="$25-$100", basis="arpa", segment="ARPA per month", value="80", statistic="median"),
        fx.frow(metric=M, band="ACV <$1K", basis="acv", segment="All", value="89", statistic="median"),
        fx.frow(metric=M, band="ACV >$250K", basis="acv", segment="All", value="99", statistic="median"),
    ]
    fx.load_report(lib, "ex-nrr-2099", rows)


def bands(res):
    return {r["band"] for r in res["rows"]}


# --- 1. non-size bands leave the default ---------------------------------------------------------------
def test_no_band_drops_acv_and_arpa_rows_with_a_note(lib):
    _seed(lib)
    res = fg.find_figures(lib, M)
    assert bands(res) == {"$20M-$50M", "<$100M"}
    assert any("something other than company size" in n and "left out" in n for n in res["notes"]), "never silent"


def test_no_band_fails_open_when_only_non_size_rows_exist(lib):
    _seed(lib, with_size=False)
    res = fg.find_figures(lib, M)
    assert len(res["rows"]) == 4
    assert any("Every figure on file" in n for n in res["notes"])


@pytest.mark.parametrize("hint", ["NRR by ACV", "net dollar retention by ARPA", "NRR by price tier"])
def test_a_question_naming_the_other_basis_still_returns_those_rows(lib, hint):
    _seed(lib)
    res = fg.find_figures(lib, M, basis_hint=hint)
    assert {"ACV <$1K", "<$25"} <= bands(res)


def test_explicit_basis_parameter_still_returns_acv_rows(lib):
    _seed(lib)
    res = fg.find_figures(lib, M, basis="acv")
    assert bands(res) == {"ACV <$1K", "ACV >$250K"}


def test_a_dollar_band_question_is_unchanged_from_814(lib):
    _seed(lib)
    res = fg.find_figures(lib, M, band="$20M-$50M")
    assert not ({"ACV <$1K", "<$25"} & bands(res))
    assert any("company ARR or revenue" in n for n in res["notes"])


# --- 2. estimates rank after actuals --------------------------------------------------------------------
def _r(key, **kw):
    r = {"figure_key": key, "report_slug": f"rep{key}", "source": f"Pub{key}", "report": "R", "report_year": "2025",
         "data_period": "2024", "band": "$20M-$50M", "band_basis": "ARR", "metric": M, "denominator": "percent of prior-year ARR",
         "unit": "%", "statistic": "median", "value": "100", "value_num": 100.0, "match": "exact", "gaps": [],
         "segment": "", "series": f"Pub{key}", "cited_source": "", "note": ""}
    r.update(kw)
    return r


def test_an_estimate_never_leads_and_loses_the_cap_to_an_actual():
    est = _r(1, report_year="2026", data_period="2026E", segment="AI-native/enabled")
    act = _r(2, report_year="2025", data_period="2024")
    within = lambda r: (0, fg.statistic_rank(r.get("statistic")))
    assert [r["figure_key"] for r in tr.select_rows([est, act], 1, within)] == [2]
    assert sorted([est, act], key=tr.row_order_key)[0]["figure_key"] == 2
    # still printed, and marked, when room allows
    both = [r["figure_key"] for r in tr.select_rows([est, act], 2, within)]
    assert sorted(both) == [1, 2]
    text, _ = tr.build_metric_table([est, act], lambda r: r["figure_key"])
    assert "2026E" in text and "estimate" in text and text.index("Pub2") < text.index("Pub1")


# --- 3. all-segment rows rank before segment rows -------------------------------------------------------
@pytest.mark.parametrize("seg", ["", "All", "all", "Total population", "all SaaS companies", "Overall"])
def test_segment_is_all(seg):
    assert fg.segment_is_all(seg)


def test_segment_is_all_rejects_a_real_segment():
    assert not fg.segment_is_all("AI-native/enabled") and not fg.segment_is_all("AI core to the product")


def test_all_segment_rows_lead_and_the_label_is_one_word():
    seg = _r(1, segment="AI core to the product")
    allp = _r(2, segment="total population")
    alls = _r(3, segment="all SaaS companies")
    within = lambda r: (0, 0)
    assert sorted([seg, allp, alls], key=tr.row_order_key)[-1]["figure_key"] == 1
    assert [r["figure_key"] for r in tr.select_rows([seg, allp, alls], 2, within)] != [1, 2]
    assert 1 not in [r["figure_key"] for r in tr.select_rows([seg, allp, alls], 2, within)]
    text, _ = tr.build_metric_table([seg, allp, alls], lambda r: r["figure_key"])
    assert "total population" not in text and "all SaaS" not in text
    assert text.count("| All |") == 2 and "AI core to the product" in text


def test_a_named_segment_leaves_the_order_alone():
    a = _r(1, segment="AI core to the product", segment_asked=True)
    b = _r(2, segment="All", segment_asked=True)
    assert tr._hygiene_rank(a)[1] == tr._hygiene_rank(b)[1] == 0


# --- 4. shift lines honor statistic intent --------------------------------------------------------------
def _sh(line, stat, metric="ARR growth"):
    return {"line": line, "statistic": stat, "metric": metric, "size": 5.0, "threshold": 3.0, "unit": "percent"}


def test_typical_question_leaves_other_and_target_shifts_out_when_a_median_exists():
    sh = [_sh("a", "other"), _sh("b", "median"), _sh("c", "mean", "Gross margin"), _sh("d", "target", "Gross margin")]
    out, note = fg.rank_shifts(list(sh), 4, wants=fg.statistic_wants("What is typical ARR growth?"))
    assert {s["line"] for s in out} == {"b", "c"} and note["stat_left_out"] == 2


def test_other_only_shifts_are_kept_never_an_empty_list():
    out, note = fg.rank_shifts([_sh("a", "other")], 4, wants={})
    assert [s["line"] for s in out] == ["a"] and note["stat_left_out"] == 0


def test_best_range_and_extreme_questions_keep_every_statistic():
    sh = [_sh("a", "other"), _sh("b", "median")]
    for ask in ("What does good ARR growth look like?", "What is the range of ARR growth?", "What is the max ARR growth?"):
        out, note = fg.rank_shifts(list(sh), 4, wants=fg.statistic_wants(ask), best=fg.statistic_wants(ask)["best"])
        assert len(out) == 2 and note["stat_left_out"] == 0


def test_no_wants_argument_is_the_old_behavior():
    out, note = fg.rank_shifts([_sh("a", "other"), _sh("b", "median")], 4)
    assert len(out) == 2 and note["stat_left_out"] == 0


def test_shift_lines_cap_is_still_four():
    from linklib import table_render
    assert table_render.SHIFT_LINES == 4


# --- investigation: why retention rows classified "other" (id 106 not_in_table) ---------------------------
@pytest.mark.parametrize("denom", ["1 plus (upsold minus downsold minus churned MRR) / sum of MRR from same cohort",
                                   "annual, after churn, including upsell and expansion, in cohorts"])
def test_a_retention_row_with_a_formula_denominator_is_a_percent_row(denom):
    r = _r(9, metric="net revenue retention", denominator=denom)
    assert fg.row_table_kind(r) == "spend_share"
