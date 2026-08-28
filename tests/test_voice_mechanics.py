"""linklib.voice_mechanics — the deterministic spaced-em-dash backstop
(2026-08). See CLAUDE.md's matching bullet for the incident and root cause
this closes: `voice_core`'s em-dash rule was correctly present in every
generation prompt the whole time, but a natural-language "never" is not a
guarantee against an LLM's probabilistic drift — this module is the
code-level correction that makes "zero spaced em dashes" actually
achievable, applied at every `Library` write path rather than relied on at
generation time."""
from linklib.voice_mechanics import fix_spaced_em_dashes, normalize_voice_mechanics


def test_collapses_a_single_spaced_em_dash():
    assert fix_spaced_em_dashes("a — b") == "a—b"


def test_collapses_multiple_spaces_around_the_dash():
    assert fix_spaced_em_dashes("a  —  b") == "a—b"


def test_collapses_non_breaking_spaces_around_the_dash():
    assert fix_spaced_em_dashes("a — b") == "a—b"


def test_reproduces_and_fixes_the_actual_incident_shape():
    # Real shape from the 2026-08 spot-check (Concourse's Agent taxonomy,
    # pre-fix): a spaced em dash bolting a trailing clause onto a sentence.
    original = "Concourse's T&E Audit Agent flags policy violations — reviewed by a human before posting."
    fixed = "Concourse's T&E Audit Agent flags policy violations—reviewed by a human before posting."
    assert fix_spaced_em_dashes(original) == fixed


def test_already_unspaced_em_dash_is_a_no_op():
    text = "two acquisitions—Ansible to Red Hat, Tidelift to Sonar—both closing soon"
    assert fix_spaced_em_dashes(text) == text


def test_spaced_double_hyphen_used_as_a_dash_becomes_a_real_em_dash():
    assert fix_spaced_em_dashes("word1 -- word2") == "word1—word2"


def test_unspaced_double_hyphen_is_left_alone():
    # "2020--2024" is a range, not a dash-with-spaces mistake — must not be
    # touched by this backstop, which only targets the SPACED shape.
    text = "growth from 2020--2024 accelerated"
    assert fix_spaced_em_dashes(text) == text


def test_multiple_violations_in_one_string_all_fixed():
    text = "a — b and c — d and e—f"
    assert fix_spaced_em_dashes(text) == "a—b and c—d and e—f"


def test_empty_and_none_are_safe_no_ops():
    assert fix_spaced_em_dashes("") == ""
    assert fix_spaced_em_dashes(None) is None


def test_normalize_voice_mechanics_is_the_same_entry_point():
    assert normalize_voice_mechanics("a — b") == fix_spaced_em_dashes("a — b")
