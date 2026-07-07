"""Outbound email wiring for the four flows that should never fail silently:
contact form, tool submissions, new-user welcome emails, and (see
test_password_reset.py) password resets. Each route sends through
_send_email_safely, so a failing send is caught, logged to stdout, AND
persisted to email_failures — this pins that persistence and the admin
surface built on top of it (/admin/email-failures + its task badge).
"""
import pathlib
import sys
import tempfile, os, time
from urllib.parse import unquote

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


def _configure_email(monkeypatch, fake_notification=None, fake_welcome=None,
                      fake_tool_submission_confirmation=None, fake_contact_confirmation=None):
    from linklib import email_utils
    monkeypatch.setattr(email_utils, "is_configured", lambda: True)
    # notify_to falls back to LINKLIB_CONTACT_EMAIL or default_notify_email()
    # (the latter reads a module-level var frozen at email_utils import time,
    # so it won't pick up a monkeypatched env var) — set this explicitly so
    # the contact/tool-submission notify path actually has a target address.
    monkeypatch.setenv("LINKLIB_CONTACT_EMAIL", "brian@bmweis.com")
    if fake_notification is not None:
        monkeypatch.setattr(email_utils, "send_notification_email", fake_notification)
    if fake_welcome is not None:
        monkeypatch.setattr(email_utils, "send_welcome_email", fake_welcome)
    # Both routes always fire a confirmation to the submitter alongside the
    # Brian-facing notification — stub it out to a harmless no-op unless a
    # test cares about it specifically, so existing notification-only tests
    # don't also need to reason about the confirmation send.
    monkeypatch.setattr(email_utils, "send_tool_submission_confirmation_email",
                         fake_tool_submission_confirmation or (lambda *a, **k: True))
    monkeypatch.setattr(email_utils, "send_contact_confirmation_email",
                         fake_contact_confirmation or (lambda *a, **k: True))


def _past_ts() -> str:
    """A `ts` value old enough to clear /contact's time-trap, simulating a
    form that was rendered a while before this submission."""
    return str(time.time() - 10)


# --- contact form -------------------------------------------------------------

def test_contact_form_sends_notification(env, monkeypatch):
    calls = []
    _configure_email(monkeypatch, fake_notification=lambda to, subject, body, **kw: calls.append((to, subject, body)) or True)
    c = _client(env)
    r = c.post("/contact", data={"name": "Jane", "email": "jane@x.com", "message": "hi", "ts": _past_ts()}, follow_redirects=False)
    assert r.status_code == 303
    assert len(calls) == 1 and "Jane" in calls[0][1]


def test_contact_form_failure_is_logged_not_silent(env, monkeypatch):
    def _boom(to, subject, body, **kw):
        raise RuntimeError("401 Unauthorized")
    _configure_email(monkeypatch, fake_notification=_boom)
    c = _client(env)
    r = c.post("/contact", data={"name": "Jane", "email": "jane@x.com", "message": "hi", "ts": _past_ts()}, follow_redirects=False)
    assert r.status_code == 303   # the contact record still saves; email failure doesn't 500
    lib = env._lib()
    try:
        assert len(lib.list_contacts()) == 1
        failures = lib.list_email_failures()
        assert len(failures) == 1
        assert failures[0]["context"] == "contact"
        assert "401" in failures[0]["detail"]
    finally:
        lib.close()


def test_contact_form_sends_submitter_confirmation(env, monkeypatch):
    calls = []
    _configure_email(monkeypatch, fake_contact_confirmation=lambda to, name, message, **kw: calls.append(
        (to, name, message)) or True)
    c = _client(env)
    r = c.post("/contact", data={"name": "Jane", "email": "jane@x.com", "message": "hi", "ts": _past_ts()}, follow_redirects=False)
    assert r.status_code == 303
    assert len(calls) == 1
    assert calls[0] == ("jane@x.com", "Jane", "hi")


def test_contact_confirmation_failure_is_logged_not_silent(env, monkeypatch):
    def _boom(to, name, message, **kw):
        raise RuntimeError("mailer down")
    _configure_email(monkeypatch, fake_contact_confirmation=_boom)
    c = _client(env)
    c.post("/contact", data={"name": "Jane", "email": "jane@x.com", "message": "hi", "ts": _past_ts()}, follow_redirects=False)
    lib = env._lib()
    try:
        failures = lib.list_email_failures()
        assert any(f["context"] == "contact_confirmation" for f in failures)
    finally:
        lib.close()


def test_contact_form_not_configured_does_not_log_failure(env):
    # No OAuth configured (test default) — this is a graceful no-op, not a
    # failure, so nothing should land in email_failures.
    c = _client(env)
    c.post("/contact", data={"name": "Jane", "email": "jane@x.com", "message": "hi", "ts": _past_ts()})
    lib = env._lib()
    try:
        assert lib.count_pending_email_failures() == 0
    finally:
        lib.close()


# --- tool submission ----------------------------------------------------------

def test_tool_submission_notifies_brian(env, monkeypatch):
    calls = []
    _configure_email(monkeypatch, fake_notification=lambda to, subject, body, **kw: calls.append((to, subject, body)) or True)
    c = _admin_client(env)   # /tools/submit is member-gated (spam control)
    r = c.post("/tools/submit", data={
        "name": "Test Tool", "url": "https://example.com", "description": "desc",
        "submitted_by": "user@example.com",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert len(calls) == 1 and "Test Tool" in calls[0][1]
    lib = env._lib()
    try:
        tools = [t for t in lib.list_tools(approved_only=False) if t["submitted_by"]]
        assert len(tools) == 1 and tools[0]["approved"] == 0
    finally:
        lib.close()


def test_tool_submission_failure_is_logged(env, monkeypatch):
    def _boom(to, subject, body, **kw):
        raise RuntimeError("smtp down")
    _configure_email(monkeypatch, fake_notification=_boom)
    c = _admin_client(env)
    c.post("/tools/submit", data={
        "name": "Test Tool", "url": "https://example.com", "description": "desc",
        "submitted_by": "user@example.com",
    })
    lib = env._lib()
    try:
        failures = lib.list_email_failures()
        assert len(failures) == 1 and failures[0]["context"] == "tool_submission"
    finally:
        lib.close()


def test_tool_submission_sends_submitter_confirmation(env, monkeypatch):
    calls = []
    _configure_email(monkeypatch, fake_tool_submission_confirmation=lambda to, tool_name, tool_url, description, **kw:
                      calls.append((to, tool_name, tool_url, description)) or True)
    c = _admin_client(env)
    c.post("/tools/submit", data={
        "name": "Test Tool", "url": "https://example.com", "description": "desc",
        "submitted_by": "user@example.com",
    }, follow_redirects=False)
    assert len(calls) == 1
    assert calls[0] == ("user@example.com", "Test Tool", "https://example.com", "desc")


def test_tool_submission_confirmation_failure_is_logged_not_silent(env, monkeypatch):
    def _boom(to, tool_name, tool_url, description, **kw):
        raise RuntimeError("mailer down")
    _configure_email(monkeypatch, fake_tool_submission_confirmation=_boom)
    c = _admin_client(env)
    c.post("/tools/submit", data={
        "name": "Test Tool", "url": "https://example.com", "description": "desc",
        "submitted_by": "user@example.com",
    }, follow_redirects=False)
    lib = env._lib()
    try:
        failures = lib.list_email_failures()
        assert any(f["context"] == "tool_submission_confirmation" for f in failures)
    finally:
        lib.close()


# --- new user welcome email ---------------------------------------------------

def test_new_user_with_email_gets_welcome_email(env, monkeypatch):
    calls = []
    _configure_email(monkeypatch, fake_welcome=lambda to, username, temp_password, login_url, name="", **kw: calls.append(
        (to, username, temp_password, login_url, name)) or True)
    admin = _admin_client(env)
    r = admin.post("/admin/users/create", data={
        "username": "newmember", "password": "temppassword123", "role": "user", "email": "new@example.com",
        "name": "New Member",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert len(calls) == 1
    to, username, temp_password, login_url, name = calls[0]
    assert to == "new@example.com" and username == "newmember" and temp_password == "temppassword123"
    assert login_url.endswith("/login")
    assert name == "New Member"
    assert "Welcome email sent" in unquote(r.headers["location"])


def test_new_user_without_email_no_send_attempted(env, monkeypatch):
    calls = []
    _configure_email(monkeypatch, fake_welcome=lambda *a, **k: calls.append(1) or True)
    admin = _admin_client(env)
    r = admin.post("/admin/users/create", data={
        "username": "newmember", "password": "temppassword123", "role": "user",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert calls == []
    assert "No email on file" in unquote(r.headers["location"])


def test_new_user_welcome_failure_is_logged(env, monkeypatch):
    def _boom(to, username, temp_password, login_url, name="", **kw):
        raise RuntimeError("mailer down")
    _configure_email(monkeypatch, fake_welcome=_boom)
    admin = _admin_client(env)
    r = admin.post("/admin/users/create", data={
        "username": "newmember", "password": "temppassword123", "role": "user", "email": "new@example.com",
    }, follow_redirects=False)
    assert "Couldn't email" in unquote(r.headers["location"])
    lib = env._lib()
    try:
        failures = lib.list_email_failures()
        assert len(failures) == 1 and failures[0]["context"] == "welcome"
        # The account still exists — a broken welcome email must not block account creation.
        assert lib.get_user("newmember") is not None
    finally:
        lib.close()


# --- admin surface -------------------------------------------------------------

def test_admin_email_failures_page_lists_and_dismisses(env):
    lib = env._lib()
    try:
        fid = lib.log_email_failure("contact", "boom")
    finally:
        lib.close()
    admin = _admin_client(env)
    r = admin.get("/admin/email-failures")
    assert r.status_code == 200 and "boom" in r.text and "contact" in r.text

    admin.post(f"/admin/email-failures/{fid}/dismiss")
    lib = env._lib()
    try:
        assert lib.count_pending_email_failures() == 0
    finally:
        lib.close()


def test_admin_email_failures_requires_admin(env):
    c = _client(env)
    r = c.get("/admin/email-failures", follow_redirects=False)
    assert r.status_code == 303 and "/login" in r.headers["location"]


def test_admin_nav_shows_task_dot_for_email_failure(env):
    lib = env._lib()
    try:
        lib.log_email_failure("contact", "boom")
    finally:
        lib.close()
    admin = _admin_client(env)
    r = admin.get("/library")
    assert 'class="task-dot"' in r.text
