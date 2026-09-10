"""Durability audit item 2 (elevated) — a pre-backup PRAGMA integrity_check
(+ FTS5 self-check) against the live database, wired into backup_now()
before every snapshot.

Covers:
- linklib.backup.check_integrity(): ok on a healthy DB, failure (with
  detail) on real structural corruption and on a broken FTS5 index, and
  never raises even when the DB path itself is bad.
- Library.record_integrity_check/list_integrity_check_log round-trip.
- backup_now() logs every integrity attempt (ok or failure) to
  integrity_check_log, and a FAILED check BLOCKS the upload entirely — no
  Drive request is made, and a matching backup_log failure row is written
  too so the existing status banner picks it up.
- A healthy check doesn't change backup_now's existing success behavior.
- /admin/library-backup renders the new integrity banner.

Doesn't hit the real Google Drive API — same monkeypatch convention as
tests/test_backup.py.
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
def configured_env(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("GOOGLE_OAUTH_REFRESH_TOKEN", "rtoken")


class _FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json


# ---------------------------------------------------------------------------
# check_integrity
# ---------------------------------------------------------------------------

def test_check_integrity_ok_on_a_healthy_db():
    db = tempfile.mktemp(suffix=".db")
    Library(db).close()  # creates schema, including articles_fts
    try:
        result = backup.check_integrity(db)
        assert result == {"ok": True, "detail": "ok"}
    finally:
        os.remove(db)


def test_check_integrity_reports_structural_corruption():
    """A real corrupt SQLite file — PRAGMA integrity_check must catch it,
    and check_integrity must never raise, only report."""
    db = tempfile.mktemp(suffix=".db")
    Library(db).close()
    # Corrupt the file by truncating it mid-page — a reliable way to make
    # SQLite's own integrity_check report a real problem without needing to
    # hand-craft a byte-perfect corruption.
    size = os.path.getsize(db)
    with open(db, "r+b") as f:
        f.truncate(size // 2)
    try:
        result = backup.check_integrity(db)
        assert result["ok"] is False
        assert result["detail"] and result["detail"] != "ok"
    finally:
        os.remove(db)


def test_check_integrity_never_raises_on_a_missing_file():
    result = backup.check_integrity("/nonexistent/path/does-not-exist.db")
    assert result["ok"] is False
    assert result["detail"]


def test_check_integrity_catches_fts_drift(monkeypatch):
    """Simulate the FTS5 self-check failing even though the pragma passes,
    by wrapping the connection check_integrity() opens so the FTS
    integrity-check statement specifically raises."""
    db = tempfile.mktemp(suffix=".db")
    Library(db).close()

    real_connect = sqlite3.connect

    class _Wrapped:
        def __init__(self, conn):
            self._conn = conn

        def execute(self, sql, *a, **k):
            if "integrity-check" in sql and "articles_fts" in sql:
                raise sqlite3.DatabaseError("database disk image is malformed")
            return self._conn.execute(sql, *a, **k)

        def close(self):
            self._conn.close()

    monkeypatch.setattr(backup.sqlite3, "connect", lambda path: _Wrapped(real_connect(path)))
    try:
        result = backup.check_integrity(db)
        assert result["ok"] is False
        assert "FTS5 self-check failed" in result["detail"]
    finally:
        os.remove(db)


# ---------------------------------------------------------------------------
# Library.record_integrity_check / list_integrity_check_log
# ---------------------------------------------------------------------------

def test_record_and_list_integrity_check_log_round_trip():
    db = tempfile.mktemp(suffix=".db")
    lib = Library(db)
    try:
        lib.record_integrity_check(status="ok", detail="ok")
        lib.record_integrity_check(status="failure", detail="row 42 missing from index")
        rows = lib.list_integrity_check_log()
        assert len(rows) == 2
        assert rows[0]["status"] == "failure"  # most recent first
        assert rows[0]["detail"] == "row 42 missing from index"
        assert rows[1]["status"] == "ok"
    finally:
        lib.close()
        os.remove(db)


# ---------------------------------------------------------------------------
# backup_now(): integrity check wiring — blocks on failure
# ---------------------------------------------------------------------------

def test_backup_now_logs_ok_integrity_check_and_still_succeeds(monkeypatch, configured_env):
    db = tempfile.mktemp(suffix=".db")
    real_lib = Library(db)
    real_lib.upsert(Article(url="https://example.com/a", title="A"))
    real_lib.close()

    monkeypatch.setattr(backup, "_access_token", lambda: "faketoken")
    monkeypatch.setattr(backup.requests, "post",
                         lambda *a, **k: _FakeResponse({"id": "drivefile123", "name": "x"}))
    monkeypatch.setattr(backup.requests, "get", lambda *a, **k: _FakeResponse({"files": []}))

    result = backup.backup_now(db)
    assert result["drive_file_id"] == "drivefile123"

    lib = Library(db)
    try:
        integrity_rows = lib.list_integrity_check_log()
        backup_rows = lib.list_backup_log()
    finally:
        lib.close()
    assert len(integrity_rows) == 1
    assert integrity_rows[0]["status"] == "ok"
    assert len(backup_rows) == 1
    assert backup_rows[0]["status"] == "success"
    os.remove(db)


def test_backup_now_blocks_upload_when_integrity_check_fails(monkeypatch, configured_env):
    """The core behavior this item exists for: a failed integrity check
    must prevent any Drive request at all, not just get logged alongside a
    normal upload."""
    db = tempfile.mktemp(suffix=".db")
    Library(db).close()

    monkeypatch.setattr(backup, "check_integrity",
                         lambda path: {"ok": False, "detail": "row 7 missing"})

    def _fail_if_called(*a, **k):
        raise AssertionError("Drive upload must not be attempted when the integrity check fails")
    monkeypatch.setattr(backup, "_access_token", _fail_if_called)
    monkeypatch.setattr(backup.requests, "post", _fail_if_called)

    with pytest.raises(RuntimeError, match="integrity check failed"):
        backup.backup_now(db)

    lib = Library(db)
    try:
        integrity_rows = lib.list_integrity_check_log()
        backup_rows = lib.list_backup_log()
    finally:
        lib.close()
    assert len(integrity_rows) == 1
    assert integrity_rows[0]["status"] == "failure"
    assert integrity_rows[0]["detail"] == "row 7 missing"
    # A blocked backup is still a logged backup_log failure, so the
    # existing /admin/library-backup status banner surfaces it with no
    # second code path.
    assert len(backup_rows) == 1
    assert backup_rows[0]["status"] == "failure"
    assert "integrity check failed" in backup_rows[0]["error"]
    os.remove(db)


def test_backup_now_not_configured_never_runs_integrity_check(monkeypatch):
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_REFRESH_TOKEN", raising=False)
    db = tempfile.mktemp(suffix=".db")
    Library(db).close()

    def _fail_if_called(path):
        raise AssertionError("integrity check should not run when backups aren't configured")
    monkeypatch.setattr(backup, "check_integrity", _fail_if_called)

    with pytest.raises(RuntimeError, match="not configured"):
        backup.backup_now(db)
    os.remove(db)


# ---------------------------------------------------------------------------
# Admin page
# ---------------------------------------------------------------------------

@pytest.fixture
def admin_client(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    client = TestClient(appmod.app, raise_server_exceptions=True)
    client.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    yield client, appmod, db
    if os.path.exists(db):
        os.remove(db)


def test_admin_backup_page_shows_no_checks_yet_banner(admin_client):
    client, appmod, db = admin_client
    r = client.get("/admin/library-backup")
    assert r.status_code == 200
    assert "Pre-backup integrity check" in r.text
    assert "No integrity check has run yet" in r.text


def test_admin_backup_page_shows_ok_banner(admin_client):
    client, appmod, db = admin_client
    lib = appmod._lib()
    lib.record_integrity_check(status="ok", detail="ok")
    lib.close()

    r = client.get("/admin/library-backup")
    assert r.status_code == 200
    assert "ok</strong>&mdash;structural check and FTS5 self-check both passed" in r.text


def test_admin_backup_page_shows_failure_banner_loudly(admin_client):
    client, appmod, db = admin_client
    lib = appmod._lib()
    lib.record_integrity_check(status="failure", detail="row 42 missing from index")
    lib.close()

    r = client.get("/admin/library-backup")
    assert r.status_code == 200
    assert "failed</strong>" in r.text
    assert "row 42 missing from index" in r.text
    assert "var(--coral-wash)" in r.text
