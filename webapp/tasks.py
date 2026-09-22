"""Open-task badge counts for the admin hub's notification badges — recolored
from coral to `--alert` in the 2026-09 "count everything that needs
attention" pass (see below): a needs-attention signal is a status, and per
BRAND.md status belongs to the `--good`/`--caution`/`--alert` family, coral
is a rare accent. Flagging the reversal explicitly, same as every other
documented color-token change in this codebase — `.task-dot`/`.task-badge`
had used coral since the original admin-hub badge build, and CLAUDE.md once
described that as a sanctioned exception; it no longer is.

Each entry in `open_task_counts` is keyed by the admin href it badges — the
hub page sums the hrefs belonging to a group for the collapsed section badge,
and looks up individual hrefs for the expanded sub-item badges. A href with
no entry (or a zero count) shows no badge. The top nav's own `.task-dot`
(`_has_open_admin_tasks()`) is now a real number too, not a presence dot —
`sum(open_task_counts(lib).values())`, rendered via
`webapp.app._admin_nav_badge()` — since "something needs attention" and "12
things need attention" are a different decision to make, the same reasoning
PR 28 (2026-09) already applied to every admin-page badge below.

2026-09 coverage pass: three sources that were computed live on /admin/checks
but never reached ANY badge are folded in here — the failing "Live + CI"
run_all() rows already did (`_failing_checks_count()`), but the three dated
freshness reminders (Pricing, New-model awareness, Exa pricing) and backup
staleness did not. The "Database-backed copy" scan on /admin/checks
deliberately does NOT get its own separate count here — see
`_stale_admin_checks_reminders`'s own docstring for why: those findings are
the same underlying violations `/admin/voice/review-queue` already counts
(the backfill script populates the queue FROM that scan, and every write
since logs new findings there directly), and double-counting them under two
different hrefs would inflate the total past what's actually pending.

Deliberately reads from data that already exists (queue/flagged counts,
unapproved tools, failing checks) rather than a new task-tracking table —
the one addition is a pair of `settings` timestamps (`admin_viewed_contacts`,
`admin_viewed_tool_leads`) so append-only logs with no other state can still
answer "is there something new since Brian last looked."

`DOT_ONLY_HREFS` used to mark sources reviewed as one full list at once,
with no per-item view or action (Contact submissions, Toolbox intros) —
webapp.app's admin-page badge helpers rendered those as a plain dot instead
of a count, on the theory that "3 things to look at" implies a per-item
granularity those two sources don't have. **Retired (PR 28, 2026-09), per
Brian's explicit call**: the admin page (`/admin`) is where he decides what
to work on next, and a real count there is strictly more useful than a dot
even for an all-or-none source — "4 new messages" and "1 new message" are a
different decision to make, whatever the drill-down looks like once you get
there. Every admin-page badge (`webapp.app._badge_for_href`/`_group_badge`)
is a plain numeric count now, with no exceptions. **The top nav bar's own
`.task-dot` is no longer exempt either (2026-09 follow-up)** — it read as a
pure presence boolean up through PR 28, deliberately unlike every other
badge on the site; Brian's own ask ("show a number, not just a dot") ends
that distinction, so `_has_open_admin_tasks()` stays (still used for the
occasional pure-boolean check) but the nav itself now renders the same
`sum(open_task_counts(lib).values())` total every other aggregate badge on
this page already computes. See CLAUDE.md's PR 28 note for the DOT_ONLY_HREFS
history and the "logged-in slowness"/badge-coverage investigation note for
this follow-up.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time

from linklib import backup as _backup
from linklib.db import Library
from linklib.models import models_review_is_stale
from linklib.pricing import exa_pricing_review_is_stale, pricing_review_is_stale
from webapp import checks as _checks

_logger = logging.getLogger(__name__)

# _failing_checks_count() badges /admin and /admin/library with the same
# in-app check results /admin/checks itself computes live — but
# _checks.run_all() re-lints the entire linklib/webapp/scripts tree with
# pyflakes and spawns a node --check subprocess per shared <script> block
# (2026-08 perf investigation: ~3.5s on a fresh dashboard render, with no
# caching anywhere in the call chain, so every /admin or /admin/library
# render paid this in full — a real cost in production and the single
# biggest line item in the test suite's wall-clock time, since dozens of
# tests render one of these two pages). Cached here with a short TTL, same
# pattern as linklib.feed's per-feed cache — the check results only change
# when the source code changes, which doesn't happen more than once every
# few minutes even during active development, so a short TTL keeps the
# badge close to live while eliminating the repeat cost. /admin/checks
# itself is untouched and still calls run_all() directly on every view —
# only the badge count is cached.
_CHECKS_CACHE_TTL = 120  # seconds
_checks_cache_lock = threading.Lock()
_checks_cache: tuple[float, int] | None = None

# 2026-09 (coral-PR follow-up): the naive fix here — a plain lock held
# across the run_all() call, so a concurrent cache-miss caller waits for
# whoever's already computing instead of redoing the work — is UNSAFE.
# This function is genuinely re-entered through its own call chain:
# run_all() -> coral_moment_problems() renders every public route, and in
# open-auth mode (no LINKLIB_PASSWORD/LINKLIB_SAVE_TOKEN — see
# webapp.app's own comment on _CORAL_CHECK_CONTEXT for why that's the
# trigger) every one of those renders is role="admin", so _page() calls
# _has_open_admin_tasks() -> this function again — on a DIFFERENT OS
# thread than the one already running the outer call, since anyio's
# run_in_threadpool dispatches each nested TestClient render onto its own
# threadpool worker (the exact mechanism the coral fix's own comment
# documents; confirmed again here with a live instrumented GET "/" in
# open-auth mode: depth reached exactly 2, on two distinct thread ids,
# confirmed by call-graph reading, not just re-derived from that
# comment). A `threading.Lock` around the compute step would have the
# outer thread hold the lock while it's blocked waiting for the render to
# finish, and the nested call — on that different thread — block trying
# to acquire the very lock the outer thread won't release until the
# nested call returns: a permanent cross-thread deadlock, not a slow
# path. `threading.RLock` does NOT fix this — it only waives re-entry for
# the SAME thread, and the nested call is provably on a different one
# (see above), so RLock blocks it exactly like a plain Lock would.
#
# Fixed instead with a non-blocking sentinel, never a lock a thread can
# wait on: `_checks_computing` marks "a computation is in flight"; any
# caller — genuinely concurrent OR the recursive same-chain case above —
# that sees it set just returns whatever's already cached (or 0, on a
# cold start with nothing cached yet) instead of trying to also compute
# or waiting for the one in flight to finish. Nothing ever blocks on
# another thread's progress, so this can't deadlock regardless of whether
# the second caller is an independent request or this exact function
# re-entering itself nested inside its own first call. The recursive case
# additionally benefits: it no longer redoes run_all() a second time at
# all (the coral-PR-era measurement of "2x run_all(), ~18s" for one
# open-auth page render no longer applies — confirmed below). The only
# behavioral cost is a returned count that can be transiently stale (up
# to the ~3.5-9s a `run_all()` pass takes) during the narrow window a
# computation is actually in flight — acceptable for a nav badge that was
# already only ever a 120s-stale approximation, and never a value
# anything besides that badge depends on for correctness.
_checks_computing = False


def _compute_failing_checks_count() -> int:
    """The actual run_all() pass + count. Pulled out of _failing_checks_count()
    so the background refresher (below) and the synchronous fallback path
    share one implementation rather than two copies that could drift."""
    return sum(1 for r in _checks.run_all() if r["where"] == "In-app" and r["ok"] is False)


def _failing_checks_count() -> int:
    global _checks_cache, _checks_computing
    now = time.time()
    with _checks_cache_lock:
        if _checks_cache is not None and now - _checks_cache[0] < _CHECKS_CACHE_TTL:
            return _checks_cache[1]
        if _checks_computing:
            return _checks_cache[1] if _checks_cache is not None else 0
        _checks_computing = True
    try:
        count = _compute_failing_checks_count()
    finally:
        with _checks_cache_lock:
            _checks_cache = (time.time(), count)
            _checks_computing = False
    return count


# --- Background refresher (2026-09) ------------------------------------------
# The TTL-cache-plus-sentinel above still means the FIRST admin page render
# after each 120s window pays the full run_all() cost inline, blocking that
# one request (measured: several seconds in production even after the
# typography-scan perf fix in linklib/voice_review.py — see CLAUDE.md's
# "logged-in slowness" investigation). That's the real fix this closes: a
# daemon thread recomputes the cache on its own schedule, independent of any
# page render, so in ordinary operation _failing_checks_count() above almost
# always just reads an already-warm cache (a microsecond dict/tuple read)
# rather than ever triggering the compute path itself.
#
# The synchronous fallback in _failing_checks_count() is deliberately left
# completely unchanged, not replaced — it's still correct, still needed (a
# worker that just restarted, or a refresher thread that failed to start,
# has no warm cache to fall back on), and every test in
# tests/test_task_badges.py exercises exactly that fallback path directly,
# with no FastAPI startup event involved (this codebase's tests construct
# TestClient(app) directly, without `with`, which — confirmed empirically,
# not assumed — never fires an @app.on_event("startup") hook; only
# tests/test_seed_toolbox_startup.py opts into that via `with TestClient`).
# So this refresher is purely additive: it changes normal production
# behavior (the cache is usually warm before anyone needs it) without
# changing what happens when it isn't (the exact, already-tested fallback).
#
# Staleness bound: at most one refresh interval (_CHECKS_CACHE_TTL, 120s)
# plus however long one run_all() pass takes to finish (a few seconds) —
# so the badge can be up to roughly 2 minutes behind the true state in the
# worst case, the same order of staleness the TTL cache already promised,
# just no longer paid for by whichever request happens to land first.
#
# Connection safety (2026-09 follow-up, confirmed rather than assumed): this
# thread never shares a sqlite3 connection with any other thread. Every
# check in run_all() that touches the DB (original_content_mirror_problems,
# voice_review_queue_status, and every route coral_moment_problems() renders
# via TestClient) does so through webapp.app._lib(), which always opens a
# brand-new Library(DB_PATH) — and therefore a brand-new sqlite3.connect()
# call — and closes it before returning, entirely within whichever thread
# is currently executing. sqlite3 connections default to check_same_thread=
# True (this codebase never overrides that — grepped linklib/db.py), so a
# shared connection touched from two threads would raise immediately rather
# than silently corrupt anything; the fact that nothing here has ever hit
# that error is a real confirmation this thread opens its own connections,
# not just an absence of evidence.
#
# Worker-process count (2026-09 follow-up): production runs uvicorn with no
# --workers flag (Dockerfile CMD / Procfile both start `uvicorn
# webapp.app:app` bare) — uvicorn's own default is a single worker process,
# matching this codebase's existing single-process assumption elsewhere
# (see _JOB_STATE's own comment above). So today there is exactly one of
# these threads running in production. If that ever changed to N>1 worker
# processes, each is a separate OS process with its own Python interpreter
# and its own copy of every module-level global here (_checks_cache,
# _checks_refresher_started, ...) — nothing in this module is shared across
# processes. Each worker would start and run its own independent refresher,
# computing run_all() on its own schedule against the same underlying
# database. That's safe (no shared-state hazard, no corruption risk — each
# process's cache is self-consistent) but wasteful (N redundant run_all()
# passes every interval instead of one) and can let the badge value
# genuinely differ by request depending on which worker answers it, if two
# workers' refresh cycles drift out of phase. Worth revisiting (e.g. moving
# the schedule into a single cron-style trigger, or accepting the
# redundancy as cheap enough to ignore) only if/when a real multi-worker
# deploy is adopted — not a concern under the current single-process setup.
#
# Test-suite guard (2026-09 follow-up, issue #592 item 2): even the one
# test file that does trigger the startup event (test_seed_toolbox_startup.py,
# via `with TestClient`) no longer actually starts this thread —
# start_background_checks_refresher() itself now no-ops under pytest by
# default (see its own docstring). A leftover refresher thread from an
# earlier test looping in the background, re-reading a since-monkeypatched-
# and-deleted LINKLIB_DB path, was a real "database is locked" flakiness
# risk this closes at the source rather than only narrowing the window with
# the once-per-thread-start db_path fix above.
_checks_refresher_started = False
_checks_refresher_lock = threading.Lock()

# Per-attempt state (2026-09, health-visibility follow-up) — deliberately
# separate from _checks_cache, which only ever holds the last SUCCESSFUL
# result. A refresher that's alive but failing every pass would otherwise
# look identical, from the outside, to one that died outright: the cache
# just sits there, unrefreshed, either way. Tracking every attempt (not
# just successes) is what lets /admin/checks tell those two states apart —
# "last successful refresh 6 minutes ago, last attempt 4 seconds ago" is a
# live-but-failing refresher; "last successful refresh 6 minutes ago, last
# attempt 6 minutes ago" is a dead one.
_checks_last_attempt_at: float | None = None
_checks_last_error: str | None = None


def _reconcile_voice_review_queue_once(db_path: str) -> None:
    """One pass of `Library.reconcile_voice_review_queue()` — the
    bidirectional-sync fix (2026-09): the live DB scan and the review queue
    used to be able to disagree in both directions (a new can't-auto-fix
    finding entering the DB via an ordinary save never reached the queue;
    fixing a violation directly on a record's own edit page never closed
    its stale open queue row). Run from this same background refresher
    loop rather than a request path, for the identical reason run_all()
    itself is TTL-cached/background-refreshed — a full DB scan is real
    work no page render should pay for inline. Opens its own Library
    connection (own thread, own connection — see this module's own
    connection-safety comment above) and never raises past this function;
    a failure here is logged, not propagated, so it can never take the
    checks refresher itself down.

    2026-09 follow-up (issue #592 item 2) — `db_path` is now passed in by
    the caller (resolved once, when the refresher thread starts) rather
    than re-read from `LINKLIB_DB` on every single iteration. Production
    never changes this env var after boot, so the behavior there is
    identical; a per-iteration re-read was a real latent risk under any
    test that monkeypatches LINKLIB_DB per-test while a leftover refresher
    thread from an earlier test is still looping in the background — it
    could pick up a mid-test env value and open a connection against a DB
    file a different test is mid-teardown on ("database is locked")."""
    from linklib.db import Library as _Library
    try:
        lib = _Library(db_path)
        try:
            lib.reconcile_voice_review_queue()
        finally:
            lib.close()
    except Exception:
        _logger.exception("Voice review queue reconciliation pass failed")


def _run_one_refresh_iteration() -> None:
    """One pass: compute + cache on success, log + record on failure — never
    raises. Pulled out of the sleep loop below so a test can call this
    directly, synchronously, without spinning up a thread or waiting on
    time.sleep(_CHECKS_CACHE_TTL) — this is the one code path that actually
    makes production fast, and before this it had no direct test coverage
    at all (only the request-triggered synchronous fallback in
    _failing_checks_count() did)."""
    global _checks_cache, _checks_last_attempt_at, _checks_last_error
    with _checks_cache_lock:
        _checks_last_attempt_at = time.time()
    try:
        count = _compute_failing_checks_count()
    except Exception as exc:
        # Never let one bad pass kill the loop — leave whatever's cached
        # (possibly nothing yet) and try again next interval. Logged, not
        # silently swallowed, so a persistently failing refresher shows up
        # in Railway's own logs even before anyone looks at /admin/checks.
        _logger.exception("Background checks refresher iteration failed")
        with _checks_cache_lock:
            _checks_last_error = f"{type(exc).__name__}: {exc}"
    else:
        with _checks_cache_lock:
            _checks_cache = (time.time(), count)
            _checks_last_error = None


def _checks_refresher_loop(db_path: str) -> None:
    while True:
        _run_one_refresh_iteration()
        _reconcile_voice_review_queue_once(db_path)
        time.sleep(_CHECKS_CACHE_TTL)


def start_background_checks_refresher(force: bool = False) -> None:
    """Start the daemon thread that keeps _checks_cache warm on a schedule.
    Called once from webapp.app's own startup hook — idempotent (a second
    call, e.g. from a hot-reload, is a no-op) so nothing here needs its own
    process-level guard beyond this module's own flag.

    2026-09 follow-up (issue #592 item 2), two changes:

    1. The DB path is resolved exactly once, right here, and threaded
       through to the loop/reconciliation pass as a plain argument instead
       of each iteration independently re-reading LINKLIB_DB from the
       environment. Production's path never changes after boot, so this
       costs nothing there — see `_reconcile_voice_review_queue_once`'s own
       docstring for the test-suite risk this closes.

    2. This is a no-op under the test suite by default — `webapp.app`'s
       startup hook fires on every `with TestClient(appmod.app)` (see this
       module's own connection-safety comment above `_checks_refresher_
       started`), and a leftover thread from one test looping in the
       background is exactly the wandering-between-test-databases hazard
       item 2 exists to close, not just the per-iteration re-read. Detected
       via `PYTEST_CURRENT_TEST` — pytest sets this in `os.environ` for the
       duration of every test's setup/call/teardown, a standard, reliable
       way for library code to detect it's running under pytest without a
       pytest import of its own.

       `PYTEST_CURRENT_TEST` is only ever set while a test is actively
       running (setup/call/teardown) — never during collection or module
       import. Checked directly (issue #592's own follow-up review): every
       `with TestClient(...)` in this suite — the only thing that can
       actually trigger `webapp.app`'s startup event and reach this
       function at all — lives inside a `def test_*(...)` function body,
       never at module scope, and there is no conftest.py providing a
       session/module-scoped fixture that could construct one earlier
       either. So under the CURRENT suite there is no real gap: nothing
       ever reaches here before `PYTEST_CURRENT_TEST` exists. As a cheap,
       strictly broader backup guard against a FUTURE test file collecting
       a `TestClient` (or otherwise triggering the startup event) outside
       any test function — where `PYTEST_CURRENT_TEST` would still be
       unset — this also checks `"pytest" in sys.modules`, true for the
       whole pytest process lifetime (collection through final teardown),
       not just while a test is running. Safe in production: `pytest` is
       requirements-dev.txt-only, never installed in the Docker image, so
       this can never be true there. A test that wants to exercise the
       real background thread (rather than calling
       `_run_one_refresh_iteration`/`_reconcile_voice_review_queue_once`
       directly, as every existing refresher test in
       tests/test_task_badges.py already does) can still do so explicitly
       via `force=True`."""
    if not force and ("PYTEST_CURRENT_TEST" in os.environ or "pytest" in sys.modules):
        return
    global _checks_refresher_started
    with _checks_refresher_lock:
        if _checks_refresher_started:
            return
        _checks_refresher_started = True
    db_path = os.environ.get("LINKLIB_DB", "library.db")
    threading.Thread(target=_checks_refresher_loop, args=(db_path,), daemon=True).start()


def refresher_status() -> dict:
    """What /admin/checks renders so a dead or failing refresher is visible
    on the page itself, not inferred from a slow load — the same standing
    rule that section's own copy already states for the disk-space check
    ("a healthy check that says nothing looks identical to one that never
    ran"). Returns last_success_at/last_attempt_at (epoch seconds, or None
    if it hasn't happened yet) and last_error (the most recent exception
    message, or None if the last attempt succeeded or none has run)."""
    with _checks_cache_lock:
        last_success_at = _checks_cache[0] if _checks_cache is not None else None
        return {
            "started": _checks_refresher_started,
            "last_success_at": last_success_at,
            "last_attempt_at": _checks_last_attempt_at,
            "last_error": _checks_last_error,
        }


def _stale_admin_checks_reminders(lib: Library) -> int:
    """0-3 — how many of /admin/checks' three dated manual-attestation
    banners (Pricing freshness, New-model awareness, Exa pricing freshness)
    currently read stale/never-reviewed. These are computed live in the
    admin_checks() route but never fed into run_all()'s "In-app" list (they
    aren't pass/fail checks — see webapp.checks.run_all's own comment above
    the three banners), which is exactly why _failing_checks_count() above
    has never counted them: nothing in run_all()'s return value represents
    them at all. Each `_stale()` call is three settings reads plus a date
    comparison — cheap, no reason to route it through the same TTL-cached/
    background-refreshed path run_all() needs.

    Deliberately does NOT also add /admin/checks' "Database-backed copy"
    scan violation count here — those are the same underlying findings
    /admin/voice/review-queue already counts (scripts/backfill_voice_
    review_queue.py populates the queue FROM that exact scan, and every
    Library write since logs new findings into the queue directly via
    Library._vf/log_voice_correction — see CLAUDE.md's "Voice review queue"
    section), so adding a second count for the identical violations under
    the /admin/checks href would double the total past what's actually
    pending, which is precisely what was flagged as a real risk here."""
    return sum([
        pricing_review_is_stale(lib.get_setting("pricing_last_verified")),
        models_review_is_stale(lib.get_setting("models_last_reviewed")),
        exa_pricing_review_is_stale(lib.get_setting("exa_pricing_last_verified")),
    ])


def _backup_stale_count(lib: Library) -> int:
    """1 if backups are configured but the most recent successful backup_log
    row is older than linklib.backup.BACKUP_STALE_HOURS (or there's never
    been one), else 0. Gated on backup.is_configured() so a dev/test
    environment with no Drive credentials at all (which will never have a
    successful row) doesn't permanently badge itself — that's a
    configuration choice, not a "something broke" signal, and
    /admin/library-backup's own status banner already covers "backups are
    off" separately."""
    if not _backup.is_configured():
        return 0
    return int(_backup.backup_is_stale(lib.most_recent_successful_backup_at()))


def open_task_counts(lib: Library) -> dict[str, int]:
    """Non-zero open-task counts keyed by the admin href they badge."""
    counts = {
        # The Archive Queue's own "pending" badge (2026-09, PR 3) and the
        # "Remove content" review-queue badge (2026-09, PR 4) were both
        # retired along with their underlying features — see CLAUDE.md.
        "/admin/inbox/contact-submissions": lib.count_contacts_since(lib.get_setting("admin_viewed_contacts")),
        # 2026-09: both Software's and Communities' badges used to be a SUM
        # of two separate counts (pending-approval + needing-review) —
        # Software's own history is a longer chain of fixes (see git
        # history / CLAUDE.md), Communities' was the original shape. Both
        # summed patterns share the same real flaw: if a single row can
        # satisfy both conditions at once, it gets counted twice, inflating
        # the badge past what's actually pending. Confirmed for tools this
        # isn't hypothetical — see count_tools_needing_attention()'s own
        # docstring for why EVERY unapproved tool already also has
        # needs_review=1 by construction, making the old sum a real, live
        # double-count today, not a latent risk. Communities' overlap is
        # possible but not automatic (see count_communities_needing_
        # attention()'s docstring) — fixed the same way regardless, since
        # the sum pattern doesn't protect against it either way. Both
        # badges now read a single dedup-safe count apiece, replacing the
        # sum entirely rather than summing alongside it.
        "/admin/tools/software": lib.count_tools_needing_attention(),
        "/admin/inbox/toolbox-intros": lib.count_tool_leads_since(lib.get_setting("admin_viewed_tool_leads")),
        "/admin/tools/communities": lib.count_communities_needing_attention(),
        "/admin/inbox/community-gaps": lib.community_gap_counts()["unreviewed"],
        # _failing_checks_count() is the failing "Live + CI" run_all() rows
        # (Brand standards, Voice standards, Typography, Hub-nav orphans,
        # ...) — the amber "never reviewed" freshness reminders below it on
        # the page are a genuinely separate signal (see
        # _stale_admin_checks_reminders' own docstring for why the "33 DB
        # voice violations" section is deliberately NOT a third addend
        # here).
        "/admin/checks": _failing_checks_count() + _stale_admin_checks_reminders(lib),
        "/admin/library-backup": _backup_stale_count(lib),
        "/admin/users": lib.count_pending_password_resets(),
        "/admin/inbox/email-failures": lib.count_pending_email_failures(),
        # 2026-09 (Phase 3): ask-feedback is no longer deferred — it has the
        # same manual "mark reviewed" toggle Community gaps already uses
        # (ask_feedback.reviewed, same column name/type/default), not an
        # auto-clear-on-view mechanism (investigated and explicitly
        # rejected for Community gaps first, then matched here rather than
        # diverging). Every other un-badged queue Phase 0 inventoried is
        # still deliberately left out — logged for a future flow-
        # harmonization decision instead.
        "/admin/fpa-buddy/feedback": lib.count_unreviewed_ask_feedback(),
        "/admin/tools/software/feature-review-queue": lib.count_feature_review_queue(status="pending"),
        "/admin/reader/backfill-content": lib.count_needs_content_check() + lib.count_articles_needing_manual_review(),
        "/admin/tools/software/name-duplicates": len(lib.find_tool_name_duplicate_candidates()),
        "/admin/compare-summary-feedback": lib.count_compare_summary_feedback(reviewed=False),
        "/admin/voice/review-queue": lib.count_open_voice_review_items(),
    }
    return {href: n for href, n in counts.items() if n}


def has_open_tasks(lib: Library) -> bool:
    return bool(open_task_counts(lib))
