"""Whole-record tool profile signoff (2026-08).

Mirrors community_profiles.needs_review as closely as sensible for tools'
shape, sitting alongside the three existing per-field
*_needs_verification flags without touching or being derived from them —
see CLAUDE.md's "Tools whole-record profile signoff" bullet.

- tools.needs_review (new column, default 0 on every existing and new row)
- Library.set_tool_needs_review / mark_tool_reviewed / count_tools_needing_review
- Manual checkbox on the tool edit form, independent of any generation event
- POST /admin/tools/software/{tool_id}/mark-reviewed, logging to
  narrative_review_log with field_type='profile'
- Admin-only bookkeeping: no public-facing gating (Brian's explicit call —
  the three per-field flags already do that job for tools).
"""
import os
import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Library


@pytest.fixture
def lib():
    db = tempfile.mktemp(suffix=".db")
    library = Library(db)
    yield library
    library.close()
    if os.path.exists(db):
        os.remove(db)


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


# -- Library layer --------------------------------------------------------------

def test_needs_review_defaults_to_zero_on_creation(lib):
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    assert lib.get_tool(tool_id)["needs_review"] == 0


def test_set_tool_needs_review_toggles_independent_of_per_field_flags(lib):
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    # A per-field flag being 1 must not force the whole-record flag, and
    # vice versa — the two are independent, never derived from each other.
    lib.update_tool(tool_id, "Runway", "d", "https://runway.com", ["FP&A"],
                     summary="s", description_needs_verification=1)
    assert lib.get_tool(tool_id)["needs_review"] == 0

    lib.set_tool_needs_review(tool_id, 1)
    tool = lib.get_tool(tool_id)
    assert tool["needs_review"] == 1
    assert tool["description_needs_verification"] == 1  # untouched

    lib.mark_tool_description_verified(tool_id)
    tool = lib.get_tool(tool_id)
    assert tool["description_needs_verification"] == 0
    assert tool["needs_review"] == 1  # untouched by the per-field verify


def test_mark_tool_reviewed_clears_flag_only(lib):
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.set_tool_needs_review(tool_id, 1)
    lib.update_tool(tool_id, "Runway", "d", "https://runway.com", ["FP&A"],
                     summary="s", description_needs_verification=1)

    lib.mark_tool_reviewed(tool_id)
    tool = lib.get_tool(tool_id)
    assert tool["needs_review"] == 0
    assert tool["description_needs_verification"] == 1  # untouched


def test_mark_tool_reviewed_is_noop_for_missing_tool(lib):
    lib.mark_tool_reviewed(999999)  # must not raise


def test_count_tools_needing_review(lib):
    t1 = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    t2 = lib.add_tool("Abacum", "FP&A", "https://abacum.io", ["FP&A"], approved=1)
    assert lib.count_tools_needing_review() == 0
    lib.set_tool_needs_review(t1, 1)
    lib.set_tool_needs_review(t2, 1)
    assert lib.count_tools_needing_review() == 2
    lib.mark_tool_reviewed(t1)
    assert lib.count_tools_needing_review() == 1


# -- Edit-submit route: manual checkbox, independent of AI drafting -------------

def test_edit_submit_sets_needs_review_from_checkbox(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    slug = lib.get_tool(tool_id)["slug"]
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post(f"/tools/software/{slug}/edit", data={
        "name": "Runway", "url": "https://runway.com", "description": "d",
        "summary": "s", "needs_review": "1",
    }, follow_redirects=False)
    assert r.status_code == 303

    lib = Library(os.environ["LINKLIB_DB"])
    assert lib.get_tool(tool_id)["needs_review"] == 1
    lib.close()


def test_edit_submit_clears_needs_review_when_checkbox_unchecked(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.set_tool_needs_review(tool_id, 1)
    slug = lib.get_tool(tool_id)["slug"]
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post(f"/tools/software/{slug}/edit", data={
        "name": "Runway", "url": "https://runway.com", "description": "d", "summary": "s",
    }, follow_redirects=False)
    assert r.status_code == 303

    lib = Library(os.environ["LINKLIB_DB"])
    assert lib.get_tool(tool_id)["needs_review"] == 0
    lib.close()


def test_edit_submit_does_not_set_needs_review_from_ai_drafted_fields(env):
    """A fresh Generate + Save on Description/Agent taxonomy/Differentiation
    must NOT auto-flip the whole-record flag — unlike Communities'
    needs_review, this one is purely manual, per explicit direction."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    slug = lib.get_tool(tool_id)["slug"]
    lib.close()

    client = _client(env)
    _login(client)
    client.post(f"/tools/software/{slug}/edit", data={
        "name": "Runway", "url": "https://runway.com", "description": "AI drafted text",
        "summary": "AI drafted summary", "ai_drafted_fields": "description,summary",
    }, follow_redirects=False)

    lib = Library(os.environ["LINKLIB_DB"])
    tool = lib.get_tool(tool_id)
    assert tool["description_needs_verification"] == 1  # the per-field flag DOES fire
    assert tool["needs_review"] == 0                     # the whole-record flag does NOT
    lib.close()


# -- Mark-reviewed route ---------------------------------------------------------

def test_mark_reviewed_route_clears_flag_and_logs(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    lib.create_user("brian", "pw", role="admin", name="Brian")
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.update_tool(tool_id, "Runway", "Full text", "https://runway.com", ["FP&A"], summary="s")
    lib.set_tool_needs_review(tool_id, 1)
    lib.close()

    client = _client(env)
    _login(client, "brian", "pw")
    r = client.post(f"/admin/tools/software/{tool_id}/mark-reviewed", follow_redirects=False)
    assert r.status_code == 303

    lib = Library(os.environ["LINKLIB_DB"])
    tool = lib.get_tool(tool_id)
    assert tool["needs_review"] == 0
    review = lib.get_latest_narrative_review("tool", "profile", tool_id)
    assert review is not None
    assert review["admin_username"] == "brian"
    assert review["detail"] == "Full text"
    lib.close()


def test_mark_reviewed_route_requires_auth(env):
    client = _client(env)
    assert client.post("/admin/tools/software/1/mark-reviewed").status_code == 401


def test_mark_reviewed_route_404s_for_missing_tool(env):
    client = _client(env)
    _login(client)
    assert client.post("/admin/tools/software/999999/mark-reviewed").status_code == 404


# -- Edit page rendering ----------------------------------------------------------

def test_edit_page_shows_checkbox_and_mark_reviewed_button_when_needs_review(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    slug = lib.get_tool(tool_id)["slug"]
    lib.set_tool_needs_review(tool_id, 1)
    lib.close()

    client = _client(env)
    _login(client)
    r = client.get(f"/tools/software/{slug}/edit")
    assert 'name="needs_review"' in r.text
    assert " checked" in r.text.split('name="needs_review"')[1][:40]
    assert 'action="/admin/tools/software/{}/mark-reviewed"'.format(tool_id) in r.text
    assert "Mark reviewed" in r.text


def test_edit_page_hides_mark_reviewed_button_when_not_flagged(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    slug = lib.get_tool(tool_id)["slug"]
    lib.close()

    client = _client(env)
    _login(client)
    r = client.get(f"/tools/software/{slug}/edit")
    assert 'action="/admin/tools/software/{}/mark-reviewed"'.format(tool_id) not in r.text


def test_edit_page_shows_reviewed_by_line_after_mark_reviewed(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    lib.create_user("brian", "pw", role="admin", name="Brian")
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    slug = lib.get_tool(tool_id)["slug"]
    lib.close()

    client = _client(env)
    _login(client, "brian", "pw")
    client.post(f"/admin/tools/software/{tool_id}/mark-reviewed", follow_redirects=False)

    r = client.get(f"/tools/software/{slug}/edit")
    assert "Reviewed by brian on" in r.text


# -- No public gating: profile page renders identically regardless of flag ------

def test_public_profile_page_unaffected_by_needs_review_flag(env):
    """Admin-only bookkeeping, per explicit direction — unlike Communities'
    needs_review, this must not hide/change anything on the public profile
    page (the three per-field flags already do that job for tools)."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "Planning software for finance teams.",
                            "https://runway.com", ["FP&A"], approved=1)
    lib.update_tool(tool_id, "Runway", "Planning software for finance teams.",
                     "https://runway.com", ["FP&A"], summary="Planning software.")
    slug = lib.get_tool(tool_id)["slug"]
    lib.close()

    client = _client(env)
    r_before = client.get(f"/tools/software/{slug}")
    assert r_before.status_code == 200
    assert "Planning software for finance teams." in r_before.text

    lib = Library(os.environ["LINKLIB_DB"])
    lib.set_tool_needs_review(tool_id, 1)
    lib.close()

    r_after = client.get(f"/tools/software/{slug}")
    assert r_after.status_code == 200
    assert r_after.text == r_before.text
