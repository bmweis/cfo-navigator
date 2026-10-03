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


def test_depth_and_sources_are_two_dropdowns_with_panels_in_flow(env):
    appmod, _ = env
    html = _admin_html(appmod)
    block = html.split('id="ask-dd-top"', 1)[1].split('class="ask-action-row"', 1)[0]
    # Two buttons in one row, Depth first, each opening its own panel.
    assert block.count('class="ask-dd-btn"') == 2
    assert block.index('data-dd="depth"') < block.index('data-dd="sources"')
    assert block.count('class="ask-dd-panel"') == 2
    assert block.count(" hidden>") == 2
    # Panels sit in page flow: never absolutely positioned, so they cannot
    # cover the question box, the Ask button or the follow-up input.
    css = re.search(r"\.ask-dd-panel\{[^}]*\}", html).group(0)
    assert "position" not in css
    assert "ask-controls" not in html and "fu-pop" not in html


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
    # No persistent subtext or badge left in the controls block. Delimited by
    # the ask-action-row's own class (not a hardcoded whitespace gap between
    # the two blocks) since PR 17 nested both inside the FP&A Buddy page's
    # two-column intro grid, adding wrapper divs between them.
    controls = html.split('id="ask-dd-top"', 1)[1].split('class="ask-action-row"', 1)[0]
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
    assert "document.querySelectorAll('.fpa-intro-area-controls .ask-tag[data-source].active')" in html
    assert "document.querySelectorAll('.ask-tag.active')" not in html
