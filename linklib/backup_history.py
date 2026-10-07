"""One merged history of backups, checks and restores for /admin/library-backup.

Backups come from the backup_log table. Checks and restores come from
restore-audit.jsonl on the volume, because the table lives inside the
database a restore replaces, while the audit file does not. HTML-free, so
the page and the tests share one place for the merge.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

HISTORY_LIMIT = 50

# Result chips, in the words the page shows.
SUCCEEDED, FAILED, REFUSED, INTERRUPTED = "Succeeded", "Failed", "Refused", "Interrupted"

_AUDIT_RESULT = {"checked": SUCCEEDED, "finished": SUCCEEDED, "failed": FAILED, "rolled_back": FAILED,
                 "refused": REFUSED, "interrupted": INTERRUPTED}
_ARTICLES = re.compile(r"([\d,]+) articles")
_LEAD = re.compile(r"^(Restore finished: |The (restore|check) failed: )")


def parse_time(iso: str):
    """A timezone-aware datetime from a stored time, or None. Naive values are UTC."""
    try:
        dt = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _articles(rec: dict):
    n = rec.get("articles")
    if isinstance(n, int):
        return n
    m = _ARTICLES.search(rec.get("message") or "")
    return int(m.group(1).replace(",", "")) if m else None


def _duration(rec: dict) -> str:
    a, b = parse_time(rec.get("started_at", "")), parse_time(rec.get("time", ""))
    if not a or not b:
        return ""
    sec = max(0, int((b - a).total_seconds()))
    return f"{sec // 60}m {sec % 60:02d}s"


def _audit_row(rec: dict, user_names: dict) -> dict | None:
    when = parse_time(rec.get("time", ""))
    if not when:
        return None
    mode = "Check" if rec.get("mode") == "check" else "Restore"
    result = _AUDIT_RESULT.get(rec.get("result", ""), FAILED)
    if result == SUCCEEDED:
        bits = []
        n = _articles(rec)
        if n is not None:
            bits.append(f"{n:,} articles")
        d = _duration(rec)
        if d:
            bits.append(d)
        detail = " · ".join(bits)
    else:
        msg = _LEAD.sub("", rec.get("message") or "")
        detail = re.sub(r"\s*Nothing was changed\.\s*$", "", msg)
    user = rec.get("user")
    by = user_names.get(str(user), str(user)) if user not in (None, "") else "—"
    return {"when": when, "action": mode, "file": rec.get("file_name") or "—", "result": result,
            "detail": detail, "by": by}


def _backup_row(b: dict, plain_error) -> dict | None:
    when = parse_time(b.get("created_at", ""))
    if not when:
        return None
    ok = b.get("status") == "success"
    detail = f"{b.get('row_count', 0):,} articles" if ok else plain_error(b.get("error", ""))
    return {"when": when, "action": "Backup", "file": b.get("filename") or "—",
            "result": SUCCEEDED if ok else FAILED, "detail": detail, "by": "—"}


def history_rows(backup_rows: list[dict], audit_rows: list[dict], plain_error, user_names: dict | None = None,
                 interrupted: dict | None = None, limit: int = HISTORY_LIMIT) -> list[dict]:
    """Newest first. `interrupted` is the current status record when its
    process is gone; such a run never wrote an audit line."""
    names = user_names or {}
    rows = [r for r in (_backup_row(b, plain_error) for b in backup_rows) if r]
    rows += [r for r in (_audit_row(a, names) for a in audit_rows) if r]
    if interrupted:
        when = parse_time(interrupted.get("updated_at", "")) or parse_time(interrupted.get("started_at", ""))
        if when:
            u = interrupted.get("user")
            rows.append({"when": when, "action": "Check" if interrupted.get("mode") == "check" else "Restore",
                         "file": interrupted.get("file_name") or "—", "result": INTERRUPTED,
                         "detail": "The process stopped before it finished.",
                         "by": names.get(str(u), str(u)) if u not in (None, "") else "—"})
    rows.sort(key=lambda r: r["when"], reverse=True)
    return rows[:limit]


def last_restore_times(audit_rows: list[dict]) -> tuple[dict, dict]:
    """(by file id, by file name) -> time of the latest successful restore from it.
    Checks never count."""
    by_id: dict = {}
    by_name: dict = {}
    for rec in audit_rows:
        if rec.get("mode") != "restore" or rec.get("result") != "finished":
            continue
        t = parse_time(rec.get("time", ""))
        if not t:
            continue
        for key, bucket in ((rec.get("file_id"), by_id), (rec.get("file_name"), by_name)):
            if key and (key not in bucket or t > bucket[key]):
                bucket[key] = t
    return by_id, by_name


def restored_at(file: dict, by_id: dict, by_name: dict) -> str:
    """'YYYY-MM-DD HH:MM' (UTC) of the latest restore from this Drive file, else ''."""
    t = by_id.get(file.get("id")) or by_name.get(file.get("name"))
    return t.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M") if t else ""
