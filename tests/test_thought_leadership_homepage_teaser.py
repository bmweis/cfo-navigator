"""Phase 3 addendum: homepage Thought Leadership teaser section, mirroring
the Toolbox teaser's structure, plus the "Feature on homepage" pin field
that drives its tile selection, and the "Speaking &amp; Events" double-
escaping fix on /thought-leadership.
"""
import pathlib
import sys
import tempfile
import os

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


def _admin_client(appmod):
    c = _client(appmod)
    c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    return c


def test_homepage_has_thought_leadership_teaser_section(env):
    html = _client(env).get("/").text
    assert "THOUGHT LEADERSHIP" in html
    assert "What I write about" in html
    assert "See all Thought Leadership" in html
    assert 'href="/thought-leadership"' in html
    # Exact copy, not rephrased.
    assert "AI in finance&mdash;separating signal from noise, tracking what&rsquo;s changing." in html
    assert ("Frameworks myself and others have built, real opinions, and stories from the "
            "trenches.") in html
    assert ("How to move from scorekeeper to strategic partner: stop reporting what happened, "
            "start shaping what&rsquo;s next.") in html
    assert ("Showing up for the finance community&mdash;hosting my own podcast, speaking on "
            "panels, co-chairing demo days and events.") in html
    # No specific community named.
    assert "The F Suite" not in html


def test_toolbox_teaser_section_unchanged(env):
    html = _client(env).get("/").text
    assert "TOOLBOX" in html
    assert "Everything in the toolbox" in html
    assert "See the full toolbox" in html


def test_add_and_edit_forms_have_feature_on_homepage_checkbox(env):
    c = _admin_client(env)
    add_html = c.get("/admin/thought-leadership/new").text
    assert 'name="featured_home"' in add_html
    assert "Feature on homepage" in add_html

    lib = env._lib()
    try:
        item_id = lib.add_thought_leadership("writing", "A Piece", "https://example.com/piece",
                                              "Forbes", "Jan 2026", "2026-01")
    finally:
        lib.close()
    edit_html = c.get(f"/admin/thought-leadership/{item_id}/edit").text
    assert 'name="featured_home"' in edit_html
    assert "Feature on homepage" in edit_html


def test_featured_home_checkbox_persists_via_add_and_edit_routes(env):
    c = _admin_client(env)
    c.post("/admin/thought-leadership/new", data={
        "type": "writing", "title": "Pinned Piece", "url": "https://example.com/pinned",
        "venue": "Forbes", "date_label": "Jan 2020", "display_order": "",
        "featured_home": "1",
    }, follow_redirects=False)
    lib = env._lib()
    try:
        items = lib.list_thought_leadership(type="writing")
    finally:
        lib.close()
    assert len(items) == 1
    assert items[0]["featured_home"] == 1

    item_id = items[0]["id"]
    # Edit and uncheck the pin.
    c.post(f"/admin/thought-leadership/{item_id}/edit", data={
        "type": "writing", "title": "Pinned Piece", "url": "https://example.com/pinned",
        "venue": "Forbes", "date_label": "Jan 2020", "display_order": "0",
    }, follow_redirects=False)
    lib = env._lib()
    try:
        it = lib.get_thought_leadership(item_id)
    finally:
        lib.close()
    assert it["featured_home"] == 0


def test_homepage_teaser_shows_pinned_entry_first_then_backfills_recent(env):
    lib = env._lib()
    try:
        lib.add_thought_leadership("writing", "Old Unpinned", "https://example.com/old",
                                   "Forbes", "Jan 2020", "2020-01", featured_home=False)
        lib.add_thought_leadership("speaking", "Pinned Older Talk", "https://example.com/pinned",
                                   "The F Suite", "Jun 2020", "2020-06", featured_home=True)
        lib.add_thought_leadership("podcast", "Recent Pod", "https://example.com/pod",
                                   "Cash Flow Show", "Jul 2026", "2026-07", featured_home=False)
        lib.add_thought_leadership("press", "Newest Press", "https://example.com/press",
                                   "TechCrunch", "Aug 2026", "2026-08", featured_home=False)
    finally:
        lib.close()
    html = _client(env).get("/").text
    pinned_idx = html.find("Pinned Older Talk")
    newest_idx = html.find("Newest Press")
    recent_idx = html.find("Recent Pod")
    old_idx = html.find("Old Unpinned")
    assert pinned_idx != -1 and newest_idx != -1 and recent_idx != -1
    # Pinned tile appears despite being the oldest dated entry.
    assert pinned_idx < newest_idx
    # The oldest unpinned entry is bumped by the two more-recent unpinned ones
    # (only 3 tiles total: 1 pinned + 2 backfilled).
    assert old_idx == -1


def test_homepage_teaser_tile_links_directly_to_piece(env):
    lib = env._lib()
    try:
        lib.add_thought_leadership("writing", "Direct Link Piece", "https://example.com/direct",
                                   "Forbes", "Jan 2026", "2026-01", featured_home=True)
    finally:
        lib.close()
    html = _client(env).get("/").text
    assert 'href="https://example.com/direct"' in html


def test_speaking_and_events_renders_without_double_escaping(env):
    html = _client(env).get("/thought-leadership").text
    assert "&amp;amp;" not in html
    assert "Speaking &amp; Events" in html  # correctly single-escaped in the raw HTML
