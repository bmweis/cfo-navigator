"""PR 3b-1: role assignments, allowed roles enforced at save, Matchmaker seeded
from LINKLIB_CHAT_MODEL once, Fable notes, and the Buddy block with a visible reason."""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import pricing
from linklib.db import Library
from linklib.models import BUDDY_BLOCK_REASON, DEFAULT_CHAT_MODEL


@pytest.fixture
def lib(tmp_path, monkeypatch):
    monkeypatch.setenv("LINKLIB_DB", str(tmp_path / "l.db"))
    monkeypatch.delenv("LINKLIB_CHAT_MODEL", raising=False)
    pricing.reset_price_cache()
    l = Library(str(tmp_path / "l.db"))
    yield l
    l.close()


def _verify(lib, mid):
    row = lib.get_model_pricing(mid)
    rates = {k: (row[k] if row[k] is not None else 1.0) for k in Library._PRICE_FIELDS}
    lib.set_model_pricing(mid, rates, verified=True)


def test_matchmaker_is_seeded_from_the_env_var_once_then_the_var_is_ignored(lib, monkeypatch):
    monkeypatch.setenv("LINKLIB_CHAT_MODEL", "claude-sonnet-5")
    lib.seed_model_catalog(); lib.seed_model_roles()
    assert lib.get_role_model("matchmaker") == "claude-sonnet-5"
    monkeypatch.setenv("LINKLIB_CHAT_MODEL", "claude-haiku-4-5-20251001")
    lib.set_setting("model_roles_seeded", "")
    lib.seed_model_roles()                         # even a re-run never overwrites
    assert lib.get_role_model("matchmaker") == "claude-sonnet-5"


def test_unseeded_matchmaker_falls_back_to_the_code_default(lib):
    assert lib.get_role_model("matchmaker") == DEFAULT_CHAT_MODEL


def test_allowed_roles_defaults_per_brief(lib):
    lib.seed_model_catalog(); lib.seed_model_roles()
    assert lib.model_allowed_roles("claude-fable-5-1") == []
    assert lib.model_allowed_roles("claude-fable-5") == []
    assert lib.model_allowed_roles("claude-sonnet-5-5") == []
    assert lib.model_allowed_roles("claude-opus-5-5") == ["enrichment"]
    assert lib.model_allowed_roles("claude-opus-5-5") == ["enrichment"]
    assert "buddy_quick" not in lib.model_allowed_roles("claude-opus-5-5")
    assert "buddy_deep" in lib.model_allowed_roles("claude-sonnet-4-6")


def test_role_refused_when_not_allowed_even_with_verified_pricing(lib):
    lib.seed_model_catalog(); lib.seed_model_roles()
    _verify(lib, "claude-fable-5-1")
    problems = lib.set_role_model("matchmaker", "claude-fable-5-1")
    assert any("not allowed for Matchmaker" in p for p in problems)
    assert lib.get_role_model("matchmaker") != "claude-fable-5-1"
    from linklib.models import ENRICHMENT_BLOCK_REASON
    problems = lib.set_role_model("enrichment", "claude-fable-5-1")
    assert any(ENRICHMENT_BLOCK_REASON in p for p in problems)
    assert lib.get_enrich_model() != "claude-fable-5-1"


def test_buddy_tiers_refuse_opus_5_5_with_the_visible_reason(lib):
    lib.seed_model_catalog(); lib.seed_model_roles()
    problems = lib.set_role_model("buddy_deep", "claude-opus-5-5")
    assert any(BUDDY_BLOCK_REASON in p for p in problems)
    assert lib.get_role_model("buddy_deep") == "claude-opus-4-8"       # tiers unchanged


def test_matchmaker_refuses_always_on_thinking_models_with_a_visible_reason(lib):
    from linklib.models import MATCHMAKER_BLOCK_REASON
    lib.seed_model_catalog(); lib.seed_model_roles()
    for mid in ("claude-opus-5-5", "claude-sonnet-5-5"):
        _verify(lib, mid)
        problems = lib.set_role_model("matchmaker", mid)
        assert any(MATCHMAKER_BLOCK_REASON in p for p in problems), mid
    assert lib.get_role_model("matchmaker") == DEFAULT_CHAT_MODEL
    _verify(lib, "claude-opus-5-5")
    assert lib.set_role_model("enrichment", "claude-opus-5-5") == []     # Opus 5.5 stays allowed for enrichment


def test_unverified_pricing_still_refuses_an_allowed_model(lib):
    lib.seed_model_catalog(); lib.seed_model_roles()
    problems = lib.set_role_model("enrichment", "claude-sonnet-5-5")
    assert any("verified date" in p for p in problems)           # pricing is still reported
    from linklib.models import ENRICHMENT_BLOCK_REASON
    assert any(ENRICHMENT_BLOCK_REASON in p for p in problems)   # alongside the role block


def test_not_using_and_deactivated_models_cannot_take_a_role(lib):
    lib.seed_model_catalog(); lib.seed_model_roles()
    lib.set_model_status("claude-sonnet-5", "not_using", "no")
    assert "it is marked not using" in lib.set_role_model("matchmaker", "claude-sonnet-5")
    lib.set_model_status("claude-sonnet-4-6", "deactivated")
    assert lib.set_role_model("matchmaker", "claude-sonnet-4-6")


def test_fable_notes_seeded_with_source_and_not_overwritten(lib):
    lib.seed_model_catalog()
    lib.conn.execute("UPDATE model_catalog SET note='mine' WHERE model_id='claude-fable-5'")
    lib.conn.commit()
    lib.seed_model_roles()
    notes = {r["model_id"]: r["note"] for r in lib.list_model_catalog()}
    assert notes["claude-fable-5"] == "mine"
    assert "30-day" in notes["claude-fable-5-1"] and "not confirmed against the live page" in notes["claude-fable-5-1"]


def test_roles_seed_never_changes_a_status(lib):
    lib.seed_model_catalog()
    lib.set_model_status("claude-opus-5", "deactivated")
    lib.seed_model_roles()
    assert lib.get_model_status("claude-opus-5") == "deactivated"


def test_matchmaker_answer_uses_the_role_model(lib, monkeypatch):
    lib.seed_model_catalog(); lib.seed_model_roles()
    lib.set_role_model("matchmaker", "claude-sonnet-5")
    from linklib import matchmaker
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    ans = matchmaker._answer(lib, "software", "q")
    assert ans.model == "claude-sonnet-5"
