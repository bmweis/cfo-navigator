"""Round 4 lint: a change the model writes itself (anywhere in the answer), the label check on a republished
row, and the size-order check on tables whose cells are values. Invented publishers; the sentence and the table
rows are the ones ask_questions id 53 showed."""
from linklib import answer_lint as al

ID53_SENTENCE = ("For $20–$50M ARR SaaS companies, the most recent total headcount median is 131 FTEs as of Q2 2025, "
                 "down from 200 in Q2 2024—both from High Alpha, citing different survey editions.")


def _phrases(text, shifts=None, rows=None):
    return [f["phrase"] for f in al.lint_answer(text, shifts if shifts is not None else [], rows)]


# --- Part 2: a model-written change ------------------------------------------------------------

def test_the_id_53_sentence_is_flagged_when_there_is_no_computed_line():
    assert "change written by the model with no computed line" in _phrases(ID53_SENTENCE, shifts=[])


def test_a_change_between_two_figures_is_flagged_in_any_wording():
    for s in ("Headcount fell from 78% to 73% over the period.",
              "It rose from 20 to 30 employees.",
              "Gross margin is up from 70% last edition.",           # one figure only: not a pair
              ):
        flagged = "change written by the model with no computed line" in _phrases(s, shifts=[])
        assert flagged == (s != "Gross margin is up from 70% last edition."), s


def test_a_levels_only_sentence_passes():
    s = "Example High 2025 reports 131 employees at $20–$50M ARR, and Example High 2024 reports 200; these are different editions."
    assert "change written by the model with no computed line" not in _phrases(s, shifts=[])


def test_the_sentence_passes_when_a_computed_line_carries_both_figures():
    shifts = [{"from": {"value": "200"}, "to": {"value": "131"}, "size": 34.5, "line": "Employees: down 34.5 percent, from 200 count to 131 count; derived from stored reports"}]
    assert "change written by the model with no computed line" not in _phrases(ID53_SENTENCE, shifts=shifts)


def test_a_sentence_with_a_derived_label_a_table_row_and_the_what_shifted_section_are_not_judged():
    assert "change written by the model with no computed line" not in _phrases(
        "Down from 200 to 131 (derived from stored reports).", shifts=[])
    assert "change written by the model with no computed line" not in _phrases(
        "| Employees | down from 200 to 131 | Q2 2025 |", shifts=[])
    inside = "## What shifted\nEmployees fell from 200 to 131.\n\n## Spend\nText."
    assert "change written by the model with no computed line" not in _phrases(inside, shifts=[])


def test_unknown_shifts_turns_the_check_off():
    assert al.change_claim_problems(ID53_SENTENCE, None) == []


def test_years_and_period_labels_are_not_figures():
    s = "Down from the 2024 edition to the 2025 edition, Q2 and Q3 aside."
    assert "change written by the model with no computed line" not in _phrases(s, shifts=[])


# --- Part 3 (lint fixes): the label check honours a republished row ---------------------------------

def _row(source, year, value, metric="Marketing", **kw):
    return {"source": source, "report_year": year, "value": value, "metric": metric, "also_printed_in": [],
            "cited_source": "", "series": source, **kw}


def test_a_republishers_row_that_cites_its_original_is_a_right_label_for_the_original_edition():
    rows = [_row("Example High", "2024", "7"), _row("Example High", "2025", "30", metric="Engineering"),
            _row("Example Jurassic", "2026", "7", cited_source="Example High 2025 SaaS Benchmarks", series="Example High")]
    text = "| Marketing | 7% | Q2 2025 | Example High 2025, also printed in Example Jurassic 2026[6] |"
    assert "table label does not match the figure" not in _phrases(text, rows=rows)


def test_a_wrong_edition_label_is_still_flagged():
    rows = [_row("Example High", "2024", "200", metric="Employees"), _row("Example High", "2025", "131", metric="Employees")]
    assert "table label does not match the figure" in _phrases("| 200 | Q2 2024 | Example High 2025[1] |", rows=rows)


# --- Part 3 (lint fixes): size order is judged on bands, not on values -----------------------------

HEADCOUNT = ("| Total FTEs | Band | Statistic | Period | Source |\n|---|---|---|---|---|\n"
             "| 131 | $20–50M ARR | Median | Q2 2025 | Example High 2025[1] |\n"
             "| 200 | $20–50M ARR | Median | Q2 2024 | Example High 2024[2] |\n"
             "| 113 | $20–50M ARR | Median | 2023 | Example Emergence 2024[3] |\n"
             "| 165 | $20–50M ARR | Median | Q2 2023 | Example OV 2023[4] |\n")

SPEND_OUT_OF_ORDER = ("| Line item | Value | Band | Source |\n|---|---|---|---|\n"
                      "| G&A | 26.9% | <$100M ARR | Example Index[8] |\n"
                      "| G&A | 18% | $5M–$25M ARR | Example Mostly[9] |\n")


def test_a_table_whose_bands_are_all_equal_is_not_out_of_order():
    assert al.table_order_problems(HEADCOUNT) == []


def test_values_in_the_first_cells_are_never_read_as_bands():
    assert al.table_order_problems("| 131 | x |\n|---|---|\n| 113 | y |\n") == []


def test_a_spend_table_whose_bands_run_out_of_size_order_is_still_flagged():
    assert [f["phrase"] for f in al.table_order_problems(SPEND_OUT_OF_ORDER)] == ["table rows out of size order"]


# --- Part 5: the closing "Left out" line only where a figures table is shown ---------------------------

def test_the_left_out_line_is_removed_from_an_answer_with_no_figures_table_and_the_removal_is_recorded():
    from linklib.answer_strip import drop_left_out_without_table
    text = ("CAC payback is the months of gross profit it takes to earn back the cost of winning a customer [1].\n\n"
            "Left out (not sent): Example Benchmarks 2099 4 rows, Sample Survey 2099 2 rows.")
    clean, rec = drop_left_out_without_table(text)
    assert "Left out" not in clean and clean.startswith("CAC payback")
    assert rec and rec[0]["pattern"] == "left-out line with no figures table" and rec[0]["action"] == "removed"


def test_the_left_out_line_stays_when_the_answer_has_a_figures_table():
    from linklib.answer_strip import drop_left_out_without_table
    text = "| a | b |\n|---|---|\n| 1 | 2 |\n\nLeft out (not sent): Example Benchmarks 2099 4 rows."
    assert drop_left_out_without_table(text) == (text, [])
