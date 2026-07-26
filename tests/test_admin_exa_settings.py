"""Phase 7 of the Exa migration: the /admin/exa-settings kill-switch page —
toggle plus an on-demand "Test connection" action. Lives in the FP&A Buddy
admin section (Phase 6).
"""
import pathlib
import sys
import tempfile
import os

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    from linklib.db import Library
    lib = Library(db)
    lib.create_user("member1", "supersecret", role="user")
    lib.close()
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


def _member_client(appmod):
    c = _client(appmod)
    c.post("/login", data={"username": "member1", "password": "supersecret"}, follow_redirects=False)
    return c


def test_requires_admin_not_just_member(env):
    anon = _client(env)
    resp = anon.get("/admin/exa-settings", follow_redirects=False)
    assert resp.status_code in (302, 303, 307, 308)

    member = _member_client(env)
    resp = member.get("/admin/exa-settings", follow_redirects=False)
    assert resp.status_code in (302, 303, 307, 308)


def test_page_shows_toggle_checked_by_default(env):
    c = _admin_client(env)
    resp = c.get("/admin/exa-settings")
    assert resp.status_code == 200
    body = resp.text
    assert 'id="exa-toggle"' in body
    assert 'id="exa-toggle" checked' in body   # default: enabled


def test_page_flags_missing_exa_api_key(env):
    c = _admin_client(env)
    body = c.get("/admin/exa-settings").text
    assert "is not set on this host" in body


def test_page_no_key_banner_when_key_is_set(env, monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "fake-key")
    c = _admin_client(env)
    body = c.get("/admin/exa-settings").text
    assert "is not set on this host" not in body


def test_toggle_off_persists_and_reflects_on_reload(env):
    c = _admin_client(env)
    resp = c.post("/admin/exa-settings/toggle", json={"enabled": False})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "enabled": False}

    body = c.get("/admin/exa-settings").text
    assert 'id="exa-toggle" checked' not in body

    resp2 = c.post("/admin/exa-settings/toggle", json={"enabled": True})
    assert resp2.json() == {"ok": True, "enabled": True}
    body2 = c.get("/admin/exa-settings").text
    assert 'id="exa-toggle" checked' in body2


def test_toggle_requires_admin(env):
    member = _member_client(env)
    resp = member.post("/admin/exa-settings/toggle", json={"enabled": False})
    assert resp.status_code == 401


def test_test_connection_reports_missing_key(env):
    c = _admin_client(env)
    resp = c.post("/admin/exa-settings/test-connection")
    assert resp.status_code == 200
    d = resp.json()
    assert d["ok"] is False
    assert "EXA_API_KEY" in d["error"]


def test_test_connection_requires_admin(env):
    member = _member_client(env)
    resp = member.post("/admin/exa-settings/test-connection")
    assert resp.status_code == 401


def test_test_connection_success_path(env, monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "fake-key")

    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"results": [{"url": "https://x.com"}]}

    import linklib.agent as agent_mod
    monkeypatch.setattr(agent_mod.requests, "post", lambda *a, **k: _FakeResponse())

    c = _admin_client(env)
    resp = c.post("/admin/exa-settings/test-connection")
    assert resp.status_code == 200
    d = resp.json()
    assert d["ok"] is True
    assert d["cost_usd"] > 0


def test_new_card_is_reachable_from_admin_hub(env):
    c = _admin_client(env)
    resp = c.get("/admin")
    assert resp.status_code == 200
    assert "/admin/exa-settings" in resp.text
    assert "Exa web search" in resp.text
