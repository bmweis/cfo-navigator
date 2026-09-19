"""/admin/voice: the three DB-backed voice fields (voice_core,
voice_fpa_buddy, voice_matchmaker), their seeding, the blocked-generation
banner, and the reviewer's three composed rubrics (#95, plus the Chat
Matchmaker voice pass).

2026-08 visibility follow-up rewrite: an empty voice setting used to
silently fall back to its code-constant default at read time. That's
retired — `Library.seed_voice_prompts()` populates all three from their
defaults once per database, and after that, an empty setting BLOCKS
whatever depends on it (`linklib.voice_settings.require_voice_setting`
raises) instead of substituting a default. This file covers: the seeded vs.
unseeded/cleared states on the page itself (badge text, the blocked
banner), the save/reset endpoints (reset now writes the real default text,
not blank — a blank save is a distinct, still-supported "deliberately
clear it" action), and that the reviewer's three rubrics compose from live
settings and refuse (503) rather than fall back when a setting is missing.

Covers what tests/test_ask_agent.py can't from inside linklib: the actual
HTML the admin sees, and the save/reset/review routes' real request/response
shapes.
"""
import os
import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Library
from linklib import agent, matchmaker, voice_review
from linklib.voice_settings import VoicePromptMissing
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


def _db_path():
    return os.environ["LINKLIB_DB"]


def test_page_requires_auth(env):
    r = _client(env).get("/admin/voice", follow_redirects=False)
    assert r.status_code in (302, 303)


# --- Unseeded / cleared state: the blocked-generation banner -----------------

def test_unseeded_page_shows_blocked_banner_and_not_configured_badges(env):
    """A TestClient instantiated without `with` never runs the startup
    seeding hook (confirmed empirically — the fixture's fresh DB stays
    genuinely empty), so this exercises the real "never seeded" state."""
    html = _login_admin(env).get("/admin/voice").text
    assert "Generation is blocked" in html
    assert "Voice core, FP&amp;A Buddy voice, Chat matchmaker voice" in html or \
           "Voice core" in html  # names listed in the banner
    assert html.count("Not configured&mdash;generation blocked") == 3
    assert ">Customized<" not in html
    assert ">Default (as seeded)<" not in html


def test_seeded_page_shows_default_as_seeded_no_banner(env):
    lib = Library(_db_path())
    lib.seed_voice_prompts()
    lib.close()
    html = _login_admin(env).get("/admin/voice").text
    assert "Generation is blocked" not in html
    assert html.count("Default (as seeded)") == 3
    assert ">Customized<" not in html
    assert _esc(agent.VOICE_CORE_DEFAULT) in html
    assert _esc(agent.VOICE_FPA_BUDDY_DEFAULT) in html
    assert _esc(matchmaker.VOICE_MATCHMAKER_DEFAULT) in html


def test_page_mirrors_mechanical_lists_read_only(env):
    """2026-09 voice-enforcement PR: BANNED_WORDS/FILLER_PHRASES/PERFORMATIVE
    stay in linklib/voice_review.py, permanently — the page renders them
    live from that import for visibility only, marked source-managed, no
    save mechanism of any kind for this section."""
    html = _login_admin(env).get("/admin/voice").text
    assert "Source-managed" in html
    assert "Mechanical rules" in html
    for w in voice_review.BANNED_WORDS:
        assert _esc(w) in html
    for p in voice_review.FILLER_PHRASES:
        assert _esc(p) in html
    for p in voice_review.PERFORMATIVE:
        assert _esc(p) in html
    # No save/reset wiring for this section — it's a plain render, not
    # another instance of _voice_field's editable-textarea pattern.
    assert "<textarea" not in html.split("Mechanical rules")[1].split("Check content against your voice")[0]


def test_seeding_then_manual_clear_shows_partial_banner(env):
    """A deliberate clear-out of just one field must still show as blocked
    for that field alone, distinct from the other two seeded fields."""
    lib = Library(_db_path())
    lib.seed_voice_prompts()
    lib.set_setting("voice_matchmaker", "")
    lib.close()
    html = _login_admin(env).get("/admin/voice").text
    assert "Generation is blocked" in html
    assert html.count("Not configured&mdash;generation blocked") == 1
    assert html.count("Default (as seeded)") == 2


# --- Save / reset endpoints ---------------------------------------------------

def test_save_core_then_page_reflects_customization(env):
    c = _login_admin(env)
    r = c.post("/admin/voice/core", json={"voice_core": "MY CUSTOM CORE"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["custom"] is True
    html = c.get("/admin/voice").text
    assert "MY CUSTOM CORE" in html
    assert 'id="voice-core-badge"' in html
    assert ">Customized<" in html
    # the fallback panel still shows the built-in default, verbatim, regardless
    assert _esc(agent.VOICE_CORE_DEFAULT) in html


def test_blank_save_is_a_deliberate_clear_not_a_reset(env):
    """Saving an empty textarea (the Save button, not Reset) must leave the
    setting genuinely empty — blocking generation — not silently write the
    default text back."""
    c = _login_admin(env)
    c.post("/admin/voice/core", json={"voice_core": "MY CUSTOM CORE"})
    r = c.post("/admin/voice/core", json={"voice_core": ""})
    assert r.json()["custom"] is False
    lib = Library(_db_path())
    try:
        assert lib.get_setting("voice_core") == ""
    finally:
        lib.close()


def test_reset_writes_the_real_default_text_not_blank(env):
    """2026-08 behavior change: reset used to send a blank value (which
    then silently fell back to the default at read time). Now a blank
    setting blocks generation, so 'reset' has to actually write the
    default text into the setting."""
    c = _login_admin(env)
    c.post("/admin/voice/core", json={"voice_core": "MY CUSTOM CORE"})
    r = c.post("/admin/voice/core", json={"reset": True})
    body = r.json()
    assert body["ok"] is True
    assert body["value"] == agent.VOICE_CORE_DEFAULT
    lib = Library(_db_path())
    try:
        # Non-empty and equal to the default — generation is NOT blocked
        # after a reset, unlike after a blank save.
        assert lib.get_setting("voice_core") == agent.VOICE_CORE_DEFAULT
    finally:
        lib.close()


def test_reset_fpa_buddy_writes_its_own_default(env):
    c = _login_admin(env)
    r = c.post("/admin/voice/fpa-buddy", json={"reset": True})
    assert r.json()["value"] == agent.VOICE_FPA_BUDDY_DEFAULT


def test_reset_matchmaker_writes_its_own_default(env):
    c = _login_admin(env)
    r = c.post("/admin/voice/matchmaker", json={"reset": True})
    assert r.json()["value"] == matchmaker.VOICE_MATCHMAKER_DEFAULT


def test_save_fpa_buddy_independent_of_core(env):
    c = _login_admin(env)
    c.post("/admin/voice/fpa-buddy", json={"voice_fpa_buddy": "MY CUSTOM FPA"})
    lib = Library(_db_path())
    try:
        assert lib.get_setting("voice_fpa_buddy") == "MY CUSTOM FPA"
        assert lib.get_setting("voice_core") == ""   # untouched
    finally:
        lib.close()


def test_save_matchmaker_voice_independent_of_core_and_fpa(env):
    c = _login_admin(env)
    c.post("/admin/voice/matchmaker", json={"voice_matchmaker": "MY CUSTOM MATCHMAKER"})
    lib = Library(_db_path())
    try:
        assert lib.get_setting("voice_matchmaker") == "MY CUSTOM MATCHMAKER"
        assert lib.get_setting("voice_core") == ""       # untouched
        assert lib.get_setting("voice_fpa_buddy") == ""  # untouched
    finally:
        lib.close()


# --- require_voice_setting integration ----------------------------------------

def test_matchmaker_build_system_uses_live_db_setting_not_just_default(env):
    """The Chat Matchmaker's own _build_system should pick up a saved
    voice_matchmaker override, mirroring how agent._build_system already
    does for voice_fpa_buddy."""
    lib = Library(_db_path())
    lib.set_setting("voice_core", "CUSTOM CORE")
    lib.set_setting("voice_matchmaker", "CUSTOM MATCHMAKER VOICE")
    lib.add_community("Test Community", "https://example.com", "Finance leaders",
                      "Free", ["Peer group"], approved=1)
    system = matchmaker._build_system(lib, "community")
    lib.close()
    assert "CUSTOM MATCHMAKER VOICE" in system
    assert matchmaker.VOICE_MATCHMAKER_DEFAULT not in system


def test_matchmaker_build_system_refuses_when_voice_core_empty(env):
    lib = Library(_db_path())
    lib.set_setting("voice_matchmaker", "CUSTOM MATCHMAKER VOICE")
    # voice_core deliberately left empty
    with pytest.raises(VoicePromptMissing):
        matchmaker._build_system(lib, "community")
    lib.close()


def test_agent_build_system_refuses_when_voice_fpa_buddy_empty(env):
    lib = Library(_db_path())
    lib.set_setting("voice_core", "CUSTOM CORE")
    # voice_fpa_buddy deliberately left empty
    with pytest.raises(VoicePromptMissing):
        agent._build_system(True, False, False, lib)
    lib.close()


# --- Review endpoint -----------------------------------------------------------

def test_review_endpoint_requires_text(env):
    lib = Library(_db_path())
    lib.seed_voice_prompts()
    lib.close()
    c = _login_admin(env)
    assert c.post("/admin/voice/review", json={"text": ""}).status_code == 400


def test_review_refuses_503_when_voice_core_unseeded(env):
    c = _login_admin(env)
    r = c.post("/admin/voice/review", json={"text": "some draft copy", "rubric": "general"})
    assert r.status_code == 503


def test_review_rubric_general_uses_core_alone(env, monkeypatch):
    captured = {}

    def fake_review_text(text, voice_prompt=None, model=None):
        captured["voice_prompt"] = voice_prompt
        return {"mechanical": [], "review": "stub", "ok": True}

    monkeypatch.setattr(voice_review, "review_text", fake_review_text)
    lib = Library(_db_path())
    lib.seed_voice_prompts()
    lib.close()
    c = _login_admin(env)
    c.post("/admin/voice/review", json={"text": "some draft copy", "rubric": "general"})
    assert captured["voice_prompt"] == agent.VOICE_CORE_DEFAULT


def test_review_rubric_fpa_buddy_composes_core_plus_fpa(env, monkeypatch):
    captured = {}

    def fake_review_text(text, voice_prompt=None, model=None):
        captured["voice_prompt"] = voice_prompt
        return {"mechanical": [], "review": "stub", "ok": True}

    monkeypatch.setattr(voice_review, "review_text", fake_review_text)
    lib = Library(_db_path())
    lib.seed_voice_prompts()
    lib.close()
    c = _login_admin(env)
    c.post("/admin/voice/review", json={"text": "an FP&A Buddy answer", "rubric": "fpa_buddy"})
    assert captured["voice_prompt"] == f"{agent.VOICE_CORE_DEFAULT}\n\n{agent.VOICE_FPA_BUDDY_DEFAULT}"


def test_review_rubric_matchmaker_composes_core_plus_matchmaker(env, monkeypatch):
    captured = {}

    def fake_review_text(text, voice_prompt=None, model=None):
        captured["voice_prompt"] = voice_prompt
        return {"mechanical": [], "review": "stub", "ok": True}

    monkeypatch.setattr(voice_review, "review_text", fake_review_text)
    lib = Library(_db_path())
    lib.seed_voice_prompts()
    lib.close()
    c = _login_admin(env)
    c.post("/admin/voice/review", json={"text": "a matchmaker suggestion", "rubric": "matchmaker"})
    assert captured["voice_prompt"] == f"{agent.VOICE_CORE_DEFAULT}\n\n{matchmaker.VOICE_MATCHMAKER_DEFAULT}"


def test_review_rubric_reads_live_db_settings_not_just_defaults(env, monkeypatch):
    captured = {}

    def fake_review_text(text, voice_prompt=None, model=None):
        captured["voice_prompt"] = voice_prompt
        return {"mechanical": [], "review": "stub", "ok": True}

    monkeypatch.setattr(voice_review, "review_text", fake_review_text)
    lib = Library(_db_path())
    lib.seed_voice_prompts()
    lib.close()
    c = _login_admin(env)
    c.post("/admin/voice/core", json={"voice_core": "CUSTOM CORE FOR REVIEW"})
    c.post("/admin/voice/review", json={"text": "copy", "rubric": "fpa_buddy"})
    assert captured["voice_prompt"] == f"CUSTOM CORE FOR REVIEW\n\n{agent.VOICE_FPA_BUDDY_DEFAULT}"
