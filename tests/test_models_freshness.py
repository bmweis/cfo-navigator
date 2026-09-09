"""issue #98, Piece 2 follow-up — the /admin/checks new-model-awareness
reminder, sibling to test_pricing_freshness.py.

Answers a different question than pricing freshness: not whether an
existing model's price is current, but whether Anthropic has shipped
models this app doesn't know about at all. There's no "list every current
model" API to check against automatically, so this is a second, separate
dated manual-attestation signal — a `models_last_reviewed` settings value,
a banner that turns amber once it's stale (or was never recorded) past a
30-day window (tighter than pricing's 90, since new models ship more
often than prices change), and a "Mark reviewed" action that resets it.
"""
import os
import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


def _models_section(html: str) -> str:
    """/admin/checks renders this banner below the Pricing-freshness one
    (issue #98), and an Exa-pricing-freshness one below THIS one, which
    deliberately mirror this banner's own phrasing closely ("never been
    marked reviewed" appears in all three, independently, when
    marked-reviewed at different times) — so a plain whole-page substring
    check can false-positive/negative on an OTHER banner's state. Scope
    every assertion to just the New-model-awareness section, bounded on
    both sides so it doesn't also swallow the Exa section that follows it."""
    return html.split("New-model awareness")[1].split("Exa pricing freshness")[0]


@pytest.fixture
def admin_client(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    client = TestClient(appmod.app, raise_server_exceptions=True)
    client.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    yield client, appmod, db
    if os.path.exists(db):
        os.remove(db)


def test_banner_defaults_to_stale_when_never_reviewed(admin_client):
    client, appmod, db = admin_client
    r = client.get("/admin/checks")
    assert r.status_code == 200
    assert "New-model awareness" in r.text
    assert "never been marked reviewed" in r.text
    # Both banners share this button text — the never-reviewed pricing
    # copy and the never-reviewed models copy both render, so at least 2.
    assert r.text.count("Mark reviewed") >= 2


def test_banner_links_to_the_touchpoint_doc_and_model_overview(admin_client):
    client, appmod, db = admin_client
    r = client.get("/admin/checks")
    assert "adding-a-new-claude-model--every-touchpoint" in r.text
    assert "platform.claude.com/docs/en/about-claude/models/overview" in r.text


def test_mark_reviewed_clears_the_stale_banner(admin_client):
    client, appmod, db = admin_client
    r = client.post("/admin/checks/mark-models-reviewed", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/admin/checks"

    from linklib.db import Library
    lib = Library(db)
    try:
        stored = lib.get_setting("models_last_reviewed")
    finally:
        lib.close()
    assert stored  # a real ISO timestamp was recorded

    r2 = _models_section(client.get("/admin/checks").text)
    assert "never been marked reviewed" not in r2
    assert "past the 30-day review window" not in r2
    assert "manually checked" in r2


def test_mark_reviewed_does_not_touch_the_pricing_reminder(admin_client):
    """The two reminders are independent settings keys — marking models
    reviewed must not silently clear pricing's own staleness (and vice
    versa, covered by test_pricing_freshness.py)."""
    client, appmod, db = admin_client
    client.post("/admin/checks/mark-models-reviewed", follow_redirects=False)
    r = client.get("/admin/checks")
    assert "Pricing has" in r.text and "never been marked reviewed" in r.text


def test_banner_goes_stale_again_past_the_threshold(admin_client):
    client, appmod, db = admin_client
    from datetime import datetime, timedelta, timezone
    from linklib.db import Library
    old = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
    lib = Library(db)
    try:
        lib.set_setting("models_last_reviewed", old)
    finally:
        lib.close()

    r = client.get("/admin/checks")
    assert "past the 30-day review window" in r.text
    assert "Mark reviewed" in r.text


def test_banner_stays_fresh_within_the_threshold(admin_client):
    client, appmod, db = admin_client
    from datetime import datetime, timedelta, timezone
    from linklib.db import Library
    recent = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    lib = Library(db)
    try:
        lib.set_setting("models_last_reviewed", recent)
    finally:
        lib.close()

    r = _models_section(client.get("/admin/checks").text)
    assert "past the 30-day review window" not in r
    assert "never been marked reviewed" not in r
    assert "manually checked" in r


def test_mark_reviewed_requires_auth(monkeypatch):
    monkeypatch.setenv("LINKLIB_DB", tempfile.mktemp(suffix=".db"))
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    client = TestClient(appmod.app, raise_server_exceptions=True)
    r = client.post("/admin/checks/mark-models-reviewed", follow_redirects=False)
    assert r.status_code in (302, 303)
    assert "/login" in r.headers.get("location", "")


def test_checks_page_requires_auth_covers_models_banner_too(monkeypatch):
    monkeypatch.setenv("LINKLIB_DB", tempfile.mktemp(suffix=".db"))
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    client = TestClient(appmod.app, raise_server_exceptions=True)
    r = client.get("/admin/checks", follow_redirects=False)
    assert r.status_code in (302, 303)
