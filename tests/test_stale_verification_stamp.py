"""Stale "Verified by X on Y" stamp fix (2026-08 follow-up to Phase G PR 2).

Regenerating a field via Generate/Refresh/Save correctly sets
needs_verification=1 (the amber badge), but a prior narrative_review_log
"Mark verified" stamp for that field used to keep displaying unchanged next
to it — get_latest_narrative_review had no way to know the verified content
had since been replaced. Fixed: every write path that lands a fresh,
not-yet-human-reviewed AI draft now supersedes any live narrative_review_log
row for that (entity_type, field_type, item_id), so the stamp reads "never
verified" until an explicit Mark verified click writes a fresh one.

scripts/regen_ai_drafted_fields.py forces needs_verification=0 directly (a
deliberate bypass of the review badge — see its own docstring), so it can't
be covered by inferring "clear the stamp" from needs_verification==1; it
gets the same treatment via an explicit clear_*_stamp=True kwarg instead —
covered here at the Library layer, mirroring exactly what the script itself
calls.
"""
import os
import pathlib
import sys
import tempfile

import pytest
from tests.community_edit_helpers import post_profile, get_profile  # noqa: F401

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


# -- Library layer: explicit clear_*_stamp kwarg (mirrors the script's path) --

def test_update_tool_clears_description_stamp_when_flag_set(lib):
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.record_narrative_review(None, "tool", "description", tool_id, detail="old text")
    assert lib.get_latest_narrative_review("tool", "description", tool_id) is not None

    lib.update_tool(tool_id, "Runway", "fresh draft", "https://runway.com", ["FP&A"],
                     summary="s", description_needs_verification=1,
                     clear_description_verification_stamp=True)
    assert lib.get_latest_narrative_review("tool", "description", tool_id) is None


def test_update_tool_script_bypass_pattern_also_clears_stamp(lib):
    """The script forces needs_verification=0 directly (unlike the live
    route's =1) but still passes clear_description_verification_stamp=True
    — must clear the stamp exactly the same as the live-route case above."""
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.record_narrative_review(None, "tool", "description", tool_id, detail="old text")

    lib.update_tool(tool_id, "Runway", "fresh draft", "https://runway.com", ["FP&A"],
                     summary="s", description_needs_verification=0,
                     clear_description_verification_stamp=True)
    assert lib.get_latest_narrative_review("tool", "description", tool_id) is None
    assert lib.get_tool(tool_id)["description_needs_verification"] == 0


def test_update_tool_ordinary_hand_edit_does_not_clear_stamp(lib):
    """Negative control: a plain hand-edit save (description_needs_verification=0,
    clear flag not set) must leave an existing stamp alone — only an
    explicit clear request should touch narrative_review_log."""
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.record_narrative_review(None, "tool", "description", tool_id, detail="old text")

    lib.update_tool(tool_id, "Runway", "hand-typed edit", "https://runway.com", ["FP&A"],
                     summary="s", description_needs_verification=0)
    assert lib.get_latest_narrative_review("tool", "description", tool_id) is not None


def test_update_tool_differentiation_clears_stamp_when_flag_set(lib):
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.record_narrative_review(None, "tool", "differentiation", tool_id, detail="old note")

    lib.update_tool_differentiation(tool_id, "fresh note", needs_verification=0,
                                     clear_verification_stamp=True)
    assert lib.get_latest_narrative_review("tool", "differentiation", tool_id) is None


def test_set_tool_agent_taxonomy_draft_always_clears_stamp(lib):
    """No explicit kwarg needed — this method is used only for fresh AI
    drafts (never hand-edits), so it clears unconditionally."""
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.record_narrative_review(None, "tool", "agent_taxonomy", tool_id, detail="old note")
    assert lib.get_latest_narrative_review("tool", "agent_taxonomy", tool_id) is not None

    lib.set_tool_agent_taxonomy_draft(tool_id, "fresh drafted note", needs_verification=0)
    assert lib.get_latest_narrative_review("tool", "agent_taxonomy", tool_id) is None


def test_upsert_community_profile_clears_stamp_when_flag_set(lib):
    community_id = lib.add_community(
        name="Finance Leaders", url="https://fl.example", demographic="CFOs",
        cost_band="Free", categories=[], approved=1,
    )
    lib.record_narrative_review(None, "community", "community_profile", community_id, detail="old verdict")

    lib.upsert_community_profile(community_id, needs_review=0, verdict_summary="fresh verdict",
                                  clear_verification_stamp=True)
    assert lib.get_latest_narrative_review("community", "community_profile", community_id) is None


def test_upsert_community_profile_manual_checkbox_does_not_clear_stamp(lib):
    """A manually-checked needs_review=1 (no fresh AI draft at all) must NOT
    clear the stamp — only clear_verification_stamp=True (driven by
    profile_ai_drafted, not the needs_review value) may."""
    community_id = lib.add_community(
        name="Finance Leaders", url="https://fl.example", demographic="CFOs",
        cost_band="Free", categories=[], approved=1,
    )
    lib.record_narrative_review(None, "community", "community_profile", community_id, detail="old verdict")

    lib.upsert_community_profile(community_id, needs_review=1, verdict_summary="unchanged verdict")
    assert lib.get_latest_narrative_review("community", "community_profile", community_id) is not None


def test_superseded_row_survives_in_full_history(lib):
    """narrative_review_log stays append-only — superseding hides a row from
    get_latest_narrative_review but must not delete it."""
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.record_narrative_review(None, "tool", "description", tool_id, detail="old text")
    lib.update_tool(tool_id, "Runway", "fresh draft", "https://runway.com", ["FP&A"],
                     summary="s", description_needs_verification=1,
                     clear_description_verification_stamp=True)
    detail_texts = [r["detail"] for r in lib.list_narrative_review_log()]
    assert "old text" in detail_texts


# -- Route layer: live Generate/Refresh/Save flow ----------------------------

def test_edit_submit_clears_stale_description_stamp_on_fresh_draft(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    lib.create_user("brian", "pw", role="admin", name="Brian")
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    slug = lib.get_tool(tool_id)["slug"]
    lib.record_narrative_review(None, "tool", "description", tool_id, detail="old text")
    lib.close()

    client = _client(env)
    _login(client, "brian", "pw")
    r = client.post(f"/tools/software/{slug}/edit", data={"primary_category": "FP&A", 
        "name": "Runway", "url": "https://runway.com", "description": "AI drafted text",
        "summary": "AI drafted summary", "ai_drafted_fields": "description,summary",
    }, follow_redirects=False)
    assert r.status_code == 303

    lib = Library(os.environ["LINKLIB_DB"])
    assert lib.get_tool(tool_id)["description_needs_verification"] == 1
    assert lib.get_latest_narrative_review("tool", "description", tool_id) is None
    # The edit page should render "never verified" — no stale "Verified by" line.
    lib.close()
    r = client.get(f"/tools/software/{slug}/edit")
    assert "Verified by brian on" not in r.text
    assert "unverified, visible to visitors" in r.text


def test_edit_submit_hand_edit_does_not_clear_stale_description_stamp(env):
    """Contrast case: a hand-edit save (no ai_drafted_fields) treats itself
    as the confirmation and clears needs_verification, but must not blank
    out an existing, still-accurate "Verified by X on Y" stamp."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    lib.create_user("brian", "pw", role="admin", name="Brian")
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    slug = lib.get_tool(tool_id)["slug"]
    lib.record_narrative_review(None, "tool", "description", tool_id, detail="old text")
    lib.close()

    client = _client(env)
    _login(client, "brian", "pw")
    client.post(f"/tools/software/{slug}/edit", data={"primary_category": "FP&A", 
        "name": "Runway", "url": "https://runway.com", "description": "hand-typed tweak",
        "summary": "s",
    }, follow_redirects=False)

    lib = Library(os.environ["LINKLIB_DB"])
    assert lib.get_latest_narrative_review("tool", "description", tool_id) is not None
    lib.close()


def test_mark_verified_after_regeneration_sets_fresh_stamp(env):
    """After a regeneration clears the stamp, clicking Mark verified must
    still work correctly and produce a fresh, current stamp."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    lib.create_user("brian", "pw", role="admin", name="Brian")
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    slug = lib.get_tool(tool_id)["slug"]
    lib.record_narrative_review(None, "tool", "description", tool_id, detail="old text")
    lib.close()

    client = _client(env)
    _login(client, "brian", "pw")
    client.post(f"/tools/software/{slug}/edit", data={"primary_category": "FP&A", 
        "name": "Runway", "url": "https://runway.com", "description": "AI drafted text",
        "summary": "s", "ai_drafted_fields": "description",
    }, follow_redirects=False)

    lib = Library(os.environ["LINKLIB_DB"])
    assert lib.get_latest_narrative_review("tool", "description", tool_id) is None
    lib.close()

    r = client.post(f"/admin/tools/software/{tool_id}/description/verify", follow_redirects=False)
    assert r.status_code == 303

    lib = Library(os.environ["LINKLIB_DB"])
    tool = lib.get_tool(tool_id)
    assert tool["description_needs_verification"] == 0
    review = lib.get_latest_narrative_review("tool", "description", tool_id)
    assert review is not None
    assert review["admin_username"] == "brian"
    assert review["detail"] == "AI drafted text"
    lib.close()

    r = client.get(f"/tools/software/{slug}/edit")
    assert "Verified by brian on" in r.text


def test_community_profile_submit_clears_stale_stamp_on_fresh_draft(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    lib.create_user("brian", "pw", role="admin", name="Brian")
    community_id = lib.add_community(
        name="Finance Leaders", url="https://fl.example", demographic="CFOs",
        cost_band="Free", categories=[], approved=1,
    )
    lib.record_narrative_review(None, "community", "community_profile", community_id, detail="old verdict")
    lib.close()

    client = _client(env)
    _login(client, "brian", "pw")
    post_profile(client, community_id, data={
        "ideal_member": "CFOs", "verdict_summary": "fresh verdict",
        "ai_drafted_fields": "ideal_member,verdict_summary",
    }, follow_redirects=False)

    lib = Library(os.environ["LINKLIB_DB"])
    assert lib.get_community_profile(community_id)["needs_review"] == 1
    assert lib.get_latest_narrative_review("community", "community_profile", community_id) is None
    lib.close()


def test_mark_reviewed_after_community_regeneration_sets_fresh_stamp(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    lib.create_user("brian", "pw", role="admin", name="Brian")
    community_id = lib.add_community(
        name="Finance Leaders", url="https://fl.example", demographic="CFOs",
        cost_band="Free", categories=[], approved=1,
    )
    lib.record_narrative_review(None, "community", "community_profile", community_id, detail="old verdict")
    lib.close()

    client = _client(env)
    _login(client, "brian", "pw")
    post_profile(client, community_id, data={
        "ideal_member": "CFOs", "verdict_summary": "fresh verdict",
        "ai_drafted_fields": "ideal_member,verdict_summary",
    }, follow_redirects=False)

    r = client.post(f"/admin/tools/communities/{community_id}/mark-reviewed", follow_redirects=False)
    assert r.status_code == 303

    lib = Library(os.environ["LINKLIB_DB"])
    review = lib.get_latest_narrative_review("community", "community_profile", community_id)
    assert review is not None
    assert review["admin_username"] == "brian"
    assert review["detail"] == "fresh verdict"
    lib.close()
