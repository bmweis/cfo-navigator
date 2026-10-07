"""Status, log and audit files for a database restore (Phase 2 of #714).

These live on the volume next to the database, never inside it: a restore
replaces library.db, so anything recorded in the database would be replaced
with it. Both scripts/restore_from_drive.py (writer) and the admin page
(reader) use this module. Plain files, stdlib only.

  restore-status.json   current or last restore: state, stage, heartbeat
  restore.log           one line per step, flushed as it happens
  restore-audit.jsonl   one JSON line per attempt: who, which file, result
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone

STATUS_FILE = "restore-status.json"
LOG_FILE = "restore.log"
AUDIT_FILE = "restore-audit.jsonl"

# Free space kept beyond the download itself, same as the script's own check.
SPACE_MARGIN = 16 * 1024 * 1024
# The script refreshes its heartbeat every few seconds. Past this, a status
# that still says "running" is treated as an interrupted restore.
HEARTBEAT_STALE_SECONDS = 60


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dir(db_path: str) -> str:
    return os.path.dirname(os.path.abspath(db_path))


def paths(db_path: str) -> dict:
    d = _dir(db_path)
    return {"status": os.path.join(d, STATUS_FILE), "log": os.path.join(d, LOG_FILE),
            "audit": os.path.join(d, AUDIT_FILE)}


def read_status(db_path: str) -> dict:
    try:
        with open(paths(db_path)["status"], "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_status(db_path: str, **fields) -> dict:
    """Merge fields into the status file, atomically."""
    p = paths(db_path)["status"]
    cur = read_status(db_path)
    cur.update(fields)
    cur["updated_at"] = now_iso()
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cur, f)
        f.flush()
    os.replace(tmp, p)
    return cur


def reset_status(db_path: str, **fields) -> dict:
    """Start a fresh status record (a new restore or check)."""
    try:
        os.remove(paths(db_path)["status"])
    except OSError:
        pass
    return write_status(db_path, **fields)


def log_line(db_path: str, msg: str) -> None:
    line = f"{now_iso()} {msg}\n"
    with open(paths(db_path)["log"], "a", encoding="utf-8") as f:
        f.write(line)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass


def log_tail(db_path: str, n: int = 12) -> list[str]:
    try:
        with open(paths(db_path)["log"], "r", encoding="utf-8", errors="replace") as f:
            return [ln.rstrip("\n") for ln in f.readlines()[-n:]]
    except OSError:
        return []


def audit(db_path: str, **rec) -> None:
    rec = {"time": now_iso(), **rec}
    with open(paths(db_path)["audit"], "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
        f.flush()


def pid_alive(pid) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _age_seconds(iso: str) -> float:
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds()
    except (TypeError, ValueError):
        return 1e9


def effective_state(status: dict) -> str:
    """idle, running, interrupted, finished, failed, rolled_back or checked.
    A record that says "running" but whose process is gone or whose heartbeat
    has stopped is "interrupted"."""
    if not status:
        return "idle"
    state = status.get("state", "idle")
    if state != "running":
        return state
    if not pid_alive(status.get("pid")):
        return "interrupted"
    if _age_seconds(status.get("updated_at", "")) > HEARTBEAT_STALE_SECONDS:
        return "interrupted"
    return "running"


def restore_is_running(db_path: str) -> bool:
    return effective_state(read_status(db_path)) == "running"


def disk_problem(db_path: str, snapshot_bytes: int) -> str:
    """'' when the download fits, else a plain sentence. Mirrors the script's
    own check so the page can refuse before starting anything."""
    d = _dir(db_path)
    free = shutil.disk_usage(d).free
    need = int(snapshot_bytes) + SPACE_MARGIN
    if free < need:
        return (f"Not enough free space on the volume: {free / 1_000_000:,.0f} MB free, "
                f"{need / 1_000_000:,.0f} MB needed for the download. Nothing was changed.")
    return ""


def elapsed_seconds(status: dict) -> int:
    start = status.get("started_at")
    if not start:
        return 0
    end = status.get("finished_at") or now_iso()
    try:
        a = datetime.fromisoformat(start)
        b = datetime.fromisoformat(end)
        return max(0, int((b - a).total_seconds()))
    except ValueError:
        return 0


