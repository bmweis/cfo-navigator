"""Automated Software-vendor research pipeline (search overhaul automation
follow-up): _run_tool_research (shared drafting logic — real feature rows +
an agent-taxonomy draft in one Claude call), the two auto-trigger points
(admin add-form, public /tools/submit form — both via BackgroundTasks so a
slow/failed research call never blocks the tool from going live), the
on-demand "Generate AI Data" admin route, and the "Mark verified"
one-click action for the agent-taxonomy note.
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


def _mock_generate_tool_features(monkeypatch, *, taxonomy="Uses an agent called Aura.",
                                  taxonomy_confident=True, features=None, result=None):
    calls = []

    def _fake(name, url, description="", model=""):
        calls.append((name, url, description))
        if result is not None:
            return result
        return enrich.ToolFeaturesResult(
            features=features or [enrich.ToolFeatureDraft(
                feature_name="Scenario modeling", standalone_available=True,
                bundled_only=False, notes="", source_url=url, needs_verification=False,
            )],
            agent_taxonomy_note=taxonomy,
            agent_taxonomy_needs_verification=not taxonomy_confident,
            low_confidence=False, model="claude-opus-4-8",
            input_tokens=500, output_tokens=300, cost_usd=0.02,
        )

    import linklib.enrich as enrich_mod
    monkeypatch.setattr(enrich_mod, "generate_tool_features", _fake)
    return calls


# -- _run_tool_research (direct) ------------------------------------------------

def test_run_tool_research_writes_features_and_taxonomy_draft(env, monkeypatch):
    calls = _mock_generate_tool_features(monkeypatch)
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
    features = lib.list_tool_features(tool_id)
    assert len(features) == 1
    assert features[0]["feature_name"] == "Scenario modeling"
    assert features[0]["source"] == "llm_enrichment"
    lib.close()


def test_run_tool_research_dedupes_existing_features(env, monkeypatch):
    _mock_generate_tool_features(monkeypatch, features=[
        enrich.ToolFeatureDraft(feature_name="Scenario modeling", standalone_available=True),
    ])
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.add_tool_feature(tool_id, "Scenario modeling", standalone_available=1, source="manual")
    lib.close()

    ok = env._run_tool_research(tool_id)
    assert ok is True

    lib = Library(os.environ["LINKLIB_DB"])
    assert len(lib.list_tool_features(tool_id)) == 1   # no duplicate row written
    lib.close()


def test_run_tool_research_returns_false_when_generate_fails(env, monkeypatch):
    import linklib.enrich as enrich_mod
    monkeypatch.setattr(enrich_mod, "generate_tool_features", lambda *a, **k: None)
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.close()

    assert env._run_tool_research(tool_id) is False


def test_run_tool_research_returns_false_for_missing_tool(env, monkeypatch):
    import linklib.enrich as enrich_mod
    monkeypatch.setattr(enrich_mod, "generate_tool_features",
                         lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not be called")))
    assert env._run_tool_research(999999) is False


# -- auto-trigger on tool creation ----------------------------------------------

def test_admin_add_tool_triggers_background_research(env, monkeypatch):
    calls = _mock_generate_tool_features(monkeypatch)
    client = _client(env)
    _login(client)
    r = client.post("/admin/tools/new", data={
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
    calls = _mock_generate_tool_features(monkeypatch)
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
    calls = _mock_generate_tool_features(monkeypatch)
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    tool_slug = lib.get_tool(tool_id)["slug"]
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post(f"/admin/tools/{tool_id}/research/refresh", follow_redirects=False)
    assert r.status_code == 303
    assert "research_refreshed=1" in r.headers["location"]
    assert calls

    r = client.get(f"/tools/software/{tool_slug}/edit?research_refreshed=1")
    assert "AI research refreshed" in r.text


def test_research_refresh_failure_banner(env, monkeypatch):
    import linklib.enrich as enrich_mod
    monkeypatch.setattr(enrich_mod, "generate_tool_features", lambda *a, **k: None)
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    tool_slug = lib.get_tool(tool_id)["slug"]
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post(f"/admin/tools/{tool_id}/research/refresh", follow_redirects=False)
    assert r.status_code == 303
    assert "research_refreshed=0" in r.headers["location"]

    r = client.get(f"/tools/software/{tool_slug}/edit?research_refreshed=0")
    assert "Couldn" in r.text


def test_research_refresh_requires_auth(env):
    r = _client(env).post("/admin/tools/1/research/refresh")
    assert r.status_code == 401


def test_research_refresh_404_for_missing_tool(env):
    client = _client(env)
    _login(client)
    r = client.post("/admin/tools/999999/research/refresh")
    assert r.status_code == 404


# -- agent-taxonomy verify route --------------------------------------------------

def test_agent_taxonomy_verify_clears_flag(env):
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.set_tool_agent_taxonomy_draft(tool_id, "Drafted note.", needs_verification=1)
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post(f"/admin/tools/{tool_id}/agent-taxonomy/verify", follow_redirects=False)
    assert r.status_code == 303

    lib = Library(os.environ["LINKLIB_DB"])
    tool = lib.get_tool(tool_id)
    assert tool["agent_taxonomy_note"] == "Drafted note."   # text untouched
    assert tool["agent_taxonomy_needs_verification"] == 0
    lib.close()


def test_agent_taxonomy_verify_requires_auth(env):
    r = _client(env).post("/admin/tools/1/agent-taxonomy/verify")
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
    assert "Generate AI Data" in r.text


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
