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
    from linklib import extract
    monkeypatch.setattr(extract, "fetch_page", lambda url, **kw: types.SimpleNamespace(content=content))


def _mock_anthropic_citing(monkeypatch, payload_json, cited_document_indexes,
                           input_tokens=200, output_tokens=150):
    """Same shape as test_agent_taxonomy_enrichment's helper — a single text
    block carrying a real (SDK-shaped) `citations` list referencing
    document_index positions, plus the captured messages.create kwargs so a
    test can assert on the document block actually sent."""
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


DESC_JSON = """{
    "description": "Runway is a financial planning platform for finance teams at growth-stage companies.",
    "summary": "Runway is an FP&A platform for growth-stage finance teams.",
    "confident": true
}"""


# -- generate_tool_description: real document-block grounding ----------------

def test_generate_tool_description_sends_real_document_block(monkeypatch):
    _mock_fetch_page(monkeypatch, "Runway is an FP&A platform for finance teams.")
    captured = _mock_anthropic_citing(monkeypatch, DESC_JSON, cited_document_indexes=[])

    enrich.generate_tool_description("Runway", "https://runway.com")

    content = captured["messages"][0]["content"]
    assert isinstance(content, list)   # doc block + a trailing text block, not a bare string
    doc_blocks = [b for b in content if b.get("type") == "document"]
    assert len(doc_blocks) == 1
    assert doc_blocks[0]["citations"] == {"enabled": True}
    assert doc_blocks[0]["source"]["data"] == "Runway is an FP&A platform for finance teams."
    assert content[-1]["type"] == "text"   # drafting instructions ride last


def test_generate_tool_description_returns_verified_citations_tagged_tool_page(monkeypatch):
    _mock_fetch_page(monkeypatch, "Runway is an FP&A platform for finance teams.")
    _mock_anthropic_citing(monkeypatch, DESC_JSON, cited_document_indexes=[0])

    draft = enrich.generate_tool_description("Runway", "https://runway.com")
    assert draft is not None
    assert len(draft.citations) == 1
    assert draft.citations[0]["url"] == "https://runway.com"
    assert draft.citations[0]["type"] == "tool_page"
    # The stored fields are untouched plain text — no [n] markers spliced
    # into the JSON output (that would have corrupted the parse entirely).
    assert "[1]" not in draft.description
    assert "financial planning platform" in draft.description


def test_generate_tool_description_json_still_parses_when_citations_present(monkeypatch):
    """The real risk this fix introduces: citations enabled on a
    strict-JSON response. Confirms json.loads still succeeds and both
    fields (plus 'confident') still come through."""
    _mock_fetch_page(monkeypatch, "Homepage content.")
    _mock_anthropic_citing(monkeypatch, DESC_JSON, cited_document_indexes=[0])

    draft = enrich.generate_tool_description("Runway", "https://runway.com")
    assert draft is not None
    assert draft.confident is True
    assert draft.summary == "Runway is an FP&A platform for growth-stage finance teams."


def test_generate_tool_description_no_citations_when_low_confidence(monkeypatch):
    """No page content fetched at all → no document sent → nothing to
    cite, regardless of what the (mocked) response claims."""
    _mock_fetch_page(monkeypatch, "")
    _mock_anthropic_citing(monkeypatch, DESC_JSON, cited_document_indexes=[0])

    draft = enrich.generate_tool_description("Obscure Co", "https://obscure.example")
    assert draft is not None
    assert draft.low_confidence is True
    assert draft.citations == []


# -- _validate_citations_payload (server-side guard on browser input) --------

@pytest.fixture
def app_module(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
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
    _mock_fetch_page(monkeypatch, "Runway is an FP&A platform for finance teams.")
    _mock_anthropic_citing(monkeypatch, DESC_JSON, cited_document_indexes=[0])

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
    assert "No citations recorded for this draft" in r.text


def test_description_hidden_from_public_when_unverified(app_module):
    """Superseded by the Description/Community profile publish-gate
    follow-up (see CLAUDE.md): Description now gets the same Abacum-fix
    publish gate Agent taxonomy already had — an unverified draft is
    hidden from a public visitor. Was
    test_description_renders_publicly_regardless_of_needs_verification,
    which pinned the old no-gate behavior as a deliberate, flagged
    follow-up; rewritten now that the follow-up has shipped, same as the
    2026-08 confidence-indicator tests were renamed/rewritten when their
    own pinned behavior changed. See tests/test_review_state_publish_gates.py
    for the full gate coverage."""
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "An unverified AI-drafted description.", "https://runway.com", [],
                           approved=1, summary="Summary.",
                           description_needs_verification=1, description_ai_confident=0)
    slug = lib.get_tool(tool_id)["slug"]
    lib.close()

    client = _client(app_module)   # no login — a signed-out public visitor
    r = client.get(f"/tools/software/{slug}")
    assert r.status_code == 200
    assert "An unverified AI-drafted description." not in r.text
