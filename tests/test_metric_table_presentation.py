"""Table presentation (2026-11-26, follow-up to #814, ids 103 and 105): non-spend percent rows (ARR growth,
retention) get their own heading, one normalized metric name, no repeated Line item or Basis column, a Segment column
only when needed, and a stored statistic printed as stored. Fixtures use the real id 105 and id 103 denominator
strings and metric spellings with invented publishers and figures; they are NOT production's stored rows."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from linklib import figures as fg  # noqa: E402
from linklib import table_render as tr  # noqa: E402

GROWTH_DENOMS = ["year-over-year ARR growth", "YoY ARR growth", "% of ARR a year earlier",
                 "(current ARR minus ARR 12 months prior) / ARR 12 months prior", "percent year over year",
                 "ARR / Prior Year ARR - 1", "percent of prior-year ARR"]
GROWTH_NAMES = ["ARR growth", "ARR Growth", "ARR growth (year-over-year)", "ARR growth rate", "ARR Growth Rate",
                "year-over-year ARR growth", "ARR growth"]


def row(key, metric="ARR growth", canon="ARR Growth", denom="percent year over year", stat="median", **kw):
    r = {"figure_key": key, "report_slug": f"ex-{key}", "source": f"Ex{key}", "report": "Ex 2099", "report_year": "2099",
         "data_period": "2098", "band": "$20M-$50M", "metric": metric, "canonical_metric": canon, "denominator": denom,
         "unit": "%", "statistic": stat, "value": "15", "value_num": 15.0 + key, "match": "exact", "gaps": [],
         "segment": "", "series": f"Ex{key}", "cited_source": "", "note": "", "band_basis": "ARR"}
    r.update(kw)
    return r


def growth_rows():
    return [row(i + 1, metric=GROWTH_NAMES[i], denom=GROWTH_DENOMS[i], value=str(10 + i), value_num=10.0 + i) for i in range(7)]


def build(rows):
    return tr.build_spend(rows, lambda r: r["figure_key"])[0]


def header(md):
    line = next(l for l in md.splitlines() if l.startswith("| ") and "---" not in l)
    return [c.strip() for c in line.strip("|").split("|")]


def cells(md, col):
    h = header(md)
    out = []
    for l in md.splitlines():
        if l.startswith("| ") and "---" not in l and not l.startswith("| " + h[0] + " |"):
            out.append([c.strip() for c in l.strip("|").split("|")][h.index(col)])
    return out


def test_growth_rows_are_headed_by_the_canonical_metric_not_spend():
    md = build(growth_rows())
    assert md.splitlines()[0] == "**ARR growth**"
    assert "Spend by function" not in md and "year-over-year" not in md.splitlines()[0]


def test_retention_rows_are_headed_by_their_metric_too():
    rows = [row(1, metric="Net Dollar Retention", canon="Net Dollar Retention", denom="% of beginning ARR"),
            row(2, metric="NDR", canon="Net Dollar Retention", denom="percent of prior ARR (window not defined)")]
    md = build(rows)
    assert md.splitlines()[0] == "**Net dollar retention**" and "Spend by function" not in md


def test_one_metric_drops_the_line_item_column_and_the_names_never_print():
    md = build(growth_rows())
    assert "Line item" not in header(md)
    assert "ARR Growth Rate" not in md and "ARR growth (year-over-year)" not in md


def test_equivalent_bases_collapse_to_one_line_and_no_basis_column():
    md = build(growth_rows())
    assert "Basis" not in header(md)
    assert "Basis as printed:" in md and "publisher wording varies slightly" in md


def test_the_seven_growth_denominators_are_one_group():
    assert len({fg.denominator_group(d)[0] for d in GROWTH_DENOMS}) == 1


def test_unrelated_denominators_do_not_join_the_growth_group():
    g = fg.denominator_group("percent year over year")[0]
    for other in ("percent of revenue", "% of ARR", "percent of prior ARR", "% of beginning ARR"):
        assert fg.denominator_group(other)[0] != g


def test_bases_that_differ_in_substance_keep_the_basis_column():
    rows = [row(1, denom="percent of revenue"), row(2, denom="percent of ARR")]
    md = build(rows)
    assert "Basis" in header(md) and "Basis as printed" not in md


def test_segment_column_only_when_a_row_has_one():
    assert "Segment" not in header(build(growth_rows()))
    rows = growth_rows()[:3]
    rows[1]["segment"] = "AI core to the product"
    md = build(rows)
    assert "Segment" in header(md)
    seg = cells(md, "Segment")
    assert "AI core to the product" in seg and "All" in seg


def test_an_all_segment_label_does_not_add_the_column():
    rows = growth_rows()[:2]
    rows[0]["segment"] = "All"
    assert "Segment" not in header(build(rows))


def test_estimate_keeps_its_stored_statistic_and_is_marked_in_the_report_cell():
    r = row(1, data_period="2026E", note="respondent expectation", stat="median", band="$10M-$25M")
    md = build([r])
    assert cells(md, "Statistic") == ["median"]
    rep = cells(md, "Report and period")[0]
    assert "2026E, respondent expectation" in rep and "target" not in md


def test_a_stored_target_still_prints_target():
    md = build([row(1, stat="benchmark target"), row(2, stat="median")])
    assert "target" in cells(md, "Statistic")


def test_statistic_display_uses_words():
    md = build([row(1, stat="top_quartile"), row(2, stat="p75")])
    assert "top_quartile" not in md and "top quartile" in cells(md, "Statistic") and "p75" in cells(md, "Statistic")
    assert tr.statistic_display("bottom_quartile") == "bottom quartile"


def test_a_real_spend_table_is_unchanged():
    r = row(1, metric="S&M % of Revenue", canon="", denom="percent of revenue", basis_group="revenue")
    r2 = row(2, metric="R&D % of Revenue", canon="", denom="percent of revenue", basis_group="revenue")
    md = build([r, r2])
    assert md.splitlines()[0] == "**Spend by function, percent of revenue**"
    assert header(md) == ["Line item", "Band", "Statistic", "Value", "Basis", "Report and period"]


def test_two_canonical_metrics_keep_the_line_item_column():
    rows = [row(1), row(2, metric="Net Dollar Retention", canon="Net Dollar Retention", denom="percent year over year")]
    md = build(rows)
    assert "Line item" in header(md)
    assert set(cells(md, "Line item")) == {"ARR growth", "Net dollar retention"}


def test_an_unresolved_metric_keeps_its_raw_name():
    md = build([row(1, metric="Pipeline Coverage Ratio", canon="")])
    assert md.splitlines()[0] == "**Pipeline Coverage Ratio**"


def test_mixed_spend_and_growth_rows_render_two_tables():
    rows = [row(1, metric="S&M % of Revenue", canon="", denom="percent of revenue", basis_group="revenue"), row(2)]
    md = build(rows)
    assert "**Spend by function, percent of revenue**" in md and "**ARR growth**" in md


def test_render_tables_still_selfchecks_clean():
    text, info = tr.render_tables("{{table:spend}}", growth_rows(), lambda r: r["figure_key"], wants={})
    assert "ARR growth" in text and not info.get("selfcheck")
