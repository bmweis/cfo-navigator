"""/admin/library layout fixes: the top-row height match and the Manage feeds box.

The height fix is CSS-only and its real verification was a live browser
measurement (flow-diagram card 241px vs. seafoam callout 263px before, both
241px after — the 22px delta being the diagram card's own margin-bottom
becoming phantom space inside a stretch-aligned grid). What's asserted here is
the rule that produces it, so a later edit to this page's <style> block can't
quietly drop it without a test noticing.
"""
import os
import pathlib
import shutil
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

REPO_OPML = str(pathlib.Path(__file__).resolve().parents[1] / "preferred_sites.opml")


@pytest.fixture
def env(monkeypatch, tmp_path):
    opml = tmp_path / "sites.opml"
    shutil.copy(REPO_OPML, opml)
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_SITES_OPML", str(opml))
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib
    import webapp.app as appmod
    importlib.reload(appmod)
    yield appmod
    if os.path.exists(db):
        os.remove(db)


def _admin_client(appmod):
    from fastapi.testclient import TestClient
    client = TestClient(appmod.app, raise_server_exceptions=True)
    client.post("/login", data={"username": "admin", "password": "adminpass"},
                follow_redirects=False)
    return client


def _library_html(appmod):
    with _admin_client(appmod) as client:
        return client.get("/admin/library").text


def test_top_row_last_cell_offsets_the_diagrams_phantom_margin(env):
    """Without this rule the seafoam callout (height:100%) stretches into the
    22px margin the flow-diagram card carries, rendering taller than the
    diagram beside it."""
    html = _library_html(env)
    assert ".lib-top-row>div:last-child{margin-bottom:22px;}" in html


def test_the_margin_correction_is_dropped_on_the_stacked_mobile_layout(env):
    """Stacked, there's no shared row height to match, so carrying the desktop
    correction down would just add a stray gap."""
    html = _library_html(env)
    assert ".lib-top-row>div:last-child{margin-top:22px;margin-bottom:0;}" in html


def test_manage_feeds_box_links_to_the_feed_admin_page(env):
    html = _library_html(env)
    assert 'href="/admin/library/feeds"' in html
    assert "Manage feeds" in html


def test_manage_feeds_box_sits_in_the_saving_articles_section(env):
    """Placement is deliberate (Brian's call), so pin it: the box belongs below
    the two capture-path accordions, not in one of the three tool sections."""
    html = _library_html(env)
    share_sheet = html.index("Share-Sheet shortcut")
    manage_feeds = html.index('href="/admin/library/feeds"')
    existing_mgmt = html.index("Existing archive management")
    assert share_sheet < manage_feeds < existing_mgmt


def test_manage_feeds_box_reuses_the_page_link_card_style(env):
    """It should be the same _lib_card component as Tag cleanup and the rest,
    not a bespoke box — same surface, border, radius, and trailing arrow."""
    html = _library_html(env)
    card_start = html.index('href="/admin/library/feeds"')
    card = html[card_start - 200:card_start + 700]
    assert "border-radius:14px" in card
    assert "&rarr;" in card


def test_manage_feeds_box_is_admin_only(env):
    from fastapi.testclient import TestClient
    anon = TestClient(env.app)
    resp = anon.get("/admin/library", follow_redirects=False)
    assert resp.status_code in (302, 303, 307)
