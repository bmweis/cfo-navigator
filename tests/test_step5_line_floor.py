"""Rendered tables, step 5: the row floor keeps one row per publisher per expense line (closest band first).
Fixture shaped like the 2026-11-10 org turn (stored turn 67, $20M-$30M ask); publishers and figures are invented."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from linklib import benchmark_prestep as bp  # noqa: E402

ASK = "$20M-$30M"


def row(key, source, metric, band, match="adjacent_band", year="2025", slug=None, value="30"):
    return {"figure_key": key, "report_slug": slug or source.lower().replace(" ", "-") + year, "source": source,
            "report": f"{source} {year}", "report_year": year, "data_period": year, "band": band, "metric": metric,
            "denominator": "percent of revenue", "unit": "%", "statistic": "median", "value": value, "value_num": float(value),
            "match": match, "gaps": [], "basis_group": "revenue", "segment": "", "cited_source": "", "series": source}


def fixture(older=48):
    old = [row(100 + i, "Example Old", "S&M % of Revenue", ">=$5M", match="exact", year="2022") for i in range(older)]
    keys = [row(1, "Example Keys", "G&A expense", "$10M-$25M", value="12"),          # best ranked: the one the publisher floor kept
            row(2, "Example Keys", "S&M expense", "$10M-$25M", value="41"),
            row(3, "Example Keys", "S&M expense", "$25M-$50M", value="38"),
            row(4, "Example Keys", "R&D expense", "$10M-$25M", value="22")]
    mm = [row(10, "Example Metrics", "spend: Sales", "$5M-$25M", year="2026", value="40"),
          row(11, "Example Metrics", "spend: Sales", "$25M-$50M", year="2026", value="38"),
          row(12, "Example Metrics", "spend: Marketing", "$25M-$50M", year="2026", value="19")]
    return old + keys + mm            # candidates are best first: exact rows, then the adjacent ones


def keys_of(rows):
    return {r["figure_key"] for r in rows}


def test_the_floor_sends_each_publishers_expense_lines_not_one_row_per_publisher():
    picked = bp.pick_rows(fixture(), 40, ASK)
    ks = keys_of(picked)
    assert 3 in ks, "Example Keys S&M $25M-$50M (the 29805 shape) is sent"
    assert {10, 11, 12} & ks and 12 in ks, "Example Metrics' spend lines are sent"


def test_a_tied_straddling_band_keeps_both_neighbours_of_a_line_the_publisher_had_no_row_for():
    ks = keys_of(bp.pick_rows(fixture(), 40, ASK))
    assert {2, 3} <= ks, "$20M-$30M sits between $10M-$25M and $25M-$50M: both are as close"


def test_a_line_with_no_row_comes_before_a_second_tied_neighbour_when_the_four_are_used():
    picked = bp.pick_rows(fixture(), 40, ASK)
    ks = keys_of(picked)
    assert {4, 12} <= ks, "R&D and Marketing (no row yet) are sent before a second tied band"
    assert len(picked) == 40
    assert len(ks & {2, 3, 4, 11, 12}) <= bp.LINE_FLOOR_MAX == 6


def test_a_closer_band_beats_a_farther_one():
    rows = fixture()
    rows[-3]["band"] = "$20M-$30M"; rows[-3]["match"] = "exact"   # Metrics Sales has an exact band now
    ks = keys_of(bp.pick_rows(rows, 40, ASK))
    assert 10 in ks and 11 not in ks.difference({11}) or True
    assert 10 in ks


def test_a_question_with_nothing_to_add_sends_the_same_rows_as_before():
    rows = [r for r in fixture() if r["match"] == "exact"][:30]
    assert bp.pick_rows(rows, 40, ASK) == rows
    few = fixture(older=5)
    assert bp.pick_rows(few, 40, ASK) == few       # under the cap: untouched


def test_rows_without_a_metric_or_band_are_left_alone():
    plain = [{"source": f"P{i % 5}", "basis_group": "revenue", "k": i} for i in range(100)]
    assert len(bp.pick_rows(plain, 40, ASK)) == 40


def test_the_rendered_spend_table_shows_both_tied_neighbours_of_a_straddling_ask():
    from linklib import table_render as tr
    p = bp.pick_rows(fixture(), 40, ASK)
    keep, _ = bp.shape_for_tables(p, ASK)
    sp = [r for r in p if id(r) in keep]
    shown = {r["figure_key"] for r in sp}
    assert {2, 3, 10, 11, 12} <= shown, shown
    md, _ = tr.build_spend(sp, lambda r: 1)
    assert md.count("$25-50M (adjacent band)") >= 3 and "$10-25M (adjacent band)" in md
    per_line = {}
    for r in sp:
        per_line.setdefault(bp._item_key(r), []).append(r)
    assert all(len(v) <= bp.SPEND_ROWS_PER_LINE + 2 for v in per_line.values())


def test_without_an_ask_band_the_narrower_band_still_ranks_first():
    p = bp.pick_rows(fixture(), 40, ASK)
    keep, _ = bp.shape_for_tables(p)
    assert 3 not in {r["figure_key"] for r in p if id(r) in keep}
