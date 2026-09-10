"""Canonical-host redirect: legacy hostnames 301 to bmweis.com.

The middleware must redirect ONLY the two known legacy hosts, ONLY when
PUBLIC_BASE is a real https base, and never for /health or /admin/backup-now
(Phase O — the daily backup trigger, a Railway Cron Service as of 2026-08
(originally a GitHub Action), calls the latter directly on the legacy
Railway hostname on purpose, to route around Cloudflare's Bot Fight Mode; a
redirect there would silently no-op the backup, since a plain
non-2xx-checking caller treats a 3xx as success and never follows it —
exactly what happened on the first live run before this exemption existed).
"""
import pathlib
import sys
import tempfile, os

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


@pytest.fixture
def appmod(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PUBLIC_BASE", "https://bmweis.com")
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    yield appmod
    if os.path.exists(db):
        os.remove(db)


def _client(appmod):
    from fastapi.testclient import TestClient
    return TestClient(appmod.app, raise_server_exceptions=True)


def test_www_redirects_to_apex(appmod):
    r = _client(appmod).get("/thought-leadership?x=1",
                            headers={"host": "www.bmweis.com"},
                            follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"] == "https://bmweis.com/thought-leadership?x=1"


def test_old_railway_host_redirects(appmod):
    r = _client(appmod).get("/", headers={"host": "cfo-navigator-production.up.railway.app"},
                            follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"] == "https://bmweis.com/"


def test_canonical_and_dev_hosts_untouched(appmod):
    c = _client(appmod)
    assert c.get("/", headers={"host": "bmweis.com"},
                 follow_redirects=False).status_code == 200
    assert c.get("/", headers={"host": "localhost:8000"},
                 follow_redirects=False).status_code == 200


def test_health_never_redirects(appmod):
    r = _client(appmod).get("/health", headers={"host": "www.bmweis.com"},
                            follow_redirects=False)
    assert r.status_code == 200


def test_backup_now_never_redirects_even_on_legacy_railway_host(appmod):
    """Phase O regression pin: the daily backup trigger (a Railway Cron
    Service as of 2026-08) calls this route directly on
    cfo-navigator-production.up.railway.app on purpose (to bypass
    Cloudflare's Bot Fight Mode against bmweis.com). A 301 here would
    silently defeat that — a plain non-2xx-checking caller treats a 3xx as
    success and never follows it, so the trigger would report green while
    the backup logic never ran at all (exactly what happened before this
    exemption, back when this was a GitHub Action)."""
    r = _client(appmod).post("/admin/backup-now",
                              headers={"host": "cfo-navigator-production.up.railway.app"},
                              follow_redirects=False)
    # The route itself must have executed — whatever status it returns
    # (401 unauthorized, 503 not configured, ...) — rather than the
    # middleware short-circuiting with a 301 that never reaches it.
    assert r.status_code != 301
    assert "location" not in r.headers


def test_other_admin_routes_on_legacy_host_still_redirect(appmod):
    """The exemption is scoped to exactly /admin/backup-now, not admin
    routes generally — pins that this stays narrow rather than quietly
    widening."""
    r = _client(appmod).get("/admin/library-backup",
                             headers={"host": "cfo-navigator-production.up.railway.app"},
                             follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"] == "https://bmweis.com/admin/library-backup"


def test_no_redirect_when_base_is_localhost(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.delenv("LINKLIB_PUBLIC_BASE", raising=False)
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    try:
        r = _client(appmod).get("/", headers={"host": "www.bmweis.com"},
                                follow_redirects=False)
        assert r.status_code == 200   # dev default base -> middleware inert
    finally:
        if os.path.exists(db):
            os.remove(db)
