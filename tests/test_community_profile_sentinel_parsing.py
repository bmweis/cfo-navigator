"""Community profile citation fix (2026-08, see CLAUDE.md).

Written against tests/citations_fixtures/community_profile_sentinel_fixtures.py's
frozen spec BEFORE generate_community_profile was wired to it. Three layers,
same discipline as the merged Description/Agent taxonomy fix
(tests/test_enrich_sentinel_parsing.py):

1. _parse_labeled_blocks against PARSE_LABELED_BLOCKS_CASES — including the
   terminal_key/CONFIDENCE-collision case, which is the one that actually
   protects data integrity here (a confidence sub-key line must never be
   allowed to reopen a narrative field's block), not just formatting.
2. generate_community_profile's full pipeline against
   COMMUNITY_PROFILE_RESPONSES — mocked multi-block Claude responses,
   asserting parsed fields, confidence dict, real citation markers, and the
   "no cite-tag substring ever" regression guard.
3. A real bug-reproduction case (COMMUNITY_OLD_BUG_REPRODUCTION_CASE),
   verified against the actual pre-fix code the same way the merged PR's
   Datarails reproduction was — not just asserted.
"""
import pathlib
import sys
import types

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import enrich


@pytest.fixture(autouse=True)
def _no_citation_policy(monkeypatch):
    """This file tests parsing/grounding, not citation policy (the retry and
    refusal rules are covered in tests/test_uncited_drafts.py), so a draft is
    accepted here whether or not it carries citations."""
    from linklib import enrich as _enrich
    monkeypatch.setattr(_enrich, "_run_cited_draft", lambda attempt, *a, **k: attempt())
from tests.citations_fixtures.community_profile_sentinel_fixtures import (
    PARSE_LABELED_BLOCKS_CASES,
    COMMUNITY_PROFILE_RESPONSES,
    COMMUNITY_OLD_BUG_REPRODUCTION_CASE,
)


# -- layer 1: _parse_labeled_blocks -------------------------------------------

@pytest.mark.parametrize("case", PARSE_LABELED_BLOCKS_CASES, ids=[c["name"] for c in PARSE_LABELED_BLOCKS_CASES])
def test_parse_labeled_blocks_matches_spec(case):
    result = enrich._parse_labeled_blocks(case["text"], case["keys"], terminal_key=case["terminal_key"])
    assert result == case["expected"]


def test_terminal_key_protects_data_integrity_not_just_formatting():
    """The specific case Brian asked to see proven: without terminal_key,
    a confidence sub-key line would corrupt the real narrative field
    parsed earlier in the SAME text. This test asserts that corruption
    does NOT happen — not just that the confidence block parses, but that
    the fields before it survive intact and unmodified."""
    case = next(c for c in PARSE_LABELED_BLOCKS_CASES if c["name"] == "terminal_key_stops_header_scanning_entirely")
    result = enrich._parse_labeled_blocks(case["text"], case["keys"], terminal_key=case["terminal_key"])

    # The actual integrity assertion: ideal_member/anti_fit must be the
    # REAL narrative text from earlier in the response, not overwritten by
    # anything inside CONFIDENCE:'s body (which also contains lines shaped
    # exactly like "IDEAL_MEMBER: true" / "ANTI_FIT: false").
    assert result["ideal_member"] == "Series B+ CFOs at SaaS companies."
    assert "true" not in result["ideal_member"]
    assert result["anti_fit"] == "Solo founders."
    assert "false" not in result["anti_fit"]
    # And confidence's own body is preserved verbatim, never re-parsed.
    assert result["confidence"] == "IDEAL_MEMBER: true\nANTI_FIT: false"


def test_without_terminal_key_the_collision_would_actually_corrupt_data():
    """Negative control: proves the terminal_key mechanism is load-bearing,
    not decorative — the same input, parsed WITHOUT terminal_key, really
    does get corrupted. If this test ever fails (i.e. the naive scan stops
    corrupting on its own), the terminal_key mechanism has become
    redundant and this whole design decision should be revisited."""
    case = next(c for c in PARSE_LABELED_BLOCKS_CASES if c["name"] == "terminal_key_stops_header_scanning_entirely")
    result = enrich._parse_labeled_blocks(case["text"], case["keys"], terminal_key=None)

    # Without the guard, "IDEAL_MEMBER: true" inside CONFIDENCE:'s body is
    # mistaken for a fresh header, overwriting the real narrative text.
    assert result["ideal_member"] == "true"   # corrupted — proves the guard is necessary
    assert result["anti_fit"] == "false"       # corrupted


# -- layer 2: full generation pipeline, mocked Claude call --------------------

def _mock_fetch_page(monkeypatch, contents: dict):
    """Mocks at the _fetch_grounding_page layer, not extract.fetch_page —
    see tests/test_enrich_sentinel_parsing.py's identical helper for why:
    these tests prove the sentinel-parsing contract, not the 2026-09
    JS-render grounding-fetch/quality-gate behavior."""
    def _fake_fetch_grounding_page(url, exa_enabled=True):
        content = contents.get(url, "")
        if content.strip():
            return enrich.GroundingFetch(content=content.strip(), ok=True, status="fetched")
        return enrich.GroundingFetch(status="unreadable", reason="no content")

    monkeypatch.setattr(enrich, "_fetch_grounding_page", _fake_fetch_grounding_page)


def _mock_anthropic_blocks(monkeypatch, blocks):
    """Same shape as the merged fix's helper — one SDK text block per
    (text, cited_document_indexes) pair, matching real citation-boundary
    splitting."""
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
            input_tokens=400, output_tokens=900,
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
        )
        return types.SimpleNamespace(content=content, usage=usage)
    fake = types.SimpleNamespace(Anthropic=lambda *a, **k: types.SimpleNamespace(
        messages=types.SimpleNamespace(create=lambda **kw: _create(**kw))))
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")


@pytest.mark.parametrize("case", COMMUNITY_PROFILE_RESPONSES, ids=[c["name"] for c in COMMUNITY_PROFILE_RESPONSES])
def test_generate_community_profile_matches_sentinel_spec(monkeypatch, case):
    _mock_fetch_page(monkeypatch, {"https://chief.com": "Homepage content about Chief."})
    _mock_anthropic_blocks(monkeypatch, case["blocks"])

    draft = enrich.generate_community_profile("Chief", "https://chief.com", voice_core="Test voice guide.")
    assert draft is not None

    for field, expected in case["expected_fields"].items():
        assert getattr(draft, field) == expected, f"{field} mismatch for {case['name']!r}"
    if "expected_ideal_member_contains" in case:
        assert case["expected_ideal_member_contains"] in draft.ideal_member
    for field, expected in case["expected_confidence"].items():
        assert draft.confidence[field] is expected, f"confidence[{field}] mismatch for {case['name']!r}"
    assert len(draft.citations) == case["expected_citation_count"]
    for bad in case.get("forbid_substrings", []):
        for value in [draft.ideal_member, draft.verdict_summary, draft.value_prop]:
            assert bad not in value, f"regression: {bad!r} leaked for scenario {case['name']!r}"


def test_confidence_defaults_all_12_keys_even_when_block_missing_entirely(monkeypatch):
    """No CONFIDENCE: block at all in the response (not even present) —
    every one of the 12 tracked keys must still default to False, not
    raise and not silently omit keys."""
    _mock_fetch_page(monkeypatch, {"https://chief.com": "Homepage content."})
    _mock_anthropic_blocks(monkeypatch, [("IDEAL_MEMBER:\nSeed-stage CFOs.", [])])

    draft = enrich.generate_community_profile("Chief", "https://chief.com", voice_core="Test voice guide.")
    assert draft is not None
    assert set(draft.confidence) == set(enrich.COMMUNITY_CONFIDENCE_FIELDS)
    assert all(v is False for v in draft.confidence.values())


def test_placeholder_null_words_coerce_to_empty_string_except_cpe_eligible(monkeypatch):
    """The old JSON prompt used a real `null` for "unknown" on several
    fields, stored as "" via `data.get(f) or ""`. Plain text has no null,
    so the new prompt asks the model to write literal words instead
    ("Unclear", "None reported", "None publicly reported") — those words
    must be coerced back to "" to preserve the existing storage/rendering
    contract, NOT stored verbatim as a new, unplanned behavior change.

    The one deliberate exception: cpe_eligible's "Unclear" was already a
    real, literal stored value in the ORIGINAL prompt (rule 9: "Yes"/"No"/
    "Unclear") — not a null-placeholder — so it must NOT be coerced away."""
    _mock_fetch_page(monkeypatch, {"https://chief.com": "Homepage content."})
    _mock_anthropic_blocks(monkeypatch, [(
        "IDEAL_MEMBER:\nSeed-stage CFOs.\n\n"
        "NOTABLE_MEMBERS:\nNone publicly reported.\n\n"
        "PUBLIC_CRITICISM:\nNone reported.\n\n"
        "RESOURCES_INCLUDED:\nNo\n\n"
        "CPE_ELIGIBLE:\nUnclear",
        [],
    )])

    draft = enrich.generate_community_profile("Chief", "https://chief.com", voice_core="Test voice guide.")
    assert draft is not None
    assert draft.notable_members == ""
    assert draft.public_criticism == ""
    assert draft.resources_included == "No"   # a real value, never a placeholder
    assert draft.cpe_eligible == "Unclear"     # NOT coerced — a legitimate literal value here


def test_confidence_string_false_does_not_evaluate_truthy(monkeypatch):
    """The real Python footgun this rewrite has to avoid: bool("false") is
    True. _parse_community_confidence must compare against the literal
    string "true", not truthiness, once its input is sentinel-parsed
    strings instead of real JSON booleans."""
    _mock_fetch_page(monkeypatch, {"https://chief.com": "Homepage content."})
    _mock_anthropic_blocks(monkeypatch, [(
        "IDEAL_MEMBER:\nSeed-stage CFOs.\n\nCONFIDENCE:\nIDEAL_MEMBER: false", []
    )])

    draft = enrich.generate_community_profile("Chief", "https://chief.com", voice_core="Test voice guide.")
    assert draft is not None
    assert draft.confidence["ideal_member"] is False   # NOT True — bool("false") would be wrong here


# -- layer 3: the real bug-reproduction case, verified against pre-fix code --

def test_old_bug_shape_still_leaks_if_the_model_reverts(monkeypatch):
    """Same discipline as the merged fix's Datarails reproduction — proven,
    not asserted. But the actual OUTCOME here is different from the
    tool-side fix, and worth being precise about rather than assuming
    parity: this JSON-blob shape (`"ideal_member": "..."`, lowercase,
    quoted) contains no line matching a recognized "KEY:" header (headers
    are uppercase, unquoted, own line — "IDEAL_MEMBER:") anywhere in it,
    so _parse_labeled_blocks recognizes ZERO fields. Unlike the two-field
    fix (which still returns a note as long as SOME text came back),
    generate_community_profile treats a completely-empty parse as a hard
    failure and returns None — a deliberate, stronger guard specific to
    this field, because upsert_community_profile is a FULL REPLACE of all
    23 columns: silently saving an all-empty draft wouldn't just carry
    stale tags forward, it would blank a community's entire profile. So
    the honest finding here is actually better than the tool-side case:
    this exact old-bug shape can't reach the DB at all through this path,
    not because anything strips the tags, but because nothing in it
    matches the new field-header contract in the first place — a
    structural side effect of the format change, not a content filter."""
    _mock_fetch_page(monkeypatch, {"https://chief.example": "Homepage content."})
    _mock_anthropic_blocks(monkeypatch, [(
        COMMUNITY_OLD_BUG_REPRODUCTION_CASE["raw_text"],
        COMMUNITY_OLD_BUG_REPRODUCTION_CASE["cited_document_indexes"],
    )])

    draft = enrich.generate_community_profile("Chief", "https://chief.example", voice_core="Test voice guide.")
    assert draft is None


# -- static prompt-content assertions -----------------------------------------

def test_community_profile_prompt_no_longer_requests_json():
    prompt = enrich._COMMUNITY_PROFILE_PROMPT
    assert "STRICT JSON" not in prompt


def test_community_profile_prompt_bans_editor_facing_address():
    prompt = enrich._COMMUNITY_PROFILE_PROMPT.lower()
    assert "the page content" in prompt or "the provided page" in prompt
    assert "never reference" in prompt or "never mention" in prompt


def test_community_profile_prompt_excludes_ratings_testimonials_and_reported_results():
    prompt = enrich._COMMUNITY_PROFILE_PROMPT.lower()
    assert "review scores" in prompt or "star ratings" in prompt
    assert "testimonials" in prompt
    assert "customer logos" in prompt or "logos" in prompt
