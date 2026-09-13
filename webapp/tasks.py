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


def _failing_checks_count() -> int:
    global _checks_cache
    now = time.time()
    with _checks_cache_lock:
        if _checks_cache is not None and now - _checks_cache[0] < _CHECKS_CACHE_TTL:
            return _checks_cache[1]
    count = sum(1 for r in _checks.run_all() if r["where"] == "In-app" and r["ok"] is False)
    with _checks_cache_lock:
        _checks_cache = (now, count)
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
