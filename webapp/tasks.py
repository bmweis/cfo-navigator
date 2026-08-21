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

from linklib.db import Library
from webapp import checks as _checks

DOT_ONLY_HREFS = frozenset({"/admin/contacts", "/admin/tools/software/leads"})


def _failing_checks_count() -> int:
    return sum(1 for r in _checks.run_all() if r["where"] == "In-app" and r["ok"] is False)


def open_task_counts(lib: Library) -> dict[str, int]:
    """Non-zero open-task counts keyed by the admin href they badge."""
    counts = {
        "/admin/library/queue": lib.queue_count(status="pending"),
        "/admin/library/review-removals": lib.flagged_count(),
        "/admin/contacts": lib.count_contacts_since(lib.get_setting("admin_viewed_contacts")),
        "/admin/tools/software": lib.count_pending_tools(),
        "/admin/tools/software/leads": lib.count_tool_leads_since(lib.get_setting("admin_viewed_tool_leads")),
        "/admin/tools/communities": lib.count_pending_communities() + lib.count_communities_needing_review(),
        "/admin/community-gaps": lib.community_gap_counts()["unreviewed"],
        "/admin/checks": _failing_checks_count(),
        "/admin/users": lib.count_pending_password_resets(),
        "/admin/email-failures": lib.count_pending_email_failures(),
        # Phase 1c badge scope-down: only these three of the un-badged queues
        # Phase 0 inventoried got wired in — each already has a cheap count
        # (an indexed COUNT, or a documented "fine to run live" full scan;
        # see that PR's CLAUDE.md note). ask-feedback and the three
        # tools.*_needs_verification flags are deliberately left out: none of
        # them has a pending/reviewed concept at all yet, so badging them
        # would be new-feature design work, not badge-wiring — logged for a
        # future flow-harmonization decision instead.
        "/admin/tools/software/feature-review-queue": lib.count_feature_review_queue(status="pending"),
        "/admin/library/backfill-content": lib.count_needs_content_check() + lib.count_articles_needing_manual_review(),
        "/admin/tools/software/name-duplicates": len(lib.find_tool_name_duplicate_candidates()),
    }
    return {href: n for href, n in counts.items() if n}


def has_open_tasks(lib: Library) -> bool:
    return bool(open_task_counts(lib))
