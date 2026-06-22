"""Off-site backup of the SQLite library to Dropbox.

Design notes
------------
- **Consistent snapshots.** We never copy the live ``.db`` file directly (it
  may be mid-write, and the WAL sidecar holds uncommitted pages). Instead we
  use SQLite's online backup API to produce a clean, self-contained copy, then
  upload that.
- **No new dependency.** Talks to the Dropbox HTTP API with ``requests``
  (already required) — no Dropbox SDK.
- **Never-expiring auth.** Uses an OAuth *refresh token* (+ app key/secret) to
  mint a short-lived access token on each run, so backups keep working
  indefinitely without re-authorising.
- **Debounced.** ``maybe_backup`` only uploads if it's been longer than
  ``min_interval_hours`` since the last successful backup (tracked by a marker
  file beside the database, so it survives restarts).

Configuration (environment variables — all three required to enable backups):
    DROPBOX_APP_KEY        Dropbox app key
    DROPBOX_APP_SECRET     Dropbox app secret
    DROPBOX_REFRESH_TOKEN  OAuth refresh token (offline access)

Optional:
    DROPBOX_BACKUP_DIR     Dropbox folder for snapshots (default "/").
                           For an "App folder" Dropbox app this is relative to
                           that app's folder.

If the variables aren't set, every function here is a safe no-op — the app runs
exactly as before.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
from datetime import datetime, timezone

import requests

_TOKEN_URL = "https://api.dropbox.com/oauth2/token"
_UPLOAD_URL = "https://content.dropboxapi.com/2/files/upload"


def is_configured() -> bool:
    return bool(
        os.environ.get("DROPBOX_APP_KEY")
        and os.environ.get("DROPBOX_APP_SECRET")
        and os.environ.get("DROPBOX_REFRESH_TOKEN")
    )


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


def _access_token() -> str:
    r = requests.post(
        _TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": os.environ["DROPBOX_REFRESH_TOKEN"],
        },
        auth=(os.environ["DROPBOX_APP_KEY"], os.environ["DROPBOX_APP_SECRET"]),
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def backup_now(db_path: str) -> dict:
    """Take a snapshot and upload it to Dropbox. Returns {name, bytes}.

    Raises if Dropbox isn't configured or the upload fails.
    """
    if not is_configured():
        raise RuntimeError("Dropbox backup is not configured")

    tmp = snapshot_to_file(db_path)
    try:
        with open(tmp, "rb") as f:
            data = f.read()
    finally:
        os.remove(tmp)

    folder = os.environ.get("DROPBOX_BACKUP_DIR", "/").rstrip("/")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    name = f"{folder}/library-{stamp}.db"

    token = _access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Dropbox-API-Arg": json.dumps(
            {"path": name, "mode": "add", "autorename": True, "mute": True}
        ),
        "Content-Type": "application/octet-stream",
    }
    r = requests.post(_UPLOAD_URL, headers=headers, data=data, timeout=120)
    r.raise_for_status()
    return {"name": name, "bytes": len(data)}


def maybe_backup(db_path: str, min_interval_hours: float = 24.0) -> None:
    """Back up only if enough time has passed since the last success.

    Safe to call from a request path / background task: never raises — failures
    are logged and swallowed so they can't break a user action.
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
        print(f"[backup] uploaded {result['name']} ({result['bytes']} bytes)")
    except Exception as e:  # pragma: no cover - network/credential issues
        print(f"[backup] failed: {e}")
