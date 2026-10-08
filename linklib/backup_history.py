"""The one table on /admin/library-backup: a row per backup file in Drive, plus a
row for each failure.

Drive is the base because a restore of an older snapshot deletes the newer
rows from backup_log (inside the database), and those files must keep their
buttons. Each file row carries the article count (from backup_log, when it
still has a row), the latest Check and the latest Restore of any outcome (from
restore-audit.jsonl on the volume, which a restore does not replace), so a
failure on a file shows on that file's own row. A failed backup, and a failed,
refused or interrupted check or restore whose file is not in the Drive list
(or whose file id and name are unknown), get a row of their own. A successful
check or restore of a file no longer in Drive has nothing to attach to and is
dropped. HTML-free, so the page and the tests share one place for the merge.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

FAILURE_LIMIT = 20   # newest failure rows shown; the audit file keeps every attempt

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


def _stamp(dt) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")


def _who(rec: dict, user_names: dict) -> str:
    user = rec.get("user")
    return user_names.get(str(user), str(user)) if user not in (None, "") else ""


def _failure_detail(rec: dict) -> str:
    msg = _LEAD.sub("", rec.get("message") or "")
    return re.sub(r"\s*Nothing was changed\.\s*$", "", msg)


def _is_file(rec: dict, file: dict) -> bool:
    """An audit line belongs to a Drive file by id, or by name when the line has no id."""
    return bool(rec.get("file_id") == file.get("id") or
                (not rec.get("file_id") and rec.get("file_name") == file.get("name")))


def _latest(audit_rows: list[dict], file: dict, mode: str) -> dict | None:
    """The newest audit line of any outcome for this Drive file (id first, then name)."""
    best, best_t = None, None
    for rec in audit_rows:
        if rec.get("mode") != mode or not _is_file(rec, file):
            continue
        t = parse_time(rec.get("time", ""))
        if t and (best_t is None or t > best_t):
            best, best_t = rec, t
    return best


def _backup_for(file: dict, backup_rows: list[dict]) -> dict | None:
    """The newest successful backup_log row for this Drive file (id first, then name)."""
    for key, col in (("id", "drive_file_id"), ("name", "filename")):
        if not file.get(key):
            continue
        for b in backup_rows:    # newest first, as list_backup_log returns them
            if b.get("status") == "success" and b.get(col) == file[key]:
                return b
    return None


def _file_row(f: dict, backup_rows, audit_rows, user_names) -> dict:
    b = _backup_for(f, backup_rows)
    chk = _latest(audit_rows, f, "check")
    rst = _latest(audit_rows, f, "restore")
    made = parse_time(f.get("created", ""))
    row = {"kind": "file", "sort": made.timestamp() if made else 0.0, "id": f.get("id", ""),
           "name": f.get("name", ""), "size": int(f.get("size") or 0),
           "made": _stamp(made) if made else (f.get("created") or ""),
           "articles": b.get("row_count") if b else None, "check": None, "restored": None}
    for key, rec in (("check", chk), ("restored", rst)):
        if rec:
            t = parse_time(rec.get("time", ""))
            res = _AUDIT_RESULT.get(rec.get("result", ""), FAILED)
            row[key] = {"result": res, "when": _stamp(t) if t else "", "by": _who(rec, user_names),
                        "reason": "" if res == SUCCEEDED else _failure_detail(rec)}
    return row


def _failure_rows(files, backup_rows, audit_rows, plain_error, user_names) -> list[dict]:
    out = []
    for b in backup_rows:
        if b.get("status") == "success":
            continue
        t = parse_time(b.get("created_at", ""))
        if t:
            out.append({"kind": "failure", "sort": t.timestamp(), "when": _stamp(t), "action": "Backup",
                        "file": b.get("filename") or "", "result": FAILED,
                        "detail": plain_error(b.get("error", "")), "by": ""})
    for rec in audit_rows:
        res = _AUDIT_RESULT.get(rec.get("result", ""), FAILED)
        if res == SUCCEEDED or any(_is_file(rec, f) for f in files):
            continue    # a success is not a failure; a file in Drive shows its own attempts
        t = parse_time(rec.get("time", ""))
        if not t:
            continue
        out.append({"kind": "failure", "sort": t.timestamp(), "when": _stamp(t),
                    "action": "Check" if rec.get("mode") == "check" else "Restore",
                    "file": rec.get("file_name") or "", "result": res,
                    "detail": _failure_detail(rec), "by": _who(rec, user_names)})
    out.sort(key=lambda r: r["sort"], reverse=True)
    return out[:FAILURE_LIMIT]


def table_rows(files: list[dict], backup_rows: list[dict], audit_rows: list[dict], plain_error,
               user_names: dict | None = None) -> list[dict]:
    """Newest first. `files` are the Drive list (empty when Drive could not be
    reached; the failure rows still show)."""
    names = user_names or {}
    rows = [_file_row(f, backup_rows, audit_rows, names) for f in files]
    rows += _failure_rows(files, backup_rows, audit_rows, plain_error, names)
    rows.sort(key=lambda r: r["sort"], reverse=True)
    return rows
