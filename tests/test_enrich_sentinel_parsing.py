"""Citation-tag-investigation generation-path fix (2026-08, see CLAUDE.md).

Written against tests/citations_fixtures/enrich_sentinel_fixtures.py's
frozen spec BEFORE generate_tool_description/generate_tool_agent_taxonomy
were wired to it — these tests are the independently-stated contract the
implementation has to satisfy, not a record of what the implementation
happened to produce. Three layers:

1. _split_trailing_sentinels against SPLIT_SENTINEL_CASES — the new
   deterministic parsing boundary (replaces json.loads()).
2. generate_tool_agent_taxonomy / generate_tool_description's full
   pipeline against AGENT_TAXONOMY_RESPONSES / DESCRIPTION_RESPONSES —
   mocked Claude responses in the new plain-prose + trailing-sentinel
   shape, asserting parsed fields, real inject_markers=True citation
   markers, and the "no cite-tag substring ever" regression guard.
3. Static prompt-content assertions — the new prompts no longer ask for
   "STRICT JSON" and do carry the D1 rule additions (no markdown syntax,
   no editor-facing address, no ratings/testimonials/logos/results) — the
   mechanical half of "this class of bug is closed" that complements the
   live diagnostic-script check (which covers actual model behavior, not
   reachable from a unit test).
"""
import pathlib
import sys
import types

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import enrich
from tests.citations_fixtures.enrich_sentinel_fixtures import (
    SPLIT_SENTINEL_CASES,
    AGENT_TAXONOMY_RESPONSES,
    DESCRIPTION_RESPONSES,
)


# -- layer 1: _split_trailing_sentinels ---------------------------------------

@pytest.mark.parametrize("case", SPLIT_SENTINEL_CASES, ids=[c["name"] for c in SPLIT_SENTINEL_CASES])
def test_split_trailing_sentinels_matches_spec(case):
    remaining, found = enrich._split_trailing_sentinels(case["text"], case["keys"])
    assert remaining == case["expected_remaining"]
    assert found == case["expected_found"]


# -- layer 2: full generation pipeline, mocked Claude call --------------------

def _mock_fetch_page(monkeypatch, contents: dict):
    from linklib import extract
    monkeypatch.setattr(extract, "fetch_page", lambda url, **kw: types.SimpleNamespace(
        content=contents.get(url, "")))
    monkeypatch.setattr(enrich, "_discover_nav_pages", lambda base_url, max_pages=10: [])


def _mock_anthropic_blocks(monkeypatch, blocks):
    """blocks: list of (text, cited_document_indexes) — one SDK text block
    per entry, each with its own `citations` list. Mirrors how the real
    Citations API actually splits a response into separate blocks at
    citation boundaries (a cited claim and an uncited trailing line are
    different blocks, never one block carrying both) — unlike a single
    flat mocked block, which would let inject_markers=True append a
    citation marker after content that was never actually cited."""
    def _create(**kw):
        class _Citation:
            def __init__(self, idx):
                self.document_index = idx

        class _Block:
            def __init__(self, text, cited_indexes):
                self.type = "text"
                self.text = text
                self.citations = [_Citation(i) for i in cited_indexes]

        content = [_Block(text, cited) for text, cited in blocks]
        usage = types.SimpleNamespace(
            input_tokens=200, output_tokens=150,
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
        )
        return types.SimpleNamespace(content=content, usage=usage)
    fake = types.SimpleNamespace(Anthropic=lambda *a, **k: types.SimpleNamespace(
        messages=types.SimpleNamespace(create=lambda **kw: _create(**kw))))
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")


@pytest.mark.parametrize("case", AGENT_TAXONOMY_RESPONSES, ids=[c["name"] for c in AGENT_TAXONOMY_RESPONSES])
def test_generate_tool_agent_taxonomy_matches_sentinel_spec(monkeypatch, case):
    _mock_fetch_page(monkeypatch, {
        "https://runway.com": "Homepage content about Runway.",
        "https://runway.com/pricing": "Pricing tiers.",
    })
    _mock_anthropic_blocks(monkeypatch, case["blocks"])

    result = enrich.generate_tool_agent_taxonomy("Runway", "https://runway.com")
    assert result is not None

    if "expected_note" in case:
        assert result.agent_taxonomy_note == case["expected_note"]
    if "expected_note_contains" in case:
        assert case["expected_note_contains"] in result.agent_taxonomy_note
    assert result.confident is case["expected_confident"]
    assert len(result.citations) == case["expected_citation_count"]
    for bad in case.get("forbid_substrings", []):
        assert bad not in result.agent_taxonomy_note, (
            f"regression: {bad!r} leaked into agent_taxonomy_note for scenario {case['name']!r}"
        )


@pytest.mark.parametrize("case", DESCRIPTION_RESPONSES, ids=[c["name"] for c in DESCRIPTION_RESPONSES])
def test_generate_tool_description_matches_sentinel_spec(monkeypatch, case):
    _mock_fetch_page(monkeypatch, {"https://concourse.example": "Homepage content."})
    _mock_anthropic_blocks(monkeypatch, case["blocks"])

    draft = enrich.generate_tool_description("Concourse", "https://concourse.example")
    assert draft is not None

    assert case["expected_description_contains"] in draft.description
    assert draft.summary == case["expected_summary"]
    assert draft.confident is case["expected_confident"]
    assert len(draft.citations) == case["expected_citation_count"]
    for bad in case.get("forbid_substrings", []):
        assert bad not in draft.description, (
            f"regression: {bad!r} leaked into description for scenario {case['name']!r}"
        )
        assert bad not in draft.summary, (
            f"regression: {bad!r} leaked into summary for scenario {case['name']!r}"
        )


def test_agent_taxonomy_real_citation_marker_is_injected_inline():
    """Confirms inject_markers=True is actually wired up — the whole point
    of moving off inject_markers=False. A cited span gets a real [n]
    marker in the stored text now, matching agent.py's FP&A Buddy
    convention, not the old JSON-safe "never touch the text" behavior.
    (The actual end-to-end proof is
    test_generate_tool_agent_taxonomy_matches_sentinel_spec's own
    "real_citation_fires_and_marker_lands_in_note" case — this is just a
    fixture sanity check that the case is wired the way this test's name
    claims.)"""
    case = next(c for c in AGENT_TAXONOMY_RESPONSES if c["name"] == "real_citation_fires_and_marker_lands_in_note")
    assert case["expected_note_contains"] == "[1]"
    assert any(cited for _text, cited in case["blocks"])


# -- layer 3: static prompt-content assertions --------------------------------

def test_agent_taxonomy_prompt_no_longer_requests_json():
    prompt = enrich._AGENT_TAXONOMY_PROMPT
    assert "STRICT JSON" not in prompt
    assert "no prose, no markdown fences" not in prompt  # the old JSON-only instruction phrase


def test_description_prompt_no_longer_requests_json():
    prompt = enrich._TOOL_DESC_PROMPT
    assert "STRICT JSON" not in prompt
    assert '"description", "summary", "confident"' not in prompt  # the old JSON-key instruction


@pytest.mark.parametrize("prompt_name", ["_AGENT_TAXONOMY_PROMPT", "_TOOL_DESC_PROMPT"])
def test_prompt_bans_editor_facing_address(prompt_name):
    prompt = getattr(enrich, prompt_name)
    lowered = prompt.lower()
    assert "the provided pages" in lowered or "the page content" in lowered  # names the exact phrase to avoid
    assert "never reference" in lowered or "never mention" in lowered


@pytest.mark.parametrize("prompt_name", ["_AGENT_TAXONOMY_PROMPT", "_TOOL_DESC_PROMPT"])
def test_prompt_bans_markdown_emphasis_syntax(prompt_name):
    prompt = getattr(enrich, prompt_name)
    assert "**bold**" in prompt or "markdown syntax" in prompt.lower()


@pytest.mark.parametrize("prompt_name", ["_AGENT_TAXONOMY_PROMPT", "_TOOL_DESC_PROMPT"])
def test_prompt_excludes_ratings_testimonials_and_reported_results(prompt_name):
    prompt = getattr(enrich, prompt_name).lower()
    assert "review scores" in prompt or "star ratings" in prompt
    assert "testimonials" in prompt
    assert "customer logos" in prompt or "logos" in prompt
