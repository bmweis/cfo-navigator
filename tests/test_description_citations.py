"""Citations-API grounding fix, Phase 2 — Description field. Same pattern
as Phase 1b's Agent taxonomy (tests/test_agent_taxonomy_enrichment.py):
covers linklib.enrich.generate_tool_description's real document-block
grounding (unit, mocked Claude call + mocked page fetch), the server-side
`_validate_citations_payload` guard on the browser-carried citations
payload, and the two submit routes (new-tool, existing-tool edit) that
persist/clear entity_citations for field_name='description'.
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


def _mock_fetch_page(monkeypatch, content=""):
    """2026-09 JS-render grounding fix: generate_tool_description now gates
    every fetch through extract.assess_extraction_quality (a real word-count
    floor plus paywall/bot-challenge checks), not a bare truthiness check —
    so the mocked PageData needs raw_html/blocked/fetch_error too, not just
    content. `content` must be >=60 words for a test that expects a
    successful, direct (not Exa-fallback) draft — see LONG_PAGE_CONTENT."""
    from linklib import extract
    monkeypatch.setattr(extract, "fetch_page", lambda url, **kw: types.SimpleNamespace(
        content=content, raw_html=content, blocked=False, fetch_error=""))


def _mock_exa_fallback(monkeypatch, text="", cost=0.0):
    """Mocks the Exa fallback generate_tool_description/generate_tool_agent_
    taxonomy/generate_community_profile/generate_community_listing all fall
    back to when the direct fetch is blocked/thin/unreachable — see
    linklib.enrich._fetch_grounding_page. Not mocking this at all (the
    default in every other test here) exercises the real function, which
    safely no-ops to ("", 0.0) with no EXA_API_KEY set — exactly the "Exa
    fallback also produced nothing" case those tests need."""
    from linklib import medium_platform
    monkeypatch.setattr(medium_platform, "fetch_content_by_url", lambda lib, url: (text, cost))


# A realistic ~110-word page body — long enough to clear
# extract.assess_extraction_quality's real 60-word floor, for every test
# below that expects a successful DIRECT (non-Exa-fallback) fetch.
LONG_PAGE_CONTENT = (
    "Runway is a financial planning platform for finance teams at growth-stage companies. "
    "It consolidates budgeting, forecasting, and headcount planning into one collaborative "
    "workspace built for FP&A analysts and controllers who need to model scenarios quickly. "
    "Teams connect their general ledger and payroll systems, then build driver-based models "
    "that update automatically as actuals come in from month to month. The platform is used "
    "by finance leaders who need to answer board questions about runway, burn, and hiring "
    "plans without waiting on a spreadsheet rebuild every single time a number changes."
)


def _mock_anthropic_citing(monkeypatch, blocks, input_tokens=200, output_tokens=150):
    """blocks: list of (text, cited_document_indexes) — one SDK text block
    per entry, each with its own `citations` list. Mirrors how the real
    Citations API actually splits a response into separate blocks at
    citation boundaries (a cited claim and an uncited trailing sentinel
    line are different blocks, never one block carrying both) — a single
    flat block would let inject_markers=True append a citation marker
    after content that was never actually cited. Also captures the actual
    messages.create kwargs so a test can assert on the document block sent."""
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


# Plain prose + trailing sentinel lines (SUMMARY, CONFIDENT) — see
# CLAUDE.md's citation-tag-investigation follow-up; this stopped being a
# JSON payload.
DESC_BODY = "Runway is a financial planning platform for finance teams at growth-stage companies."
DESC_TAIL = (
    "\n\nSUMMARY: Runway is an FP&A platform for growth-stage finance teams.\n\n"
    "CONFIDENT: true"
)


# -- generate_tool_description: real document-block grounding ----------------

def test_generate_tool_description_sends_real_document_block(monkeypatch):
    _mock_fetch_page(monkeypatch, LONG_PAGE_CONTENT)
    captured = _mock_anthropic_citing(monkeypatch, [(DESC_BODY + DESC_TAIL, [])])

    enrich.generate_tool_description("Runway", "https://runway.com", voice_core="Test voice guide.")

    content = captured["messages"][0]["content"]
    assert isinstance(content, list)   # doc block + a trailing text block, not a bare string
    doc_blocks = [b for b in content if b.get("type") == "document"]
    assert len(doc_blocks) == 1
    assert doc_blocks[0]["citations"] == {"enabled": True}
    assert doc_blocks[0]["source"]["data"] == LONG_PAGE_CONTENT
    assert content[-1]["type"] == "text"   # drafting instructions ride last


def test_generate_tool_description_returns_verified_citations_tagged_tool_page(monkeypatch):
    _mock_fetch_page(monkeypatch, LONG_PAGE_CONTENT)
    _mock_anthropic_citing(monkeypatch, [(DESC_BODY, [0]), (DESC_TAIL, [])])

    draft = enrich.generate_tool_description("Runway", "https://runway.com", voice_core="Test voice guide.")
    assert draft is not None
    assert len(draft.citations) == 1
    assert draft.citations[0]["url"] == "https://runway.com"
    assert draft.citations[0]["type"] == "tool_page"
    # The cited write-up gets a real [n] marker spliced in by
    # extract_citations itself (inject_markers=True) — the new intended
    # footnote rendering, not the old "citations are JSON-unsafe, never
    # touch the text" behavior.
    assert "[1]" in draft.description
    assert "financial planning platform" in draft.description
    # The trailing sentinel lines are stripped out, not left dangling.
    assert "SUMMARY" not in draft.description
    assert "CONFIDENT" not in draft.description
    assert draft.low_confidence is False   # a real direct fetch, no Exa fallback needed


def test_generate_tool_description_confident_still_parses_when_citations_present(monkeypatch):
    """The real risk this fix targets: citations enabled alongside trailing
    SUMMARY/CONFIDENT sentinel lines. Confirms both sentinels still parse
    correctly even with a real citation attached earlier in the response."""
    _mock_fetch_page(monkeypatch, LONG_PAGE_CONTENT)
    _mock_anthropic_citing(monkeypatch, [(DESC_BODY, [0]), (DESC_TAIL, [])])

    draft = enrich.generate_tool_description("Runway", "https://runway.com", voice_core="Test voice guide.")
    assert draft is not None
    assert draft.confident is True
    assert draft.summary == "Runway is an FP&A platform for growth-stage finance teams."


def test_generate_tool_description_low_confidence_and_still_cited_when_fetched_via_exa(monkeypatch):
    """2026-09 JS-render grounding fix: a direct fetch that's too thin (the
    JS-shell shape — the page loaded, but the plain-text extraction is way
    under the 60-word floor) falls back to Exa. When Exa recovers real,
    substantive content, the draft still succeeds — low_confidence=True
    (a second-choice fetch route, worth a second look) but NOT ungrounded:
    the Exa-recovered text still rides as a real Citations-API document
    block, so citations are populated exactly like a direct-fetch draft."""
    _mock_fetch_page(monkeypatch, "Loading…")   # a near-empty JS shell — 1 word, fails the gate
    _mock_exa_fallback(monkeypatch, LONG_PAGE_CONTENT, cost=0.007)
    _mock_anthropic_citing(monkeypatch, [(DESC_BODY, [0]), (DESC_TAIL, [])])

    draft = enrich.generate_tool_description("Runway", "https://runway.com", voice_core="Test voice guide.")
    assert draft is not None
    assert draft.low_confidence is True
    assert draft.exa_cost_usd == 0.007
    assert len(draft.citations) == 1
    assert draft.citations[0]["url"] == "https://runway.com"


def test_generate_tool_description_raises_when_direct_and_exa_both_unusable(monkeypatch):
    """The non-negotiable refusal (2026-09 JS-render grounding fix): when
    neither the direct fetch nor the Exa fallback can produce anything
    usable, generate_tool_description raises GroundingUnavailable instead
    of silently drafting a hedge from the model's own knowledge — this
    site's radical-transparency standard renders a pending/low-confidence
    field to every visitor with a badge, never hides it, so an ungrounded
    hedge would still be live, public copy about a real vendor."""
    _mock_fetch_page(monkeypatch, "")   # empty direct fetch
    _mock_exa_fallback(monkeypatch, "", cost=0.0)   # Exa also finds nothing
    _mock_anthropic_citing(monkeypatch, [(DESC_BODY, [0]), (DESC_TAIL, [])])

    with pytest.raises(enrich.GroundingUnavailable) as exc_info:
        enrich.generate_tool_description("Obscure Co", "https://obscure.example", voice_core="Test voice guide.")
    assert exc_info.value.url == "https://obscure.example"
    assert exc_info.value.reason == "too-thin"


# -- _validate_citations_payload (server-side guard on browser input) --------

@pytest.fixture
def app_module(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    # 2026-08 visibility follow-up: the generate-* routes now refuse
    # (require_voice_setting) unless voice_core is seeded.
    _seed_lib = Library(db)
    _seed_lib.seed_voice_prompts()
    _seed_lib.close()
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    yield appmod
    if os.path.exists(db):
        os.remove(db)


def test_validate_citations_payload_accepts_well_formed_list(app_module):
    raw = '[{"n": 1, "title": "Runway", "url": "https://runway.com", "type": "tool_page"}]'
    out = app_module._validate_citations_payload(raw)
    assert out == [{"n": 1, "title": "Runway", "url": "https://runway.com", "type": "tool_page"}]


def test_validate_citations_payload_rejects_non_list_top_level(app_module):
    assert app_module._validate_citations_payload('{"url": "https://runway.com"}') == []


def test_validate_citations_payload_rejects_malformed_json(app_module):
    assert app_module._validate_citations_payload("not json") == []


def test_validate_citations_payload_drops_non_http_url(app_module):
    raw = '[{"title": "evil", "url": "javascript:alert(1)"}]'
    assert app_module._validate_citations_payload(raw) == []


def test_validate_citations_payload_drops_missing_url(app_module):
    raw = '[{"title": "no url here"}]'
    assert app_module._validate_citations_payload(raw) == []


def test_validate_citations_payload_falls_back_title_to_url(app_module):
    raw = '[{"url": "https://runway.com"}]'
    out = app_module._validate_citations_payload(raw)
    assert out == [{"n": 1, "title": "https://runway.com", "url": "https://runway.com", "type": "tool_page"}]


def test_validate_citations_payload_caps_title_length(app_module):
    long_title = "x" * 500
    raw = f'[{{"title": "{long_title}", "url": "https://runway.com"}}]'
    out = app_module._validate_citations_payload(raw)
    assert len(out[0]["title"]) == app_module._MAX_CITATION_TITLE_LEN


def test_validate_citations_payload_renumbers_after_dropping_malformed_entry(app_module):
    raw = ('[{"title": "good1", "url": "https://a.example"}, '
           '{"title": "bad", "url": "javascript:x"}, '
           '{"title": "good2", "url": "https://b.example"}]')
    out = app_module._validate_citations_payload(raw)
    assert [c["n"] for c in out] == [1, 2]
    assert [c["url"] for c in out] == ["https://a.example", "https://b.example"]


def test_validate_citations_payload_empty_string(app_module):
    assert app_module._validate_citations_payload("") == []


# -- generate-description AJAX route: returns citations + model --------------

def _client(appmod):
    from fastapi.testclient import TestClient
    return TestClient(appmod.app, raise_server_exceptions=True)


def _login(client):
    r = client.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    assert r.status_code in (302, 303)


def test_generate_description_route_returns_citations_and_model(app_module, monkeypatch):
    _mock_fetch_page(monkeypatch, LONG_PAGE_CONTENT)
    _mock_anthropic_citing(monkeypatch, [(DESC_BODY, [0]), (DESC_TAIL, [])])

    client = _client(app_module)
    _login(client)
    r = client.post("/admin/tools/software/generate-description",
                     json={"name": "Runway", "url": "https://runway.com"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert len(body["citations"]) == 1
    assert body["citations"][0]["url"] == "https://runway.com"
    assert body["model"]


# -- new-tool submit route: closes the pre-existing needs_verification/ ------
# -- confidence/citations gap for a brand-new tool ----------------------------

def test_new_tool_fresh_draft_records_needs_verification_confidence_and_citations(app_module):
    client = _client(app_module)
    _login(client)
    citations_json = '[{"n": 1, "title": "Runway", "url": "https://runway.com", "type": "tool_page"}]'
    r = client.post("/admin/tools/software/new", data={
        "name": "Runway", "url": "https://runway.com",
        "description": "AI-drafted description.", "summary": "AI-drafted summary.",
        "ai_drafted_fields": "description,summary",
        "ai_drafted_confidence": "description:1",
        "ai_drafted_citations": citations_json,
        "ai_drafted_citations_model": "claude-opus-5",
    }, follow_redirects=False)
    assert r.status_code == 303

    lib = Library(os.environ["LINKLIB_DB"])
    tool = lib.list_tools(approved_only=True)[0]
    assert tool["description_needs_verification"] == 1
    assert tool["description_ai_confident"] == 1
    citations = lib.get_entity_citations("tool", tool["id"], "description")
    assert len(citations) == 1
    assert citations[0]["url"] == "https://runway.com"
    lib.close()


def test_new_tool_hand_written_description_records_no_citations_or_verification_flag(app_module):
    """A tool added without ever clicking Generate (no ai_drafted_fields at
    all) — the pre-fix behavior for description_needs_verification/
    description_ai_confident, unaffected by this change."""
    client = _client(app_module)
    _login(client)
    r = client.post("/admin/tools/software/new", data={
        "name": "Runway", "url": "https://runway.com",
        "description": "Hand-written description.", "summary": "Hand-written summary.",
    }, follow_redirects=False)
    assert r.status_code == 303

    lib = Library(os.environ["LINKLIB_DB"])
    tool = lib.list_tools(approved_only=True)[0]
    assert tool["description_needs_verification"] == 0
    assert tool["description_ai_confident"] is None
    assert lib.get_entity_citations("tool", tool["id"], "description") == []
    lib.close()


def test_new_tool_malformed_citations_payload_persists_nothing(app_module):
    client = _client(app_module)
    _login(client)
    r = client.post("/admin/tools/software/new", data={
        "name": "Runway", "url": "https://runway.com",
        "description": "AI-drafted description.", "summary": "AI-drafted summary.",
        "ai_drafted_fields": "description,summary",
        "ai_drafted_citations": "not json",
    }, follow_redirects=False)
    assert r.status_code == 303

    lib = Library(os.environ["LINKLIB_DB"])
    tool = lib.list_tools(approved_only=True)[0]
    assert lib.get_entity_citations("tool", tool["id"], "description") == []
    lib.close()


# -- existing-tool edit-submit route: persist / clear -------------------------

def _add_tool(lib):
    return lib.add_tool("Runway", "Old description.", "https://runway.com", [],
                        approved=1, summary="Old summary.")


def test_edit_submit_persists_citations_for_fresh_draft(app_module):
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = _add_tool(lib)
    slug = lib.get_tool(tool_id)["slug"]
    lib.close()

    client = _client(app_module)
    _login(client)
    citations_json = '[{"n": 1, "title": "Runway", "url": "https://runway.com", "type": "tool_page"}]'
    r = client.post(f"/tools/software/{slug}/edit", data={
        "name": "Runway", "url": "https://runway.com",
        "description": "Freshly generated description.", "summary": "Freshly generated summary.",
        "ai_drafted_fields": "description,summary",
        "ai_drafted_confidence": "description:1",
        "ai_drafted_citations": citations_json,
        "ai_drafted_citations_model": "claude-opus-5",
    }, follow_redirects=False)
    assert r.status_code == 303

    lib = Library(os.environ["LINKLIB_DB"])
    tool = lib.get_tool(tool_id)
    assert tool["description_needs_verification"] == 1
    citations = lib.get_entity_citations("tool", tool_id, "description")
    assert len(citations) == 1
    assert citations[0]["url"] == "https://runway.com"
    lib.close()


def test_edit_submit_clears_citations_on_hand_edit(app_module):
    """A save that doesn't follow a fresh Generate click — even one that
    changes the description text by hand — clears any prior AI citations,
    same convention update_tool_agent_taxonomy already applies."""
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = _add_tool(lib)
    slug = lib.get_tool(tool_id)["slug"]
    lib.set_entity_citations("tool", tool_id, "description",
                             [{"n": 1, "title": "Old source", "url": "https://old.example", "type": "tool_page"}],
                             model="claude-opus-5")
    lib.close()

    client = _client(app_module)
    _login(client)
    r = client.post(f"/tools/software/{slug}/edit", data={
        "name": "Runway", "url": "https://runway.com",
        "description": "Hand-edited description, no Generate this session.",
        "summary": "Hand-edited summary.",
    }, follow_redirects=False)
    assert r.status_code == 303

    lib = Library(os.environ["LINKLIB_DB"])
    tool = lib.get_tool(tool_id)
    assert tool["description_needs_verification"] == 0
    assert lib.get_entity_citations("tool", tool_id, "description") == []
    lib.close()


def test_edit_submit_clears_citations_when_only_other_fields_change(app_module):
    """Resaving the form for an unrelated reason (no fresh description
    draft this session) is itself treated as a review — needs_verification
    and citations both clear, matching description_needs_verification's own
    existing convention."""
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = _add_tool(lib)
    slug = lib.get_tool(tool_id)["slug"]
    lib.update_tool(tool_id, "Runway", "AI description.", "https://runway.com", [],
                    summary="AI summary.", description_needs_verification=1, description_ai_confident=1)
    lib.set_entity_citations("tool", tool_id, "description",
                             [{"n": 1, "title": "Source", "url": "https://runway.com", "type": "tool_page"}],
                             model="claude-opus-5")
    lib.close()

    client = _client(app_module)
    _login(client)
    r = client.post(f"/tools/software/{slug}/edit", data={
        "name": "Runway", "url": "https://runway.com",
        "description": "AI description.", "summary": "AI summary.",
        "promoted": "1",
    }, follow_redirects=False)
    assert r.status_code == 303

    lib = Library(os.environ["LINKLIB_DB"])
    tool = lib.get_tool(tool_id)
    assert tool["description_needs_verification"] == 0
    assert lib.get_entity_citations("tool", tool_id, "description") == []
    lib.close()


# -- rendering: public cap, admin uncapped, and the (deliberate) lack -------
# -- of a publish gate for Description ----------------------------------------

def test_public_profile_shows_capped_sources_list(app_module):
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = _add_tool(lib)
    slug = lib.get_tool(tool_id)["slug"]
    citations = [{"n": i + 1, "title": f"Source {i}", "url": f"https://source{i}.example", "type": "tool_page"}
                 for i in range(7)]
    lib.set_entity_citations("tool", tool_id, "description", citations, model="claude-opus-5")
    lib.close()

    client = _client(app_module)
    r = client.get(f"/tools/software/{slug}")
    assert r.status_code == 200
    html = r.text
    assert html.count("source0.example") == 1
    assert "source6.example" not in html   # capped at 5 — the 7th source is dropped


def test_admin_edit_page_shows_uncapped_sources_list(app_module):
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = _add_tool(lib)
    slug = lib.get_tool(tool_id)["slug"]
    citations = [{"n": i + 1, "title": f"Source {i}", "url": f"https://source{i}.example", "type": "tool_page"}
                 for i in range(7)]
    lib.set_entity_citations("tool", tool_id, "description", citations, model="claude-opus-5")
    lib.close()

    client = _client(app_module)
    _login(client)
    r = client.get(f"/tools/software/{slug}/edit")
    assert r.status_code == 200
    assert "source6.example" in r.text   # uncapped for the admin reviewer


def test_admin_edit_page_shows_empty_note_when_no_citations(app_module):
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = _add_tool(lib)
    slug = lib.get_tool(tool_id)["slug"]
    lib.close()

    client = _client(app_module)
    _login(client)
    r = client.get(f"/tools/software/{slug}/edit")
    assert r.status_code == 200
    assert "No sources recorded for this draft" in r.text


def test_description_shown_under_review_to_public_when_unverified(app_module):
    """Superseded again by Brian's radical-transparency review standard
    (Gate-Extraction Phase 0/PR A, see CLAUDE.md): Description's
    hide-from-visitors gate (added in the earlier Description/Community
    profile publish-gate follow-up this docstring used to describe) is
    itself now superseded — an unverified draft always renders for every
    viewer, labeled "under review" for a visitor instead of hidden. See
    tests/test_review_state_publish_gates.py for the full coverage."""
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "An unverified AI-drafted description.", "https://runway.com", [],
                           approved=1, summary="Summary.",
                           description_needs_verification=1, description_ai_confident=0)
    slug = lib.get_tool(tool_id)["slug"]
    lib.close()

    client = _client(app_module)   # no login — a signed-out public visitor
    r = client.get(f"/tools/software/{slug}")
    assert r.status_code == 200
    assert "An unverified AI-drafted description." in r.text
    assert "under review" in r.text
    assert "unverified, visible to visitors" not in r.text
