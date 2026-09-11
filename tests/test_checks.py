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
