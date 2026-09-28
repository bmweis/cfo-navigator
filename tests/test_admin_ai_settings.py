"""The merged /admin/system/ai page (PR 10, 2026-09 admin AI-page
consolidation) — replaces the three formerly separate pages
/admin/exa-settings, /admin/system/model, and /admin/system/ai-usage, all
three of which now 404 (no redirect, admin-only surface, nothing was
bookmarked externally — same precedent every other admin URL-restructure PR
in this codebase has used).

Covers, in one file since they're now one page:
  - Library.get_enrich_model/set_enrich_model (the underlying setting)
  - the merged page itself: renders, requires auth, shows both editable
    Configuration cards (Enrichment model, Exa web search) and the
    read-only Usage index below them
  - the four POST routes the two Configuration cards now post to
    (/admin/system/ai/model/save, /admin/system/ai/model/test-connection,
    /admin/system/ai/exa/toggle, /admin/system/ai/exa/test-connection)
  - that generation call sites actually resolve the DB-stored model
  - the three old URLs 404 signed in and signed out
  - the inverted mechanical guard (ai_config_editable_outside_ai_page):
    passes clean on the real app, and is proven to actually catch a
    planted violation, not just pass trivially
  - the merged page still shows all the confirmed facts from the old
    dashboard's own coverage (all three Claude surfaces, all four Exa call
    sites, the OpenAI footnote, the three freshness dots)
"""
import os
import sys
import pathlib
import tempfile
import types

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Library


# --- Library.get_enrich_model / set_enrich_model ----------------------------

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
    from linklib.enrich import DEFAULT_MODEL
    from linklib.models import _REGISTRY
    assert DEFAULT_MODEL in {m["id"] for m in _REGISTRY}
    assert DEFAULT_MODEL == "claude-opus-5"


# --- admin page + routes -----------------------------------------------------

@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    _seed_lib = Library(db)
    _seed_lib.seed_voice_prompts()
    _seed_lib.create_user("member1", "supersecret", role="user")
    _seed_lib.close()
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
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


# -- page renders / auth -----------------------------------------------------

def test_page_requires_auth(env):
    r = _client(env).get("/admin/system/ai", follow_redirects=False)
    assert r.status_code in (302, 303)
    assert "/login" in r.headers.get("location", "")


def test_page_renders_for_admin(env):
    r = _admin_client(env).get("/admin/system/ai")
    assert r.status_code == 200
    assert "AI configuration and usage" in r.text


def test_member_cannot_reach_page(env):
    r = _member_client(env).get("/admin/system/ai", follow_redirects=False)
    assert r.status_code in (302, 303)


# -- Configuration/Usage index visually and structurally separated ----------

def test_page_has_a_configuration_section_and_a_usage_index_section(env):
    body = _admin_client(env).get("/admin/system/ai").text
    assert "<h2" in body
    assert "Configuration</h2>" in body
    assert "Usage index</h2>" in body
    config_idx = body.index("Configuration</h2>")
    usage_idx = body.index("Usage index</h2>")
    assert config_idx < usage_idx


# -- Enrichment model card ----------------------------------------------------

def test_model_select_shows_default_selected(env):
    from linklib.enrich import DEFAULT_MODEL
    body = _admin_client(env).get("/admin/system/ai").text
    assert f'value="{DEFAULT_MODEL}" selected' in body


def test_model_select_surfaces_a_selection_not_in_the_registry(env):
    lib_ = Library(os.environ["LINKLIB_DB"])
    lib_.set_enrich_model("claude-some-retired-model")
    lib_.close()
    body = _admin_client(env).get("/admin/system/ai").text
    assert 'value="claude-some-retired-model" selected' in body
    assert "no longer in the curated list" in body


def test_model_save_persists_selection(env):
    client = _admin_client(env)
    r = client.post("/admin/system/ai/model/save", json={"model": "claude-sonnet-5"})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    lib_ = Library(os.environ["LINKLIB_DB"])
    saved = lib_.get_enrich_model()
    lib_.close()
    assert saved == "claude-sonnet-5"


def test_model_save_rejects_empty(env):
    client = _admin_client(env)
    r = client.post("/admin/system/ai/model/save", json={"model": ""})
    assert r.status_code == 400


def test_model_save_requires_auth(env):
    r = _client(env).post("/admin/system/ai/model/save", json={"model": "claude-sonnet-5"})
    assert r.status_code == 401


def test_model_test_connection_reports_missing_key(env, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client = _admin_client(env)
    r = client.post("/admin/system/ai/model/test-connection", json={"model": "claude-sonnet-5"})
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
    client = _admin_client(env)
    r = client.post("/admin/system/ai/model/test-connection", json={"model": "claude-sonnet-5"})
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True
    assert d["cost_usd"] >= 0


def test_generate_description_route_uses_stored_model(env, monkeypatch):
    lib_ = Library(os.environ["LINKLIB_DB"])
    lib_.set_enrich_model("claude-sonnet-5")
    lib_.close()

    captured = {}

    def _fake_generate_tool_description(name, url, model=None, voice_core="", exa_enabled=True):
        captured["model"] = model
        from linklib.enrich import ToolDescriptionDraft
        return ToolDescriptionDraft(description="D", summary="S", model=model,
                                    input_tokens=1, output_tokens=1, cost_usd=0.0)

    monkeypatch.setattr("linklib.enrich.generate_tool_description", _fake_generate_tool_description)

    client = _admin_client(env)
    r = client.post("/admin/tools/software/generate-description",
                     json={"name": "Runway", "url": "https://runway.com"})
    assert r.status_code == 200
    assert captured["model"] == "claude-sonnet-5"


def test_overhead_spend_page_surfaces_active_model_and_links_to_merged_page(env):
    lib_ = Library(os.environ["LINKLIB_DB"])
    lib_.set_enrich_model("claude-sonnet-5")
    lib_.close()
    body = _admin_client(env).get("/admin/overhead-spend").text
    assert "Active enrichment model" in body
    assert "Sonnet 5" in body or "claude-sonnet-5" in body
    assert '/admin/system/ai"' in body


# -- Exa web-search card -------------------------------------------------------

def test_exa_toggle_checked_by_default(env):
    body = _admin_client(env).get("/admin/system/ai").text
    assert 'id="exa-toggle"' in body
    assert 'id="exa-toggle" checked' in body


def test_exa_page_flags_missing_api_key(env):
    body = _admin_client(env).get("/admin/system/ai").text
    assert "is not set on this host" in body


def test_exa_page_no_key_banner_when_key_is_set(env, monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "fake-key")
    body = _admin_client(env).get("/admin/system/ai").text
    assert "is not set on this host" not in body


def test_exa_toggle_off_persists_and_reflects_on_reload(env):
    client = _admin_client(env)
    r = client.post("/admin/system/ai/exa/toggle", json={"enabled": False})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "enabled": False}
    body = client.get("/admin/system/ai").text
    assert 'id="exa-toggle" checked' not in body

    r2 = client.post("/admin/system/ai/exa/toggle", json={"enabled": True})
    assert r2.json() == {"ok": True, "enabled": True}
    body2 = client.get("/admin/system/ai").text
    assert 'id="exa-toggle" checked' in body2


def test_exa_toggle_requires_admin(env):
    member = _member_client(env)
    r = member.post("/admin/system/ai/exa/toggle", json={"enabled": False})
    assert r.status_code == 401


def test_exa_test_connection_reports_missing_key(env):
    r = _admin_client(env).post("/admin/system/ai/exa/test-connection")
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is False
    assert "EXA_API_KEY" in d["error"]


def test_exa_test_connection_requires_admin(env):
    member = _member_client(env)
    r = member.post("/admin/system/ai/exa/test-connection")
    assert r.status_code == 401


def test_exa_test_connection_success_path(env, monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "fake-key")

    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"results": [{"url": "https://x.com"}]}

    import linklib.agent as agent_mod
    monkeypatch.setattr(agent_mod.requests, "post", lambda *a, **k: _FakeResponse())

    r = _admin_client(env).post("/admin/system/ai/exa/test-connection")
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True
    assert d["cost_usd"] > 0


def test_exa_toggle_status_reflects_live_setting_in_usage_index(env):
    lib_ = Library(os.environ["LINKLIB_DB"])
    lib_.set_exa_enabled(False)
    lib_.close()
    body = _admin_client(env).get("/admin/system/ai").text
    assert "Off" in body


# -- Usage index (read-only) content, carried over from the old dashboard ---

def test_claude_section_shows_all_three_surfaces_and_their_current_models(env):
    from linklib.agent import EFFORT_SETTINGS
    from linklib.models import DEFAULT_CHAT_MODEL

    body = _admin_client(env).get("/admin/system/ai").text
    assert "claude-opus-5" in body
    assert EFFORT_SETTINGS["quick"]["model"] in body
    assert EFFORT_SETTINGS["standard"]["model"] in body
    assert EFFORT_SETTINGS["deep"]["model"] in body
    assert DEFAULT_CHAT_MODEL in body
    assert "Live" in body
    assert "Code-only" in body


def test_enrichment_model_in_usage_index_reflects_a_changed_setting(env):
    lib_ = Library(os.environ["LINKLIB_DB"])
    lib_.set_enrich_model("claude-sonnet-5")
    lib_.close()
    body = _admin_client(env).get("/admin/system/ai").text
    assert "claude-sonnet-5" in body


def test_exa_section_lists_all_four_call_sites(env):
    body = _admin_client(env).get("/admin/system/ai").text
    assert "FP&amp;A Buddy web tier" in body
    assert "domain migration" in body.lower()
    assert "Medium-platform" in body
    assert "Feature Taxonomy vendor research" in body


def test_exa_feature_scan_call_site_is_explicitly_not_tracked_in_a_table(env):
    body = _admin_client(env).get("/admin/system/ai").text
    assert "Not tracked in a database table like the three above" in body
    assert "enrich_agent_taxonomy.py" in body
    assert "ask_questions" in body
    assert "content_refetch_log" in body


def test_openai_section_present(env):
    body = _admin_client(env).get("/admin/system/ai").text
    assert "text-embedding-3-small" in body
    assert "article_embeddings.cost_usd" in body
    assert "ask_questions.embed_cost_usd" in body


def test_links_out_to_overhead_spend_and_checks_freshness_anchors(env):
    body = _admin_client(env).get("/admin/system/ai").text
    assert '/admin/checks#pricing-freshness' in body
    assert '/admin/checks#new-model-awareness' in body
    assert '/admin/checks#exa-pricing-freshness' in body
    assert '/admin/overhead-spend' in body


def test_freshness_dots_link_to_checks_page_anchors(env):
    checks_html = _admin_client(env).get("/admin/checks").text
    assert 'id="pricing-freshness"' in checks_html
    assert 'id="new-model-awareness"' in checks_html
    assert 'id="exa-pricing-freshness"' in checks_html


def test_freshness_dot_shows_never_reviewed_by_default(env):
    body = _admin_client(env).get("/admin/system/ai").text
    assert "never reviewed" in body.lower()


def test_freshness_dot_reflects_a_recent_mark_reviewed(env):
    client = _admin_client(env)
    before = client.get("/admin/system/ai").text
    assert "Pricing: never reviewed" in before
    client.post("/admin/checks/mark-pricing-reviewed", follow_redirects=False)
    after = client.get("/admin/system/ai").text
    assert "Pricing: never reviewed" not in after
    assert "reviewed just now" in after.lower()


# -- the three old pages are fully retired -----------------------------------

@pytest.mark.parametrize("path", [
    "/admin/exa-settings",
    "/admin/system/model",
    "/admin/system/ai-usage",
])
def test_old_page_404s_signed_out(env, path):
    r = _client(env).get(path, follow_redirects=False)
    assert r.status_code == 404


@pytest.mark.parametrize("path", [
    "/admin/exa-settings",
    "/admin/system/model",
    "/admin/system/ai-usage",
])
def test_old_page_404s_signed_in_as_admin(env, path):
    r = _admin_client(env).get(path, follow_redirects=False)
    assert r.status_code == 404


@pytest.mark.parametrize("path,method", [
    ("/admin/exa-settings/toggle", "post"),
    ("/admin/exa-settings/test-connection", "post"),
    ("/admin/system/model/save", "post"),
    ("/admin/system/model/test-connection", "post"),
])
def test_old_post_routes_404(env, path, method):
    client = _admin_client(env)
    r = getattr(client, method)(path)
    assert r.status_code == 404


# -- hub-nav: one card replaces the three ------------------------------------

def test_admin_nav_links_to_the_merged_page_and_not_the_old_three(env):
    body = _admin_client(env).get("/admin").text
    assert "/admin/system/ai\"" in body
    assert "/admin/exa-settings" not in body
    assert "/admin/system/model\"" not in body
    assert "/admin/system/ai-usage\"" not in body


def test_fpa_buddy_tools_no_longer_carries_exa_settings(env):
    hrefs = [href for href, _, _ in env._FPA_BUDDY_TOOLS]
    assert "/admin/exa-settings" not in hrefs


def test_system_group_has_exactly_one_ai_card(env):
    """System split into Configuration/Health and maintenance (PR 11,
    2026-09) — AI configuration and usage landed in Configuration."""
    config_items = next(items for gname, _, items in env._ADMIN_GROUPS if gname == "Configuration")
    hrefs = [href for href, _, _ in config_items]
    assert hrefs.count("/admin/system/ai") == 1
    assert "/admin/system/model" not in hrefs
    assert "/admin/system/ai-usage" not in hrefs
    all_hrefs = [href for _, _, items in env._ADMIN_GROUPS for href, _, _ in items]
    assert all_hrefs.count("/admin/system/ai") == 1


# -- inverted guard: proven to catch a real violation, not just pass --------

def test_ai_config_guard_passes_clean_on_the_real_app(env):
    assert env.ai_config_editable_outside_ai_page() == []


def test_ai_config_guard_actually_fails_on_a_planted_violation(env):
    """Not just "the guard returns []" — plant a route reusing one of the
    three retired URL shapes, outside /admin/system/ai, and confirm the
    guard actually flags it. Removed again immediately so this test can't
    leak a route into any test that runs after it in the same process."""
    def _dummy():
        return "sneaky"
    env.app.add_api_route("/admin/exa-settings/sneaky", _dummy, methods=["GET"])
    try:
        assert "/admin/exa-settings/sneaky" in env.ai_config_editable_outside_ai_page()
    finally:
        env.app.router.routes = [r for r in env.app.router.routes if getattr(r, "path", None) != "/admin/exa-settings/sneaky"]


def test_ai_config_guard_wired_into_checks(env):
    from webapp import checks
    results = checks.run_all()
    row = next(r for r in results if r["name"] == "AI config consolidated")
    assert row["ok"] is True


def test_hub_nav_orphans_clean_after_the_merge(env):
    assert env.hub_nav_orphans() == []
