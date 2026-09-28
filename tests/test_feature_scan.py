"""Feature Taxonomy scan tool — linklib/feature_scan.py.

Phase 2: per-tool origination-mode research + drafting.
Phase 3: roster-wide accumulation, §7 don't-collapse/unify clustering +
judgment, and the feature_review_queue write.

Mocks both the Exa HTTP call and the Anthropic SDK (same idiom as
tests/test_tool_summary_description.py) so this suite runs with no real
API keys or network access — a genuine quality check against real vendor
content still needs a human running
scripts/test_feature_scan_origination.py by hand with real keys, per
CLAUDE.md's "keep API keys out of Code building sessions" rule.
"""
import json
import os
import sys
import tempfile
import types

import pytest

sys.path.insert(0, __file__.rsplit("/tests/", 1)[0])

from linklib import feature_scan
from linklib.db import Library


def test_domain_of_strips_www():
    assert feature_scan._domain_of("https://www.mercury.com/") == "mercury.com"
    assert feature_scan._domain_of("https://rho.co") == "rho.co"


def test_infer_tier_matches_url_keywords():
    assert feature_scan._infer_tier("https://mercury.com/changelog", requested_tier=3) == 1
    assert feature_scan._infer_tier("https://mercury.com/help/faq", requested_tier=3) == 2
    assert feature_scan._infer_tier("https://mercury.com/pricing", requested_tier=1) == 3
    # No keyword match at all -> falls back to whatever tier the query was run under.
    assert feature_scan._infer_tier("https://mercury.com/some-random-page", requested_tier=4) == 4


def test_normalize_url_treats_trailing_slash_and_case_as_equal():
    assert (feature_scan._normalize_url("https://mercury.com/changelog/")
            == feature_scan._normalize_url("https://mercury.com/changelog"))
    assert (feature_scan._normalize_url("HTTPS://Mercury.com/Changelog")
            == feature_scan._normalize_url("https://mercury.com/Changelog"))
    # A genuinely different path must NOT normalize to the same value.
    assert (feature_scan._normalize_url("https://mercury.com/changelog")
            != feature_scan._normalize_url("https://mercury.com/help"))


def _mock_exa(monkeypatch, results_by_call):
    """results_by_call: list of result-lists, one per Exa call in query order
    (changelog, help center, product, press) — a call beyond the list length
    returns no results, matching a real Exa call that just found nothing."""
    calls = {"n": 0}

    class _Resp:
        def __init__(self, results):
            self._results = results

        def raise_for_status(self):
            pass

        def json(self):
            return {"results": self._results}

    def _post(url, headers=None, json=None, timeout=None):
        i = calls["n"]
        calls["n"] += 1
        results = results_by_call[i] if i < len(results_by_call) else []
        return _Resp(results)

    monkeypatch.setattr(feature_scan.requests, "post", _post)
    monkeypatch.setenv("EXA_API_KEY", "x")
    return calls


def _mock_anthropic(monkeypatch, payload_json):
    def _create(**kw):
        class _Block:
            type = "text"
            text = payload_json
        usage = types.SimpleNamespace(
            input_tokens=500, output_tokens=400,
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
        )
        return types.SimpleNamespace(content=[_Block()], usage=usage)
    fake = types.SimpleNamespace(Anthropic=lambda *a, **k: types.SimpleNamespace(
        messages=types.SimpleNamespace(create=lambda **kw: _create(**kw))))
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")


def test_research_vendor_domain_no_key_returns_empty(monkeypatch):
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    hits, cost = feature_scan.research_vendor_domain("Mercury", "https://mercury.com")
    assert hits == []
    assert cost == 0.0


def test_research_vendor_domain_dedupes_and_sorts_by_tier(monkeypatch):
    # Same URL surfaces under both the "help center" (tier 2) query and the
    # "product" (tier 3) query — the loop runs in hierarchy order, so it
    # must keep the tier-2 classification from the FIRST time it was seen.
    _mock_exa(monkeypatch, [
        [],  # changelog query: nothing
        [{"url": "https://mercury.com/help/multi-entity", "title": "Multi-entity help",
          "text": "Mercury supports sub-accounts for multiple entities."}],
        [{"url": "https://mercury.com/help/multi-entity", "title": "dup",
          "text": "same url again"},
         {"url": "https://mercury.com/product", "title": "Product",
          "text": "Mercury banking product overview."}],
        [],  # press query: nothing
    ])
    hits, cost = feature_scan.research_vendor_domain("Mercury", "https://mercury.com")
    urls = [h.url for h in hits]
    assert urls == [
        "https://mercury.com/help/multi-entity",  # tier 2, kept from first sighting
        "https://mercury.com/product",             # tier 3
    ]
    assert hits[0].tier == 2
    assert cost > 0


def test_research_vendor_domain_respects_exa_toggle_off(monkeypatch):
    """2026-09 fetch-error follow-up gate: research_vendor_domain was the
    one real Exa consumer with no admin-toggle check at all—EXA_API_KEY
    alone gated it, so a scan spent real money regardless of the site's
    Exa kill switch (/admin/system/ai). exa_enabled=False must short-
    circuit before any HTTP call, even with a real key present—proven
    with a raising mock, not just a call-count assertion, so this test
    genuinely fails (AssertionError from inside _post, not from the
    exa_enabled assert below it) against the pre-fix code rather than
    passing by coincidence."""
    monkeypatch.setenv("EXA_API_KEY", "x")

    def _post(*a, **kw):
        raise AssertionError("Exa must not be called when exa_enabled=False")

    monkeypatch.setattr(feature_scan.requests, "post", _post)
    hits, cost = feature_scan.research_vendor_domain(
        "Mercury", "https://mercury.com", exa_enabled=False)
    assert hits == []
    assert cost == 0.0


def test_research_vendor_domain_default_still_calls_exa(monkeypatch):
    """The other half of the same gate: omitting exa_enabled (every
    pre-existing caller) must still behave exactly as before—real
    callers that haven't been updated to pass it keep working."""
    _mock_exa(monkeypatch, [
        [{"url": "https://mercury.com/changelog", "title": "Changelog", "text": "Shipped X."}],
        [], [], [],
    ])
    hits, cost = feature_scan.research_vendor_domain("Mercury", "https://mercury.com")
    assert len(hits) == 1
    assert cost > 0


def test_draft_respects_exa_toggle_off(monkeypatch):
    """draft_tool_features_for_category threads exa_enabled straight
    through to research_vendor_domain—same raising-mock proof as above,
    plus confirms the draft still completes (low_confidence=True, grounded
    on the model's own knowledge) rather than failing outright, matching
    the existing no-EXA_API_KEY behavior exactly."""
    monkeypatch.setenv("EXA_API_KEY", "x")

    def _post(*a, **kw):
        raise AssertionError("Exa must not be called when exa_enabled=False")

    monkeypatch.setattr(feature_scan.requests, "post", _post)
    _mock_anthropic(monkeypatch, '{"features": ['
        '{"name": "Some feature", "definition": "A definition.", "availability": "native", '
        '"ai_enabled": false, "confident": false, "source_url": "", "note": ""}'
        ']}')

    draft = feature_scan.draft_tool_features_for_category(
        "Mercury", "https://mercury.com", "Neobanking", voice_core="Test voice guide.",
        exa_enabled=False)
    assert draft is not None
    assert draft.low_confidence is True
    assert draft.exa_cost_usd == 0.0


def test_draft_returns_none_without_anthropic_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    draft = feature_scan.draft_tool_features_for_category("Mercury", "https://mercury.com", "Neobanking")
    assert draft is None


def test_draft_aborts_without_voice_core(monkeypatch):
    # Same "(a) injected, hard-fail if empty" contract as every other
    # generate_* helper — and confirms the guard runs BEFORE the real-money
    # Exa search, not after (the whole point of placing it first).
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setenv("EXA_API_KEY", "x")

    def _post(*a, **kw):
        raise AssertionError("Exa must not be called when voice_core is empty")

    monkeypatch.setattr(feature_scan.requests, "post", _post)
    draft = feature_scan.draft_tool_features_for_category("Mercury", "https://mercury.com", "Neobanking")
    assert draft is None


def test_draft_parses_features_and_tags_source_tier(monkeypatch):
    _mock_exa(monkeypatch, [
        [{"url": "https://mercury.com/changelog", "title": "Changelog",
          "text": "Mercury shipped multi-entity sub-accounts in July."}],
        [], [], [],
    ])
    _mock_anthropic(monkeypatch, '{"features": ['
        '{"name": "Multi-entity sub-accounts", "definition": "Separate sub-accounts per '
        'legal entity under one login.", "availability": "native", "ai_enabled": false, '
        '"confident": true, "source_url": "https://mercury.com/changelog", "note": ""}'
        ']}')

    draft = feature_scan.draft_tool_features_for_category(
        "Mercury", "https://mercury.com", "Neobanking", roster_size=10,
        voice_core="Test voice guide.",
    )
    assert draft is not None
    assert draft.low_confidence is False
    assert len(draft.features) == 1
    f = draft.features[0]
    assert f.name == "Multi-entity sub-accounts"
    assert f.availability == "native"
    assert f.ai_enabled is False
    assert f.confident is True
    assert f.source_tier == 1   # resolved from the changelog grounding hit's tier
    assert f.source_tier_label == "Changelog / release notes"
    assert draft.cost_usd > 0
    assert draft.verified_as_of   # a real date string was stamped


# --- Source-tier resolution: the real Mercury/Neobanking finding that "tier
# 0" appeared in output with no explanation. UNCITED_TIER is a real,
# intentional sentinel (§8's hierarchy starts at 1) for "the model's cited
# source_url doesn't match any fetched grounding hit" — not a hierarchy
# level. Covers both the labeling fix and the URL-normalization fix that
# recovers a genuine match lost only to formatting (trailing slash). ------

def test_uncited_source_url_resolves_to_uncited_tier_with_label(monkeypatch):
    _mock_exa(monkeypatch, [
        [{"url": "https://mercury.com/changelog", "title": "Changelog",
          "text": "Mercury shipped multi-entity sub-accounts in July."}],
        [], [], [],
    ])
    # The model cites a URL that was never actually fetched as grounding —
    # a paraphrased/hallucinated citation, not a formatting mismatch.
    _mock_anthropic(monkeypatch, '{"features": ['
        '{"name": "Some feature", "source_url": "https://mercury.com/some-other-page"}'
        ']}')
    draft = feature_scan.draft_tool_features_for_category(
        "Mercury", "https://mercury.com", "Neobanking", voice_core="Test voice guide.",
    )
    f = draft.features[0]
    assert f.source_tier == feature_scan.UNCITED_TIER == 0
    assert f.source_tier_label == "Uncited (source URL not in fetched grounding set)"


def test_source_url_trailing_slash_mismatch_still_resolves_real_tier(monkeypatch):
    # A trivial formatting difference (trailing slash) between the fetched
    # hit's URL and what the model echoes back must NOT fall through to
    # UNCITED_TIER — that would be a false "uncited" reading on a citation
    # that's actually correct.
    _mock_exa(monkeypatch, [
        [{"url": "https://mercury.com/changelog", "title": "Changelog",
          "text": "Mercury shipped multi-entity sub-accounts in July."}],
        [], [], [],
    ])
    _mock_anthropic(monkeypatch, '{"features": ['
        '{"name": "Some feature", "source_url": "https://mercury.com/changelog/"}'
        ']}')
    draft = feature_scan.draft_tool_features_for_category(
        "Mercury", "https://mercury.com", "Neobanking", voice_core="Test voice guide.",
    )
    f = draft.features[0]
    assert f.source_tier == 1
    assert f.source_tier_label == "Changelog / release notes"


def test_no_source_url_resolves_to_uncited_tier(monkeypatch):
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    _mock_anthropic(monkeypatch, '{"features": [{"name": "Some feature"}]}')
    draft = feature_scan.draft_tool_features_for_category(
        "Mercury", "https://mercury.com", "Neobanking", voice_core="Test voice guide.",
    )
    assert draft.features[0].source_tier == feature_scan.UNCITED_TIER


def test_draft_is_low_confidence_with_no_grounding(monkeypatch):
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    _mock_anthropic(monkeypatch, '{"features": []}')
    draft = feature_scan.draft_tool_features_for_category(
        "Mercury", "https://mercury.com", "Neobanking", voice_core="Test voice guide.",
    )
    assert draft is not None
    assert draft.low_confidence is True
    assert draft.features == []


def test_draft_defaults_unknown_availability_to_native(monkeypatch):
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    _mock_anthropic(monkeypatch, '{"features": ['
        '{"name": "Some feature", "availability": "bogus_value"}'
        ']}')
    draft = feature_scan.draft_tool_features_for_category(
        "Mercury", "https://mercury.com", "Neobanking", voice_core="Test voice guide.",
    )
    assert draft.features[0].availability == "native"


def test_draft_skips_features_with_no_name(monkeypatch):
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    _mock_anthropic(monkeypatch, '{"features": [{"name": ""}, {"name": "Real feature"}]}')
    draft = feature_scan.draft_tool_features_for_category(
        "Mercury", "https://mercury.com", "Neobanking", voice_core="Test voice guide.",
    )
    assert [f.name for f in draft.features] == ["Real feature"]


@pytest.mark.parametrize("roster_size,expect_thin_language", [
    (2, True),
    (3, True),
    (4, False),
    (10, False),
    (0, False),   # unknown roster size -> normal (non-thin) framing
])
def test_thin_roster_note_reflected_in_prompt(monkeypatch, roster_size, expect_thin_language):
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    captured = {}

    def _create(**kw):
        captured["prompt"] = kw["messages"][0]["content"]
        class _Block:
            type = "text"
            text = '{"features": []}'
        usage = types.SimpleNamespace(input_tokens=1, output_tokens=1,
                                       cache_creation_input_tokens=0, cache_read_input_tokens=0)
        return types.SimpleNamespace(content=[_Block()], usage=usage)

    fake = types.SimpleNamespace(Anthropic=lambda *a, **k: types.SimpleNamespace(
        messages=types.SimpleNamespace(create=lambda **kw: _create(**kw))))
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")

    feature_scan.draft_tool_features_for_category(
        "Mercury", "https://mercury.com", "Neobanking", roster_size=roster_size,
        voice_core="Test voice guide.",
    )
    assert ("SKIP the differentiator criterion" in captured["prompt"]) is expect_thin_language


# --- Truncated-response resilience (real Mercury/Neobanking crash: a JSON
# response with an uncapped feature list ran past max_tokens and got cut off
# mid-string -> JSONDecodeError, which used to fall into the generic
# except-Exception handler and lose the whole tool's research as a silent
# None). -----------------------------------------------------------------

def test_salvage_recovers_complete_objects_before_the_cutoff():
    # The exact shape of the reported crash: one complete feature object,
    # then a second one cut off mid-string.
    raw = (
        '{"features": [{"name": "Multi-entity accounts", "definition": "desc", '
        '"availability": "native", "ai_enabled": false, "confident": true, '
        '"source_url": "https://mercury.com/x", "note": ""}, '
        '{"name": "Another feature", "definition": "unterminated'
    )
    features, truncated = feature_scan._salvage_feature_objects(raw)
    assert truncated is True
    assert len(features) == 1
    assert features[0]["name"] == "Multi-entity accounts"


def test_salvage_returns_empty_not_raise_on_garbage():
    features, truncated = feature_scan._salvage_feature_objects("not json at all")
    assert features == []
    assert truncated is True


def test_salvage_full_clean_array_all_recovered():
    raw = '{"features": [{"name": "A"}, {"name": "B"}, {"name": "C"}]}'
    features, truncated = feature_scan._salvage_feature_objects(raw)
    # Even a fully well-formed array still comes back truncated=True from
    # this helper in isolation — it has no way to know the array actually
    # closed cleanly (draft_tool_features_for_category only calls this
    # AFTER a full json.loads already failed). The caller-level behavior
    # (never reaching salvage on a clean response) is covered by
    # test_draft_parses_features_and_tags_source_tier above.
    assert [f["name"] for f in features] == ["A", "B", "C"]


def _mock_anthropic_sequence(monkeypatch, payloads):
    """Like _mock_anthropic, but returns a DIFFERENT payload on each
    successive call — for testing the truncate-then-retry path, where the
    first call's response truncates and a second (retry) call follows."""
    calls = {"n": 0}

    def _create(**kw):
        i = calls["n"]
        calls["n"] += 1
        payload = payloads[min(i, len(payloads) - 1)]
        class _Block:
            type = "text"
            text = payload
        usage = types.SimpleNamespace(
            input_tokens=500, output_tokens=400,
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
        )
        return types.SimpleNamespace(content=[_Block()], usage=usage)

    fake = types.SimpleNamespace(Anthropic=lambda *a, **k: types.SimpleNamespace(
        messages=types.SimpleNamespace(create=lambda **kw: _create(**kw))))
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    return calls


def test_draft_retries_once_on_truncation_and_uses_full_retry_result(monkeypatch):
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    truncated_payload = (
        '{"features": [{"name": "Multi-entity accounts", "availability": "native", '
        '"ai_enabled": false, "confident": true}, {"name": "cut off mid'
    )
    full_payload = '{"features": [{"name": "Multi-entity accounts"}, {"name": "Virtual cards"}]}'
    calls = _mock_anthropic_sequence(monkeypatch, [truncated_payload, full_payload])

    draft = feature_scan.draft_tool_features_for_category(
        "Mercury", "https://mercury.com", "Neobanking", voice_core="Test voice guide.",
    )
    assert calls["n"] == 2   # confirms the retry actually happened, not just returned early
    assert draft is not None
    assert draft.truncated is False   # the retry's response parsed cleanly
    assert [f.name for f in draft.features] == ["Multi-entity accounts", "Virtual cards"]
    # Cost/token accounting sums BOTH calls, not just the retry.
    assert draft.input_tokens == 1000
    assert draft.output_tokens == 800


def test_draft_never_hard_crashes_when_both_attempts_truncate(monkeypatch):
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    truncated_payload = (
        '{"features": [{"name": "Multi-entity accounts"}, {"name": "cut off'
    )
    calls = _mock_anthropic_sequence(monkeypatch, [truncated_payload, truncated_payload])

    draft = feature_scan.draft_tool_features_for_category(
        "Mercury", "https://mercury.com", "Neobanking", voice_core="Test voice guide.",
    )
    assert calls["n"] == 2
    assert draft is not None   # never falls into the generic except-Exception -> None path
    assert draft.truncated is True
    assert [f.name for f in draft.features] == ["Multi-entity accounts"]


def test_draft_does_not_retry_when_first_response_parses_cleanly(monkeypatch):
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    clean_payload = '{"features": [{"name": "Multi-entity accounts"}]}'
    calls = _mock_anthropic_sequence(monkeypatch, [clean_payload])

    draft = feature_scan.draft_tool_features_for_category(
        "Mercury", "https://mercury.com", "Neobanking", voice_core="Test voice guide.",
    )
    assert calls["n"] == 1   # no wasted retry call when the first response was fine
    assert draft.truncated is False


# ============================================================================
# Phase 3 — roster-wide accumulation, §7 clustering + judgment, queue write
# ============================================================================

def test_validate_partition_handles_clean_input():
    result = feature_scan._validate_partition([[0, 2], [1], [3]], n=4)
    assert result == [[0, 2], [1], [3]]


def test_validate_partition_dedupes_duplicate_index():
    # Index 2 claimed by both groups -> keeps its FIRST membership only.
    result = feature_scan._validate_partition([[0, 2], [2, 1]], n=3)
    assert result == [[0, 2], [1]]


def test_validate_partition_recovers_missing_index():
    # Index 2 dropped entirely by the model -> becomes its own singleton,
    # appended at the end rather than silently lost.
    result = feature_scan._validate_partition([[0], [1]], n=3)
    assert result == [[0], [1], [2]]


def test_validate_partition_ignores_out_of_range_and_non_int_values():
    result = feature_scan._validate_partition([[0, 99, "x"], [1]], n=2)
    assert result == [[0], [1]]


def _candidate(tool_id, tool_name, name, **kw):
    feature = feature_scan.ProposedFeature(name=name, **kw)
    return feature_scan.CandidateFeature(tool_id=tool_id, tool_name=tool_name,
                                          feature=feature, verified_as_of="2026-08-23")


# --- Incremental clustering (2026-08 fix — replaces the whole-batch
# cluster_candidate_features(), which broke on the first real live run:
# Neobanking, 364 candidates, ZERO merges, because a one-shot call's OUTPUT
# scaled with total roster size and the model's adaptive thinking consumed
# the entire max_tokens budget before writing anything at all.
# match_candidates_to_representatives() bounds output to ONE tool's
# candidate count regardless of roster size — see the module's Phase 3
# section header for the full post-mortem. -------------------------------

def test_match_candidates_no_new_candidates_no_call():
    matches, cost = feature_scan.match_candidates_to_representatives([], [], "Neobanking")
    assert matches == []
    assert cost == 0.0


def test_match_candidates_no_representatives_all_new_no_call(monkeypatch):
    # The first tool to contribute candidates has nothing to compare
    # against yet — every candidate is trivially new, no API call needed.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    new = [_candidate(1, "Mercury", "Checking accounts"), _candidate(1, "Mercury", "Virtual cards")]
    matches, cost = feature_scan.match_candidates_to_representatives(new, [], "Neobanking")
    assert matches == [None, None]
    assert cost == 0.0


def test_match_candidates_no_key_returns_none(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    new = [_candidate(2, "Rho", "Business checking")]
    reps = [_candidate(1, "Mercury", "Checking accounts")]
    matches, cost = feature_scan.match_candidates_to_representatives(new, reps, "Neobanking")
    assert matches is None
    assert cost == 0.0


def test_match_candidates_parses_matches_and_new(monkeypatch):
    new = [
        _candidate(2, "Rho", "Business checking account"),   # matches rep 0
        _candidate(2, "Rho", "Wire transfers"),               # genuinely new
    ]
    reps = [_candidate(1, "Mercury", "Checking accounts")]
    _mock_anthropic(monkeypatch, '{"matches": [0, null]}')
    matches, cost = feature_scan.match_candidates_to_representatives(new, reps, "Neobanking")
    assert matches == [0, None]
    assert cost > 0


def test_match_candidates_output_bounded_by_new_count_not_representative_count(monkeypatch):
    # The whole point of the fix: representative count can be large (a
    # category with genuinely little overlap) without inflating the
    # OUTPUT this call asks the model to produce — only len(new) entries,
    # ever. Confirm the prompt's own stated contract reflects that.
    captured = {}

    def _create(**kw):
        captured["prompt"] = kw["messages"][0]["content"]
        class _Block:
            type = "text"
            text = '{"matches": [null, null, null]}'
        usage = types.SimpleNamespace(input_tokens=50, output_tokens=10,
                                       cache_creation_input_tokens=0, cache_read_input_tokens=0)
        return types.SimpleNamespace(content=[_Block()], usage=usage)

    fake = types.SimpleNamespace(Anthropic=lambda *a, **k: types.SimpleNamespace(
        messages=types.SimpleNamespace(create=lambda **kw: _create(**kw))))
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")

    new = [_candidate(2, "Rho", f"New feature {i}") for i in range(3)]
    reps = [_candidate(1, "Mercury", f"Existing capability {i}") for i in range(300)]
    matches, cost = feature_scan.match_candidates_to_representatives(new, reps, "Neobanking")
    assert matches == [None, None, None]
    assert "exactly 3 entries" in captured["prompt"]   # the stated output contract names len(new), not 303


def test_validate_matches_repairs_out_of_range_and_missing():
    # 5 -> out of range (only 2 reps) -> None; missing 3rd entry -> None;
    # a stray bool must not be misread as index 0/1 (bool subclasses int).
    result = feature_scan._validate_matches([5, 1, True], n_new=4, n_reps=2)
    assert result == [None, 1, None, None]


def test_judge_cluster_singleton_short_circuits_no_api_call(monkeypatch):
    # No Anthropic mock installed at all — if this made a real call it
    # would raise (ImportError on the real anthropic client with no key),
    # so a clean pass here proves the short-circuit actually skipped it.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    candidates = [_candidate(1, "Mercury", "Checking accounts")]
    decisions, cost = feature_scan.judge_cluster("Neobanking", candidates)
    assert cost == 0.0
    assert len(decisions) == 1
    assert decisions[0].merged is False
    assert decisions[0].indices == [0]


def test_judge_cluster_empty_short_circuits():
    decisions, cost = feature_scan.judge_cluster("Neobanking", [])
    assert decisions == []
    assert cost == 0.0


def test_judge_cluster_no_key_returns_none(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    candidates = [_candidate(1, "Mercury", "A"), _candidate(2, "Rho", "B")]
    decisions, cost = feature_scan.judge_cluster("Neobanking", candidates)
    assert decisions is None
    assert cost == 0.0


def test_judge_cluster_parses_merge_decision(monkeypatch):
    candidates = [
        _candidate(1, "Mercury", "On-arrival categorization with review-and-improve"),
        _candidate(2, "Rho", "Suggests category from history for one-click accept"),
    ]
    _mock_anthropic(monkeypatch, '{"groups": [{"indices": [0, 1], "merged": true, '
        '"canonical_name": "Automated transaction categorization", '
        '"canonical_definition": "Machine-suggested category per transaction.", '
        '"reasoning": "Same job (categorize transactions), different mechanism."}]}')
    decisions, cost = feature_scan.judge_cluster("Neobanking", candidates)
    assert len(decisions) == 1
    d = decisions[0]
    assert d.merged is True
    assert d.indices == [0, 1]
    assert d.canonical_name == "Automated transaction categorization"
    assert "different mechanism" in d.reasoning
    assert cost > 0


def test_judge_cluster_parses_split_decision(monkeypatch):
    candidates = [_candidate(1, "Mercury", "A"), _candidate(2, "Rho", "B")]
    _mock_anthropic(monkeypatch, '{"groups": [{"indices": [0], "merged": false}, '
        '{"indices": [1], "merged": false}]}')
    decisions, cost = feature_scan.judge_cluster("Neobanking", candidates)
    assert len(decisions) == 2
    assert all(d.merged is False for d in decisions)


def test_judge_cluster_repairs_bad_partition_and_defaults_to_unmerged(monkeypatch):
    # The model drops index 1 entirely — _validate_partition recovers it as
    # its own singleton, which (having no original decision behind it)
    # correctly defaults to unmerged rather than inheriting group 0's
    # merged=true.
    candidates = [_candidate(1, "Mercury", "A"), _candidate(2, "Rho", "B")]
    _mock_anthropic(monkeypatch, '{"groups": [{"indices": [0], "merged": true, '
        '"canonical_name": "X", "canonical_definition": "Y"}]}')
    decisions, cost = feature_scan.judge_cluster("Neobanking", candidates)
    indices_seen = sorted(i for d in decisions for i in d.indices)
    assert indices_seen == [0, 1]
    recovered = [d for d in decisions if d.indices == [1]][0]
    assert recovered.merged is False


@pytest.fixture
def temp_lib():
    db_path = tempfile.mktemp(suffix=".db")
    lib = Library(db_path)
    yield lib
    lib.close()
    if os.path.exists(db_path):
        os.remove(db_path)


def test_originate_category_features_merges_across_tools_and_writes_queue(monkeypatch, temp_lib):
    monkeypatch.delenv("EXA_API_KEY", raising=False)   # no grounding — keeps this test API-call-count deterministic
    roster = [
        {"id": 101, "name": "Mercury", "url": "https://mercury.com"},
        {"id": 102, "name": "Rho", "url": "https://rho.co"},
    ]
    # Call order: draft(Mercury) [no match call — first tool, no representatives
    # yet], draft(Rho), match(Rho's candidate vs. Mercury's representative),
    # judge(the one 2-member cluster).
    payloads = [
        '{"features": [{"name": "Transaction categorization", "definition": "On-arrival, '
        'review-and-improve.", "availability": "native", "ai_enabled": true, "confident": true, '
        '"source_url": "", "note": ""}]}',
        '{"features": [{"name": "Auto-categorize transactions", "definition": "Suggests from '
        'history, one-click accept.", "availability": "add_on", "ai_enabled": true, '
        '"confident": true, "source_url": "", "note": ""}]}',
        '{"matches": [0]}',
        '{"groups": [{"indices": [0, 1], "merged": true, '
        '"canonical_name": "Automated transaction categorization", '
        '"canonical_definition": "Machine-suggested transaction categories.", '
        '"reasoning": "Same job, different mechanism."}]}',
    ]
    calls = _mock_anthropic_sequence(monkeypatch, payloads)

    summary = feature_scan.originate_category_features(temp_lib, category_id=7,
                                                         category_name="Neobanking", tool_roster=roster,
                                                         voice_core="Test voice guide.")

    assert calls["n"] == 4
    assert summary is not None
    assert summary.tools_researched == 2
    assert summary.tools_failed == 0
    assert summary.candidates_total == 2
    assert summary.clusters_found == 1
    assert summary.features_queued == 1
    assert summary.features_merged == 1
    assert summary.features_split == 0
    assert summary.clustering_degraded is False
    assert len(summary.queue_item_ids) == 1

    item = temp_lib.get_feature_review_queue_item(summary.queue_item_ids[0])
    assert item["source"] == "scan"
    assert item["status"] == "pending"
    assert item["category_id"] == 7
    assert item["proposal_type"] == "new_feature+2 links"
    assert item["payload"]["feature"]["name"] == "Automated transaction categorization"
    linked_tool_ids = sorted(link["tool_id"] for link in item["payload"]["links"])
    assert linked_tool_ids == [101, 102]
    assert "Merged across 2 tools" in item["articulation"]


def test_originate_category_features_split_candidates_queue_separately(monkeypatch, temp_lib):
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    roster = [
        {"id": 201, "name": "Mercury", "url": "https://mercury.com"},
        {"id": 202, "name": "Rho", "url": "https://rho.co"},
    ]
    payloads = [
        '{"features": [{"name": "Checking accounts", "definition": "FDIC-insured checking."}]}',
        '{"features": [{"name": "Virtual cards", "definition": "Instant virtual card issuance."}]}',
        '{"matches": [null]}',   # the match call itself found no overlap — no judge call needed
    ]
    calls = _mock_anthropic_sequence(monkeypatch, payloads)

    summary = feature_scan.originate_category_features(temp_lib, category_id=7,
                                                         category_name="Neobanking", tool_roster=roster,
                                                         voice_core="Test voice guide.")

    assert calls["n"] == 3   # 2 drafts + 1 match call; no judge call for either singleton cluster
    assert summary.features_queued == 2
    assert summary.features_merged == 0
    assert summary.features_split == 2
    assert summary.clustering_degraded is False
    items = [temp_lib.get_feature_review_queue_item(i) for i in summary.queue_item_ids]
    assert all(it["proposal_type"] == "new_feature+link" for it in items)


def test_originate_category_features_partial_tool_failure_still_queues(monkeypatch, temp_lib):
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    roster = [
        {"id": 301, "name": "Mercury", "url": "https://mercury.com"},
        {"id": 302, "name": "BrokenTool", "url": "https://broken.example"},
    ]
    call_count = {"n": 0}

    def _create(**kw):
        call_count["n"] += 1
        if call_count["n"] == 1:
            class _Block:
                type = "text"
                text = '{"features": [{"name": "Checking accounts"}]}'
            usage = types.SimpleNamespace(input_tokens=10, output_tokens=10,
                                           cache_creation_input_tokens=0, cache_read_input_tokens=0)
            return types.SimpleNamespace(content=[_Block()], usage=usage)
        raise RuntimeError("simulated API failure for the second tool")

    fake = types.SimpleNamespace(Anthropic=lambda *a, **k: types.SimpleNamespace(
        messages=types.SimpleNamespace(create=lambda **kw: _create(**kw))))
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")

    summary = feature_scan.originate_category_features(temp_lib, category_id=1,
                                                         category_name="Neobanking", tool_roster=roster,
                                                         voice_core="Test voice guide.")
    assert summary is not None
    assert summary.tools_researched == 1
    assert summary.tools_failed == 1
    assert summary.candidates_total == 1


def test_originate_category_features_returns_none_when_every_tool_fails(monkeypatch, temp_lib):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    roster = [{"id": 1, "name": "Mercury", "url": "https://mercury.com"}]
    summary = feature_scan.originate_category_features(temp_lib, category_id=1,
                                                         category_name="Neobanking", tool_roster=roster)
    assert summary is None


def test_originate_category_features_clustering_failure_falls_back_and_is_flagged(monkeypatch, temp_lib):
    # The incremental match call for the SECOND tool (comparing it against
    # the first tool's representative) fails outright — every candidate
    # should still get queued separately rather than the whole run
    # aborting, AND the failure must be VISIBLE (clustering_degraded),
    # never indistinguishable from "genuinely found no overlap" — that
    # exact ambiguity is what let the original Neobanking bug ship silently.
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    roster = [
        {"id": 401, "name": "Mercury", "url": "https://mercury.com"},
        {"id": 402, "name": "Rho", "url": "https://rho.co"},
    ]
    call_count = {"n": 0}

    def _create(**kw):
        call_count["n"] += 1
        if call_count["n"] <= 2:
            class _Block:
                type = "text"
                text = '{"features": [{"name": "Feature ' + str(call_count["n"]) + '"}]}'
            usage = types.SimpleNamespace(input_tokens=10, output_tokens=10,
                                           cache_creation_input_tokens=0, cache_read_input_tokens=0)
            return types.SimpleNamespace(content=[_Block()], usage=usage)
        raise RuntimeError("simulated incremental-match-call failure")

    fake = types.SimpleNamespace(Anthropic=lambda *a, **k: types.SimpleNamespace(
        messages=types.SimpleNamespace(create=lambda **kw: _create(**kw))))
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")

    summary = feature_scan.originate_category_features(temp_lib, category_id=1,
                                                         category_name="Neobanking", tool_roster=roster,
                                                         voice_core="Test voice guide.")
    assert summary.candidates_total == 2
    assert summary.features_queued == 2
    assert summary.features_merged == 0
    assert summary.features_split == 2
    assert summary.clustering_degraded is True
    assert summary.clustering_degraded_tools == ["Rho"]


def test_originate_category_features_no_degradation_when_clustering_succeeds(monkeypatch, temp_lib):
    # Sanity check for the flag itself: a clean run (this one has only one
    # tool, so no match call ever runs at all) must NOT report degraded.
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    roster = [{"id": 1, "name": "Mercury", "url": "https://mercury.com"}]
    _mock_anthropic(monkeypatch, '{"features": [{"name": "Checking accounts"}]}')
    summary = feature_scan.originate_category_features(temp_lib, category_id=1,
                                                         category_name="Neobanking", tool_roster=roster,
                                                         voice_core="Test voice guide.")
    assert summary.clustering_degraded is False
    assert summary.clustering_degraded_tools == []


def test_originate_category_features_dry_run_writes_nothing(monkeypatch, temp_lib):
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    roster = [
        {"id": 501, "name": "Mercury", "url": "https://mercury.com"},
        {"id": 502, "name": "Rho", "url": "https://rho.co"},
    ]
    payloads = [
        '{"features": [{"name": "Transaction categorization"}]}',
        '{"features": [{"name": "Auto-categorize transactions"}]}',
        '{"matches": [0]}',
        '{"groups": [{"indices": [0, 1], "merged": true, '
        '"canonical_name": "Automated transaction categorization", '
        '"canonical_definition": "def", "reasoning": "same job"}]}',
    ]
    _mock_anthropic_sequence(monkeypatch, payloads)

    summary = feature_scan.originate_category_features(
        temp_lib, category_id=9, category_name="Neobanking", tool_roster=roster, dry_run=True,
        voice_core="Test voice guide.",
    )

    assert summary.features_queued == 1   # counted from queued_payloads, not the (empty) queue_item_ids
    assert summary.queue_item_ids == []   # nothing actually written
    assert len(summary.queued_payloads) == 1
    assert summary.queued_payloads[0]["feature"]["name"] == "Automated transaction categorization"
    assert temp_lib.list_feature_review_queue(status=None) == []   # confirms: truly nothing in the DB


# --- Synthetic large-N test — exercises the incremental-clustering fix at
# a scale comparable to the real failure (Neobanking: 10 tools, 364
# candidates, one duplicate recurring across every tool). This is the test
# the whole-batch design never had, and its absence is exactly why the
# original bug shipped: nothing here exercised anything near real scale.
# Confirms the fix's core property directly — Anthropic call count and
# each match call's stated output contract stay bounded by TOOL count,
# never by total candidate count. -----------------------------------------

def test_originate_category_features_large_roster_merges_correctly_and_stays_bounded(monkeypatch, temp_lib):
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    n_tools = 8
    candidates_per_tool = 20   # 1 shared duplicate + 19 tool-unique -> 160 total candidates,
                               # same order of magnitude as the real 364-candidate failure
    roster = [
        {"id": 1000 + i, "name": f"Tool{i}", "url": f"https://tool{i}.example"}
        for i in range(n_tools)
    ]

    call_log = []   # (kind, prompt) for every call, to assert output-size bounds directly
    draft_count = {"n": 0}   # counts ONLY draft-kind calls, for a stable per-tool index —
                              # calls interleave draft/match/judge, so overall call position
                              # isn't the same thing as "which tool's draft is this"

    def _create(**kw):
        prompt = kw["messages"][0]["content"]
        if '"features"' in prompt or "ORIGINATION MODE" in prompt:
            kind = "draft"
        elif '"matches"' in prompt or "checking new candidate features" in prompt.lower():
            kind = "match"
        else:
            kind = "judge"
        call_log.append((kind, prompt))

        if kind == "draft":
            tool_i = draft_count["n"]
            draft_count["n"] += 1
            features = [{"name": "Accounting software sync", "definition": "Syncs to QuickBooks/Xero."}]
            features += [{"name": f"Tool{tool_i} unique feature {j}", "definition": "desc"}
                         for j in range(candidates_per_tool - 1)]
            text = json.dumps({"features": features})
        elif kind == "match":
            # The shared duplicate is always candidate index 0 for every
            # tool, and (by construction of the incremental algorithm)
            # always becomes representative index 0 the first time it's
            # ever seen (tool 0's draft) — every subsequent tool's shared
            # candidate matches representative 0, every unique candidate
            # matches nothing.
            text = json.dumps({"matches": [0] + [None] * (candidates_per_tool - 1)})
        else:   # judge — called once, for the one real 8-member merged cluster
            text = json.dumps({"groups": [{
                "indices": list(range(n_tools)), "merged": True,
                "canonical_name": "Automated accounting software sync",
                "canonical_definition": "Two-way sync with accounting software (QuickBooks/Xero).",
                "reasoning": "Same job across every vendor, phrased differently.",
            }]})

        class _Block:
            type = "text"
            def __init__(self, t):
                self.text = t
        usage = types.SimpleNamespace(input_tokens=200, output_tokens=50,
                                       cache_creation_input_tokens=0, cache_read_input_tokens=0)
        return types.SimpleNamespace(content=[_Block(text)], usage=usage)

    fake = types.SimpleNamespace(Anthropic=lambda *a, **k: types.SimpleNamespace(
        messages=types.SimpleNamespace(create=lambda **kw: _create(**kw))))
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")

    summary = feature_scan.originate_category_features(
        temp_lib, category_id=1, category_name="Neobanking", tool_roster=roster,
        voice_core="Test voice guide.",
    )

    assert summary is not None
    assert summary.tools_researched == n_tools
    assert summary.candidates_total == n_tools * candidates_per_tool   # 160
    assert summary.clustering_degraded is False

    # The core fix property: total calls scale with TOOL count (8 drafts +
    # 7 match calls [no match call for the first tool] + 1 judge call = 16),
    # nowhere near what a single-shot whole-batch design over 160 candidates
    # would have required, and nothing here is proportional to
    # candidates_per_tool at all.
    assert len(call_log) == n_tools + (n_tools - 1) + 1

    # Every match call's stated contract asks for exactly candidates_per_tool
    # entries — NEVER anything close to the running representative count
    # (which grows toward ~160 over the run) or the total candidate count.
    match_prompts = [p for kind, p in call_log if kind == "match"]
    assert len(match_prompts) == n_tools - 1
    for p in match_prompts:
        assert f"exactly {candidates_per_tool} entries" in p

    # Real correctness: the duplicate collapsed into ONE merged feature
    # spanning all 8 tools; everything else stayed separate (152 = 8*19).
    assert summary.features_merged == 1
    assert summary.features_split == n_tools * (candidates_per_tool - 1)
    assert summary.features_queued == 1 + n_tools * (candidates_per_tool - 1)

    merged_items = [
        temp_lib.get_feature_review_queue_item(i) for i in summary.queue_item_ids
    ]
    merged = [it for it in merged_items if len(it["payload"]["links"]) == n_tools]
    assert len(merged) == 1
    assert merged[0]["payload"]["feature"]["name"] == "Automated accounting software sync"
    assert sorted(l["tool_id"] for l in merged[0]["payload"]["links"]) == sorted(
        t["id"] for t in roster
    )
