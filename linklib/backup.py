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
- **Scheduling lives outside this module.** A GitHub Action
  (``.github/workflows/backup.yml``, daily as of 2026-08 — Railway-native
  volume snapshots turned out unavailable on the current plan, so this is
  the only recovery path and daily beats weekly for how much a restore
  could lose) is the primary trigger, hitting ``POST /admin/backup-now`` on
  the live site. The ~18 ``maybe_backup()`` call sites sprinkled through
  ``webapp/app.py``'s admin/save routes are a harmless bonus trigger (still
  debounced to once a week by default — see ``maybe_backup``'s own
  ``min_interval_hours``) — they fire only when an admin action happens to
  land more than that long after the last success, which historically
  hasn't been reliable on its own (Phase O investigation).
- **Retention.** Daily snapshots would grow the Drive folder without limit
  where weekly never accumulated fast enough to matter — ``backup_now()``
  calls ``prune_old_backups()`` after every successful upload, keeping the
  most recent 14 snapshots unconditionally plus one per ISO week for 8
  weeks further back, deleting everything older. Stateless and
  self-healing: recomputed from Drive's actual file listing every run, not
  a separate tracking table, so a missed prune just means one extra file
  survives until the next run catches it.
- **The target Drive folder is self-managed, not hand-picked.** The OAuth
  refresh token is minted with the ``drive.file`` scope (see below) —
  deliberately the narrowest Drive scope, not full ``drive`` access — which
  only grants visibility into files/folders *the app itself created via the
  API*. A folder made by hand in the Drive web UI (even in the same
  account) is invisible to ``drive.file``: referencing it as a snapshot's
  ``parents`` gets a ``404`` from Google (which deliberately returns 404
  rather than 403 for an inaccessible resource, to avoid confirming it
  exists). This bit a hand-made "Library Backup" folder from the original
  Phase O setup — every upload 404'd against it, account and folder ID
  both correct, purely a scope mismatch. Fixed by having ``backup_now()``
  create and own its own folder (``_resolve_folder_id`` below) instead of
  targeting a pre-existing one: the first successful run creates a folder
  named "CFO Navigator — Library Backups" in My Drive root and remembers
  its id in the ``settings`` table (key ``backup_drive_folder_id``); every
  run after that reuses it. ``GOOGLE_DRIVE_FOLDER_ID`` still wins if set —
  useful if a folder is ever explicitly granted to the app some other way
  (e.g. a Drive Picker consent flow) — but the default, unset case is now a
  folder the app can actually see, not My Drive root.

Configuration (environment variables — all three required to enable backups):
    GOOGLE_OAUTH_CLIENT_ID       Google Cloud OAuth client ID
    GOOGLE_OAUTH_CLIENT_SECRET   Google Cloud OAuth client secret
    GOOGLE_OAUTH_REFRESH_TOKEN   OAuth refresh token (drive.file + gmail.send scopes)

Optional:
    GOOGLE_DRIVE_FOLDER_ID       Explicit Drive folder id override. Leave unset —
                                  the app creates and remembers its own folder
                                  (see the design note above); this is only for
                                  pointing at a folder some other mechanism has
                                  already explicitly granted the app access to.

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


FOLDER_NAME = "CFO Navigator — Library Backups"
_FOLDER_SETTING_KEY = "backup_drive_folder_id"
_FILES_URL = "https://www.googleapis.com/drive/v3/files"


def _create_backup_folder(token: str) -> str:
    """Create the dedicated Drive folder for snapshots, in My Drive root.
    A folder the app creates itself via the API is automatically visible
    to it under the drive.file scope — unlike a folder made by hand in the
    Drive UI, which drive.file can't see or write into (see this module's
    docstring)."""
    r = requests.post(
        f"{_FILES_URL}?fields=id",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"name": FOLDER_NAME, "mimeType": "application/vnd.google-apps.folder"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["id"]


def _resolve_folder_id(db_path: str, token: str) -> str:
    """Which Drive folder id snapshots upload into this run.

    GOOGLE_DRIVE_FOLDER_ID wins if set — an explicit override for a folder
    granted to the app some other way. Otherwise the app manages its own
    folder: reuse the id already recorded in `settings` (a prior run
    created one), or create a fresh one now and persist its id for next
    time. Never targets the pre-existing hand-made "Library Backup"
    folder — drive.file's scope can't see it regardless of what id is
    configured for it."""
    env_folder = os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "").strip()
    if env_folder:
        return env_folder

    from linklib.db import Library
    lib = Library(db_path)
    try:
        existing = lib.get_setting(_FOLDER_SETTING_KEY)
        if existing:
            return existing
        folder_id = _create_backup_folder(token)
        lib.set_setting(_FOLDER_SETTING_KEY, folder_id)
        return folder_id
    finally:
        lib.close()


def known_folder_id(db_path: str) -> str:
    """Read-only lookup of the folder snapshots upload into, for display
    (e.g. /admin/library/backup) — never creates a folder as a side effect
    of a page view, unlike _resolve_folder_id which is called mid-upload
    and will create one on first use. Returns '' if nothing's been
    configured or created yet."""
    env_folder = os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "").strip()
    if env_folder:
        return env_folder
    from linklib.db import Library
    lib = Library(db_path)
    try:
        return lib.get_setting(_FOLDER_SETTING_KEY)
    finally:
        lib.close()


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


def _list_backup_files(token: str, folder_id: str) -> list[dict]:
    """Every non-trashed file in the backup folder, newest first. Fresh
    from Drive on every call — no separate tracking table — so a missed
    prune pass just means one extra file survives until the next run
    catches it, never permanent drift."""
    r = requests.get(
        _FILES_URL,
        headers={"Authorization": f"Bearer {token}"},
        params={"q": f"'{folder_id}' in parents and trashed=false",
                "fields": "files(id,name,createdTime)",
                "orderBy": "createdTime desc",
                "pageSize": 1000},
        timeout=30,
    )
    r.raise_for_status()
    return r.json().get("files", [])


def _select_backups_to_delete(files: list[dict], keep_daily: int = 14,
                               keep_weekly: int = 8) -> list[dict]:
    """`files` sorted newest-first (createdTime desc, matches
    _list_backup_files's own ordering). Keeps the most recent `keep_daily`
    files unconditionally (covers the daily cadence's most recent stretch),
    then walks further back keeping at most one file per distinct ISO
    calendar week for the next `keep_weekly` weeks — everything else is
    marked for deletion. Deterministic and stateless: recomputed from
    Drive's actual listing every call, same non-drifting design as
    _list_backup_files above."""
    keep_ids = {f["id"] for f in files[:keep_daily]}
    seen_weeks = set()
    for f in files[keep_daily:]:
        try:
            dt = datetime.fromisoformat(f["createdTime"].replace("Z", "+00:00"))
        except Exception:
            continue
        week_key = dt.isocalendar()[:2]  # (ISO year, ISO week number)
        if week_key in seen_weeks:
            continue
        if len(seen_weeks) >= keep_weekly:
            continue
        seen_weeks.add(week_key)
        keep_ids.add(f["id"])
    return [f for f in files if f["id"] not in keep_ids]


def _delete_backup_file(token: str, file_id: str) -> None:
    r = requests.delete(f"{_FILES_URL}/{file_id}", headers={"Authorization": f"Bearer {token}"}, timeout=30)
    r.raise_for_status()


def prune_old_backups(db_path: str, keep_daily: int = 14, keep_weekly: int = 8) -> dict:
    """Delete Drive snapshots beyond the retention policy — the most recent
    `keep_daily` backups unconditionally, plus at most one per ISO week for
    `keep_weekly` further weeks back. Added when the backup cadence moved
    from weekly to daily (2026-08): unbounded daily snapshots would have
    grown the Drive folder without limit, where weekly never accumulated
    fast enough to matter.

    Best-effort, same contract as maybe_backup: never raises. A pruning
    failure must never affect whether the backup itself succeeded — this
    is called from backup_now() AFTER a successful upload, wrapped so its
    own failure can't turn a real backup success into a reported failure.
    Returns {"deleted": N, "kept": N, "error": str} for the caller to log."""
    if not is_configured():
        return {"deleted": 0, "kept": 0, "error": ""}
    try:
        token = _access_token()
        folder_id = _resolve_folder_id(db_path, token)
        files = _list_backup_files(token, folder_id)
        to_delete = _select_backups_to_delete(files, keep_daily=keep_daily, keep_weekly=keep_weekly)
        for f in to_delete:
            try:
                _delete_backup_file(token, f["id"])
            except Exception as del_err:
                print(f"[backup] failed to delete old snapshot {f.get('name')}: {del_err}")
        return {"deleted": len(to_delete), "kept": len(files) - len(to_delete), "error": ""}
    except Exception as e:
        print(f"[backup] prune failed: {e}")
        return {"deleted": 0, "kept": 0, "error": str(e)}


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

        token = _access_token()
        folder_id = _resolve_folder_id(db_path, token)
        metadata = {"name": name, "parents": [folder_id]}

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
        prune_result = prune_old_backups(db_path)
        if prune_result["deleted"]:
            print(f"[backup] pruned {prune_result['deleted']} old snapshot(s), "
                  f"{prune_result['kept']} kept under retention policy")
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
