"""Agent-taxonomy research — LLM enrichment first pass (Software search
overhaul Phase 4b; narrowed to agent-taxonomy only in the Feature Taxonomy
Phase 1b PR 2 legacy retirement, which dropped the feature-drafting half of
this pipeline along with the tool_features table). Covers
linklib.enrich.generate_tool_agent_taxonomy (unit, mocked Claude call +
mocked page fetch) and scripts/enrich_agent_taxonomy.py's selection/dry-run
logic (also mocked — no real API calls in tests).
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
    """contents: url -> content string. Missing urls return empty content.
    Also stubs out _discover_nav_pages (real network via `requests.get`) so it
    falls back to the guessed-path candidates these tests are written
    against, instead of making a real HTTP call in the test process."""
    from linklib import extract

    def _fetch(url, **kw):
        return types.SimpleNamespace(content=contents.get(url, ""))
    monkeypatch.setattr(extract, "fetch_page", _fetch)
    monkeypatch.setattr(enrich, "_discover_nav_pages", lambda base_url, max_pages=10: [])


TAXONOMY_JSON = """{
    "summary": "Runway uses an AI-assisted scenario modeling feature; no named agent found.",
    "confident": true
}"""


def test_generate_tool_agent_taxonomy_parses_result(monkeypatch):
    _mock_fetch_page(monkeypatch, {
        "https://runway.com": "Homepage content about Runway.",
        "https://runway.com/pricing": "Pricing tiers: Starter, Growth, Enterprise.",
    })
    _mock_anthropic(monkeypatch, TAXONOMY_JSON)

    result = enrich.generate_tool_agent_taxonomy("Runway", "https://runway.com", "FP&A for high-growth teams.")
    assert result is not None
    assert "scenario modeling" in result.agent_taxonomy_note
    assert result.agent_taxonomy_needs_verification is False   # confident: true
    assert result.confident is True   # (2026-08 follow-up) same signal, stored separately
    assert result.cost_usd > 0
    assert result.low_confidence is False   # pricing page fetched successfully


def test_generate_tool_agent_taxonomy_parses_confident_false(monkeypatch):
    _mock_fetch_page(monkeypatch, {"https://runway.com": "Homepage content about Runway."})
    _mock_anthropic(monkeypatch, '{"summary": "Unclear agent framing.", "confident": false}')

    result = enrich.generate_tool_agent_taxonomy("Runway", "https://runway.com")
    assert result is not None
    assert result.confident is False
    assert result.agent_taxonomy_needs_verification is True


def test_generate_tool_agent_taxonomy_low_confidence_when_no_pages_fetch(monkeypatch):
    _mock_fetch_page(monkeypatch, {})   # every fetch returns empty content
    _mock_anthropic(monkeypatch, TAXONOMY_JSON)

    result = enrich.generate_tool_agent_taxonomy("Obscure Co", "https://obscure.example")
    assert result is not None
    assert result.low_confidence is True


def test_generate_tool_agent_taxonomy_marks_unconfident_as_needing_verification(monkeypatch):
    _mock_fetch_page(monkeypatch, {"https://runway.com": "Homepage only, no pricing page."})
    _mock_anthropic(monkeypatch, """{"summary": "Some AI-powered marketing language, no specifics.",
        "confident": false}""")

    result = enrich.generate_tool_agent_taxonomy("Runway", "https://runway.com")
    assert result is not None
    assert result.agent_taxonomy_needs_verification is True


def test_generate_tool_agent_taxonomy_returns_none_without_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert enrich.generate_tool_agent_taxonomy("Runway", "https://runway.com") is None


# -- Citations-API grounding fix, Phase 1b: real document blocks -------------

def _mock_anthropic_citing(monkeypatch, payload_json, cited_document_indexes,
                           input_tokens=200, output_tokens=150):
    """Like _mock_anthropic, but the single text block carries a real
    `citations` list (SDK-shaped) referencing document_index positions —
    simulating the API having mechanically grounded the response in the
    document blocks that were sent. Also captures the actual kwargs passed
    to messages.create so a test can assert on the document blocks sent."""
    captured = {}

    def _create(**kw):
        captured.update(kw)

        class _Citation:
            def __init__(self, idx):
                self.document_index = idx

        class _Block:
            type = "text"
            text = payload_json
            citations = [_Citation(i) for i in cited_document_indexes]
        usage = types.SimpleNamespace(
            input_tokens=input_tokens, output_tokens=output_tokens,
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
        )
        return types.SimpleNamespace(content=[_Block()], usage=usage)
    fake = types.SimpleNamespace(Anthropic=lambda *a, **k: types.SimpleNamespace(
        messages=types.SimpleNamespace(create=lambda **kw: _create(**kw))))
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    return captured


def test_generate_tool_agent_taxonomy_sends_real_document_blocks(monkeypatch):
    """Each fetched page rides as its own Citations-API `document` content
    block (not flattened into the prompt string), tagged type="tool_page"."""
    _mock_fetch_page(monkeypatch, {
        "https://runway.com": "Homepage content about Runway.",
        "https://runway.com/pricing": "Pricing tiers: Starter, Growth, Enterprise.",
    })
    captured = _mock_anthropic_citing(monkeypatch, TAXONOMY_JSON, cited_document_indexes=[])

    enrich.generate_tool_agent_taxonomy("Runway", "https://runway.com")

    content = captured["messages"][0]["content"]
    assert isinstance(content, list)   # doc blocks + a trailing text block, not a bare string
    doc_blocks = [b for b in content if b.get("type") == "document"]
    assert len(doc_blocks) == 2
    assert all(b["citations"] == {"enabled": True} for b in doc_blocks)
    bodies = [b["source"]["data"] for b in doc_blocks]
    assert "Homepage content about Runway." in bodies
    assert "Pricing tiers: Starter, Growth, Enterprise." in bodies
    assert content[-1]["type"] == "text"   # drafting instructions ride last


def test_generate_tool_agent_taxonomy_returns_verified_citations_tagged_tool_page(monkeypatch):
    _mock_fetch_page(monkeypatch, {
        "https://runway.com": "Homepage content about Runway.",
        "https://runway.com/pricing": "Pricing tiers: Starter, Growth, Enterprise.",
    })
    _mock_anthropic_citing(monkeypatch, TAXONOMY_JSON, cited_document_indexes=[0, 1])

    result = enrich.generate_tool_agent_taxonomy("Runway", "https://runway.com")
    assert result is not None
    assert len(result.citations) == 2
    assert {c["url"] for c in result.citations} == {"https://runway.com", "https://runway.com/pricing"}
    assert all(c["type"] == "tool_page" for c in result.citations)
    # The stored note itself is untouched plain text — no [n] markers spliced
    # into the JSON output (that would have corrupted the parse entirely).
    assert "[1]" not in result.agent_taxonomy_note
    assert "scenario modeling" in result.agent_taxonomy_note


def test_generate_tool_agent_taxonomy_json_still_parses_when_citations_present(monkeypatch):
    """The real risk this fix introduces: citations enabled on a
    strict-JSON response. Confirms json.loads still succeeds and 'confident'
    still comes through, even with a citation attached mid-response."""
    _mock_fetch_page(monkeypatch, {"https://runway.com": "Homepage content."})
    _mock_anthropic_citing(monkeypatch, TAXONOMY_JSON, cited_document_indexes=[0])

    result = enrich.generate_tool_agent_taxonomy("Runway", "https://runway.com")
    assert result is not None
    assert result.confident is True
    assert result.agent_taxonomy_needs_verification is False


def test_generate_tool_agent_taxonomy_no_citations_when_low_confidence(monkeypatch):
    """No page content fetched at all → no documents sent → nothing to cite,
    regardless of what the (mocked) response claims."""
    _mock_fetch_page(monkeypatch, {})
    _mock_anthropic_citing(monkeypatch, TAXONOMY_JSON, cited_document_indexes=[0])

    result = enrich.generate_tool_agent_taxonomy("Obscure Co", "https://obscure.example")
    assert result is not None
    assert result.low_confidence is True
    assert result.citations == []


# -- scripts/enrich_agent_taxonomy.py (dry-run + selection) -------------------

@pytest.fixture
def db_path():
    path = tempfile.mktemp(suffix=".db")
    yield path
    if os.path.exists(path):
        os.remove(path)


def test_script_select_tools_by_name(db_path):
    from scripts.enrich_agent_taxonomy import _select_tools
    lib = Library(db_path)
    lib.add_tool("Ramp", "Spend", "https://ramp.com", [], approved=1)
    lib.add_tool("Brex", "Spend", "https://brex.com", [], approved=1)
    lib.add_tool("Runway", "FP&A", "https://runway.com", [], approved=1)

    selected = _select_tools(lib, "Ramp,Runway", 0)
    assert sorted(t["name"] for t in selected) == ["Ramp", "Runway"]
    lib.close()


def test_script_select_tools_by_limit(db_path):
    from scripts.enrich_agent_taxonomy import _select_tools
    lib = Library(db_path)
    for i in range(5):
        lib.add_tool(f"Tool {i}", "d", f"https://tool{i}.com", [], approved=1)
    selected = _select_tools(lib, "", 3)
    assert len(selected) == 3
    lib.close()


def test_script_writes_taxonomy_and_skips_already_researched_on_rerun(monkeypatch, db_path):
    import scripts.enrich_agent_taxonomy as script_mod
    lib = Library(db_path)
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", [], approved=1)
    lib.close()

    call_count = {"n": 0}

    def _fake_generate(name, url, description="", model=""):
        call_count["n"] += 1
        return enrich.AgentTaxonomyResult(
            agent_taxonomy_note="Uses AI-assisted scenario modeling; no named agent found.",
            agent_taxonomy_needs_verification=True,
            low_confidence=False, model="claude-haiku-4-5-20251001",
            input_tokens=100, output_tokens=80, cost_usd=0.001,
        )

    monkeypatch.setattr(script_mod.enrich_mod, "generate_tool_agent_taxonomy", _fake_generate)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setattr(sys, "argv", ["prog", "--db", db_path, "--tools", "Runway"])
    rc = script_mod.main()
    assert rc == 0

    lib = Library(db_path)
    tool = lib.get_tool(tool_id)
    assert tool["agent_taxonomy_note"]
    assert tool["agent_taxonomy_needs_verification"] == 1
    lib.close()

    # Re-running without --force should skip the tool entirely (already has an
    # agent-taxonomy note)
    monkeypatch.setattr(sys, "argv", ["prog", "--db", db_path, "--tools", "Runway"])
    rc = script_mod.main()
    assert rc == 0
    assert call_count["n"] == 1   # generate_tool_agent_taxonomy not called again


def test_script_dry_run_writes_nothing(monkeypatch, db_path):
    import scripts.enrich_agent_taxonomy as script_mod
    lib = Library(db_path)
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", [], approved=1)
    lib.close()

    def _fake_generate(name, url, description="", model=""):
        return enrich.AgentTaxonomyResult(
            agent_taxonomy_note="Uses AI-assisted scenario modeling.",
            agent_taxonomy_needs_verification=False,
            model="claude-haiku-4-5-20251001", input_tokens=50, output_tokens=40, cost_usd=0.0005,
        )

    monkeypatch.setattr(script_mod.enrich_mod, "generate_tool_agent_taxonomy", _fake_generate)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setattr(sys, "argv", ["prog", "--db", db_path, "--tools", "Runway", "--dry-run"])
    rc = script_mod.main()
    assert rc == 0

    lib = Library(db_path)
    assert not (lib.get_tool(tool_id)["agent_taxonomy_note"] or "").strip()
    lib.close()


def test_script_dry_run_prints_taxonomy_detail(monkeypatch, db_path, capsys):
    import scripts.enrich_agent_taxonomy as script_mod
    lib = Library(db_path)
    lib.add_tool("Runway", "FP&A", "https://runway.com", [], approved=1)
    lib.close()

    def _fake_generate(name, url, description="", model=""):
        return enrich.AgentTaxonomyResult(
            agent_taxonomy_note="Uses AI-assisted scenario modeling; no named agent found.",
            agent_taxonomy_needs_verification=True,
            model="claude-haiku-4-5-20251001", input_tokens=50, output_tokens=40, cost_usd=0.0005,
        )

    monkeypatch.setattr(script_mod.enrich_mod, "generate_tool_agent_taxonomy", _fake_generate)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setattr(sys, "argv", ["prog", "--db", db_path, "--tools", "Runway", "--dry-run"])
    rc = script_mod.main()
    assert rc == 0

    out = capsys.readouterr().out
    assert "Agent taxonomy: Uses AI-assisted scenario modeling" in out
    assert "[needs verification]" in out


def test_script_warns_on_duplicate_tool_names(monkeypatch, db_path, capsys):
    from scripts.enrich_agent_taxonomy import _select_tools
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
    import scripts.enrich_agent_taxonomy as script_mod
    lib = Library(db_path)
    lib.add_tool("Runway", "FP&A", "https://runway.com", [], approved=1)
    lib.close()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setattr(sys, "argv", ["prog", "--db", db_path])
    rc = script_mod.main()
    assert rc == 2


def test_script_requires_api_key(monkeypatch, db_path):
    import scripts.enrich_agent_taxonomy as script_mod
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    open(db_path, "a").close()  # resolve_db_path now requires the file to exist
    monkeypatch.setattr(sys, "argv", ["prog", "--db", db_path, "--tools", "Runway"])
    rc = script_mod.main()
    assert rc == 2
