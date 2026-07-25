"""Feature comparison data — LLM enrichment first pass (Software search
overhaul Phase 4b). Covers linklib.enrich.generate_tool_features (unit,
mocked Claude call + mocked page fetch) and scripts/enrich_tool_features.py's
selection/dedup/dry-run logic (also mocked — no real API calls in tests).
"""
import os
import pathlib
import sys
import tempfile
import types

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import enrich
from linklib.db import Library


def _mock_anthropic(monkeypatch, payload_json, input_tokens=200, output_tokens=150):
    def _create(**kw):
        class _Block:
            type = "text"
            text = payload_json
        usage = types.SimpleNamespace(
            input_tokens=input_tokens, output_tokens=output_tokens,
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
        )
        return types.SimpleNamespace(content=[_Block()], usage=usage)
    fake = types.SimpleNamespace(Anthropic=lambda *a, **k: types.SimpleNamespace(
        messages=types.SimpleNamespace(create=lambda **kw: _create(**kw))))
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")


def _mock_fetch_page(monkeypatch, contents: dict):
    """contents: url -> content string. Missing urls return empty content."""
    from linklib import extract

    def _fetch(url, **kw):
        return types.SimpleNamespace(content=contents.get(url, ""))
    monkeypatch.setattr(extract, "fetch_page", _fetch)


FEATURES_JSON = """{
    "features": [
        {"feature_name": "Scenario modeling", "standalone_available": true,
         "bundled_only": false, "notes": "", "confident": true},
        {"feature_name": "Headcount planning", "standalone_available": false,
         "bundled_only": true, "notes": "Growth tier and above", "confident": true},
        {"feature_name": "API access", "standalone_available": true,
         "bundled_only": true, "notes": "", "confident": false}
    ]
}"""


def test_generate_tool_features_parses_drafts(monkeypatch):
    _mock_fetch_page(monkeypatch, {
        "https://runway.com": "Homepage content about Runway.",
        "https://runway.com/pricing": "Pricing tiers: Starter, Growth, Enterprise.",
    })
    _mock_anthropic(monkeypatch, FEATURES_JSON)

    result = enrich.generate_tool_features("Runway", "https://runway.com", "FP&A for high-growth teams.")
    assert result is not None
    assert len(result.features) == 3
    scenario = next(f for f in result.features if f.feature_name == "Scenario modeling")
    assert scenario.standalone_available is True
    assert scenario.bundled_only is False
    assert scenario.needs_verification is False   # confident: true
    api = next(f for f in result.features if f.feature_name == "API access")
    assert api.standalone_available is True
    assert api.bundled_only is True
    assert api.needs_verification is True          # confident: false
    assert result.cost_usd > 0
    assert result.low_confidence is False           # pricing page fetched successfully
    # Pricing page is preferred over Homepage for the batch source_url
    assert scenario.source_url == "https://runway.com/pricing"


def test_generate_tool_features_low_confidence_when_no_pages_fetch(monkeypatch):
    _mock_fetch_page(monkeypatch, {})   # every fetch returns empty content
    _mock_anthropic(monkeypatch, FEATURES_JSON)

    result = enrich.generate_tool_features("Obscure Co", "https://obscure.example")
    assert result is not None
    assert result.low_confidence is True
    assert result.features[0].source_url == ""


def test_generate_tool_features_falls_back_to_homepage_source(monkeypatch):
    _mock_fetch_page(monkeypatch, {"https://runway.com": "Homepage only, no pricing page."})
    _mock_anthropic(monkeypatch, FEATURES_JSON)

    result = enrich.generate_tool_features("Runway", "https://runway.com")
    assert result is not None
    assert result.features[0].source_url == "https://runway.com"


def test_generate_tool_features_skips_features_without_a_name(monkeypatch):
    _mock_fetch_page(monkeypatch, {"https://runway.com": "content"})
    _mock_anthropic(monkeypatch, """{"features": [
        {"feature_name": "", "standalone_available": true, "bundled_only": false, "confident": true},
        {"feature_name": "Real feature", "standalone_available": true, "bundled_only": false, "confident": true}
    ]}""")
    result = enrich.generate_tool_features("Runway", "https://runway.com")
    assert [f.feature_name for f in result.features] == ["Real feature"]


def test_generate_tool_features_returns_none_without_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert enrich.generate_tool_features("Runway", "https://runway.com") is None


# -- scripts/enrich_tool_features.py (dedup + dry-run + selection) ------------

def _draft(name, standalone=True, bundled=False, confident=True):
    return enrich.ToolFeatureDraft(
        feature_name=name, standalone_available=standalone, bundled_only=bundled,
        source_url="https://example.com/pricing", needs_verification=not confident,
    )


@pytest.fixture
def db_path():
    path = tempfile.mktemp(suffix=".db")
    yield path
    if os.path.exists(path):
        os.remove(path)


def test_script_select_tools_by_name(db_path):
    from scripts.enrich_tool_features import _select_tools
    lib = Library(db_path)
    lib.add_tool("Ramp", "Spend", "https://ramp.com", [], approved=1)
    lib.add_tool("Brex", "Spend", "https://brex.com", [], approved=1)
    lib.add_tool("Runway", "FP&A", "https://runway.com", [], approved=1)

    selected = _select_tools(lib, "Ramp,Runway", 0)
    assert sorted(t["name"] for t in selected) == ["Ramp", "Runway"]
    lib.close()


def test_script_select_tools_by_limit(db_path):
    from scripts.enrich_tool_features import _select_tools
    lib = Library(db_path)
    for i in range(5):
        lib.add_tool(f"Tool {i}", "d", f"https://tool{i}.com", [], approved=1)
    selected = _select_tools(lib, "", 3)
    assert len(selected) == 3
    lib.close()


def test_script_writes_features_and_skips_dupes_on_rerun(monkeypatch, db_path):
    import scripts.enrich_tool_features as script_mod
    lib = Library(db_path)
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", [], approved=1)
    lib.close()

    call_count = {"n": 0}

    def _fake_generate(name, url, description="", model=""):
        call_count["n"] += 1
        return enrich.ToolFeaturesResult(
            features=[_draft("Scenario modeling"), _draft("Headcount planning", confident=False)],
            low_confidence=False, model="claude-haiku-4-5-20251001",
            input_tokens=100, output_tokens=80, cost_usd=0.001,
        )

    monkeypatch.setattr(script_mod.enrich_mod, "generate_tool_features", _fake_generate)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setattr(sys, "argv", ["prog", "--db", db_path, "--tools", "Runway"])
    rc = script_mod.main()
    assert rc == 0

    lib = Library(db_path)
    features = lib.list_tool_features(tool_id)
    assert len(features) == 2
    hc = next(f for f in features if f["feature_name"] == "Headcount planning")
    assert hc["needs_verification"] == 1
    assert hc["source"] == "llm_enrichment"
    lib.close()

    # Re-running without --force should skip the tool entirely (already has features)
    monkeypatch.setattr(sys, "argv", ["prog", "--db", db_path, "--tools", "Runway"])
    rc = script_mod.main()
    assert rc == 0
    assert call_count["n"] == 1   # generate_tool_features not called again


def test_script_dry_run_writes_nothing(monkeypatch, db_path):
    import scripts.enrich_tool_features as script_mod
    lib = Library(db_path)
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", [], approved=1)
    lib.close()

    def _fake_generate(name, url, description="", model=""):
        return enrich.ToolFeaturesResult(
            features=[_draft("Scenario modeling")], model="claude-haiku-4-5-20251001",
            input_tokens=50, output_tokens=40, cost_usd=0.0005,
        )

    monkeypatch.setattr(script_mod.enrich_mod, "generate_tool_features", _fake_generate)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setattr(sys, "argv", ["prog", "--db", db_path, "--tools", "Runway", "--dry-run"])
    rc = script_mod.main()
    assert rc == 0

    lib = Library(db_path)
    assert lib.list_tool_features(tool_id) == []
    lib.close()


def test_script_dry_run_prints_feature_detail(monkeypatch, db_path, capsys):
    import scripts.enrich_tool_features as script_mod
    lib = Library(db_path)
    lib.add_tool("Runway", "FP&A", "https://runway.com", [], approved=1)
    lib.close()

    def _fake_generate(name, url, description="", model=""):
        return enrich.ToolFeaturesResult(
            features=[
                _draft("Scenario modeling", standalone=True, bundled=False, confident=True),
                _draft("Headcount planning", standalone=False, bundled=True, confident=False),
            ],
            model="claude-haiku-4-5-20251001", input_tokens=50, output_tokens=40, cost_usd=0.0005,
        )

    monkeypatch.setattr(script_mod.enrich_mod, "generate_tool_features", _fake_generate)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setattr(sys, "argv", ["prog", "--db", db_path, "--tools", "Runway", "--dry-run"])
    rc = script_mod.main()
    assert rc == 0

    out = capsys.readouterr().out
    assert "Scenario modeling: standalone" in out
    assert "Headcount planning: bundled-only" in out
    assert "[needs verification]" in out
    # The confident one shouldn't be flagged
    assert "Scenario modeling: standalone [needs verification]" not in out


def test_script_warns_on_duplicate_tool_names(monkeypatch, db_path, capsys):
    from scripts.enrich_tool_features import _select_tools
    lib = Library(db_path)
    lib.add_tool("Digits", "d1", "https://digits1.example", [], approved=1)
    lib.add_tool("Digits", "d2", "https://digits2.example", [], approved=1)
    lib.add_tool("Runway", "FP&A", "https://runway.com", [], approved=1)
    selected = _select_tools(lib, "Digits,Runway", 0)
    assert len(selected) == 3   # both Digits rows, not deduped
    err = capsys.readouterr().err
    assert "multiple approved rows share these names" in err
    assert "digits" in err
    lib.close()


def test_script_requires_scope_flag(monkeypatch, db_path):
    import scripts.enrich_tool_features as script_mod
    lib = Library(db_path)
    lib.add_tool("Runway", "FP&A", "https://runway.com", [], approved=1)
    lib.close()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setattr(sys, "argv", ["prog", "--db", db_path])
    rc = script_mod.main()
    assert rc == 2


def test_script_requires_api_key(monkeypatch, db_path):
    import scripts.enrich_tool_features as script_mod
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(sys, "argv", ["prog", "--db", db_path, "--tools", "Runway"])
    rc = script_mod.main()
    assert rc == 2
