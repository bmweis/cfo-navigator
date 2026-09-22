"""issue #98, Piece 2 — the /admin/checks pricing-freshness reminder.

There's no pricing API to reconcile MODEL_PRICING against automatically
(see linklib/pricing.py's module docstring), so this is a dated
manual-attestation signal, not a pass/fail check: a `pricing_last_verified`
settings value, a banner that turns amber once it's stale (or was never
recorded), and a "Mark reviewed" action that resets it — the same
reviewed-toggle pattern already used for Community gaps
(toggle_community_gap_reviewed) and FP&A Buddy feedback
(toggle_ask_feedback_reviewed), applied here as a plain dated setting since
there's no per-row entity to flip.
"""
import os
import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


def _pricing_section(html: str) -> str:
    """/admin/checks now renders a second, sibling reminder banner below
    this one (Anthropic models, issue #98 Piece 2 follow-up, renamed from
    "New-model awareness" in the 2026-09 /admin/checks summary rework) that
    deliberately mirrors this banner's own phrasing closely ("never been
    marked reviewed" appears in both, independently, when marked-reviewed
    at different times) — so a plain whole-page substring check can false-
    positive/negative on the OTHER banner's state. Scope every assertion
    to just the Pricing-freshness section.

    Splits on the models heading's own `id` ATTRIBUTE, not its visible text
    ("Anthropic models") — that visible label also appears once earlier on
    the page, as a row name in the top status summary (2026-09 rework),
    which a text-based split would incorrectly treat as the section
    boundary. The id attribute renders exactly once, on the real <h3>."""
    return html.split('id="new-model-awareness"')[0]


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


def test_checks_page_requires_auth(monkeypatch):
    monkeypatch.setenv("LINKLIB_DB", tempfile.mktemp(suffix=".db"))
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    client = TestClient(appmod.app, raise_server_exceptions=True)
    r = client.get("/admin/checks", follow_redirects=False)
    assert r.status_code in (302, 303)


def test_banner_defaults_to_stale_when_never_reviewed(admin_client):
    client, appmod, db = admin_client
    r = client.get("/admin/checks")
    assert r.status_code == 200
    assert "Anthropic pricing" in r.text
    assert "never been marked reviewed" in r.text
    assert "Mark reviewed" in r.text


def test_mark_reviewed_clears_the_stale_banner(admin_client):
    client, appmod, db = admin_client
    r = client.post("/admin/checks/mark-pricing-reviewed", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/admin/checks"

    from linklib.db import Library
    lib = Library(db)
    try:
        stored = lib.get_setting("pricing_last_verified")
    finally:
        lib.close()
    assert stored  # a real ISO timestamp was recorded

    r2 = _pricing_section(client.get("/admin/checks").text)
    assert "never been marked reviewed" not in r2
    assert "past the 90-day review window" not in r2
    assert "manually verified" in r2


def test_banner_goes_stale_again_past_the_threshold(admin_client):
    client, appmod, db = admin_client
    from datetime import datetime, timedelta, timezone
    from linklib.db import Library
    old = (datetime.now(timezone.utc) - timedelta(days=91)).isoformat()
    lib = Library(db)
    try:
        lib.set_setting("pricing_last_verified", old)
    finally:
        lib.close()

    r = client.get("/admin/checks")
    assert "past the 90-day review window" in r.text
    assert "Mark reviewed" in r.text


def test_banner_stays_fresh_within_the_threshold(admin_client):
    client, appmod, db = admin_client
    from datetime import datetime, timedelta, timezone
    from linklib.db import Library
    recent = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    lib = Library(db)
    try:
        lib.set_setting("pricing_last_verified", recent)
    finally:
        lib.close()

    r = _pricing_section(client.get("/admin/checks").text)
    assert "past the 90-day review window" not in r
    assert "never been marked reviewed" not in r
    assert "manually verified" in r


def test_mark_reviewed_requires_auth(monkeypatch):
    monkeypatch.setenv("LINKLIB_DB", tempfile.mktemp(suffix=".db"))
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    client = TestClient(appmod.app, raise_server_exceptions=True)
    r = client.post("/admin/checks/mark-pricing-reviewed", follow_redirects=False)
    assert r.status_code in (302, 303)
    assert "/login" in r.headers.get("location", "")
