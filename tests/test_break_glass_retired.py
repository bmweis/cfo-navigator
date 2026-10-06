"""The shared-secret login fallback is retired (issue #627).

These tests opt out of the suite-wide admin seeding (tests/conftest.py) so
they see the real behavior: a password that matches LINKLIB_PASSWORD but has
no `users` row must NOT log in.
"""
import importlib
import tempfile

import pytest

pytestmark = pytest.mark.no_admin_seed


@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "sharedsecret")
    monkeypatch.setenv("LINKLIB_SAVE_TOKEN", "savetoken123")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    monkeypatch.delenv("LINKLIB_ADMIN_USERNAME", raising=False)
    import webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    from linklib.db import Library
    lib = Library(db)
    lib.create_user("bmw", "realpassword1", role="admin")
    lib.close()
    return appmod, TestClient(appmod.app)


def _login(c, u, p):
    return c.post("/login", data={"username": u, "password": p}, follow_redirects=False)


def test_shared_password_with_admin_username_no_longer_logs_in(env):
    _, c = env
    r = _login(c, "admin", "sharedsecret")
    assert r.status_code == 303 and r.headers["location"].startswith("/login?error=1")
    assert "cfo_session" not in r.cookies


def test_save_token_as_password_no_longer_logs_in(env):
    _, c = env
    r = _login(c, "admin", "savetoken123")
    assert r.headers["location"].startswith("/login?error=1")
    assert "cfo_session" not in r.cookies


def test_custom_admin_username_env_gives_no_fallback(env, monkeypatch):
    monkeypatch.setenv("LINKLIB_ADMIN_USERNAME", "boss")
    import webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    r = _login(TestClient(appmod.app), "boss", "sharedsecret")
    assert r.headers["location"].startswith("/login?error=1")


def test_wrong_password_response_is_identical(env):
    _, c = env
    a = _login(c, "admin", "sharedsecret")
    b = _login(c, "admin", "totally-wrong")
    assert a.status_code == b.status_code and a.headers["location"] == b.headers["location"]


def test_real_users_row_still_logs_in(env):
    _, c = env
    r = _login(c, "bmw", "realpassword1")
    assert r.status_code == 303 and r.headers["location"] == "/admin"
    assert "cfo_session" in r.cookies
    assert c.get("/admin", follow_redirects=False).status_code == 200


def test_other_uses_of_the_secret_are_unchanged(env):
    """Out of scope here: token routes keep accepting the save token."""
    _, c = env
    assert c.get("/api/search?q=x&token=savetoken123").status_code == 200
    assert c.get("/api/search?q=x&token=wrong").status_code == 401
    assert c.get("/api/search?q=x").status_code == 401
    r = c.post("/save?token=savetoken123", json={"url": ""})
    assert r.status_code != 401
    assert c.post("/save?token=nope", json={"url": ""}).status_code == 401
