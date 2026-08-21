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

    def _fake(name, url, description="", model=""):
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

    ok = env._run_tool_research(tool_id)
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

    assert env._run_tool_research(tool_id) is False


def test_run_tool_research_returns_false_for_missing_tool(env, monkeypatch):
    import linklib.enrich as enrich_mod
    monkeypatch.setattr(enrich_mod, "generate_tool_agent_taxonomy",
                         lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not be called")))
    assert env._run_tool_research(999999) is False


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
    assert "AI research refreshed" in r.text


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
    assert "AI research refreshed" in r.text
    assert "before marking it verified" not in r.text
    # And, matching that: no verify button/badge should be on the page either.
    assert "Mark verified" not in r.text
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
    assert "Mark verified" in r.text


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
