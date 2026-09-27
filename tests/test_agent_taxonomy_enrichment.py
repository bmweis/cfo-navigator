"""Agent-taxonomy research — LLM enrichment first pass (Software search
overhaul Phase 4b; narrowed to agent-taxonomy only in the Feature Taxonomy
Phase 1b PR 2 legacy retirement, which dropped the feature-drafting half of
this pipeline along with the tool_features table). Covers
linklib.enrich.generate_tool_agent_taxonomy (unit, mocked Claude call +
mocked page fetch, voice_core="Test voice guide.") and scripts/enrich_agent_taxonomy.py's selection/dry-run
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


def _mock_anthropic(monkeypatch, raw_text, input_tokens=200, output_tokens=150):
    """raw_text: the plain-prose response body, e.g. a note ending in its
    own trailing "CONFIDENT: true|false" sentinel line (the new contract —
    see CLAUDE.md's citation-tag-investigation follow-up; this stopped
    being a JSON payload)."""
    def _create(**kw):
        class _Block:
            type = "text"
            text = raw_text
            citations = []
        usage = types.SimpleNamespace(
            input_tokens=input_tokens, output_tokens=output_tokens,
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
        )
        return types.SimpleNamespace(content=[_Block()], usage=usage)
    fake = types.SimpleNamespace(Anthropic=lambda *a, **k: types.SimpleNamespace(
        messages=types.SimpleNamespace(create=lambda **kw: _create(**kw))))
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")


# Padding filler so a short, readable test content string (e.g. "Homepage
# content about Runway.") still clears extract.assess_extraction_quality's
# real 60-word floor (2026-09 JS-render grounding fix) — appended, never
# prepended, so every existing "starts with ..." assertion still holds and
# a body-content substring check (e.g. `"Pricing tiers..." in body`) still
# finds the original text intact.
_PAD = (
    "This page also describes how finance teams evaluate the product, what "
    "onboarding looks like, and how pricing scales with usage across a "
    "typical growth-stage company's planning cycle from quarter to quarter."
)


def _mock_fetch_page(monkeypatch, contents: dict):
    """contents: url -> content string. Missing urls simulate a genuine 404
    (fetch_error set, no Exa attempt — see _fetch_grounding_page's own
    docstring for why a fetch_error skips the Exa fallback entirely), the
    expected shape for a guessed Product/Solutions path that doesn't exist.
    A present, non-empty value is padded to clear the real 60-word floor
    (extract.assess_extraction_quality) unless it's already long enough.
    Also stubs out _discover_nav_pages (real network via `requests.get`) so
    it falls back to the guessed-path candidates these tests are written
    against, instead of making a real HTTP call in the test process."""
    from linklib import extract

    def _fetch(url, **kw):
        raw = contents.get(url)
        if raw is None:
            return types.SimpleNamespace(content="", raw_html="", blocked=False, fetch_error="HTTP 404")
        if not raw:
            # A genuinely thin/empty 200 OK — the JS-shell shape, deliberately
            # left unpadded so the real quality gate fails it and the Exa
            # fallback actually gets a chance to run.
            return types.SimpleNamespace(content="", raw_html="", blocked=False, fetch_error="")
        content = raw
        while len(content.split()) < 65:
            content = f"{content} {_PAD}"
        return types.SimpleNamespace(content=content, raw_html=content, blocked=False, fetch_error="")
    monkeypatch.setattr(extract, "fetch_page", _fetch)
    monkeypatch.setattr(enrich, "_discover_nav_pages", lambda base_url, max_pages=10: [])


def _mock_exa_fallback(monkeypatch, text="", cost=0.0):
    from linklib import medium_platform
    monkeypatch.setattr(medium_platform, "fetch_content_by_url", lambda lib, url: (text, cost))


# A realistic ~90-word page body — long enough to clear extract.
# assess_extraction_quality's real 60-word floor on its own, for every test
# that mocks a successful Exa-fallback recovery.
EXA_RECOVERED_CONTENT = (
    "Obscure Co builds a finance automation product for small teams that need help "
    "closing the books each month without hiring a full accounting staff. The platform "
    "connects to a company's bank feeds and general ledger, then reconciles transactions "
    "and flags anything that looks unusual before a human ever has to look at it. Finance "
    "leads use it to close faster and to spend less time on manual reconciliation work "
    "every month, freeing them up to focus on forecasting and planning instead of data entry."
)


TAXONOMY_RESPONSE = (
    "Runway uses an AI-assisted scenario modeling feature; no named agent found.\n\n"
    "CONFIDENT: true"
)


def test_generate_tool_agent_taxonomy_parses_result(monkeypatch):
    _mock_fetch_page(monkeypatch, {
        "https://runway.com": "Homepage content about Runway.",
        "https://runway.com/pricing": "Pricing tiers: Starter, Growth, Enterprise.",
    })
    _mock_anthropic(monkeypatch, TAXONOMY_RESPONSE)

    result = enrich.generate_tool_agent_taxonomy("Runway", "https://runway.com", "FP&A for high-growth teams.", voice_core="Test voice guide.")
    assert result is not None
    assert "scenario modeling" in result.agent_taxonomy_note
    assert result.agent_taxonomy_needs_verification is False   # confident: true
    assert result.confident is True   # (2026-08 follow-up) same signal, stored separately
    assert result.cost_usd > 0
    assert result.low_confidence is False   # pricing page fetched successfully


def test_generate_tool_agent_taxonomy_parses_confident_false(monkeypatch):
    _mock_fetch_page(monkeypatch, {"https://runway.com": "Homepage content about Runway."})
    _mock_anthropic(monkeypatch, "Unclear agent framing.\n\nCONFIDENT: false")

    result = enrich.generate_tool_agent_taxonomy("Runway", "https://runway.com", voice_core="Test voice guide.")
    assert result is not None
    assert result.confident is False
    assert result.agent_taxonomy_needs_verification is True


def test_generate_tool_agent_taxonomy_low_confidence_when_homepage_fetched_via_exa(monkeypatch):
    """2026-09 JS-render grounding fix: every candidate's direct fetch is a
    genuine 404 (guessed paths — see _mock_fetch_page) except the homepage,
    which is a too-thin JS shell that recovers via the Exa fallback.
    low_confidence=True (a second-choice route), but the draft still
    succeeds and is still grounded on real content."""
    _mock_fetch_page(monkeypatch, {"https://obscure.example": ""})   # homepage: 200 OK, empty body
    _mock_exa_fallback(monkeypatch, EXA_RECOVERED_CONTENT, cost=0.007)
    _mock_anthropic(monkeypatch, TAXONOMY_RESPONSE)

    result = enrich.generate_tool_agent_taxonomy("Obscure Co", "https://obscure.example", voice_core="Test voice guide.")
    assert result is not None
    assert result.low_confidence is True
    assert result.exa_cost_usd == 0.007


def test_generate_tool_agent_taxonomy_raises_when_nothing_fetches(monkeypatch):
    """The non-negotiable refusal (2026-09 JS-render grounding fix): every
    candidate 404s (a genuine fetch_error, so Exa is never even tried for
    the guessed paths — see _fetch_grounding_page), and the homepage's own
    Exa fallback also finds nothing — generate_tool_agent_taxonomy raises
    GroundingUnavailable instead of drafting a hedge from the model's own
    knowledge."""
    _mock_fetch_page(monkeypatch, {"https://obscure.example": ""})
    _mock_exa_fallback(monkeypatch, "", cost=0.0)
    _mock_anthropic(monkeypatch, TAXONOMY_RESPONSE)

    with pytest.raises(enrich.GroundingUnavailable) as exc_info:
        enrich.generate_tool_agent_taxonomy("Obscure Co", "https://obscure.example", voice_core="Test voice guide.")
    assert exc_info.value.url == "https://obscure.example"


def test_generate_tool_agent_taxonomy_marks_unconfident_as_needing_verification(monkeypatch):
    _mock_fetch_page(monkeypatch, {"https://runway.com": "Homepage only, no pricing page."})
    _mock_anthropic(monkeypatch, "Some AI-powered marketing language, no specifics.\n\nCONFIDENT: false")

    result = enrich.generate_tool_agent_taxonomy("Runway", "https://runway.com", voice_core="Test voice guide.")
    assert result is not None
    assert result.agent_taxonomy_needs_verification is True


def test_generate_tool_agent_taxonomy_returns_none_without_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert enrich.generate_tool_agent_taxonomy("Runway", "https://runway.com", voice_core="Test voice guide.") is None


# -- Citations-API grounding fix, Phase 1b: real document blocks -------------

def _mock_anthropic_citing(monkeypatch, blocks, input_tokens=200, output_tokens=150):
    """blocks: list of (text, cited_document_indexes) — one SDK text block
    per entry, each with its own `citations` list. Mirrors how the real
    Citations API actually splits a response into separate blocks at
    citation boundaries (a cited claim and an uncited trailing sentinel
    line are different blocks, never one block carrying both) — a single
    flat block would let inject_markers=True append a citation marker
    after content that was never actually cited. Also captures the actual
    kwargs passed to messages.create so a test can assert on the document
    blocks sent."""
    captured = {}

    def _create(**kw):
        captured.update(kw)

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
            input_tokens=input_tokens, output_tokens=output_tokens,
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
        )
        return types.SimpleNamespace(content=content, usage=usage)
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
    captured = _mock_anthropic_citing(monkeypatch, [(TAXONOMY_RESPONSE, [])])

    enrich.generate_tool_agent_taxonomy("Runway", "https://runway.com", voice_core="Test voice guide.")

    content = captured["messages"][0]["content"]
    assert isinstance(content, list)   # doc blocks + a trailing text block, not a bare string
    doc_blocks = [b for b in content if b.get("type") == "document"]
    assert len(doc_blocks) == 2
    assert all(b["citations"] == {"enabled": True} for b in doc_blocks)
    bodies = [b["source"]["data"] for b in doc_blocks]
    assert any("Homepage content about Runway." in b for b in bodies)
    assert any("Pricing tiers: Starter, Growth, Enterprise." in b for b in bodies)
    assert content[-1]["type"] == "text"   # drafting instructions ride last


def test_generate_tool_agent_taxonomy_returns_verified_citations_tagged_tool_page(monkeypatch):
    _mock_fetch_page(monkeypatch, {
        "https://runway.com": "Homepage content about Runway.",
        "https://runway.com/pricing": "Pricing tiers: Starter, Growth, Enterprise.",
    })
    _mock_anthropic_citing(monkeypatch, [
        ("Runway uses an AI-assisted scenario modeling feature.", [0, 1]),
        ("\n\nCONFIDENT: true", []),
    ])

    result = enrich.generate_tool_agent_taxonomy("Runway", "https://runway.com", voice_core="Test voice guide.")
    assert result is not None
    assert len(result.citations) == 2
    assert {c["url"] for c in result.citations} == {"https://runway.com", "https://runway.com/pricing"}
    assert all(c["type"] == "tool_page" for c in result.citations)
    # The cited claim gets a real [n] marker spliced in by extract_citations
    # itself (inject_markers=True) — the new intended footnote rendering,
    # not the old "never touch the text, citations are JSON-unsafe" behavior.
    assert "[1]" in result.agent_taxonomy_note or "[2]" in result.agent_taxonomy_note
    assert "scenario modeling" in result.agent_taxonomy_note
    # The trailing sentinel line itself is stripped, not left dangling in
    # the stored note.
    assert "CONFIDENT" not in result.agent_taxonomy_note


def test_generate_tool_agent_taxonomy_confident_still_parses_when_citations_present(monkeypatch):
    """The real risk this fix targets: citations enabled alongside a
    trailing CONFIDENT sentinel line. Confirms the sentinel still parses
    correctly even with a real citation attached earlier in the response."""
    _mock_fetch_page(monkeypatch, {"https://runway.com": "Homepage content."})
    _mock_anthropic_citing(monkeypatch, [
        ("Runway uses an AI-assisted scenario modeling feature.", [0]),
        ("\n\nCONFIDENT: true", []),
    ])

    result = enrich.generate_tool_agent_taxonomy("Runway", "https://runway.com", voice_core="Test voice guide.")
    assert result is not None
    assert result.confident is True
    assert result.agent_taxonomy_needs_verification is False


def test_generate_tool_agent_taxonomy_still_cites_a_page_fetched_via_exa(monkeypatch):
    """2026-09 JS-render grounding fix: a page recovered via the Exa
    fallback still rides as a real Citations-API document block — the
    fallback doesn't mean "ungrounded," only "needed a second-choice
    route." See test_generate_tool_agent_taxonomy_raises_when_nothing_
    fetches for the case where nothing at all could be recovered."""
    _mock_fetch_page(monkeypatch, {"https://obscure.example": ""})
    _mock_exa_fallback(monkeypatch, EXA_RECOVERED_CONTENT, cost=0.007)
    _mock_anthropic_citing(monkeypatch, [(TAXONOMY_RESPONSE, [0])])

    result = enrich.generate_tool_agent_taxonomy("Obscure Co", "https://obscure.example", voice_core="Test voice guide.")
    assert result is not None
    assert result.low_confidence is True
    assert len(result.citations) == 1
    assert result.citations[0]["url"] == "https://obscure.example"


# -- scripts/enrich_agent_taxonomy.py (dry-run + selection) -------------------

@pytest.fixture
def db_path():
    path = tempfile.mktemp(suffix=".db")
    # 2026-08 visibility follow-up: the script now refuses (require_voice_setting)
    # unless voice_core is seeded.
    _seed_lib = Library(path)
    _seed_lib.seed_voice_prompts()
    _seed_lib.close()
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

    def _fake_generate(name, url, description="", model="", voice_core=""):
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

    def _fake_generate(name, url, description="", model="", voice_core=""):
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

    def _fake_generate(name, url, description="", model="", voice_core=""):
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
