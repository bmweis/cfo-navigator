"""Feature Taxonomy scan tool, Phase 2 — linklib/feature_scan.py's per-tool
origination-mode research + drafting function. Mocks both the Exa HTTP call
and the Anthropic SDK (same idiom as tests/test_tool_summary_description.py)
so this suite runs with no real API keys or network access — a genuine
quality check against real vendor content still needs a human running
scripts/test_feature_scan_origination.py by hand with real keys, per
CLAUDE.md's "keep API keys out of Code building sessions" rule.
"""
import sys
import types

import pytest

sys.path.insert(0, __file__.rsplit("/tests/", 1)[0])

from linklib import feature_scan


def test_domain_of_strips_www():
    assert feature_scan._domain_of("https://www.mercury.com/") == "mercury.com"
    assert feature_scan._domain_of("https://rho.co") == "rho.co"


def test_infer_tier_matches_url_keywords():
    assert feature_scan._infer_tier("https://mercury.com/changelog", requested_tier=3) == 1
    assert feature_scan._infer_tier("https://mercury.com/help/faq", requested_tier=3) == 2
    assert feature_scan._infer_tier("https://mercury.com/pricing", requested_tier=1) == 3
    # No keyword match at all -> falls back to whatever tier the query was run under.
    assert feature_scan._infer_tier("https://mercury.com/some-random-page", requested_tier=4) == 4


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


def test_draft_returns_none_without_anthropic_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
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
    assert draft.cost_usd > 0
    assert draft.verified_as_of   # a real date string was stamped


def test_draft_is_low_confidence_with_no_grounding(monkeypatch):
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    _mock_anthropic(monkeypatch, '{"features": []}')
    draft = feature_scan.draft_tool_features_for_category(
        "Mercury", "https://mercury.com", "Neobanking",
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
        "Mercury", "https://mercury.com", "Neobanking",
    )
    assert draft.features[0].availability == "native"


def test_draft_skips_features_with_no_name(monkeypatch):
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    _mock_anthropic(monkeypatch, '{"features": [{"name": ""}, {"name": "Real feature"}]}')
    draft = feature_scan.draft_tool_features_for_category(
        "Mercury", "https://mercury.com", "Neobanking",
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
        "Mercury", "https://mercury.com", "Neobanking",
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
        "Mercury", "https://mercury.com", "Neobanking",
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
        "Mercury", "https://mercury.com", "Neobanking",
    )
    assert calls["n"] == 1   # no wasted retry call when the first response was fine
    assert draft.truncated is False
