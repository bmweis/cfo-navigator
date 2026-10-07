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
  ``min_interval_hours`` (default one week) since the last successful
  backup—read from ``backup_log`` (via ``Library.list_backup_log``), the
  same already-authoritative record every trigger writes to, not a
  standalone marker file (removed—see ``maybe_backup``'s own docstring
  for why one existed and went stale).
- **Every attempt is logged to `backup_log`** (success or failure), via
  ``Library.record_backup_attempt`` — not just printed to stdout. This is
  what powers the status banner + history table on ``/admin/library-backup``
  (Phase O). The ``print()`` calls below stay as a redundant secondary
  signal in Railway's runtime logs, but they're no longer the only record.
- **Scheduling lives outside this module.** A Railway Cron Service in the
  same project (daily as of 2026-08 — Railway-native volume snapshots
  turned out unavailable on the current plan, so this is the only recovery
  path and daily beats weekly for how much a restore could lose; migrated
  off a GitHub Actions schedule later in 2026-08 after a GitHub billing
  outage silently stopped it firing for 9 days — see RUNBOOK.md §7) is the
  primary trigger, hitting ``POST /admin/backup-now`` on the live site. The ~18 ``maybe_backup()`` call sites sprinkled through
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
import threading
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


# The daily Railway Cron Service hits POST /admin/backup-now roughly once
# every 24h — 26h gives a couple hours of slack for the cron's own timing
# jitter before treating a missing successful row as genuinely stale, same
# "dated reminder threshold" shape as linklib.pricing.PRICING_REVIEW_STALE_DAYS,
# just for something an admin badge can flag live rather than something only
# a human re-check can answer (backup_log's own created_at timestamps are a
# real, mechanically-checkable fact, not a manual attestation). Feeds
# webapp.tasks.open_task_counts()'s "stale backup" badge entry.
BACKUP_STALE_HOURS = 26


def backup_is_stale(last_success_iso: str, *, now: datetime | None = None) -> bool:
    """True when `last_success_iso` (Library.most_recent_successful_backup_at()'s
    return value, "" if there has never been a successful backup) is older
    than BACKUP_STALE_HOURS — or missing entirely, same "flag it" signal as
    genuinely stale. Deliberately ignores whether backups are configured at
    all (is_configured()) — that's a separate, existing signal on
    /admin/library-backup's own status banner; this only answers "is the
    most recent successful row old," which is exactly what a badge needs to
    catch a cron that silently stopped firing or a string of failures."""
    if not last_success_iso:
        return True
    try:
        then = datetime.fromisoformat(last_success_iso)
    except ValueError:
        return True
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return (now - then).total_seconds() >= BACKUP_STALE_HOURS * 3600


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
    (e.g. /admin/library-backup) — never creates a folder as a side effect
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


def list_snapshots(token: str, folder_id: str, timeout: float = 30) -> list[dict]:
    """Every non-trashed file in the backup folder with its size and md5,
    newest first. Used by scripts/restore_from_drive.py. Under the
    drive.file scope this only ever returns files the app itself created
    (a file moved in by hand is invisible), the same view prune_old_backups
    works from."""
    r = requests.get(
        _FILES_URL,
        headers={"Authorization": f"Bearer {token}"},
        params={"q": f"'{folder_id}' in parents and trashed=false",
                "fields": "files(id,name,createdTime,size,md5Checksum)",
                "orderBy": "createdTime desc",
                "pageSize": 1000},
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json().get("files", [])


def find_backup_folders(token: str) -> list[dict]:
    """Folders named FOLDER_NAME that the app can see. The disaster-recovery
    fallback: the folder id normally lives in the settings table, which is
    inside the database being restored."""
    name = FOLDER_NAME.replace("\\", "\\\\").replace("'", "\\'")
    r = requests.get(
        _FILES_URL,
        headers={"Authorization": f"Bearer {token}"},
        params={"q": f"name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false",
                "fields": "files(id,name,createdTime)",
                "orderBy": "createdTime desc"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json().get("files", [])


def download_snapshot(token: str, file_id: str, dest_path: str, chunk: int = 1 << 20) -> tuple[int, str]:
    """Stream a Drive file to dest_path without holding it in memory.
    Returns (bytes_written, md5_hex)."""
    import hashlib
    md5 = hashlib.md5()
    total = 0
    with requests.get(f"{_FILES_URL}/{file_id}", params={"alt": "media"},
                      headers={"Authorization": f"Bearer {token}"},
                      stream=True, timeout=(30, 120)) as r:
        r.raise_for_status()
        with open(dest_path, "wb") as out:
            for part in r.iter_content(chunk):
                if part:
                    out.write(part)
                    md5.update(part)
                    total += len(part)
    return total, md5.hexdigest()


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


def check_integrity(db_path: str) -> dict:
    """Durability audit item 2 (elevated) — nothing in this app ever ran
    `PRAGMA integrity_check` against the live database, meaning corruption
    would only ever surface at restore time, by which point it had already
    been propagated into every retained daily/weekly snapshot. Runs against
    `db_path` directly (the LIVE database, not a snapshot) — the whole point
    is to catch corruption before it's ever captured into a backup, not
    after.

    Two checks, in order: `PRAGMA integrity_check` (SQLite's own structural
    check — must return exactly one row reading 'ok'), then, only if that
    passes, the FTS5 self-check RUNBOOK.md §4's restore rehearsal already
    runs by hand — `INSERT INTO articles_fts(articles_fts) VALUES
    ('integrity-check')`, which raises if the FTS index and the `articles`
    content table have drifted apart. Skipping the FTS check when the
    pragma already failed avoids a second, redundant failure signal for the
    same underlying corruption.

    Never raises — always returns {"ok": bool, "detail": str}, so a caller
    (backup_now) can treat this as a plain data check rather than exception
    handling."""
    try:
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute("PRAGMA integrity_check").fetchall()
            pragma_ok = len(rows) == 1 and rows[0][0] == "ok"
            if not pragma_ok:
                detail = "; ".join(str(r[0]) for r in rows) if rows else "integrity_check returned no rows"
                return {"ok": False, "detail": detail}
            try:
                conn.execute("INSERT INTO articles_fts(articles_fts) VALUES('integrity-check')")
            except sqlite3.DatabaseError as fts_exc:
                return {"ok": False, "detail": f"FTS5 self-check failed: {fts_exc}"}
            return {"ok": True, "detail": "ok"}
        finally:
            conn.close()
    except Exception as e:  # pragma: no cover - defensive, matches maybe_backup's contract
        return {"ok": False, "detail": f"integrity check itself failed to run: {e}"}


def _log_integrity_attempt(db_path: str, result: dict) -> None:
    """Best-effort — same non-masking contract as _log_attempt below."""
    try:
        from linklib.db import Library
        lib = Library(db_path)
        try:
            lib.record_integrity_check(status="ok" if result["ok"] else "failure",
                                       detail=result.get("detail", ""))
        finally:
            lib.close()
    except Exception as log_err:  # pragma: no cover - logging must never break backup itself
        print(f"[backup] failed to write integrity_check_log row: {log_err}")


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


# One backup at a time. A non-blocking lock, not a queue: the admin button, a
# double click and the daily cron request must never overlap, because each run
# builds a snapshot of the whole database (about 254 MB) and a second one
# alongside it would double the memory and disk use. The app is a single
# uvicorn process, so a process-level lock is enough.
_BACKUP_LOCK = threading.Lock()


class BackupBusy(RuntimeError):
    """Another backup is already running in this process. Not a failure:
    nothing is logged to backup_log for it."""


def backup_is_running() -> bool:
    return _BACKUP_LOCK.locked()


def try_acquire_exclusive() -> bool:
    """Take the same lock a backup uses, for a restore (Phase 2 of #714).
    While it is held the nightly cron's request gets "already running" and
    the page's backup button refuses. Non-blocking; release_exclusive()
    when the restore process has exited."""
    return _BACKUP_LOCK.acquire(blocking=False)


def release_exclusive() -> None:
    _BACKUP_LOCK.release()


class _MultipartBody:
    """Streams the Drive multipart/related body (metadata part, the snapshot
    file, closing boundary) without reading the file into memory. Has
    __len__ so requests sends Content-Length instead of chunked encoding,
    and __iter__ so requests treats it as a stream."""

    def __init__(self, head: bytes, path: str, tail: bytes, chunk: int = 1 << 20):
        self._head, self._path, self._tail, self._chunk = head, path, tail, chunk
        self._size = os.path.getsize(path)

    def __len__(self) -> int:
        return len(self._head) + self._size + len(self._tail)

    def __iter__(self):
        yield self._head
        with open(self._path, "rb") as f:
            while True:
                part = f.read(self._chunk)
                if not part:
                    break
                yield part
        yield self._tail


def plain_error(err: str) -> str:
    """A plain-language reading of a stored backup_log error, for the admin
    page. The raw text stays in backup_log (and Railway's logs) for debugging;
    the page never shows it."""
    e = (err or "").lower()
    if "not configured" in e:
        return "Google Drive backup is not set up. The three GOOGLE_OAUTH variables are missing."
    if "integrity check" in e:
        return "The database failed its integrity check, so no backup was made. Nothing was uploaded."
    if "timed out" in e or "timeout" in e:
        return "Google Drive did not answer in time. Try again in a few minutes."
    if "401" in e or "403" in e or "invalid_grant" in e or "unauthorized" in e:
        return "Google refused the sign-in. The Drive login may need to be set up again."
    if "404" in e:
        return "Google could not find the backup folder."
    if "connection" in e or "max retries" in e:
        return "The app could not reach Google Drive. Try again in a few minutes."
    return "The backup did not finish. The Railway logs have the details."


def list_for_display(db_path: str, timeout: float = 10.0) -> dict:
    """Backups the app can see in its Drive folder, for the admin page.
    Never raises: returns {"ok": bool, "files": [...], "message": str}.
    Short timeouts so a slow Drive cannot hang the page."""
    if not is_configured():
        return {"ok": False, "files": [],
                "message": "Google Drive backup is not set up, so there is nothing to list."}
    try:
        folder_id = known_folder_id(db_path)
        if not folder_id:
            return {"ok": True, "files": [], "message": ""}
        r = requests.post(
            _TOKEN_URL,
            data={"grant_type": "refresh_token",
                  "refresh_token": os.environ["GOOGLE_OAUTH_REFRESH_TOKEN"],
                  "client_id": os.environ["GOOGLE_OAUTH_CLIENT_ID"],
                  "client_secret": os.environ["GOOGLE_OAUTH_CLIENT_SECRET"]},
            timeout=timeout)
        r.raise_for_status()
        token = r.json()["access_token"]
        files = list_snapshots(token, folder_id, timeout=timeout)
    except Exception as e:
        print(f"[backup] drive list failed: {e}")
        return {"ok": False, "files": [],
                "message": "Could not reach Google Drive just now. Reload the page to try again."}
    out = [{"id": f.get("id", ""), "name": f.get("name", ""),
            "size": int(f.get("size") or 0), "created": f.get("createdTime", "")}
           for f in files]
    return {"ok": True, "files": out, "message": ""}


def backup_now(db_path: str) -> dict:
    """Take a snapshot and upload it to Google Drive, one run at a time.
    Raises BackupBusy (and logs nothing) if another backup is in progress."""
    if not _BACKUP_LOCK.acquire(blocking=False):
        raise BackupBusy("A backup is already running")
    try:
        return _backup_now_locked(db_path)
    finally:
        _BACKUP_LOCK.release()


def _backup_now_locked(db_path: str) -> dict:
    """Take a snapshot and upload it to Google Drive.
    Returns {name, bytes, drive_file_id, row_count}.

    Raises if Drive isn't configured, the integrity check fails, or the
    upload fails. Every attempt — success or failure — is logged to
    backup_log (see _log_attempt) before returning or re-raising, so the
    admin UI has a persistent record even when this raises.

    Durability audit item 2: runs check_integrity() against the live DB
    first, on every call (same cadence as the backup itself), and logs the
    result to integrity_check_log regardless of outcome. **A failed
    integrity check BLOCKS that night's upload** rather than uploading
    anyway — see check_integrity's docstring for why this check exists at
    all. Blocking, not upload-and-flag, was the deliberate choice: the
    entire point of running this before the snapshot is to stop corruption
    from being captured into Drive in the first place. Uploading it anyway
    would still overwrite/age out the retained good snapshots via
    prune_old_backups on the very next healthy run, so "upload anyway" buys
    nothing a human couldn't get from the loud integrity_check_log/
    backup_log failure alone, and it actively risks a corrupt file
    eventually becoming what section 1's restore procedure reaches for.
    Skipping the upload leaves every already-retained good snapshot
    untouched (prune only ever runs after a SUCCESSFUL upload), which is
    exactly the safe failure mode here — see CLAUDE.md's "Decisions for
    review" note on this PR for the full reasoning, flagged for Brian's
    sign-off rather than decided silently."""
    if not is_configured():
        _log_attempt(db_path, status="failure",
                     error="Google Drive backup is not configured "
                           "(GOOGLE_OAUTH_CLIENT_ID/CLIENT_SECRET/REFRESH_TOKEN not set)")
        raise RuntimeError("Google Drive backup is not configured")

    integrity = check_integrity(db_path)
    _log_integrity_attempt(db_path, integrity)
    if not integrity["ok"]:
        _log_attempt(db_path, status="failure",
                     error=f"Backup skipped — integrity check failed: {integrity['detail']}")
        raise RuntimeError(f"integrity check failed, backup skipped: {integrity['detail']}")

    try:
        tmp = snapshot_to_file(db_path)
        try:
            row_count = _article_count(tmp)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            name = f"library-{stamp}.db"

            token = _access_token()
            folder_id = _resolve_folder_id(db_path, token)
            metadata = {"name": name, "parents": [folder_id]}

            # Drive's multipart upload wants a metadata JSON part + a media part,
            # joined by a boundary — there's no `requests` helper for this shape
            # (it's multipart/related, not the multipart/form-data used elsewhere).
            # The file part is streamed from disk, never held in memory.
            boundary = uuid.uuid4().hex
            head = (
                f"--{boundary}\r\n"
                f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
                f"{json.dumps(metadata)}\r\n"
                f"--{boundary}\r\n"
                f"Content-Type: application/octet-stream\r\n\r\n"
            ).encode("utf-8")
            body = _MultipartBody(head, tmp, f"\r\n--{boundary}--".encode("utf-8"))
            size = os.path.getsize(tmp)

            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": f"multipart/related; boundary={boundary}",
            }
            # fields=id,name pins what the response body includes — Drive's
            # default response shape isn't guaranteed to carry `id`, and the
            # admin UI's Drive link depends on it.
            r = requests.post(f"{_UPLOAD_URL}&fields=id,name", headers=headers, data=body, timeout=(30, 600))
            r.raise_for_status()
        finally:
            os.remove(tmp)
        drive_file_id = r.json().get("id", "")
        result = {"name": name, "bytes": size, "drive_file_id": drive_file_id, "row_count": row_count}
        _log_attempt(db_path, status="success", filename=name, drive_file_id=drive_file_id,
                     size_bytes=size, row_count=row_count)
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

    The debounce reads its "last success" time from ``backup_log`` — the
    real, already-authoritative record every backup attempt (from any
    trigger: this debounce, the daily Railway Cron hitting
    ``POST /admin/backup-now``, or a manual click on
    ``/admin/library-backup``) already writes to. This used to read a
    standalone ``.last_backup`` marker file instead, written only by this
    function's own successful runs — since the real trigger keeping backups
    current is the daily cron, which never touched this file, the marker
    went stale (confirmed in production: dated weeks after backups were
    verifiably succeeding daily) while still looking exactly like a live
    status signal. A file that reads like a health signal and isn't one is
    worse than no file, so the marker and its write path are gone outright
    rather than "fixed" to be refreshed by more triggers — ``backup_log`` is
    already the single source of truth for this question and needn't be
    duplicated.
    """
    if not is_configured():
        return
    from linklib.db import Library
    lib = Library(db_path)
    try:
        # list_backup_log is ordered newest-first regardless of status, so
        # the most recent SUCCESS may not be row 0 (a debounce run right
        # after a failed attempt shouldn't re-trigger immediately just
        # because the newest row happens to be that failure).
        rows = lib.list_backup_log(limit=20)
    finally:
        lib.close()
    last_ts = 0.0
    for row in rows:
        if row.get("status") == "success":
            try:
                last_ts = datetime.fromisoformat(row["created_at"]).timestamp()
            except Exception:
                last_ts = 0.0
            break
    if time.time() - last_ts < min_interval_hours * 3600:
        return
    try:
        result = backup_now(db_path)
        print(f"[backup] uploaded {result['name']} ({result['bytes']} bytes) to Google Drive")
    except Exception as e:  # pragma: no cover - network/credential issues
        print(f"[backup] failed: {e}")
