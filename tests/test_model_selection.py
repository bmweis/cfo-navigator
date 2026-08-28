"""AI model selection (2026-08) — a live, DB-backed choice of which Claude
model powers linklib.enrich's generation calls, same on/off-toggle pattern
as /admin/exa-settings' Phase 7 kill switch (Library.get_exa_enabled), just
a model id instead of a boolean. Covers: Library.get_enrich_model/
set_enrich_model (round trip + fallback), the /admin/system/model page and
its save/test-connection routes, and that generation call sites actually
resolve the DB-stored model rather than a hardcoded default.
"""
import os
import sys
import pathlib
import tempfile
import types

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Library


@pytest.fixture
def lib(tmp_path):
    db = str(tmp_path / "library.db")
    library = Library(db)
    yield library
    library.close()


def test_get_enrich_model_falls_back_to_default_when_unset(lib):
    from linklib.enrich import DEFAULT_MODEL
    assert lib.get_enrich_model() == DEFAULT_MODEL


def test_set_and_get_enrich_model_round_trips(lib):
    lib.set_enrich_model("claude-haiku-4-5-20251001")
    assert lib.get_enrich_model() == "claude-haiku-4-5-20251001"


def test_set_enrich_model_strips_whitespace(lib):
    lib.set_enrich_model("  claude-sonnet-5  ")
    assert lib.get_enrich_model() == "claude-sonnet-5"


def test_enrich_default_model_is_a_valid_current_registry_id():
    """Regression guard: the hardcoded default must be a real id in the
    curated registry, not something that merely looks plausible. (This
    build briefly "fixed" a real, current model id — claude-opus-5, per
    Anthropic's own docs — into an older one on a mistaken assumption;
    corrected back. The point of this test stands either way: whatever the
    default is, it must actually be in the registry.)"""
    from linklib.enrich import DEFAULT_MODEL
    from linklib.models import _REGISTRY
    assert DEFAULT_MODEL in {m["id"] for m in _REGISTRY}
    assert DEFAULT_MODEL == "claude-opus-5"


# -- admin page + routes -----------------------------------------------------

@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    # 2026-08 visibility follow-up: the generate-description route now
    # refuses (require_voice_setting) unless voice_core is seeded.
    _seed_lib = Library(db)
    _seed_lib.seed_voice_prompts()
    _seed_lib.close()
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    yield appmod
    if os.path.exists(db):
        os.remove(db)


def _client(appmod):
    from fastapi.testclient import TestClient
    return TestClient(appmod.app, raise_server_exceptions=True)


def _login(client):
    r = client.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    assert r.status_code in (302, 303)


def test_model_settings_page_requires_login(env):
    r = _client(env).get("/admin/system/model", follow_redirects=False)
    assert r.status_code in (302, 303)


def test_model_settings_page_shows_default_selected(env):
    client = _client(env)
    _login(client)
    r = client.get("/admin/system/model")
    assert r.status_code == 200
    from linklib.enrich import DEFAULT_MODEL
    assert f'value="{DEFAULT_MODEL}" selected' in r.text


def test_model_settings_save_persists_selection(env):
    client = _client(env)
    _login(client)
    r = client.post("/admin/system/model/save", json={"model": "claude-sonnet-5"})
    assert r.status_code == 200
    assert r.json()["ok"] is True

    from linklib.db import Library
    lib_ = Library(os.environ["LINKLIB_DB"])
    saved = lib_.get_enrich_model()
    lib_.close()
    assert saved == "claude-sonnet-5"


def test_model_settings_save_rejects_empty(env):
    client = _client(env)
    _login(client)
    r = client.post("/admin/system/model/save", json={"model": ""})
    assert r.status_code == 400


def test_model_settings_requires_auth_to_save(env):
    r = _client(env).post("/admin/system/model/save", json={"model": "claude-sonnet-5"})
    assert r.status_code == 401


def test_model_settings_page_surfaces_a_selection_not_in_the_registry(env):
    """A previously-saved model that's since been retired from the curated
    registry still shows in the dropdown, labeled plainly, rather than being
    silently dropped (which would make the page lie about what's running)."""
    from linklib.db import Library
    lib_ = Library(os.environ["LINKLIB_DB"])
    lib_.set_enrich_model("claude-some-retired-model")
    lib_.close()

    client = _client(env)
    _login(client)
    r = client.get("/admin/system/model")
    assert 'value="claude-some-retired-model" selected' in r.text
    assert "no longer in the curated list" in r.text


def test_model_test_connection_reports_missing_key(env, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client = _client(env)
    _login(client)
    r = client.post("/admin/system/model/test-connection", json={"model": "claude-sonnet-5"})
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is False
    assert "ANTHROPIC_API_KEY" in d["error"]


def test_model_test_connection_success(env, monkeypatch):
    def _create(**kw):
        class _Block:
            type = "text"
            text = "ok"
        usage = types.SimpleNamespace(
            input_tokens=10, output_tokens=1,
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
        )
        return types.SimpleNamespace(content=[_Block()], usage=usage)
    fake = types.SimpleNamespace(Anthropic=lambda *a, **k: types.SimpleNamespace(
        messages=types.SimpleNamespace(create=lambda **kw: _create(**kw))))
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")

    client = _client(env)
    _login(client)
    r = client.post("/admin/system/model/test-connection", json={"model": "claude-sonnet-5"})
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True
    assert d["cost_usd"] >= 0


def test_overhead_spend_page_surfaces_active_model(env):
    from linklib.db import Library
    lib_ = Library(os.environ["LINKLIB_DB"])
    lib_.set_enrich_model("claude-sonnet-5")
    lib_.close()

    client = _client(env)
    _login(client)
    r = client.get("/admin/overhead-spend")
    assert r.status_code == 200
    assert "Active enrichment model" in r.text
    assert "Sonnet 5" in r.text or "claude-sonnet-5" in r.text


# -- generation call sites resolve the DB-stored model -----------------------

def test_generate_description_route_uses_stored_model(env, monkeypatch):
    from linklib.db import Library
    lib_ = Library(os.environ["LINKLIB_DB"])
    lib_.set_enrich_model("claude-sonnet-5")
    lib_.close()

    captured = {}

    def _fake_generate_tool_description(name, url, model=None, voice_core=""):
        captured["model"] = model
        from linklib.enrich import ToolDescriptionDraft
        return ToolDescriptionDraft(description="D", summary="S", model=model,
                                    input_tokens=1, output_tokens=1, cost_usd=0.0)

    monkeypatch.setattr("linklib.enrich.generate_tool_description", _fake_generate_tool_description)

    client = _client(env)
    _login(client)
    r = client.post("/admin/tools/software/generate-description",
                     json={"name": "Runway", "url": "https://runway.com"})
    assert r.status_code == 200
    assert captured["model"] == "claude-sonnet-5"
