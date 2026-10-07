"""Suite-wide test isolation (2026-10 test hygiene batch).

Two autouse behaviors, both kept deliberately small and explicit:

1. Reset the module-level state that survives a test's own
   `importlib.reload(webapp.app)` (webapp.app re-executes on reload, so its
   globals reset for free; the modules below are never reloaded, so anything
   a test leaves in them leaks into every later test in the process):
     - webapp.tasks   : the _failing_checks_count() cache and its in-flight
                        sentinel, plus the refresher's last-attempt fields
     - linklib.feed   : the per-feed 30-minute cache
     - linklib.models : the live Models API cache
     - linklib.sources: preferred_domains()'s lru_cache
     - linklib.pricing: the model_pricing price cache and its warned-once set
   NOT reset, on purpose: webapp.tasks._static_check_cache. That one is a
   pure function of on-disk source and is meant to live for the whole process
   (see the long comment above it in webapp/tasks.py; resetting it per test
   would undo the ~11s-per-call saving it exists for). A test that patches
   something reachable from it still calls reset_static_check_cache() itself.

2. Replace webapp.app.coral_moment_problems() with a stub returning "no
   problems" for every test that does not need the real scan. The real one
   renders every public route through TestClient (~2s each time, and it runs
   inside every run_all(), which every admin-page render reaches). A test
   that needs the real scan opts back in with `@pytest.mark.real_coral` (or
   `pytestmark = pytest.mark.real_coral` for a whole module), or by using the
   `no_password_env` fixture, whose whole purpose is the open-auth re-entrant
   call chain that scan triggers.

   The app is reloaded inside most tests' own fixtures, which re-executes the
   module and would throw a patch away, so the stub is re-applied after every
   `importlib.reload(webapp.app)`.
"""
from __future__ import annotations

import os
import importlib

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "real_coral: run the real coral_moment_problems() page scan instead of the suite-wide stub")
    config.addinivalue_line(
        "markers",
        "no_admin_seed: do not auto-create the 'admin' users row on login (tests of the retired fallback)")


def _reset_module_globals() -> None:
    from webapp import tasks
    tasks._checks_cache = None
    tasks._checks_computing = False
    tasks._checks_last_attempt_at = None
    tasks._checks_last_error = None
    from linklib import feed, models, sources
    feed._cache.clear()
    models._cache["data"] = None
    models._cache["at"] = 0.0
    sources.preferred_domains.cache_clear()
    from linklib import pricing
    pricing.reset_price_cache()


def _wants_real_coral(request) -> bool:
    return (request.node.get_closest_marker("real_coral") is not None
            or "no_password_env" in request.fixturenames)


@pytest.fixture(autouse=True)
def _isolate_module_globals():
    _reset_module_globals()
    yield
    _reset_module_globals()


@pytest.fixture(autouse=True)
def _stub_coral_scan(request, monkeypatch):
    if _wants_real_coral(request):
        yield
        return

    import sys
    real_fns: list = []

    def _stub() -> list[str]:
        return []

    def _apply(mod) -> None:
        real_fns.append((mod, mod.coral_moment_problems))
        mod.coral_moment_problems = _stub

    real_reload = importlib.reload

    def _reload(module, *args, **kwargs):
        out = real_reload(module, *args, **kwargs)
        if getattr(out, "__name__", "") == "webapp.app":
            _apply(out)
        return out

    monkeypatch.setattr(importlib, "reload", _reload)
    app_mod = sys.modules.get("webapp.app")
    if app_mod is not None:
        _apply(app_mod)
    yield
    # Put back the real function on each module object we touched. Entries are
    # in capture order, so for a module reloaded mid-test the last entry (the
    # freshly reloaded real function) is the one that sticks.
    for mod, fn in real_fns:
        mod.coral_moment_problems = fn


@pytest.fixture(autouse=True)
def _seed_admin_on_login(request, monkeypatch):
    """The shared-secret login fallback was retired (issue #627), so a login as
    "admin" needs a real `users` row. Hundreds of tests log in as
    admin/<LINKLIB_PASSWORD> against a fresh temp DB; this creates that row
    lazily, only at the moment such a login is attempted, so a test that makes
    its own "admin" user first is unaffected. Opt out with
    `@pytest.mark.no_admin_seed` (tests/test_break_glass_retired.py does)."""
    if request.node.get_closest_marker("no_admin_seed") is not None:
        return
    from linklib.db import Library
    original = Library.authenticate

    def authenticate(self, username, password):
        user = original(self, username, password)
        if user is None and (username or "").strip().lower() == "admin":
            secret = os.environ.get("LINKLIB_PASSWORD") or os.environ.get("LINKLIB_SAVE_TOKEN")
            if secret and password == secret and self.get_user("admin") is None:
                self.create_user("admin", secret, role="admin", password_change_recommended=False)
                user = original(self, username, password)
        return user

    monkeypatch.setattr(Library, "authenticate", authenticate)
