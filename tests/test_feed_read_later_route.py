"""Webapp-level coverage for /feed/read-later and /feed/save permissions.

Save-for-later is a personal bookmark: any signed-in member (not just admin)
should be able to use it, and it must be fully scoped to their own account —
never visible to or affected by another user's saves. Saving straight into
the shared Archive (/feed/save) stays admin-only.
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
    lib.create_user("member2", "supersecret", role="user")
    lib.close()
    yield appmod, db
    if os.path.exists(db):
        os.remove(db)


def _client(appmod):
    from fastapi.testclient import TestClient
    return TestClient(appmod.app, raise_server_exceptions=True)


def _login(appmod, username, password):
    c = _client(appmod)
    c.post("/login", data={"username": username, "password": password}, follow_redirects=False)
    return c


def test_anonymous_gets_401(env):
    appmod, _ = env
    c = _client(appmod)
    r = c.post("/feed/read-later", data={"url": "https://ex.com/a"})
    assert r.status_code == 401


def test_a_regular_member_can_use_read_later(env):
    """This is the permission the ticket asks for: not admin-only."""
    appmod, _ = env
    c = _login(appmod, "member1", "supersecret")
    r = c.post("/feed/read-later", data={"url": "https://ex.com/a", "title": "A"})
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_two_members_read_later_lists_are_isolated(env):
    appmod, db = env
    c1 = _login(appmod, "member1", "supersecret")
    c2 = _login(appmod, "member2", "supersecret")

    c1.post("/feed/read-later", data={"url": "https://ex.com/a", "title": "A"})
    c2.post("/feed/read-later", data={"url": "https://ex.com/b", "title": "B"})

    from linklib.db import Library
    lib = Library(db)
    try:
        u1 = lib.get_user("member1")["id"]
        u2 = lib.get_user("member2")["id"]
        assert lib.read_later_urls(u1) == {"https://ex.com/a"}
        assert lib.read_later_urls(u2) == {"https://ex.com/b"}
    finally:
        lib.close()

    # member1 removing their own save never touches member2's list.
    c1.post("/feed/read-later", data={"url": "https://ex.com/a", "action": "remove"})
    lib = Library(db)
    try:
        assert lib.read_later_urls(u1) == set()
        assert lib.read_later_urls(u2) == {"https://ex.com/b"}
    finally:
        lib.close()


def test_feed_page_read_later_view_only_shows_own_saves(env):
    appmod, db = env
    c1 = _login(appmod, "member1", "supersecret")
    c2 = _login(appmod, "member2", "supersecret")
    c1.post("/feed/read-later", data={"url": "https://ex.com/a", "title": "Alpha Piece"})
    c2.post("/feed/read-later", data={"url": "https://ex.com/b", "title": "Beta Piece"})

    html1 = c1.get("/feed?rl=1").text
    assert "Alpha Piece" in html1
    assert "Beta Piece" not in html1

    html2 = c2.get("/feed?rl=1").text
    assert "Beta Piece" in html2
    assert "Alpha Piece" not in html2


def test_feed_save_to_archive_stays_admin_only(env):
    """/feed/save writes straight into the shared Archive — unlike
    read-later, a regular member must never be able to trigger it."""
    appmod, _ = env
    c = _login(appmod, "member1", "supersecret")
    r = c.post("/feed/save", data={"url": "https://ex.com/a"})
    assert r.status_code == 401
