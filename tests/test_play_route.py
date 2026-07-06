"""Sail, Don't Row — /play route (Phases 1-7, plus Round 2's post-playtesting
rework: rowing/Stamina removed in favor of a speed ramp, a mini-map HUD
replaces the Stamina bar and floating checkpoint label, scoring now applies
a direct CollisionPenalty, and the game/admin-only footer hint is gated).

Confirms the page is fully public, renders all four ranks, embeds the live
game_rank_settings values as JSON for the client engine, picks up admin
edits without a redeploy, wires up the gust-zone DOM/JS the auto-sail
mechanic depends on, wires up the five checkpoint backdrop layers + whale +
prize elements, renders the full outcome-stats grid + login-aware
leaderboard note, confirms each rank pill's collision-rule line is derived
live from game_rank_settings rather than hardcoded, and confirms the
mini-map and rank pill name/sub-label layout fix are present. The
frame-by-frame JS behavior (gust/speed-ramp timing, checkpoint crossfade,
whale trigger, finish condition, stat computation) and the cross-viewport
responsive layout are exercised via a live-server Playwright pass during
development (see the PR description) rather than here.
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


def test_play_renders_boat_choice(env):
    """Boat choice is a second, independent axis alongside rank on the same
    pregame screen (see webapp/app.py's _SDR_BOATS / _sdr_boat_pill_html)."""
    _, client = env
    body = client.get("/play").text
    assert "Sailboat" in body and "Rowboat" in body
    assert 'data-boat="sailboat"' in body and 'data-boat="rowboat"' in body
    assert body.index('id="sdrBoatRow"') < body.index('id="sdrRankRow"')


def test_play_boat_pill_wired_into_start(env):
    _, client = env
    body = client.get("/play").text
    assert "selectedBoat" in body
    assert "startRun(selectedRank, selectedBoat)" in body


def test_collision_description_matches_mockup_defaults():
    """Default seed values must reproduce the four exact strings from
    design/mockups/sail-dont-row-final-rendering.html's rank selector."""
    from webapp.app import _sdr_collision_description
    assert _sdr_collision_description({"collision_limit": 0, "grace_window": 1}) == "No penalty on hit"
    assert _sdr_collision_description({"collision_limit": 3, "grace_window": 1}) == "3 hits and you’re sunk"
    assert _sdr_collision_description({"collision_limit": 1, "grace_window": 1}) == "1 hit and you’re sunk"
    assert _sdr_collision_description({"collision_limit": 1, "grace_window": 0}) == "Any hit ends it instantly"


def test_collision_description_tracks_admin_retuning():
    """If Brian raises a rank's collision_limit at /admin/game-settings, the
    pill text must follow — it's derived, not a hardcoded string that could
    silently drift out of sync."""
    from webapp.app import _sdr_collision_description
    assert _sdr_collision_description({"collision_limit": 5, "grace_window": 1}) == "5 hits and you’re sunk"


def test_play_rank_pills_show_collision_rule(env):
    _, client = env
    body = client.get("/play").text
    assert '<span class="sdr-rank-collision">No penalty on hit</span>' in body
    assert '<span class="sdr-rank-collision">3 hits and you’re sunk</span>' in body
    assert '<span class="sdr-rank-collision">1 hit and you’re sunk</span>' in body
    assert '<span class="sdr-rank-collision">Any hit ends it instantly</span>' in body


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
        "drift_speed": "17", "sail_speed": "9", "speed_ramp_per_sec": "0.2",
        "grace_window": "1",
    })
    body = client.get("/play").text
    assert '"par_time_seconds": 222' in body
    assert '"speed_ramp_per_sec": 0.2' in body


def test_play_links_to_admin_game_settings(env):
    _, client = env
    body = client.get("/play").text
    assert "/admin/game-settings" in body


def test_play_not_in_site_nav(env):
    """Sail, Don't Row is an easter egg, not a nav item — it must not appear
    in the top nav on the home page (or anywhere else site-wide)."""
    _, client = env
    body = client.get("/").text
    nav = body.split("<nav")[1].split("</nav>")[0]
    assert 'href="/play"' not in nav


def test_play_easter_egg_link_on_hackathon_page(env):
    """The only place Sail, Don't Row is linked from is the bottom of the
    finops-ai-hackathon article — a quiet easter egg for readers who scroll
    all the way down, not a promoted CTA."""
    _, client = env
    body = client.get("/finops-ai-hackathon").text
    assert 'href="/play"' in body
    assert "Sail, Don&rsquo;t Row" in body or "Sail, Don't Row" in body


def test_play_embeds_gust_and_sail_settings(env):
    _, client = env
    body = client.get("/play").text
    assert '"gust_coverage_pct": 35.0' in body   # Mate default
    assert '"sail_speed": 8.0' in body            # Mate default gust boost
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


def test_play_wires_up_whale_hazard(env):
    """Whale hazard: rises once per run at a randomized lane (own seeded
    PRNG via buildWhale), and striking it mid-breach is a distinct
    'Capsized!' outcome — an instant, rank-independent game over (including
    Deckhand, unlike the shark) that bypasses the rock/buoy collision_limit/
    lives system entirely."""
    _, client = env
    body = client.get("/play").text
    assert "function buildWhale" in body
    assert "function updateWhale" in body
    assert "WHALE_DANGER_START_SEC = 0.45, WHALE_DANGER_END_SEC = 2.3" in body
    assert "Capsized!" in body
    assert "endRun('breached')" in body
    # Not gated off for any rank the way updateShark gates off Deckhand.
    whale_fn_body = body.split("function updateWhale")[1].split("\n  function ")[0]
    assert "if (state.rank === 'deckhand') return;" not in whale_fn_body


def test_play_wires_up_shark_hazard(env):
    """Shark pursuit hazard: rank-tunable params embedded in RANK_SETTINGS,
    the tracking state machine present in the JS, active only from the
    Martha's Vineyard leg onward, and a distinct 'Caught!' outcome title
    separate from the normal 'Sunk' collision/lives ending."""
    _, client = env
    body = client.get("/play").text
    assert 'id="sdrShark"' in body
    assert "updateShark" in body
    assert "SHARK_ACTIVE_FRAC = 0.70" in body
    assert '"shark_cruise_distance": 60.0' in body   # Mate default
    assert '"shark_lunge_speed": 50.0' in body        # Mate default
    assert "Caught!" in body
    # Rank-independent: catching the boat calls endRun('eaten') with no
    # collision_limit/lives check, unlike the rock/buoy registerHit path.
    assert "endRun('eaten')" in body


def test_play_shark_gated_off_for_deckhand(env):
    """Deckhand's row_settings carry 0/inert shark params — the game loop
    also explicitly skips Deckhand (belt and suspenders), but the embedded
    JSON should reflect the inert tuning too."""
    _, client = env
    body = client.get("/play").text
    assert '"deckhand": {"collision_limit": 0' in body
    # Deckhand's shark_cruise_distance is 0.0 in the same JSON object as its
    # other 0/false Easy-mode defaults — confirm via the DB layer directly
    # since asserting exact JSON key order/spacing in the page body is brittle.
    from linklib.db import Library
    import os
    lib = Library(os.environ["LINKLIB_DB"])
    deckhand = lib.get_game_rank_settings("deckhand")
    lib.close()
    assert deckhand["shark_cruise_distance"] == 0.0


def test_admin_game_settings_shark_fields_hidden_for_deckhand(env):
    """Shark tuning fields are omitted entirely from Deckhand's admin card
    (not just editable-to-0) since the hazard never spawns there."""
    from fastapi.testclient import TestClient
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    client = TestClient(appmod.app)
    from linklib.db import Library
    import os
    lib = Library(os.environ["LINKLIB_DB"])
    lib.seed_game_rank_settings()
    lib.close()
    body = client.get("/admin/game-settings").text
    deckhand_card = body.split('rank id: deckhand')[1].split('rank id: mate')[0]
    mate_card = body.split('rank id: mate')[1].split('rank id: first_mate')[0]
    assert "Shark cruise distance" not in deckhand_card
    assert "Shark cruise distance" in mate_card


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
    for stat_id in ["sdrStatScore", "sdrStatDistance", "sdrStatTime", "sdrStatHits"]:
        assert f'id="{stat_id}"' in body
    assert 'id="sdrOutcomeFurthest"' in body
    assert 'id="sdrStatFurthest"' in body


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
    would otherwise mask this behind. Guests can still view the (now real)
    leaderboard without signing in."""
    _, client = appmod_with_auth
    body = client.get("/play").text
    assert 'href="/login?next=%2Fplay"' in body
    assert "Sign in to save runs" in body
    assert 'href="/play/leaderboard"' in body
    assert 'id="sdrSubmitStatus"' not in body


def test_play_leaderboard_note_signed_in_shows_submit_status(appmod_with_auth):
    """A signed-in member sees the real submit-status placeholder (updated
    by JS after the /play/submit fetch resolves) and a leaderboard link,
    not the signed-out sign-in prompt."""
    appmod, client = appmod_with_auth
    client.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    body = client.get("/play").text
    assert 'id="sdrSubmitStatus"' in body
    assert 'href="/play/leaderboard"' in body
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


def test_play_renders_minimap_not_stamina_bar(env):
    """Round 2: rowing and the Stamina resource are gone entirely — the
    top-left HUD slot is now a 5-checkpoint mini-map instead."""
    _, client = env
    body = client.get("/play").text
    assert 'id="sdrMinimap"' in body
    assert 'id="sdrMinimapFill"' in body
    assert 'id="sdrMinimapLabel"' in body
    assert body.count('class="sdr-minimap-dot') == 5
    assert "sdrStaminaFill" not in body
    assert "sdrRowBtn" not in body
    assert ">ROW<" not in body


def test_play_bottom_hud_no_longer_has_floating_checkpoint_text(env):
    """The floating checkpoint-name label folded into the mini-map — the
    bottom HUD now only shows the lives-remaining indicator."""
    _, client = env
    body = client.get("/play").text
    assert 'id="sdrCheckpoint"' not in body
    assert 'id="sdrLives"' in body


def test_admin_game_settings_hint_is_admin_only(monkeypatch, tmp_path):
    """The '/admin/game-settings is tunable' footer line must not leak to
    regular players or public visitors — only admins should see it. The
    leaderboard-save sentence stays visible to everyone."""
    db = str(tmp_path / "admingate.db")
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
    client = TestClient(appmod.app)

    anon_body = client.get("/play").text
    assert "/admin/game-settings" not in anon_body
    assert "save automatically to the" in anon_body

    client.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    admin_body = client.get("/play").text
    assert "/admin/game-settings" in admin_body


def test_play_rank_pill_name_and_sub_dont_run_together(env):
    """Regression: the name/difficulty-label spans used to have zero
    whitespace between their tags in the generated HTML ("MateMedium"),
    since adjacent Python string literals don't insert a separator. They
    must now be wrapped in a dedicated flex-column container instead of
    relying on source whitespace between inline spans."""
    _, client = env
    body = client.get("/play").text
    assert '<span class="sdr-rank-namesub"><span class="sdr-rank-name">Mate</span>' \
           '<span class="sdr-rank-sub">Medium</span></span>' in body
    assert ".sdr-rank-namesub{display:flex;flex-direction:column" in body
