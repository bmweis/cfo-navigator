"""The answer leads into computed lines, and a note about what a source prints is not a "no change exists" claim."""
from linklib import answer_rules
from linklib.answer_lint import lint_answer, no_change_claim_problems

PHRASE = "no change exists claim with computed shifts"
# Neutral fixture: one computed line, invented values.
SHIFTS = [{"kind": "points", "size": 7.0, "line": "Example metric at $20M-$50M (median): down 7 points"}]

ID87_SENTENCE = ("No source in the archive prints a rate of change by expense line for the $20M-$50M band "
                 "specifically. The computed shifts below cover what can be shown.")


def _flagged(text, shifts=SHIFTS):
    return bool(no_change_claim_problems(text, shifts))


def test_must_not_flag_a_sentence_about_what_a_source_prints():
    assert not _flagged(ID87_SENTENCE)
    assert not _flagged("No report prints a growth rate for this band; the computed lines are derived from stored editions.")
    assert PHRASE not in [f["phrase"] for f in lint_answer(ID87_SENTENCE, shifts=SHIFTS)]


def test_must_still_flag_a_real_no_change_claim():
    assert _flagged("No source in the archive shows year-over-year change by expense line.")
    assert _flagged("The archive has no time series for these lines.")
    assert _flagged("No per-line change exists in the archive.")


def test_known_gap_prints_only_wording_is_not_flagged():
    # KNOWN GAP, not a pass: a real claim worded only in the "prints" form is let through by the verb exemption.
    # The exemption reverses part of the #795 wording; the sentence is literally true and the lint cannot otherwise
    # tell it from a real claim. This test records the current behaviour so a change to it is deliberate.
    assert not _flagged("No source prints year-over-year change by line, so growth can't be compared.")


def test_no_flag_without_computed_shifts_either_way():
    assert not _flagged("No source in the archive shows year-over-year change by expense line.", shifts=[])


def test_rules_lead_with_a_scope_sentence_then_the_lines_and_caveat_goes_after():
    full = answer_rules.HARD_RULES + answer_rules.ANSWER_RULES
    assert "the first sentence says the band and period and that the computed changes follow, with no number in it" in full
    assert "always after the lines, never before them and never twice" in full
    assert "Bad: \"No per-line change exists in the archive.\"" in full


def test_old_bad_example_is_gone_from_every_rule_text():
    for text in (answer_rules.HARD_RULES, answer_rules.ANSWER_RULES, answer_rules.SHAPE_RULES):
        assert "prints a rate of change by expense line" not in text
        assert "No source prints a rate" not in text
