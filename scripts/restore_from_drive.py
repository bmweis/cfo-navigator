#!/usr/bin/env python3
"""Restore library.db from a Google Drive snapshot, run on the Railway
container (RUNBOOK.md section 1, Path C).

Why this exists: restoring through /admin/library-backup cannot work at
the current database size. The site sits behind Cloudflare, which rejects
request bodies over 100 MB on the Free plan, and the database is about
254 MB (the 2026-10-06 rehearsal ran for about 55 minutes and never logged
a POST). This script runs where the volume is, pulls the snapshot straight
from Drive with the same OAuth client the daily backup uses, and swaps it
in. No browser, no Cloudflare, nothing uploaded from home.

What it does, in order:
  1. Finds the Drive backup folder (--folder-id, then GOOGLE_DRIVE_FOLDER_ID,
     then the id stored in --db's settings table if that file still opens,
     then a search by folder name, which works under the drive.file scope
     because the app created the folder).
  2. Lists snapshots (--list) or picks one (--snapshot NAME_OR_ID, default
     the newest).
  3. Checks free space, then streams the snapshot into a temp file in the
     SAME directory as --db (so the final swap is an atomic os.replace),
     and checks size and md5 against what Drive reports.
  4. Validates it: PRAGMA integrity_check, the FTS5 self-check, and
     SELECT COUNT(*) FROM articles. A corrupt or non-library file is
     refused and the destination is never touched.
  5. --dry-run stops here, having written nothing to the destination.
  6. Otherwise keeps the current database as <db>.pre-restore-<timestamp>
     (a hard link, so it costs no extra space; a copy if the filesystem
     cannot link), swaps the snapshot in with os.replace, and removes the
     stale -wal and -shm files. The pre-restore copy is never deleted.

Safe by default: if the destination exists it refuses to replace it
unless --yes-replace-live is passed. A destination that does not exist
(a scratch path, or a volume that was lost) needs no flag.

Side effects of the old UI path (/admin/library-backup/upload-db) and how
this script handles each:
  * validate before swapping (SELECT COUNT(*) FROM articles): done, plus
    integrity_check and the FTS5 self-check.
  * atomic os.replace onto the live path: done.
  * remove -wal and -shm next to the live file: done.
  * no restart needed (the app opens a fresh connection per request): the
    same holds here.
  * the redirect banner "uploaded=N": replaced by the article count this
    script prints.
  * the post-restore checklist (health, search, a fresh Drive snapshot via
    POST /admin/backup-now): NOT automated. The script prints it; do it
    from RUNBOOK.md section 1.
  Stop the Railway cron or avoid a manual backup while the swap runs; a
  request that already has the old file open keeps reading the old inode
  (which is now the pre-restore copy) until it finishes.

Needs the same variables as the daily backup: GOOGLE_OAUTH_CLIENT_ID,
GOOGLE_OAUTH_CLIENT_SECRET, GOOGLE_OAUTH_REFRESH_TOKEN.

Usage (inside `railway ssh`, from /app):
    python -m scripts.restore_from_drive --db /data/library.db --list
    python -m scripts.restore_from_drive --db /data/restore-test.db --dry-run
    python -m scripts.restore_from_drive --db /data/restore-test.db
    python -m scripts.restore_from_drive --db /data/library.db --yes-replace-live
    python -m scripts.restore_from_drive --db /data/library.db --snapshot library-20261005-090001.db --yes-replace-live
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from linklib import backup  # noqa: E402
from linklib.db import resolve_db_path  # noqa: E402

SPACE_MARGIN = 16 * 1024 * 1024


def _mb(n: int) -> str:
    return f"{n / 1_000_000:,.1f} MB"


def _folder_from_db(db_path: str) -> str:
    if not os.path.isfile(db_path):
        return ""
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            row = conn.execute("SELECT value FROM settings WHERE key=?",
                               (backup._FOLDER_SETTING_KEY,)).fetchone()
            return row[0] if row else ""
        finally:
            conn.close()
    except Exception:
        return ""


def resolve_folder(token: str, db_path: str, cli_folder: str | None) -> str:
    if cli_folder:
        return cli_folder
    env = os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "").strip()
    if env:
        return env
    stored = _folder_from_db(db_path)
    if stored:
        return stored
    found = backup.find_backup_folders(token)
    if not found:
        sys.exit(f"No Drive folder named '{backup.FOLDER_NAME}' is visible to this OAuth token. "
                 "Pass --folder-id if you know it.")
    if len(found) > 1:
        ids = ", ".join(f"{f['id']} (created {f.get('createdTime', '?')})" for f in found)
        sys.exit(f"{len(found)} folders match the backup folder name: {ids}. Pick one with --folder-id.")
    return found[0]["id"]


def _logged_counts(db_path: str) -> dict:
    """Best effort: article counts the app recorded per Drive file id."""
    if not os.path.isfile(db_path):
        return {}
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            return {r[0]: r[1] for r in conn.execute(
                "SELECT drive_file_id, row_count FROM backup_log "
                "WHERE status='success' AND drive_file_id<>''")}
        finally:
            conn.close()
    except Exception:
        return {}


def print_list(files: list[dict], counts: dict) -> None:
    print(f"{'name':32} {'size':>11}  {'created (UTC)':20} articles")
    for f in files:
        size = int(f["size"]) if f.get("size") else 0
        n = counts.get(f["id"])
        print(f"{f['name']:32} {_mb(size):>11}  {f.get('createdTime', '')[:19]:20} "
              f"{n if n is not None else 'unknown'}")


def pick(files: list[dict], wanted: str | None) -> dict:
    if not files:
        sys.exit("The Drive backup folder has no snapshots.")
    if not wanted:
        return files[0]
    for f in files:
        if wanted in (f["name"], f["id"]):
            return f
    sys.exit(f"No snapshot named or with id '{wanted}'. Run with --list.")


def validate(path: str) -> int:
    res = backup.check_integrity(path)
    if not res["ok"]:
        raise ValueError(f"integrity check failed: {res['detail']}")
    try:
        return backup._article_count(path)
    except Exception as e:
        raise ValueError(f"not a library database: {e}")


def _remove_sidecars(path: str) -> None:
    for sc in ("-wal", "-shm"):
        try:
            os.remove(path + sc)
        except FileNotFoundError:
            pass


def keep_current(dest: str, stamp: str) -> str:
    """Keep the current database as <dest>.pre-restore-<stamp>. Hard link
    (no extra space); copy if the filesystem cannot link. Returns its path."""
    try:  # fold pending WAL pages into the main file so the kept copy is whole
        conn = sqlite3.connect(dest, timeout=5)
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()
    except Exception:
        pass
    pre = f"{dest}.pre-restore-{stamp}"
    for sc in ("", "-wal"):
        src = dest + sc
        if not os.path.exists(src):
            continue
        try:
            os.link(src, pre + sc)
        except OSError:
            free = shutil.disk_usage(os.path.dirname(dest) or ".").free
            need = os.path.getsize(src) + SPACE_MARGIN
            if free < need:
                raise RuntimeError(f"cannot keep the current database: {_mb(free)} free, "
                                   f"need {_mb(need)} for a copy")
            shutil.copy2(src, pre + sc)
    return pre


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=None, help="Destination path, absolute (defaults via LINKLIB_DB)")
    ap.add_argument("--list", action="store_true", help="List Drive snapshots and exit")
    ap.add_argument("--snapshot", default=None, help="Snapshot name or Drive id (default: newest)")
    ap.add_argument("--folder-id", default=None, help="Drive folder id (default: discovered)")
    ap.add_argument("--dry-run", action="store_true", help="Download and validate only; write nothing to the destination")
    ap.add_argument("--yes-replace-live", action="store_true",
                    help="Required to replace a destination that already exists")
    args = ap.parse_args(argv)

    if not backup.is_configured():
        sys.exit("GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET and GOOGLE_OAUTH_REFRESH_TOKEN "
                 "are not all set in this environment.")
    dest = resolve_db_path(args.db, allow_missing=True)
    dest_dir = os.path.dirname(dest)
    if not os.path.isdir(dest_dir):
        sys.exit(f"Destination directory {dest_dir} does not exist.")

    token = backup._access_token()
    folder_id = resolve_folder(token, dest, args.folder_id)
    files = backup.list_snapshots(token, folder_id)

    if args.list:
        print_list(files, _logged_counts(dest))
        return 0

    snap = pick(files, args.snapshot)
    size = int(snap.get("size") or 0)
    exists = os.path.exists(dest)
    free = shutil.disk_usage(dest_dir).free
    need = size + SPACE_MARGIN
    print(f"Snapshot:     {snap['name']} ({_mb(size)}, created {snap.get('createdTime', '?')})")
    print(f"Destination:  {dest} ({'exists' if exists else 'does not exist'})")
    print(f"Free space:   {_mb(free)} (download needs {_mb(need)}; keeping the current file is a hard link, "
          f"a copy needs another {_mb(os.path.getsize(dest)) if exists else '0 MB'} if links are unsupported)")

    if exists and not args.dry_run and not args.yes_replace_live:
        sys.exit(f"Refusing to replace {dest} without --yes-replace-live. "
                 "Re-run with --dry-run first to download and validate only.")
    if free < need:
        sys.exit(f"Not enough free space in {dest_dir}: {_mb(free)} free, need {_mb(need)}. Nothing was written.")

    fd, tmp = tempfile.mkstemp(dir=dest_dir, prefix=".restore-", suffix=".tmp")
    os.close(fd)
    try:
        got, md5 = backup.download_snapshot(token, snap["id"], tmp)
        print(f"Downloaded {_mb(got)}.")
        if size and got != size:
            sys.exit(f"Downloaded {got} bytes but Drive reports {size}. Destination untouched.")
        if snap.get("md5Checksum") and snap["md5Checksum"] != md5:
            sys.exit("md5 does not match Drive's checksum. Destination untouched.")
        try:
            count = validate(tmp)
        except ValueError as e:
            sys.exit(f"Snapshot refused: {e}. Destination untouched.")
        _remove_sidecars(tmp)
        print(f"Validated: integrity ok, {count:,} articles.")

        if args.dry_run:
            print("Dry run: nothing written to the destination.")
            return 0

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        pre = ""
        if exists:
            pre = keep_current(dest, stamp)
            print(f"Kept the current database as {pre} (never deleted by this script).")
        os.replace(tmp, dest)
        tmp = ""
        _remove_sidecars(dest)
        after = backup._article_count(dest)
        print(f"Restored {dest}: {after:,} articles.")
        if after != count:
            sys.exit(f"Article count after the swap ({after}) differs from the validated file ({count}).")
        print("\nNext (RUNBOOK.md section 1, post-restore checklist):\n"
              "  1. curl https://bmweis.com/health\n"
              "  2. Check /read?view=saved and one search.\n"
              "  3. POST /admin/backup-now so Drive holds the restored state.\n"
              "  4. Confirm the new row on /admin/library-backup.")
        return 0
    finally:
        if tmp and os.path.exists(tmp):
            os.remove(tmp)
        if tmp:
            _remove_sidecars(tmp)


if __name__ == "__main__":
    raise SystemExit(main())
