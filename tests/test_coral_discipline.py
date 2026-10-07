"""Coral discipline (PR 16, 2026-09) — at most one coral "moment" per public
page, checked signed out. See webapp/app.py's `coral_moment_problems()` own
module comment for the full design and its honest, stated limits (signed-out
snapshot only, inline `style=` backgrounds only, non-admin/no-path-param
routes only).

Found 2026-09: `_CARD_ICON_STYLES` cycled seafoam/navy/coral by array index,
so whichever card landed in the third slot spent the site's one rare accent
by accident, not deliberate placement — Communities on /tools, and (twice
over) on the homepage. This suite proves the detector actually catches that
class of regression (not just passes trivially against whatever the current
code happens to render) and that the real, current pages are clean.
"""
import pathlib
import sys
import tempfile
import os

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


# The whole module exercises the real page scan (see tests/conftest.py).
pytestmark = pytest.mark.real_coral


@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    yield appmod
    if os.path.exists(db):
        os.remove(db)


def test_no_coral_moment_violations_today(env):
    """The real, current state: every checked public page has at most one
    coral moment, signed out. This is the "passes once the gap is fixed"
    half of the build brief — it exercises the live routes exactly as
    committed, not a rigged fixture."""
    assert env.coral_moment_problems() == []


def test_detector_actually_catches_an_injected_second_coral_moment(env, monkeypatch):
    """Proves the detector isn't trivially passing: inject a second coral
    background into the MCP callout (the one deliberate coral moment /tools
    and the homepage already have) and confirm both pages are flagged."""
    orig = env._mcp_callout_html

    def rigged():
        return orig() + '<div style="background:var(--coral-wash);">extra</div>'

    monkeypatch.setattr(env, "_mcp_callout_html", rigged)

    problems = env.coral_moment_problems()
    # The homepage dropped the callout (plain MCP text, 2026-10), so only /tools
    # carries the coral moment now and only /tools can be pushed over the limit.
    assert any(p.startswith("/tools ") for p in problems), problems
    assert not any(p.startswith("/ ") for p in problems), problems


def test_card_icon_styles_no_longer_cycles_coral(env):
    """The actual fix: coral is dropped from the icon cycle entirely, not
    just reordered — every entry is seafoam or navy."""
    for bg, stroke in env._CARD_ICON_STYLES:
        assert "coral" not in bg
        assert "coral" not in stroke
    assert len(env._CARD_ICON_STYLES) == 2


def test_mcp_callout_is_coral_not_navy(env):
    """The /tools MCP callout is that page's one deliberate coral moment."""
    html = env._mcp_callout_html()
    assert "var(--coral-wash)" in html
    assert "var(--navy-wash)" not in html
    # non-clickable statement of capability, never a coral button
    assert "<a " not in html
    assert "<button" not in html


def test_checks_run_all_reports_coral_discipline_pass(env):
    # Unlike hub_nav_orphans (pure route/tuple introspection), this check
    # does real signed-out HTTP GETs against routes that touch the DB, so it
    # needs the `env` fixture's real (empty, schema-initialized) database —
    # not the ambient/unset LINKLIB_DB the process might otherwise have.
    from webapp import checks
    results = checks.run_all()
    row = next(r for r in results if r["name"] == "Coral discipline (one moment per page)")
    assert row["ok"] is True
    assert row["where"] == "Live + CI"


@pytest.fixture
def no_password_env(monkeypatch):
    """No LINKLIB_PASSWORD/LINKLIB_SAVE_TOKEN at all — the documented
    "open, local dev convenience" auth mode, where _is_authed()/_role()
    treat every visitor as admin. Deliberately not the `env` fixture
    above, which always sets a password."""
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.delenv("LINKLIB_PASSWORD", raising=False)
    monkeypatch.delenv("LINKLIB_SAVE_TOKEN", raising=False)
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    yield appmod
    if os.path.exists(db):
        os.remove(db)


def test_coral_check_does_not_recurse_in_open_auth_mode(no_password_env):
    """Real bug found while building this check, not theoretical: in open
    auth mode (no password configured) every page renders role="admin", so
    _page()'s nav badge computation (_has_open_admin_tasks() ->
    webapp.tasks._failing_checks_count()) calls checks.run_all() on EVERY
    page render — including the pages coral_moment_problems() itself
    renders. Before the _CORAL_CHECK_IN_PROGRESS guard, this recursed:
    coral_moment_problems() -> renders "/" -> _page() (role="admin") ->
    _has_open_admin_tasks() -> checks.run_all() -> coral_moment_problems()
    again, without end — reproduced live to at least depth 3 before being
    killed. Confirms both that the top-level call still returns a real,
    non-empty-by-construction result (not just "didn't hang") and that a
    nested call (simulated directly, the same way the recursion actually
    reaches it) returns [] rather than recursing.

    2026-09 update: the guard is now call-chain-scoped state
    (_CORAL_CHECK_CONTEXT, a contextvars.ContextVar — not a module-level
    bool, and not threading.local() either; both were tried and found
    wrong, see webapp/app.py's own comment for the full story). This test
    simulates "the guard is already active on this call chain" by setting
    the ContextVar directly (same-thread, same-context, so the set is
    visible to the very next call exactly as it would be mid-recursion);
    the same-chain short-circuit behavior is unchanged."""
    problems = no_password_env.coral_moment_problems()
    assert isinstance(problems, list)   # returned at all — the actual regression

    token = no_password_env._CORAL_CHECK_CONTEXT.set(True)
    try:
        assert no_password_env.coral_moment_problems() == []
    finally:
        no_password_env._CORAL_CHECK_CONTEXT.reset(token)


def test_admin_nav_badge_computation_does_not_hang_in_open_auth_mode(no_password_env):
    """The actual real-world trigger: _has_open_admin_tasks() is what every
    page render calls when role=="admin", which is every page in open auth
    mode. Asserts it returns promptly rather than hanging — this is what
    tests/test_checks.py's own hang (before that file started setting
    LINKLIB_PASSWORD like every other test fixture) actually traced to."""
    result = no_password_env._has_open_admin_tasks()
    assert isinstance(result, bool)


def test_same_request_recursion_stays_bounded_across_threadpool_workers(
    no_password_env,
):
    """Regression test for the bug a `threading.local()` version of this
    guard shipped with, briefly, before being caught and reverted in this
    same PR: a real signed-out GET "/" in open-auth mode recurses through
    _page()'s admin-nav badge computation back into
    coral_moment_problems() — and each of that recursion's nested
    TestClient(...).get(path) calls is dispatched by Starlette's own
    run_in_threadpool onto a THREADPOOL WORKER THREAD, which need not be
    (and, measured directly, usually isn't) the same OS thread that's
    already running the outer call. A per-OS-thread guard cannot see
    "already in progress" across that hop, so each nested level saw a
    fresh, unset flag and started a whole new real pass over every
    route — unbounded threadpool growth, confirmed to still be recursing
    at depth 5+ after 25 real seconds before being killed in manual
    testing. The correct guard (contextvars.ContextVar, which anyio
    explicitly copies into each dispatched worker thread) keeps this
    bounded, restoring the plain-module-global baseline of roughly
    15-20 seconds for one such request. This test doesn't assert an exact
    duration (this sandbox is not a stable timing environment) — it
    asserts the request actually FINISHES within a generous bound in a
    background thread, which the threading.local() version did not do
    even at 10x that bound."""
    import threading as _threading
    from fastapi.testclient import TestClient

    client = TestClient(no_password_env.app, raise_server_exceptions=False)
    result = {}

    def run():
        result["resp"] = client.get("/", follow_redirects=False)

    t = _threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout=90)
    assert not t.is_alive(), (
        "GET \"/\" in open-auth mode did not finish within 90s — this is "
        "exactly the unbounded-recursion regression a per-OS-thread guard "
        "(threading.local()) reintroduces; it must stay bounded via the "
        "call-chain-scoped contextvars.ContextVar guard instead"
    )
    assert result["resp"].status_code == 200


def test_concurrent_calls_do_not_leak_in_progress_state_across_threads(
    no_password_env, monkeypatch
):
    """The real cross-thread race, deliberately synchronized rather than
    hoped-for: thread A takes the guard and blocks mid-check (simulating an
    ordinary admin page render against a stale checks cache);  thread B
    calls coral_moment_problems() while A is still "in progress" and must
    get its OWN independent, correct result — never a false-negative []
    leaked from A's still-active state.

    This test is written to fail deterministically against the old
    module-level `_CORAL_CHECK_IN_PROGRESS` global (thread B reads A's
    flag, sees True, and short-circuits to [] without ever running its own
    check) and to pass against `threading.local()` (thread B's own flag is
    independently False, so it runs for real). See the PR description for
    real captured output from both runs — a green test that would have
    been green before the fix proves nothing, so this one was run against
    both states before being trusted.
    """
    import threading as _threading

    a_started = _threading.Event()
    a_may_finish = _threading.Event()
    call_count = {"n": 0}

    def fake_routes():
        call_count["n"] += 1
        if call_count["n"] == 1:
            # Thread A: signal it has taken the guard, then block — this is
            # thread A "mid-check", the exact window the real race exploits.
            a_started.set()
            a_may_finish.wait(timeout=5)
            return []
        # Thread B, if it actually reaches here (only possible with a
        # per-thread guard): a single real route to check for real.
        return ["/"]

    def fake_moments(html):
        # Force "more than one coral moment" unconditionally, so a non-empty
        # result can only mean thread B actually ran its own check for
        # real — not that it got lucky with real page content.
        return 5

    monkeypatch.setattr(no_password_env, "_coral_check_routes", fake_routes)
    monkeypatch.setattr(no_password_env, "_coral_moments_on_page", fake_moments)

    result_b = {}

    def run_a():
        no_password_env.coral_moment_problems()

    def run_b():
        assert a_started.wait(timeout=5), "thread A never reached in-progress"
        result_b["problems"] = no_password_env.coral_moment_problems()

    t_a = _threading.Thread(target=run_a)
    t_b = _threading.Thread(target=run_b)
    t_a.start()
    assert a_started.wait(timeout=5), "thread A never reached in-progress"
    t_b.start()
    t_b.join(timeout=10)
    a_may_finish.set()
    t_a.join(timeout=10)

    assert not t_a.is_alive() and not t_b.is_alive(), "a thread never finished"
    assert result_b.get("problems") == ["/ (5 coral moments)"], (
        "thread B's result was suppressed by thread A's unrelated "
        f"in-progress state instead of running its own check: {result_b}"
    )
