"""Sail, Don't Row — Phase 7: leaderboard write path (/play/submit) and
public read path (/play/leaderboard), plus the Difficulty Index helpers
that back the gradient difficulty pills.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from webapp.app import _sdr_difficulty_index, _sdr_difficulty_label, _sdr_course_week


# -- Difficulty Index / label (pure functions) -------------------------------

def test_difficulty_index_deckhand_is_easiest():
    # Deckhand defaults: gust_coverage_pct=45, obstacle_density=3 -> index 42
    idx = _sdr_difficulty_index(45.0, 3.0)
    assert idx == 42
    assert _sdr_difficulty_label(idx) == "Choppy Waters"


def test_difficulty_index_skipper_is_hardest():
    # Skipper defaults: gust_coverage_pct=20, obstacle_density=9
    idx = _sdr_difficulty_index(20.0, 9.0)
    assert idx > 80
    assert _sdr_difficulty_label(idx) == "Storm Warning"


def test_difficulty_index_monotonic_in_obstacle_density():
    lo = _sdr_difficulty_index(35.0, 3.0)
    hi = _sdr_difficulty_index(35.0, 9.0)
    assert hi > lo


def test_difficulty_index_monotonic_in_gust_coverage():
    low_gust = _sdr_difficulty_index(10.0, 5.0)
    high_gust = _sdr_difficulty_index(50.0, 5.0)
    assert low_gust > high_gust   # less wind = harder


def test_difficulty_label_bucket_boundaries():
    assert _sdr_difficulty_label(0) == "Fair Winds"
    assert _sdr_difficulty_label(30) == "Fair Winds"
    assert _sdr_difficulty_label(31) == "Choppy Waters"
    assert _sdr_difficulty_label(55) == "Choppy Waters"
    assert _sdr_difficulty_label(56) == "Rough Seas"
    assert _sdr_difficulty_label(80) == "Rough Seas"
    assert _sdr_difficulty_label(81) == "Storm Warning"
    assert _sdr_difficulty_label(100) == "Storm Warning"


def test_course_week_format():
    import datetime
    week = _sdr_course_week(datetime.datetime(2026, 7, 3, tzinfo=datetime.timezone.utc))
    assert week == "2026-W27"


# -- /play/submit and /play/leaderboard --------------------------------------

@pytest.fixture
def env(monkeypatch, tmp_path):
    db = str(tmp_path / "t.db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    from linklib.db import Library
    lib = Library(db)
    lib.seed_game_rank_settings()
    lib.create_user("sailor", "sailorpass", role="user")
    lib.close()
    client = TestClient(appmod.app)
    yield appmod, client


def _login(client):
    client.post("/login", data={"username": "sailor", "password": "sailorpass"}, follow_redirects=False)


def test_submit_requires_login(env):
    _, client = env
    r = client.post("/play/submit", json={
        "rank": "mate", "score": 80, "distance_fraction": 1.0,
        "finished": True, "time_seconds": 100, "efficiency_pct": 70,
    })
    assert r.status_code == 401


def test_submit_rejects_unknown_rank(env):
    _, client = env
    _login(client)
    r = client.post("/play/submit", json={
        "rank": "admiral", "score": 80, "distance_fraction": 1.0,
        "finished": True, "time_seconds": 100, "efficiency_pct": 70,
    })
    assert r.status_code == 400


def test_submit_records_a_run(env):
    appmod, client = env
    _login(client)
    r = client.post("/play/submit", json={
        "rank": "mate", "score": 82, "distance_fraction": 1.0,
        "finished": True, "time_seconds": 145, "efficiency_pct": 68,
    })
    assert r.status_code == 200
    assert r.json()["ok"] is True

    board = client.get("/play/leaderboard?rank=mate&scope=all").text
    assert "sailor" in board
    assert ">82<" in board


def test_submit_clamps_out_of_range_values(env):
    """A tampered client sending score=9999 or negative distance must not
    corrupt the leaderboard — values are clamped to their valid ranges."""
    appmod, client = env
    _login(client)
    r = client.post("/play/submit", json={
        "rank": "mate", "score": 9999, "distance_fraction": -5,
        "finished": True, "time_seconds": -100, "efficiency_pct": 500,
    })
    assert r.status_code == 200
    from linklib.db import Library
    import os
    lib = Library(os.environ["LINKLIB_DB"])
    row = lib.list_game_leaderboard("mate")[0]
    lib.close()
    assert row["score"] == 100
    assert row["distance_fraction"] == 0.0
    assert row["time_seconds"] == 0.0
    assert row["efficiency_pct"] == 100.0


def test_submit_computes_difficulty_server_side_ignoring_client_input(env):
    """The client never sends difficulty/course_week — even if it tried to,
    the server only ever computes these from live rank settings + its own
    clock, never trusting client input for them."""
    appmod, client = env
    _login(client)
    client.post("/play/submit", json={
        "rank": "skipper", "score": 90, "distance_fraction": 1.0,
        "finished": True, "time_seconds": 100, "efficiency_pct": 90,
        "difficulty_label": "Fair Winds", "course_week": "1999-W01",  # ignored if sent
    })
    from linklib.db import Library
    import os
    lib = Library(os.environ["LINKLIB_DB"])
    row = lib.list_game_leaderboard("skipper")[0]
    lib.close()
    assert row["difficulty_label"] == "Storm Warning"   # Skipper defaults -> Storm Warning
    assert row["course_week"] != "1999-W01"


def test_leaderboard_is_public(env):
    _, client = env
    r = client.get("/play/leaderboard")
    assert r.status_code == 200


def test_leaderboard_defaults_to_mate_this_week(env):
    _, client = env
    body = client.get("/play/leaderboard").text
    assert '<a class="sdr-lb-tab active" href="/play/leaderboard?rank=mate&scope=week">' in body
    assert '<a class="sdr-lb-tab active" href="/play/leaderboard?rank=mate&scope=week">This Week</a>' in body


def test_leaderboard_empty_state(env):
    _, client = env
    body = client.get("/play/leaderboard?rank=deckhand&scope=week").text
    assert "No runs yet" in body


def test_leaderboard_invalid_rank_falls_back_to_mate(env):
    _, client = env
    r = client.get("/play/leaderboard?rank=notarank")
    assert r.status_code == 200
    assert 'href="/play/leaderboard?rank=mate&scope=week">Mate</a>' in r.text
