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

def test_edit_submit_ignores_stray_needs_review_form_field(env):
    """2026-08 Review-status consolidation: the checkbox is gone from this
    form (the whole-record signoff is now the shared pill+action at the top
    of the edit page, an immediate one-click route, not tied to Save) — a
    stray `needs_review` form field is simply ignored by the submit route
    now. A tool with no prior manual state and no fresh draft this submit
    stays at whatever its current DB value already was."""
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
    assert lib.get_tool(tool_id)["needs_review"] == 0
    lib.close()


def test_edit_submit_persists_existing_needs_review_without_fresh_draft(env):
    """Manual state now persists by carrying the CURRENTLY-persisted DB
    value forward on every save (there's no checkbox to reflect it back
    any more) — a plain resave with no fresh draft this submit can never
    silently clear a manually-set flag; only the dedicated "Mark reviewed"
    action can."""
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
    assert lib.get_tool(tool_id)["needs_review"] == 1
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


def test_edit_submit_fresh_draft_overrides_currently_reviewed_state(env):
    """A currently-reviewed tool (needs_review=0) that gets a fresh draft
    this same submit is forced back to 1 regardless — a fresh draft always
    wins over whatever the prior persisted state was."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.mark_tool_reviewed(tool_id)
    slug = lib.get_tool(tool_id)["slug"]
    lib.close()

    client = _client(env)
    _login(client)
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

def test_edit_page_shows_review_status_pill_and_mark_reviewed_at_top(env):
    """2026-08 consolidation: the checkbox is gone — the whole-record
    signoff is now the shared Review-status pill + one-click action,
    positioned at the TOP of the edit page (before the main form), not a
    checkbox tied to Save at the bottom."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    slug = lib.get_tool(tool_id)["slug"]
    lib.close()

    client = _client(env)
    _login(client)
    r = client.get(f"/tools/software/{slug}/edit")
    assert 'name="needs_review"' not in r.text  # the checkbox is gone
    assert "Needs review" in r.text  # the coral pill text
    assert f'action="/admin/tools/software/{tool_id}/mark-reviewed"' in r.text
    assert "Mark reviewed" in r.text
    assert f'name="redirect_to" value="/tools/software/{slug}/edit"' in r.text
    # Positioned before the main edit form, not after it.
    assert r.text.index("Mark reviewed") < r.text.index('id="tool-edit-form"')


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


# -- "Flag for review" quick-toggle (2026-08 follow-up) --------------------------
# The mirror action of "Mark reviewed": lets an admin flag a profile as
# needing review straight from the admin list, without opening the edit
# form — Brian's ask for a way to flag a profile "in the moment" while just
# looking at it. Admin-gated exactly like everything else in this system.

def test_tool_flag_for_review_route_sets_flag(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "d", "https://runway.com", ["FP&A"], approved=1)
    lib.mark_tool_reviewed(tool_id)
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post(f"/admin/tools/software/{tool_id}/flag-for-review", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/admin/tools/software"

    lib = Library(os.environ["LINKLIB_DB"])
    assert lib.get_tool(tool_id)["needs_review"] == 1
    lib.close()


def test_tool_flag_for_review_does_not_touch_narrative_review_log(env):
    """Flagging isn't a confirmation — narrative_review_log only ever
    records the opposite action (an explicit "I reviewed this")."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    lib.create_user("brian", "pw", role="admin", name="Brian")
    tool_id = lib.add_tool("Runway", "d", "https://runway.com", ["FP&A"], approved=1)
    lib.close()

    client = _client(env)
    _login(client, "brian", "pw")
    client.post(f"/admin/tools/software/{tool_id}/mark-reviewed", follow_redirects=False)
    client.post(f"/admin/tools/software/{tool_id}/flag-for-review", follow_redirects=False)

    lib = Library(os.environ["LINKLIB_DB"])
    assert lib.get_tool(tool_id)["needs_review"] == 1
    # The stamp from the earlier Mark reviewed click is untouched/still live.
    review = lib.get_latest_narrative_review("tool", "profile", tool_id)
    assert review is not None
    assert review["admin_username"] == "brian"
    lib.close()


def test_tool_flag_for_review_requires_auth(env):
    client = _client(env)
    assert client.post("/admin/tools/software/1/flag-for-review").status_code == 401


def test_tool_flag_for_review_404s_for_missing_tool(env):
    client = _client(env)
    _login(client)
    assert client.post("/admin/tools/software/999999/flag-for-review").status_code == 404


def test_tool_flag_for_review_honors_edit_page_redirect_to(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "d", "https://runway.com", ["FP&A"], approved=1)
    slug = lib.get_tool(tool_id)["slug"]
    lib.mark_tool_reviewed(tool_id)
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post(f"/admin/tools/software/{tool_id}/flag-for-review",
                     data={"redirect_to": f"/tools/software/{slug}/edit"}, follow_redirects=False)
    assert r.headers["location"] == f"/tools/software/{slug}/edit"


def test_tool_flag_for_review_rejects_unknown_redirect_to(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "d", "https://runway.com", ["FP&A"], approved=1)
    lib.mark_tool_reviewed(tool_id)
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post(f"/admin/tools/software/{tool_id}/flag-for-review",
                     data={"redirect_to": "https://evil.example.com"}, follow_redirects=False)
    assert r.headers["location"] == "/admin/tools/software"


def test_software_admin_list_never_shows_flag_for_review(env):
    """2026-08 Review-status consolidation, item 1: "Flag for review" is
    deliberately NOT offered on either admin list, in either state — it
    only ever belongs on the profile VIEW page and the edit page, never a
    list of many rows. "Mark reviewed" is unaffected and still shows for a
    flagged row."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    t1 = lib.add_tool("Runway", "d", "https://runway.com", ["FP&A"], approved=1)  # needs_review=1
    t2 = lib.add_tool("Abacum", "d", "https://abacum.io", ["FP&A"], approved=1)
    lib.mark_tool_reviewed(t2)
    lib.close()

    client = _client(env)
    _login(client)
    r = client.get("/admin/tools/software")
    assert "flag-for-review" not in r.text
    assert f'action="/admin/tools/software/{t1}/mark-reviewed"' in r.text


def test_community_flag_for_review_route_sets_flag(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    community_id = lib.add_community("CFO Alliance", "https://cfoalliance.example", "demo", "Free", ["General"], approved=1)
    lib.upsert_community_profile(community_id, ideal_member="x", needs_review=0)
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post(f"/admin/tools/communities/{community_id}/flag-for-review", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/admin/tools/communities"

    lib = Library(os.environ["LINKLIB_DB"])
    assert lib.get_community_profile(community_id)["needs_review"] == 1
    lib.close()


def test_community_flag_for_review_is_noop_when_no_profile_row_exists(env):
    """A community with no profile draft yet has nothing to flag — the
    route must not error, and must not fabricate a profile row."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    community_id = lib.add_community("CFO Alliance", "https://cfoalliance.example", "demo", "Free", ["General"], approved=1)
    assert lib.get_community_profile(community_id) is None
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post(f"/admin/tools/communities/{community_id}/flag-for-review", follow_redirects=False)
    assert r.status_code == 303

    lib = Library(os.environ["LINKLIB_DB"])
    assert lib.get_community_profile(community_id) is None  # still no row fabricated
    lib.close()


def test_community_flag_for_review_requires_auth(env):
    client = _client(env)
    assert client.post("/admin/tools/communities/1/flag-for-review").status_code == 401


def test_communities_admin_list_never_shows_flag_for_review(env):
    """Same item-1 rule as the Software list — see
    test_software_admin_list_never_shows_flag_for_review."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    c1 = lib.add_community("CFO Alliance", "https://cfoalliance.example", "demo", "Free", ["General"], approved=1)
    c2 = lib.add_community("The F Suite", "https://thefsuite.example", "demo", "Free", ["General"], approved=1)
    lib.upsert_community_profile(c1, ideal_member="x", needs_review=1)
    lib.upsert_community_profile(c2, ideal_member="y", needs_review=0)
    lib.close()

    client = _client(env)
    _login(client)
    r = client.get("/admin/tools/communities")
    assert "flag-for-review" not in r.text
    assert f'action="/admin/tools/communities/{c1}/mark-reviewed"' in r.text


# -- Review status on the profile VIEW page (item 2/3) --------------------------

def test_software_view_page_shows_review_status_pill_for_admin(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "d", "https://runway.com", ["FP&A"], approved=1)  # needs_review=1
    lib.update_tool(tool_id, "Runway", "d", "https://runway.com", ["FP&A"], summary="s",
                     description_needs_verification=1)
    slug = lib.get_tool(tool_id)["slug"]
    lib.close()

    client = _client(env)
    _login(client)
    r = client.get(f"/tools/software/{slug}")
    assert "Needs review" in r.text
    assert "(1/3)" in r.text  # 1 of the 3 per-field flags is set
    assert f'action="/admin/tools/software/{tool_id}/mark-reviewed"' in r.text
    assert f'name="redirect_to" value="/tools/software/{slug}"' in r.text


def test_software_view_page_hides_review_status_for_anonymous_visitor(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "d", "https://runway.com", ["FP&A"], approved=1)
    slug = lib.get_tool(tool_id)["slug"]
    lib.close()

    client = _client(env)  # no login
    r = client.get(f"/tools/software/{slug}")
    assert "Needs review" not in r.text
    assert "flag-for-review" not in r.text
    assert "mark-reviewed" not in r.text


def test_software_view_page_shows_reviewed_pill_when_not_flagged(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "d", "https://runway.com", ["FP&A"], approved=1)
    lib.mark_tool_reviewed(tool_id)
    slug = lib.get_tool(tool_id)["slug"]
    lib.close()

    client = _client(env)
    _login(client)
    r = client.get(f"/tools/software/{slug}")
    assert "Reviewed" in r.text
    assert f'action="/admin/tools/software/{tool_id}/flag-for-review"' in r.text


def test_community_view_page_shows_review_status_pill_for_admin(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    community_id = lib.add_community("CFO Alliance", "https://cfoalliance.example", "demo", "Free", ["General"], approved=1)
    lib.upsert_community_profile(community_id, ideal_member="x", needs_review=1,
                                  confidence={"ideal_member": 0, "anti_fit": 0})
    slug = lib.get_community(community_id)["slug"]
    lib.close()

    client = _client(env)
    _login(client)
    r = client.get(f"/tools/communities/{slug}")
    assert "Needs review" in r.text
    assert "(2/12)" in r.text  # 2 of the 12 confidence-tracked fields reported low confidence
    assert f'action="/admin/tools/communities/{community_id}/mark-reviewed"' in r.text


def test_community_view_page_hides_review_status_when_no_profile_drafted(env):
    """A community with no profile draft yet has nothing to review or
    flag — the pill/action block is simply absent, not shown empty."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    community_id = lib.add_community("CFO Alliance", "https://cfoalliance.example", "demo", "Free", ["General"], approved=1)
    slug = lib.get_community(community_id)["slug"]
    lib.close()

    client = _client(env)
    _login(client)
    r = client.get(f"/tools/communities/{slug}")
    assert "Needs review" not in r.text
    assert "flag-for-review" not in r.text


def test_community_view_page_hides_review_status_for_anonymous_visitor(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    community_id = lib.add_community("CFO Alliance", "https://cfoalliance.example", "demo", "Free", ["General"], approved=1)
    lib.upsert_community_profile(community_id, ideal_member="x", needs_review=1)
    slug = lib.get_community(community_id)["slug"]
    lib.close()

    client = _client(env)  # no login
    r = client.get(f"/tools/communities/{slug}")
    assert "Needs review" not in r.text
    assert "mark-reviewed" not in r.text


# -- Filter matches the pill's own signal exactly (item 4) -----------------------

def test_software_filter_and_pill_agree(env):
    """The admin-list filter must key off exactly the same signal the pill
    displays — no separate/stale logic. A tool shown as "Needs review" by
    the pill is exactly the set the filter returns, and vice versa."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    lib.add_tool("Runway", "d", "https://runway.com", ["FP&A"], approved=1)  # needs_review=1
    t2 = lib.add_tool("Abacum", "d", "https://abacum.io", ["FP&A"], approved=1)
    lib.mark_tool_reviewed(t2)
    lib.close()

    client = _client(env)
    _login(client)
    full = client.get("/admin/tools/software").text
    filtered = client.get("/admin/tools/software?filter=needs_review").text
    # Runway shows a coral pill on the unfiltered page and survives filtering.
    assert "Runway" in full and "Runway" in filtered
    # Abacum shows a green pill on the unfiltered page and is excluded by the filter.
    assert "Abacum" in full and "Abacum" not in filtered


def test_communities_filter_and_pill_agree(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    c1 = lib.add_community("CFO Alliance", "https://cfoalliance.example", "demo", "Free", ["General"], approved=1)
    c2 = lib.add_community("The F Suite", "https://thefsuite.example", "demo", "Free", ["General"], approved=1)
    lib.upsert_community_profile(c1, ideal_member="x", needs_review=1)
    lib.upsert_community_profile(c2, ideal_member="y", needs_review=0)
    lib.close()

    client = _client(env)
    _login(client)
    full = client.get("/admin/tools/communities").text
    filtered = client.get("/admin/tools/communities?filter=needs_review").text
    assert "CFO Alliance" in full and "CFO Alliance" in filtered
    assert "The F Suite" in full and "The F Suite" not in filtered


# -- Shared pill component (unit-level) ------------------------------------------

def test_review_status_pill_html_reviewed_state(env):
    html = env._review_status_pill_html(True)
    assert "Reviewed" in html
    assert "Needs review" not in html


def test_review_status_pill_html_needs_review_state_with_breakdown(env):
    html = env._review_status_pill_html(False, (2, 3))
    assert "Needs review" in html
    assert "(2/3)" in html


def test_review_status_pill_html_needs_review_state_without_breakdown(env):
    html = env._review_status_pill_html(False)
    assert "Needs review</span>" in html  # no trailing " (n/total)" fraction


def test_review_status_action_html_polarity():
    """The action must be the MIRROR of the pill's own state — Flag for
    review when currently reviewed, Mark reviewed when currently needs
    review. (This exact polarity bug was caught and fixed while building
    this component — this test pins it down.)"""
    from webapp.app import _review_status_action_html
    reviewed_action = _review_status_action_html(True, "/mark", "/flag", "/redirect")
    assert "Flag for review" in reviewed_action
    assert "Mark reviewed" not in reviewed_action

    needs_review_action = _review_status_action_html(False, "/mark", "/flag", "/redirect")
    assert "Mark reviewed" in needs_review_action
    assert "Flag for review" not in needs_review_action


# -- No public gating: profile page renders identically regardless of flag ------

def test_public_profile_page_unaffected_by_needs_review_flag(env):
    """Admin-only bookkeeping, per explicit direction — unlike Communities'
    needs_review, this must not hide/change anything on the PUBLIC
    (unauthenticated) profile page (the three per-field flags already do
    that job for tools). The signed-in admin view is deliberately
    different now (it shows the Review-status pill) — see
    test_software_view_page_shows_review_status_pill_for_admin above."""
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
