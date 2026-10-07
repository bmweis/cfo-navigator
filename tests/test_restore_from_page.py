"""Restore from backup on /admin/library-backup (Phase 2 of #714). Drive is a
fake and the database is a scratch file: the restore script runs in-process
against it instead of as a subprocess. Nothing here touches the network or a
real database."""
import hashlib
import importlib
import json
import os
import pathlib
import shutil
import sqlite3
import sys
import time

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import backup, restore_status
from linklib.db import Article, Library
from scripts import restore_from_drive as rfd

TOKEN = "cron-token"


def _make_db(path, titles):
    lib = Library(path)
    for i, t in enumerate(titles):
        lib.upsert(Article(url=f"https://x.test/{os.path.basename(path)}/{i}", title=t))
    lib.close()


def _titles(path):
    c = sqlite3.connect(path)
    try:
        return sorted(r[0] for r in c.execute("SELECT title FROM articles"))
    finally:
        c.close()


@pytest.fixture
def world(tmp_path, monkeypatch):
    db = str(tmp_path / "library.db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_SITES_OPML", str(tmp_path / "sites.opml"))
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SAVE_TOKEN", TOKEN)
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    for k in ("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "GOOGLE_OAUTH_REFRESH_TOKEN"):
        monkeypatch.setenv(k, "x")
    monkeypatch.delenv("GOOGLE_DRIVE_FOLDER_ID", raising=False)
    _make_db(db, ["live-1", "live-2"])

    src = str(tmp_path / "snap-src.db")
    _make_db(src, ["snap-a", "snap-b", "snap-c"])
    snap = backup.snapshot_to_file(src)
    files = {"f-new": {"id": "f-new", "name": "library-20261005-090000.db", "createdTime": "2026-10-05T09:00:00Z",
                       "size": str(os.path.getsize(snap)), "src": snap}}

    monkeypatch.setattr(backup, "_access_token", lambda: "tok")
    monkeypatch.setattr(backup, "find_backup_folders", lambda t: [{"id": "folder1", "name": backup.FOLDER_NAME}])
    monkeypatch.setattr(backup, "list_snapshots",
                        lambda t, folder: [{k: v for k, v in f.items() if k != "src"} for f in files.values()])

    def fake_download(token, file_id, dest, chunk=1 << 20):
        shutil.copyfile(files[file_id]["src"], dest)
        return os.path.getsize(dest), hashlib.md5(open(dest, "rb").read()).hexdigest()
    monkeypatch.setattr(backup, "download_snapshot", fake_download)
    monkeypatch.setattr(backup, "list_for_display", lambda db_path, timeout=10.0: {
        "ok": True, "message": "",
        "files": [{"id": f["id"], "name": f["name"], "size": int(f["size"]), "created": f["createdTime"]}
                  for f in files.values()]})

    import webapp.app as appmod
    importlib.reload(appmod)
    ran = []

    def run_in_process(cmd):  # the subprocess stand-in: same script, same argv
        ran.append(cmd)
        return rfd.main(cmd[3:])  # drop: python -m scripts.restore_from_drive
    monkeypatch.setattr(appmod, "_restore_run_process", run_in_process)

    from fastapi.testclient import TestClient
    admin = TestClient(appmod.app)
    admin.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    anon = TestClient(appmod.app)
    yield {"app": appmod, "admin": admin, "anon": anon, "db": db, "tmp": tmp_path, "ran": ran, "files": files}


def _wait_done(timeout=30):
    end = time.time() + timeout
    while backup.backup_is_running() and time.time() < end:
        time.sleep(0.05)
    assert not backup.backup_is_running(), "restore thread did not finish"


def _start(w, mode="restore", confirm="RESTORE", fid="f-new", **kw):
    return w["admin"].post(f"/admin/library-backup/restore/{fid}", data={"mode": mode, "confirm": confirm},
                           follow_redirects=False, **kw)


def _audit(db):
    p = restore_status.paths(db)["audit"]
    return [json.loads(x) for x in open(p).read().splitlines()] if os.path.exists(p) else []


# --- refusals: nothing starts, nothing changes ---------------------------------

def test_refuses_without_the_typed_phrase(world):
    for bad in ("", "restore", "RESTORE ", "yes"):
        r = _start(world, confirm=bad)
        assert r.status_code == 400 and "Type RESTORE" in r.text
    assert world["ran"] == [] and _titles(world["db"]) == ["live-1", "live-2"]
    assert not backup.backup_is_running()


def test_restore_needs_an_admin_session_not_the_save_token(world):
    r = world["anon"].post("/admin/library-backup/restore/f-new", data={"mode": "restore", "confirm": "RESTORE"},
                           headers={"X-Save-Token": TOKEN}, follow_redirects=False)
    assert r.status_code == 401
    r = world["anon"].post("/admin/library-backup/restore/f-new", params={"token": TOKEN},
                           data={"mode": "restore", "confirm": "RESTORE"}, follow_redirects=False)
    assert r.status_code == 401
    assert world["anon"].get("/admin/library-backup/restore-status", headers={"X-Save-Token": TOKEN}).status_code == 401
    assert world["ran"] == []


def test_refuses_an_id_not_in_the_drive_list(world):
    r = _start(world, fid="not-a-real-id")
    assert r.status_code == 404 and "not in the list" in r.text
    assert world["admin"].get("/admin/library-backup/restore/not-a-real-id").status_code == 404
    assert world["ran"] == []


def test_refuses_while_a_backup_or_restore_holds_the_lock(world):
    assert backup.try_acquire_exclusive()
    try:
        r = _start(world)
        assert r.status_code == 409 and "backup is running" in r.text
    finally:
        backup.release_exclusive()
    assert world["ran"] == []


def test_refuses_while_a_background_job_runs(world):
    world["app"]._job_set("enrich", running=True)
    try:
        r = _start(world)
        assert r.status_code == 409 and "background job is running (enrich)" in r.text
    finally:
        world["app"]._job_set("enrich", running=False)
    assert world["ran"] == [] and not backup.backup_is_running()


def test_refuses_when_disk_is_short(world, monkeypatch):
    class Usage:
        free = 1_000
    monkeypatch.setattr(restore_status.shutil, "disk_usage", lambda p: Usage)
    r = _start(world)
    assert r.status_code == 409 and "Not enough free space" in r.text
    assert world["ran"] == []


def test_refuses_when_the_status_file_says_another_restore_runs(world):
    restore_status.reset_status(world["db"], state="running", stage="downloading", mode="restore",
                                pid=os.getpid(), started_at=restore_status.now_iso())
    r = _start(world)
    assert r.status_code == 409 and "already running" in r.text
    assert world["ran"] == []


def test_cron_gets_200_already_running_during_a_restore(world):
    assert backup.try_acquire_exclusive()  # what the restore thread holds
    try:
        r = world["anon"].post("/admin/backup-now", headers={"X-Save-Token": TOKEN})
        assert r.status_code == 200 and "already running" in r.text
    finally:
        backup.release_exclusive()


# --- a real restore, against a scratch database -------------------------------

def test_restore_swaps_the_file_and_writes_status_and_audit(world):
    db, tmp = world["db"], world["tmp"]
    # an older pre-restore copy, a stale download, and an unrelated file
    (tmp / "library.db.pre-restore-20260101-000000").write_bytes(b"old")
    (tmp / "library.db.pre-restore-20260101-000000-wal").write_bytes(b"")
    (tmp / ".restore-abc.tmp").write_bytes(b"junk")
    (tmp / "keep-me.txt").write_text("x")

    r = _start(world)
    assert r.status_code == 303 and r.headers["location"].endswith("restore=started")
    _wait_done()

    assert _titles(db) == ["snap-a", "snap-b", "snap-c"]
    st = restore_status.read_status(db)
    assert st["state"] == "finished" and st["post_applied"] is True
    names = sorted(p.name for p in tmp.iterdir())
    pre = [n for n in names if ".pre-restore-" in n and not n.endswith("-wal")]
    assert len(pre) == 1 and pre[0] != "library.db.pre-restore-20260101-000000"
    assert _titles(str(tmp / pre[0])) == ["live-1", "live-2"]      # the current file was kept
    assert not [n for n in names if n.startswith(".restore-")]     # stale and own tmp gone
    assert "keep-me.txt" in names
    log = "\n".join(restore_status.log_tail(db, 50))
    assert "Deleted older copy" in log and "Deleted leftover download" in log
    a = [x for x in _audit(db) if x["result"] == "finished"]
    assert len(a) == 1 and a[0]["file_id"] == "f-new" and a[0]["mode"] == "restore"
    assert a[0]["file_name"] == "library-20261005-090000.db" and str(a[0]["user"]) != "terminal"
    # the page reports it, in plain words
    page = world["admin"].get("/admin/library-backup").text
    assert "Restore finished." in page and "No restart is needed" in page
    j = world["admin"].get("/admin/library-backup/restore-status").json()
    assert j["state"] == "finished"


def test_check_mode_validates_and_swaps_nothing(world):
    r = _start(world, mode="check", confirm="")  # a check needs no typed phrase
    assert r.status_code == 303
    _wait_done()
    assert _titles(world["db"]) == ["live-1", "live-2"]
    st = restore_status.read_status(world["db"])
    assert st["state"] == "checked" and "valid backup with 3 articles" in st["message"]
    assert not [p for p in world["tmp"].iterdir() if ".pre-restore-" in p.name or p.name.startswith(".restore-")]
    assert _audit(world["db"])[-1]["mode"] == "check"


def test_failed_validation_after_the_swap_rolls_back(world, monkeypatch):
    db = world["db"]
    real = backup.check_integrity
    monkeypatch.setattr(backup, "check_integrity",
                        lambda p: {"ok": False, "detail": "simulated"} if os.path.abspath(p) == os.path.abspath(db)
                        else real(p))
    _start(world)
    _wait_done()
    assert _titles(db) == ["live-1", "live-2"]                      # the previous database is back
    st = restore_status.read_status(db)
    assert st["state"] == "rolled_back" and "put back" in st["message"] and "lost" in st["message"]
    assert not [p for p in world["tmp"].iterdir() if ".pre-restore-" in p.name]   # consumed by the rollback
    assert _audit(db)[-1]["result"] == "rolled_back"
    assert "rolled back" in world["admin"].get("/admin/library-backup").text


def test_script_removes_stale_download_even_when_it_fails_early(world, monkeypatch):
    (world["tmp"] / ".restore-zzz.tmp").write_bytes(b"junk")
    monkeypatch.setattr(backup, "download_snapshot", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        rfd.main(["--db", world["db"], "--yes-replace-live"])
    assert not [p for p in world["tmp"].iterdir() if p.name.startswith(".restore-")]
    assert restore_status.read_status(world["db"])["state"] == "failed"


# --- interrupted ----------------------------------------------------------------

def test_dead_process_shows_interrupted(world):
    restore_status.reset_status(world["db"], state="running", stage="downloading", mode="restore",
                                pid=2_999_999, started_at=restore_status.now_iso())
    assert world["admin"].get("/admin/library-backup/restore-status").json()["state"] == "interrupted"
    page = world["admin"].get("/admin/library-backup").text
    assert "Interrupted." in page and "railway ssh" in page and "pre-restore" in page


def test_stale_heartbeat_shows_interrupted_but_a_fresh_one_does_not(world):
    restore_status.reset_status(world["db"], state="running", stage="downloading", mode="restore",
                                pid=os.getpid(), started_at=restore_status.now_iso())
    assert restore_status.effective_state(restore_status.read_status(world["db"])) == "running"
    st = restore_status.read_status(world["db"])
    st["updated_at"] = "2020-01-01T00:00:00+00:00"
    assert restore_status.effective_state(st) == "interrupted"


# --- the page ------------------------------------------------------------------

def test_confirmation_page_says_what_restoring_does(world):
    html = world["admin"].get("/admin/library-backup/restore/f-new").text
    for needle in ("library-20261005-090000.db", "MCP tokens", "lost", "revoked", "Download a backup first",
                   "Check this backup", "Type <code>RESTORE</code>", "pre-restore"):
        assert needle in html, needle


def test_list_script_links_each_backup_to_its_confirmation_page(world):
    html = world["admin"].get("/admin/library-backup").text
    assert "/admin/library-backup/restore/'+encodeURIComponent(f.id)" in html
    assert "Use the terminal instead when the app is down" in html
    assert "python -m scripts.restore_from_drive --db /data/library.db --yes-replace-live" in html


def test_confirmation_page_is_not_a_hub_nav_orphan(world):
    assert world["app"].hub_nav_orphans() == []


def test_drive_list_stacks_on_a_phone_so_the_restore_button_is_never_off_screen(world):
    """At 390px the table used to scroll sideways and hide Restore from backup."""
    page = world["admin"].get("/admin/library-backup").text
    assert "t.className='drive-list-table bk-stack'" in page
    assert ".bk-stack td{border-bottom:none !important;" in page and ".bk-stack, .bk-stack tbody" in page


def test_panel_does_not_repeat_the_heading_in_the_message(world):
    app = world["app"]
    assert app._panel_msg("The restore failed: Not enough free space.") == "Not enough free space."
    assert app._panel_msg("Restore finished: 4,516 articles.") == "4,516 articles."
    assert app._panel_msg("Something else.") == "Something else."
