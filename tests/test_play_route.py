"""Sail, Don't Row — /play route (Phase 1 core engine, Phase 2 wind/rowing,
Phase 3 checkpoint route + Nantucket finish, Phase 4 outcome-screen stats).

Confirms the page is fully public, renders all four ranks, embeds the live
game_rank_settings values as JSON for the client engine, picks up admin
edits without a redeploy, wires up the gust-zone DOM/JS the Phase 2
auto-sail mechanic depends on, wires up the five checkpoint backdrop
layers + whale + prize elements Phase 3 depends on, and renders the full
outcome-stats grid + login-aware leaderboard note Phase 4 adds. The
frame-by-frame JS behavior (gust/stamina timing, checkpoint crossfade,
whale trigger, finish condition, stat computation) is exercised via a
live-server Playwright pass during development (see the PR description)
rather than here.
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


def test_play_embeds_gust_and_sail_settings(env):
    _, client = env
    body = client.get("/play").text
    assert '"gust_coverage_pct": 35.0' in body   # Mate default
    assert '"sail_speed": 40.0' in body
    assert "buildGustZones" in body
    assert 'id="sdrGusts"' in body


def test_play_gust_seed_independent_of_obstacle_seed(env):
    """Changing obstacle_density must not perturb gust placement, and vice
    versa — they're seeded with distinct '|gust' suffixed strings precisely
    so the two knobs can be tuned independently at /admin/game-settings."""
    _, client = env
    body = client.get("/play").text
    assert "currentWeekKey() + '|' + rankKey);" in body          # obstacle seed
    assert "currentWeekKey() + '|' + rankKey + '|gust');" in body  # gust seed


def test_play_renders_five_checkpoint_layers(env):
    _, client = env
    body = client.get("/play").text
    for i in range(5):
        assert f'<div class="sdr-skyline-layer{" sdr-active" if i == 0 else ""}" data-cp="{i}">' in body
        assert f'<div class="sdr-reflection-layer{" sdr-active" if i == 0 else ""}" data-cp="{i}">' in body


def test_play_checkpoint_labels_and_thresholds(env):
    _, client = env
    body = client.get("/play").text
    assert 'CHECKPOINTS = [[0.0,"Charles River"],[0.20,"Boston Harbor"],' \
           '[0.45,"Cape Cod"],[0.70,"Martha\'s Vineyard"],[0.90,"Nantucket"]];' in body


def test_play_wires_up_whale_and_prize(env):
    _, client = env
    body = client.get("/play").text
    assert 'id="sdrWhale"' in body
    assert 'id="sdrPrize"' in body
    assert "maybeTriggerWhale" in body
    assert "WHALE_TRIGGER_FRAC = 0.325" in body


def test_play_finish_condition_reaches_nantucket(env):
    """Reaching COURSE_LENGTH must call endRun('finish'), distinct from a
    collision-based 'sunk' ending — both are wired through the same endRun,
    but only 'finish' gets the "You made it to Nantucket" title/gold accent."""
    _, client = env
    body = client.get("/play").text
    assert "if (state.worldX >= COURSE_LENGTH){ endRun('finish'); return; }" in body
    assert "You made it to Nantucket" in body
    assert "sdr-finish" in body


def test_play_no_leftover_debug_hook(env):
    """A dev-only window.__sdrDebug hook was used to test checkpoint/whale/
    finish logic during Phase 3/4 development — must not ship."""
    _, client = env
    body = client.get("/play").text
    assert "__sdrDebug" not in body


def test_play_renders_outcome_stats_grid(env):
    _, client = env
    body = client.get("/play").text
    for stat_id in ["sdrStatScore", "sdrStatDistance", "sdrStatTime", "sdrStatEfficiency"]:
        assert f'id="{stat_id}"' in body
    assert 'id="sdrOutcomeFurthest"' in body
    assert 'id="sdrStatFurthest"' in body
    assert 'id="sdrPaceBreakdown"' in body


def test_play_scrim_has_plain_fallback_background(env):
    """backdrop-filter isn't universal — the scrim must have a real
    background-color so it still reads as an overlay without blur support."""
    _, client = env
    body = client.get("/play").text
    assert ".sdr-outcome-scrim{position:absolute;inset:0;background:rgba(247,246,241,0.6);backdrop-filter:blur(5px);" in body


def test_play_leaderboard_note_signed_out_links_to_login(appmod_with_auth):
    """With auth actually enabled (LINKLIB_PASSWORD set) and no session
    cookie, the visitor is a true guest — must see the sign-in prompt, not
    the local-dev "everyone's a member" fallback the default env fixture
    would otherwise mask this behind."""
    _, client = appmod_with_auth
    body = client.get("/play").text
    assert 'href="/login?next=%2Fplay"' in body
    assert "Sign in to save runs" in body
    assert "Leaderboard opens in a later update" not in body


def test_play_leaderboard_note_signed_in_no_fake_submission(appmod_with_auth):
    """A signed-in member must see an honest 'not built yet' note, never a
    fabricated 'score saved' claim — the leaderboard table is Phase 7."""
    appmod, client = appmod_with_auth
    client.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    body = client.get("/play").text
    assert "Leaderboard opens in a later update" in body
    assert "Sign in to save runs" not in body


@pytest.fixture
def appmod_with_auth(monkeypatch, tmp_path):
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
    lib.close()
    yield appmod, TestClient(appmod.app)
