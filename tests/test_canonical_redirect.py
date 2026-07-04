"""Canonical-host redirect: legacy hostnames 301 to bmweis.com.

The middleware must redirect ONLY the two known legacy hosts, ONLY when
PUBLIC_BASE is a real https base, and never for /health — so local dev and
Railway's healthcheck are untouched.
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
