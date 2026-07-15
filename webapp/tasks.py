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

DOT_ONLY_HREFS = frozenset({"/admin/contacts", "/admin/tools/leads"})


def _failing_checks_count() -> int:
    return sum(1 for r in _checks.run_all() if r["where"] == "In-app" and r["ok"] is False)


def open_task_counts(lib: Library) -> dict[str, int]:
    """Non-zero open-task counts keyed by the admin href they badge."""
    counts = {
        "/admin/queue": lib.queue_count(status="pending"),
        "/admin/review-removals": lib.flagged_count(),
        "/admin/contacts": lib.count_contacts_since(lib.get_setting("admin_viewed_contacts")),
        "/admin/tools": lib.count_pending_tools(),
        "/admin/tools/leads": lib.count_tool_leads_since(lib.get_setting("admin_viewed_tool_leads")),
        "/admin/tools/communities": lib.count_pending_communities(),
        "/admin/community-gaps": lib.community_gap_counts()["unreviewed"],
        "/admin/checks": _failing_checks_count(),
        "/admin/users": lib.count_pending_password_resets(),
        "/admin/email-failures": lib.count_pending_email_failures(),
    }
    return {href: n for href, n in counts.items() if n}


def has_open_tasks(lib: Library) -> bool:
    return bool(open_task_counts(lib))
