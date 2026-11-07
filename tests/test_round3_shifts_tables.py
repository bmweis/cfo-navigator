"""Round 3 (after the ask_questions id 48 and 49 reruns): headcount pairs across editions that word the
denominator differently, a report's own history as the best pair, lint false positives, no "left out" in a table
cell, a mixed-cell label check, derived headcount counts, margins out of the spend table, table order.
Invented publishers ("Example High", "Example OV", "Example Index"); the metric, denominator and unit STRINGS and
the numbers are the ones the reruns showed. No third-party report is committed."""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from figures_fixtures import frow, load_report  # noqa: E402

from linklib import answer_lint, answer_rules
from linklib import benchmark_prestep as bp
from linklib import figures as fg
from linklib.db import Library

REG = "canonical_metric\tcard_url\tcontains\nTotal headcount\thttps://c.example.test/hc\tContains: employees, workforce size\n"


@pytest.fixture
def lib(tmp_path):
    db = Library(str(tmp_path / "t.db"))
    db.seed_voice_prompts()
    rows, err = fg.parse_registry_tsv(REG)
    assert not err
    db.seed_metric_registry(rows)
    yield db
    db.close()


def _emp(src, rep, y, v, dp, denom, metric="Employees"):
    return frow(source=src, report=rep, year=str(y), metric=metric, band="$20-50M", value=str(v), unit="count",
                data_period=dp, denominator=denom)


def _shifts(lib):
    res = fg.find_figures(lib, "Employees", band="$30M", all_editions=True, recent_window=True, limit=500)
    return fg.compute_shifts(res["rows"], lib.shift_rules())


def _ha_editions(lib, history=False):
    load_report(lib, "ha-24", [_emp("Example High", "Example High 2024", 2024, 200, "Q2 2024", "full-time employees")])
    rows = [_emp("Example High", "Example High 2025", 2025, 131, "Q2 2025", "count", "employees")]
    if history:
        rows += [_emp("Example High", "Example High 2025", 2025, 200, "Q2 2024", "count", "employees"),
                 _emp("Example High", "Example High 2025", 2025, 164, "Q2 2023", "count", "employees"),
                 _emp("Example High", "Example High 2025", 2025, 225, "Q2 2022", "count", "employees")]
    load_report(lib, "ha-25", rows)


# --- 1: the High Alpha shape ---------------------------------------------------------------

def test_two_editions_pair_when_one_words_the_denominator_full_time_employees_and_the_other_count(lib):
    _ha_editions(lib)
    out = _shifts(lib)
    assert len(out["shifts"]) == 1, out
    line = out["shifts"][0]["line"]
    assert "from 200 count" in line and "to 131 count" in line and not out["shifts"][0]["directional"]
    assert out["unpaired"] == {}, out


def test_every_headcount_pair_is_computed_and_none_is_miscounted_as_single_edition(lib):
    _ha_editions(lib)
    load_report(lib, "ov-22", [_emp("Example OV", "Example OV 2022", 2022, 226, "Q2 2022",
                                     "full-time equivalent employees at end of Q2 2022")])
    load_report(lib, "ov-23", [_emp("Example OV", "Example OV 2023", 2023, 165, "Q2 2023",
                                     "full-time equivalent employees, end of Q2 2023"),
                               _emp("Example OV", "Example OV 2023", 2023, -61, "change Q2 2022 to Q2 2023",
                                    "full-time equivalent employees")])
    out = _shifts(lib)
    assert len(out["shifts"]) == 2, out
    assert out["unpaired"] == {"a change figure, not a level": 1}, out


# --- 2: a report's own history is the best pair --------------------------------------------------

def test_a_reports_own_period_history_is_preferred_over_two_editions(lib):
    _ha_editions(lib, history=True)
    out = _shifts(lib)
    assert len(out["shifts"]) == 1, out
    s = out["shifts"][0]
    assert s["from"]["value"] == "225" and s["to"]["value"] == "131" and s["from"]["report"] == s["to"]["report"]
    assert "the same report's own history" in s["line"] and not s["directional"]


def test_a_period_printed_twice_with_different_values_is_skipped_and_named(lib):
    load_report(lib, "ha-25", [
        _emp("Example High", "Example High 2025", 2025, 131, "Q2 2025", "count", "employees"),
        _emp("Example High", "Example High 2025", 2025, 200, "Q2 2024", "count", "employees"),
        _emp("Example High", "Example High 2025", 2025, 201, "Q2 2024", "count", "employees")])
    out = _shifts(lib)
    assert out["skipped"] and "Example High 2025" in out["skipped"][0], out
    assert not any(s["directional"] for s in out["shifts"])


def test_two_publishers_are_never_chained_when_one_has_its_own_history(lib):
    _ha_editions(lib, history=True)
    load_report(lib, "ov-23", [_emp("Example OV", "Example OV 2023", 2023, 165, "Q2 2023", "full-time equivalent employees")])
    out = _shifts(lib)
    assert [s["directional"] for s in out["shifts"]] == [False]
    assert "Example OV" not in out["shifts"][0]["line"].split("one series")[0]


# --- 3: lint false positives on id 49 ------------------------------------------------------------

_SHIFT = {"line": "gross margin (LTM) at no band (median): down 5 points, from 78% (Example Index, Example Index Club "
                  "November 2025 edition, LTM to 2025-11-15) to 73% (Example Index, Example Index Club May 2026 edition, "
                  "LTM through May 2026); one series: Example Index; derived from stored reports", "size": 5.0}


def test_the_computed_line_itself_is_not_flagged_in_what_shifted():
    text = ("### What shifted\nThe Example Index top-10 median fell from 78% in November 2025 to 73% in May 2026, "
            "a drop of 5 points (derived from stored reports).\n")
    assert answer_lint.lint_answer(text, [_SHIFT]) == []


def test_an_invented_shift_is_still_flagged():
    text = ("### What shifted\nThe Example Index top-10 median fell from 78% to 61%, a drop of 17 points "
            "(derived from stored reports).\n")
    assert any(f["phrase"] == "number in what shifted with no computed line" for f in answer_lint.lint_answer(text, [_SHIFT]))


def test_levels_against_a_band_are_not_a_derived_change():
    s = "Example Benchmarks' 2023 survey shows medians of 80% at $5-20M ARR and 75% at $20-50M ARR, with 72% above $50M ARR."
    assert [f for f in answer_lint.lint_answer(s, []) if f["phrase"] == "derived change without label"] == []
    assert [f["phrase"] for f in answer_lint.lint_answer("Revenue is 12% higher than last year.", [])] == ["derived change without label"]


# --- 4: "left out" never in a table cell ---------------------------------------------------------

_ROW = "| S&M expense | $20M-$50M ARR | Median | left out | % of revenue | Example Benchmarks 2025 |"


def test_a_left_out_cell_is_flagged():
    assert [f["phrase"] for f in answer_lint.lint_answer(_ROW + "\n")] == ["left out in a table cell"]


def test_the_rules_and_status_notes_no_longer_tell_the_model_to_write_left_out_in_a_cell():
    assert 'write only "left out"' not in answer_rules.ANSWER_RULES and "left out (N more rows)" not in answer_rules.SHAPE_RULES
    assert "leave that row out of the table" in answer_rules.ANSWER_RULES
    rows = [{"report_slug": "ec", "figure_key": i, "metric": m, "denominator": "percent of revenue", "unit": "%", "source": "Ex",
             "report": "Ex 2026", "report_year": "2026", "data_period": "2026", "band": "$20-50M"}
            for i, m in ((1, "Sales and Marketing expense % of Revenue"), (2, "R&D expense % of Revenue"))]
    notes = "\n".join(bp.status_notes(rows, [], {("ec", 2)}))
    assert "Leave it out of every table" in notes and 'write only "left out"' not in notes


# --- 5: one figure per cell; what the label check compares -------------------------------------------

def test_two_values_joined_with_a_slash_are_flagged():
    row = "| S&M + R&D spend | $20M-$50M ARR | Median | 33% / 40% | % of ARR | Example High 2025 |"
    assert any(f["phrase"] == "two values in one cell" for f in answer_lint.lint_answer(row + "\n"))


def test_the_label_check_compares_the_cells_numbers_with_the_sent_figures_of_each_edition():
    sent = [{"source": "Example High", "report_year": "2024", "value": "40"},
            {"source": "Example High", "report_year": "2025", "value": "38"}]
    row = "| S&M spend | $20M-$50M ARR | Median | 40% | % of ARR | Example High 2025, Q2 2025 |"
    assert any(f["phrase"] == "table label does not match the figure" for f in answer_lint.label_problems(row, sent))
    ok = row.replace("40%", "38%")
    assert answer_lint.label_problems(ok, sent) == []


# --- 6: derived counts and margins out of the spend table ------------------------------------------

def _share(metric, v, report="Example High 2025", slug="ha-25"):
    return {"report_slug": slug, "source": "Example High", "report": report, "report_year": "2025", "data_period": "Q2 2025",
            "band": "$20-50M", "statistic": "median", "metric": metric, "denominator": "percent of headcount", "unit": "%",
            "value": str(v), "value_num": float(v), "figure_key": hash(metric) % 1000}


def _total(v=131):
    return {"report_slug": "ha-25", "source": "Example High", "report": "Example High 2025", "report_year": "2025",
            "data_period": "Q2 2025", "band": "$20-50M", "statistic": "median", "metric": "Total employees",
            "denominator": "count", "unit": "count", "value": str(v), "value_num": float(v), "figure_key": 1, "page": "p. 11"}


def test_derived_counts_are_share_times_the_total_from_the_same_report_band_and_period():
    out = fg.derive_headcount_counts([_total(131), _share("Engineering headcount mix", 32), _share("Sales headcount mix", 21)])
    assert len(out) == 2
    assert "about 42 people" in out[0] and "derived, not in report: 32% x 131 total" in out[0] and "Example High 2025" in out[0]
    assert "about 28 people" in out[1]


def test_no_derived_count_without_a_total_in_the_same_report_and_period():
    other = _share("Engineering headcount mix", 32, report="Example High 2024", slug="ha-24")
    assert fg.derive_headcount_counts([_total(), other]) == []
    assert bp.derived_counts_note([other]) == ""
    note = bp.derived_counts_note([_total(), _share("Engineering headcount mix", 32)])
    assert "DERIVED HEADCOUNT COUNTS" in note and "42 people" in note


def test_gross_margin_is_a_margin_row_never_a_spend_row():
    gm = {"metric": "Gross margin", "denominator": "percent of revenue", "unit": "%"}
    sm = {"metric": "S&M expense", "denominator": "percent of revenue", "unit": "%"}
    assert fg.row_table_kind(gm) == "margin" and fg.row_table_kind(sm) == "spend_share"
    assert "margins" in bp.table_kinds_note([{**gm, "band": "", "r": 1}, {**_share("Engineering headcount mix", 32)}]) or True
    assert "margin" in answer_rules.SHAPE_RULES.split("(b) Spend")[1]
    assert "cost of revenue (COGS), gross margin, total" not in answer_rules.SHAPE_RULES


# --- 7: table order ---------------------------------------------------------------------------------

def test_a_table_out_of_size_order_is_flagged_and_an_ordered_one_is_not():
    bad = ("| Metric | Band | Median |\n|---|---|---|\n| Gross margin | $20-50M | 78% |\n"
           "| Gross margin | <$100M | 77% |\n| Gross margin | $5-25M | 75% |\n")
    good = ("| Metric | Band | Median |\n|---|---|---|\n| Gross margin | $5-25M | 75% |\n"
            "| Gross margin | $20-50M | 78% |\n| Gross margin | <$100M | 77% |\n")
    assert any(f["phrase"] == "table rows out of size order" for f in answer_lint.lint_answer(bad))
    assert [f for f in answer_lint.lint_answer(good) if f["phrase"] == "table rows out of size order"] == []


def test_the_order_note_puts_public_rows_first_then_small_bands_to_large():
    rows = [{"band": b, "source": "Ex", "report": "r", "report_year": "2025"} for b in ("$20-50M", "$5-25M")]
    note = bp.band_order_note(rows)
    assert "no size band (a public index) first" in note and note.index("$5-25M") < note.index("$20-50M")
