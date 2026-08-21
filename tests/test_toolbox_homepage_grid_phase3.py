"""Phase 3: CFO Toolbox 2x2 tile grid (Software, Resources, Communities,
FP&A Buddy) on both /tools and a new homepage teaser section, plus a 5th,
admin-only, seafoam-bordered tile on /tools linking to /admin/library.

Covers the acceptance criteria: the 4-tile grid replaces the old 3-card
/tools layout, the homepage teaser mirrors it (minus the 5th tile, minus the
now-redundant standalone "CFO Toolbox" homepage card), and the 5th tile is
genuinely absent from the response HTML for a non-admin visitor — not just
CSS-hidden — while never appearing on the homepage regardless of auth state.
"""
import pathlib
import sys
import tempfile, os

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
    from linklib.db import Library
    lib = Library(db)
    lib.create_user("member1", "supersecret", role="user")
    lib.close()
    yield appmod
    if os.path.exists(db):
        os.remove(db)


def _client(appmod):
    from fastapi.testclient import TestClient
    return TestClient(appmod.app, raise_server_exceptions=True)


def _member_client(appmod):
    c = _client(appmod)
    c.post("/login", data={"username": "member1", "password": "supersecret"}, follow_redirects=False)
    return c


def _admin_client(appmod):
    c = _client(appmod)
    c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    return c


def test_tools_landing_has_4_tile_grid_with_fpa_buddy(env):
    html = _client(env).get("/tools").text
    assert "toolbox-grid" in html
    assert 'href="/tools/software"' in html
    assert 'href="/tools/resources"' in html
    assert 'href="/tools/communities"' in html
    assert 'href="/tools/fpa-buddy"' in html
    assert ">FP&amp;A Buddy<" in html
    # Old 3-card layout is gone.
    assert "toolbox-cards" not in html
    assert "still being built out" not in html


def test_tools_landing_anonymous_has_no_trace_of_admin_tile(env):
    html = _client(env).get("/tools").text
    assert "/admin/library" not in html
    assert ">Library<" not in html


def test_tools_landing_member_has_no_trace_of_admin_tile(env):
    # A signed-in non-admin member should be treated the same as anonymous.
    html = _member_client(env).get("/tools").text
    assert "/admin/library" not in html
    assert ">Library<" not in html


def test_tools_landing_admin_sees_5th_seafoam_bordered_tile(env):
    html = _admin_client(env).get("/tools").text
    assert 'href="/admin/library"' in html
    assert ">Library<" in html
    assert "var(--seafoam)" in html


def test_homepage_has_toolbox_teaser_section(env):
    html = _client(env).get("/").text
    assert "Everything in the toolbox" in html
    assert ">CFO Toolbox<" in html
    assert "See the full toolbox" in html
    # All four public tiles' one-liners show up as mini-tiles.
    assert "The software high-growth finance teams actually use." in html
    assert "The benchmarking sources I actually rely on." in html
    assert "CFO and finance communities worth joining." in html
    assert "Ask a real FP&amp;A question, get a sourced answer." in html


def test_homepage_old_cfo_toolbox_card_is_gone(env):
    # The old standalone top-row "CFO Toolbox" card (with its own copy) is
    # gone — replaced first by a Phase 3 teaser section and then, in the
    # Homepage Restructure phase, by the sidebar Toolbox panel.
    html = _client(env).get("/").text
    assert "A curated directory of the software high-growth" not in html


def test_homepage_teaser_never_shows_admin_tile_even_for_admin(env):
    html = _admin_client(env).get("/").text
    assert "/admin/library" not in html
    assert ">Library<" not in html
