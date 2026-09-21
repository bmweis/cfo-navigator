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


def test_admin_nav_shows_task_badge_when_reset_pending(env):
    # 2026-09: the nav's .task-dot presence-only dot is retired — the nav
    # now shows the same real numeric .task-badge every other admin badge
    # uses (see webapp/tasks.py's module docstring).
    c = _client(env)
    c.post("/forgot-password", data={"username": "jane"})
    admin = _admin_client(env)
    r = admin.get("/")
    assert 'class="task-badge"' in r.text   # badge renders sitewide, not just on /admin


# --- self-service reset link (token-based) ----------------------------------
# jane has no email in the base fixture — these tests create a user with one.

def _extract_reset_url(env, username):
    """Pull the reset link straight off the DB row rather than an actual sent
    email — no Google OAuth is configured in tests, so send_password_reset_email
    always no-ops, but the token is generated and stored regardless."""
    lib = env._lib()
    try:
        row = lib.list_password_reset_requests(pending_only=True)[-1]
        assert row["username"] == username
        return row["token_hash"], row["expires_at"]
    finally:
        lib.close()


def test_forgot_password_with_email_creates_token(env):
    lib = env._lib()
    try:
        lib.create_user("amy", "supersecret", email="amy@example.com")
    finally:
        lib.close()
    c = _client(env)
    r = c.post("/forgot-password", data={"username": "amy"}, follow_redirects=False)
    assert r.status_code == 303
    token_hash, expires_at = _extract_reset_url(env, "amy")
    assert token_hash   # a real token was generated and hashed for storage
    assert expires_at   # and it has an expiry


def test_reset_password_end_to_end(env, monkeypatch):
    lib = env._lib()
    try:
        lib.create_user("amy", "supersecret", email="amy@example.com")
    finally:
        lib.close()

    # Capture the raw token the way the emailed link would carry it, by
    # intercepting send_password_reset_email instead of reading the (hashed,
    # unrecoverable) DB value.
    captured = {}
    from linklib import email_utils

    def _fake_send(to, username, reset_url, **kw):
        captured["reset_url"] = reset_url
        return True
    monkeypatch.setattr(email_utils, "send_password_reset_email", _fake_send)
    monkeypatch.setattr(email_utils, "is_configured", lambda: True)

    c = _client(env)
    c.post("/forgot-password", data={"username": "amy"}, follow_redirects=False)
    assert "reset_url" in captured
    token = captured["reset_url"].split("token=")[1]

    # The reset page renders a form for a valid token.
    r = c.get(f"/reset-password?token={token}")
    assert r.status_code == 200 and "Set a new password" in r.text

    # Submitting a new password takes effect and signs in with it.
    r = c.post("/reset-password", data={"token": token, "password": "brandnewpassword"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login?reset=1"
    assert lib_authenticate(env, "amy", "brandnewpassword")
    assert not lib_authenticate(env, "amy", "supersecret")

    # The token is single-use — the same link no longer works.
    r = c.get(f"/reset-password?token={token}")
    assert "This link has expired" in r.text


def lib_authenticate(env, username, password):
    lib = env._lib()
    try:
        return lib.authenticate(username, password) is not None
    finally:
        lib.close()


def test_reset_password_invalid_token_shows_expired_page(env):
    c = _client(env)
    r = c.get("/reset-password?token=not-a-real-token")
    assert r.status_code == 200 and "This link has expired" in r.text


def test_reset_password_short_password_rejected(env, monkeypatch):
    lib = env._lib()
    try:
        lib.create_user("amy", "supersecret", email="amy@example.com")
    finally:
        lib.close()
    captured = {}
    from linklib import email_utils
    monkeypatch.setattr(email_utils, "send_password_reset_email",
                         lambda to, username, reset_url, **kw: captured.setdefault("reset_url", reset_url) or True)
    monkeypatch.setattr(email_utils, "is_configured", lambda: True)
    c = _client(env)
    c.post("/forgot-password", data={"username": "amy"})
    token = captured["reset_url"].split("token=")[1]
    r = c.post("/reset-password", data={"token": token, "password": "short"}, follow_redirects=False)
    assert r.status_code == 303 and "error=1" in r.headers["location"]
    # Password unchanged.
    assert lib_authenticate(env, "amy", "supersecret")


def test_forgot_password_without_email_notifies_brian(env, monkeypatch):
    # jane (base fixture) has no email — Brian should get a notification
    # email instead of a silent DB-only row, since there's no self-service
    # link possible for her.
    calls = []
    from linklib import email_utils
    monkeypatch.setenv("LINKLIB_CONTACT_EMAIL", "brian@bmweis.com")
    monkeypatch.setattr(email_utils, "is_configured", lambda: True)
    monkeypatch.setattr(email_utils, "send_notification_email",
                         lambda to, subject, body, **kw: calls.append((to, subject, body)) or True)
    c = _client(env)
    c.post("/forgot-password", data={"username": "jane"})
    assert len(calls) == 1
    assert "jane" in calls[0][1]
