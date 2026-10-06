"""PR 3a: model_pricing + model_catalog (status), seeding, the DB-backed
compute_cost, the save-time refusal, and the rule that only an admin action
ever changes a status."""
import json
import logging
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import pricing
from linklib.db import Library


@pytest.fixture
def lib(tmp_path, monkeypatch):
    db = str(tmp_path / "library.db")
    monkeypatch.setenv("LINKLIB_DB", db)
    pricing.reset_price_cache()
    library = Library(db)
    yield library
    library.close()
    pricing.reset_price_cache()


# -- migration: existing "not using" entries and reasons survive ----------------

def test_migration_carries_every_existing_not_using_entry_and_reason(lib):
    legacy = [
        {"id": "claude-fable-5", "reason": "Too costly for enrichment", "added_at": "2026-10-01T00:00:00+00:00"},
        {"id": "claude-opus-4-7", "reason": "Superseded by Opus 5.5", "added_at": "2026-10-02T00:00:00+00:00"},
        {"id": "claude-sonnet-4-5", "reason": "Old generation", "added_at": "2026-10-02T00:00:00+00:00"},
    ]
    lib.set_setting("models_not_using", json.dumps(legacy))
    assert lib.seed_model_catalog()["seeded"] is True
    got = {e["id"]: e["reason"] for e in lib.list_models_not_using()}
    assert got == {e["id"]: e["reason"] for e in legacy}
    # a legacy id that is also a registry model is not left 'available'
    assert lib.get_model_status("claude-fable-5") == "not_using"
    # an id that was never in the registry gets a catalog row too
    assert lib.get_model_status("claude-opus-4-7") == "not_using"


def test_seed_runs_once_and_never_overwrites_an_edited_row(lib):
    lib.seed_model_catalog()
    lib.set_model_pricing("claude-opus-5", {"input": 1, "output": 2, "cache_write": 3,
                                            "cache_write_1h": 4, "cache_read": 5}, verified=True)
    lib.set_model_status("claude-sonnet-5", "deactivated")
    lib.set_setting("model_catalog_seeded", "")          # even a re-run must not clobber
    lib.seed_model_catalog()
    assert lib.get_model_pricing("claude-opus-5")["input"] == 1.0
    assert lib.get_model_status("claude-sonnet-5") == "deactivated"


def test_seed_does_not_resurrect_a_removed_row_once_flagged(lib):
    lib.seed_model_catalog()
    lib.conn.execute("DELETE FROM model_catalog WHERE model_id='claude-sonnet-4-6'")
    lib.conn.commit()
    assert lib.seed_model_catalog()["seeded"] is False
    assert lib.get_model_status("claude-sonnet-4-6") is None


def test_seed_rows_verified_on_and_new_models_unverified(lib):
    lib.seed_model_catalog()
    for mid in ("claude-opus-5-5", "claude-sonnet-4-6", "claude-haiku-4-5-20251001"):
        assert lib.get_model_pricing(mid)["verified_on"] == "2026-09-28"
    for mid in ("claude-fable-5", "claude-fable-5-1", "claude-sonnet-5-5"):
        row = lib.get_model_pricing(mid)
        assert row["verified_on"] == ""
        assert "not confirmed against the live page" in row["source_note"]
        assert row["cache_write"] is None and row["cache_write_1h"] is None
    assert lib.get_model_pricing("claude-fable-5")["cache_read"] is None
    assert lib.get_model_pricing("claude-fable-5-1")["cache_read"] == 0.25


# -- refusal ---------------------------------------------------------------------

def test_incomplete_or_unverified_pricing_cannot_be_enabled(lib):
    lib.seed_model_catalog()
    problems = lib.model_enable_problems("claude-fable-5-1")
    assert "missing cache write rate" in problems and "missing verified date" in problems
    assert lib.model_enable_problems("claude-opus-5-5") == []
    # complete rates but never verified is still refused
    lib.set_model_pricing("claude-fable-5-1", {"input": 10, "output": 50, "cache_write": 12.5,
                                               "cache_write_1h": 20, "cache_read": 0.25}, verified=False)
    assert lib.model_enable_problems("claude-fable-5-1") == ["missing verified date"]
    # verified makes it enableable
    lib.set_model_pricing("claude-fable-5-1", {"input": 10, "output": 50, "cache_write": 12.5,
                                               "cache_write_1h": 20, "cache_read": 0.25}, verified=True)
    assert lib.model_enable_problems("claude-fable-5-1") == []


def test_deactivated_model_cannot_be_enabled(lib):
    lib.seed_model_catalog()
    lib.set_model_status("claude-opus-5-5", "deactivated")
    assert lib.model_enable_problems("claude-opus-5-5") == ["it is deactivated"]


def test_editing_rates_without_verifying_clears_the_verified_date(lib):
    lib.seed_model_catalog()
    row = lib.get_model_pricing("claude-opus-5-5")
    lib.set_model_pricing("claude-opus-5-5", {k: row[k] for k in Library._PRICE_FIELDS}, verified=False)
    assert lib.get_model_pricing("claude-opus-5-5")["verified_on"] == ""


# -- only an admin action changes status -------------------------------------------

def test_seeding_and_background_passes_never_change_a_status(lib, monkeypatch):
    lib.seed_model_catalog()
    lib.set_model_status("claude-opus-5", "deactivated")
    lib.set_model_status("claude-sonnet-5", "not_using", "reason")
    before = {r["model_id"]: (r["status"], r["reason"]) for r in lib.list_model_catalog()}
    from linklib import lineup, models
    monkeypatch.setattr(models, "_live_models", lambda: {"claude-brand-new": {"name": "X", "created": None}})
    lineup.check_lineup(lib)                 # the lineup check
    lib.seed_model_catalog()                 # a re-seed
    lib.pricing_freshness()
    assert {r["model_id"]: (r["status"], r["reason"]) for r in lib.list_model_catalog()
            if r["model_id"] in before} == before
    # a model reported "not live" is flagged, never auto-deactivated
    monkeypatch.setattr(models, "_live_models", lambda: {"claude-brand-new": {"name": "X", "created": None}})
    lineup.check_lineup(lib)
    assert lib.get_model_status("claude-opus-5-5") == "available"


def test_not_using_requires_a_reason(lib):
    assert lib.add_model_not_using("claude-x", "") is False
    assert lib.add_model_not_using("claude-x", "because") is True
    lib.remove_model_not_using("claude-x")
    assert lib.get_model_status("claude-x") == "available"


# -- compute_cost reads the table --------------------------------------------------

def test_compute_cost_reads_the_edited_db_row_and_cache_resets_on_edit(lib):
    lib.seed_model_catalog()
    base = pricing.compute_cost("claude-opus-5-5", 1_000_000, 0)
    assert base == pytest.approx(4.0)
    lib.set_model_pricing("claude-opus-5-5", {"input": 9, "output": 20, "cache_write": 5,
                                              "cache_write_1h": 8, "cache_read": 0.2}, verified=True)
    assert pricing.compute_cost("claude-opus-5-5", 1_000_000, 0) == pytest.approx(9.0)


def test_incomplete_db_row_falls_back_to_dict_then_sonnet_with_a_warning(lib, caplog):
    lib.seed_model_catalog()
    caplog.set_level(logging.WARNING, logger="linklib.pricing")
    # Fable 5.1 has an incomplete row and is not in the code dict: Sonnet 4.6 rates, loud warning
    cost = pricing.compute_cost("claude-fable-5-1", 1_000_000, 0)
    assert cost == pytest.approx(pricing.MODEL_PRICING["claude-sonnet-4-6"]["input"])
    assert any("claude-fable-5-1" in r.message for r in caplog.records)


def test_no_db_available_uses_the_code_dict(monkeypatch):
    monkeypatch.setenv("LINKLIB_DB", "/nonexistent/x.db")
    pricing.reset_price_cache()
    assert pricing.compute_cost("claude-opus-5", 1_000_000, 0) == pytest.approx(5.0)


# -- row staleness -----------------------------------------------------------------

def test_row_pricing_state():
    from datetime import datetime, timezone
    now = datetime(2026, 10, 5, tzinfo=timezone.utc)
    assert pricing.row_pricing_state("", now=now) == "unverified"
    assert pricing.row_pricing_state("2026-09-28", now=now) == "fresh"
    assert pricing.row_pricing_state("2026-06-01", now=now) == "stale"


def test_unseeded_db_still_allows_a_model_the_code_dict_prices(lib):
    # before the seed has run, the verified code dict is the seed source
    assert lib.model_enable_problems("claude-opus-5-5") == []
    assert lib.model_enable_problems("claude-fable-5-1") == ["no pricing row"]
