"""Generator prompt tightening (character-budget-limits-targets PR): the four
AI-drafted fields that gained a hard limit/soft target (tools.description,
tools.agent_taxonomy_note, tools.competitive_differentiation,
community_profiles.stage_focus/jobs_program/team_or_individual) also get an
explicit length ceiling in the prompt text they're drafted from, converted
from the character target using ~6.2 characters/word.

`max_tokens` for description/agent_taxonomy is deliberately UNCHANGED —
lowering it risked reintroducing a previously-fixed, documented truncation
bug (see CLAUDE.md's citation-tag investigation and PR 260's
MIN_GENERATE_MAX_TOKENS note). Only the prose's own stated length ceiling
moved; these tests pin that, not a token-count change.
"""
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import enrich


def test_description_prompt_drops_budget_not_a_constraint_and_adds_a_word_ceiling():
    assert "Budget and depth are not a constraint here" not in enrich._TOOL_DESC_PROMPT
    assert "400 words" in enrich._TOOL_DESC_PROMPT
    assert "2,200 characters" in enrich._TOOL_DESC_PROMPT


def test_description_max_tokens_unchanged_at_3000():
    """Not lowered — see this file's own module docstring for why."""
    import inspect
    src = inspect.getsource(enrich.generate_tool_description)
    assert "_checked_max_tokens(3000)" in src


def test_agent_taxonomy_prompt_drops_budget_not_a_constraint_and_adds_a_word_ceiling():
    assert "Budget and depth are not a constraint here" not in enrich._AGENT_TAXONOMY_PROMPT
    assert "400 words" in enrich._AGENT_TAXONOMY_PROMPT
    assert "2,500 characters" in enrich._AGENT_TAXONOMY_PROMPT


def test_agent_taxonomy_max_tokens_unchanged_at_2000():
    import inspect
    src = inspect.getsource(enrich.generate_tool_agent_taxonomy)
    assert "_checked_max_tokens(2000)" in src


def test_differentiation_prompt_and_budget_untouched_already_under_target():
    """Differentiation's own rule 4 ("one or two sentences") already keeps
    it well under its 600-char target (longest stored: 546) — no prompt or
    max_tokens change needed here."""
    import inspect
    assert "One or two sentences" in enrich._TOOL_DIFFERENTIATION_PROMPT
    src = inspect.getsource(enrich.generate_tool_differentiation)
    assert "_checked_max_tokens(1200)" in src


def test_community_profile_prompt_gives_every_limited_field_a_ceiling_under_its_target():
    """PR 2a: every live prose field is asked to stay under a word/character
    ceiling that sits at or below its soft target in PROFILE_LIMITS."""
    from linklib.community_profile import PROFILE_LIMITS
    prompt = enrich._COMMUNITY_PROFILE_PROMPT
    for field, (target, _mx) in PROFILE_LIMITS.items():
        m = re.search(rf"^{field.upper()}:.*?roughly (\d+)\s+characters", prompt, re.M | re.S)
        assert m, f"{field} has no character ceiling in the prompt"
        assert int(m.group(1)) <= target, (field, m.group(1), target)


def test_community_profile_max_tokens_unchanged_shared_across_23_fields():
    import inspect
    src = inspect.getsource(enrich.generate_community_profile)
    assert "_checked_max_tokens(6000)" in src


# --- Empty-state "yet" copy retired (both riders' remaining strings) -------

def test_yet_copy_retired_from_gates():
    from linklib import gates
    assert gates.EMPTY_COPY["tool_agent_taxonomy"].visitor_text == \
        "What this tool's agents do hasn't been documented."
    assert gates.EMPTY_COPY["community_profile_group"].visitor_text == \
        "This section hasn't been researched."
    assert gates.COMPARE_EMPTY_LABELS["tool_agent_taxonomy"] == "Not documented."
    assert gates.COMPARE_EMPTY_LABELS["community_profile_group"] == "Not documented."
    assert gates.COMPARE_EMPTY_LABELS["tool_competitors"] == "Not curated."
    assert gates.COMPARE_EMPTY_LABELS["community_similar_communities"] == "Not curated."
    for text in gates.EMPTY_COPY.values():
        assert "yet" not in text.visitor_text.lower(), text.visitor_text
    for text in gates.COMPARE_EMPTY_LABELS.values():
        assert "yet" not in text.lower(), text
