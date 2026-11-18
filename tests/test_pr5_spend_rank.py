"""PR 5: periods like CY-24 parse, OpenView "including headcount" spend rows are spend, and the spend table ranks by statistic.
Strings are copied from stored rows (read 2026-11-18); values and figure keys are real, publishers are not invented."""
import pytest
from linklib import figures as fg
from linklib import benchmark_prestep as bp


@pytest.mark.parametrize("period,year", [
    ("CY-24", 2024), ("CY-24 or LTM", 2024), ("FY24", 2024), ("FY2025", 2025), ("FY-25", 2025),
    ("LTM to Jan-Feb 2026 submission", 2026), ("Q2 2023", 2023), ("2022", 2022),
    ("change Q2 2022 to Q2 2023", 2022), ("2023 edition; benchmark targets (2023 edition)", 2023),
    ("historical, at the revenue run-rate", -1), ("", -1), (None, -1)])
def test_year_num_shapes(period, year):
    assert fg._year_num(period) == year


def _r(**kw):
    base = dict(unit="%", statistic="median", segment="", band="$20-50M", match="exact", band_context=False,
                report_year="2023", data_period="Q2 2023", source="OpenView", series="OpenView", figure_key=1, value_num=30.0)
    base.update(kw)
    return base


@pytest.mark.parametrize("metric,denom", [
    ("Sales & Marketing Spend", "% of ending ARR, Q2 2023, including headcount"),
    ("R&D Spend", "% of ending ARR, Q2 2023, including headcount"),
    ("Sales & Marketing Spend", "percent of ending ARR as of Q2 2022, including headcount"),
    ("R&D Spend", "percent of ending ARR as of Q2 2022, including headcount")])
def test_including_headcount_spend_is_spend(metric, denom):
    r = _r(metric=metric, denominator=denom)
    assert fg.row_table_kind(r) == "spend_share"
    assert fg.expense_line_of(metric, denom, "%")


@pytest.mark.parametrize("metric,denom", [
    ("Engineering headcount mix", "percent of headcount"),
    ("Sales", "% of total employees"),
    ("Headcount distribution by function: S&M share", "% of headcount"),
    ("R&D headcount", "share of FTEs")])
def test_true_headcount_share_unchanged(metric, denom):
    assert fg.row_table_kind(_r(metric=metric, denominator=denom)) == "headcount_share"


def test_cy24_beats_older_period_in_spend_rank():
    base = dict(metric="Sales and Marketing Expenses % of Revenue", denominator="percent of revenue")
    cy24 = _r(source="Benchmarkit", series="Benchmarkit", report_year="2025", data_period="CY-24", figure_key=4779, **base)
    old = _r(source="Benchmarkit", series="Benchmarkit", report_year="2023", data_period="2022", figure_key=1803, **base)
    assert bp._spend_rank(cy24) < bp._spend_rank(old)


def test_median_beats_target_and_quartile():
    base = dict(metric="S&M", denominator="% of revenue")
    med = _r(figure_key=4779, data_period="2022", **base)
    p25 = _r(figure_key=4778, data_period="2022", statistic="p25", **base)
    tgt = _r(figure_key=363, data_period="2023 edition; benchmark targets (2023 edition)", statistic="other", **base)
    order = sorted([tgt, p25, med], key=bp._spend_rank)
    assert [r["figure_key"] for r in order] == [4779, 4778, 363]


def test_tiebreak_is_numeric_not_string():
    base = dict(metric="S&M", denominator="% of revenue")
    a, b = _r(figure_key=999, **base), _r(figure_key=1000, **base)
    assert bp._spend_rank(a) < bp._spend_rank(b)
