"""Phase O — off-site Drive backup automation repair.

Covers the three things that changed: (1) linklib.backup logs every attempt
(success or failure) to the new backup_log table instead of only print()ing,
(2) /admin/backup-now returns a real non-2xx status on failure so the weekly
GitHub Action can tell success from failure, and (3) /admin/library/backup's
status banner correctly distinguishes "not configured", "configured but the
last attempt failed", "configured but the folder ID is missing", and "on".

Doesn't hit the real Google Drive API — requests.post and linklib.backup's
own helpers are monkeypatched, same convention as test_email_notifications.py
stubbing out linklib.email_utils's send functions.
"""
import os
import pathlib
import sqlite3
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import backup
from linklib.db import Article, Library


@pytest.fixture
def lib():
    db = tempfile.mktemp(suffix=".db")
    library = Library(db)
    yield library
    library.close()
    if os.path.exists(db):
        os.remove(db)


@pytest.fixture
def configured_env(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("GOOGLE_OAUTH_REFRESH_TOKEN", "rtoken")


# --- Library.record_backup_attempt / list_backup_log ------------------------

def test_record_and_list_backup_log_round_trip(lib):
    lib.record_backup_attempt(status="success", filename="library-20260810-090000.db",
                               drive_file_id="abc123", size_bytes=4096, row_count=1500)
    lib.record_backup_attempt(status="failure", error="401 Unauthorized")

    rows = lib.list_backup_log()
    assert len(rows) == 2
    # most recent first
    assert rows[0]["status"] == "failure"
    assert rows[0]["error"] == "401 Unauthorized"
    assert rows[0]["drive_file_id"] == ""
    assert rows[1]["status"] == "success"
    assert rows[1]["filename"] == "library-20260810-090000.db"
    assert rows[1]["drive_file_id"] == "abc123"
    assert rows[1]["bytes"] == 4096
    assert rows[1]["row_count"] == 1500


def test_backup_log_table_exists_on_a_fresh_db(lib):
    # Library.__init__ runs _SCHEMA — this is the "did I remember to add the
    # CREATE TABLE" smoke test.
    lib.conn.execute("SELECT COUNT(*) FROM backup_log").fetchone()


# --- linklib.backup: configuration helpers -----------------------------------

def test_is_configured_requires_all_three_oauth_vars(monkeypatch):
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_REFRESH_TOKEN", raising=False)
    assert not backup.is_configured()


def test_is_configured_true_when_all_three_set(configured_env):
    assert backup.is_configured()


def test_folder_configured_independent_of_oauth(monkeypatch, configured_env):
    monkeypatch.delenv("GOOGLE_DRIVE_FOLDER_ID", raising=False)
    assert not backup.folder_configured()
    monkeypatch.setenv("GOOGLE_DRIVE_FOLDER_ID", "folder123")
    assert backup.folder_configured()


# --- linklib.backup.backup_now: logs to backup_log on both outcomes ---------

class _FakeResponse:
    def __init__(self, json_body, status=200):
        self._json = json_body
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json


def test_backup_now_logs_success_with_drive_file_id_and_row_count(lib, monkeypatch, configured_env):
    lib.close()  # not used directly — backup_now opens its own Library(db_path)

    # Build a tiny real DB with a known article count, since backup_now's
    # row_count comes from a real SELECT COUNT(*) against the snapshot.
    db = tempfile.mktemp(suffix=".db")
    real_lib = Library(db)
    real_lib.upsert(Article(url="https://example.com/a", title="A"))
    real_lib.upsert(Article(url="https://example.com/b", title="B"))
    real_lib.close()

    monkeypatch.setattr(backup, "_access_token", lambda: "faketoken")
    monkeypatch.setattr(backup.requests, "post",
                         lambda *a, **k: _FakeResponse({"id": "drivefile123", "name": "x"}))

    result = backup.backup_now(db)
    assert result["drive_file_id"] == "drivefile123"
    assert result["row_count"] == 2
    assert result["bytes"] > 0

    logged = Library(db)
    try:
        rows = logged.list_backup_log()
    finally:
        logged.close()
    assert len(rows) == 1
    assert rows[0]["status"] == "success"
    assert rows[0]["drive_file_id"] == "drivefile123"
    assert rows[0]["row_count"] == 2
    os.remove(db)


def test_backup_now_logs_failure_and_reraises(monkeypatch, configured_env):
    db = tempfile.mktemp(suffix=".db")
    Library(db).close()  # create the schema

    monkeypatch.setattr(backup, "_access_token", lambda: (_ for _ in ()).throw(RuntimeError("refresh token revoked")))

    with pytest.raises(RuntimeError, match="refresh token revoked"):
        backup.backup_now(db)

    logged = Library(db)
    try:
        rows = logged.list_backup_log()
    finally:
        logged.close()
    assert len(rows) == 1
    assert rows[0]["status"] == "failure"
    assert "refresh token revoked" in rows[0]["error"]
    os.remove(db)


def test_backup_now_raises_without_logging_when_not_configured(monkeypatch):
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_REFRESH_TOKEN", raising=False)
    db = tempfile.mktemp(suffix=".db")
    Library(db).close()

    with pytest.raises(RuntimeError, match="not configured"):
        backup.backup_now(db)

    # Not being configured isn't a real "attempt" — nothing should be logged.
    logged = Library(db)
    try:
        rows = logged.list_backup_log()
    finally:
        logged.close()
    assert rows == []
    os.remove(db)


# --- /admin/backup-now: real status codes ------------------------------------

@pytest.fixture
def admin_client(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_REFRESH_TOKEN", raising=False)
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    client = TestClient(appmod.app, raise_server_exceptions=True)
    client.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    yield client, appmod, db
    if os.path.exists(db):
        os.remove(db)
    for sidecar in ("-wal", "-shm"):
        if os.path.exists(db + sidecar):
            os.remove(db + sidecar)


def test_backup_now_route_returns_503_when_not_configured(admin_client):
    client, appmod, db = admin_client
    r = client.post("/admin/backup-now")
    assert r.status_code == 503
    assert "not configured" in r.text.lower()


def test_backup_now_route_returns_502_on_failure(admin_client, monkeypatch):
    client, appmod, db = admin_client
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("GOOGLE_OAUTH_REFRESH_TOKEN", "rtoken")
    monkeypatch.setattr(appmod.backup, "backup_now",
                         lambda db_path: (_ for _ in ()).throw(RuntimeError("upload failed")))
    r = client.post("/admin/backup-now")
    assert r.status_code == 502
    assert "backup failed" in r.text.lower()


def test_backup_now_route_returns_200_on_success(admin_client, monkeypatch):
    client, appmod, db = admin_client
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("GOOGLE_OAUTH_REFRESH_TOKEN", "rtoken")
    monkeypatch.setattr(appmod.backup, "backup_now",
                         lambda db_path: {"name": "library-x.db", "bytes": 123, "row_count": 5,
                                           "drive_file_id": "abc"})
    r = client.post("/admin/backup-now")
    assert r.status_code == 200
    assert "uploaded" in r.text.lower()


# --- /admin/library/backup: status banner states -----------------------------

def test_admin_backup_page_shows_red_banner_when_not_configured(admin_client):
    client, appmod, db = admin_client
    r = client.get("/admin/library/backup")
    assert r.status_code == 200
    assert "backups are <strong>off</strong>" in r.text.lower()


def test_admin_backup_page_shows_amber_banner_on_last_failure(admin_client, monkeypatch):
    client, appmod, db = admin_client
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("GOOGLE_OAUTH_REFRESH_TOKEN", "rtoken")
    monkeypatch.setenv("GOOGLE_DRIVE_FOLDER_ID", "folder123")
    lib = appmod._lib()
    lib.record_backup_attempt(status="failure", error="token expired")
    lib.close()
    r = client.get("/admin/library/backup")
    assert "failed" in r.text.lower()
    assert "token expired" in r.text


def test_admin_backup_page_shows_amber_banner_when_folder_id_missing(admin_client, monkeypatch):
    client, appmod, db = admin_client
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("GOOGLE_OAUTH_REFRESH_TOKEN", "rtoken")
    monkeypatch.delenv("GOOGLE_DRIVE_FOLDER_ID", raising=False)
    lib = appmod._lib()
    lib.record_backup_attempt(status="success", filename="library-x.db",
                               drive_file_id="abc", size_bytes=100, row_count=5)
    lib.close()
    r = client.get("/admin/library/backup")
    assert "google_drive_folder_id" in r.text.lower()
    assert "my drive root" in r.text.lower()


def test_admin_backup_page_shows_green_banner_when_healthy(admin_client, monkeypatch):
    client, appmod, db = admin_client
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("GOOGLE_OAUTH_REFRESH_TOKEN", "rtoken")
    monkeypatch.setenv("GOOGLE_DRIVE_FOLDER_ID", "folder123")
    lib = appmod._lib()
    lib.record_backup_attempt(status="success", filename="library-20260810-090000.db",
                               drive_file_id="abc123", size_bytes=4096, row_count=1500)
    lib.close()
    r = client.get("/admin/library/backup")
    assert "backups are <strong>on</strong>" in r.text.lower()
    assert "library-20260810-090000.db" in r.text
    assert 'href="https://drive.google.com/file/d/abc123/view"' in r.text


def test_admin_backup_page_history_table_shows_no_backups_yet(admin_client, monkeypatch):
    client, appmod, db = admin_client
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("GOOGLE_OAUTH_REFRESH_TOKEN", "rtoken")
    monkeypatch.setenv("GOOGLE_DRIVE_FOLDER_ID", "folder123")
    r = client.get("/admin/library/backup")
    assert "no off-site backups recorded yet" in r.text.lower()
