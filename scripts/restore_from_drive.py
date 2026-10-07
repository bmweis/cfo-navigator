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

Side effects of the old UI upload path (removed 2026-10) and how
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
import glob
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from linklib import backup  # noqa: E402
from linklib import restore_status as rs  # noqa: E402
from linklib.db import resolve_db_path  # noqa: E402

SPACE_MARGIN = rs.SPACE_MARGIN
HEARTBEAT_SECONDS = 5


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


class Reporter:
    """Prints every step and records it for the admin page: a status file,
    a log, and one audit line at the end (all on the volume next to the
    database, never inside it). --list never creates one."""

    def __init__(self, dest: str, mode: str, user: str):
        self.dest, self.mode, self.user = dest, mode, user
        self.snap: dict = {}
        self.final = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.tmp_path = ""
        self.total = 0

    def begin(self) -> None:
        rs.reset_status(self.dest, state="running", stage="starting", mode=self.mode,
                        pid=os.getpid(), started_at=rs.now_iso(), message="", user=self.user)
        self._thread = threading.Thread(target=self._beat, daemon=True)
        self._thread.start()

    def _beat(self) -> None:
        while not self._stop.wait(HEARTBEAT_SECONDS):
            try:
                extra = {}
                if self.tmp_path and os.path.exists(self.tmp_path):
                    extra = {"downloaded_bytes": os.path.getsize(self.tmp_path), "total_bytes": self.total}
                rs.write_status(self.dest, **extra)
            except Exception:
                pass

    def say(self, msg: str) -> None:
        print(msg, flush=True)
        try:
            rs.log_line(self.dest, msg)
        except OSError:
            pass

    def stage(self, stage: str, msg: str = "") -> None:
        if msg:
            self.say(msg)
        rs.write_status(self.dest, stage=stage)

    def end(self, state: str, message: str) -> None:
        if self.final:
            return
        self.final = True
        self._stop.set()
        self.say(message)
        rs.write_status(self.dest, state=state, stage=state, message=message, finished_at=rs.now_iso(),
                        pid=0)
        rs.audit(self.dest, user=self.user, file_id=self.snap.get("id", ""),
                 file_name=self.snap.get("name", ""), mode=self.mode, result=state, message=message)


def _stamp_of(path: str) -> str:
    return path.rsplit(".pre-restore-", 1)[1]


def prune_pre_restore(dest: str, keep: str, say) -> None:
    """Keep the newest pre-restore file (the one just made) and delete older
    ones with their -wal copies. Never touches `keep`."""
    found = sorted({p for p in glob.glob(glob.escape(dest) + ".pre-restore-*") if not p.endswith("-wal")},
                   key=_stamp_of, reverse=True)
    for p in found:
        if os.path.abspath(p) == os.path.abspath(keep):
            continue
        if _stamp_of(p) >= _stamp_of(keep):
            continue  # never delete anything newer than the file just created
        for q in (p, p + "-wal"):
            try:
                sz = os.path.getsize(q)
                os.remove(q)
                say(f"Deleted older copy {q} ({_mb(sz)}).")
            except FileNotFoundError:
                pass


def remove_stale_tmp(dest_dir: str, say) -> None:
    for p in glob.glob(os.path.join(glob.escape(dest_dir), ".restore-*.tmp")):
        try:
            sz = os.path.getsize(p)
            os.remove(p)
            say(f"Deleted leftover download {p} ({_mb(sz)}).")
        except OSError:
            pass
        _remove_sidecars(p)


def rollback(dest: str, pre: str) -> None:
    """Put the kept pre-restore database back with one rename."""
    os.replace(pre, dest)
    if os.path.exists(pre + "-wal"):
        os.replace(pre + "-wal", dest + "-wal")
    else:
        try:
            os.remove(dest + "-wal")
        except FileNotFoundError:
            pass
    try:
        os.remove(dest + "-shm")
    except FileNotFoundError:
        pass


def _run(args, rep: Reporter, dest: str, dest_dir: str) -> int:
    token = backup._access_token()
    folder_id = resolve_folder(token, dest, args.folder_id)
    files = backup.list_snapshots(token, folder_id)
    snap = pick(files, args.snapshot)
    rep.snap = snap
    size = int(snap.get("size") or 0)
    exists = os.path.exists(dest)
    free = shutil.disk_usage(dest_dir).free
    need = size + SPACE_MARGIN
    rep.say(f"Snapshot:     {snap['name']} ({_mb(size)}, created {snap.get('createdTime', '?')})")
    rep.say(f"Destination:  {dest} ({'exists' if exists else 'does not exist'})")
    rep.say(f"Free space:   {_mb(free)} (download needs {_mb(need)}; keeping the current file is a hard link, "
            f"a copy needs another {_mb(os.path.getsize(dest)) if exists else '0 MB'} if links are unsupported)")

    if exists and not args.dry_run and not args.yes_replace_live:
        sys.exit(f"Refusing to replace {dest} without --yes-replace-live. "
                 "Re-run with --dry-run first to download and validate only.")
    if free < need:
        sys.exit(f"Not enough free space in {dest_dir}: {_mb(free)} free, need {_mb(need)}. Nothing was written.")

    remove_stale_tmp(dest_dir, rep.say)
    fd, tmp = tempfile.mkstemp(dir=dest_dir, prefix=".restore-", suffix=".tmp")
    os.close(fd)
    rep.tmp_path, rep.total = tmp, size
    swapped = False
    pre = ""
    try:
        rep.stage("downloading", f"Downloading {snap['name']} from Drive.")
        got, md5 = backup.download_snapshot(token, snap["id"], tmp)
        rep.say(f"Downloaded {_mb(got)}.")
        if size and got != size:
            sys.exit(f"Downloaded {got} bytes but Drive reports {size}. Destination untouched.")
        if snap.get("md5Checksum") and snap["md5Checksum"] != md5:
            sys.exit("md5 does not match Drive's checksum. Destination untouched.")
        rep.stage("validating", "Checking the downloaded file (integrity, search index, article count).")
        try:
            count = validate(tmp)
        except ValueError as e:
            sys.exit(f"Snapshot refused: {e}. Destination untouched.")
        _remove_sidecars(tmp)
        rep.say(f"Validated: integrity ok, {count:,} articles.")

        if args.dry_run:
            rep.end("checked", f"Dry run finished: {snap['name']} is a valid backup with {count:,} articles. "
                               "Nothing was changed.")
            return 0

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        rep.stage("swapping", "Swapping the restored database in.")
        if exists:
            pre = keep_current(dest, stamp)
            rep.say(f"Kept the current database as {pre}.")
        try:
            os.replace(tmp, dest)
            tmp = ""
            swapped = True
            _remove_sidecars(dest)
            after = backup._article_count(dest)
            res = backup.check_integrity(dest)
            if after != count:
                raise ValueError(f"article count after the swap ({after}) differs from the validated file ({count})")
            if not res["ok"]:
                raise ValueError(f"integrity check failed after the swap: {res['detail']}")
        except Exception as e:
            if swapped and pre:
                rollback(dest, pre)
                rep.end("rolled_back",
                        f"The restore failed after the swap ({e}). The previous database was put back. "
                        "Anything written to the database between the swap and the rollback is lost.")
                return 1
            raise
        rep.say(f"Restored {dest}: {after:,} articles.")
        if pre:
            prune_pre_restore(dest, pre, rep.say)
        rep.end("finished", f"Restore finished: {after:,} articles from {snap['name']}. "
                            f"The previous database is kept as {pre or 'none (there was no previous file)'}.")
        return 0
    finally:
        rep.tmp_path = ""
        if tmp and os.path.exists(tmp):
            os.remove(tmp)
        if tmp:
            _remove_sidecars(tmp)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=None, help="Destination path, absolute (defaults via LINKLIB_DB)")
    ap.add_argument("--list", action="store_true", help="List Drive snapshots and exit")
    ap.add_argument("--snapshot", default=None, help="Snapshot name or Drive id (default: newest)")
    ap.add_argument("--folder-id", default=None, help="Drive folder id (default: discovered)")
    ap.add_argument("--dry-run", action="store_true", help="Download and validate only; write nothing to the destination")
    ap.add_argument("--yes-replace-live", action="store_true",
                    help="Required to replace a destination that already exists")
    ap.add_argument("--audit-user", default="terminal",
                    help="Who to record in restore-audit.jsonl (the admin page passes the admin's user id)")
    args = ap.parse_args(argv)

    if not backup.is_configured():
        sys.exit("GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET and GOOGLE_OAUTH_REFRESH_TOKEN "
                 "are not all set in this environment.")
    dest = resolve_db_path(args.db, allow_missing=True)
    dest_dir = os.path.dirname(dest)
    if not os.path.isdir(dest_dir):
        sys.exit(f"Destination directory {dest_dir} does not exist.")

    if args.list:
        token = backup._access_token()
        folder_id = resolve_folder(token, dest, args.folder_id)
        print_list(backup.list_snapshots(token, folder_id), _logged_counts(dest))
        return 0

    prior = rs.read_status(dest)
    if rs.effective_state(prior) == "running" and int(prior.get("pid") or 0) != os.getpid():
        sys.exit("Another restore is already running (see restore-status.json). Nothing was changed.")

    rep = Reporter(dest, "check" if args.dry_run else "restore", args.audit_user)
    rep.begin()
    try:
        rc = _run(args, rep, dest, dest_dir)
    except SystemExit as e:
        rep.end("failed", str(e.code) if isinstance(e.code, str) else "The restore did not finish.")
        raise
    except Exception as e:
        rep.end("failed", f"The restore failed: {e}")
        raise
    if not rep.final:
        rep.end("failed", "The restore ended without a result.")
    if rc == 0 and not args.dry_run:
        rep.say("\nNext (RUNBOOK.md section 1, post-restore checklist):\n"
                "  1. curl https://bmweis.com/health\n"
                "  2. Check /read?view=saved and one search.\n"
                "  3. Run Back up to Drive now on /admin/library-backup so Drive holds the restored state.")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
