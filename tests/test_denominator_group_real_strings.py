"""Denominator groups, follow-up to #812: real production denominator spellings (read from the 2026-11-26 audit
CSV). The metric names, publishers and values in the fixtures are invented ("Example Benchmarks"); only the
denominator strings are real, so a production "after" is inference until a question is rerun."""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from figures_fixtures import frow, load_report  # noqa: E402

from linklib import figures as fg
from linklib.db import Library


@pytest.fixture
def lib(tmp_path):
    db = Library(str(tmp_path / "t.db"))
    db.seed_voice_prompts()
    yield db
    db.close()


def g(s):
    return fg.denominator_group(s)[0]


def _shifts(lib, metric, den_a, den_b, unit="percent"):
    def r(y, v, den):
        return frow(source="Example Benchmarks", report=f"Example Benchmarks {y}", year=str(y), metric=metric,
                    band="$20M-$50M", value=str(v), unit=unit, denominator=den, data_period=str(y - 1))
    load_report(lib, "eb-2023", [r(2023, 100, den_a)])
    load_report(lib, "eb-2025", [r(2025, 110, den_b)])
    res = fg.find_figures(lib, metric, band="$30M", all_editions=True, recent_window=True, limit=500)
    return fg.compute_shifts(res["rows"], lib.shift_rules())


# 1. retention family
@pytest.mark.parametrize("a,b", [("beginning ARR", "starting ARR"), ("beginning cohort ARR", "cohort ARR"),
                                 ("cohort ARR", "starting ARR"), ("beginning ARR", "beginning cohort ARR"),
                                 ("percent of beginning ARR", "% of starting ARR")])
def test_retention_starting_arr_spellings_are_one_group(a, b):
    assert g(a) == g(b)


def test_retention_spellings_pair_across_editions(lib):
    out = _shifts(lib, "Net dollar retention", "beginning cohort ARR", "starting ARR")
    assert out["shifts"], out["unpaired_groups"]
    assert "stated basis" in out["shifts"][0]["line"]


@pytest.mark.parametrize("other", ["prior-period ARR", "prior ARR", "average BOP and EOP ARR", "starting ARR, annualized",
                                   "ARR contracted at 12/31/20", "(window not defined)", "(as NDR)", "starting MRR",
                                   "beginning MRR", "MRR at start of cohort"])
def test_retention_keep_out_strings_stay_their_own_group(other):
    assert g(other) != g("starting ARR")
    assert fg.denominator_group(other)[1] != "synonym"


def test_prior_arr_and_prior_period_arr_do_not_join_each_other_or_the_cohort():
    assert len({g("prior ARR"), g("prior-period ARR"), g("starting ARR")}) == 3


def test_grr_and_nrr_never_pair_even_with_the_same_denominator(lib):
    def r(y, metric, v):
        return frow(source="Example Benchmarks", report=f"Example Benchmarks {y}", year=str(y), metric=metric,
                    band="$20M-$50M", value=str(v), unit="percent", denominator="starting ARR", data_period=str(y - 1))
    load_report(lib, "eb-2023", [r(2023, "Gross dollar retention", 88)])
    load_report(lib, "eb-2025", [r(2025, "Net dollar retention", 104)])
    rows = []
    for m in ("Gross dollar retention", "Net dollar retention"):
        rows += fg.find_figures(lib, m, band="$30M", all_editions=True, recent_window=True, limit=500)["rows"]
    assert not fg.compute_shifts(rows, lib.shift_rules())["shifts"]


# 2. subscription revenue
def test_gaap_subscription_revenue_joins_subscription_revenue_either_word_order():
    assert g("% of subscription revenue") == g("percent of subscription GAAP revenue") == g("% of GAAP subscription revenue")
    assert fg.denominator_group("percent of subscription GAAP revenue") == ("subscription revenue", "synonym")


def test_subscription_stays_apart_from_total_and_recurring_revenue():
    assert g("% of subscription revenue") != g("% of revenue")
    assert g("% of GAAP subscription revenue") != g("% of GAAP revenue")
    assert g("subscription revenue") != g("recurring revenue")


def test_gaap_subscription_pairs_across_editions(lib):
    out = _shifts(lib, "Gross margin", "% of subscription revenue", "percent of subscription GAAP revenue")
    assert out["shifts"], out["unpaired_groups"]


# 3. Tier 1
def test_plus_and_plus_sign_are_one_token():
    assert g("growth + FCF margin") == g("growth plus FCF margin") == g("growth+FCF margin")


def test_rule_of_40_composites_stay_apart():
    assert len({g("growth + FCF margin"), g("growth + EBITDA margin"), g("revenue growth"), g("FCF margin")}) == 4


def test_short_tokens_are_not_dropped_fte():
    assert g("EOP ARR / EOP FTEs") == "eop arr eop employee"
    assert g("ARR per FTE, $") == g("$ ARR per FTE") == g("ARR per full-time employee") == "arr per employee"


def test_role_tokens_keep_arr_per_fte_apart_from_arr_per_ae():
    assert g("ARR per AE") != g("ARR per FTE")
    assert g("ARR per CSM") != g("ARR per FTE")
    assert g("ARR per AE") != g("ARR per CSM")


def test_plain_fte_denominators_still_collapse_to_employees():
    assert g("full-time equivalent employees at end of Q2 2022") == g("FTE employees") == g("employees")


def test_dollar_alone_is_still_empty_and_not_a_stated_basis():
    assert fg.denominator_group("$") == ("", "")        # reported, not changed: "$" shares "(empty)" with a blank
    assert fg.denominator_group("") == ("", "own")


# 4. guards
def test_per_employee_unit_scales_stay_apart():
    assert len({g("$K per employee"), g("$ per employee"), g("USD per employee")}) == 3
    assert g("ending ARR per $k") != g("ending ARR per $")


def test_cac_payback_margin_variants_stay_apart():
    trio = ["months", "months, gross-margin adjusted", "months of subscription gross margin",
            "months of subscription gross margin to recover fully loaded CAC"]
    assert len({g(s) for s in trio}) == 4


def test_cac_ratio_new_arr_variants_stay_apart():
    assert len({g("new customer ARR"), g("new ARR"), g("new plus expansion ARR")}) == 3
    assert g("$ S&M per $1 new ARR") == g("S&M $ per $1 new ARR")


def test_existing_good_groups_hold():
    for s in ("percent of revenue", "% of revenue", "revenue", "percent of total revenue", "% of GAAP revenue",
              "percent of GAAP revenue"):
        assert g(s) == "revenue", s
    assert g("% of GAAP revenue (estimated)") != "revenue"
