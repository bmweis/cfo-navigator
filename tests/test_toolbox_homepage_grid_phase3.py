"""Phase 3: CFO Toolbox 2x2 tile grid (Software, Resources, Communities,
FP&A Buddy) on both /tools and a new homepage teaser section, plus an
admin-only, seafoam "Reader access" card on /tools.

Covers the acceptance criteria: the 4-tile grid replaces the old 3-card
/tools layout, the homepage teaser mirrors it (minus the Reader card, minus
the now-redundant standalone "CFO Toolbox" homepage card), and the Reader
card is genuinely absent from the response HTML for a non-admin visitor —
not just CSS-hidden — while never appearing on the homepage teaser
regardless of auth state (it has its own, separate, always-present sidebar
card there instead — see homepage()).

PR 16 (2026-09) moved this card out of the Toolbox 2x2 grid, where it used
to be an orphaned 5th tile, into its own standalone card below the grid,
sharing markup with the homepage's identical sidebar card via
`_reader_access_card_html()` — see that function's own docstring. It no
longer opens in a new tab (the homepage's own card never did either, and
the two are now required to read identically)."""
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


# The admin-only Reader card was labelled "Library" and pointed at
# /admin/library — the admin management page, not the reading surface it
# promised. PR 9 (2026-09) renamed it to "Reader" and repointed it at /read.
# PR 16 (2026-09) pulled it out of the tile grid into its own standalone
# card and renamed it "Reader access" (matching the homepage's own card
# verbatim — see _reader_access_card_html()). Both were confirmed in
# source before being changed.
def test_tools_landing_anonymous_has_no_trace_of_admin_tile(env):
    html = _client(env).get("/tools").text
    assert 'href="/read"' not in html
    assert "Reader access" not in html


def test_tools_landing_member_has_no_trace_of_admin_tile(env):
    # A signed-in non-admin member should be treated the same as anonymous.
    html = _member_client(env).get("/tools").text
    assert 'href="/read"' not in html
    assert "Reader access" not in html


def test_tools_landing_admin_sees_reader_access_card(env):
    html = _admin_client(env).get("/tools").text
    assert 'href="/read"' in html
    assert "Reader access" in html
    assert "var(--seafoam)" in html
    # Never the retired admin page it used to point at.
    assert "/admin/library" not in html
    # Below the tile grid, not a 5th tile inside it (PR 16).
    grid_end = html.index('<div class="toolbox-grid">')
    grid_end = html.index("</div>", grid_end)
    reader_start = html.index("Reader access")
    assert reader_start > grid_end


def test_reader_access_card_matches_homepage_verbatim(env):
    """PR 16: both cards share _reader_access_card_html() — same copy, same
    markup, no target="_blank" (unlike the old /tools-only 5th tile, which
    opened in a new tab and had different, less accurate copy)."""
    import re
    tools_html = _admin_client(env).get("/tools").text
    home_html = _admin_client(env).get("/").text
    pattern = re.compile(r'<a href="/read".*?</a>', re.S)
    tools_card = pattern.search(tools_html).group(0)
    home_card = pattern.search(home_html).group(0)
    assert tools_card == home_card
    assert 'target="_blank"' not in tools_card


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
    assert ">Reader<" not in html
