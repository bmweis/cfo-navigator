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

`DOT_ONLY_HREFS` marks sources that are reviewed as one full list at once,
with no per-item view or action (e.g. Contact Submissions — you see every
message at once, there's nothing to open or resolve individually). A number
on a badge like that implies a granularity that doesn't exist ("3 things to
look at" when it's really just "the list has something new"), so the webapp
renders these as a plain dot instead of a count. Sources with a real
per-item action (approve a tool, dismiss an email failure, resolve a
password reset) keep their numeric count.
"""
from __future__ import annotations

import threading
import time

from linklib.db import Library
from webapp import checks as _checks

DOT_ONLY_HREFS = frozenset({"/admin/contacts", "/admin/tools/software/leads"})

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
        "/admin/library/queue": lib.queue_count(status="pending"),
        "/admin/library/review-removals": lib.flagged_count(),
        "/admin/contacts": lib.count_contacts_since(lib.get_setting("admin_viewed_contacts")),
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
        "/admin/tools/software/leads": lib.count_tool_leads_since(lib.get_setting("admin_viewed_tool_leads")),
        "/admin/tools/communities": lib.count_communities_needing_attention(),
        "/admin/community-gaps": lib.community_gap_counts()["unreviewed"],
        "/admin/checks": _failing_checks_count(),
        "/admin/users": lib.count_pending_password_resets(),
        "/admin/email-failures": lib.count_pending_email_failures(),
        # Phase 1c badge scope-down: only these three of the un-badged queues
        # Phase 0 inventoried got wired in — each already has a cheap count
        # (an indexed COUNT, or a documented "fine to run live" full scan;
        # see that PR's CLAUDE.md note). ask-feedback is deliberately still
        # left out — no reviewed-state column exists on ask_feedback at all
        # — logged for a future flow-harmonization decision instead. The
        # tools *_needs_verification flags are no longer in that deferred
        # bucket: they're wired above, folded into the Software card's own
        # count.
        "/admin/tools/software/feature-review-queue": lib.count_feature_review_queue(status="pending"),
        "/admin/library/backfill-content": lib.count_needs_content_check() + lib.count_articles_needing_manual_review(),
        "/admin/tools/software/name-duplicates": len(lib.find_tool_name_duplicate_candidates()),
        "/admin/compare-summary-feedback": lib.count_compare_summary_feedback(reviewed=False),
    }
    return {href: n for href, n in counts.items() if n}


def has_open_tasks(lib: Library) -> bool:
    return bool(open_task_counts(lib))
