"""Encourage-password-change: users.password_change_recommended, the
dismissible nudge banner on the two post-login landing pages, the in-session
/change-password form, and the admin-reset nudge email.

Covers:
- create_user defaults the flag to 1 (temp/admin-set password).
- Admin resetting an existing account's password (POST /admin/users/{id}/password)
  sets the flag and sends send_admin_password_reset_email when an email is on file.
- Self-service reset (/reset-password) and the in-session /change-password
  form both clear the flag.
- The nudge banner renders on / and /admin when the flag is set for the
  signed-in user, and is absent once cleared.
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
    yield appmod
    if os.path.exists(db):
        os.remove(db)


def _client(appmod):
    from fastapi.testclient import TestClient
    return TestClient(appmod.app, raise_server_exceptions=True)


def _login(appmod, username, password):
    c = _client(appmod)
    c.post("/login", data={"username": username, "password": password}, follow_redirects=False)
    return c


def _admin_client(appmod):
    return _login(appmod, "admin", "adminpass")


# --- flag defaults / set / clear --------------------------------------------

def test_create_user_defaults_flag_to_true(env):
    lib = env._lib()
    try:
        uid = lib.create_user("jane", "supersecret", role="user")
        user = lib.get_user_by_id(uid)
    finally:
        lib.close()
    assert user["password_change_recommended"] == 1


def test_create_user_can_opt_out_of_flag(env):
    lib = env._lib()
    try:
        uid = lib.create_user("jane", "supersecret", role="user", password_change_recommended=False)
        user = lib.get_user_by_id(uid)
    finally:
        lib.close()
    assert user["password_change_recommended"] == 0


def test_admin_reset_sets_flag(env):
    lib = env._lib()
    try:
        uid = lib.create_user("jane", "supersecret", role="user")
        lib.set_password_change_recommended(uid, False)  # pretend jane already changed it once
    finally:
        lib.close()
    admin = _admin_client(env)
    admin.post(f"/admin/users/{uid}/password", data={"password": "newtemppassword"})
    lib = env._lib()
    try:
        user = lib.get_user_by_id(uid)
    finally:
        lib.close()
    assert user["password_change_recommended"] == 1


def test_admin_reset_sends_nudge_email_when_email_on_file(env, monkeypatch):
    calls = []
    from linklib import email_utils
    monkeypatch.setattr(email_utils, "is_configured", lambda: True)
    monkeypatch.setattr(
        email_utils, "send_admin_password_reset_email",
        lambda to, username, temp_password, login_url, **kw: calls.append((to, username, temp_password)) or True,
    )
    lib = env._lib()
    try:
        uid = lib.create_user("jane", "supersecret", role="user", email="jane@example.com")
    finally:
        lib.close()
    admin = _admin_client(env)
    r = admin.post(f"/admin/users/{uid}/password", data={"password": "newtemppassword"}, follow_redirects=False)
    assert r.status_code == 303
    assert len(calls) == 1
    to, username, temp_password = calls[0]
    assert to == "jane@example.com"
    assert username == "jane"
    assert temp_password == "newtemppassword"


def test_admin_reset_no_email_on_file_does_not_send(env, monkeypatch):
    from linklib import email_utils
    monkeypatch.setattr(email_utils, "is_configured", lambda: True)
    sent = []
    monkeypatch.setattr(
        email_utils, "send_admin_password_reset_email",
        lambda *a, **kw: sent.append(1) or True,
    )
    lib = env._lib()
    try:
        uid = lib.create_user("jane", "supersecret", role="user")  # no email
    finally:
        lib.close()
    admin = _admin_client(env)
    r = admin.post(f"/admin/users/{uid}/password", data={"password": "newtemppassword"}, follow_redirects=False)
    assert r.status_code == 303
    assert sent == []
    from urllib.parse import unquote
    assert "no email on file" in unquote(r.headers["location"]).lower()
    lib = env._lib()
    try:
        assert lib.authenticate("jane", "newtemppassword") is not None
    finally:
        lib.close()


def test_self_service_reset_clears_flag(env, monkeypatch):
    from linklib import email_utils
    monkeypatch.setattr(email_utils, "is_configured", lambda: True)
    captured = {}
    monkeypatch.setattr(
        email_utils, "send_password_reset_email",
        lambda to, username, reset_url, **kw: captured.setdefault("reset_url", reset_url) or True,
    )
    lib = env._lib()
    try:
        uid = lib.create_user("amy", "supersecret", email="amy@example.com")
        assert lib.get_user_by_id(uid)["password_change_recommended"] == 1
    finally:
        lib.close()
    c = _client(env)
    c.post("/forgot-password", data={"username": "amy"})
    token = captured["reset_url"].split("token=")[1]
    r = c.post("/reset-password", data={"token": token, "password": "brandnewpassword"}, follow_redirects=False)
    assert r.status_code == 303
    lib = env._lib()
    try:
        assert lib.get_user_by_id(uid)["password_change_recommended"] == 0
    finally:
        lib.close()


# --- in-session /change-password --------------------------------------------

def test_change_password_requires_login(env):
    c = _client(env)
    r = c.get("/change-password", follow_redirects=False)
    assert r.status_code == 303 and "/login" in r.headers["location"]
    r = c.post("/change-password", data={"current_password": "x", "new_password": "y" * 8}, follow_redirects=False)
    assert r.status_code == 303 and "/login" in r.headers["location"]


def test_change_password_wrong_current_password_rejected(env):
    lib = env._lib()
    try:
        uid = lib.create_user("jane", "supersecret", role="user")
    finally:
        lib.close()
    c = _login(env, "jane", "supersecret")
    r = c.post("/change-password", data={"current_password": "wrongpass", "new_password": "newpassword123"},
               follow_redirects=False)
    assert r.status_code == 303 and "error=current" in r.headers["location"]
    lib = env._lib()
    try:
        assert lib.authenticate("jane", "supersecret") is not None
        assert lib.get_user_by_id(uid)["password_change_recommended"] == 1
    finally:
        lib.close()


def test_change_password_too_short_rejected(env):
    lib = env._lib()
    try:
        lib.create_user("jane", "supersecret", role="user")
    finally:
        lib.close()
    c = _login(env, "jane", "supersecret")
    r = c.post("/change-password", data={"current_password": "supersecret", "new_password": "short"},
               follow_redirects=False)
    assert r.status_code == 303 and "error=short" in r.headers["location"]


def test_change_password_success_updates_password_and_clears_flag(env):
    lib = env._lib()
    try:
        uid = lib.create_user("jane", "supersecret", role="user")
        assert lib.get_user_by_id(uid)["password_change_recommended"] == 1
    finally:
        lib.close()
    c = _login(env, "jane", "supersecret")
    r = c.post("/change-password", data={"current_password": "supersecret", "new_password": "newpassword123"},
               follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/change-password?done=1"
    lib = env._lib()
    try:
        assert lib.authenticate("jane", "newpassword123") is not None
        assert lib.authenticate("jane", "supersecret") is None
        assert lib.get_user_by_id(uid)["password_change_recommended"] == 0
    finally:
        lib.close()
    # The done=1 confirmation page renders instead of the form.
    r2 = c.get("/change-password?done=1")
    assert r2.status_code == 200 and "Password updated" in r2.text


# --- nudge banner on the post-login landing pages ---------------------------

def test_nudge_banner_shown_on_homepage_for_flagged_member(env):
    lib = env._lib()
    try:
        lib.create_user("jane", "supersecret", role="user")
    finally:
        lib.close()
    c = _login(env, "jane", "supersecret")
    r = c.get("/")
    assert 'id="pw-nudge-banner"' in r.text
    assert "/change-password" in r.text


def test_nudge_banner_absent_once_flag_cleared(env):
    lib = env._lib()
    try:
        uid = lib.create_user("jane", "supersecret", role="user")
        lib.set_password_change_recommended(uid, False)
    finally:
        lib.close()
    c = _login(env, "jane", "supersecret")
    r = c.get("/")
    assert 'id="pw-nudge-banner"' not in r.text


def test_nudge_banner_absent_for_signed_out_visitor(env):
    lib = env._lib()
    try:
        lib.create_user("jane", "supersecret", role="user")
    finally:
        lib.close()
    c = _client(env)
    r = c.get("/")
    assert 'id="pw-nudge-banner"' not in r.text


def test_nudge_banner_shown_on_admin_hub_for_flagged_admin(env):
    # The break-glass admin login has no `users` row, so it can never carry
    # the flag — create a real admin account instead.
    lib = env._lib()
    try:
        lib.create_user("realadmin", "supersecret", role="admin")
    finally:
        lib.close()
    c = _login(env, "realadmin", "supersecret")
    r = c.get("/admin")
    assert 'id="pw-nudge-banner"' in r.text


def test_nudge_banner_absent_for_break_glass_admin_login(env):
    # The reserved host-password admin login has no matching `users` row —
    # _password_change_nudge_html must not error, and shows nothing.
    admin = _admin_client(env)
    r = admin.get("/admin")
    assert r.status_code == 200
    assert 'id="pw-nudge-banner"' not in r.text
