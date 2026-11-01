"""Display-only time zone for admin pages (2026-11).

Everything is stored in UTC. An admin page may show a stored timestamp in the zone set in the
`admin_display_timezone` setting (default America/New_York), with the zone's abbreviation, so a
run at 8:11 pm Eastern on 10-31 does not read as 11-01. Nothing stored changes.
"""
from __future__ import annotations

from datetime import datetime, timezone

SETTING_KEY = "admin_display_timezone"
DEFAULT_ZONE = "America/New_York"


def zone_for(name: str | None):
    """(tzinfo, name actually used). An unknown or empty name falls back to the default zone;
    a machine with no zone database falls back to UTC, which is then what the label says."""
    from zoneinfo import ZoneInfo
    for cand in ((name or "").strip(), DEFAULT_ZONE):
        if not cand:
            continue
        try:
            return ZoneInfo(cand), cand
        except Exception:
            continue
    return timezone.utc, "UTC"


def is_valid_zone(name: str) -> bool:
    from zoneinfo import ZoneInfo
    try:
        ZoneInfo((name or "").strip())
        return bool((name or "").strip())
    except Exception:
        return False


def _parse(iso: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat((iso or "").strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def local_stamp(iso: str, zone_name: str | None = None) -> str:
    """"2026-10-31 20:11 EDT" for a stored UTC timestamp; the raw text when it cannot be read."""
    dt = _parse(iso)
    if dt is None:
        return (iso or "")[:16]
    tz, _ = zone_for(zone_name)
    loc = dt.astimezone(tz)
    return f"{loc:%Y-%m-%d %H:%M} {loc.tzname() or ''}".strip()
