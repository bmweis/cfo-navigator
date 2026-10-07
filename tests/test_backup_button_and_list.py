"""Archive backup page: the upload flow is gone, "Back up to Drive now" runs in
the background behind a lock, and the Drive list degrades to a plain message.
Drive itself is always mocked."""
import importlib
import os
import pathlib
import sys
import tempfile
import threading

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import backup
from linklib.db import Library

TOKEN = "cron-token"


@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SAVE_TOKEN", TOKEN)
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    for k in ("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "GOOGLE_OAUTH_REFRESH_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    import webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    admin = TestClient(appmod.app)
    admin.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    yield appmod, admin, TestClient(appmod.app), db
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(db + suffix):
            os.remove(db + suffix)


def _configure(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "cs")
    monkeypatch.setenv("GOOGLE_OAUTH_REFRESH_TOKEN", "rt")


# --- the upload flow is gone -------------------------------------------------

def test_page_has_no_upload_form_and_route_is_gone(env):
    appmod, admin, _, _ = env
    html = admin.get("/admin/library-backup").text
    assert "Upload replacement database" not in html
    assert 'type="file"' not in html
    assert "upload-db" not in html
    r = admin.post("/admin/library-backup/upload-db", files={"file": ("x.db", b"x")},
                   follow_redirects=False)
    assert r.status_code in (404, 405)


def test_page_shows_the_restore_commands(env):
    _, admin, _, _ = env
    html = admin.get("/admin/library-backup").text
    assert "python -m scripts.restore_from_drive --db /data/library.db --yes-replace-live" in html
    assert "pre-restore" in html
    assert "restart the service" in html


# --- the button --------------------------------------------------------------

def test_backup_run_needs_an_admin_session(env):
    appmod, admin, anon, _ = env
    assert anon.post("/admin/library-backup/run", follow_redirects=False).status_code == 401
    r = anon.post("/admin/library-backup/run", headers={"X-Save-Token": TOKEN}, follow_redirects=False)
    assert r.status_code == 401
    r = anon.post("/admin/library-backup/run", params={"token": TOKEN}, follow_redirects=False)
    assert r.status_code == 401


def test_backup_run_starts_a_background_backup_and_redirects(env, monkeypatch):
    appmod, admin, _, _ = env
    started = threading.Event()
    monkeypatch.setattr(appmod.backup, "backup_now", lambda p: started.set() or {})
    r = admin.post("/admin/library-backup/run", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/admin/library-backup?started=1"
    assert started.wait(5)


def test_second_click_is_refused_while_a_backup_runs(env):
    appmod, admin, _, _ = env
    assert backup._BACKUP_LOCK.acquire(blocking=False)
    try:
        r = admin.post("/admin/library-backup/run", follow_redirects=False)
        assert r.headers["location"] == "/admin/library-backup?busy=1"
        page = admin.get("/admin/library-backup?busy=1").text
        assert "A backup is already running." in page
        assert "Backup running" in page
    finally:
        backup._BACKUP_LOCK.release()


def test_lock_refuses_overlap_and_logs_nothing(env, monkeypatch):
    _, _, _, db = env
    _configure(monkeypatch)
    Library(db).close()
    assert backup._BACKUP_LOCK.acquire(blocking=False)
    try:
        with pytest.raises(backup.BackupBusy):
            backup.backup_now(db)
    finally:
        backup._BACKUP_LOCK.release()
    lib = Library(db)
    try:
        assert lib.list_backup_log() == []
    finally:
        lib.close()
    assert not backup.backup_is_running()


def test_cron_path_returns_200_when_a_backup_is_running(env):
    appmod, _, anon, _ = env
    assert backup._BACKUP_LOCK.acquire(blocking=False)
    try:
        r = anon.post("/admin/backup-now", headers={"X-Save-Token": TOKEN})
    finally:
        backup._BACKUP_LOCK.release()
    assert r.status_code == 200
    assert "already running" in r.text


def test_failed_backup_reaches_the_log_and_the_page_in_plain_words(env, monkeypatch):
    appmod, admin, _, db = env
    _configure(monkeypatch)
    monkeypatch.setattr(backup, "_access_token",
                        lambda: (_ for _ in ()).throw(RuntimeError("401 Client Error: Unauthorized for url: https://x")))
    with pytest.raises(RuntimeError):
        backup.backup_now(db)
    lib = Library(db)
    try:
        rows = lib.list_backup_log()
    finally:
        lib.close()
    assert rows[0]["status"] == "failure"
    assert "401" in rows[0]["error"]          # raw text stays in the log
    page = admin.get("/admin/library-backup").text
    assert "Google refused the sign-in" in page
    assert "Unauthorized for url" not in page


def test_backup_streams_the_file_and_names_it_like_the_nightly_ones(env, monkeypatch):
    _, _, _, db = env
    _configure(monkeypatch)
    lib = Library(db)
    lib.close()
    seen = {}

    class Resp:
        def raise_for_status(self): pass
        def json(self): return {"id": "fid", "name": "n"}

    def fake_post(url, headers=None, data=None, timeout=None, **kw):
        seen["is_bytes"] = isinstance(data, (bytes, bytearray))
        seen["len"] = len(data)
        seen["body"] = b"".join(data)
        return Resp()

    monkeypatch.setattr(backup, "_access_token", lambda: "t")
    monkeypatch.setattr(backup, "_resolve_folder_id", lambda *a: "folder")
    monkeypatch.setattr(backup.requests, "post", fake_post)
    monkeypatch.setattr(backup, "prune_old_backups", lambda p: {"deleted": 0, "kept": 0, "error": ""})
    result = backup.backup_now(db)
    assert not seen["is_bytes"]                      # a stream, not one big bytes object
    assert seen["len"] == len(seen["body"])          # Content-Length matches what is sent
    assert seen["body"].count(b"SQLite format 3") == 1
    import re
    assert re.fullmatch(r"library-\d{8}-\d{6}\.db", result["name"])
    assert result["bytes"] > 0


# --- the Drive list ----------------------------------------------------------

def test_drive_list_needs_an_admin_session(env):
    _, _, anon, _ = env
    assert anon.get("/admin/library-backup/drive-list").status_code == 401
    assert anon.get("/admin/library-backup/drive-list", headers={"X-Save-Token": TOKEN}).status_code == 401


def test_drive_list_shows_a_button_made_file(env, monkeypatch):
    appmod, admin, _, db = env
    _configure(monkeypatch)
    monkeypatch.setattr(backup, "known_folder_id", lambda p: "folder")

    class TokenResp:
        def raise_for_status(self): pass
        def json(self): return {"access_token": "t"}
    monkeypatch.setattr(backup.requests, "post", lambda *a, **k: TokenResp())
    monkeypatch.setattr(backup, "list_snapshots", lambda t, f, timeout=30: [
        {"id": "1", "name": "library-20261007-101500.db", "size": "266000000",
         "createdTime": "2026-10-07T10:15:00.000Z"}])
    d = admin.get("/admin/library-backup/drive-list").json()
    assert d["ok"] and d["files"][0]["name"] == "library-20261007-101500.db"
    assert d["files"][0]["size"] == 266000000


def test_drive_list_empty_when_no_folder_yet(env, monkeypatch):
    _, admin, _, _ = env
    _configure(monkeypatch)
    d = admin.get("/admin/library-backup/drive-list").json()
    assert d == {"ok": True, "files": [], "message": ""}


@pytest.mark.parametrize("exc", [TimeoutError("timed out"), RuntimeError("boom 500 secret detail")])
def test_drive_list_degrades_to_a_plain_message(env, monkeypatch, exc):
    _, admin, _, _ = env
    _configure(monkeypatch)
    monkeypatch.setattr(backup, "known_folder_id", lambda p: "folder")
    monkeypatch.setattr(backup.requests, "post", lambda *a, **k: (_ for _ in ()).throw(exc))
    r = admin.get("/admin/library-backup/drive-list")
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is False
    assert "Could not reach Google Drive" in d["message"]
    assert "secret detail" not in d["message"]


def test_drive_list_when_not_configured_is_plain(env):
    _, admin, _, _ = env
    d = admin.get("/admin/library-backup/drive-list").json()
    assert d["ok"] is False and "not set up" in d["message"]


def test_json_route_is_not_a_hub_nav_orphan(env):
    appmod, _, _, _ = env
    assert "/admin/library-backup/drive-list" not in appmod.hub_nav_orphans()


# --- the streamed upload (nightly cron shares this path) ----------------------

def _fake_drive(monkeypatch, post):
    monkeypatch.setattr(backup, "_access_token", lambda: "t")
    monkeypatch.setattr(backup, "_resolve_folder_id", lambda *a: "folder")
    monkeypatch.setattr(backup.requests, "post", post)
    monkeypatch.setattr(backup, "prune_old_backups", lambda p: {"deleted": 0, "kept": 0, "error": ""})


def test_streamed_body_has_a_content_length_equal_to_its_real_size_and_reads_in_chunks(tmp_path):
    import requests
    f = tmp_path / "snap.db"
    f.write_bytes(os.urandom(3 * (1 << 20) + 123))
    head, tail = b"HEAD\r\n", b"\r\nTAIL"
    body = backup._MultipartBody(head, str(f), tail, chunk=1 << 20)
    parts = list(body)                                   # what requests/http.client would send
    assert parts[0] == head and parts[-1] == tail
    assert len(parts) == 1 + 4 + 1                       # 3 full chunks + remainder, not one read
    assert max(len(p) for p in parts[1:-1]) <= 1 << 20
    real_size = sum(len(p) for p in parts)
    assert real_size == len(head) + f.stat().st_size + len(tail)
    prepared = requests.Request("POST", "https://example.invalid/upload", data=body,
                                headers={"Content-Type": "multipart/related; boundary=b"}).prepare()
    assert prepared.headers["Content-Length"] == str(real_size)
    assert "Transfer-Encoding" not in prepared.headers   # not chunked-encoded


def test_failure_inside_the_streamed_upload_is_logged_and_raised(env, monkeypatch):
    _, _, _, db = env
    _configure(monkeypatch)
    Library(db).close()

    def failing_post(url, headers=None, data=None, timeout=None, **kw):
        next(iter(data))                                  # start streaming, then the connection dies
        raise RuntimeError("connection reset while streaming")

    _fake_drive(monkeypatch, failing_post)
    with pytest.raises(RuntimeError, match="connection reset"):
        backup.backup_now(db)
    lib = Library(db)
    try:
        rows = lib.list_backup_log()
    finally:
        lib.close()
    assert rows[0]["status"] == "failure" and "connection reset" in rows[0]["error"]
    assert not backup.backup_is_running()                 # lock released after a failure


def test_cron_path_success_response_shape_through_the_real_backup_now(env, monkeypatch):
    """Same status code and message shape the cron has always received, with
    only Drive mocked (this test also passes against the pre-streaming code)."""
    appmod, _, anon, db = env
    _configure(monkeypatch)
    Library(db).close()

    class Resp:
        def raise_for_status(self): pass
        def json(self): return {"id": "fid", "name": "n"}

    def post(url, headers=None, data=None, timeout=None, **kw):
        if isinstance(data, (bytes, bytearray)):
            pass
        else:
            b"".join(data)
        return Resp()

    _fake_drive(monkeypatch, post)
    r = anon.post("/admin/backup-now", headers={"X-Save-Token": TOKEN})
    assert r.status_code == 200
    import re
    assert re.search(r"Uploaded <strong>library-\d{8}-\d{6}\.db</strong> \([\d,]+ bytes, [\d,]+ articles\) to Google Drive\.", r.text)
