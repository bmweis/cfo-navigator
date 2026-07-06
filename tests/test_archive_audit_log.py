"""Every admin add/edit/delete on the Archive (the `articles` table) must
leave an audit trail: who, what action, which item, when. Library-level
coverage for the archive_audit_log table itself; webapp-level coverage that
the actual admin routes call it (and that a non-admin can never trigger one)."""
import pathlib
import sys
import tempfile, os

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Article, Library


@pytest.fixture
def lib(tmp_path):
    db = Library(str(tmp_path / "test.db"))
    try:
        yield db
    finally:
        db.close()


def test_record_and_list_archive_audit(lib):
    admin_id = lib.create_user("brian", "supersecret", role="admin")
    lib.record_archive_audit(admin_id, "add", item_id=1, detail="https://ex.com/a")
    lib.record_archive_audit(admin_id, "edit", item_id=1, detail="tags=['saas']")
    lib.record_archive_audit(admin_id, "delete", item_id=1)

    rows = lib.list_archive_audit_log()
    assert len(rows) == 3
    assert [r["action"] for r in rows] == ["delete", "edit", "add"]   # newest first
    assert rows[0]["admin_username"] == "brian"


def test_bulk_operation_logs_a_summary_row_with_no_item_id(lib):
    admin_id = lib.create_user("brian", "supersecret", role="admin")
    lib.record_archive_audit(admin_id, "edit", item_id=None, detail="tag rename: old -> new (12 articles)")
    row = lib.list_archive_audit_log()[0]
    assert row["item_id"] is None
    assert "12 articles" in row["detail"]


# --- webapp-level: the actual admin routes write these rows -----------------

@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    from linklib.db import Library
    lib = Library(db)
    lib.create_user("admin", "adminpass", role="admin")   # matches the break-glass username, gives it a real users row
    lib.create_user("member1", "supersecret", role="user")
    art_id = lib.upsert(Article(url="https://ex.com/existing", title="Existing"))
    lib.close()
    yield appmod, db, art_id
    if os.path.exists(db):
        os.remove(db)


def _admin_client(appmod):
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app, raise_server_exceptions=True)
    c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    return c


def _member_client(appmod):
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app, raise_server_exceptions=True)
    c.post("/login", data={"username": "member1", "password": "supersecret"}, follow_redirects=False)
    return c


def _audit_rows(db):
    lib = Library(db)
    try:
        return lib.list_archive_audit_log()
    finally:
        lib.close()


def test_admin_tag_edit_is_audited(env):
    appmod, db, art_id = env
    c = _admin_client(appmod)
    r = c.post(f"/library/{art_id}/tags", json={"tags": ["saas", "arr"]})
    assert r.status_code == 200
    rows = _audit_rows(db)
    assert len(rows) == 1
    assert rows[0]["action"] == "edit"
    assert rows[0]["item_id"] == art_id
    assert rows[0]["admin_username"] == "admin"


def test_admin_delete_is_audited(env):
    appmod, db, art_id = env
    c = _admin_client(appmod)
    r = c.post(f"/library/{art_id}/delete", follow_redirects=False)
    assert r.status_code == 303
    rows = _audit_rows(db)
    assert len(rows) == 1
    assert rows[0]["action"] == "delete"
    assert rows[0]["item_id"] == art_id


def test_member_cannot_edit_or_trigger_an_audit_row(env):
    appmod, db, art_id = env
    c = _member_client(appmod)
    r = c.post(f"/library/{art_id}/tags", json={"tags": ["saas"]})
    assert r.status_code == 401
    r = c.post(f"/library/{art_id}/delete", follow_redirects=False)
    assert r.status_code == 401
    assert _audit_rows(db) == []
