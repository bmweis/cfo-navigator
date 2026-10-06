"""Homepage Status note: one field, a last-revised stamp, Mark reviewed, a
14-day staleness warning, and a /admin/checks row plus a single Admin badge
count. Clocks are injected, never slept on."""
import importlib
import os
import pathlib
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import homepage_status as hs  # noqa: E402

LINK = '<a href="https://ready.example" target="_blank" rel="noopener">Ready Education</a>'


@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    for k in ("LINKLIB_PASSWORD", "LINKLIB_SAVE_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    import webapp.app as appmod
    import webapp.checks as checks
    importlib.reload(appmod)
    importlib.reload(checks)
    from webapp import tasks
    tasks._checks_cache = None
    yield appmod
    if os.path.exists(db):
        os.remove(db)


def _client(appmod):
    from fastapi.testclient import TestClient
    return TestClient(appmod.app)  # open-auth mode, like the sibling copy tests


def _set(appmod, key, value):
    lib = appmod._lib()
    try:
        lib.set_setting(key, value)
    finally:
        lib.close()


def _get(appmod, key):
    lib = appmod._lib()
    try:
        return lib.get_setting(key)
    finally:
        lib.close()


def _ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


# ---- pure rule ------------------------------------------------------------

def test_status_age_boundary():
    now = datetime(2026, 10, 20, tzinfo=timezone.utc)
    assert hs.status_age((now - timedelta(days=13)).isoformat(), now)["stale"] is False
    assert hs.status_age((now - timedelta(days=14)).isoformat(), now)["stale"] is True


def test_status_age_missing_counts_as_confirmed_today():
    a = hs.status_age("")
    assert a == {"known": False, "days": 0, "stale": False}
    assert "counts as confirmed today" in hs.check_detail(a)


# ---- admin page, save, render --------------------------------------------

def test_card_is_one_status_box_and_shows_legacy_text_until_first_save(env):
    c = _client(env)
    _set(env, "homepage_teaser_copy", "Old lead line.")
    _set(env, "homepage_expanded_copy", "Old rest of bio.")
    html = c.get("/admin/copy/homepage").text
    assert 'id="home-status-copy"' in html
    assert "Old lead line.\n\nOld rest of bio." in html
    assert "Lead line" not in html and "Rest of the bio" not in html
    assert _get(env, "homepage_status_copy") == ""          # nothing written by viewing


def test_empty_defaults_show_effective_text_not_an_empty_box(env):
    html = _client(env).get("/admin/copy/homepage").text
    assert "wrapping up my time at Mux" in html


def test_save_stamps_time_keeps_legacy_keys_and_renders_link(env):
    c = _client(env)
    _set(env, "homepage_teaser_copy", "Old lead line.")
    r = c.post("/admin/copy/homepage", json={"homepage_status_copy": f"One.\n\nWith {LINK}."})
    assert r.status_code == 200 and "Last revised or confirmed" in r.json()["label"]
    assert _get(env, "homepage_status_revised_at")
    assert _get(env, "homepage_teaser_copy") == "Old lead line."   # nothing deleted
    home = c.get("/").text
    assert LINK in home and "<p>One.</p>" in home


def test_empty_save_is_refused(env):
    c = _client(env)
    assert c.post("/admin/copy/homepage", json={"homepage_status_copy": "  "}).status_code == 400
    assert _get(env, "homepage_status_copy") == ""


def test_link_passes_outbound_check_and_preview_shows_it(env):
    from linklib import brand_check
    assert brand_check.outbound_link_problems(f"x = '''{LINK}'''") == []
    r = _client(env).post("/admin/copy/preview", json={"text": f"See {LINK}"})
    assert LINK in r.json()["html"]


def test_help_text_says_to_use_raw_anchor(env):
    html = _client(env).get("/admin/copy/homepage").text
    assert 'target="_blank" rel="noopener"' in html and "Preview" in html


def test_mark_reviewed_restarts_clock_without_changing_text(env):
    c = _client(env)
    c.post("/admin/copy/homepage", json={"homepage_status_copy": "Stays the same."})
    _set(env, "homepage_status_revised_at", _ago(20))
    assert c.post("/admin/copy/homepage/status-reviewed", json={}).status_code == 200
    assert _get(env, "homepage_status_copy") == "Stays the same."
    assert hs.status_age(_get(env, "homepage_status_revised_at"))["days"] == 0


def test_warning_appears_at_14_days_and_not_before(env):
    c = _client(env)
    _set(env, "homepage_status_revised_at", _ago(13))
    assert 'id="status-warning" role="status" style="' in c.get("/admin/copy/homepage").text
    assert "display:none;" in c.get("/admin/copy/homepage").text.split('id="status-warning"')[1][:400]
    _set(env, "homepage_status_revised_at", _ago(15))
    page = c.get("/admin/copy/homepage").text.split('id="status-warning"')[1][:400]
    assert "display:block;" in page and "Last revised 15 days ago. Update it, or mark it reviewed" in page


def test_viewing_never_clears_staleness(env):
    c = _client(env)
    _set(env, "homepage_status_revised_at", _ago(30))
    c.get("/admin/copy/homepage"); c.get("/admin/copy/homepage"); c.get("/")
    assert hs.status_age(_get(env, "homepage_status_revised_at"))["stale"]


# ---- check row and badge --------------------------------------------------

def test_check_passes_then_fails_then_passes_after_save_or_review(env):
    from webapp import checks
    _set(env, "homepage_status_revised_at", _ago(3))
    assert checks.homepage_status_check()["ok"] is True
    _set(env, "homepage_status_revised_at", _ago(20))
    row = checks.homepage_status_check()
    assert row["ok"] is False and "20 days ago" in row["detail"]
    c = _client(env)
    c.post("/admin/copy/homepage/status-reviewed", json={})
    assert checks.homepage_status_check()["ok"] is True
    _set(env, "homepage_status_revised_at", _ago(20))
    c.post("/admin/copy/homepage", json={"homepage_status_copy": "New."})
    assert checks.homepage_status_check()["ok"] is True


def test_check_with_no_stamp_passes_and_says_so(env):
    from webapp import checks
    row = checks.homepage_status_check()
    assert row["ok"] is True and "counts as confirmed today" in row["detail"]


def test_check_is_in_run_all_and_themed(env):
    from webapp import checks
    names = [r["name"] for r in checks.run_all()]
    assert names.count("Homepage status is current") == 1
    assert env._live_check_theme("Homepage status is current") == "Voice and copy"


def test_badge_moves_once_and_only_when_stale(env):
    from webapp import tasks
    from tests.test_task_badges import _mark_all_freshness_reviewed
    lib = env._lib()
    try:
        _mark_all_freshness_reviewed(os.environ["LINKLIB_DB"])
        base = tasks._stale_admin_checks_reminders(lib)
        lib.set_setting("homepage_status_revised_at", _ago(20))
        assert tasks._stale_admin_checks_reminders(lib) == base + 1
        lib.set_setting("homepage_status_revised_at", _ago(1))
        assert tasks._stale_admin_checks_reminders(lib) == base
    finally:
        lib.close()


def test_startup_seed_starts_the_clock_once(env):
    from fastapi.testclient import TestClient
    with TestClient(env.app):
        first = _get(env, "homepage_status_revised_at")
    assert first
    with TestClient(env.app):
        assert _get(env, "homepage_status_revised_at") == first
