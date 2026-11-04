"""Part A2: editions that print the same figure with different wording must still pair, and every
unpaired candidate says why. Invented data only ("Sample Survey Co", "Example Benchmarks", "Example Index")."""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from figures_fixtures import frow, load_report  # noqa: E402

from linklib import answer_lint
from linklib import figures as fg
from linklib.db import Library

REG = ("canonical_metric\tcard_url\tcontains\n"
       "Gross Margin\thttps://c.example.test/gm\tContains: gross margin\n"
       "Total headcount\thttps://c.example.test/hc\tContains: employees, workforce size\n")


@pytest.fixture
def lib(tmp_path):
    db = Library(str(tmp_path / "t.db"))
    db.seed_voice_prompts()
    rows, err = fg.parse_registry_tsv(REG)
    assert not err
    db.seed_metric_registry(rows)
    yield db
    db.close()


def _shifts(lib, metric, band):
    res = fg.find_figures(lib, metric, band=band, all_editions=True, recent_window=True, limit=500)
    return fg.compute_shifts(res["rows"], lib.shift_rules())


def _emp(src, y, value, dp, denominator, band="$20-50M"):
    return frow(source=src, report=f"{src} {y}", year=str(y), metric="Employees", band=band, value=str(value),
                unit="count", data_period=dp, denominator=denominator)


def test_headcount_pairs_when_the_denominator_is_worded_differently_each_edition(lib):
    load_report(lib, "e-22", [_emp("Example Benchmarks", 2022, 226, "Q2 2022", "full-time equivalent employees at end of Q2 2022")])
    load_report(lib, "e-23", [_emp("Example Benchmarks", 2023, 165, "Q2 2023", "full-time equivalent employees, end of Q2 2023")])
    load_report(lib, "s-24", [_emp("Sample Survey Co", 2024, 200, "Q2 2024", "full-time employees")])
    load_report(lib, "s-25", [_emp("Sample Survey Co", 2025, 131, "Q2 2025", "full-time equivalent employees")])
    out = _shifts(lib, "Employees", "$30M")
    lines = " | ".join(s["line"] for s in out["shifts"])
    assert len(out["shifts"]) == 2, out
    assert "Example Benchmarks" in lines and "Sample Survey Co" in lines
    assert not any(s["directional"] for s in out["shifts"])


def _idx(report, year, dp, seg, denom, value, unit="%"):
    return frow(source="Example Index", report=report, year=year, metric="gross margin (LTM)", band="", basis="none",
                segment=seg, denominator=denom, value=str(value), unit=unit, data_period=dp)


def test_public_index_pairs_across_editions_that_word_the_segment_denominator_and_unit_differently(lib):
    load_report(lib, "i-25", [_idx("Index Club November 2025 edition", "2025", "LTM to 2025-11-15",
                                   "public index, top 10 median", "revenue", 78)])
    load_report(lib, "i-26", [_idx("Example Index Club May 2026 edition", "2026", "LTM through May 2026",
                                   "public; top 10 companies", "percent of revenue", 73, unit="percent")])
    out = _shifts(lib, "Gross Margin", "$30M")
    assert len(out["shifts"]) == 1 and "down 5 points" in out["shifts"][0]["line"], out


def test_change_figures_are_not_paired_as_levels(lib):
    # "change 2022 to 2023" rows are differences, not levels; two of them must not make a shift.
    load_report(lib, "e-23", [_emp("Example Benchmarks", 2023, -61, "change Q2 2022 to Q2 2023", "full-time equivalent employees")])
    load_report(lib, "e-24", [_emp("Example Benchmarks", 2024, 35, "change 2023 to 2024", "full-time employees")])
    out = _shifts(lib, "Employees", "$30M")
    assert out["shifts"] == [] and out["unpaired"].get("a change figure, not a level") == 2, out


def test_single_edition_means_one_edition_only_and_the_other_reasons_are_counted(lib):
    # Same publisher, two editions, but the band, segment, statistic and metric name each differ in one pair.
    load_report(lib, "e-22", [
        _emp("Example Benchmarks", 2022, 226, "Q2 2022", "full-time employees", band="$20-50M"),
        frow(source="Example Benchmarks", report="Example Benchmarks 2022", year="2022", metric="Employees", band="$10-20M",
             value="90", unit="count", data_period="Q2 2022", denominator="full-time employees"),
        frow(source="Example Benchmarks", report="Example Benchmarks 2022", year="2022", metric="Employees", band="$20-50M",
             value="250", unit="count", data_period="Q2 2022", denominator="full-time employees", statistic="p75"),
        frow(source="Example Benchmarks", report="Example Benchmarks 2022", year="2022", metric="Employees", band="$20-50M",
             value="220", unit="count", data_period="Q2 2022", denominator="full-time employees", segment="Fintech"),
        frow(source="Example Benchmarks", report="Example Benchmarks 2022", year="2022", metric="Workforce size", band="$20-50M",
             value="230", unit="count", data_period="Q2 2022", denominator="full-time employees"),
    ])
    load_report(lib, "e-23", [
        _emp("Example Benchmarks", 2023, 165, "Q2 2023", "full-time employees", band="$20-50M"),
        frow(source="Example Benchmarks", report="Example Benchmarks 2023", year="2023", metric="Employees", band="$50-100M",
             value="400", unit="count", data_period="Q2 2023", denominator="full-time employees"),
        frow(source="Example Benchmarks", report="Example Benchmarks 2023", year="2023", metric="Employees", band="$20-50M",
             value="200", unit="count", data_period="Q2 2023", denominator="full-time employees", statistic="p25"),
    ])
    out = _shifts(lib, "Total headcount", "")
    un = out["unpaired"]
    assert len(out["shifts"]) == 1                      # the clean $20-50M median pair
    assert un.get("editions exist but statistic differs", 0) >= 1, un
    assert un.get("editions exist but band differs", 0) >= 1, un
    assert un.get("editions exist but segment differs", 0) >= 1, un
    assert un.get("editions exist but metric name differs", 0) >= 1, un
    assert un.get("single edition") is None or un["single edition"] < sum(un.values()), un


def test_a_lone_edition_is_still_single_edition(lib):
    load_report(lib, "e-23", [_emp("Example Benchmarks", 2023, 165, "Q2 2023", "full-time employees")])
    out = _shifts(lib, "Employees", "$30M")
    assert out["unpaired"] == {"single edition": 1}, out


def test_a_conflict_in_one_report_names_metric_and_report_and_other_pairs_still_compute(lib):
    # High Alpha-shaped: the 2024 report prints 201 and 200 for one cell; the other publisher words its
    # denominator differently each year and must still pair.
    load_report(lib, "s-24", [_emp("Sample Survey Co", 2024, 201, "Q2 2024", "full-time employees"),
                              _emp("Sample Survey Co", 2024, 200, "Q2 2024", "full-time employees")])
    load_report(lib, "s-25", [_emp("Sample Survey Co", 2025, 131, "Q2 2025", "full-time equivalent employees")])
    load_report(lib, "e-22", [_emp("Example Benchmarks", 2022, 226, "Q2 2022", "full-time equivalent employees at end of Q2 2022")])
    load_report(lib, "e-23", [_emp("Example Benchmarks", 2023, 165, "Q2 2023", "full-time equivalent employees, end of Q2 2023")])
    out = _shifts(lib, "Employees", "$30M")
    assert len(out["skipped"]) == 1
    s = out["skipped"][0]
    assert "Employees" in s and "Sample Survey Co" in s and "2024" in s and "no shift was computed" in s
    assert len(out["shifts"]) == 1 and "Example Benchmarks" in out["shifts"][0]["line"]


def test_what_shifted_ends_before_the_left_out_line():
    shifts = [{"line": "S&M at $25M-$50M (median): down 17 points, from 55% to 38%", "size": 17.0}]
    text = ("Spend is covered above.\n\nWhat shifted\n- S&M at $25M-$50M: down 17 points, from 55% to 38% "
            "(derived from stored reports).\n\nLeft out (not sent): Example Benchmarks 2022 31 rows, Sample Survey Co 2024 12 rows.\n")
    flags = answer_lint.lint_answer(text, shifts)
    assert not any(f["phrase"] == "number in what shifted with no computed line" for f in flags), flags


def test_a_stray_number_inside_what_shifted_is_still_flagged():
    shifts = [{"line": "S&M at $25M-$50M (median): down 17 points, from 55% to 38%", "size": 17.0}]
    text = "What shifted\n- G&A was down 4 points.\n\nLeft out (not sent): Example Benchmarks 2022 31 rows.\n"
    flags = answer_lint.lint_answer(text, shifts)
    assert any(f["phrase"] == "number in what shifted with no computed line" for f in flags), flags


def _sm(src, y, band, value, dp, basis="Revenue", denom="percent of revenue"):
    return frow(source=src, report=f"{src} {y}", year=str(y), metric="S&M expense", band=band, basis=basis, value=str(value),
                unit="%", data_period=dp, denominator=denom)


def test_an_endpoint_far_from_other_publishers_is_flagged_not_changed(lib):
    load_report(lib, "k-22", [_sm("Sample Survey Co", 2023, "$25M-$50M", 55, "2022")])
    load_report(lib, "k-24", [_sm("Sample Survey Co", 2025, "$25M-$50M", 38, "2024")])
    load_report(lib, "b-22", [_sm("Example Benchmarks", 2023, "$20M-$50M", 35, "2022")])
    load_report(lib, "o-22", [_sm("Example Index", 2023, "$20M-$50M", 40, "2022", denom="percent of ARR")])
    out = _shifts(lib, "S&M expense", "$30M")
    assert len(out["shifts"]) == 1 and "down 17 points" in out["shifts"][0]["line"]      # the line is untouched
    assert len(out["outliers"]) == 1, out
    n = out["outliers"][0]
    assert n.startswith("endpoint outlier vs other publishers") and "55" in n and "35" in n and "40" in n


def test_no_outlier_note_when_other_publishers_agree(lib):
    load_report(lib, "k-22", [_sm("Sample Survey Co", 2023, "$25M-$50M", 55, "2022")])
    load_report(lib, "k-24", [_sm("Sample Survey Co", 2025, "$25M-$50M", 38, "2024")])
    load_report(lib, "b-22", [_sm("Example Benchmarks", 2023, "$20M-$50M", 53, "2022")])
    assert _shifts(lib, "S&M expense", "$30M")["outliers"] == []
