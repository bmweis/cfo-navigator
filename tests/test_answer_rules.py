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


RULES = {
    "table first": ["one compact table", "metric, band as printed, statistic", "value, report and year, match note"],
    "short prose, full sentence": ["keep the prose short", "end on a full sentence"],
    "period exactly as given": ["exactly as the figures give it", "only from numbers shown", "Count the years"],
    "no unsupported causes": ["Do not state a cause", "attribute it to that source by name"],
    "derived labelled, stored preferred": ["derived, not in report", "show the inputs", "use the stored figure"],
    "newest edition and change table": ["newest edition", "every edition in one small table, oldest to newest"],
    "date every source": ["year of every source", "more than three years old"],
}


@pytest.mark.parametrize("name", list(RULES))
def test_rule_is_in_the_prompt_of_a_figures_turn(name):
    system = _system(True)
    for phrase in RULES[name]:
        assert phrase in system, f"{name}: missing {phrase!r}"


def test_rules_are_not_sent_without_figures():
    system = _system(False)
    assert "derived, not in report" not in system and PRESTEP_RULES not in system


def test_only_one_label_for_derived_numbers():
    assert "derived, not in the reports" not in PRESTEP_RULES


def test_standard_ceiling_is_2500_and_the_others_are_unchanged():
    assert EFFORT_SETTINGS["standard"]["max_tokens"] == 2500
    assert EFFORT_SETTINGS["quick"]["max_tokens"] == 700
    assert EFFORT_SETTINGS["deep"]["max_tokens"] == 4000
