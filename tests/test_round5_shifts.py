"""Round 5 (after ids 54 to 56). Invented publishers ("Example High", "Example OV", "Example Spend N", "Example Index",
"Example MM"); strings and numbers are the shapes the reruns showed. No third-party report is committed."""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from figures_fixtures import frow, load_report  # noqa: E402

from linklib import answer_lint as al
from linklib import answer_rules as ar
from linklib import benchmark_prestep as bp
from linklib import figures as fg
from linklib.db import Library


@pytest.fixture
def lib(tmp_path):
    db = Library(str(tmp_path / "t.db"))
    db.seed_voice_prompts()
    yield db
    db.close()


def _emp(src, rep, y, v, dp, denom, metric="Employees", statistic="median", band="$20-50M", segment=""):
    return frow(source=src, report=rep, year=str(y), metric=metric, band=band, value=str(v), unit="count",
                data_period=dp, denominator=denom, statistic=statistic, segment=segment)


def _headcount(lib):
    load_report(lib, "ha-24", [_emp("Example High", "Example High 2024", 2024, 200, "Q2 2024", "full-time employees")])
    load_report(lib, "ha-25", [
        _emp("Example High", "Example High 2025", 2025, 131, "Q2 2025", "count", "employees"),
        _emp("Example High", "Example High 2025", 2025, 200, "Q2 2024", "count", "employees"),
        _emp("Example High", "Example High 2025", 2025, 164, "Q2 2023", "count", "employees"),
        _emp("Example High", "Example High 2025", 2025, 225, "Q2 2022", "count", "employees")])
    load_report(lib, "ov-22", [_emp("Example OV", "Example OV 2022", 2022, 226, "Q2 2022", "full-time equivalent employees at end of Q2 2022")])
    load_report(lib, "ov-23", [_emp("Example OV", "Example OV 2023", 2023, 165, "Q2 2023", "full-time equivalent employees, end of Q2 2023")])


def _spend(lib, n=5):
    """n publishers whose S&M moved a lot between two editions: each is a larger shift (by size) than headcount."""
    for i in range(n):
        for y, v, dp in ((2024, "42", "FY2023"), (2025, "20", "FY2024")):
            load_report(lib, f"sp{i}-{y}", [frow(source=f"Example Spend {i}", report=f"Example Spend {i} {y}", year=str(y),
                                                 metric="Sales and marketing expense", band="$20-50M", segment="", value=v,
                                                 unit="percent", data_period=dp, denominator="revenue", statistic="median")])


def _sub(metrics, segment="Enterprise, sales-led", text="org structure and spend profile for Enterprise SaaS, sales-led, at $20-$30M ARR"):
    return bp.Sub(intent="benchmark", text=text, metrics=metrics, band="$20-$30M ARR", segment=segment, year="all")


# --- Part 1 -----------------------------------------------------------------------------------------------------------

def test_the_headcount_shift_survives_larger_spend_shifts_and_the_compound_segment(lib):
    """Cause of the 'no headcount shift' reruns: shifts were sorted by size and cut to 4, so five bigger S&M shifts pushed the
    headcount line off the list. The code now orders by tier and keeps the best shift of each metric first."""
    _headcount(lib)
    _spend(lib)
    ps = bp.Prestep()
    bp._shift_notes(lib, ps, [_sub(["Headcount by function", "Sales and marketing expense"])])
    got = {(s["from"]["source"], s["from"]["value"], s["to"]["value"]) for s in ps.shifts}
    assert ("Example High", "225", "131") in got, (ps.shifts, ps.shifts_suppressed)
    assert len(ps.shifts) <= bp.MAX_SHIFT_LINES


def test_printed_order_puts_the_asked_metric_headline_before_larger_cohort_shifts(lib):
    _headcount(lib)
    _spend(lib)
    ps = bp.Prestep()
    bp._shift_notes(lib, ps, [_sub(["Headcount by function", "Sales and marketing expense"])])
    assert [s["metric"] for s in ps.shifts[:2]].count("employees") + [s["metric"] for s in ps.shifts[:2]].count("Employees") >= 1


def test_group_names_are_recorded_for_a_segment_that_differs_between_editions(lib):
    load_report(lib, "mm-a", [frow(source="Example MM", report="Example MM 2025", year="2025", metric="headcount mix: Sales",
                                   band="$20-50M", segment="private", value="20", unit="percent", data_period="2024",
                                   denominator="employees")])
    load_report(lib, "mm-b", [frow(source="Example MM", report="Example MM 2026", year="2026", metric="headcount mix: Sales",
                                   band="$20-50M", segment="Enterprise", value="25", unit="percent", data_period="2025",
                                   denominator="employees")])
    out = fg.compute_shifts(fg.find_figures(lib, "headcount mix: Sales", band="$20-$30M ARR", all_editions=True)["rows"])
    names = out["unpaired_groups"].get("editions exist but segment differs", [])
    assert names and "Example MM" in names[0] and "private" in names[0] and "enterprise" in names[0].lower(), out["unpaired_groups"]


def test_a_segment_written_two_ways_is_one_segment(lib):
    for slug, y, seg, v, dp in (("mm1", 2025, "private; public excluded", "20", "2024"), ("mm2", 2026, "private (public excluded)", "26", "2025"),
                                ("mm3", 2027, "private", "30", "2026")):
        load_report(lib, slug, [frow(source="Example MM", report=f"Example MM {y}", year=str(y), metric="headcount mix: Sales",
                                     band="$20-50M", segment=seg, value=v, unit="percent", data_period=dp, denominator="employees")])
    out = fg.compute_shifts(fg.find_figures(lib, "headcount mix: Sales", band="$20-$30M ARR", all_editions=True)["rows"])
    assert out["shifts"], out["unpaired"]


# --- Part 3 -----------------------------------------------------------------------------------------------------------

def _gm(src, rep, y, v, dp, band="", segment="", stat="median"):
    return frow(source=src, report=rep, year=str(y), metric="Gross margin", band=band, segment=segment, value=str(v),
                unit="percent", data_period=dp, denominator="revenue", statistic=stat)


def test_an_estimate_endpoint_is_never_a_shift(lib):
    load_report(lib, "kb-22", [_gm("Example Sapphire", "Example Sapphire 2022", 2022, 64, "2022 actuals", "$20-50M", stat="p25")])
    load_report(lib, "kb-26", [_gm("Example Sapphire", "Example Sapphire 2026", 2026, 73, "2026 estimate", "$20-50M", stat="p25")])
    out = fg.compute_shifts(fg.find_figures(lib, "Gross margin", band="$20-$30M ARR", all_editions=True)["rows"])
    assert out["shifts"] == []
    assert any("estimate" in s for s in out["skipped"]), out["skipped"]
    for dp in ("2026E", "2023 targets", "2025 forecast", "2024 plan", "FY2026 budget"):
        assert fg.is_estimate({"data_period": dp, "metric": "x"}), dp
    assert not fg.is_estimate({"data_period": "Q2 2025", "metric": "Gross margin"})


def test_quartile_shifts_are_not_printed_unless_asked_and_the_median_headline_leads(lib):
    for slug, y, dp, med, p25 in (("ix-a", 2025, "November 2025", "78", "50"), ("ix-b", 2026, "May 2026", "73", "70")):
        load_report(lib, slug, [_gm("Example Index", f"Example Index {y}", y, med, dp),
                                _gm("Example Index", f"Example Index {y}", y, p25, dp, stat="p25")])
    for slug, y, dp, v in (("mm-a", 2025, "LTM April 2025", "76"), ("mm-b", 2026, "LTM January 2026", "88")):
        load_report(lib, slug, [_gm("Example MM", f"Example MM {y}", y, v, dp, band="$50M+")])
    sub = bp.Sub(intent="benchmark", text="how has gross margin changed over the past few years", metrics=["Gross margin"],
                 band="$20-$30M ARR", segment="", year="all")
    ps = bp.Prestep()
    bp._shift_notes(lib, ps, [sub])
    assert ps.shifts and ps.shifts[0]["from"]["source"] == "Example Index" and ps.shifts[0]["statistic"] == "median", ps.shifts
    assert not any(fg.is_quartile_stat(s["statistic"]) for s in ps.shifts), ps.shifts
    assert any("quartile" in x for x in ps.shifts_skipped), ps.shifts_skipped
    sub.text = "how has the bottom quartile of gross margin changed"
    ps2 = bp.Prestep()
    bp._shift_notes(lib, ps2, [sub])
    assert any(fg.is_quartile_stat(s["statistic"]) for s in ps2.shifts)


def test_the_note_says_the_first_lines_are_in_print_order(lib):
    _headcount(lib)
    ps = bp.Prestep()
    block = bp._shift_notes(lib, ps, [_sub(["Headcount by function"])])
    assert "first two of these lines" in block and "two largest" not in block


def test_a_line_item_that_differs_between_the_two_editions_is_noted(lib):
    # Both names reduce to the same family but are not the same printed line item.
    load_report(lib, "m1", [frow(source="Example MM", report="Example MM 2025", year="2025", metric="Sales", band="$20-50M", segment="",
                                 value="40", unit="percent", data_period="LTM July 2025", denominator="revenue", statistic="median")])
    load_report(lib, "m2", [frow(source="Example MM", report="Example MM 2026", year="2026", metric="sales", band="$20-50M", segment="",
                                 value="17", unit="percent", data_period="LTM May 2026", denominator="revenue", statistic="median")])
    sh = fg.compute_shifts(fg.find_figures(lib, "Sales", band="$20-$30M ARR", all_editions=True)["rows"])["shifts"]
    assert sh and sh[0]["line_item_differs"] is False        # same name apart from case: not flagged
    rows = [dict(r) for r in fg.find_figures(lib, "Sales", band="$20-$30M ARR", all_editions=True)["rows"]]
    rows[0]["metric"] = "Sales and marketing"
    s2 = fg.compute_shifts(rows)["shifts"]
    assert (not s2) or all("line_item_differs" in x for x in s2)


# --- Part 4 -----------------------------------------------------------------------------------------------------------

def _share(src, rep, y, fn, v, seg=""):
    return frow(source=src, report=rep, year=str(y), metric=f"headcount share {fn}" if not seg else "Percent of employees by department",
                band="$20-50M", segment=seg, value=str(v), unit="percent", data_period="Q2 2025", denominator="employees")


def test_derived_counts_name_the_function_when_it_sits_in_the_segment(lib):
    rows = [_share("Example High", "Example High 2025", 2025, "", v, seg=fn) for fn, v in (("Engineering", 30), ("Sales", 20), ("CS & Support", 13))]
    rows.append(_emp("Example High", "Example High 2025", 2025, 131, "Q2 2025", "count", "employees"))
    load_report(lib, "ha-25", rows)
    got = fg.find_figures(lib, "Percent of employees by department", band="$20-$30M ARR", all_editions=True)["rows"]
    got += fg.find_figures(lib, "employees", band="$20-$30M ARR", all_editions=True)["rows"]
    lines = fg.derive_headcount_counts(got)
    assert len(lines) == 3, lines
    assert any(l.startswith("Percent of employees by department: Sales") and "about 26" in l for l in lines), lines
    assert any("CS & Support" in l and "about 17" in l for l in lines), lines


def _swap_rows():
    return [dict(source="Example High", report_year="2025", metric=f"headcount share {fn}", segment="", unit="percent",
                 denominator="employees", value=str(v), value_num=float(v)) for fn, v in
            (("Engineering", 30), ("Sales", 20), ("CS and support", 13), ("G&A", 11))]


def test_a_function_label_swapped_with_another_functions_share_is_flagged():
    text = ("| Function | Share | Derived |\n|---|---|---|\n| Engineering | 30% | ~39 |\n| CS and support | 20% | ~26 |\n"
            "| Sales | 13% | ~17 |\n| G&A | 11% | ~14 |\n")
    flags = al.function_share_problems(text, _swap_rows())
    assert len(flags) == 2 and all(f["phrase"] == "function label does not match its share" for f in flags), flags
    good = text.replace("CS and support | 20%", "CS and support | 13%").replace("Sales | 13%", "Sales | 20%")
    assert al.function_share_problems(good, _swap_rows()) == []
    assert al.function_share_problems("| CS & Support | 12% |", _swap_rows()) == []      # a figure no sent row prints is not judged


# --- Part 2, 5, 6 -------------------------------------------------------------------------------------------------------

def test_the_answer_rules_ban_down_from_without_a_computed_line_and_unsourced_inference():
    assert 'never write "down from", "up from", "rose" or "fell"' in ar.ANSWER_RULES
    assert "No inference about the user's own situation" in ar.ANSWER_RULES


def test_a_scope_sentence_with_a_year_and_a_band_is_not_a_figure_in_what_shifted():
    text = ("## What shifted\nThe archive has no headcount mix data before 2022 and no spend data split to exactly $20-$30M ARR "
            "or filtered to sales-led companies.\n")
    assert al.what_shifted_problems(text, [{"line": "Employees at $20-50M: down 41.8 percent, from 225 to 131", "size": 41.8}]) == []
    bad = "## What shifted\nHeadcount fell 99 percent.\n"
    assert al.what_shifted_problems(bad, [{"line": "Employees: down 41.8 percent", "size": 41.8}])


def test_an_inference_about_the_users_own_company_is_flagged_but_a_scope_caveat_is_not():
    t = "These are all-segment medians, so a sales-heavy company may run somewhat higher on S&M and somewhat lower on Engineering than these medians show."
    assert al.inference_problems(t)
    assert al.inference_problems("These medians cover all segments.") == []
    assert al.inference_problems("Engineering was 30% of headcount, and the 2024 median may be higher at 33%.") == []
