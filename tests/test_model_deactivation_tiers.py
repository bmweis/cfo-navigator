"""PR 3b-2: deactivation blocks until a replacement is chosen for every role,
applies atomically, is a flag not a delete; Buddy tier changes need a
confirmation with the cost before and after; the Buddy path follows the role."""
import os
import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import pricing
from linklib.db import Library


@pytest.fixture
def lib(tmp_path, monkeypatch):
    monkeypatch.setenv("LINKLIB_DB", str(tmp_path / "l.db"))
    pricing.reset_price_cache()
    l = Library(str(tmp_path / "l.db"))
    l.seed_model_catalog(); l.seed_model_roles()
    yield l
    l.close()


def test_deactivation_blocks_without_a_replacement_for_each_role(lib):
    lib.set_role_model("matchmaker", "claude-sonnet-5")
    assert lib.set_role_model("enrichment", "claude-sonnet-5") == []
    problems = lib.deactivate_model("claude-sonnet-5", {"enrichment": "claude-opus-5-5"})
    assert problems == ["Matchmaker needs a replacement"]
    assert lib.get_model_status("claude-sonnet-5") == "available"      # nothing applied
    assert lib.get_enrich_model() == "claude-sonnet-5"                 # not even the valid half


def test_deactivation_refuses_a_replacement_that_is_not_allowed_and_writes_nothing(lib):
    lib.set_role_model("matchmaker", "claude-sonnet-5")
    lib.set_role_model("enrichment", "claude-sonnet-5")
    problems = lib.deactivate_model("claude-sonnet-5", {"enrichment": "claude-opus-5-5",
                                                        "matchmaker": "claude-fable-5-1"})
    assert any("Matchmaker" in p for p in problems)
    assert lib.get_enrich_model() == "claude-sonnet-5" and lib.get_model_status("claude-sonnet-5") == "available"


def test_deactivation_applies_everything_in_one_go_and_keeps_pricing(lib):
    lib.set_role_model("matchmaker", "claude-sonnet-5")
    lib.set_role_model("enrichment", "claude-sonnet-5")
    assert lib.deactivate_model("claude-sonnet-5", {"enrichment": "claude-opus-5-5",
                                                    "matchmaker": "claude-sonnet-4-6"}) == []
    assert lib.get_enrich_model() == "claude-opus-5-5"
    assert lib.get_role_model("matchmaker") == "claude-sonnet-4-6"
    assert lib.get_model_status("claude-sonnet-5") == "deactivated"
    assert lib.get_model_pricing("claude-sonnet-5") is not None        # a flag, never a delete
    assert lib.model_enable_problems("claude-sonnet-5") == ["it is deactivated"]


def test_unused_model_deactivates_with_no_replacements(lib):
    assert lib.deactivate_model("claude-sonnet-5", {}) == []
    assert lib.get_model_status("claude-sonnet-5") == "deactivated"


def test_deactivated_replacement_is_refused_and_background_never_deactivates(lib, monkeypatch):
    lib.set_model_status("claude-sonnet-4-6", "deactivated")
    lib.set_role_model("matchmaker", "claude-sonnet-5")
    assert lib.deactivate_model("claude-sonnet-5", {"matchmaker": "claude-sonnet-4-6"})
    from linklib import lineup, models
    monkeypatch.setattr(models, "_live_models", lambda: {})
    lineup.check_lineup(lib)                                           # "not live" never deactivates
    assert lib.get_model_status("claude-opus-5-5") == "available"


def test_typical_tokens_need_history_else_profile(lib):
    assert lib.typical_tier_tokens("standard") is None
    uid = lib.create_user("u1", "supersecret", role="user")
    for i in range(10):
        lib.conn.execute("INSERT INTO ask_questions(user_id, question, answer, effort, turn_index, input_tokens, output_tokens, created_at) "
                         "VALUES (?, 'q','a','standard',0,1000,400,'2026-10-01')", (uid,))
    lib.conn.commit()
    assert lib.typical_tier_tokens("standard") == (1000, 400)


def test_buddy_roles_follow_the_tier_default_until_changed(lib):
    from linklib.agent import EFFORT_SETTINGS
    for t in ("quick", "standard", "deep"):
        assert lib.get_role_model(f"buddy_{t}") == EFFORT_SETTINGS[t]["model"]
    assert lib.set_role_model("buddy_standard", "claude-sonnet-5") == []
    assert lib.get_role_model("buddy_standard") == "claude-sonnet-5"


def test_ask_orchestrator_passes_the_tier_role_model(lib, monkeypatch):
    lib.set_role_model("buddy_quick", "claude-sonnet-5")
    import linklib.agent as agent
    import webapp.ask_orchestrator as orch
    seen = {}
    def fake(lib_, q, **kw):
        seen.update(kw)
        raise RuntimeError("stop")
    monkeypatch.setattr(agent, "answer_question", fake)
    with pytest.raises(RuntimeError):
        orch.run_ask(lib, user_id=None, question="q", effort="quick")
    assert seen["model"] == "claude-sonnet-5"


@pytest.fixture
def client(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "pw")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    l = Library(db); l.seed_model_catalog(); l.seed_model_roles(); l.close()
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "admin", "password": "pw"}, follow_redirects=False)
    yield c, db
    if os.path.exists(db):
        os.remove(db)


def test_tier_change_requires_confirmation_and_previews_before_and_after(client):
    c, db = client
    r = c.post("/admin/system/ai/model/save", json={"role": "buddy_standard", "model": "claude-sonnet-5"})
    assert r.status_code == 400 and "Confirm" in r.json()["error"]
    p = c.post("/admin/system/ai/buddy-tier/preview", json={"tier": "standard", "model": "claude-sonnet-5"}).json()
    assert p["ok"] and "Estimated cost per question $0.028 before, $0.019 after." in p["summary"]
    r = c.post("/admin/system/ai/model/save", json={"role": "buddy_standard", "model": "claude-sonnet-5", "confirm": True})
    assert r.status_code == 200


def test_tier_preview_refuses_opus_5_5_with_the_visible_reason(client):
    c, db = client
    p = c.post("/admin/system/ai/buddy-tier/preview", json={"tier": "deep", "model": "claude-opus-5-5"}).json()
    assert not p["ok"] and "Buddy not yet checked for always-on thinking" in p["error"]


def test_deactivate_page_and_post_round_trip(client):
    c, db = client
    c.post("/admin/system/ai/model/save", json={"role": "matchmaker", "model": "claude-sonnet-5"})
    page = c.get("/admin/system/ai/deactivate/claude-sonnet-5").text
    assert "Matchmaker" in page and "Choose a replacement" in page
    r = c.post("/admin/system/ai/deactivate/claude-sonnet-5", data={}, follow_redirects=False)
    assert r.status_code == 303 and "error=" in r.headers["location"]
    r = c.post("/admin/system/ai/deactivate/claude-sonnet-5", data={"role_matchmaker": "claude-sonnet-4-6"}, follow_redirects=False)
    assert r.headers["location"].endswith("#model-pricing")
    l = Library(db)
    assert l.get_model_status("claude-sonnet-5") == "deactivated" and l.get_role_model("matchmaker") == "claude-sonnet-4-6"
    l.close()
    c.post("/admin/system/ai/reactivate/claude-sonnet-5")
    l = Library(db)
    assert l.get_model_status("claude-sonnet-5") == "available"
    l.close()


def test_buddy_page_estimate_follows_the_role_model(client):
    c, db = client
    c.post("/admin/system/ai/model/save", json={"role": "buddy_standard", "model": "claude-sonnet-5", "confirm": True})
    html = c.get("/tools/fpa-buddy").text
    import re; assert re.search(r"var COST = \{[^}]*\"standard\": 0\.0187", html)
