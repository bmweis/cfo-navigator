"""Self-service "forgot password" request flow: a signed-out user files a
request from /login, it shows up as a pending task on /admin/users, and
resetting (or dismissing) it clears the badge.
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
    lib.create_user("jane", "supersecret", role="user")
    lib.close()
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


def test_forgot_password_creates_request_for_known_user(env):
    c = _client(env)
    r = c.post("/forgot-password", data={"username": "jane"}, follow_redirects=False)
    assert r.status_code == 303
    lib = env._lib()
    try:
        assert lib.count_pending_password_resets() == 1
        req = lib.list_password_reset_requests()[0]
        assert req["username"] == "jane"
    finally:
        lib.close()


def test_forgot_password_unknown_username_is_silent(env):
    c = _client(env)
    r = c.post("/forgot-password", data={"username": "nobody"}, follow_redirects=False)
    assert r.status_code == 303   # same response whether or not the account exists
    lib = env._lib()
    try:
        assert lib.count_pending_password_resets() == 0
    finally:
        lib.close()


def test_pending_reset_shows_on_admin_users_page(env):
    c = _client(env)
    c.post("/forgot-password", data={"username": "jane"})
    admin = _admin_client(env)
    r = admin.get("/admin/users")
    assert "Requested a password reset" in r.text


def test_admin_reset_password_resolves_request(env):
    c = _client(env)
    c.post("/forgot-password", data={"username": "jane"})
    lib = env._lib()
    try:
        uid = lib.get_user("jane")["id"]
    finally:
        lib.close()
    admin = _admin_client(env)
    admin.post(f"/admin/users/{uid}/password", data={"password": "newpassword123"})
    lib = env._lib()
    try:
        assert lib.count_pending_password_resets() == 0
        assert lib.authenticate("jane", "newpassword123") is not None
    finally:
        lib.close()


def test_admin_dismiss_reset_request(env):
    c = _client(env)
    c.post("/forgot-password", data={"username": "jane"})
    lib = env._lib()
    try:
        uid = lib.get_user("jane")["id"]
    finally:
        lib.close()
    admin = _admin_client(env)
    admin.post(f"/admin/users/{uid}/password-reset/dismiss")
    lib = env._lib()
    try:
        assert lib.count_pending_password_resets() == 0
    finally:
        lib.close()


def test_admin_nav_shows_task_dot_when_reset_pending(env):
    c = _client(env)
    c.post("/forgot-password", data={"username": "jane"})
    admin = _admin_client(env)
    r = admin.get("/library")
    assert 'class="task-dot"' in r.text   # dot renders sitewide, not just on /admin
