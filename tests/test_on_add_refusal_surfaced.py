"""A refused on-add research run is visible to the admin (issue #696).

The background job queued by "Add to directory" has no UI. After #693 it
refuses an uncited draft twice in a row, and that refusal (like a page that
can't be read, or any other failure) only reached stdout. It is now stored
on the tool (tools.research_refusal), shown as visible text on the edit
page, and counted by the Software badge until a draft or a hand-written
note replaces it."""
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
    lib = Library(db)
    lib.seed_voice_prompts()
    lib.close()
    import importlib
    import webapp.app as appmod
    importlib.reload(appmod)
    yield appmod
    for ext in ("", "-shm", "-wal"):
        if os.path.exists(db + ext):
            os.remove(db + ext)


def _client(appmod):
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app, raise_server_exceptions=True)
    assert c.post("/login", data={"username": "admin", "password": "adminpass"},
                  follow_redirects=False).status_code in (302, 303)
    return c


def _raise(exc):
    def f(*a, **k):
        raise exc
    return f


def _add(client):
    r = client.post("/admin/tools/software/new", data={
        "name": "Aura", "url": "https://aura.example", "description": "FP&A platform",
        "summary": "FP&A platform for scenario modeling."}, follow_redirects=False)
    assert r.status_code == 303
    lib = Library(os.environ["LINKLIB_DB"])
    tool = lib.list_tools(approved_only=True)[0]
    lib.close()
    return tool


def _tool(tool_id):
    lib = Library(os.environ["LINKLIB_DB"])
    t = lib.get_tool(tool_id)
    lib.close()
    return t


def test_uncited_refusal_on_add_is_stored_and_shown(env, monkeypatch):
    monkeypatch.setattr(enrich, "generate_tool_agent_taxonomy",
                        _raise(enrich.UncitedDraft("agent_taxonomy", "no_citations", "https://aura.example")))
    c = _client(env)
    tool = _add(c)
    assert "no citations" in tool["research_refusal"]
    page = c.get(f"/tools/software/{tool['slug']}/edit").text
    assert "no draft was saved" in page and "no citations" in page


def test_grounding_and_unexpected_failures_are_stored_too(env, monkeypatch):
    monkeypatch.setattr(enrich, "generate_tool_agent_taxonomy",
                        _raise(enrich.GroundingUnavailable("too-thin", "https://aura.example")))
    tool = _add(_client(env))
    assert "too-thin" in tool["research_refusal"]
    monkeypatch.setattr(enrich, "generate_tool_agent_taxonomy", _raise(RuntimeError("boom")))
    env._run_tool_research(tool["id"], True)
    assert "didn't complete" in _tool(tool["id"])["research_refusal"]


def test_counted_by_badge_even_after_mark_reviewed(env, monkeypatch):
    monkeypatch.setattr(enrich, "generate_tool_agent_taxonomy",
                        _raise(enrich.UncitedDraft("agent_taxonomy", "no_citations", "https://aura.example")))
    tool = _add(_client(env))
    lib = Library(os.environ["LINKLIB_DB"])
    lib.mark_tool_reviewed(tool["id"])
    assert lib.count_tools_needing_attention() == 1
    # dedup-safe: also needs_review again, still one tool
    lib.set_tool_needs_review(tool["id"], 1)
    assert lib.count_tools_needing_attention() == 1
    lib.close()


def test_page_view_does_not_clear_and_draft_or_hand_note_does(env, monkeypatch):
    monkeypatch.setattr(enrich, "generate_tool_agent_taxonomy",
                        _raise(enrich.UncitedDraft("agent_taxonomy", "no_citations", "https://aura.example")))
    c = _client(env)
    tool = _add(c)
    c.get(f"/tools/software/{tool['slug']}/edit")
    assert _tool(tool["id"])["research_refusal"]
    lib = Library(os.environ["LINKLIB_DB"])
    lib.update_tool_agent_taxonomy(tool["id"], "   ")
    assert lib.get_tool(tool["id"])["research_refusal"]          # empty note: kept
    lib.update_tool_agent_taxonomy(tool["id"], "Hand-written note.")
    assert lib.get_tool(tool["id"])["research_refusal"] == ""
    lib.set_tool_research_refusal(tool["id"], "again")
    lib.set_tool_agent_taxonomy_draft(tool["id"], "A draft [1].")
    assert lib.get_tool(tool["id"])["research_refusal"] == ""
    lib.close()


def test_successful_on_add_leaves_no_refusal_and_refresh_button_never_sets_one(env, monkeypatch):
    monkeypatch.setattr(enrich, "generate_tool_agent_taxonomy",
                        _raise(enrich.UncitedDraft("agent_taxonomy", "no_citations", "https://aura.example")))
    tool = _add(_client(env))
    lib = Library(os.environ["LINKLIB_DB"])
    lib.set_tool_research_refusal(tool["id"], "")
    lib.close()
    env._run_tool_research(tool["id"])           # the Refresh button path
    assert _tool(tool["id"])["research_refusal"] == ""
