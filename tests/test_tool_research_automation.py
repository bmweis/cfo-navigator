"""Automated Software-vendor research pipeline (search overhaul automation
follow-up; narrowed to agent-taxonomy-only in the Feature Taxonomy Phase 1b
PR 2 legacy retirement, which dropped the feature-drafting half of
_run_tool_research along with the tool_features table): _run_tool_research
(shared drafting logic — an agent-taxonomy draft via one Claude call), the
two auto-trigger points (admin add-form, public /tools/submit form — both
via BackgroundTasks so a slow/failed research call never blocks the tool
from going live), the on-demand "Generate summary" admin route, and the
"Mark verified" one-click action for the agent-taxonomy note.
"""
import os
import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import enrich
from linklib.db import Library


@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    # 2026-08 visibility follow-up: voice_core must be seeded or every
    # generate_* call in this file's paths (_run_tool_research, the
    # generate-description route) refuses via require_voice_setting.
    _seed_lib = Library(db)
    _seed_lib.seed_voice_prompts()
    _seed_lib.close()
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    yield appmod
    if os.path.exists(db):
        os.remove(db)


def _client(appmod):
    from fastapi.testclient import TestClient
    return TestClient(appmod.app, raise_server_exceptions=True)


def _login(client):
    r = client.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    assert r.status_code in (302, 303)


def _mock_generate_tool_agent_taxonomy(monkeypatch, *, taxonomy="Uses an agent called Aura.",
                                        taxonomy_confident=True, result=None):
    calls = []

    def _fake(name, url, description="", model="", voice_core="", exa_enabled=True):
        calls.append((name, url, description))
        if result is not None:
            return result
        return enrich.AgentTaxonomyResult(
            agent_taxonomy_note=taxonomy,
            agent_taxonomy_needs_verification=not taxonomy_confident,
            low_confidence=False, model="claude-opus-4-8",
            input_tokens=500, output_tokens=300, cost_usd=0.02,
        )

    import linklib.enrich as enrich_mod
    monkeypatch.setattr(enrich_mod, "generate_tool_agent_taxonomy", _fake)
    return calls


# -- _run_tool_research (direct) ------------------------------------------------

def test_run_tool_research_writes_taxonomy_draft(env, monkeypatch):
    calls = _mock_generate_tool_agent_taxonomy(monkeypatch)
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.close()

    ok, reason, url = env._run_tool_research(tool_id)
    assert ok is True
    assert calls == [("Runway", "https://runway.com", "FP&A")]

    lib = Library(os.environ["LINKLIB_DB"])
    tool = lib.get_tool(tool_id)
    assert tool["agent_taxonomy_note"] == "Uses an agent called Aura."
    assert tool["agent_taxonomy_needs_verification"] == 0
    lib.close()


def test_run_tool_research_returns_false_when_generate_fails(env, monkeypatch):
    import linklib.enrich as enrich_mod
    monkeypatch.setattr(enrich_mod, "generate_tool_agent_taxonomy", lambda *a, **k: None)
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.close()

    assert env._run_tool_research(tool_id)[0] is False


def test_run_tool_research_returns_false_for_missing_tool(env, monkeypatch):
    import linklib.enrich as enrich_mod
    monkeypatch.setattr(enrich_mod, "generate_tool_agent_taxonomy",
                         lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not be called")))
    assert env._run_tool_research(999999)[0] is False


# -- auto-trigger on tool creation ----------------------------------------------

def test_admin_add_tool_triggers_background_research(env, monkeypatch):
    calls = _mock_generate_tool_agent_taxonomy(monkeypatch)
    client = _client(env)
    _login(client)
    r = client.post("/admin/tools/software/new", data={
        "name": "Runway", "url": "https://runway.com", "description": "FP&A platform",
        "summary": "FP&A platform for scenario modeling.",
    }, follow_redirects=False)
    assert r.status_code == 303

    assert calls and calls[0][0] == "Runway"
    lib = Library(os.environ["LINKLIB_DB"])
    tool = lib.list_tools(approved_only=True)[0]
    assert tool["agent_taxonomy_note"]
    lib.close()


def test_tools_submit_triggers_background_research(env, monkeypatch):
    calls = _mock_generate_tool_agent_taxonomy(monkeypatch)
    client = _client(env)
    _login(client)
    r = client.post("/tools/submit", data={
        "name": "Runway", "url": "https://runway.com", "description": "FP&A platform",
        "submitted_by": "brian@example.com",
    }, follow_redirects=False)
    assert r.status_code == 303

    assert calls and calls[0][0] == "Runway"
    lib = Library(os.environ["LINKLIB_DB"])
    tools = lib.list_tools(approved_only=False)
    assert len(tools) == 1
    assert tools[0]["agent_taxonomy_note"]
    lib.close()


# -- on-demand refresh route -----------------------------------------------------

def test_research_refresh_success(env, monkeypatch):
    calls = _mock_generate_tool_agent_taxonomy(monkeypatch)
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    tool_slug = lib.get_tool(tool_id)["slug"]
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post(f"/admin/tools/software/{tool_id}/research/refresh", follow_redirects=False)
    assert r.status_code == 303
    assert "research_refreshed=1" in r.headers["location"]
    assert calls

    r = client.get(f"/tools/software/{tool_slug}/edit?research_refreshed=1")
    assert "Drafted. Review the" in r.text   # shrunk from "AI research refreshed" banner (2026-08 design fix)


def test_research_refresh_failure_banner(env, monkeypatch):
    import linklib.enrich as enrich_mod
    monkeypatch.setattr(enrich_mod, "generate_tool_agent_taxonomy", lambda *a, **k: None)
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    tool_slug = lib.get_tool(tool_id)["slug"]
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post(f"/admin/tools/software/{tool_id}/research/refresh", follow_redirects=False)
    assert r.status_code == 303
    assert "research_refreshed=0" in r.headers["location"]

    r = client.get(f"/tools/software/{tool_slug}/edit?research_refreshed=0")
    assert "Couldn" in r.text


def test_research_refresh_requires_auth(env):
    r = _client(env).post("/admin/tools/software/1/research/refresh")
    assert r.status_code == 401


def test_research_refresh_404_for_missing_tool(env):
    client = _client(env)
    _login(client)
    r = client.post("/admin/tools/software/999999/research/refresh")
    assert r.status_code == 404


# -- agent-taxonomy verify route --------------------------------------------------

def test_agent_taxonomy_verify_clears_flag(env):
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.set_tool_agent_taxonomy_draft(tool_id, "Drafted note.", needs_verification=1)
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post(f"/admin/tools/software/{tool_id}/agent-taxonomy/verify", follow_redirects=False)
    assert r.status_code == 303

    lib = Library(os.environ["LINKLIB_DB"])
    tool = lib.get_tool(tool_id)
    assert tool["agent_taxonomy_note"] == "Drafted note."   # text untouched
    assert tool["agent_taxonomy_needs_verification"] == 0
    lib.close()


def test_agent_taxonomy_verify_requires_auth(env):
    r = _client(env).post("/admin/tools/software/1/agent-taxonomy/verify")
    assert r.status_code == 401


# -- edit page rendering ----------------------------------------------------------

def test_edit_page_shows_needs_verification_badge_and_refresh_button(env):
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    tool_slug = lib.get_tool(tool_id)["slug"]
    lib.set_tool_agent_taxonomy_draft(tool_id, "Drafted note.", needs_verification=1)
    lib.close()

    client = _client(env)
    _login(client)
    r = client.get(f"/tools/software/{tool_slug}/edit")
    assert "Needs verification" in r.text
    assert "Mark verified" in r.text
    assert "Generate summary" in r.text


def test_edit_page_hides_badge_once_verified(env):
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    tool_slug = lib.get_tool(tool_id)["slug"]
    lib.set_tool_agent_taxonomy_draft(tool_id, "Drafted note.", needs_verification=0)
    lib.close()

    client = _client(env)
    _login(client)
    r = client.get(f"/tools/software/{tool_slug}/edit")
    assert "Agent taxonomy" in r.text
    assert 'agent-taxonomy/verify' not in r.text   # no verify action rendered once confirmed


# -- Phase G: banner-conditional bug fix -------------------------------------
# The banner used to say "before marking them verified" on every successful
# refresh regardless of whether anything actually ended up flagged
# needs_verification — so a confident LLM run left admins staring at a
# promise with no button anywhere on the page to fulfill it. The clause now
# tracks the real agent_taxonomy_needs_verification flag directly (the only
# flag left in play since the legacy per-feature needs_verification flag was
# retired along with tool_features).

def test_research_refresh_banner_drops_verify_clause_when_confident(env, monkeypatch):
    _mock_generate_tool_agent_taxonomy(monkeypatch, taxonomy_confident=True)
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    tool_slug = lib.get_tool(tool_id)["slug"]
    lib.close()

    client = _client(env)
    _login(client)
    client.post(f"/admin/tools/software/{tool_id}/research/refresh", follow_redirects=False)

    r = client.get(f"/tools/software/{tool_slug}/edit?research_refreshed=1")
    assert "Drafted. Review the" in r.text   # shrunk from "AI research refreshed" banner (2026-08 design fix)
    assert "before marking it verified" not in r.text
    # And, matching that: no verify button/badge/hidden-form should render for
    # Agent taxonomy either. Target the actual widget markup
    # (_narrative_verify_widget's own "agent-taxonomy-verify-form" id, shared
    # by its hidden <form> and its button's form= attribute — see
    # webapp/app.py's _narrative_verify_widget call for agent taxonomy), not
    # a bare "Mark verified" substring: that string can also appear in
    # unrelated page prose or JS comments (e.g. the Save-and-mark-verified
    # feature's own client-side helper functions, always present on this
    # page regardless of this field's state) with no discriminating power
    # over whether the real control rendered.
    assert 'agent-taxonomy-verify-form' not in r.text
    assert 'background:#fef3c7' not in r.text   # the needs-verification badge's styling


def test_research_refresh_banner_keeps_verify_clause_when_unconfident(env, monkeypatch):
    _mock_generate_tool_agent_taxonomy(monkeypatch, taxonomy_confident=False)
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    tool_slug = lib.get_tool(tool_id)["slug"]
    lib.close()

    client = _client(env)
    _login(client)
    client.post(f"/admin/tools/software/{tool_id}/research/refresh", follow_redirects=False)

    r = client.get(f"/tools/software/{tool_slug}/edit?research_refreshed=1")
    assert "before marking it verified" in r.text
    # Symmetric with the confident case above: assert the real widget markup
    # rendered (the hidden form/button pair sharing "agent-taxonomy-verify-
    # form"), not just that the phrase "Mark verified" appears somewhere.
    assert 'agent-taxonomy-verify-form' in r.text


# -- Phase G: narrative_review_log audit trail --------------------------------

def test_agent_taxonomy_verify_writes_narrative_review_log(env):
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.set_tool_agent_taxonomy_draft(tool_id, "Drafted note.", needs_verification=1)
    lib.close()

    client = _client(env)
    _login(client)
    client.post(f"/admin/tools/software/{tool_id}/agent-taxonomy/verify", follow_redirects=False)

    lib = Library(os.environ["LINKLIB_DB"])
    review = lib.get_latest_narrative_review("tool", "agent_taxonomy", tool_id)
    assert review is not None
    assert review["entity_type"] == "tool"
    assert review["field_type"] == "agent_taxonomy"
    assert review["item_id"] == tool_id
    assert review["detail"] == "Drafted note."
    log = lib.list_narrative_review_log()
    assert len(log) == 1
    lib.close()


def test_agent_taxonomy_verify_logs_reviewer_username(env):
    lib = Library(os.environ["LINKLIB_DB"])
    lib.create_user("brian", "pw", role="admin", name="Brian")
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.set_tool_agent_taxonomy_draft(tool_id, "Drafted note.", needs_verification=1)
    lib.close()

    client = _client(env)
    client.post("/login", data={"username": "brian", "password": "pw"}, follow_redirects=False)
    client.post(f"/admin/tools/software/{tool_id}/agent-taxonomy/verify", follow_redirects=False)

    lib = Library(os.environ["LINKLIB_DB"])
    review = lib.get_latest_narrative_review("tool", "agent_taxonomy", tool_id)
    assert review["admin_username"] == "brian"
    lib.close()


def test_agent_taxonomy_verify_reverify_appends_not_overwrites(env):
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.set_tool_agent_taxonomy_draft(tool_id, "First draft.", needs_verification=1)
    lib.close()

    client = _client(env)
    _login(client)
    client.post(f"/admin/tools/software/{tool_id}/agent-taxonomy/verify", follow_redirects=False)

    lib = Library(os.environ["LINKLIB_DB"])
    lib.set_tool_agent_taxonomy_draft(tool_id, "Refreshed draft.", needs_verification=1)
    lib.close()

    client.post(f"/admin/tools/software/{tool_id}/agent-taxonomy/verify", follow_redirects=False)

    lib = Library(os.environ["LINKLIB_DB"])
    log = lib.list_narrative_review_log()
    assert len(log) == 2
    latest = lib.get_latest_narrative_review("tool", "agent_taxonomy", tool_id)
    assert latest["detail"] == "Refreshed draft."
    lib.close()


def test_edit_page_shows_verified_by_line_after_verify(env):
    lib = Library(os.environ["LINKLIB_DB"])
    lib.create_user("brian", "pw", role="admin", name="Brian")
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    tool_slug = lib.get_tool(tool_id)["slug"]
    lib.set_tool_agent_taxonomy_draft(tool_id, "Drafted note.", needs_verification=1)
    lib.close()

    client = _client(env)
    client.post("/login", data={"username": "brian", "password": "pw"}, follow_redirects=False)
    client.post(f"/admin/tools/software/{tool_id}/agent-taxonomy/verify", follow_redirects=False)

    r = client.get(f"/tools/software/{tool_slug}/edit")
    assert "Verified by brian on" in r.text


def test_edit_page_no_verified_by_line_when_never_verified(env):
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    tool_slug = lib.get_tool(tool_id)["slug"]
    lib.set_tool_agent_taxonomy_draft(tool_id, "Drafted note.", needs_verification=1)
    lib.close()

    client = _client(env)
    _login(client)
    r = client.get(f"/tools/software/{tool_slug}/edit")
    assert "Verified by" not in r.text


# -- Citations-API grounding fix, Phase 1b: entity_citations storage/render -

def test_run_tool_research_persists_citations(env, monkeypatch):
    """_run_tool_research writes AgentTaxonomyResult.citations to the shared
    entity_citations table via Library.set_entity_citations."""
    citations = [{"n": 1, "title": "Homepage", "url": "https://runway.com", "type": "tool_page"}]
    _mock_generate_tool_agent_taxonomy(monkeypatch, result=enrich.AgentTaxonomyResult(
        agent_taxonomy_note="Uses an agent called Aura.",
        agent_taxonomy_needs_verification=False, confident=True,
        low_confidence=False, citations=citations,
        model="claude-opus-4-8", input_tokens=500, output_tokens=300, cost_usd=0.02,
    ))
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.close()

    assert env._run_tool_research(tool_id)[0] is True

    lib = Library(os.environ["LINKLIB_DB"])
    assert lib.get_entity_citations("tool", tool_id, "agent_taxonomy") == citations
    lib.close()


def test_entity_citations_defaults_empty_when_never_set(env):
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    assert lib.get_entity_citations("tool", tool_id, "agent_taxonomy") == []
    lib.close()


def test_entity_citations_upsert_replaces_not_accumulates(env):
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.set_entity_citations("tool", tool_id, "agent_taxonomy",
                             [{"n": 1, "title": "Old", "url": "https://old.example", "type": "tool_page"}],
                             model="claude-opus-4-8")
    lib.set_entity_citations("tool", tool_id, "agent_taxonomy",
                             [{"n": 1, "title": "New", "url": "https://new.example", "type": "tool_page"}],
                             model="claude-opus-4-8")
    citations = lib.get_entity_citations("tool", tool_id, "agent_taxonomy")
    assert len(citations) == 1
    assert citations[0]["url"] == "https://new.example"
    lib.close()


def test_update_tool_agent_taxonomy_clears_stale_citations(env):
    """A human hand-editing the note (the admin edit-form save path) has no
    citation trace to keep — entity_citations must clear, not keep pointing
    at sources for text a person just overwrote."""
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.set_tool_agent_taxonomy_draft(tool_id, "AI-drafted note.", needs_verification=1)
    lib.set_entity_citations("tool", tool_id, "agent_taxonomy",
                             [{"n": 1, "title": "Homepage", "url": "https://runway.com", "type": "tool_page"}],
                             model="claude-opus-4-8")
    lib.update_tool_agent_taxonomy(tool_id, "Brian's hand-edited note.")
    assert lib.get_entity_citations("tool", tool_id, "agent_taxonomy") == []
    assert lib.get_tool(tool_id)["agent_taxonomy_note"] == "Brian's hand-edited note."
    lib.close()


def test_profile_page_renders_citation_sources_for_verified_note(env):
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    tool_slug = lib.get_tool(tool_id)["slug"]
    lib.set_tool_agent_taxonomy_draft(tool_id, "Uses an agent called Aura.", needs_verification=0)
    lib.set_entity_citations("tool", tool_id, "agent_taxonomy",
                             [{"n": 1, "title": "Homepage", "url": "https://runway.com", "type": "tool_page"}],
                             model="claude-opus-4-8")
    lib.close()

    r = _client(env).get(f"/tools/software/{tool_slug}")
    assert r.status_code == 200
    assert "Sources" in r.text
    assert "[1] Homepage" in r.text
    assert 'href="https://runway.com"' in r.text


def test_profile_page_caps_citations_at_five(env):
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    tool_slug = lib.get_tool(tool_id)["slug"]
    lib.set_tool_agent_taxonomy_draft(tool_id, "Uses several agents.", needs_verification=0)
    citations = [
        {"n": i, "title": f"Page {i}", "url": f"https://runway.com/p{i}", "type": "tool_page"}
        for i in range(1, 8)   # 7 real citations
    ]
    lib.set_entity_citations("tool", tool_id, "agent_taxonomy", citations, model="claude-opus-4-8")
    lib.close()

    r = _client(env).get(f"/tools/software/{tool_slug}")
    assert r.status_code == 200
    for i in range(1, 6):
        assert f"[{i}] Page {i}" in r.text
    for i in range(6, 8):
        assert f"[{i}] Page {i}" not in r.text   # dropped, no "+N more" indicator


def test_profile_page_no_sources_section_when_no_citations(env):
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    tool_slug = lib.get_tool(tool_id)["slug"]
    lib.set_tool_agent_taxonomy_draft(tool_id, "Hand-written, no citations.", needs_verification=0)
    lib.close()

    r = _client(env).get(f"/tools/software/{tool_slug}")
    assert r.status_code == 200
    assert r.text.count(">Sources<") == 0


def test_edit_page_shows_no_citations_recorded_note(env):
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    tool_slug = lib.get_tool(tool_id)["slug"]
    lib.set_tool_agent_taxonomy_draft(tool_id, "Drafted note.", needs_verification=1)
    lib.close()

    client = _client(env)
    _login(client)
    r = client.get(f"/tools/software/{tool_slug}/edit")
    assert "No sources recorded for this draft" in r.text


def test_edit_page_shows_full_uncapped_citation_list_next_to_verify_action(env):
    """Explicit requirement: the admin sees every source on the SAME view as
    the Mark verified button, before deciding to publish — not truncated
    the way the public page's 5-source cap is."""
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    tool_slug = lib.get_tool(tool_id)["slug"]
    lib.set_tool_agent_taxonomy_draft(tool_id, "Drafted note.", needs_verification=1)
    citations = [
        {"n": i, "title": f"Page {i}", "url": f"https://runway.com/p{i}", "type": "tool_page"}
        for i in range(1, 8)   # more than the public 5-source cap
    ]
    lib.set_entity_citations("tool", tool_id, "agent_taxonomy", citations, model="claude-opus-4-8")
    lib.close()

    client = _client(env)
    _login(client)
    r = client.get(f"/tools/software/{tool_slug}/edit")
    for i in range(1, 8):
        assert f"[{i}] Page {i}" in r.text   # all 7, uncapped
    # Both the citation list and the "Mark verified" action live inside the
    # same #gen-host-tool-taxonomy block on this one page.
    taxonomy_block = r.text.split('id="gen-host-tool-taxonomy"')[1].split("</div>\n    </div>")[0]
    assert "Mark verified" in taxonomy_block
    assert "[1] Page 1" in taxonomy_block
