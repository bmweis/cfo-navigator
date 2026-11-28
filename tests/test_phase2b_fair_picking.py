"""Phase 2b (2026-11-28, after ask_questions id 109): year before sample size, round-robin by report inside a part's cap,
performance-tier segments out of a plain answer, one recency window per part, an accurate older-data clause.
Publisher, report, band, segment and period strings are the real ones from id 109; every figure is invented."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from linklib import benchmark_prestep as bp  # noqa: E402
from linklib import figures as fg  # noqa: E402
from linklib.db import Library  # noqa: E402
from tests import figures_fixtures as fx  # noqa: E402

M = "Net revenue retention"          # registered; "net dollar retention" is its alias


@pytest.fixture
def lib(tmp_path):
    lb = Library(str(tmp_path / "lib.db"))
    fx.seed_registry(lb)
    return lb


def row(src, rep, yr, band, seg="All", per="2026 (through Q2)", n="38", metric=M, value="104", stat="median"):
    return fx.frow(metric=metric, band=band, segment=seg, data_period=per, value=value, unit="%", n=n,
                   source=src, report=rep, year=yr, denominator="percent of prior ARR", statistic=stat)


BANDS = ["<$1M", "$1M-$5M", "$5M-$20M", "$20M-$50M", "$50M-$100M", "$100M+"]


def seed_crowd(lib):
    for slug, rep in [("iq-sos", "State of Scaling: The Great Sorting"), ("iq-gtm", "The State of Go-to-Market in 2026"),
                      ("iq-e5", "The ICONIQ Enterprise Five")]:
        fx.load_report(lib, slug, [row("ICONIQ", rep, "2026", b, seg=s, value=str(100 + i)) for i, (b, s) in
                                   enumerate((b, s) for b in BANDS for s in ("All", "Cohort X", "Cohort Y"))])
    for slug, src, rep in [("ha", "High Alpha", "2026 Startup Benchmarks Report"),
                           ("kb", "KeyBanc Capital Markets and Sapphire Ventures", "SaaS Survey 2025"),
                           ("jc", "Jurassic Capital", "B2B SaaS Benchmarks Report 2026"),
                           ("mm", "Mostly Metrics", "Benchmarking Club May 2026 edition")]:
        fx.load_report(lib, slug, [row(src, rep, "2026", b, n="", per="Q2 2026") for b in BANDS])


def test_year_outranks_sample_size(lib):
    fx.load_report(lib, "old", [row("Alpha", "Alpha 2025", "2025", "$20M-$50M", per="FY2024", n="120", value="90")])
    fx.load_report(lib, "new", [row("Beta", "Beta 2026", "2026", "$20M-$50M", per="FY2025", n="", value="95")])
    vals = [r["value"] for r in fg.find_figures(lib, M, band="$20M-$50M")["rows"]]
    assert vals == ["95", "90"]                 # old order put the 2024 survey with a stated n first
    sub = bp.Sub(intent="benchmark", text="typical NRR at $20M-$50M", metrics=[M], band="$20M-$50M")
    bp.gather(lib, sub)
    assert [r["value"] for r in sub.candidates] == ["95", "90"]


def test_same_year_still_prefers_a_stated_sample(lib):
    fx.load_report(lib, "a", [row("Alpha", "Alpha 2026", "2026", "$20M-$50M", per="FY2025", n="", value="90")])
    fx.load_report(lib, "b", [row("Beta", "Beta 2026", "2026", "$20M-$50M", per="FY2025", n="55", value="95")])
    assert [r["value"] for r in fg.find_figures(lib, M, band="$20M-$50M")["rows"]] == ["95", "90"]


def test_round_robin_gives_every_report_a_share(lib):
    seed_crowd(lib)
    sub = bp.Sub(intent="benchmark", text="What is typical net dollar retention?", metrics=[M])
    bp.gather(lib, sub)
    picked = bp.pick_rows(sub.candidates, 40)
    by = {}
    for r in picked:
        by[r["report_slug"]] = by.get(r["report_slug"], 0) + 1
    assert len(picked) == 40
    assert all(by.get(s, 0) >= 3 for s in ("ha", "kb", "jc", "mm"))        # old: 1 each (the publisher floor)
    iconiq = sum(v for k, v in by.items() if k.startswith("iq-"))
    assert iconiq < 20


def test_one_report_part_is_unchanged(lib):
    fx.load_report(lib, "only", [row("Alpha", "Alpha 2026", "2026", b, per=f"FY20{20 + i}") for i, b in enumerate(BANDS)])
    sub = bp.Sub(intent="benchmark", text="NRR", metrics=[M])
    bp.gather(lib, sub)
    assert bp._report_round_robin(sub.candidates) == list(range(len(sub.candidates)))
    assert bp.pick_rows(sub.candidates, 3) == sub.candidates[:3] or len(bp.pick_rows(sub.candidates, 3)) == 3


def tier_report(lib):
    fx.load_report(lib, "iq", [row("ICONIQ", "State of Scaling", "2026", "<$100M", seg=s, value=v)
                               for s, v in (("All", "104"), ("Pacesetters", "120"), ("Others", "102"))])


def segs(sub):
    return sorted(r["segment"] for r in sub.candidates)


def test_tier_segments_left_out_of_a_plain_question(lib):
    tier_report(lib)
    sub = bp.Sub(intent="benchmark", text="What is typical net dollar retention?", metrics=[M])
    bp.gather(lib, sub)
    assert segs(sub) == ["All"]
    assert sorted(r["segment"] for r in sub.also_stored) == ["Others", "Pacesetters"]


def test_tier_segments_kept_when_asked_or_named(lib):
    tier_report(lib)
    top = bp.Sub(intent="benchmark", text="What do top performers get for net dollar retention?", metrics=[M])
    bp.gather(lib, top)
    assert "Pacesetters" in segs(top)
    named = bp.Sub(intent="benchmark", text="NRR for Pacesetters", metrics=[M], segment="Pacesetters")
    bp.gather(lib, named)
    assert segs(named) == ["Pacesetters"]


def test_tier_only_metric_is_not_emptied(lib):
    fx.load_report(lib, "iq", [row("ICONIQ", "State of Scaling", "2026", "<$10M", seg="Pacesetters", metric="Net $ retention")])
    sub = bp.Sub(intent="benchmark", text="typical net $ retention", metrics=["Net $ retention"])
    bp.gather(lib, sub)
    assert segs(sub) == ["Pacesetters"]          # fail open: nothing else would remain


def test_one_window_for_the_part_not_one_per_metric(lib):
    """id 109 sub 2: 'Annual Net Dollar Retention' is printed only by 2021 and 2022 editions, so its own call anchored
    on 2020/2022 and kept them while the other metric's call windowed on 2026."""
    for slug, rep, yr, per in [("a", "A 2026", "2026", "FY2025"), ("b", "B 2026", "2026", "FY2025"),
                               ("c", "C 2026", "2026", "FY2024")]:
        fx.load_report(lib, slug, [row("Src" + slug, rep, yr, "<$10M", per=per, metric="Net $ retention")])
    fx.load_report(lib, "kb21", [row("KeyBanc Capital Markets", "Private SaaS Company Survey", "2021", ">=$5M",
                                     seg=">=$5M 2020 ending ARR", per="2020", metric="Annual Net Dollar Retention", value="101.8")])
    fx.load_report(lib, "ov22", [row("OpenView", "2022 SaaS Benchmarks Report", "2022", "$5M-$20M", per="2022",
                                     metric="Annual Net Dollar Retention")])
    sub = bp.Sub(intent="benchmark", text="Annual Net Dollar Retention Net $ retention",
                 metrics=["Annual Net Dollar Retention", "Net $ retention"])
    bp.gather(lib, sub)
    assert {r["report_slug"] for r in sub.candidates} == {"a", "b", "c"}
    assert sub.window_left_out == 2 and sub.window_from == 2024


def clause_sub(left, frm):
    s = bp.Sub(intent="benchmark", text="x", metrics=[M])
    s.window_left_out, s.window_from = left, frm
    return s


def test_older_data_clause_names_the_main_part_only():
    assert bp.older_data_clause([clause_sub(214, 2025), clause_sub(55, 2024)]) == "Older data (before 2025) left out: 214 figures."
    assert bp.older_data_clause([clause_sub(0, 0), clause_sub(3, 2024)]) == "Older data (before 2024) left out: 3 figures."
    assert bp.older_data_clause([clause_sub(0, 0)]) == ""


# --- round-robin runs inside a match tier, never across tiers --------------------------------------------------

def _tier_row(key, report, match, band, yr="2025"):
    return {"figure_key": key, "report_slug": report, "report": report, "source": "Example Pub", "report_year": yr,
            "data_period": yr, "band": band, "metric": "S&M % of Revenue", "denominator": "% of revenue", "unit": "%",
            "statistic": "median", "value": "30", "value_num": 30.0, "match": match, "gaps": [], "basis_group": "revenue",
            "segment": "", "cited_source": "", "series": report, "band_context": match != "exact"}


def test_an_exact_band_row_is_not_displaced_by_a_far_band_report_taking_its_turn():
    """Report A has six exact rows, report B two exact rows, report C only far-band rows. Sorted best first (exact
    before adjacent_band). Cap 9: every exact row (8) fits, so only ONE of C's rows may go in, and none of the
    exact rows may be dropped to give C or B a 'turn'."""
    a = [_tier_row(10 + i, "rep-a", "exact", "$20M-$50M") for i in range(6)]
    b = [_tier_row(20 + i, "rep-b", "exact", "$20M-$50M") for i in range(2)]
    c = [_tier_row(30 + i, "rep-c", "adjacent_band", "$100M-$200M") for i in range(4)]
    rows = a + b + c                                   # already best first: exact tier, then the far tier
    picked = bp.pick_rows(rows, 9, "$20M-$50M")
    ks = {r["figure_key"] for r in picked}
    assert {r["figure_key"] for r in a + b} <= ks, "every exact-band row is kept"
    assert len(ks & {30, 31, 32, 33}) == 1


def test_far_band_report_takes_no_slot_while_a_second_exact_row_waits():
    """Cap 4: report A has three exact rows, report B has one exact row, report C only a far-band row. The four
    slots go to the four exact rows; C gets none, even though C's report has not had a turn."""
    a = [_tier_row(10 + i, "rep-a", "exact", "$20M-$50M") for i in range(3)]
    b = [_tier_row(20, "rep-b", "exact", "$20M-$50M")]
    c = [_tier_row(30, "rep-c", "adjacent_band", "$100M-$200M")]
    picked = bp.pick_rows(a + b + c, 4, "$20M-$50M")
    assert {r["figure_key"] for r in picked} == {10, 11, 12, 20}
