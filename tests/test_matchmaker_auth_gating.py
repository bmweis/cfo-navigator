"""Matchmaker sign-in gating + login/logout redirect defaults.

Covers:
- Software/Community Matchmaker links show a sign-in prompt (with a `next`
  pointing at the matchmaker) when signed out, and the normal link when
  signed in — the matchmaker routes themselves stay public either way.
- `/login`'s `next` param wins after a successful login regardless of role.
- With no `next`, the default is role-based: admin -> /admin, everyone
  else -> homepage (never /library).
- `/logout` always lands on the homepage.
- `next` is validated against open redirects.
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


def test_software_matchmaker_link_gated_signed_out(env):
    c = _client(env)
    html = c.get("/tools/software").text
    assert 'href="/login?next=%2Ftools%2Fsoftware%2Ffind"' in html
    assert "Sign in for access to Software matchmaker" in html
    assert 'href="/tools/software/find"' not in html


def test_software_matchmaker_link_normal_signed_in(env):
    c = _member_client(env)
    html = c.get("/tools/software").text
    assert 'href="/tools/software/find"' in html
    assert "Sign in for access" not in html


def test_community_matchmaker_link_gated_signed_out(env):
    c = _client(env)
    html = c.get("/tools/communities").text
    assert 'href="/login?next=%2Ftools%2Fcommunities%2Ffind"' in html
    assert "Sign in for access to Community matchmaker" in html
    assert 'href="/tools/communities/find"' not in html


def test_community_matchmaker_link_normal_signed_in(env):
    c = _member_client(env)
    html = c.get("/tools/communities").text
    assert 'href="/tools/communities/find"' in html
    assert "Sign in for access" not in html


def test_matchmaker_routes_still_public(env):
    # Gating is a link-level nudge, not a hard route gate — direct navigation
    # still works signed-out (unchanged Communities/Software matchmaker logic).
    c = _client(env)
    assert c.get("/tools/software/find").status_code == 200
    assert c.get("/tools/communities/find").status_code == 200


def test_login_next_redirects_to_matchmaker_regardless_of_role(env):
    member = _client(env)
    r = member.post("/login", data={"username": "member1", "password": "supersecret",
                                     "next": "/tools/software/find"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/tools/software/find"

    admin = _client(env)
    r = admin.post("/login", data={"username": "admin", "password": "adminpass",
                                    "next": "/tools/communities/find"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/tools/communities/find"


def test_login_default_redirect_by_role(env):
    member = _client(env)
    r = member.post("/login", data={"username": "member1", "password": "supersecret"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"

    admin = _client(env)
    r = admin.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/admin"


@pytest.mark.parametrize("bad_next", [
    "https://evil.com/phish",
    "//evil.com/phish",
    "/\\evil.com/phish",
    "not-a-path",
])
def test_login_rejects_open_redirect_next(env, bad_next):
    c = _client(env)
    r = c.post("/login", data={"username": "member1", "password": "supersecret",
                                "next": bad_next}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"


def test_logout_redirects_home_for_every_role(env):
    for c in (_client(env), _member_client(env), _admin_client(env)):
        r = c.get("/logout", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/"
