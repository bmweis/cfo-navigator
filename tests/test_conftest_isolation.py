"""Regression tests for tests/conftest.py's suite-wide isolation.

The two `test_*_leak_*` / `test_*_clean_*` pairs depend on running in file
order, in one process: the first test deliberately dirties module-level state
the way a real test could, the second asserts it starts clean. Run with
`--noconftest` to see them fail (that is the fail-first proof): the dirtied
state survives into the next test because nothing resets it.
"""
import importlib
import time

import pytest


# -- #609: module globals that survive a test's own reload of webapp.app ------

def test_globals_leak_step_one_dirty_everything():
    from webapp import tasks
    from linklib import feed, models, sources
    tasks._checks_cache = (time.time(), 7)
    tasks._checks_computing = True
    tasks._checks_last_attempt_at = 123.0
    tasks._checks_last_error = "boom"
    feed._cache["https://leak.example/feed"] = (time.time(), [{"title": "leaked"}])
    models._cache["data"] = ["leaked"]
    models._cache["at"] = time.time()
    sources.preferred_domains()          # populates the lru_cache
    assert sources.preferred_domains.cache_info().currsize >= 1


def test_globals_clean_step_two_nothing_leaked_in():
    from webapp import tasks
    from linklib import feed, models, sources
    assert tasks._checks_cache is None
    assert tasks._checks_computing is False
    assert tasks._checks_last_attempt_at is None
    assert tasks._checks_last_error is None
    assert feed._cache == {}
    assert models._cache == {"data": None, "at": 0.0}
    assert sources.preferred_domains.cache_info().currsize == 0


def test_static_check_cache_is_deliberately_not_reset():
    """The static-source cache must survive per-test resets (see conftest)."""
    from webapp import tasks
    tasks.reset_static_check_cache()
    tasks.cached_static_check("conftest-probe", lambda: "kept")
    assert tasks._static_check_cache["conftest-probe"] == "kept"
    # the autouse reset runs between tests, not inside one; the next test
    # proves it left the cache alone.


def test_static_check_cache_survived_the_previous_tests_reset():
    from webapp import tasks
    assert tasks._static_check_cache is not None
    assert tasks._static_check_cache.get("conftest-probe") == "kept"
    tasks.reset_static_check_cache()


# -- #610: coral scan stub, re-applied after reloads, with an opt-in ----------

def _fresh_app(monkeypatch, tmp_path):
    monkeypatch.setenv("LINKLIB_DB", str(tmp_path / "t.db"))
    import webapp.app as appmod
    importlib.reload(appmod)
    return appmod


def test_default_tests_get_the_coral_stub_even_after_a_reload(monkeypatch, tmp_path):
    appmod = _fresh_app(monkeypatch, tmp_path)
    assert appmod.coral_moment_problems.__name__ == "_stub"
    assert appmod.coral_moment_problems() == []


@pytest.mark.real_coral
def test_the_real_function_is_restored_after_a_stubbed_test():
    # Runs after the stubbed test above, with no reload of its own and no stub
    # applied (real_coral): a stub left behind by that test's teardown would
    # still be on the module object here.
    import sys
    mod = sys.modules.get("webapp.app")
    assert mod is not None
    assert mod.coral_moment_problems.__name__ == "coral_moment_problems"


@pytest.mark.real_coral
def test_real_coral_marker_opts_back_into_the_real_scan(monkeypatch, tmp_path):
    appmod = _fresh_app(monkeypatch, tmp_path)
    assert appmod.coral_moment_problems.__name__ == "coral_moment_problems"
