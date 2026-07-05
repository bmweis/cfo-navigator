"""Sail, Don't Row leaderboard (linklib.db game_runs).

Backs the public leaderboard at /play/leaderboard — Round 2 flipped this from
per-rank tables to one combined board (each row tagged with a rank badge),
best score per player across all ranks, "this week" scoped by course_week or
all-time when omitted. `rank` is still accepted as an optional filter.
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


@pytest.fixture
def users(lib):
    lib.create_user("alice", "pw", role="user")
    lib.create_user("bob", "pw", role="user")
    return {
        "alice": lib.get_user("alice")["id"],
        "bob": lib.get_user("bob")["id"],
    }


def test_record_and_list_leaderboard_sorts_by_score_desc(lib, users):
    lib.record_game_run(users["alice"], "mate", 60, 0.7, False, 80.0, 1,
                        "2026-W27", 45, "Choppy Waters")
    lib.record_game_run(users["bob"], "mate", 90, 1.0, True, 100.0, 0,
                        "2026-W27", 45, "Choppy Waters")
    board = lib.list_game_leaderboard(course_week="2026-W27")
    assert [r["username"] for r in board] == ["bob", "alice"]
    assert [r["score"] for r in board] == [90, 60]


def test_leaderboard_shows_best_run_per_player_not_every_attempt(lib, users):
    lib.record_game_run(users["alice"], "mate", 40, 0.4, False, 30.0, 2,
                        "2026-W27", 45, "Choppy Waters")
    lib.record_game_run(users["alice"], "mate", 80, 1.0, True, 90.0, 0,
                        "2026-W27", 45, "Choppy Waters")
    board = lib.list_game_leaderboard(course_week="2026-W27")
    assert len(board) == 1
    assert board[0]["score"] == 80   # best, not most-recent or first


def test_leaderboard_is_combined_across_ranks_best_run_wins(lib, users):
    """Round 2: one combined board — a player's single best run shows,
    whichever rank it happened to be played on, not one row per rank."""
    lib.record_game_run(users["alice"], "mate", 70, 1.0, True, 90.0, 1,
                        "2026-W27", 45, "Choppy Waters")
    lib.record_game_run(users["alice"], "skipper", 95, 1.0, True, 90.0, 0,
                        "2026-W27", 87, "Storm Warning")
    board = lib.list_game_leaderboard(course_week="2026-W27")
    assert len(board) == 1
    assert board[0]["score"] == 95
    assert board[0]["rank"] == "skipper"


def test_leaderboard_rank_filter_still_available(lib, users):
    """`rank` remains an optional filter for callers that want it, even
    though the public /play/leaderboard route no longer exposes one."""
    lib.record_game_run(users["alice"], "mate", 70, 1.0, True, 90.0, 1,
                        "2026-W27", 45, "Choppy Waters")
    lib.record_game_run(users["alice"], "skipper", 95, 1.0, True, 90.0, 0,
                        "2026-W27", 87, "Storm Warning")
    assert [r["score"] for r in lib.list_game_leaderboard(rank="mate")] == [70]
    assert [r["score"] for r in lib.list_game_leaderboard(rank="skipper")] == [95]
    assert lib.list_game_leaderboard(rank="first_mate") == []


def test_this_week_scope_excludes_other_weeks(lib, users):
    lib.record_game_run(users["alice"], "mate", 70, 1.0, True, 90.0, 1,
                        "2026-W26", 45, "Choppy Waters")
    lib.record_game_run(users["bob"], "mate", 50, 0.5, False, 40.0, 2,
                        "2026-W27", 45, "Choppy Waters")
    this_week = lib.list_game_leaderboard(course_week="2026-W27")
    assert [r["username"] for r in this_week] == ["bob"]
    all_time = lib.list_game_leaderboard()
    assert {r["username"] for r in all_time} == {"alice", "bob"}


def test_leaderboard_respects_limit(lib, users):
    for i in range(5):
        lib.create_user(f"player{i}", "pw", role="user")
        uid = lib.get_user(f"player{i}")["id"]
        lib.record_game_run(uid, "mate", 50 + i, 1.0, True, 90.0, 0,
                            "2026-W27", 45, "Choppy Waters")
    board = lib.list_game_leaderboard(course_week="2026-W27", limit=2)
    assert len(board) == 2
    assert board[0]["score"] == 54   # highest first


def test_record_game_run_stores_hits_not_efficiency(lib, users):
    """Round 2 dropped Stamina/efficiency from the mechanic entirely —
    collisions now score directly, so the row stores a hits count instead."""
    lib.record_game_run(users["alice"], "mate", 70, 1.0, True, 90.0, 2,
                        "2026-W27", 45, "Choppy Waters")
    board = lib.list_game_leaderboard(course_week="2026-W27")
    assert board[0]["hits"] == 2
