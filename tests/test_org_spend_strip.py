"""Strip and lint gaps that survived PRs 757 and 758 (PR 759 brief, Part C). The sentences are the
shapes seen in ask_questions id 35, on invented numbers and invented publishers."""
import pytest

from linklib.answer_lint import lint_answer
from linklib.answer_strip import strip_answer

PAD = " ".join(["The newest figures are printed in the Example Benchmarks 2026 report."] * 6) + " [1]"


def _t(extra):
    return f"{PAD}\n\n{extra}"


def phrases(text):
    return [f["phrase"] for f in lint_answer(text)]


# --- strip: adverbs between "will" and the verb (uncited only) ---------------------------

@pytest.mark.parametrize("sentence", [
    "A sales-led enterprise company at this stage will almost certainly sit toward the upper end of the S&M quartile range.",
    "A company like yours will likely run above the median.",
    "A company like yours will typically be near the top.",
    "A company like yours will probably see a higher figure.",
    "A company like yours will sit near the top.",
])
def test_will_with_up_to_two_adverbs_is_stripped_when_uncited(sentence):
    clean, rec = strip_answer(_t(sentence))
    assert sentence not in clean
    assert [r["action"] for r in rec] == ["removed"]
    assert rec[0]["pattern"] == "forecast or advice"


def test_a_cited_will_sentence_is_not_stripped_but_is_flagged():
    s = "The median figures above will understate what a purely sales-led enterprise motion typically spends on go-to-market.[11]"
    clean, rec = strip_answer(_t(s))
    assert s in clean and rec == []
    assert "will understate" in phrases(s)


@pytest.mark.parametrize("sentence", [
    "The 2026 edition will be published in June, per the publisher.",    # pre-existing "will be" risk, kept as is
])
def test_documented_false_positive_of_will_be(sentence):
    clean, rec = strip_answer(_t(sentence))
    assert rec and rec[0]["pattern"] == "forecast or advice"


@pytest.mark.parametrize("sentence", [
    "The report will not change the figures.",
    "Sales will close deals in the quarter the report covers.",
])
def test_will_followed_by_other_verbs_is_left_alone(sentence):
    clean, rec = strip_answer(_t(sentence))
    assert sentence in clean and rec == []


# --- trend or characterization words: flag only, never stripped --------------------------

@pytest.mark.parametrize("sentence,phrase", [
    ("The compression is broad-based across all three line items, not just one function.", "broad-based"),
    ("The compression is broad-based across all three line items, not just one function.", "across all"),
    ("The mix shift is material for a company this size.", "material"),
    ("That is a meaningful step down from recent years [2].", "meaningful"),
    ("The drop was sharp [3].", "sharp"),
    ("A steep decline in the later years.", "steep"),
    ("The change is significant.", "significant"),
])
def test_trend_and_characterization_words_are_flagged(sentence, phrase):
    assert phrase in phrases(sentence)


def test_these_are_never_stripped_even_when_uncited():
    s = "The compression is broad-based across all three line items, not just one function."
    clean, rec = strip_answer(_t(s))
    assert s in clean and rec == []


def test_a_trend_word_in_a_sentence_with_figures_is_not_a_no_digit_flag():
    # "across all" with a number in the sentence is not the no-digit trend flag (documented limit).
    assert "across all" not in phrases("S&M was 35% in 2024 and 35% in 2025 across all three editions.")
    assert "broad-based" not in phrases("The decline was broad-based, from 40% to 35% in every line.")


# --- derived change without the label ----------------------------------------------------

@pytest.mark.parametrize("sentence", [
    "Headcount went from 226 to 165, a drop of 61 from Q2 2022.",
    "S&M is down 5 points from 2023.",
    "R&D is up 3 points between the two reports.",
    "The 2025 median is 12% higher than the 2024 median.",
])
def test_a_change_without_a_derived_label_is_flagged(sentence):
    assert "derived change without label" in phrases(sentence)


@pytest.mark.parametrize("sentence", [
    "Headcount went from 226 to 165, a drop of 61 (derived, not in report: 226 in Q2 2022 to 165 in Q2 2025).",
    "Headcount was 165 in Q2 2025.",
])
def test_a_labelled_change_or_no_change_is_not_flagged(sentence):
    assert "derived change without label" not in phrases(sentence)


def test_a_change_a_report_states_is_flagged_too_documented_false_positive():
    assert "derived change without label" in phrases("The report says revenue is up 12% [1].")


# --- the id 35 sentences, end to end through the lint and strip ---------------------------

def test_id35_sentences_are_recorded_the_right_way():
    text = _t("\n".join([
        "A sales-led enterprise company at this stage will almost certainly sit toward the upper end of the S&M quartile range, so plan for it.",
        "That is a meaningful step down from recent years [2].",
        "Headcount went from 226 to 165, a drop of 61 from Q2 2022.",
        "The compression is broad-based across all three line items, not just one function.",
        "So the median figures above will understate what a purely sales-led enterprise motion typically spends on go-to-market.[11]",
    ]))
    clean, rec = strip_answer(text)
    assert len(rec) == 1 and "almost certainly" in rec[0]["sentence"]
    assert "meaningful step down" in clean and "a drop of 61" in clean and "broad-based" in clean
    flags = phrases(clean)
    for want in ("meaningful", "derived change without label", "broad-based", "will understate"):
        assert want in flags, (want, flags)
