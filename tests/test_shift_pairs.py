"""Computed shifts: the pair shapes that produced nothing (follow-up to #765). Invented data only
("Sample Survey Co", "Example Index")."""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from figures_fixtures import frow, load_report  # noqa: E402

from linklib import answer_lint
from linklib import benchmark_prestep as bp
from linklib import figures as fg
from linklib.db import Library

REG = ("canonical_metric\tcard_url\tcontains\n"
       "Gross Margin\thttps://c.example.test/gm\tContains: gross margin\n"
       "Total headcount\thttps://c.example.test/hc\tContains: headcount, total employees, employees\n")


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


def _emp(src, y, band, value, dp):
    return frow(source=src, report=f"{src} {y}", year=str(y), metric="Employees", band=band, value=str(value),
                unit="count", data_period=dp)


def test_a_band_printed_two_ways_is_one_series(lib):
    load_report(lib, "s-2024", [_emp("Sample Survey Co", 2024, "$20M-$50M", 200, "Q2 2024")])
    load_report(lib, "s-2025", [_emp("Sample Survey Co", 2025, "$20-50M", 131, "Q2 2025")])
    out = _shifts(lib, "Employees", "$30M")
    assert len(out["shifts"]) == 1 and "down 34.5 percent" in out["shifts"][0]["line"]
    assert out["shifts"][0]["directional"] is False


def test_a_public_index_with_no_size_band_pairs_with_itself(lib):
    load_report(lib, "i-25", [frow(source="Example Index", report="Index Nov 2025", year="2025", metric="Gross Margin",
                                   band="", basis="none", segment="Top 10", value="78", unit="%", data_period="Nov 2025")])
    load_report(lib, "i-26", [frow(source="Example Index", report="Index May 2026", year="2026", metric="Gross Margin",
                                   band="", basis="none", segment="Top 10", value="73", unit="%", data_period="May 2026")])
    out = _shifts(lib, "Gross Margin", "$30M")          # a size was asked; the index prints none
    assert len(out["shifts"]) == 1 and "down 5 points" in out["shifts"][0]["line"]


def test_a_five_points_apart_yields_a_line(lib):
    load_report(lib, "p-24", [frow(source="Sample Survey Co", report="Sample 2024", year="2024", metric="Gross Margin",
                                   band="$20M-$50M", value="70", unit="%", data_period="FY2023")])
    load_report(lib, "p-25", [frow(source="Sample Survey Co", report="Sample 2025", year="2025", metric="Gross Margin",
                                   band="$20M-$50M", value="75", unit="%", data_period="FY2024")])
    assert "up 5 points" in _shifts(lib, "Gross Margin", "$30M")["shifts"][0]["line"]


def test_a_conflicting_cell_skips_that_pair_and_says_so(lib):
    load_report(lib, "s-2024", [_emp("Sample Survey Co", 2024, "$20M-$50M", 201, "Q2 2024"),
                                _emp("Sample Survey Co", 2024, "$20M-$50M", 200, "Q2 2024")])
    load_report(lib, "s-2025", [_emp("Sample Survey Co", 2025, "$20-50M", 131, "Q2 2025")])
    out = _shifts(lib, "Employees", "$30M")
    assert out["shifts"] == [] and "no shift was computed" in out["skipped"][0]


def test_a_every_unpaired_candidate_gets_a_counted_reason(lib):
    load_report(lib, "s-2025", [_emp("Sample Survey Co", 2025, "$20M-$50M", 131, "Q2 2025")])
    out = _shifts(lib, "Employees", "$30M")
    assert out["shifts"] == [] and out["unpaired"] == {"single edition": 1}
    load_report(lib, "s-2024", [_emp("Sample Survey Co", 2024, "$20M-$50M", 150, "Q2 2025")])
    assert _shifts(lib, "Employees", "$30M")["unpaired"] == {"same data period in both editions": 1}


def test_a_a_single_publisher_pair_wins_and_nothing_is_chained(lib):
    load_report(lib, "a-2022", [_emp("Sample Survey Co", 2022, "$20M-$50M", 226, "Q2 2022")])
    load_report(lib, "a-2023", [_emp("Sample Survey Co", 2023, "$20M-$50M", 165, "Q2 2023")])
    load_report(lib, "b-2025", [_emp("Example Benchmarks", 2025, "$20M-$50M", 131, "Q2 2025")])
    out = _shifts(lib, "Employees", "$30M")
    assert len(out["shifts"]) == 1 and out["shifts"][0]["directional"] is False
    assert "226" in out["shifts"][0]["line"] and "131" not in out["shifts"][0]["line"]


def test_a_the_notes_say_so_when_there_are_no_shifts(lib):
    load_report(lib, "s-2025", [_emp("Sample Survey Co", 2025, "$20M-$50M", 131, "Q2 2025")])
    ps = bp.Prestep(active=True)
    block = bp._shift_notes(lib, ps, [bp.Sub(intent="benchmark", text="employees", metrics=["Employees"], band="$30M")])
    assert block == bp.NO_SHIFTS_NOTE and "no \"what changed\" claim" in block
    assert ps.shifts_unpaired == {"single edition": 1} and "shifts_unpaired" in ps.log()


def test_d_what_shifted_may_hold_only_computed_lines():
    shifts = [{"size": 5.0, "line": "Gross Margin at no band (median): down 5 points, from 78% (X, Nov 2025) to 73% (X, May 2026)"}]
    good = "What shifted\n- Down 5 points, from 78% to 73% (derived from stored reports).\n\nNext"
    assert answer_lint.lint_answer(good, shifts=shifts) == []
    bad = "## What shifted\n- A drop of 95 from 226 to 131.\n"
    assert any(f["phrase"] == "number in what shifted with no computed line" for f in answer_lint.lint_answer(bad, shifts=shifts))
    assert any(f["phrase"] == "what shifted without computed lines" for f in answer_lint.lint_answer("What shifted\n- Down 9.\n", shifts=[]))
    assert not any(f["phrase"].startswith("what shifted") for f in answer_lint.lint_answer("What shifted\n- Down 9.\n", shifts=None))


def test_d_directionally_consistent_is_a_characterization_flag():
    flags = answer_lint.lint_answer("The sentence is directionally consistent with a smaller team.")
    assert any(f["phrase"] == "directionally consistent" for f in flags)
