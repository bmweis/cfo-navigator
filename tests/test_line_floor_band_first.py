"""Expense-line floor: the closest band to the asked size wins a floor slot ahead of a newer, farther-band row.
Shaped like production turn 75 part 3 (cap 18, $20M-$30M ask). Real fields of figure 29805 (KeyBanc and Sapphire
2025, S&M expense, $25M-$50M, median 38, % of revenue); the other publishers and rows are invented."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from linklib import benchmark_prestep as bp  # noqa: E402

ASK = "$20M-$30M"
SAP = "KeyBanc Capital Markets and Sapphire Ventures"


def row(key, source, metric, band, year, match="adjacent_band", value="30", period=None):
    return {"figure_key": key, "report_slug": source.lower().replace(" ", "-") + year, "source": source,
            "report": f"{source} {year}", "report_year": year, "data_period": period or year, "band": band,
            "metric": metric, "denominator": "% of revenue", "unit": "%", "statistic": "median", "value": value,
            "value_num": float(value), "match": match, "gaps": [], "basis_group": "revenue", "segment": "",
            "cited_source": "", "series": source}


def part3(cap_filler=40):
    exact = [row(100 + i, "Example Old", "S&M % of Revenue", "$20M-$30M", "2022", match="exact") for i in range(cap_filler)]
    iconiq = [row(200, "Example ICONIQ", "S&M expense", "<$100M", "2026", value="35"),
              row(201, "Example ICONIQ", "R&D expense", "<$100M", "2026", value="25"),
              row(202, "Example ICONIQ", "G&A expense", "<$100M", "2026", value="15"),
              row(203, "Example Metrics", "Sales and marketing", "$5M-$75M", "2026", value="40"),
              row(204, "Example Metrics", "Sales expense", "$5M-$75M", "2026", value="20"),
              row(205, "Example Metrics", "Marketing expense", "$5M-$75M", "2026", value="19"),
              row(206, "Example Metrics", "Customer success expense", "$5M-$75M", "2026", value="9"),
              row(207, "Example Metrics", "R&D expense", "$5M-$75M", "2026", value="22")]
    sap = [row(29801, SAP, "S&M expense", "$10M-$25M", "2025", value="41"),
           row(29805, SAP, "S&M expense", "$25M-$50M", "2025", value="38"),
           row(29806, SAP, "R&D expense", "$25M-$50M", "2025", value="21"),
           row(29807, SAP, "G&A expense", "$25M-$50M", "2025", value="12")]
    return exact + iconiq + sap


def keys(rows):
    return {r["figure_key"] for r in rows}


def test_closest_band_row_is_sent_ahead_of_newer_far_band_rows():
    picked = bp.pick_rows(part3(), 18, ASK)
    assert 29805 in keys(picked)


def test_near_band_older_row_beats_far_band_newer_row_for_the_last_slot():
    rows = part3(cap_filler=17)          # 17 exact rows + publisher floor fill the cap; one floor slot is left
    cap = 18
    picked = bp.pick_rows(rows, cap, ASK)
    ks = keys(picked)
    assert len(picked) == cap
    near = {29805, 29801, 29806, 29807} & ks
    assert near, "a Sapphire band that touches the ask is sent"
    # the one far-band row cannot displace every near-band one
    assert {200, 201, 202, 203, 204, 205, 206, 207} & ks != {200, 201, 202, 203, 204, 205, 206, 207}


def test_no_near_band_rows_leaves_the_order_unchanged():
    exact = [row(100 + i, "Example Old", "S&M % of Revenue", "$20M-$30M", "2022", match="exact") for i in range(40)]
    far = [row(300, "Example A", "S&M expense", "$100M-$200M", "2025"),
           row(301, "Example B", "S&M expense", "$100M-$200M", "2026"),
           row(302, "Example B", "R&D expense", "$100M-$200M", "2026")]
    rows = exact + far
    picked = bp.pick_rows(rows, 12, ASK)
    # newer editions still lead among equally far rows (today's behaviour)
    assert {301, 302} <= keys(picked)
