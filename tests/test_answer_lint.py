"""Retest fixes, Part C3: the answer-claim lint. Flag only; invented sentences.

Documented false positives are pinned so a change that fixes or worsens them is seen."""
import pytest

from linklib.answer_lint import lint_answer


def phrases(text):
    return [f["phrase"] for f in lint_answer(text)]


# The breaches the retest found (tests 1 and 3), reworded with invented numbers.
@pytest.mark.parametrize("text,phrase", [
    ("Spend was 38% of revenue, driven by the aggressive investment posture of the prior cycle.", "driven by"),
    ("Headcount of 140 people reflects the 2021 hiring environment.", "reflects"),
    ("Margins of 79% are likely to hold next year.", "likely"),
    ("The 38% ratio is almost certainly a stage effect.", "almost certainly"),
    ("A 25% share is a good threshold for a company this size.", "good threshold"),
    ("You should keep S&M near 38% at this stage.", "should"),
    ("Practical read: stay near the median.", "practical read"),
])
def test_breaches_are_flagged(text, phrase):
    assert phrase in phrases(text)


@pytest.mark.parametrize("text", [
    "Median gross margin was 79% in FY2024 and 81% in FY2025 at $20M-$50M ARR (Example Benchmarks, 2099).",
    "Spend was 38% of revenue, driven by hiring, according to the report [2].",   # a source states it, cited
    "Headcount is likely to differ by stage.",                                       # no number near the trigger
    "You should read the definition card first.",
])
def test_clean_text_is_not_flagged(text):
    assert phrases(text) == []


def test_practical_read_is_flagged_even_without_a_number_and_even_when_cited():
    assert phrases("The practical read here [1] is simple.") == ["practical read"]


def test_clean_answer_returns_empty_list():
    assert lint_answer("") == [] and lint_answer(None) == []


def test_known_false_positives():
    """Documented, not fixed: a neutral instruction that happens to sit near a number,
    and a cited clause in an uncited sentence."""
    assert phrases("Companies should report FTEs, not contractors, in the 140 row.") == ["should"]
    assert phrases("Spend was 38% of revenue [1], which is likely noisy.") == []          # [n] anywhere in the sentence clears it
    assert phrases("Spend was 38% of revenue. This is likely noise.") == []                # the number is in the other sentence
