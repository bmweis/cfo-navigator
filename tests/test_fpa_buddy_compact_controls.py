"""Phase 4: FP&A Buddy's Sources/Depth controls compact into two columns.

Depth stops being a tall stack of descriptive cards and becomes single-select
buttons built from the same .ask-tag component Sources uses, side by side in an
.ask-controls grid. The per-tier detail (archive count, web-search count, token
estimate) and the "Recommended" badge stop being persistent UI and move into a
hover tooltip; Standard stays the default selection for an admin.

The subtle regression risk this file exists to pin: Depth's buttons now share
.ask-tag AND its .active state with Sources, so any unscoped '.ask-tag.active'
query would sweep the selected depth tier into the sources list posted to /ask.
"""
import os
import pathlib
import re
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
    lib = Library(db)
    lib.create_user("member1", "supersecret", role="user")
    lib.close()
    yield appmod, db
    if os.path.exists(db):
        os.remove(db)


def _client(appmod):
    from fastapi.testclient import TestClient
    return TestClient(appmod.app, raise_server_exceptions=True)


def _admin_html(appmod):
    c = _client(appmod)
    c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    return c.get("/tools/fpa-buddy").text


def _member_html(appmod):
    c = _client(appmod)
    c.post("/login", data={"username": "member1", "password": "supersecret"}, follow_redirects=False)
    return c.get("/tools/fpa-buddy").text


def test_sources_and_depth_render_side_by_side_in_one_grid(env):
    appmod, _ = env
    html = _admin_html(appmod)
    assert 'class="ask-controls"' in html
    # Two columns in the same grid container, Sources first.
    block = html.split('class="ask-controls"', 1)[1].split("</style>", 1)[0]
    assert block.count('class="ask-control"') == 2
    assert block.index("Sources") < block.index("Depth")
    # A real two-column grid, collapsing to one column on narrow viewports.
    assert re.search(r"\.ask-controls\{display:grid;grid-template-columns:1fr 1fr", html)
    assert re.search(r"@media \(max-width:640px\)\{\.ask-controls\{grid-template-columns:1fr", html)


def test_depth_buttons_reuse_the_sources_tag_component(env):
    appmod, _ = env
    html = _admin_html(appmod)
    for tier in ("quick", "standard", "deep"):
        assert re.search(r'class="ask-tag(?: active)?" data-tier="%s"' % tier, html)
    # The old tall-card markup and its CSS are gone entirely.
    assert "ask-tier" not in html
    assert "ask-tiers" not in html


def test_depth_is_single_select_with_standard_default_for_admin(env):
    appmod, _ = env
    html = _admin_html(appmod)
    assert 'role="radiogroup"' in html
    active = re.findall(r'class="ask-tag active" data-tier="(\w+)"', html)
    assert active == ["standard"]
    assert 'data-tier="standard" role="radio" aria-checked="true"' in html
    # selectTier clears every tier before setting one — radio, not toggle.
    assert "document.querySelectorAll('.ask-tag[data-tier]')" in html


def test_anonymous_member_still_defaults_to_quick(env):
    appmod, _ = env
    html = _member_html(appmod)
    active = re.findall(r'class="ask-tag active" data-tier="(\w+)"', html)
    assert active == ["quick"]


def test_tier_detail_and_recommended_move_from_persistent_ui_to_hover(env):
    appmod, _ = env
    html = _admin_html(appmod)
    # No persistent subtext or badge left in the controls block.
    controls = html.split('class="ask-controls"', 1)[1].split("</div>\n\n<div class=\"ask-action-row\"", 1)[0]
    assert "tokens out" not in controls.replace('title="', "\x00").split("\x00")[0]
    assert "ask-tier-badge" not in html
    assert ">Recommended<" not in html
    # ...but still available on hover, per tier.
    assert 'title="4 archive sources &middot; 2 web searches &middot; ~700 tokens out"' in html
    assert ('title="8 archive sources &middot; 4 web searches &middot; ~1,500 tokens out'
            ' &middot; Recommended"') in html
    assert 'title="16 archive sources &middot; 6 web searches &middot; ~2,500 tokens out"' in html


def test_ask_collects_sources_scoped_so_depth_is_not_swept_in(env):
    appmod, _ = env
    html = _admin_html(appmod)
    assert "document.querySelectorAll('.ask-tag[data-source].active')" in html
    assert "document.querySelectorAll('.ask-tag.active')" not in html
