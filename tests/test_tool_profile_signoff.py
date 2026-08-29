"""Whole-record tool profile signoff (2026-08), amended same phase.

Mirrors community_profiles.needs_review as closely as sensible for tools'
shape, sitting alongside the three existing per-field *_needs_verification
flags — see CLAUDE.md's "Tools whole-record profile signoff" bullet.

Two reversals from the original build, both by explicit direction:

- **Auto-linked to per-field regeneration** — a fresh Generate/Refresh draft
  that sets ANY of the three per-field flags (description_needs_verification,
  agent_taxonomy_needs_verification, competitive_differentiation_needs_
  verification) to 1 also forces tools.needs_review to 1, mirroring
  Communities' own `needs_review = checkbox OR profile_ai_drafted` pattern —
  precisely where structurally possible (Description/Differentiation share a
  request with the checkbox, in admin_tools_edit_submit) and via a direct
  force-to-1 call where it isn't (Agent taxonomy's own draft trigger fires
  from a separate request in _run_tool_research). The checkbox/"Mark
  reviewed" button can still clear it to 0 at any time, and that manual 0
  persists across any later save that doesn't itself draft one of the three
  fields.
- **Defaults to 1 (needs review) on tool creation**, not 0 — a brand-new
  profile should read as "needs review" until someone signs off on it.

Also covers the new "Needs review" admin-list filter/badge/inline Mark
reviewed action on /admin/tools/software (mirroring the pre-existing one on
/admin/tools/communities), plus a regression test for a genuine pre-existing
bug this amendment's verification pass found (and fixed) in BOTH admin list
routes' empty-state fallback expression.
"""
import os
import pathlib
import sys
import tempfile
from unittest.mock import patch

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


class _FakeTaxonomyResult:
    """Stand-in for enrich.generate_tool_agent_taxonomy's return value."""
    def __init__(self, needs_verification, confident):
        self.agent_taxonomy_note = "A note about this tool's agent capabilities."
        self.agent_taxonomy_needs_verification = needs_verification
        self.confident = confident
        self.low_confidence = 0
        self.citations = []
        self.model = "test-model"
        self.cost_usd = 0.0
        self.input_tokens = 0
        self.output_tokens = 0


# -- Library layer: default state, manual toggle, mark-reviewed -----------------

def test_needs_review_defaults_to_one_on_creation(lib):
    """Reversal 2: a brand-new tool starts flagged for review, not
    already-reviewed — matches Communities' own "unreviewed until confirmed"
    intent, even though the mechanism differs (Communities has no profile
    row at all yet; tools default the column itself to 1)."""
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    assert lib.get_tool(tool_id)["needs_review"] == 1


def test_add_tool_accepts_explicit_needs_review_override(lib):
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1, needs_review=0)
    assert lib.get_tool(tool_id)["needs_review"] == 0


def test_set_tool_needs_review_toggles_independent_of_per_field_flags_at_library_layer(lib):
    """Independence still holds at the raw Library layer — Library.update_tool()
    itself never touches needs_review; the auto-link coupling lives one layer
    up, in the webapp submit routes/call sites (mirroring where Communities'
    own profile_ai_drafted OR lives: the route, not upsert_community_profile).
    A direct field write that bypasses those routes entirely is unaffected."""
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1, needs_review=0)
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
    assert lib.count_tools_needing_review() == 2  # both default to 1 now
    lib.mark_tool_reviewed(t1)
    assert lib.count_tools_needing_review() == 1
    lib.mark_tool_reviewed(t2)
    assert lib.count_tools_needing_review() == 0


# -- Edit-submit route: checkbox + auto-link from Description/Differentiation ---

def test_edit_submit_sets_needs_review_from_checkbox(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1, needs_review=0)
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


def test_edit_submit_clears_needs_review_when_checkbox_unchecked_and_no_fresh_draft(env):
    """Manual 0 persists across a save that doesn't itself draft one of the
    three fields — no ai_drafted_fields this submit, checkbox unchecked."""
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


def test_edit_submit_forces_needs_review_when_description_ai_drafted(env):
    """Reversal 1: a fresh Description draft this submit forces needs_review
    to 1, even with the checkbox absent/unchecked — the opposite of the
    original build's purely-manual behavior."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.mark_tool_reviewed(tool_id)
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
    assert tool["description_needs_verification"] == 1
    assert tool["needs_review"] == 1
    lib.close()


def test_edit_submit_forces_needs_review_when_differentiation_ai_drafted(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.mark_tool_reviewed(tool_id)
    slug = lib.get_tool(tool_id)["slug"]
    lib.close()

    client = _client(env)
    _login(client)
    client.post(f"/tools/software/{slug}/edit", data={
        "name": "Runway", "url": "https://runway.com", "description": "d", "summary": "s",
        "competitive_differentiation": "AI drafted note",
        "ai_drafted_fields": "competitive_differentiation",
    }, follow_redirects=False)

    lib = Library(os.environ["LINKLIB_DB"])
    tool = lib.get_tool(tool_id)
    assert tool["competitive_differentiation_needs_verification"] == 1
    assert tool["needs_review"] == 1
    lib.close()


def test_edit_submit_fresh_draft_overrides_unchecked_checkbox(env):
    """The checkbox being explicitly absent (unchecked) in the SAME submit
    as a fresh draft must not prevent the force-to-1 — a fresh draft always
    wins, regardless of the checkbox's value in that submission."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.mark_tool_reviewed(tool_id)
    slug = lib.get_tool(tool_id)["slug"]
    lib.close()

    client = _client(env)
    _login(client)
    # No "needs_review" key at all in the posted data (unchecked checkbox).
    client.post(f"/tools/software/{slug}/edit", data={
        "name": "Runway", "url": "https://runway.com", "description": "AI text",
        "summary": "AI sum", "ai_drafted_fields": "description,summary",
    }, follow_redirects=False)

    lib = Library(os.environ["LINKLIB_DB"])
    assert lib.get_tool(tool_id)["needs_review"] == 1
    lib.close()


def test_edit_submit_hand_edit_only_does_not_force_needs_review(env):
    """A hand-edited save with no ai_drafted_fields is itself a confirmation
    for the per-field flags (existing convention) — it must not force
    needs_review either, same as before this amendment."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.update_tool(tool_id, "Runway", "d", "https://runway.com", ["FP&A"],
                     summary="s", description_needs_verification=1)
    lib.mark_tool_reviewed(tool_id)
    slug = lib.get_tool(tool_id)["slug"]
    lib.close()

    client = _client(env)
    _login(client)
    client.post(f"/tools/software/{slug}/edit", data={
        "name": "Runway", "url": "https://runway.com", "description": "hand-edited text",
        "summary": "s",
    }, follow_redirects=False)

    lib = Library(os.environ["LINKLIB_DB"])
    tool = lib.get_tool(tool_id)
    assert tool["description_needs_verification"] == 0
    assert tool["needs_review"] == 0
    lib.close()


# -- Agent taxonomy auto-link (a separate request from the checkbox) ------------

def test_run_tool_research_forces_needs_review_on_unconfident_draft(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    lib.set_setting("voice_core", "be direct")
    tool_id = lib.add_tool("Runway", "d", "https://runway.com", ["FP&A"], approved=1)
    lib.mark_tool_reviewed(tool_id)
    lib.close()

    with patch("linklib.enrich.generate_tool_agent_taxonomy",
               return_value=_FakeTaxonomyResult(needs_verification=True, confident=False)):
        assert env._run_tool_research(tool_id) is True

    lib = Library(os.environ["LINKLIB_DB"])
    tool = lib.get_tool(tool_id)
    assert tool["agent_taxonomy_needs_verification"] == 1
    assert tool["needs_review"] == 1
    lib.close()


def test_run_tool_research_does_not_force_needs_review_on_confident_draft(env):
    """A confident agent-taxonomy draft leaves needs_review untouched — the
    trigger is keyed on the per-field flag actually landing on 1, not
    merely "a fresh draft happened"."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    lib.set_setting("voice_core", "be direct")
    tool_id = lib.add_tool("Runway", "d", "https://runway.com", ["FP&A"], approved=1)
    lib.mark_tool_reviewed(tool_id)
    lib.close()

    with patch("linklib.enrich.generate_tool_agent_taxonomy",
               return_value=_FakeTaxonomyResult(needs_verification=False, confident=True)):
        assert env._run_tool_research(tool_id) is True

    lib = Library(os.environ["LINKLIB_DB"])
    tool = lib.get_tool(tool_id)
    assert tool["agent_taxonomy_needs_verification"] == 0
    assert tool["needs_review"] == 0
    lib.close()


def test_run_tool_research_bypass_script_pattern_does_not_trigger_auto_link(env):
    """scripts/regen_ai_drafted_fields.py's own deliberate bypass (forces
    needs_verification=0 for all three fields) must not trip the auto-link —
    confirmed directly at the Library layer, the same call shape that
    script uses."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "d", "https://runway.com", ["FP&A"], approved=1)
    lib.mark_tool_reviewed(tool_id)
    lib.set_tool_agent_taxonomy_draft(tool_id, "fresh note", needs_verification=0, ai_confident=1)
    assert lib.get_tool(tool_id)["needs_review"] == 0
    lib.close()


# -- Mark-reviewed route: redirect_to validation ---------------------------------

def test_mark_reviewed_route_clears_flag_and_logs(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    lib.create_user("brian", "pw", role="admin", name="Brian")
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.update_tool(tool_id, "Runway", "Full text", "https://runway.com", ["FP&A"], summary="s")
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


def test_mark_reviewed_route_defaults_to_admin_list_redirect(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post(f"/admin/tools/software/{tool_id}/mark-reviewed", follow_redirects=False)
    assert r.headers["location"] == "/admin/tools/software"


def test_mark_reviewed_route_honors_edit_page_redirect_to(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    slug = lib.get_tool(tool_id)["slug"]
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post(f"/admin/tools/software/{tool_id}/mark-reviewed",
                     data={"redirect_to": f"/tools/software/{slug}/edit"}, follow_redirects=False)
    assert r.headers["location"] == f"/tools/software/{slug}/edit"


def test_mark_reviewed_route_rejects_unknown_redirect_to(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post(f"/admin/tools/software/{tool_id}/mark-reviewed",
                     data={"redirect_to": "https://evil.example.com"}, follow_redirects=False)
    assert r.headers["location"] == "/admin/tools/software"


# -- Edit page rendering ----------------------------------------------------------

def test_edit_page_shows_checkbox_and_mark_reviewed_button_when_needs_review(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    slug = lib.get_tool(tool_id)["slug"]
    lib.close()

    client = _client(env)
    _login(client)
    r = client.get(f"/tools/software/{slug}/edit")
    assert 'name="needs_review"' in r.text
    assert " checked" in r.text.split('name="needs_review"')[1][:40]
    assert 'action="/admin/tools/software/{}/mark-reviewed"'.format(tool_id) in r.text
    assert "Mark reviewed" in r.text
    # The edit page's own hidden form must carry redirect_to back to itself.
    assert f'name="redirect_to" value="/tools/software/{slug}/edit"' in r.text


def test_edit_page_hides_mark_reviewed_button_when_not_flagged(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.mark_tool_reviewed(tool_id)
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


# -- Admin list "Needs review" filter (new scope) --------------------------------

def test_software_admin_list_shows_needs_review_count_link(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    lib.add_tool("Runway", "d", "https://runway.com", ["FP&A"], approved=1)  # defaults to needs_review=1
    lib.close()

    client = _client(env)
    _login(client)
    r = client.get("/admin/tools/software")
    assert "needs review" in r.text  # "1 needs review →" (singular n==1 phrasing)
    assert "Needs review" in r.text  # the row badge


def test_software_admin_list_filter_scopes_to_needs_review_only(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    lib.add_tool("Runway", "d", "https://runway.com", ["FP&A"], approved=1)
    t2 = lib.add_tool("Abacum", "d", "https://abacum.io", ["FP&A"], approved=1)
    lib.mark_tool_reviewed(t2)
    lib.close()

    client = _client(env)
    _login(client)
    r = client.get("/admin/tools/software?filter=needs_review")
    assert "Runway" in r.text
    assert "Abacum" not in r.text
    assert "needing review" in r.text  # header suffix


def test_software_admin_list_filter_shows_nothing_left_to_review_when_empty(env):
    """Regression test for the operator-precedence bug this amendment's
    verification pass found in BOTH admin list routes: `A or B if C else D`
    parses as `(A or B) if C else D`, so the "empty" fallback used to render
    unconditionally whenever filter=="needs_review" — discarding real
    matching rows. This asserts the OPPOSITE: when there genuinely are no
    rows to review, the correct empty message shows; the sibling test above
    (`test_software_admin_list_filter_scopes_to_needs_review_only`) is what
    actually proves real rows still render when filter=="needs_review"."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Abacum", "d", "https://abacum.io", ["FP&A"], approved=1)
    lib.mark_tool_reviewed(tool_id)
    lib.close()

    client = _client(env)
    _login(client)
    r = client.get("/admin/tools/software?filter=needs_review")
    assert "Nothing left to review." in r.text
    assert "Abacum" not in r.text


def test_software_admin_list_inline_mark_reviewed_button(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "d", "https://runway.com", ["FP&A"], approved=1)
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post(f"/admin/tools/software/{tool_id}/mark-reviewed", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/admin/tools/software"

    lib = Library(os.environ["LINKLIB_DB"])
    assert lib.get_tool(tool_id)["needs_review"] == 0
    lib.close()


def test_communities_admin_list_filter_still_works(env):
    """Communities' own ?filter=needs_review already existed before this
    amendment — a light regression check that it (and the same
    empty-state-fallback fix) still behaves correctly, not new coverage of
    a from-scratch feature."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    c1 = lib.add_community("CFO Alliance", "https://cfoalliance.example", "demo", "Free", ["General"], approved=1)
    c2 = lib.add_community("The F Suite", "https://thefsuite.example", "demo", "Free", ["General"], approved=1)
    lib.upsert_community_profile(c1, ideal_member="x", needs_review=1)
    lib.upsert_community_profile(c2, ideal_member="y", needs_review=0)
    lib.close()

    client = _client(env)
    _login(client)
    r = client.get("/admin/tools/communities?filter=needs_review")
    assert "CFO Alliance" in r.text
    assert "The F Suite" not in r.text


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
