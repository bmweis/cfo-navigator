"""The homepage Status note: how stale it is, and what to say about it.

One pure module so the admin card, the /admin/checks row and the Admin badge
all read the same rule. The clock is a UTC ISO timestamp in the `settings`
row `homepage_status_revised_at`, set by a save or by "Mark reviewed" and
never by a page view.
"""
from __future__ import annotations

from datetime import datetime, timezone

STATUS_COPY_KEY = "homepage_status_copy"
STATUS_REVISED_KEY = "homepage_status_revised_at"
# Not an admin setting on purpose: a named constant is enough for one note.
STATUS_STALE_DAYS = 14


def status_age(stamp: str, now: datetime | None = None) -> dict:
    """{'known', 'days', 'stale'} for a stored timestamp.

    A missing or unreadable stamp counts as confirmed today (days 0, not
    stale, known False), so the first deploy never fails on day one; the
    startup seed then writes a real stamp and the clock starts.
    """
    now = now or datetime.now(timezone.utc)
    try:
        then = datetime.fromisoformat(stamp) if stamp else None
    except ValueError:
        then = None
    if then is None:
        return {"known": False, "days": 0, "stale": False}
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    days = max(0, (now - then).days)
    return {"known": True, "days": days, "stale": days >= STATUS_STALE_DAYS}


def _days(n: int) -> str:
    return f"{n} day{'' if n == 1 else 's'}"


def warning_text(days: int) -> str:
    return (f"Last revised {_days(days)} ago. Update it, or mark it reviewed "
            f"if it is still accurate.")


def check_detail(age: dict) -> str:
    if not age["known"]:
        return ("No revision date yet, so it counts as confirmed today. "
                "The clock starts at the next deploy.")
    if age["stale"]:
        return (f"Last revised or confirmed {_days(age['days'])} ago. "
                f"Update it or mark it reviewed.")
    return f"Last revised or confirmed {_days(age['days'])} ago."
