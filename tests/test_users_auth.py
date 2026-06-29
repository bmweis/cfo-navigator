"""Auth foundation: password hashing, user accounts, role-aware sessions.

Phase 1 of the three-tier auth. Pins that hashing round-trips, accounts
authenticate (respecting active/role), and the session cookie carries role
with backward-compat for legacy cookies.
"""
import pathlib
import sys
import tempfile, os

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Library
from linklib.passwords import hash_password, verify_password


@pytest.fixture
def lib(tmp_path):
    db = Library(str(tmp_path / "t.db"))
    try:
        yield db
    finally:
        db.close()


def test_password_roundtrip():
    h = hash_password("correct horse battery staple")
    assert h.startswith("scrypt$")
    assert verify_password("correct horse battery staple", h)
    assert not verify_password("wrong", h)
    assert not verify_password("x", "garbage")


def test_create_and_authenticate(lib):
    lib.create_user("Jane.Doe", "supersecret", role="user", email="j@x.com")
    assert lib.authenticate("jane.doe", "supersecret")["role"] == "user"   # username lowercased
    assert lib.authenticate("jane.doe", "nope") is None
    # password hash never leaves the layer
    assert "password_hash" not in lib.authenticate("jane.doe", "supersecret")


def test_disabled_user_cannot_auth(lib):
    uid = lib.create_user("bob", "supersecret")
    lib.set_user_active(uid, False)
    assert lib.authenticate("bob", "supersecret") is None
    lib.set_user_active(uid, True)
    assert lib.authenticate("bob", "supersecret") is not None


def test_reset_password(lib):
    uid = lib.create_user("amy", "oldpassword")
    lib.set_user_password(uid, "newpassword")
    assert lib.authenticate("amy", "oldpassword") is None
    assert lib.authenticate("amy", "newpassword") is not None


def test_duplicate_username_rejected(lib):
    import sqlite3
    lib.create_user("dup", "supersecret")
    with pytest.raises(sqlite3.IntegrityError):
        lib.create_user("DUP", "supersecret")   # case-insensitive collision


def test_update_user_username_and_name(lib):
    uid = lib.create_user("sfunk", "supersecret", name="S. Funk")
    lib.update_user(uid, username="sarah.funk", name="Sarah Funk")
    assert lib.get_user("sfunk") is None
    u = lib.get_user("sarah.funk")
    assert u and u["name"] == "Sarah Funk"
    # auth follows the new username; the renamed account still works
    assert lib.authenticate("sarah.funk", "supersecret") is not None


def test_set_user_role(lib):
    uid = lib.create_user("promo", "supersecret", role="user")
    assert lib.get_user("promo")["role"] == "user"
    lib.set_user_role(uid, "admin")
    assert lib.get_user("promo")["role"] == "admin"
    lib.set_user_role(uid, "user")          # demote back
    assert lib.get_user("promo")["role"] == "user"
    lib.set_user_role(uid, "superuser")     # invalid coerces to 'user'
    assert lib.get_user("promo")["role"] == "user"


def test_update_user_name_only(lib):
    uid = lib.create_user("kim", "supersecret")
    lib.update_user(uid, name="Kim Lee")
    assert lib.get_user("kim")["name"] == "Kim Lee"   # username unchanged


def test_update_user_email(lib):
    uid = lib.create_user("rob", "supersecret", email="old@x.com", name="Rob")
    lib.update_user(uid, username="rob", name="Rob", email="new@x.com")
    u = lib.get_user("rob")
    assert u["email"] == "new@x.com" and u["name"] == "Rob"


def test_update_user_username_collision_rejected(lib):
    lib.create_user("taken", "supersecret")
    uid = lib.create_user("mover", "supersecret")
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        lib.update_user(uid, username="TAKEN")   # case-insensitive collision


# --- session cookie (role-aware) ------------------------------------------

@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("LINKLIB_DB", tempfile.mktemp(suffix=".db"))
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "testkey")
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    yield appmod
    if os.path.exists(appmod.DB_PATH):
        os.remove(appmod.DB_PATH)


def test_session_roundtrip_and_roles(app):
    admin = app._make_session("admin", "")
    user = app._make_session("user", "jane")
    assert app._session_claims(admin)["role"] == "admin"
    assert app._session_claims(user)["role"] == "user"
    assert app._session_claims(user)["username"] == "jane"
    assert app._session_claims("tampered.deadbeef") is None


def test_legacy_cookie_treated_as_admin(app):
    # Old cookies were just a signed expiry number — must still read as admin.
    import time
    legacy = app._sign(str(int(time.time()) + 1000))
    claims = app._session_claims(legacy)
    assert claims and claims["role"] == "admin"


def test_admin_vs_member_gating(app):
    class _Req:
        def __init__(self, cookie): self.cookies = {app.COOKIE_NAME: cookie}
    admin_req = _Req(app._make_session("admin", ""))
    user_req = _Req(app._make_session("user", "jane"))
    assert app._is_authed(admin_req) is True and app._is_member(admin_req) is True
    assert app._is_authed(user_req) is False and app._is_member(user_req) is True
