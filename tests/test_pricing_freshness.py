"""Per-row pricing freshness (PR 3a) on /admin/checks.

Each model_pricing row carries its own verified_on date and goes stale on its
own after 90 days. The old single pricing_last_verified timestamp and its
"Mark reviewed" button are gone; a row is verified by editing it on
/admin/system/ai. Unverified rows are named but do not make the row stale.
"""
import os
import pathlib
import sys
import tempfile
from datetime import datetime, timedelta, timezone

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


def _seed(db, *, age_days=None):
    from linklib.db import Library
    lib = Library(db)
    try:
        lib.seed_model_catalog()
        if age_days is not None:
            old = (datetime.now(timezone.utc) - timedelta(days=age_days)).date().isoformat()
            lib.conn.execute("UPDATE model_pricing SET verified_on=? WHERE verified_on<>''", (old,))
            lib.conn.commit()
    finally:
        lib.close()


def test_freshly_seeded_rows_are_fresh_and_new_models_unverified(admin_client):
    client, appmod, db = admin_client
    from linklib.db import Library
    lib = Library(db)
    try:
        lib.seed_model_catalog()
        lib.conn.execute("UPDATE model_pricing SET verified_on=? WHERE verified_on<>''",
                         (datetime.now(timezone.utc).date().isoformat(),))
        lib.conn.commit()
        pf = lib.pricing_freshness()
    finally:
        lib.close()
    assert pf["stale_ids"] == []
    assert set(pf["unverified_ids"]) == {"claude-fable-5", "claude-fable-5-1", "claude-sonnet-5-5"}
    text = _pricing_section(client.get("/admin/checks").text)
    assert "past the 90-day" not in text.lower()
    assert "cannot be switched on" in text


def test_one_old_row_makes_the_row_stale_and_is_named(admin_client):
    client, appmod, db = admin_client
    _seed(db)
    from linklib.db import Library
    lib = Library(db)
    try:
        old = (datetime.now(timezone.utc) - timedelta(days=95)).date().isoformat()
        new = datetime.now(timezone.utc).date().isoformat()
        lib.conn.execute("UPDATE model_pricing SET verified_on=? WHERE verified_on<>''", (new,))
        lib.conn.execute("UPDATE model_pricing SET verified_on=? WHERE model_id='claude-opus-5'", (old,))
        lib.conn.commit()
        assert lib.pricing_freshness()["stale_ids"] == ["claude-opus-5"]
    finally:
        lib.close()
    text = _pricing_section(client.get("/admin/checks").text)
    assert "Past the 90-day window" in text and "Opus 5" in text


def test_not_using_model_does_not_make_pricing_stale(admin_client):
    client, appmod, db = admin_client
    _seed(db, age_days=200)
    from linklib.db import Library
    lib = Library(db)
    try:
        for r in lib.list_model_pricing():
            lib.set_model_status(r["model_id"], "not_using", "test")
        assert lib.pricing_freshness()["stale_ids"] == []
    finally:
        lib.close()


def test_the_global_mark_reviewed_route_is_gone(admin_client):
    client, appmod, db = admin_client
    r = client.post("/admin/checks/mark-pricing-reviewed", follow_redirects=False)
    assert r.status_code in (404, 405)


def test_pricing_row_links_to_the_pricing_table_not_the_old_button(admin_client):
    client, appmod, db = admin_client
    text = _pricing_section(client.get("/admin/checks").text)
    assert "/admin/system/ai#model-pricing" in text
    assert "mark-pricing-reviewed" not in text


def test_pricing_message_never_claims_openai_coverage(admin_client):
    client, appmod, db = admin_client
    pf = {"stale_ids": ["claude-opus-5"], "unverified_ids": [], "oldest": "2026-01-01"}
    assert "OpenAI" not in appmod._pricing_freshness_message(pf)[1]
