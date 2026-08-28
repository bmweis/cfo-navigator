"""generate_tool_differentiation content-exclusion rules follow-up (2026-08,
see CLAUDE.md and generate_tool_differentiation's own docstring). This
field predates the citation-tag investigation's D1 content rules (no
vendor-reported stats/proof-points, no testimonials, no editor-facing
asides) — a blast-radius spot-check surfaced the gap live (a vendor-
reported stat in a sampled Differentiation output for Scale AI). Two
layers, mirroring tests/test_enrich_sentinel_parsing.py's own pattern for
Description/Agent taxonomy:

1. Pipeline-level fixtures (DIFFERENTIATION_RESPONSES) — a clean/compliant
   case, and an old-bug-reproduction case proving the fix is preventative
   (prompt-level) rather than corrective (there's no code-level filter on
   this field's plain-JSON output either).
2. Static prompt-content assertions — the new prompt rules are actually
   present, mirroring test_prompt_excludes_ratings_testimonials_and_reported_results
   and test_prompt_bans_editor_facing_address for the other two prompts.
"""
import pathlib
import sys
import types

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import enrich
from tests.citations_fixtures.differentiation_fixtures import DIFFERENTIATION_RESPONSES


def _mock_anthropic(monkeypatch, payload_json):
    def _create(**kw):
        class _Block:
            type = "text"
            text = payload_json
        usage = types.SimpleNamespace(
            input_tokens=100, output_tokens=80,
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
        )
        return types.SimpleNamespace(content=[_Block()], usage=usage)
    fake = types.SimpleNamespace(Anthropic=lambda *a, **k: types.SimpleNamespace(
        messages=types.SimpleNamespace(create=lambda **kw: _create(**kw))))
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")


# -- layer 1: full generation pipeline, mocked Claude call --------------------

@pytest.mark.parametrize(
    "case", DIFFERENTIATION_RESPONSES,
    ids=[c["name"] for c in DIFFERENTIATION_RESPONSES],
)
def test_generate_tool_differentiation_matches_exclusion_spec(monkeypatch, case):
    _mock_anthropic(monkeypatch, case["raw_response"])

    draft = enrich.generate_tool_differentiation(
        "Scale AI", "https://scale.com", case["description"],
        case["competitor_names"], voice_core="Test voice guide.",
    )
    assert draft is not None

    if "expected_differentiation" in case:
        assert draft.competitive_differentiation == case["expected_differentiation"]
        assert draft.confident is case["expected_confident"]
        for bad in case["forbid_substrings"]:
            assert bad not in draft.competitive_differentiation, (
                f"regression: {bad!r} leaked into competitive_differentiation "
                f"for scenario {case['name']!r}"
            )

    if "expected_leaked_substrings" in case:
        # Disclosed, tested limitation (same as
        # test_old_bug_shape_still_leaks_if_the_model_reverts_to_it for
        # Description/Agent taxonomy): if the model ignores the new rule,
        # nothing after the API call can strip a vendor stat from plain
        # JSON output — this assertion is EXPECTED to find the substring,
        # proving the fix is preventative, not a code-level filter.
        for leaked in case["expected_leaked_substrings"]:
            assert leaked in draft.competitive_differentiation, (
                f"expected disclosed leak of {leaked!r} for scenario "
                f"{case['name']!r} (documents the known limitation — see "
                "module docstring)"
            )


# -- layer 2: static prompt-content assertions --------------------------------

def test_differentiation_prompt_excludes_ratings_testimonials_and_reported_results():
    prompt = enrich._TOOL_DIFFERENTIATION_PROMPT.lower()
    assert "review scores" in prompt or "star ratings" in prompt
    assert "testimonials" in prompt
    assert "customer logos" in prompt or "logos" in prompt
    # The exclusion must explicitly reach content already sitting in the
    # description/competitor context passed into the prompt, not just
    # content the model might invent from scratch — that's the actual gap
    # a legacy, not-yet-regenerated description can reintroduce.
    assert "description" in prompt and "even if" in prompt


def test_differentiation_prompt_bans_editor_facing_address():
    prompt = enrich._TOOL_DIFFERENTIATION_PROMPT.lower()
    assert "the description above" in prompt or "the competitor context" in prompt
    assert "never reference" in prompt or "never mention" in prompt


def test_differentiation_prompt_still_requests_json():
    # Unlike Description/Agent taxonomy, this field's Citations-API
    # grounding stays deferred (no citations mechanism to have forced a
    # move off strict JSON) — confirm this fix didn't accidentally change
    # the response contract.
    prompt = enrich._TOOL_DIFFERENTIATION_PROMPT
    assert "Respond with JSON only" in prompt
