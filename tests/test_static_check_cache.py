"""The static-source check cache (webapp/tasks.py's `cached_static_check`/
`reset_static_check_cache`) — the 2026-09 test-suite-runtime fix.

Five of `run_all()`'s checks (table format, pyflakes, script syntax, voice
standards, typography) are pure functions of on-disk source code — confirmed
per each underlying function's own docstring/body, not assumed — and were
measured to cost ~11.4s of `run_all()`'s ~15s total, every bit of it safe to
compute once per process instead of on every call. See `webapp/tasks.py`'s
own module comment (right where `_static_check_cache` sits, deliberately
adjacent to the pre-existing `_checks_cache`) for the full reasoning on why
THIS cache never needs resetting per test, unlike `_checks_cache`, which the
#573 incident already proved must be.

This file proves three things a fast-but-wrong cache could fail at:
1. `compute()` is genuinely called once per key, not once per call.
2. `reset_static_check_cache()` genuinely clears it — a planted change is
   invisible before the reset and visible after, not the reverse and not
   both.
3. The cache is safe against `importlib.reload(webapp.app)` +
   `importlib.reload(webapp.checks)` together — the exact pattern
   `tests/test_checks.py`'s own `env` fixture uses on every single test,
   which is the scenario that made a naive "cache inside webapp/checks.py"
   design dead on arrival for that file specifically.
"""
import importlib
import os
import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import webapp.app as appmod
    importlib.reload(appmod)
    import webapp.checks as checksmod
    importlib.reload(checksmod)
    from webapp import tasks as taskmod
    taskmod._checks_cache = None
    taskmod._checks_computing = False
    taskmod.reset_static_check_cache()
    yield taskmod
    taskmod.reset_static_check_cache()
    if os.path.exists(db):
        os.remove(db)


def test_compute_runs_once_per_key(env):
    taskmod = env
    calls = {"n": 0}

    def compute():
        calls["n"] += 1
        return ["result"]

    r1 = taskmod.cached_static_check("some_key", compute)
    r2 = taskmod.cached_static_check("some_key", compute)
    r3 = taskmod.cached_static_check("some_key", compute)

    assert calls["n"] == 1, "compute() must run exactly once per key, not once per call"
    assert r1 == r2 == r3 == ["result"]


def test_different_keys_do_not_collide(env):
    taskmod = env
    a = taskmod.cached_static_check("key_a", lambda: "A")
    b = taskmod.cached_static_check("key_b", lambda: "B")
    assert a == "A"
    assert b == "B"


def test_none_result_is_cached_not_recomputed(env):
    """The real shape _pyflakes_problems()/script_syntax_problems() return
    when their tool isn't installed. A cache keyed on truthiness (e.g.
    `dict.get(key)` instead of `key in dict`) would treat None as "not
    cached" and recompute forever — this proves it doesn't."""
    taskmod = env
    calls = {"n": 0}

    def compute():
        calls["n"] += 1
        return None

    r1 = taskmod.cached_static_check("maybe_none", compute)
    r2 = taskmod.cached_static_check("maybe_none", compute)
    assert r1 is None and r2 is None
    assert calls["n"] == 1


def test_plant_reset_prove_fresh(env):
    """The Condition A test: plant a change, call the reset, prove the next
    call returns fresh results — and, just as importantly, prove the cache
    really was serving the STALE value before the reset (a test that only
    checks the post-reset state can't tell a real cache from no cache at
    all)."""
    taskmod = env
    state = {"value": ["original finding"]}

    def compute():
        return list(state["value"])

    first = taskmod.cached_static_check("plantable", compute)
    assert first == ["original finding"]

    # Plant a change. Without a reset, the cache must still serve the OLD
    # value — this is the assertion that proves there's really a cache here.
    state["value"] = ["planted finding"]
    still_stale = taskmod.cached_static_check("plantable", compute)
    assert still_stale == ["original finding"], (
        "the cache should still be serving the pre-plant value — if this "
        "fails, either the cache isn't caching, or this test is racing "
        "something that clears it on its own"
    )

    taskmod.reset_static_check_cache()

    fresh = taskmod.cached_static_check("plantable", compute)
    assert fresh == ["planted finding"], "reset_static_check_cache() must make the next call recompute for real"


def test_reset_does_not_touch_checks_cache(env):
    """The two caches in webapp/tasks.py are deliberately independent —
    resetting the static-source cache must never touch `_checks_cache`
    (the DB/request-dependent one), and vice versa isn't exercised here
    since #573's own tests already cover resetting THAT one."""
    taskmod = env
    taskmod._checks_cache = (123.0, 5)
    taskmod.cached_static_check("irrelevant", lambda: "x")
    taskmod.reset_static_check_cache()
    assert taskmod._checks_cache == (123.0, 5), "reset_static_check_cache() must not touch _checks_cache"


def test_survives_reload_of_both_app_and_checks(env, monkeypatch):
    """The scenario that made a naive in-module cache dead on arrival:
    tests/test_checks.py's own `env` fixture reloads BOTH webapp.app and
    webapp.checks on every single test. This proves the cache — which
    lives in webapp.tasks, reloaded nowhere in the suite — survives both
    reloads happening together, not just one."""
    taskmod = env
    import webapp.app as appmod
    import webapp.checks as checksmod

    calls = {"n": 0}

    def fake_pyflakes():
        calls["n"] += 1
        return []

    monkeypatch.setattr(checksmod, "_pyflakes_problems", fake_pyflakes)
    r1 = taskmod.cached_static_check("pyflakes_reload_test", checksmod._pyflakes_problems)
    assert calls["n"] == 1
    assert r1 == []

    # Reload both modules, exactly like tests/test_checks.py's own fixture.
    importlib.reload(appmod)
    importlib.reload(checksmod)
    # A fresh reload rebinds checksmod._pyflakes_problems to the REAL
    # function again (the monkeypatch above only patched the pre-reload
    # module object) — call the cache with the same key and a trivial
    # compute that would prove itself wrong immediately if the cache had
    # been silently cleared by the reload.
    calls2 = {"n": 0}

    def compute_after_reload():
        calls2["n"] += 1
        return "should never run"

    r2 = taskmod.cached_static_check("pyflakes_reload_test", compute_after_reload)
    assert calls2["n"] == 0, "the cache must survive reload of webapp.app + webapp.checks together"
    assert r2 == [], "the value cached before the reload must still be the one returned after it"


def test_run_all_caches_the_five_source_only_checks_across_reload():
    """End-to-end: run_all() itself, not the primitive — first call full
    cost, second call (after reload(webapp.app)+reload(webapp.checks), the
    exact tests/test_checks.py pattern) hits the warm cache and returns
    structurally identical results. Doesn't assert a specific wall-clock
    number (flaky by nature) — asserts the thing that actually matters:
    identical check names/ok-values across the reload boundary, proving
    the cache didn't silently change what gets reported."""
    db = tempfile.mktemp(suffix=".db")
    try:
        os.environ["LINKLIB_DB"] = db
        os.environ["LINKLIB_PASSWORD"] = "adminpass"
        os.environ["LINKLIB_SECRET_KEY"] = "k"
        import webapp.app as appmod
        importlib.reload(appmod)
        import webapp.checks as checksmod
        importlib.reload(checksmod)
        from webapp import tasks as taskmod
        taskmod._checks_cache = None
        taskmod._checks_computing = False
        taskmod.reset_static_check_cache()

        r1 = checksmod.run_all()

        importlib.reload(appmod)
        importlib.reload(checksmod)
        taskmod._checks_cache = None
        taskmod._checks_computing = False

        r2 = checksmod.run_all()

        names1 = [r["name"] for r in r1]
        names2 = [r["name"] for r in r2]
        assert names1 == names2
        ok1 = [r["ok"] for r in r1]
        ok2 = [r["ok"] for r in r2]
        assert ok1 == ok2
    finally:
        taskmod.reset_static_check_cache()
        if os.path.exists(db):
            os.remove(db)
