"""Off-site backup of the SQLite library to Google Drive (Workspace account).

Design notes
------------
- **Consistent snapshots.** We never copy the live ``.db`` file directly (it
  may be mid-write, and the WAL sidecar holds uncommitted pages). Instead we
  use SQLite's online backup API to produce a clean, self-contained copy, then
  upload that.
- **No new dependency.** Talks to the Drive v3 REST API with ``requests``
  (already required) — no Google API client library.
- **Never-expiring auth.** Uses an OAuth *refresh token* (+ client id/secret)
  to mint a short-lived access token on each run, so backups keep working
  indefinitely without re-authorizing. The same OAuth client and refresh
  token also power outbound email (linklib/email_utils.py) — mint the token
  once with BOTH scopes, ``drive.file`` and ``gmail.send``, authorizing with
  the Workspace account that should own backups and send mail. Setup steps
  live in .env.example.
- **Debounced.** ``maybe_backup`` only uploads if it's been longer than
  ``min_interval_hours`` (default one week) since the last successful backup
  (tracked by a marker file beside the database, so it survives restarts).
- **Every attempt is logged to `backup_log`** (success or failure), via
  ``Library.record_backup_attempt`` — not just printed to stdout. This is
  what powers the status banner + history table on ``/admin/library/backup``
  (Phase O). The ``print()`` calls below stay as a redundant secondary
  signal in Railway's runtime logs, but they're no longer the only record.
- **Scheduling lives outside this module.** A weekly GitHub Action
  (``.github/workflows/backup.yml``) is the primary trigger, hitting
  ``POST /admin/backup-now`` on the live site. The ~18 ``maybe_backup()``
  call sites sprinkled through ``webapp/app.py``'s admin/save routes are a
  harmless bonus trigger — they fire only when an admin action happens to
  land more than a week after the last success, which historically hasn't
  been reliable on its own (Phase O investigation).

Configuration (environment variables — all three required to enable backups):
    GOOGLE_OAUTH_CLIENT_ID       Google Cloud OAuth client ID
    GOOGLE_OAUTH_CLIENT_SECRET   Google Cloud OAuth client secret
    GOOGLE_OAUTH_REFRESH_TOKEN   OAuth refresh token (drive.file + gmail.send scopes)

Optional but strongly recommended:
    GOOGLE_DRIVE_FOLDER_ID       Drive folder ID for snapshots (default: My Drive
                                  root — see folder_configured() below; the admin
                                  status banner flags this as a separate warning
                                  from "not configured at all", since backups
                                  keep working either way, just to the wrong place)

If the variables aren't set, every function here is a safe no-op — the app runs
exactly as before.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
import uuid
from datetime import datetime, timezone

import requests

_TOKEN_URL = "https://oauth2.googleapis.com/token"
_UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart"


def is_configured() -> bool:
    return bool(
        os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
        and os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
        and os.environ.get("GOOGLE_OAUTH_REFRESH_TOKEN")
    )


def folder_configured() -> bool:
    """Whether GOOGLE_DRIVE_FOLDER_ID is set. Separate from is_configured()
    on purpose: backups still work with this unset (they land in My Drive
    root), so it's a distinct warning state in the admin UI, not a hard
    "backups are off" condition."""
    return bool(os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "").strip())


def _marker_path(db_path: str) -> str:
    d = os.path.dirname(os.path.abspath(db_path)) or "."
    return os.path.join(d, ".last_backup")


def snapshot_to_file(db_path: str) -> str:
    """Write a consistent copy of the database to a temp file; return its path.

    Caller is responsible for deleting the returned file.
    """
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    src = sqlite3.connect(db_path)
    try:
        dst = sqlite3.connect(tmp)
        try:
            with dst:
                src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    # The backup target is fully checkpointed; clear any stray sidecars.
    for sidecar in ("-wal", "-shm"):
        try:
            os.remove(tmp + sidecar)
        except FileNotFoundError:
            pass
    return tmp


def _article_count(db_path: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    finally:
        conn.close()


def _log_attempt(db_path: str, *, status: str, filename: str = "", drive_file_id: str = "",
                  size_bytes: int = 0, row_count: int = 0, error: str = "") -> None:
    """Write one backup_log row. Best-effort — a logging failure must never
    mask the backup's real success/failure, so this only prints on error
    rather than raising. Imports Library lazily to keep this module's own
    import graph minimal (mirrors the deferred imports used for background
    jobs elsewhere in the app)."""
    try:
        from linklib.db import Library
        lib = Library(db_path)
        try:
            lib.record_backup_attempt(
                status=status, filename=filename, drive_file_id=drive_file_id,
                size_bytes=size_bytes, row_count=row_count, error=error,
            )
        finally:
            lib.close()
    except Exception as log_err:  # pragma: no cover - logging must never break backup itself
        print(f"[backup] failed to write backup_log row: {log_err}")


def _access_token() -> str:
    r = requests.post(
        _TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": os.environ["GOOGLE_OAUTH_REFRESH_TOKEN"],
            "client_id": os.environ["GOOGLE_OAUTH_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_OAUTH_CLIENT_SECRET"],
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def backup_now(db_path: str) -> dict:
    """Take a snapshot and upload it to Google Drive.
    Returns {name, bytes, drive_file_id, row_count}.

    Raises if Drive isn't configured or the upload fails. Every attempt —
    success or failure — is logged to backup_log (see _log_attempt) before
    returning or re-raising, so the admin UI has a persistent record even
    when this raises.
    """
    if not is_configured():
        raise RuntimeError("Google Drive backup is not configured")

    try:
        tmp = snapshot_to_file(db_path)
        try:
            row_count = _article_count(tmp)
            with open(tmp, "rb") as f:
                data = f.read()
        finally:
            os.remove(tmp)

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        name = f"library-{stamp}.db"
        metadata = {"name": name}
        folder_id = os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "").strip()
        if folder_id:
            metadata["parents"] = [folder_id]

        # Drive's multipart upload wants a metadata JSON part + a media part,
        # joined by a boundary — there's no `requests` helper for this shape
        # (it's multipart/related, not the multipart/form-data used elsewhere).
        boundary = uuid.uuid4().hex
        body = (
            f"--{boundary}\r\n"
            f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
            f"{json.dumps(metadata)}\r\n"
            f"--{boundary}\r\n"
            f"Content-Type: application/octet-stream\r\n\r\n"
        ).encode("utf-8") + data + f"\r\n--{boundary}--".encode("utf-8")

        token = _access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/related; boundary={boundary}",
        }
        # fields=id,name pins what the response body includes — Drive's
        # default response shape isn't guaranteed to carry `id`, and the
        # admin UI's Drive link depends on it.
        r = requests.post(f"{_UPLOAD_URL}&fields=id,name", headers=headers, data=body, timeout=120)
        r.raise_for_status()
        drive_file_id = r.json().get("id", "")
        result = {"name": name, "bytes": len(data), "drive_file_id": drive_file_id, "row_count": row_count}
        _log_attempt(db_path, status="success", filename=name, drive_file_id=drive_file_id,
                     size_bytes=len(data), row_count=row_count)
        return result
    except Exception as e:
        _log_attempt(db_path, status="failure", error=str(e))
        raise


def maybe_backup(db_path: str, min_interval_hours: float = 168.0) -> None:
    """Back up only if enough time has passed since the last success.

    Defaults to once a week. Safe to call from a request path / background
    task: never raises — failures are logged and swallowed so they can't
    break a user action.
    """
    if not is_configured():
        return
    marker = _marker_path(db_path)
    try:
        last = float(open(marker).read().strip())
    except Exception:
        last = 0.0
    if time.time() - last < min_interval_hours * 3600:
        return
    try:
        result = backup_now(db_path)
        with open(marker, "w") as f:
            f.write(str(time.time()))
        print(f"[backup] uploaded {result['name']} ({result['bytes']} bytes) to Google Drive")
    except Exception as e:  # pragma: no cover - network/credential issues
        print(f"[backup] failed: {e}")
