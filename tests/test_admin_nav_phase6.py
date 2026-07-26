"""Phase 6 of the Exa integration: reorganize the /admin hub's section
groupings. FP&A Buddy's three admin pages (How it works, report, feedback)
consolidate into their own "FP&A Buddy" section (previously split between
System and the old "Features" catch-all); Sail, Don't Row settings move into
CFO Toolbox; Features is removed entirely once it's empty.

Pure reorganization — no route changes, no new functionality. See
tests/test_admin_how_buddy_works.py for the "How FP&A Buddy works" page's
own content tests, and tests/test_game_settings.py for /admin/game-settings'.
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


def _admin_client(appmod):
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app, raise_server_exceptions=True)
    c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    return c


def test_features_section_no_longer_exists(env):
    group_names = [gname for gname, _, _ in env._ADMIN_GROUPS]
    assert "Features" not in group_names


def test_fpa_buddy_is_its_own_section_with_all_expected_pages(env):
    # 4th entry (/admin/exa-settings) added in Phase 7 — this test only
    # pins the original Phase 6 moves; Phase 7's own tests cover the toggle
    # page itself (tests/test_admin_exa_settings.py).
    fpa_groups = [items for gname, _, items in env._ADMIN_GROUPS if gname == "FP&A Buddy"]
    assert len(fpa_groups) == 1
    hrefs = [href for href, _, _ in fpa_groups[0]]
    assert hrefs == [
        "/admin/system/how-fpa-buddy-works",
        "/admin/ask-report",
        "/admin/ask-feedback",
        "/admin/exa-settings",
    ]


def test_sail_dont_row_moved_into_cfo_toolbox(env):
    toolbox_groups = [items for gname, _, items in env._ADMIN_GROUPS if gname == "CFO Toolbox"]
    assert len(toolbox_groups) == 1
    hrefs = [href for href, _, _ in toolbox_groups[0]]
    assert "/admin/game-settings" in hrefs
    # Wasn't duplicated — it should appear in CFO Toolbox and nowhere else.
    for gname, _, items in env._ADMIN_GROUPS:
        if gname != "CFO Toolbox":
            assert "/admin/game-settings" not in [href for href, _, _ in items]


def test_no_route_appears_in_two_sections(env):
    """None of the four moved pages (or anything else) got duplicated across
    sections during the reorg — every href in _ADMIN_GROUPS is unique."""
    all_hrefs = [href for _, _, items in env._ADMIN_GROUPS for href, _, _ in items]
    assert len(all_hrefs) == len(set(all_hrefs))


def test_admin_hub_renders_new_section_and_drops_features(env):
    c = _admin_client(env)
    resp = c.get("/admin")
    assert resp.status_code == 200
    body = resp.text
    assert "FP&amp;A Buddy" in body
    assert ">Features<" not in body


def test_all_four_moved_routes_still_resolve_at_the_same_urls(env):
    """The reorg only changes section placement — none of the four pages'
    own routes or content should have moved."""
    c = _admin_client(env)
    for path in ("/admin/system/how-fpa-buddy-works", "/admin/ask-report",
                 "/admin/ask-feedback", "/admin/game-settings"):
        resp = c.get(path)
        assert resp.status_code == 200, path
