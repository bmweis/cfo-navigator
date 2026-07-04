"""Sail, Don't Row leaderboard (linklib.db game_runs).

Backs the per-rank public leaderboards at /play/leaderboard — one board per
rank (Brian's call: per-rank tables, not a combined board with badges), best
score per player, "this week" scoped by course_week or all-time when omitted.
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
    lib.record_game_run(users["alice"], "mate", 60, 0.7, False, 80.0, 50.0,
                        "2026-W27", 45, "Choppy Waters")
    lib.record_game_run(users["bob"], "mate", 90, 1.0, True, 100.0, 85.0,
                        "2026-W27", 45, "Choppy Waters")
    board = lib.list_game_leaderboard("mate", course_week="2026-W27")
    assert [r["username"] for r in board] == ["bob", "alice"]
    assert [r["score"] for r in board] == [90, 60]


def test_leaderboard_shows_best_run_per_player_not_every_attempt(lib, users):
    lib.record_game_run(users["alice"], "mate", 40, 0.4, False, 30.0, 40.0,
                        "2026-W27", 45, "Choppy Waters")
    lib.record_game_run(users["alice"], "mate", 80, 1.0, True, 90.0, 70.0,
                        "2026-W27", 45, "Choppy Waters")
    board = lib.list_game_leaderboard("mate", course_week="2026-W27")
    assert len(board) == 1
    assert board[0]["score"] == 80   # best, not most-recent or first


def test_leaderboard_is_scoped_per_rank(lib, users):
    lib.record_game_run(users["alice"], "mate", 70, 1.0, True, 90.0, 60.0,
                        "2026-W27", 45, "Choppy Waters")
    lib.record_game_run(users["alice"], "skipper", 95, 1.0, True, 90.0, 90.0,
                        "2026-W27", 87, "Storm Warning")
    assert [r["score"] for r in lib.list_game_leaderboard("mate")] == [70]
    assert [r["score"] for r in lib.list_game_leaderboard("skipper")] == [95]
    assert lib.list_game_leaderboard("first_mate") == []


def test_this_week_scope_excludes_other_weeks(lib, users):
    lib.record_game_run(users["alice"], "mate", 70, 1.0, True, 90.0, 60.0,
                        "2026-W26", 45, "Choppy Waters")
    lib.record_game_run(users["bob"], "mate", 50, 0.5, False, 40.0, 40.0,
                        "2026-W27", 45, "Choppy Waters")
    this_week = lib.list_game_leaderboard("mate", course_week="2026-W27")
    assert [r["username"] for r in this_week] == ["bob"]
    all_time = lib.list_game_leaderboard("mate")
    assert {r["username"] for r in all_time} == {"alice", "bob"}


def test_leaderboard_respects_limit(lib, users):
    for i in range(5):
        lib.create_user(f"player{i}", "pw", role="user")
        uid = lib.get_user(f"player{i}")["id"]
        lib.record_game_run(uid, "mate", 50 + i, 1.0, True, 90.0, 60.0,
                            "2026-W27", 45, "Choppy Waters")
    board = lib.list_game_leaderboard("mate", course_week="2026-W27", limit=2)
    assert len(board) == 2
    assert board[0]["score"] == 54   # highest first
