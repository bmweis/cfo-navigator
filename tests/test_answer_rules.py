"""Answer-call rules for benchmark turns (Parts B and C of the turn-20 follow-up).
These are instructions in the system prompt, so the tests pin that each rule is
present in the prompt a figures turn sends and absent from a turn with no
figures. Whether the model obeys is checked by reading real answers, which needs
an API key (see the PR)."""
import pytest

from linklib import agent
from linklib.agent import EFFORT_SETTINGS
from linklib.benchmark_prestep import PRESTEP_RULES


def _system(benchmark_rules):
    class _Lib:
        def get_setting(self, k):
            return "voice text"
    import linklib.agent as a
    return a._build_system(True, False, False, _Lib(), benchmark_rules=benchmark_rules)


# Rules about claims and the shape of an answer hold on EVERY turn (retest fixes, Part C2):
# the $20-$30M turn ran no lookup and was answered without them.
EVERY_TURN = {
    "lead with sentences, publisher and year inside": ["3 to 5 sentences", "publisher and year inside the sentence"],
    "at most one small table, six rows": ["at most one small table", "Six rows at most"],
    "one line on what is missing": ["what the data does not cover"],
    "no practical read, advice, commentary, multiples": ["No \"practical read\"", "investor multiples"],
    "no illustrative figures as benchmarks": ["illustrative or worked-example company is not a benchmark"],
    "no unsupported causes": ["Do not state a cause", "attribute it to that source by name"],
    "no invented numbers": ["Never state a number, range or headcount that no source gives"],
    "derived labelled, stored preferred": ["derived, not in report", "show the inputs", "use the stored figure"],
    "medians do not add, no mixed bands": ["Medians do not add", "Do not mix bands in one table"],
    "nothing about the future or an unstated market shift": ["Say nothing about the future", "market shift"],
    "no unasked recommendation": ["Give no recommendation unless the question asks for one"],
    "disagreeing publishers both shown": ["When two publishers disagree, show both"],
    "date every source": ["year of every source", "more than three years old"],
}
# Figures mechanics stay in the figures block.
FIGURES_ONLY = {
    "table columns": ["metric, band as printed, statistic", "value, report and year, match note"],
    "period exactly as given": ["exactly as the figures give it", "only from numbers shown", "Count the years"],
    "newest edition and change table": ["newest edition", "every edition in one small table, oldest to newest"],
}


@pytest.mark.parametrize("name", list(EVERY_TURN))
@pytest.mark.parametrize("figures", [True, False])
def test_claim_and_shape_rules_are_on_every_turn(name, figures):
    system = _system(figures)
    for phrase in EVERY_TURN[name]:
        assert phrase in system, f"{name}: missing {phrase!r}"


@pytest.mark.parametrize("name", list(FIGURES_ONLY))
def test_figures_mechanics_are_in_the_prompt_of_a_figures_turn(name):
    system = _system(True)
    for phrase in FIGURES_ONLY[name]:
        assert phrase in system, f"{name}: missing {phrase!r}"


def test_figures_mechanics_are_not_sent_without_figures():
    system = _system(False)
    assert PRESTEP_RULES not in system and "metric, band as printed" not in system


def test_the_old_figures_only_table_first_rule_is_gone():
    assert "Put the figures first" not in PRESTEP_RULES and "Put the figures first" not in _system(True)


def test_only_one_label_for_derived_numbers():
    assert "derived, not in the reports" not in PRESTEP_RULES


def test_standard_ceiling_is_2500_and_the_others_are_unchanged():
    assert EFFORT_SETTINGS["standard"]["max_tokens"] == 2500
    assert EFFORT_SETTINGS["quick"]["max_tokens"] == 700
    assert EFFORT_SETTINGS["deep"]["max_tokens"] == 4000
