"""Public library-submission flow (webapp /library/submit).

Retired 2026-09 (PR 3): this used to write UN-ENRICHED into the Archive
Queue for later review (see linklib/queue.py, now deleted). A production
query found 5,508 queue rows, all dismissed, zero pending, and zero member
submissions ever — so the queue itself was retired, and this route became a
plain email notification to Brian instead. He reads the email and saves the
article himself with the bookmarklet if it's a fit. No confirmation email
goes to the submitter — only the on-page confirmation.

Still keeps its honeypot (mirroring /contact's) and its _is_member gate.
"""
import pathlib
import sys
import tempfile, os

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


@pytest.fixture
def client(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app, raise_server_exceptions=True)
    c._db = db
    c._appmod = appmod
    c.post("/login", data={"username": "admin", "password": "adminpass"},
          follow_redirects=False)  # /library/submit is member-gated
    yield c
    if os.path.exists(db):
        os.remove(db)


def _configure_email(monkeypatch, fake_notification=None):
    from linklib import email_utils
    monkeypatch.setattr(email_utils, "is_configured", lambda: True)
    # notify_to falls back to LINKLIB_CONTACT_EMAIL or default_notify_email()
    # (the latter reads a module-level var frozen at email_utils import
    # time, so it won't pick up a monkeypatched env var here) — set this
    # explicitly so the notify path always has a target address.
    monkeypatch.setenv("LINKLIB_CONTACT_EMAIL", "brian@bmweis.com")
    if fake_notification is not None:
        monkeypatch.setattr(email_utils, "send_notification_email", fake_notification)


def test_submission_sends_a_notification_email_not_a_queue_write(client, monkeypatch):
    calls = []
    _configure_email(monkeypatch, fake_notification=lambda to, subject, body, **kw:
                      calls.append((to, subject, body)) or True)
    r = client.post("/library/submit",
                    data={"url": "https://ex.com/great-post", "why": "Best NRR breakdown I've read",
                          "name": "Jane", "email": "jane@x.com"},
                    follow_redirects=False)
    assert r.status_code == 303 and "submitted=1" in r.headers["location"]
    assert len(calls) == 1
    to, subject, body = calls[0]
    assert to == "brian@bmweis.com"
    assert "Jane" in subject
    assert "https://ex.com/great-post" in body
    assert "Jane" in body and "jane@x.com" in body
    assert "NRR" in body


def test_submission_failure_is_logged_not_silent(client, monkeypatch):
    def _boom(to, subject, body, **kw):
        raise RuntimeError("smtp down")
    _configure_email(monkeypatch, fake_notification=_boom)
    client.post("/library/submit", data={"url": "https://ex.com/x"}, follow_redirects=False)
    lib = client._appmod._lib()
    try:
        failures = lib.list_email_failures()
        assert len(failures) == 1 and failures[0]["context"] == "library_submission"
    finally:
        lib.close()


def test_no_confirmation_email_is_sent_to_the_submitter(client, monkeypatch):
    """Explicit requirement: only Brian gets an email — the submitter sees
    the on-page confirmation instead."""
    from linklib import email_utils
    calls = []
    _configure_email(monkeypatch, fake_notification=lambda to, subject, body, **kw:
                      calls.append(to) or True)

    def _fail_if_called(*a, **k):
        raise AssertionError("no confirmation email should be sent to the submitter")
    # There is no dedicated confirmation function for this flow at all — assert
    # the generic send path is never asked to email anyone but Brian.
    monkeypatch.setattr(email_utils, "send_contact_confirmation_email", _fail_if_called)
    monkeypatch.setattr(email_utils, "send_tool_submission_confirmation_email", _fail_if_called)
    monkeypatch.setattr(email_utils, "send_community_submission_confirmation_email", _fail_if_called)

    client.post("/library/submit",
               data={"url": "https://ex.com/y", "email": "submitter@x.com"},
               follow_redirects=False)
    assert calls == ["brian@bmweis.com"]


def test_honeypot_drops_silently_no_email_sent(client, monkeypatch):
    calls = []
    _configure_email(monkeypatch, fake_notification=lambda to, subject, body, **kw:
                      calls.append(to) or True)
    r = client.post("/library/submit",
                    data={"url": "https://ex.com/spam", "website": "http://bot.example"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert calls == []


def test_bad_url_rejected(client):
    r = client.post("/library/submit", data={"url": "not-a-url"}, follow_redirects=False)
    assert r.status_code == 400


def test_submit_page_renders(client):
    assert "Suggest a piece for the archive" in client.get("/library/submit").text
    assert "suggestion received" in client.get("/library/submit?submitted=1").text.lower()


def test_submit_requires_member_login(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    anon = TestClient(appmod.app)
    try:
        r = anon.post("/library/submit", data={"url": "https://ex.com/x"}, follow_redirects=False)
        assert r.status_code in (302, 303, 307)
        assert "/login" in r.headers["location"]
    finally:
        if os.path.exists(db):
            os.remove(db)
