"""The restore button end to end with nothing in-process: the real route, a real
child interpreter running the real scripts/restore_from_drive.py, a scratch
database, and a fake Drive (tests/fake_drive_site/sitecustomize.py). #729's
tests ran the script in-process, where the route's own "running" claim and the
script's check shared one pid, so a start-up collision between them passed CI
and failed in production."""
import importlib
import json
import os
import pathlib
import sqlite3
import sys
import time

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import backup, restore_status
from linklib.db import Article, Library

ROOT = pathlib.Path(__file__).resolve().parents[1]


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
    data = tmp_path / "data"
    data.mkdir()
    db = str(data / "library.db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_SITES_OPML", str(tmp_path / "sites.opml"))
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SAVE_TOKEN", "cron-token")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    for k in ("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "GOOGLE_OAUTH_REFRESH_TOKEN"):
        monkeypatch.setenv(k, "x")
    monkeypatch.delenv("GOOGLE_DRIVE_FOLDER_ID", raising=False)
    _make_db(db, ["live-1", "live-2"])

    src = str(tmp_path / "snap-src.db")
    _make_db(src, ["snap-a", "snap-b", "snap-c"])
    snap = backup.snapshot_to_file(src)
    files = [{"id": "f-new", "name": "library-20261005-090000.db", "createdTime": "2026-10-05T09:00:00Z",
              "size": os.path.getsize(snap), "src": snap}]
    cfg = tmp_path / "fake-drive.json"
    cfg.write_text(json.dumps(files))
    # The child gets the repo root (so linklib imports) and the fake Drive.
    monkeypatch.setenv("FAKE_DRIVE_JSON", str(cfg))
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(ROOT / "tests" / "fake_drive_site"), str(ROOT)]))

    # The route's own file lookup (in this process) sees the same fake Drive.
    monkeypatch.setattr(backup, "_access_token", lambda: "tok")
    monkeypatch.setattr(backup, "list_for_display", lambda db_path, timeout=10.0: {
        "ok": True, "message": "",
        "files": [{"id": f["id"], "name": f["name"], "size": f["size"], "created": f["createdTime"]} for f in files]})

    import webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    admin = TestClient(appmod.app)
    admin.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    return {"app": appmod, "admin": admin, "db": db, "data": data, "tmp": tmp_path}


def _wait_done(timeout=90):
    end = time.time() + timeout
    while backup.backup_is_running() and time.time() < end:
        time.sleep(0.1)
    assert not backup.backup_is_running(), "restore thread did not finish"


def _start(w, mode, confirm="RESTORE"):
    return w["admin"].post("/admin/library-backup/restore/f-new", data={"mode": mode, "confirm": confirm},
                           follow_redirects=False)


def test_real_subprocess_restore_swaps_and_finishes(world):
    w = world
    r = _start(w, "restore")
    assert r.status_code == 303
    _wait_done()
    st = restore_status.read_status(w["db"])
    assert st["state"] == "finished", (st, restore_status.log_tail(w["db"], 20))
    assert _titles(w["db"]) == ["snap-a", "snap-b", "snap-c"]
    pre = [p.name for p in w["data"].iterdir() if ".pre-restore-" in p.name and not p.name.endswith(("-wal", "-shm"))]
    assert len(pre) == 1
    assert not [p for p in w["data"].iterdir() if p.name.startswith(".restore-")]
    audit = [json.loads(x) for x in open(restore_status.paths(w["db"])["audit"])]
    assert audit[-1]["result"] == "finished"


def test_real_subprocess_check_validates_and_swaps_nothing(world):
    w = world
    before = open(w["db"], "rb").read()
    r = _start(w, "check")
    assert r.status_code == 303
    _wait_done()
    st = restore_status.read_status(w["db"])
    assert st["state"] == "checked", (st, restore_status.log_tail(w["db"], 20))
    assert _titles(w["db"]) == ["live-1", "live-2"]
    assert not [p for p in w["data"].iterdir() if ".pre-restore-" in p.name]
    assert not [p for p in w["data"].iterdir() if p.name.startswith(".restore-")]


# --- a refusal says so, and a retry is allowed ----------------------------------

def _set_fake_drive(w, files):
    (w["tmp"] / "fake-drive.json").write_text(json.dumps(files))


def _audit(db):
    p = restore_status.paths(db)["audit"]
    return [json.loads(x) for x in open(p)] if os.path.exists(p) else []


def test_a_script_refusal_says_nothing_was_changed_and_a_retry_works(world):
    w = world
    good = json.loads((w["tmp"] / "fake-drive.json").read_text())
    _set_fake_drive(w, [])  # the route saw the backup, the script no longer does
    assert _start(w, "restore").status_code == 303
    _wait_done()
    st = restore_status.read_status(w["db"])
    assert st["state"] == "refused", st
    assert st["message"] and "stopped without a result" not in st["message"]
    assert _audit(w["db"])[-1]["result"] == "refused"
    page = w["admin"].get("/admin/library-backup").text
    assert 'data-state="refused"' in page and "Nothing was changed." in page
    assert "The restore failed" not in page and "stopped without a result" not in page
    assert _titles(w["db"]) == ["live-1", "live-2"]
    # no stuck "running" record: the next attempt is allowed and works
    _set_fake_drive(w, good)
    assert _start(w, "restore").status_code == 303
    _wait_done()
    assert restore_status.read_status(w["db"])["state"] == "finished"
    assert _titles(w["db"]) == ["snap-a", "snap-b", "snap-c"]


def test_a_dead_running_record_does_not_block_a_new_attempt(world):
    w = world
    restore_status.reset_status(w["db"], state="running", stage="downloading", mode="restore", pid=2999999,
                                started_at=restore_status.now_iso(), job_id="old", user=1)
    assert _start(w, "check").status_code == 303
    _wait_done()
    assert restore_status.read_status(w["db"])["state"] == "checked"


# --- a genuinely concurrent restore is still refused ----------------------------

def test_route_refuses_while_another_restore_really_runs(world):
    w = world
    restore_status.reset_status(w["db"], state="running", stage="downloading", mode="restore",
                                pid=os.getpid(), started_at=restore_status.now_iso(), job_id="other", user=1)
    r = _start(w, "restore")
    assert r.status_code == 409 and "already running" in r.text
    assert restore_status.read_status(w["db"])["job_id"] == "other"  # untouched
    assert _titles(w["db"]) == ["live-1", "live-2"]


@pytest.mark.parametrize("job_id", [None, "someone-elses-job"])
def test_script_refuses_a_second_restore_and_leaves_the_first_status_alone(world, job_id):
    import subprocess
    w = world
    restore_status.reset_status(w["db"], state="running", stage="downloading", mode="restore",
                                pid=os.getpid(), started_at=restore_status.now_iso(), job_id="first", user=1)
    before = restore_status.read_status(w["db"])
    cmd = [sys.executable, "-m", "scripts.restore_from_drive", "--db", w["db"], "--snapshot", "f-new",
           "--yes-replace-live"] + (["--job-id", job_id] if job_id else [])
    p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=60)
    assert p.returncode != 0
    assert "already running" in (p.stdout + p.stderr)
    after = restore_status.read_status(w["db"])
    assert after["state"] == "running" and after["job_id"] == "first" and after["pid"] == before["pid"]
    assert _titles(w["db"]) == ["live-1", "live-2"]


# --- the per-row "Check backup" button -------------------------------------

def test_row_check_needs_an_admin_session_not_the_save_token(world):
    w = world
    from fastapi.testclient import TestClient
    tok = TestClient(w["app"].app)
    r = tok.post("/admin/library-backup/check/f-new", headers={"X-Save-Token": "cron-token"}, follow_redirects=False)
    assert r.status_code == 401
    assert tok.post("/admin/library-backup/check/f-new", follow_redirects=False).status_code == 401
    assert restore_status.read_status(w["db"]) == {}


def test_row_check_refuses_an_id_not_in_the_drive_list(world):
    w = world
    r = w["admin"].post("/admin/library-backup/check/not-a-real-id", follow_redirects=False)
    assert r.status_code == 404 and "not in the list of backups in Drive" in r.text
    assert restore_status.read_status(w["db"]) == {}


def test_row_check_runs_and_swaps_nothing(world):
    w = world
    r = w["admin"].post("/admin/library-backup/check/f-new", follow_redirects=False)
    assert r.status_code == 303
    _wait_done()
    st = restore_status.read_status(w["db"])
    assert st["state"] == "checked" and st["mode"] == "check", (st, restore_status.log_tail(w["db"], 20))
    assert _titles(w["db"]) == ["live-1", "live-2"]
    page = w["admin"].get("/admin/library-backup").text
    assert "Check finished." in page


@pytest.mark.parametrize("why", ["lock", "job", "disk", "status"])
def test_row_check_is_refused_plainly_with_no_side_effects(world, monkeypatch, why):
    w = world
    app = w["app"]
    if why == "lock":
        assert backup.try_acquire_exclusive()
    elif why == "job":
        monkeypatch.setitem(app._JOB_STATE, "enrich", {"running": True})
    elif why == "disk":
        monkeypatch.setattr(restore_status, "disk_problem", lambda db, size: "Not enough free disk space.")
    else:
        restore_status.reset_status(w["db"], state="running", stage="downloading", mode="restore",
                                    pid=os.getpid(), started_at=restore_status.now_iso(), job_id="x", user=1)
    try:
        r = w["admin"].post("/admin/library-backup/check/f-new", follow_redirects=False)
        assert r.status_code == 409 and "Nothing was started" in r.text
    finally:
        if why == "lock":
            backup.release_exclusive()
    assert _titles(w["db"]) == ["live-1", "live-2"]
    if why != "status":
        assert restore_status.read_status(w["db"]) == {}


def test_list_script_has_both_buttons_per_row(world):
    page = world["admin"].get("/admin/library-backup").text
    assert "'/admin/library-backup/check/'" in page and "Check backup" in page
    assert "'/admin/library-backup/restore/'" in page and "Restore backup" in page


def test_both_buttons_fit_and_stay_tappable_at_390px(world, tmp_path):
    """Chromium only; skipped where no browser is installed (as in CI)."""
    sync_api = pytest.importorskip("playwright.sync_api")
    import threading
    import uvicorn
    w = world
    cfg = uvicorn.Config(w["app"].app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(cfg)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        pw = sync_api.sync_playwright().start()
    except Exception:
        server.should_exit = True
        pytest.skip("playwright unavailable")
    try:
        exe = "/opt/pw-browsers/chromium"
        try:
            b = pw.chromium.launch(executable_path=exe) if os.path.exists(exe) else pw.chromium.launch()
        except Exception:
            pytest.skip("no chromium")
        try:
            ctx = b.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
            pg = ctx.new_page()
            base = f"http://127.0.0.1:{port}"
            pg.goto(base + "/login")
            pg.fill("input[name=username]", "admin")
            pg.fill("input[name=password]", "adminpass")
            pg.click("button[type=submit]")
            pg.goto(base + "/admin/library-backup")
            pg.wait_for_selector(".bk-table")
            for sel in ("button:has-text('Check backup')", "a:has-text('Restore backup')"):
                box = pg.locator(".bk-table " + sel).first.bounding_box()
                assert box and box["x"] >= 0 and box["x"] + box["width"] <= 390, (sel, box)
                assert box["height"] >= 24
            assert pg.evaluate("document.documentElement.scrollWidth") <= 390
        finally:
            b.close()
    finally:
        pw.stop()
        server.should_exit = True


def test_panel_shows_only_the_current_attempts_log(world):
    w = world
    good = json.loads((w["tmp"] / "fake-drive.json").read_text())
    assert _start(w, "check").status_code == 303
    _wait_done()
    _set_fake_drive(w, [])
    assert _start(w, "restore").status_code == 303
    _wait_done()
    _set_fake_drive(w, good)
    page = w["admin"].get("/admin/library-backup").text
    log = page.split('id="restore-log"')[1].split("</pre>")[0]
    assert "Restore of" in log and "Dry run finished" not in log and "valid backup" not in log


# --- Interrupted, retry after an old record, two real processes -----------------

def _script_cmd(w, *extra, mode="restore"):
    return [sys.executable, "-m", "scripts.restore_from_drive", "--db", w["db"], "--snapshot", "f-new",
            "--dry-run" if mode == "check" else "--yes-replace-live", *extra]


def _wait_for(pred, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        v = pred()
        if v:
            return v
        time.sleep(0.1)
    raise AssertionError("timed out waiting")


def test_a_killed_script_is_reported_interrupted_and_a_new_attempt_is_allowed(world, monkeypatch):
    """The record holds the SCRIPT's pid once it has started (not the web app's, which is always alive),
    so a script killed mid-download is Interrupted straight away, with no wait for the heartbeat."""
    import signal
    import subprocess
    w = world
    monkeypatch.setenv("FAKE_DRIVE_SLEEP", "30")
    proc = subprocess.Popen(_script_cmd(w), cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        st = _wait_for(lambda: (lambda s: s if s.get("stage") == "downloading" else None)(
            restore_status.read_status(w["db"])))
        assert st["pid"] == proc.pid and st["pid"] != os.getpid()
        assert w["app"]._restore_state_view(w["db"])["state"] == "running"
        proc.send_signal(signal.SIGKILL)
        proc.wait()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    assert restore_status.read_status(w["db"])["state"] == "running"  # nobody recorded an ending
    assert w["app"]._restore_state_view(w["db"])["state"] == "interrupted"
    page = w["admin"].get("/admin/library-backup").text
    assert 'data-state="interrupted"' in page and "Interrupted." in page
    assert _titles(w["db"]) == ["live-1", "live-2"]
    # a new attempt is allowed and works
    monkeypatch.setenv("FAKE_DRIVE_SLEEP", "0")
    assert _start(w, "restore").status_code == 303
    _wait_done()
    assert restore_status.read_status(w["db"])["state"] == "finished"
    assert _titles(w["db"]) == ["snap-a", "snap-b", "snap-c"]


def test_route_watching_a_killed_script_records_a_truthful_failure(world, monkeypatch):
    """When the web app is alive and watching, a killed script is noticed at once and the panel says
    it is not known whether anything changed (a restore) instead of staying 'running'."""
    import signal
    monkeypatch.setenv("FAKE_DRIVE_SLEEP", "30")
    w = world
    assert _start(w, "restore").status_code == 303
    st = _wait_for(lambda: (lambda s: s if s.get("stage") == "downloading" else None)(
        restore_status.read_status(w["db"])))
    assert st["pid"] != os.getpid()
    os.kill(int(st["pid"]), signal.SIGKILL)
    _wait_done()
    st = restore_status.read_status(w["db"])
    assert st["state"] == "failed" and "not known whether anything changed" in st["message"]
    assert _titles(w["db"]) == ["live-1", "live-2"]
    monkeypatch.setenv("FAKE_DRIVE_SLEEP", "0")
    assert _start(w, "check").status_code == 303
    _wait_done()
    assert restore_status.read_status(w["db"])["state"] == "checked"


OLD_RECORDS = {
    # exactly what production's restore-status.json held after the two failed attempts
    "production-failed-check": {"state": "failed", "stage": "failed", "mode": "check", "pid": 0,
                                "started_at": "2026-10-07T20:48:48+00:00",
                                "message": "The restore script stopped without a result. The Railway logs and restore.log have the details.",
                                "user": 5, "file_name": "library-20261007-204540.db",
                                "updated_at": "2026-10-07T20:48:49+00:00", "finished_at": "2026-10-07T20:48:49+00:00"},
    "failed-restore": {"state": "failed", "stage": "failed", "mode": "restore", "pid": 0, "message": "x"},
    "refused": {"state": "refused", "stage": "refused", "mode": "restore", "pid": 0, "message": "x"},
    "rolled-back": {"state": "rolled_back", "stage": "rolled_back", "mode": "restore", "pid": 0, "message": "x"},
}


@pytest.mark.parametrize("name", sorted(OLD_RECORDS))
@pytest.mark.parametrize("mode", ["check", "restore"])
def test_an_old_failed_or_refused_record_blocks_nothing(world, name, mode):
    w = world
    p = restore_status.paths(w["db"])["status"]
    open(p, "w").write(json.dumps(OLD_RECORDS[name]))
    assert w["app"]._restore_refusal({"id": "f-new", "name": "n", "size": 1}, mode) == ""
    assert _start(w, mode).status_code == 303
    _wait_done()
    assert restore_status.read_status(w["db"])["state"] == ("finished" if mode == "restore" else "checked")
    if mode == "restore":
        assert _titles(w["db"]) == ["snap-a", "snap-b", "snap-c"]


def test_two_real_processes_the_second_is_refused_and_the_first_record_is_untouched(world, monkeypatch):
    import subprocess
    w = world
    monkeypatch.setenv("FAKE_DRIVE_SLEEP", "6")
    a = subprocess.Popen(_script_cmd(w), cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        first = _wait_for(lambda: (lambda s: s if s.get("stage") == "downloading" else None)(
            restore_status.read_status(w["db"])))
        assert first["pid"] == a.pid
        # process B: a second real script, with no job id and with someone else's
        for extra in ([], ["--job-id", "someone-elses-job"]):
            b = subprocess.run(_script_cmd(w, *extra, mode="check"), cwd=str(ROOT), capture_output=True,
                               text=True, timeout=60)
            assert b.returncode != 0 and "already running" in (b.stdout + b.stderr)
            now = restore_status.read_status(w["db"])
            for k in ("pid", "job_id", "started_at", "mode", "state", "user"):
                assert now.get(k) == first.get(k), k
        # and the web route refuses too
        r = _start(w, "restore")
        assert r.status_code == 409 and "already running" in r.text
        assert restore_status.read_status(w["db"])["pid"] == a.pid
    finally:
        a.wait(timeout=60)
    assert restore_status.read_status(w["db"])["state"] == "finished"  # the first one was never disturbed
    assert _titles(w["db"]) == ["snap-a", "snap-b", "snap-c"]
