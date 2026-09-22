"""The /admin/checks Exa-pricing-freshness reminder — a third, parallel
dated manual-attestation signal, sibling to test_pricing_freshness.py's
Claude pricing banner and test_models_freshness.py's new-model banner.

There's no pricing API to reconcile linklib/pricing.py's EXA_PRICING table
against automatically either, so this is a dated manual-attestation signal,
not a pass/fail check: an `exa_pricing_last_verified` settings value, a
banner that turns amber once it's stale (or was never recorded), and a
"Mark reviewed" action that resets it — the same reviewed-toggle pattern
already used for Community gaps, FP&A Buddy feedback, Claude pricing, and
new-model awareness.
"""
import os
import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


def _exa_pricing_section(html: str) -> str:
    """/admin/checks renders two sibling reminder banners above this one
    (Anthropic pricing, Anthropic models — renamed from "Pricing freshness"/
    "New-model awareness" in the 2026-09 /admin/checks summary rework) that
    share overlapping phrasing ("never been marked reviewed", "manually
    verified") — so a plain whole-page substring check can false-positive/
    negative on an OTHER banner's state. Scope every assertion to just the
    Exa pricing section, which is the last one on the page.

    Splits on the heading's own `id` attribute, not its visible text
    ("Exa pricing") — that visible label also appears once earlier on the
    page, as a row name in the top status summary, which a text-based
    split would incorrectly treat as the section boundary."""
    return html.split('id="exa-pricing-freshness"')[-1]


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
    assert "Exa pricing" in r.text
    section = _exa_pricing_section(r.text)
    assert "never been marked reviewed" in section
    assert "Mark reviewed" in section


def test_mark_reviewed_clears_the_stale_banner(admin_client):
    client, appmod, db = admin_client
    r = client.post("/admin/checks/mark-exa-pricing-reviewed", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/admin/checks"

    from linklib.db import Library
    lib = Library(db)
    try:
        stored = lib.get_setting("exa_pricing_last_verified")
    finally:
        lib.close()
    assert stored  # a real ISO timestamp was recorded

    section = _exa_pricing_section(client.get("/admin/checks").text)
    assert "never been marked reviewed" not in section
    assert "past the 90-day review window" not in section
    assert "manually verified" in section


def test_banner_goes_stale_again_past_the_threshold(admin_client):
    client, appmod, db = admin_client
    from datetime import datetime, timedelta, timezone
    from linklib.db import Library
    old = (datetime.now(timezone.utc) - timedelta(days=91)).isoformat()
    lib = Library(db)
    try:
        lib.set_setting("exa_pricing_last_verified", old)
    finally:
        lib.close()

    section = _exa_pricing_section(client.get("/admin/checks").text)
    assert "past the 90-day review window" in section
    assert "Mark reviewed" in section


def test_banner_stays_fresh_within_the_threshold(admin_client):
    client, appmod, db = admin_client
    from datetime import datetime, timedelta, timezone
    from linklib.db import Library
    recent = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    lib = Library(db)
    try:
        lib.set_setting("exa_pricing_last_verified", recent)
    finally:
        lib.close()

    section = _exa_pricing_section(client.get("/admin/checks").text)
    assert "past the 90-day review window" not in section
    assert "never been marked reviewed" not in section
    assert "manually verified" in section


def test_mark_reviewed_requires_auth(monkeypatch):
    monkeypatch.setenv("LINKLIB_DB", tempfile.mktemp(suffix=".db"))
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    client = TestClient(appmod.app, raise_server_exceptions=True)
    r = client.post("/admin/checks/mark-exa-pricing-reviewed", follow_redirects=False)
    assert r.status_code in (302, 303)
    assert "/login" in r.headers.get("location", "")


def test_pricing_and_models_banners_do_not_leak_into_exa_section(admin_client):
    """The three banners share overlapping phrasing — confirm the Exa
    section genuinely isolates via the split helper rather than accidentally
    always passing because the whole page happens to contain the phrase."""
    client, appmod, db = admin_client
    from datetime import datetime, timezone
    from linklib.db import Library
    now = datetime.now(timezone.utc).isoformat()
    lib = Library(db)
    try:
        lib.set_setting("pricing_last_verified", now)
        lib.set_setting("models_last_reviewed", now)
    finally:
        lib.close()
    full = client.get("/admin/checks").text
    # Pricing + models are now fresh ("manually verified"); Exa is still
    # never-reviewed. The isolated section must show the stale copy even
    # though the full page also contains "manually verified" twice already.
    section = _exa_pricing_section(full)
    assert "never been marked reviewed" in section
