"""Phase G PR 2: Description, Differentiation (tools) and the Community
profile draft join Agent taxonomy's needs_verification + narrative_review_log
"Mark verified" pattern established in PR 1.

- tools.description_needs_verification / competitive_differentiation_needs_verification
  (new columns), set at save time from whether this submit's ai_drafted_fields
  named the field — a fresh AI draft is unconfirmed until an explicit "Mark
  verified" click; any other save (hand-edited or untouched) clears it, same
  "editing/saving is itself a confirmation" convention update_tool_agent_taxonomy
  already used.
- Community profile draft reuses the pre-existing community_profiles.needs_review
  flag (now also auto-set on a fresh Generate + save) rather than a new column,
  per the Phase G PR 2 direction — see CLAUDE.md's Phase G note.
- field_reviews stops growing for these fields (frozen historical record) —
  still grows for everything else (Community "Auto-fill from URL" listing
  fields, competitor-match suggestions).
"""
import os
import pathlib
import sys
import tempfile

import pytest
from tests.community_edit_helpers import post_profile, get_profile, edit_url

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


# -- Library layer: update_tool / update_tool_differentiation ------------------

def test_update_tool_description_needs_verification_defaults_unchanged(lib):
    """None (the default) leaves the column alone — bulk-edit and one-off
    scripts don't pass it and must not silently clobber it."""
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.update_tool(tool_id, "Runway", "New desc", "https://runway.com", ["FP&A"],
                     summary="s", description_needs_verification=1)
    assert lib.get_tool(tool_id)["description_needs_verification"] == 1

    # A second save that omits the param must leave the flag as it was.
    lib.update_tool(tool_id, "Runway", "New desc 2", "https://runway.com", ["FP&A"], summary="s")
    assert lib.get_tool(tool_id)["description_needs_verification"] == 1


def test_update_tool_description_needs_verification_explicit_zero_clears(lib):
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.update_tool(tool_id, "Runway", "d", "https://runway.com", ["FP&A"],
                     summary="s", description_needs_verification=1)
    lib.update_tool(tool_id, "Runway", "d", "https://runway.com", ["FP&A"],
                     summary="s", description_needs_verification=0)
    assert lib.get_tool(tool_id)["description_needs_verification"] == 0


def test_update_tool_competitive_differentiation_needs_verification_param(lib):
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.update_tool_differentiation(tool_id, "Faster onboarding.", needs_verification=1)
    assert lib.get_tool(tool_id)["competitive_differentiation_needs_verification"] == 1
    lib.update_tool_differentiation(tool_id, "Faster onboarding.", needs_verification=0)
    assert lib.get_tool(tool_id)["competitive_differentiation_needs_verification"] == 0


def test_update_tool_differentiation_defaults_to_zero(lib):
    """Every pre-Phase-G caller (tests, scripts) omits needs_verification —
    must keep behaving as "verified"."""
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.update_tool_differentiation(tool_id, "Faster onboarding.")
    assert lib.get_tool(tool_id)["competitive_differentiation_needs_verification"] == 0


def test_mark_tool_description_verified(lib):
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.update_tool(tool_id, "Runway", "d", "https://runway.com", ["FP&A"],
                     summary="s", description_needs_verification=1)
    lib.mark_tool_description_verified(tool_id)
    tool = lib.get_tool(tool_id)
    assert tool["description_needs_verification"] == 0
    assert tool["description"] == "d"   # text untouched


def test_mark_tool_differentiation_verified(lib):
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.update_tool_differentiation(tool_id, "Faster onboarding.", needs_verification=1)
    lib.mark_tool_differentiation_verified(tool_id)
    tool = lib.get_tool(tool_id)
    assert tool["competitive_differentiation_needs_verification"] == 0
    assert tool["competitive_differentiation"] == "Faster onboarding."


# -- Edit-submit route: sets the flag from ai_drafted_fields --------------------

def test_edit_submit_sets_description_needs_verification_when_ai_drafted(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    slug = lib.get_tool(tool_id)["slug"]
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post(f"/tools/software/{slug}/edit", data={"primary_category": "FP&A", 
        "name": "Runway", "url": "https://runway.com", "description": "AI drafted text",
        "summary": "AI drafted summary", "ai_drafted_fields": "description,summary",
    }, follow_redirects=False)
    assert r.status_code == 303

    lib = Library(os.environ["LINKLIB_DB"])
    tool = lib.get_tool(tool_id)
    assert tool["description_needs_verification"] == 1
    lib.close()


def test_edit_submit_clears_description_needs_verification_when_not_ai_drafted(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.update_tool(tool_id, "Runway", "d", "https://runway.com", ["FP&A"],
                     summary="s", description_needs_verification=1)
    slug = lib.get_tool(tool_id)["slug"]
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post(f"/tools/software/{slug}/edit", data={"primary_category": "FP&A", 
        "name": "Runway", "url": "https://runway.com", "description": "Hand-edited text",
        "summary": "s",
    }, follow_redirects=False)
    assert r.status_code == 303

    lib = Library(os.environ["LINKLIB_DB"])
    tool = lib.get_tool(tool_id)
    assert tool["description_needs_verification"] == 0
    lib.close()


def test_edit_submit_sets_competitive_differentiation_needs_verification_when_ai_drafted(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    slug = lib.get_tool(tool_id)["slug"]
    lib.close()

    client = _client(env)
    _login(client)
    client.post(f"/tools/software/{slug}/edit", data={"primary_category": "FP&A", 
        "name": "Runway", "url": "https://runway.com", "description": "d", "summary": "s",
        "competitive_differentiation": "AI drafted note", "ai_drafted_fields": "competitive_differentiation",
    }, follow_redirects=False)

    lib = Library(os.environ["LINKLIB_DB"])
    assert lib.get_tool(tool_id)["competitive_differentiation_needs_verification"] == 1
    lib.close()


# -- Verify routes ---------------------------------------------------------------

def test_description_verify_route_clears_flag_and_logs(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    lib.create_user("brian", "pw", role="admin", name="Brian")
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.update_tool(tool_id, "Runway", "AI text", "https://runway.com", ["FP&A"],
                     summary="s", description_needs_verification=1)
    lib.close()

    client = _client(env)
    _login(client, "brian", "pw")
    r = client.post(f"/admin/tools/software/{tool_id}/description/verify", follow_redirects=False)
    assert r.status_code == 303

    lib = Library(os.environ["LINKLIB_DB"])
    tool = lib.get_tool(tool_id)
    assert tool["description_needs_verification"] == 0
    review = lib.get_latest_narrative_review("tool", "description", tool_id)
    assert review is not None
    assert review["admin_username"] == "brian"
    assert review["detail"] == "AI text"
    lib.close()


def test_differentiation_verify_route_clears_flag_and_logs(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.update_tool_differentiation(tool_id, "AI note", needs_verification=1)
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post(f"/admin/tools/software/{tool_id}/differentiation/verify", follow_redirects=False)
    assert r.status_code == 303

    lib = Library(os.environ["LINKLIB_DB"])
    tool = lib.get_tool(tool_id)
    assert tool["competitive_differentiation_needs_verification"] == 0
    review = lib.get_latest_narrative_review("tool", "differentiation", tool_id)
    assert review is not None and review["detail"] == "AI note"
    lib.close()


def test_verify_routes_require_auth(env):
    client = _client(env)
    assert client.post("/admin/tools/software/1/description/verify").status_code == 401
    assert client.post("/admin/tools/software/1/differentiation/verify").status_code == 401


def test_verify_routes_404_for_missing_tool(env):
    client = _client(env)
    _login(client)
    assert client.post("/admin/tools/software/999999/description/verify").status_code == 404
    assert client.post("/admin/tools/software/999999/differentiation/verify").status_code == 404


# -- Edit page rendering: badges/buttons/review lines --------------------------

def test_edit_page_shows_description_badge_and_button_when_needs_verification(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    slug = lib.get_tool(tool_id)["slug"]
    lib.update_tool(tool_id, "Runway", "d", "https://runway.com", ["FP&A"],
                     summary="s", description_needs_verification=1)
    lib.close()

    client = _client(env)
    _login(client)
    r = client.get(f"/tools/software/{slug}/edit")
    assert 'action="/admin/tools/software/{}/description/verify"'.format(tool_id) in r.text
    assert "unverified, visible to visitors" in r.text


def test_edit_page_hides_description_button_once_verified(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    slug = lib.get_tool(tool_id)["slug"]
    lib.close()

    client = _client(env)
    _login(client)
    r = client.get(f"/tools/software/{slug}/edit")
    assert "description/verify" not in r.text


def test_edit_page_shows_verified_by_line_for_description(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    lib.create_user("brian", "pw", role="admin", name="Brian")
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    slug = lib.get_tool(tool_id)["slug"]
    lib.update_tool(tool_id, "Runway", "d", "https://runway.com", ["FP&A"],
                     summary="s", description_needs_verification=1)
    lib.close()

    client = _client(env)
    _login(client, "brian", "pw")
    client.post(f"/admin/tools/software/{tool_id}/description/verify", follow_redirects=False)

    r = client.get(f"/tools/software/{slug}/edit")
    assert "Verified by brian on" in r.text


# -- field_reviews retirement: description/summary/differentiation stop -------

def test_field_reviews_not_written_for_retired_tool_fields(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    slug = lib.get_tool(tool_id)["slug"]
    lib.close()

    client = _client(env)
    _login(client)
    client.post(f"/tools/software/{slug}/edit", data={"primary_category": "FP&A", 
        "name": "Runway", "url": "https://runway.com", "description": "d", "summary": "s",
        "competitive_differentiation": "n",
        "ai_drafted_fields": "description,summary,competitive_differentiation",
    }, follow_redirects=False)

    lib = Library(os.environ["LINKLIB_DB"])
    reviews = lib.list_field_reviews("tool", tool_id)
    assert reviews == {}   # none of the retired fields wrote a field_reviews row
    lib.close()


# -- Community profile draft: reuses needs_review -------------------------------

def _profile_form_data(**overrides):
    data = {
        "ideal_member": "CFOs", "verdict_summary": "Best for X, not for Y.",
    }
    data.update(overrides)
    return data


def test_profile_submit_sets_needs_review_when_ai_drafted(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    community_id = lib.add_community(
        name="Finance Leaders", url="https://fl.example", demographic="CFOs",
        cost_band="Free", categories=[], approved=1,
    )
    lib.close()

    client = _client(env)
    _login(client)
    post_profile(client, community_id, data=_profile_form_data(
        ai_drafted_fields="ideal_member,verdict_summary",
    ), follow_redirects=False)

    lib = Library(os.environ["LINKLIB_DB"])
    profile = lib.get_community_profile(community_id)
    assert profile["needs_review"] == 1
    lib.close()


def test_profile_submit_ignores_stray_needs_review_form_field(env):
    """2026-08 Review-status consolidation: the needs_review checkbox is
    gone from this form (the whole-record signoff is now the shared
    pill+action at the top of the page, an immediate one-click route, not
    tied to Save) — a stray `needs_review` form field is simply ignored by
    the submit route now. A brand-new profile (no prior needs_review=1
    state) with no ai_drafted fields stays 0 regardless."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    community_id = lib.add_community(
        name="Finance Leaders", url="https://fl.example", demographic="CFOs",
        cost_band="Free", categories=[], approved=1,
    )
    lib.close()

    client = _client(env)
    _login(client)
    post_profile(client, community_id, data=_profile_form_data(needs_review="1"), follow_redirects=False)

    lib = Library(os.environ["LINKLIB_DB"])
    assert lib.get_community_profile(community_id)["needs_review"] == 0
    lib.close()


def test_profile_submit_persists_existing_needs_review_without_ai_draft(env):
    """2026-08 Review-status consolidation: manual state now persists by
    carrying the CURRENTLY-persisted DB value forward on every save (since
    there's no checkbox to reflect it back), rather than the old
    checkbox-driven behavior where an ordinary resave with no ai draft used
    to silently CLEAR a manually-set flag. Only the dedicated "Mark
    reviewed" action (its own route) can clear it now — a plain Save can
    never accidentally lose it."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    community_id = lib.add_community(
        name="Finance Leaders", url="https://fl.example", demographic="CFOs",
        cost_band="Free", categories=[], approved=1,
    )
    lib.upsert_community_profile(community_id, needs_review=1)
    lib.close()

    client = _client(env)
    _login(client)
    post_profile(client, community_id, data=_profile_form_data(), follow_redirects=False)

    lib = Library(os.environ["LINKLIB_DB"])
    assert lib.get_community_profile(community_id)["needs_review"] == 1
    lib.close()


def test_field_reviews_not_written_for_community_profile_fields(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    community_id = lib.add_community(
        name="Finance Leaders", url="https://fl.example", demographic="CFOs",
        cost_band="Free", categories=[], approved=1,
    )
    lib.close()

    client = _client(env)
    _login(client)
    post_profile(client, community_id, data=_profile_form_data(
        ai_drafted_fields="ideal_member,verdict_summary",
    ), follow_redirects=False)

    lib = Library(os.environ["LINKLIB_DB"])
    assert lib.list_field_reviews("community", community_id) == {}
    lib.close()


def test_field_reviews_still_written_for_listing_autofill_fields(env):
    """Contrast case: the Community "Auto-fill from URL" listing fields are
    NOT retired — field_reviews should still track them."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    community_id = lib.add_community(
        name="Finance Leaders", url="https://fl.example", demographic="CFOs",
        cost_band="Free", categories=[], approved=1,
    )
    slug = lib.get_community(community_id)["slug"]
    lib.close()

    client = _client(env)
    _login(client)
    client.post(f"/tools/communities/{slug}/edit", data={
        "name": "Finance Leaders", "demographic": "CFOs", "cost_band": "Free",
        "sponsorship_type": "Independent", "reach": "National",
        "ai_drafted_fields": "demographic",
    }, follow_redirects=False)

    lib = Library(os.environ["LINKLIB_DB"])
    reviews = lib.list_field_reviews("community", community_id)
    assert "demographic" in reviews
    lib.close()


# -- Mark-reviewed route: now logs + supports redirect_to -----------------------

def test_mark_reviewed_writes_narrative_review_log(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    lib.create_user("brian", "pw", role="admin", name="Brian")
    community_id = lib.add_community(
        name="Finance Leaders", url="https://fl.example", demographic="CFOs",
        cost_band="Free", categories=[], approved=1,
    )
    lib.upsert_community_profile(community_id, needs_review=1, verdict_summary="Best for X.")
    lib.close()

    client = _client(env)
    _login(client, "brian", "pw")
    r = client.post(f"/admin/tools/communities/{community_id}/mark-reviewed", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/admin/tools/communities"

    lib = Library(os.environ["LINKLIB_DB"])
    assert lib.get_community_profile(community_id)["needs_review"] == 0
    review = lib.get_latest_narrative_review("community", "community_profile", community_id)
    assert review is not None
    assert review["admin_username"] == "brian"
    assert review["detail"] == "Best for X."
    lib.close()


def test_mark_reviewed_redirects_to_profile_page_when_asked(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    community_id = lib.add_community(
        name="Finance Leaders", url="https://fl.example", demographic="CFOs",
        cost_band="Free", categories=[], approved=1,
    )
    lib.upsert_community_profile(community_id, needs_review=1)
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post(f"/admin/tools/communities/{community_id}/mark-reviewed",
                     data={"redirect_to": edit_url(community_id)},
                     follow_redirects=False)
    assert r.headers["location"] == edit_url(community_id)


def test_mark_reviewed_rejects_unknown_redirect_to(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    community_id = lib.add_community(
        name="Finance Leaders", url="https://fl.example", demographic="CFOs",
        cost_band="Free", categories=[], approved=1,
    )
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post(f"/admin/tools/communities/{community_id}/mark-reviewed",
                     data={"redirect_to": "https://evil.example"}, follow_redirects=False)
    assert r.headers["location"] == "/admin/tools/communities"


# -- Profile edit page: inline Mark reviewed button + Reviewed-by line ---------

def test_profile_edit_page_shows_mark_reviewed_button_when_needs_review(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    community_id = lib.add_community(
        name="Finance Leaders", url="https://fl.example", demographic="CFOs",
        cost_band="Free", categories=[], approved=1,
    )
    lib.upsert_community_profile(community_id, needs_review=1)
    lib.close()

    client = _client(env)
    _login(client)
    r = get_profile(client, community_id)
    assert "Mark reviewed" in r.text
    assert f"/admin/tools/communities/{community_id}/mark-reviewed" in r.text


def test_profile_edit_page_shows_reviewed_by_line_after_mark_reviewed(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    lib.create_user("brian", "pw", role="admin", name="Brian")
    community_id = lib.add_community(
        name="Finance Leaders", url="https://fl.example", demographic="CFOs",
        cost_band="Free", categories=[], approved=1,
    )
    lib.upsert_community_profile(community_id, needs_review=1)
    lib.close()

    client = _client(env)
    _login(client, "brian", "pw")
    client.post(f"/admin/tools/communities/{community_id}/mark-reviewed", follow_redirects=False)

    r = get_profile(client, community_id)
    assert "Reviewed by brian on" in r.text
