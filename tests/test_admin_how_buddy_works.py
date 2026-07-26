"""Phase 4 of the Exa integration: a System-group admin page explaining FP&A
Buddy's mechanism in plain language (/admin/system/how-fpa-buddy-works).
Docs/UI-only — no retrieval or citation logic changes.
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
    resp = anon.get("/admin/system/how-fpa-buddy-works", follow_redirects=False)
    assert resp.status_code in (302, 303, 307, 308)

    member = _member_client(env)
    resp = member.get("/admin/system/how-fpa-buddy-works", follow_redirects=False)
    assert resp.status_code in (302, 303, 307, 308)


def test_loads_for_admin_with_expected_sections(env):
    c = _admin_client(env)
    resp = c.get("/admin/system/how-fpa-buddy-works")
    assert resp.status_code == 200
    body = resp.text
    assert "Where an answer&#x27;s sources come from" in body or "Where an answer's sources come from" in body
    assert "Quick, Standard, Deep" in body
    assert "Every claim traces to a citation" in body
    assert "What it costs" in body
    assert "Web search powered by Exa" in body


def test_effort_tier_counts_are_read_live_from_effort_settings(env):
    """Per-tier source counts must come from EFFORT_SETTINGS itself, not a
    hand-typed copy that could drift out of sync with it."""
    from linklib.agent import EFFORT_SETTINGS
    c = _admin_client(env)
    body = c.get("/admin/system/how-fpa-buddy-works").text
    for tier, settings in EFFORT_SETTINGS.items():
        assert f'>{tier}<' in body.lower() or tier in body.lower()
        assert f">{settings['max_library']}<" in body
        assert f">{settings['max_feed']}<" in body
        assert f">{settings['max_web']}<" in body


def test_default_ask_cap_is_read_live_not_hardcoded(env, monkeypatch):
    """A custom default cap set via Library.set_default_ask_cap must show up
    verbatim on the page — proof this isn't a hardcoded $5.00."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    lib.set_default_ask_cap(12.34)
    lib.close()

    c = _admin_client(env)
    body = c.get("/admin/system/how-fpa-buddy-works").text
    assert "$12.34" in body


def test_admin_hub_lists_the_new_card_in_system_group(env):
    c = _admin_client(env)
    resp = c.get("/admin")
    assert resp.status_code == 200
    assert "/admin/system/how-fpa-buddy-works" in resp.text
    assert "How FP&amp;A Buddy works" in resp.text
