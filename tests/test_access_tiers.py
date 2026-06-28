"""Three-tier access control: public / member / admin.

Asserts each route lands in the right tier — public reachable signed-out, member
pages redirect signed-out and open for members, admin stays admin-only, and the
in-app reader stays admin-only (resale-safe).
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
    from fastapi.testclient import TestClient
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
    c.post("/login", data={"password": "adminpass"}, follow_redirects=False)
    return c


PUBLIC = ["/", "/about", "/thought-leadership", "/contact", "/library/submit"]
MEMBER = ["/library", "/feed", "/ask", "/tools", "/growth-engine-ratio",
          "/netsuite-mcp", "/finops-ai-hackathon"]


def test_public_pages_open_to_anonymous(env):
    c = _client(env)
    for path in PUBLIC:
        r = c.get(path, follow_redirects=False)
        assert r.status_code == 200, f"{path} -> {r.status_code}"


def test_member_pages_redirect_anonymous(env):
    c = _client(env)
    for path in MEMBER:
        r = c.get(path, follow_redirects=False)
        assert r.status_code == 303 and "/login" in r.headers["location"], f"{path} -> {r.status_code}"


def test_member_can_reach_member_pages(env):
    c = _member_client(env)
    for path in MEMBER:
        r = c.get(path, follow_redirects=False)
        assert r.status_code == 200, f"{path} -> {r.status_code}"


def test_member_blocked_from_admin_and_reader(env):
    c = _member_client(env)
    # Admin pages redirect to login for a non-admin member.
    assert c.get("/admin", follow_redirects=False).status_code == 303
    assert c.get("/admin/users", follow_redirects=False).status_code == 303
    # In-app reader is admin-only (resale-safe) even for members.
    assert c.get("/read?id=1", follow_redirects=False).status_code == 303


def test_admin_reaches_everything(env):
    c = _admin_client(env)
    assert c.get("/admin", follow_redirects=False).status_code == 200
    assert c.get("/library", follow_redirects=False).status_code == 200
    assert c.get("/read?url=https://ex.com/x", follow_redirects=False).status_code == 200


def test_member_api_gating(env):
    anon, member = _client(env), _member_client(env)
    # /api/search: 401 anon, 200 member
    assert anon.get("/api/search?q=x", follow_redirects=False).status_code == 401
    assert member.get("/api/search?q=x", follow_redirects=False).status_code == 200
    # admin-only curation API stays 401 for members
    assert member.post("/library/1/delete", follow_redirects=False).status_code in (401, 303)


def test_member_nav_shows_logout_not_admin(env):
    c = _member_client(env)
    html = c.get("/library").text
    assert "Log out" in html
    assert ">Admin<" not in html and ">Draft<" not in html
