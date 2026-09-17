"""Open-task badge counts for the admin hub's coral notification badges.

Each entry in `open_task_counts` is keyed by the admin href it badges — the
hub page sums the hrefs belonging to a group for the collapsed section badge,
and looks up individual hrefs for the expanded sub-item badges. A href with
no entry (or a zero count) shows no badge.

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
is a plain numeric count now, with no exceptions. The top NAV BAR's own
presence dot (`.task-dot`, `_has_open_admin_tasks()`) is unaffected — it was
never driven by DOT_ONLY_HREFS in the first place: it's a pure "is anything
at all pending" boolean (`bool(open_task_counts(lib))`), so "dot only, no
count" was already its whole design for every source, not something this
reversal changes. See CLAUDE.md's PR 28 note for the full write-up.
"""
from __future__ import annotations

import threading
import time

from linklib.db import Library
from webapp import checks as _checks

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
        count = sum(1 for r in _checks.run_all() if r["where"] == "In-app" and r["ok"] is False)
    finally:
        with _checks_cache_lock:
            _checks_cache = (time.time(), count)
            _checks_computing = False
    return count


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
        "/admin/checks": _failing_checks_count(),
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
    }
    return {href: n for href, n in counts.items() if n}


def has_open_tasks(lib: Library) -> bool:
    return bool(open_task_counts(lib))
