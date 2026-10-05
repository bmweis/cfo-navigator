"""Item A3 (edit-page fixes): "Mark verified" for an AI-generated field
(Description/Short summary, Competitive differentiation) used to only
appear after a round trip through Save — generateDescription/
generateDifferentiation are stateless AJAX calls that never touch the
database, so the server-rendered badge/button (computed from tools.
description_needs_verification / competitive_differentiation_needs_
verification) couldn't reflect a fresh, unsaved draft.

Fixed with a "Save and mark verified" action (client-side, injected right
after a successful Generate call — see showSaveAndMarkVerified in the
edit-form JS) that submits the ordinary edit form with the field name
added to a new hidden confirm_verified_fields input. The server-side half
lives in admin_tools_edit_submit: a field named in confirm_verified_fields
gets its needs_verification flag forced to 0 in the SAME request that
saves its (freshly drafted) text, and a real narrative_review_log row is
written — so the save and the verification happen together, against
whatever text is actually in the textarea this submit, never a stale
previously-saved value.

Agent taxonomy already shows "Mark verified" immediately (its Generate
click is a real synchronous form submit that persists directly to the
database before redirecting back to the edit page — see
_run_tool_research/admin_tools_research_refresh) and needed no fix; kept
here only as a control proving that claim.
"""
import os
import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

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


def _login(client, username="admin", password="adminpass"):
    r = client.post("/login", data={"username": username, "password": password}, follow_redirects=False)
    assert r.status_code in (302, 303)


# -- Description / Short summary --------------------------------------------

def test_confirm_verified_fields_clears_description_needs_verification_in_same_save(env):
    lib = Library(os.environ["LINKLIB_DB"])
    lib.create_user("brian", "pw", role="admin", name="Brian")
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    slug = lib.get_tool(tool_id)["slug"]
    lib.close()

    client = _client(env)
    _login(client, "brian", "pw")
    r = client.post(f"/tools/software/{slug}/edit", data={
        "name": "Runway", "url": "https://runway.com", "description": "AI drafted text",
        "summary": "AI drafted summary", "ai_drafted_fields": "description,summary",
        "confirm_verified_fields": "description",
    }, follow_redirects=False)
    assert r.status_code == 303

    lib = Library(os.environ["LINKLIB_DB"])
    tool = lib.get_tool(tool_id)
    # Without confirm_verified_fields this would be 1 (see
    # test_edit_submit_sets_description_needs_verification_when_ai_drafted
    # in test_narrative_review_pr2.py) — confirming it together clears it
    # in this same request instead.
    assert tool["description_needs_verification"] == 0
    assert tool["description"] == "AI drafted text"
    review = lib.get_latest_narrative_review("tool", "description", tool_id)
    assert review is not None
    assert review["admin_username"] == "brian"
    # The logged snapshot is the text THIS save persisted, not any prior value.
    assert review["detail"] == "AI drafted text"
    lib.close()


def test_confirm_verified_fields_never_verifies_stale_previously_saved_text(env):
    """The core risk this feature exists to avoid: confirming a field must
    never mark a stored value verified while claiming to describe a
    different, currently-drafted value. Simulate the realistic sequence —
    an already-saved, previously-verified description, then a save that
    both drafts new text (ai_drafted_fields) and confirms it in one
    request — and confirm the logged detail and the stored text agree."""
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.update_tool(tool_id, "Runway", "Old verified text", "https://runway.com", ["FP&A"], summary="s")
    slug = lib.get_tool(tool_id)["slug"]
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post(f"/tools/software/{slug}/edit", data={
        "name": "Runway", "url": "https://runway.com", "description": "Brand new drafted text",
        "summary": "new summary", "ai_drafted_fields": "description,summary",
        "confirm_verified_fields": "description",
    }, follow_redirects=False)
    assert r.status_code == 303

    lib = Library(os.environ["LINKLIB_DB"])
    tool = lib.get_tool(tool_id)
    assert tool["description"] == "Brand new drafted text"
    assert tool["description_needs_verification"] == 0
    review = lib.get_latest_narrative_review("tool", "description", tool_id)
    assert review["detail"] == "Brand new drafted text"
    assert review["detail"] != "Old verified text"
    lib.close()


def test_confirm_verified_fields_absent_behaves_exactly_as_before(env):
    """Regression guard: a plain save (no confirm_verified_fields at all,
    the shape every existing test/caller already sends) must produce the
    identical needs_verification value the pre-A3 logic always computed."""
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    slug = lib.get_tool(tool_id)["slug"]
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post(f"/tools/software/{slug}/edit", data={
        "name": "Runway", "url": "https://runway.com", "description": "AI drafted text",
        "summary": "AI drafted summary", "ai_drafted_fields": "description,summary",
    }, follow_redirects=False)
    assert r.status_code == 303

    lib = Library(os.environ["LINKLIB_DB"])
    tool = lib.get_tool(tool_id)
    assert tool["description_needs_verification"] == 1
    assert lib.get_latest_narrative_review("tool", "description", tool_id) is None
    lib.close()


def test_confirm_verified_fields_respects_the_two_tier_length_guard(env):
    """Mark verified can't bypass the description length cap — the same
    lib.update_tool call still runs _check_text_field_length regardless of
    what confirm_verified_fields says."""
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    slug = lib.get_tool(tool_id)["slug"]
    max_len = Library.TOOL_DESCRIPTION_MAX
    lib.close()

    client = _client(env)
    _login(client)
    over_limit = "x" * (max_len + 1)
    r = client.post(f"/tools/software/{slug}/edit", data={
        "name": "Runway", "url": "https://runway.com", "description": over_limit,
        "summary": "s", "ai_drafted_fields": "description",
        "confirm_verified_fields": "description",
    })
    assert r.status_code == 400

    lib = Library(os.environ["LINKLIB_DB"])
    # Nothing was written — the over-limit save was refused whole, so the
    # tool's original description (set at creation) is untouched.
    assert lib.get_tool(tool_id)["description"] == "FP&A"
    lib.close()


# -- Competitive differentiation ---------------------------------------------

def test_confirm_verified_fields_clears_differentiation_needs_verification(env):
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.update_tool(tool_id, "Runway", "d", "https://runway.com", ["FP&A"], summary="s")
    slug = lib.get_tool(tool_id)["slug"]
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post(f"/tools/software/{slug}/edit", data={
        "name": "Runway", "url": "https://runway.com", "description": "d", "summary": "s",
        "competitive_differentiation": "AI drafted bottom line",
        "ai_drafted_fields": "competitive_differentiation",
        "confirm_verified_fields": "competitive_differentiation",
    }, follow_redirects=False)
    assert r.status_code == 303

    lib = Library(os.environ["LINKLIB_DB"])
    tool = lib.get_tool(tool_id)
    assert tool["competitive_differentiation_needs_verification"] == 0
    assert tool["competitive_differentiation"] == "AI drafted bottom line"
    review = lib.get_latest_narrative_review("tool", "differentiation", tool_id)
    assert review is not None
    assert review["detail"] == "AI drafted bottom line"
    lib.close()


def test_confirm_verified_fields_can_confirm_both_fields_in_one_save(env):
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    slug = lib.get_tool(tool_id)["slug"]
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post(f"/tools/software/{slug}/edit", data={
        "name": "Runway", "url": "https://runway.com", "description": "d",
        "summary": "s", "competitive_differentiation": "diff",
        "ai_drafted_fields": "description,summary,competitive_differentiation",
        "confirm_verified_fields": "description,competitive_differentiation",
    }, follow_redirects=False)
    assert r.status_code == 303

    lib = Library(os.environ["LINKLIB_DB"])
    tool = lib.get_tool(tool_id)
    assert tool["description_needs_verification"] == 0
    assert tool["competitive_differentiation_needs_verification"] == 0
    lib.close()


# -- Rendering: the badge/action host spans and the hidden input exist -------

def test_edit_page_carries_confirm_verified_fields_hidden_input(env):
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    slug = lib.get_tool(tool_id)["slug"]
    lib.close()

    client = _client(env)
    _login(client)
    r = client.get(f"/tools/software/{slug}/edit")
    assert r.status_code == 200
    body = r.text
    assert 'id="confirm-verified-fields" name="confirm_verified_fields"' in body
    assert 'id="description-verify-badge"' in body
    assert 'id="description-verify-action"' in body
    assert 'id="differentiation-verify-badge"' in body
    assert 'id="differentiation-verify-action"' in body
    assert "function showSaveAndMarkVerified" in body
    assert "function saveAndMarkVerified" in body
    assert "showSaveAndMarkVerified('description', 'description-verify-badge', 'description-verify-action')" in body
    assert ("showSaveAndMarkVerified('competitive_differentiation', "
            "'differentiation-verify-badge', 'differentiation-verify-action')") in body


# -- Agent taxonomy: control proving it already worked (no fix needed) ------

def test_agent_taxonomy_generate_summary_shows_mark_verified_with_no_extra_save(env, monkeypatch):
    """Control: unlike Description/Differentiation, Agent taxonomy's
    "Generate summary" is a real synchronous form submit
    (research-refresh-form -> /research/refresh -> _run_tool_research)
    that persists agent_taxonomy_needs_verification directly to the
    database before redirecting back to the edit page — so the badge and
    "Mark verified" button already appear on that single redirect, with no
    separate Save step. This test exists to document and pin that claim,
    not because anything needed fixing here."""
    from linklib import enrich as enrich_mod

    class _FakeResult:
        agent_taxonomy_note = "Runs autonomously."
        agent_taxonomy_needs_verification = True
        confident = False
        low_confidence = False
        citations = [{"n": 1, "title": "T", "url": "https://x.example", "type": "tool_page"}]
        model = "test-model"
        input_tokens = 10
        output_tokens = 10
        cost_usd = 0.0
        exa_cost_usd = 0.0

    monkeypatch.setattr(enrich_mod, "generate_tool_agent_taxonomy", lambda *a, **k: _FakeResult())

    lib = Library(os.environ["LINKLIB_DB"])
    lib.seed_voice_prompts()
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    slug = lib.get_tool(tool_id)["slug"]
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post(f"/admin/tools/software/{tool_id}/research/refresh", follow_redirects=False)
    assert r.status_code == 303
    assert "research_refreshed=1" in r.headers["location"]

    lib = Library(os.environ["LINKLIB_DB"])
    assert lib.get_tool(tool_id)["agent_taxonomy_needs_verification"] == 1
    lib.close()

    r2 = client.get(f"/tools/software/{slug}/edit?research_refreshed=1")
    assert r2.status_code == 200
    assert f'action="/admin/tools/software/{tool_id}/agent-taxonomy/verify"' in r2.text
    assert "Mark verified" in r2.text
