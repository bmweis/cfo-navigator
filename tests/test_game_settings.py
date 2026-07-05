"""Sail, Don't Row rank/mode tuning (linklib.db game_rank_settings).

Backs the /admin/game-settings page, which lets Brian rebalance pace, wind,
obstacle density, and the collision rule per rank without touching code.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Library


@pytest.fixture
def lib(tmp_path):
    db = Library(str(tmp_path / "t.db"))
    try:
        yield db
    finally:
        db.close()


def test_seed_creates_four_ranks_in_order(lib):
    lib.seed_game_rank_settings()
    ranks = lib.list_game_rank_settings()
    assert [r["rank"] for r in ranks] == ["deckhand", "mate", "first_mate", "skipper"]
    assert ranks[0]["label"] == "Deckhand"
    assert ranks[0]["collision_limit"] == 0   # practice mode — no penalty
    assert ranks[3]["grace_window"] == 0      # Skipper — no invincibility window


def test_seed_is_idempotent_and_never_overwrites(lib):
    lib.seed_game_rank_settings()
    lib.update_game_rank_settings("mate", par_time_seconds=999)
    lib.seed_game_rank_settings()   # simulates a second app startup
    assert lib.get_game_rank_settings("mate")["par_time_seconds"] == 999


def test_get_game_rank_settings_unknown_returns_none(lib):
    lib.seed_game_rank_settings()
    assert lib.get_game_rank_settings("commodore") is None


def test_update_game_rank_settings_persists(lib):
    lib.seed_game_rank_settings()
    lib.update_game_rank_settings("skipper", par_time_seconds=200, gust_coverage_pct=15.0)
    r = lib.get_game_rank_settings("skipper")
    assert r["par_time_seconds"] == 200
    assert r["gust_coverage_pct"] == 15.0
    assert r["updated_at"]   # stamped


def test_update_unknown_rank_raises(lib):
    lib.seed_game_rank_settings()
    with pytest.raises(ValueError):
        lib.update_game_rank_settings("commodore", par_time_seconds=100)


def test_update_unknown_field_raises(lib):
    lib.seed_game_rank_settings()
    with pytest.raises(ValueError):
        lib.update_game_rank_settings("mate", enterprise_value=9200000)


def test_update_with_no_fields_is_noop(lib):
    lib.seed_game_rank_settings()
    before = lib.get_game_rank_settings("mate")
    lib.update_game_rank_settings("mate")
    after = lib.get_game_rank_settings("mate")
    assert before == after


def test_shark_params_inert_for_deckhand_tuned_for_others(lib):
    """The shark hazard never spawns on Deckhand (gated on rank in the game
    loop) — its seed values are 0/inert, distinct from Mate/First
    Mate/Skipper, which escalate cruise distance down and lunge
    speed/frequency up as rank hardens."""
    lib.seed_game_rank_settings()
    ranks = {r["rank"]: r for r in lib.list_game_rank_settings()}
    assert ranks["deckhand"]["shark_cruise_distance"] == 0.0
    assert ranks["deckhand"]["shark_lunge_speed"] == 0.0
    assert ranks["mate"]["shark_cruise_distance"] > 0
    assert ranks["skipper"]["shark_cruise_distance"] < ranks["mate"]["shark_cruise_distance"]
    assert ranks["skipper"]["shark_lunge_speed"] > ranks["mate"]["shark_lunge_speed"]
    assert ranks["skipper"]["shark_lunge_interval_sec"] < ranks["mate"]["shark_lunge_interval_sec"]


def test_update_shark_fields_persists(lib):
    lib.seed_game_rank_settings()
    lib.update_game_rank_settings("mate", shark_cruise_distance=200.0, shark_lunge_speed=70.0)
    r = lib.get_game_rank_settings("mate")
    assert r["shark_cruise_distance"] == 200.0
    assert r["shark_lunge_speed"] == 70.0
