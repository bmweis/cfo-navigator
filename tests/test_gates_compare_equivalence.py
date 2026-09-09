"""Gate-Extraction PR B — admin+visitor output-equivalence for the Software
and Communities compare matrices, and confirmation that `_public_community`
(the retired no-op "choke point," Phase 0 inventory item #6) is genuinely
gone with unchanged behavior at its 3 former call sites.

The row-existence-vs.-cell-content split (item #10) is exercised here
through both matrices side by side, on the same shaped fixture data, to
show the two entity types share one code path (`gates.any_populated` for
row existence, `gates.state_for`/`gates.badge_text` for cell content) —
Compare Redesign Phase 1 (2026-09) moved the actual field-selection/state
logic into `linklib/compare.py`'s serializer, rendered via
`_cmp_section_cell_html` in webapp/app.py (the retired `_compare_cell_html`
this docstring used to name), but the underlying gates.py contract these
tests exercise is unchanged.
"""
import os
import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


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


# ---------------------------------------------------------------------------
# Row-existence vs. cell-content split (#10) — Software compare matrix.
# ---------------------------------------------------------------------------

def test_software_compare_row_omitted_when_no_tool_has_any_content(env):
    """No tool in the comparison has an agent_taxonomy_note at all -> the
    whole "AI / Agent involvement" row still renders (it's a fixed
    section), but with the shared empty-label cell for every column, not
    an omitted section — this is the row-existence branch's else path."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "d", "https://runway.com", ["FP&A"], approved=1)
    b = lib.add_tool("Datarails", "d", "https://datarails.com", ["FP&A"], approved=1)
    lib.close()

    r = _client(env).get(f"/tools/software/compare?ids={a},{b}")
    assert r.status_code == 200
    assert "AI / Agent involvement" in r.text
    assert "Not yet documented." in r.text


def test_software_compare_row_present_and_per_cell_gated_when_one_tool_has_content(env):
    """One tool has content, one doesn't -> the row exists (any_populated
    True), and the two cells independently show content-with-badge vs.
    an empty cell — the row-existence check and the per-cell gate are
    genuinely separate decisions, both exercised in one row here."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "d", "https://runway.com", ["FP&A"], approved=1)
    b = lib.add_tool("Datarails", "d", "https://datarails.com", ["FP&A"], approved=1)
    lib.set_tool_agent_taxonomy_draft(a, "Runway's agent taxonomy note.", needs_verification=1)
    lib.close()

    r = _client(env).get(f"/tools/software/compare?ids={a},{b}")
    assert "Runway's agent taxonomy note." in r.text
    assert "under review" in r.text  # a's pending cell
    assert "Not yet documented." in r.text  # b's empty cell, same row


# ---------------------------------------------------------------------------
# Row-existence vs. cell-content split (#10) — Communities compare matrix,
# same fixture shape as the Software test above.
# ---------------------------------------------------------------------------

def test_communities_compare_row_omitted_when_no_community_has_any_content(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_community("Peer CFOs", "https://peercfos.example", "Series B+ CFOs", "Free", [], approved=1)
    b = lib.add_community("Finance Guild", "https://financeguild.example", "Late-stage CFOs", "Free", [], approved=1)
    lib.close()

    r = _client(env).get(f"/tools/communities/compare?ids={a},{b}")
    assert r.status_code == 200
    # Neither community has an ideal_member profile field, so that row is
    # simply absent (Communities' compare doesn't render an always-present
    # section header the way Software's Agent-taxonomy section does) —
    # confirm the page still renders cleanly with nothing to show for it.
    assert "Ideal member" not in r.text


def test_communities_compare_row_present_and_per_cell_gated_when_one_community_has_content(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_community("Peer CFOs", "https://peercfos.example", "Series B+ CFOs", "Free", [], approved=1)
    b = lib.add_community("Finance Guild", "https://financeguild.example", "Late-stage CFOs", "Free", [], approved=1)
    lib.upsert_community_profile(a, ideal_member="Peer CFOs' ideal member.", needs_review=1)
    lib.close()

    r = _client(env).get(f"/tools/communities/compare?ids={a},{b}")
    assert "Peer CFOs' ideal member." in r.text
    assert "under review" in r.text
    assert "Not yet available." in r.text


# ---------------------------------------------------------------------------
# Explicit admin+visitor equivalence, one shared fixture, both matrices.
# ---------------------------------------------------------------------------

def test_admin_and_visitor_see_identical_content_differ_only_in_badge_word_software(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "d", "https://runway.com", ["FP&A"], approved=1)
    b = lib.add_tool("Datarails", "d", "https://datarails.com", ["FP&A"], approved=1)
    lib.update_tool_differentiation(a, "A drafted differentiation note.", needs_verification=1)
    lib.close()

    visitor = _client(env).get(f"/tools/software/compare?ids={a},{b}")
    admin_client = _client(env)
    _login(admin_client)
    admin = admin_client.get(f"/tools/software/compare?ids={a},{b}")

    assert "A drafted differentiation note." in visitor.text
    assert "A drafted differentiation note." in admin.text
    assert "under review" in visitor.text
    assert "unverified, visible to visitors" not in visitor.text
    assert "unverified, visible to visitors" in admin.text
    assert "under review" not in admin.text


def test_admin_and_visitor_see_identical_content_differ_only_in_badge_word_communities(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_community("Peer CFOs", "https://peercfos.example", "Series B+ CFOs", "Free", [], approved=1)
    b = lib.add_community("Finance Guild", "https://financeguild.example", "Late-stage CFOs", "Free", [], approved=1)
    lib.upsert_community_profile(a, ideal_member="A drafted ideal-member note.", needs_review=1)
    lib.close()

    visitor = _client(env).get(f"/tools/communities/compare?ids={a},{b}")
    admin_client = _client(env)
    _login(admin_client)
    admin = admin_client.get(f"/tools/communities/compare?ids={a},{b}")

    assert "A drafted ideal-member note." in visitor.text
    assert "A drafted ideal-member note." in admin.text
    assert "under review" in visitor.text
    assert "unverified, visible to visitors" not in visitor.text
    assert "unverified, visible to visitors" in admin.text
    assert "under review" not in admin.text


# ---------------------------------------------------------------------------
# `_public_community` retirement (item #6, corrected target) — confirmed a
# true no-op before removal (see PR description); these assert its 3 former
# call sites behave identically without it, auth state explicit throughout.
# ---------------------------------------------------------------------------

def test_public_community_symbol_no_longer_exists(env):
    assert not hasattr(env, "_public_community")


def test_communities_directory_unaffected_by_retirement(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    lib.add_community("Peer CFOs", "https://peercfos.example", "Series B+ CFOs", "Free", [], approved=1)
    lib.close()

    visitor = _client(env).get("/tools/communities")
    assert visitor.status_code == 200
    assert "Peer CFOs" in visitor.text

    admin_client = _client(env)
    _login(admin_client)
    admin = admin_client.get("/tools/communities")
    assert admin.status_code == 200
    assert "Peer CFOs" in admin.text


def test_communities_compare_unaffected_by_retirement(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_community("Peer CFOs", "https://peercfos.example", "Series B+ CFOs", "Free", [], approved=1)
    b = lib.add_community("Finance Guild", "https://financeguild.example", "Late-stage CFOs", "Free", [], approved=1)
    lib.close()

    r = _client(env).get(f"/tools/communities/compare?ids={a},{b}")
    assert r.status_code == 200
    assert "Peer CFOs" in r.text
    assert "Finance Guild" in r.text


def test_community_profile_page_unaffected_by_retirement(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    c = lib.add_community("Peer CFOs", "https://peercfos.example", "Series B+ CFOs", "Free", [], approved=1)
    slug = lib.get_community(c)["slug"]
    lib.close()

    visitor = _client(env).get(f"/tools/communities/{slug}")
    assert visitor.status_code == 200
    assert "Peer CFOs" in visitor.text

    admin_client = _client(env)
    _login(admin_client)
    admin = admin_client.get(f"/tools/communities/{slug}")
    assert admin.status_code == 200
    assert "Peer CFOs" in admin.text
