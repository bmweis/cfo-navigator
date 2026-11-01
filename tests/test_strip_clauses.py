"""Part D (2026-11): clause-level strip, the added patterns, flag-only lint for a cited inference.
Invented text only. Shaped like ask_questions id 31 and 32."""
import json

import pytest

from linklib.answer_lint import lint_answer
from linklib.answer_strip import UNCITED_CLAIM, split_clause, strip_answer

PAD = " ".join(["The newest figures are printed in the Example Benchmarks 2026 report."] * 6) + " [1]"


def _strip(sentence):
    return strip_answer(f"{PAD}\n\n{sentence}")


def test_the_figure_clause_stays_and_only_the_cause_goes():
    s = ("Sample Survey Co total-population figures show total gross margin at 77% versus subscription gross "
         "margin at 81% — the gap reflects professional services, which runs at a 30% median margin.")
    clean, rec = _strip(s)
    assert "gross margin at 81%." in clean and "reflects" not in clean and "30% median" not in clean
    assert len(rec) == 1 and rec[0]["action"] == "clause_removed"
    assert rec[0]["sentence"] == "the gap reflects professional services, which runs at a 30% median margin."
    assert rec[0]["kept"].endswith("81%.")


def test_a_comma_splits_too_and_the_sentence_is_closed_cleanly():
    clean, rec = _strip("Median S&M was 38% of revenue at this size, driven by sales headcount in the period.")
    assert clean.endswith("Median S&M was 38% of revenue at this size.") and rec[0]["action"] == "clause_removed"


def test_no_figure_before_the_flagged_clause_means_the_whole_sentence_goes():
    clean, rec = _strip("Costs rose, driven by hiring in the period.")
    assert "Costs rose" not in clean and rec[0]["action"] == "removed"


def test_a_flag_in_the_first_clause_removes_the_whole_sentence():
    clean, rec = _strip("Driven by hiring, S&M reached 38% of revenue.")
    assert "38%" not in clean and rec[0]["action"] == "removed"


def test_a_number_with_a_comma_is_not_a_separator():
    clean, rec = _strip("Total headcount was 1,200 people because of the 2025 hiring plan.")
    assert rec[0]["action"] == "removed"                # no clause break before the flagged words


def test_a_cause_stated_with_figures_is_recorded_under_its_own_name():
    clean, rec = _strip("Headcount fell 12% in 2025 because of automation.")
    assert rec[0]["pattern"] == UNCITED_CLAIM and rec[0]["action"] == "removed"
    assert "automation" not in clean


def test_the_id_31_sentence_is_still_removed_and_recorded():
    clean, rec = _strip("That median is down materially from prior years—67% of $20-$50M companies reported "
                        "cutting headcount because of AI, with engineering the most frequently reduced function.")
    assert "67%" not in clean and rec[0]["pattern"] == UNCITED_CLAIM


@pytest.mark.parametrize("sentence,pattern", [
    ("A sales-led motion will sit at the higher end of S&M spend.", "forecast or advice"),
    ("The ratio will be lower next year.", "forecast or advice"),
    ("Spend will run above the median.", "forecast or advice"),
    ("Teams will see a drop.", "forecast or advice"),
    ("Please confirm whether it is total or subscription only.", "forecast or advice"),
    ("The low-to-mid 70s is the floor for a healthy SaaS business.", "characterization"),
    ("That range is attractive to investors.", "characterization"),
])
def test_new_patterns_remove_an_uncited_sentence(sentence, pattern):
    clean, rec = _strip(sentence)
    assert sentence not in clean and rec[0]["pattern"] == pattern


@pytest.mark.parametrize("sentence", [
    # false-positive notes: a plain statement of what a source or table does is kept
    "The figures are for the 2026 survey.",
    "Each table row shows the median and the sample size.",
    "Sources are listed below the answer.",
    "The report will be linked under the answer [2].",             # cited: never stripped
])
def test_ordinary_sentences_are_kept(sentence):
    clean, rec = _strip(sentence)
    assert sentence in clean and rec == []


def test_a_cited_sentence_with_a_new_pattern_is_not_stripped():
    s = "The 70-80%+ range is what makes the SaaS model attractive to investors [6]."
    clean, rec = _strip(s)
    assert s in clean and rec == []


def test_split_clause_none_cases():
    assert split_clause("Plain sentence with no flagged words.") is None
    assert split_clause("S&M was 38% of revenue.") is None


# --- flag-only lint for an inference after a cited figure ---------------------------------------

@pytest.mark.parametrize("sentence,phrase", [
    ("S&M was 38% of revenue [3]—meaning private companies run heavier on R&D than public peers.", "meaning"),
    ("G&A was 13% of revenue [3], meaning that the base is high.", "meaning"),
    ("Spend rose 4 points [2], which means the trend continues.", "which means"),
    ("The 70-80%+ range is what makes the model attractive to investors [6].", "attractive to investors"),
])
def test_cited_inferences_are_flagged_not_stripped(sentence, phrase):
    assert phrase in [f["phrase"] for f in lint_answer(sentence)]
    clean, rec = _strip(sentence)
    assert sentence in clean and rec == []                     # a cited sentence is never stripped


def test_a_plain_cited_figure_is_not_flagged():
    assert lint_answer("S&M was 38% of revenue [3].") == []
    # "meaningful" used to be the example of an unflagged cited word; PR 759 flags it (Part C3), so a
    # different plain cited sentence stands in.
    assert lint_answer("The word report appears here [2].") == []


def test_admin_row_shows_a_clause_removal_with_what_was_kept():
    from webapp.app import _stripped_html
    html = _stripped_html({"stripped_json": json.dumps([
        {"sentence": "the gap reflects services.", "pattern": "causal", "action": "clause_removed",
         "kept": "Margin was 77% versus 81%."}])})
    assert "1 sentence removed (1 cut back to its figure clause)" in html
    assert "the gap reflects services." in html and "Kept: Margin was 77% versus 81%." in html
