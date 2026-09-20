"""The /admin/checks dashboard aggregator (webapp/checks.py).

Guards the dashboard wiring itself: results are well-formed, and every check that
runs live in-app is currently green (a regression in brand/voice/sync/dead-code
would fail here as well as in its own test).

PR 16 (2026-09) — a real, pre-existing app bug found via this file, not caused
by it: `coral_moment_problems()` was the first check in `run_all()` to actually
make live HTTP requests (every other live check is pure route/tuple
introspection, no request needed), and it exposed that an unauthenticated
`GET /` hangs indefinitely — not errors, not times out on its own, genuinely
blocks — whenever neither `LINKLIB_PASSWORD` nor `LINKLIB_SAVE_TOKEN` is set
(the documented "open, local-dev convenience" auth mode). This file used to
set only `LINKLIB_DB` at module level via `setdefault`, with no
`LINKLIB_PASSWORD`/`LINKLIB_SECRET_KEY` and no per-test isolation — unlike
every other test file's `env` fixture, which always sets all three. That gap
was harmless as long as nothing in `run_all()` made a real request; once
something did, it hung the whole suite. Fixed here by adopting the same `env`
fixture convention (fresh DB + password + secret key, reloaded per test) the
rest of the suite already uses. The underlying open-auth-mode hang is a
separate, deeper bug — reported, not fixed in this PR (scoped to what this
file's own test setup needed to stop blocking CI).
"""
import pathlib
import sys
import tempfile
import os

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib
    import webapp.app as appmod
    importlib.reload(appmod)
    import webapp.checks as checksmod
    importlib.reload(checksmod)
    yield checksmod
    if os.path.exists(db):
        os.remove(db)


def test_run_all_well_formed(env):
    results = env.run_all()
    assert results, "no checks returned"
    for r in results:
        assert {"name", "what", "where", "ok", "detail"} <= set(r), r
        assert r["where"] in ("Live + CI", "CI")
        assert r["name"] and r["what"] and r["detail"]


def test_live_checks_currently_pass(env):
    for r in env.run_all():
        if r["ok"] is not None:        # ran live in this environment
            assert r["ok"] is True, f"{r['name']} failing: {r['detail']}"


def test_admin_checks_summary_banner_is_green_on_a_clean_db(env, monkeypatch):
    """Regression for a real, previously-shipped bug: `admin_checks()`'s
    summary banner compared each row's `where` against "In-app", a value
    `run_all()` has never actually produced (every live row is "Live + CI");
    the banner was permanently blank regardless of pass/fail state. Fixed to
    compare against "Live + CI" — this asserts the green branch renders on a
    clean DB; the sibling test below forces a real failure and asserts red."""
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    from fastapi.testclient import TestClient
    import webapp.app as appmod
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "admin", "password": "adminpass"})
    r = c.get("/admin/checks")
    assert r.status_code == 200
    assert "All " in r.text and "live checks passing" in r.text
    assert "live check" not in r.text.replace("live checks passing", "")


def test_admin_checks_summary_banner_is_red_on_a_real_failure(env, monkeypatch):
    """Same page, forced into the failing branch via a real run_all() check
    (mechanical_findings, imported inside webapp.checks.run_all from
    linklib.voice_review) — proves the fixed comparison actually flips the
    banner red when a live check genuinely fails, not just that it's no
    longer permanently blank."""
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    import linklib.voice_review as vr_mod
    from fastapi.testclient import TestClient
    import webapp.app as appmod
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "admin", "password": "adminpass"})

    orig = vr_mod.mechanical_findings
    vr_mod.mechanical_findings = lambda text: [("buzzword", "seamless")]
    try:
        r = c.get("/admin/checks")
        assert r.status_code == 200
        assert "failing" in r.text and "live check" in r.text
    finally:
        vr_mod.mechanical_findings = orig


def test_voice_core_gap_row_states_how_many_examples_it_checked(env):
    """A "0 gaps" verdict must say how many quoted examples were actually
    checked — otherwise it reads identically to a row that never ran, the
    same failure class as the summary banner bug above, one row down."""
    results = env.run_all()
    row = next(r for r in results if r["name"] == "Voice guide names what it enforces")
    assert row["ok"] is True
    assert "Checked" in row["detail"] and "quoted example" in row["detail"]
    import re
    m = re.search(r"Checked (\d+) quoted example", row["detail"])
    assert m and int(m.group(1)) > 0


def test_db_copy_scan_states_execution_on_a_clean_db(env, monkeypatch):
    """The Database-backed copy section on /admin/checks must say what it
    scanned even when it finds nothing — a clean scan and a scan that
    silently skipped every table both used to render the identical green
    "no violations" message."""
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    from fastapi.testclient import TestClient
    import webapp.app as appmod
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "admin", "password": "adminpass"})
    r = c.get("/admin/checks")
    assert r.status_code == 200
    assert "Scanned" in r.text and "column" in r.text and "table" in r.text
    assert "No banned words, filler, performative" in r.text


def test_db_copy_scan_shows_a_skipped_table_as_amber_not_clean(env, monkeypatch):
    """The exact regression this feature exists to prevent: forcing one
    configured table to fail its query must render a visibly distinct
    (amber) "could not be scanned" notice, never the plain green banner a
    genuinely clean scan gets."""
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    import linklib.voice_db_scan as scan_mod
    from fastapi.testclient import TestClient
    import webapp.app as appmod
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "admin", "password": "adminpass"})

    orig_tables = scan_mod._SCAN_TABLES
    scan_mod._SCAN_TABLES = (("nonexistent_table", "id", ("name",), ()),) + orig_tables[1:]
    try:
        r = c.get("/admin/checks")
        assert r.status_code == 200
        assert "could not be scanned" in r.text
        assert "nonexistent_table" in r.text
    finally:
        scan_mod._SCAN_TABLES = orig_tables


# --- Disk space (2026-09 addendum) -------------------------------------------
# webapp.checks.disk_space_status() reads live via shutil.disk_usage against
# a hardcoded _DISK_VOLUME_PATH ("/data", the documented Railway volume mount)
# — never shells out to `df`. Returns None (not a failure) when that path
# doesn't exist, which is always true in this sandbox/CI, so most of these
# tests monkeypatch the module-level path constant to a real temp directory
# rather than trying to fake the actual Railway mount.

def test_disk_space_status_none_when_volume_missing(env):
    # The real assertion this sandbox always exercises for free: /data
    # genuinely doesn't exist here, so this is the live, unfaked behavior.
    assert env.disk_space_status() is None


def test_disk_space_status_reports_real_numbers(env, monkeypatch, tmp_path):
    monkeypatch.setattr(env, "_DISK_VOLUME_PATH", str(tmp_path))
    db_file = tmp_path / "library.db"
    db_file.write_bytes(b"x" * 1024)
    status = env.disk_space_status(db_path=str(db_file))
    assert status is not None
    assert status["db_size"] == 1024
    assert status["total"] > 0
    assert status["level"] in ("ok", "warn", "critical")
    assert status["can_vacuum"] is True  # a 1KB db needs no real scratch space


def test_disk_space_status_can_vacuum_is_false_when_db_exceeds_free_space(env, monkeypatch, tmp_path):
    monkeypatch.setattr(env, "_DISK_VOLUME_PATH", str(tmp_path))
    total, used, free = env.shutil.disk_usage(str(tmp_path))
    # A db bigger than the volume's own total can never fit in free space —
    # exercises the "cannot VACUUM in place" branch without filling a real disk.
    huge = total + 1
    db_file = tmp_path / "library.db"
    db_file.write_bytes(b"\0")
    orig_getsize = env.os.path.getsize
    monkeypatch.setattr(env.os.path, "getsize", lambda p: huge if p == str(db_file) else orig_getsize(p))
    status = env.disk_space_status(db_path=str(db_file))
    assert status["db_size"] == huge
    assert status["can_vacuum"] is False


def test_disk_space_status_thresholds(env):
    assert env._DISK_WARN_PERCENT == 75
    assert env._DISK_CRITICAL_PERCENT == 85
    assert env._DISK_WARN_PERCENT < env._DISK_CRITICAL_PERCENT


def test_admin_checks_shows_unavailable_disk_banner_when_no_volume(env, monkeypatch):
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    from fastapi.testclient import TestClient
    import webapp.app as appmod
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "admin", "password": "adminpass"})
    r = c.get("/admin/checks")
    assert r.status_code == 200
    assert "Disk space" in r.text
    assert "/data" in r.text
    assert "No" in r.text  # the "no volume on this host" state, not a fake healthy row


def test_admin_checks_shows_real_disk_numbers_when_volume_present(env, monkeypatch):
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    import webapp.checks as checksmod
    # Fixed, real-looking numbers from the actual production incident this
    # feature closes — proves the banner renders exactly what it's given,
    # independent of disk_space_status's own computation (covered above).
    fixed = {
        "volume_path": "/data", "total": 434 * 1024 * 1024, "used": 254 * 1024 * 1024,
        "free": 171 * 1024 * 1024, "percent_used": 58.5, "db_path": "/data/library.db",
        "db_size": 242 * 1024 * 1024, "can_vacuum": False, "level": "ok",
    }
    monkeypatch.setattr(checksmod, "disk_space_status", lambda *a, **k: fixed)
    from fastapi.testclient import TestClient
    import webapp.app as appmod
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "admin", "password": "adminpass"})
    r = c.get("/admin/checks")
    assert r.status_code == 200
    assert "254M" in r.text
    assert "434M" in r.text
    assert "cannot" in r.text.lower()  # can_vacuum False → the "cannot run in place" note
