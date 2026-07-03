"""Sail, Don't Row — /play route (Phase 1 core engine).

Confirms the page is fully public, renders all four ranks, embeds the live
game_rank_settings values as JSON for the client engine, and picks up admin
edits without a redeploy.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


@pytest.fixture
def env(monkeypatch, tmp_path):
    db = str(tmp_path / "t.db")
    monkeypatch.setenv("LINKLIB_DB", db)
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    from linklib.db import Library
    # /play reads game_rank_settings seeded by the app's startup hook, which a
    # bare TestClient (no `with`) never fires — seed directly, same data path.
    lib = Library(db)
    lib.seed_game_rank_settings()
    lib.close()
    yield appmod, TestClient(appmod.app)


def test_play_is_public(env):
    _, client = env
    r = client.get("/play")
    assert r.status_code == 200


def test_play_renders_all_ranks(env):
    _, client = env
    body = client.get("/play").text
    assert "Deckhand" in body and "Mate" in body and "First Mate" in body and "Skipper" in body
    for rank_id in ["deckhand", "mate", "first_mate", "skipper"]:
        assert f'data-rank="{rank_id}"' in body


def test_play_embeds_rank_settings_json(env):
    _, client = env
    body = client.get("/play").text
    assert "var RANK_SETTINGS = " in body
    assert '"collision_limit": 3' in body   # Mate default
    assert '"par_time_seconds": 150' in body


def test_play_reflects_admin_edits_live(env):
    appmod, client = env
    client.post("/admin/game-settings/mate/edit", data={
        "label": "Mate", "difficulty_label": "Medium",
        "collision_limit": "3", "par_time_seconds": "222",
        "gust_coverage_pct": "35", "obstacle_density": "5",
        "drift_speed": "10", "row_speed": "99", "sail_speed": "40",
        "stamina_drain_per_sec": "15", "stamina_regen_per_sec": "4",
        "grace_window": "1",
    })
    body = client.get("/play").text
    assert '"par_time_seconds": 222' in body
    assert '"row_speed": 99.0' in body


def test_play_links_to_admin_game_settings(env):
    _, client = env
    body = client.get("/play").text
    assert "/admin/game-settings" in body


def test_play_nav_link_present(env):
    _, client = env
    body = client.get("/").text
    assert 'href="/play"' in body
