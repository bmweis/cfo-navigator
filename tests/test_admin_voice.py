"""/admin/voice: the two DB-backed voice fields (voice_core, voice_fpa_buddy),
their fallback panels, and the reviewer's two composed rubrics (#95).

Covers what tests/test_ask_agent.py can't from inside linklib: the HTML page
renders both fields' code-constant defaults in the collapsed fallback panel,
the save/reset endpoints round-trip through the settings table, and the
reviewer composes the rubric text the same way _build_system does.
"""
import os
import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Library
from linklib import agent, voice_review
from webapp.app import _esc


@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    yield appmod
    if os.path.exists(db):
        os.remove(db)


def _client(appmod):
    from fastapi.testclient import TestClient
    return TestClient(appmod.app, raise_server_exceptions=True)


def _login_admin(appmod):
    c = _client(appmod)
    c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    return c


def test_page_requires_auth(env):
    r = _client(env).get("/admin/voice", follow_redirects=False)
    assert r.status_code in (302, 303)


def test_page_renders_both_defaults_and_fallback_panels(env):
    html = _login_admin(env).get("/admin/voice").text
    assert _esc(agent.VOICE_CORE_DEFAULT) in html
    assert _esc(agent.VOICE_FPA_BUDDY_DEFAULT) in html
    assert "Default voice guide (used when the field above is empty)" in html
    assert ">Built-in default<" in html
    assert ">Customized<" not in html   # neither field is customized yet


def test_save_core_then_page_reflects_customization(env):
    c = _login_admin(env)
    r = c.post("/admin/voice/core", json={"voice_core": "MY CUSTOM CORE"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "custom": True}
    html = c.get("/admin/voice").text
    assert "MY CUSTOM CORE" in html
    assert 'id="voice-core-badge"' in html
    # the fallback panel still shows the built-in default, verbatim, regardless
    assert _esc(agent.VOICE_CORE_DEFAULT) in html


def test_reset_core_clears_customization(env):
    c = _login_admin(env)
    c.post("/admin/voice/core", json={"voice_core": "MY CUSTOM CORE"})
    r = c.post("/admin/voice/core", json={"voice_core": ""})
    assert r.json() == {"ok": True, "custom": False}
    lib = Library(os.environ["LINKLIB_DB"])
    try:
        assert lib.get_setting("voice_core") == ""
    finally:
        lib.close()


def test_save_fpa_buddy_independent_of_core(env):
    c = _login_admin(env)
    c.post("/admin/voice/fpa-buddy", json={"voice_fpa_buddy": "MY CUSTOM FPA"})
    lib = Library(os.environ["LINKLIB_DB"])
    try:
        assert lib.get_setting("voice_fpa_buddy") == "MY CUSTOM FPA"
        assert lib.get_setting("voice_core") == ""   # untouched
    finally:
        lib.close()


def test_review_endpoint_requires_text(env):
    c = _login_admin(env)
    assert c.post("/admin/voice/review", json={"text": ""}).status_code == 400


def test_review_rubric_general_uses_core_alone(env, monkeypatch):
    captured = {}

    def fake_review_text(text, voice_prompt=None, model=None):
        captured["voice_prompt"] = voice_prompt
        return {"mechanical": [], "review": "stub", "ok": True}

    monkeypatch.setattr(voice_review, "review_text", fake_review_text)
    c = _login_admin(env)
    c.post("/admin/voice/review", json={"text": "some draft copy", "rubric": "general"})
    assert captured["voice_prompt"] == agent.VOICE_CORE_DEFAULT


def test_review_rubric_fpa_buddy_composes_core_plus_fpa(env, monkeypatch):
    captured = {}

    def fake_review_text(text, voice_prompt=None, model=None):
        captured["voice_prompt"] = voice_prompt
        return {"mechanical": [], "review": "stub", "ok": True}

    monkeypatch.setattr(voice_review, "review_text", fake_review_text)
    c = _login_admin(env)
    c.post("/admin/voice/review", json={"text": "an FP&A Buddy answer", "rubric": "fpa_buddy"})
    assert captured["voice_prompt"] == f"{agent.VOICE_CORE_DEFAULT}\n\n{agent.VOICE_FPA_BUDDY_DEFAULT}"


def test_review_rubric_reads_live_db_settings_not_just_defaults(env, monkeypatch):
    captured = {}

    def fake_review_text(text, voice_prompt=None, model=None):
        captured["voice_prompt"] = voice_prompt
        return {"mechanical": [], "review": "stub", "ok": True}

    monkeypatch.setattr(voice_review, "review_text", fake_review_text)
    c = _login_admin(env)
    c.post("/admin/voice/core", json={"voice_core": "CUSTOM CORE FOR REVIEW"})
    c.post("/admin/voice/review", json={"text": "copy", "rubric": "fpa_buddy"})
    assert captured["voice_prompt"] == f"CUSTOM CORE FOR REVIEW\n\n{agent.VOICE_FPA_BUDDY_DEFAULT}"
