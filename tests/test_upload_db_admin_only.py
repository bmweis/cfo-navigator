"""POST /admin/library-backup/upload-db replaces the live library.db wholesale,
and its only validation is that an `articles` table exists. So it must accept a
real admin session only: not the shared bookmarklet token, not a member session.
A refused caller gets the same 401 an unauthenticated request gets."""
import importlib
import os
import pathlib
import shutil
import sqlite3
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

REPO_OPML = str(pathlib.Path(__file__).resolve().parents[1] / "preferred_sites.opml")
TOKEN = "bookmarklet-token"


@pytest.fixture
def env(monkeypatch, tmp_path):
    opml = tmp_path / "sites.opml"
    shutil.copy(REPO_OPML, opml)
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_SITES_OPML", str(opml))
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SAVE_TOKEN", TOKEN)
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import webapp.app as appmod
    importlib.reload(appmod)
    from linklib.db import Library
    lib = Library(db)
    lib.create_user("jane", "supersecretpw", role="user")
    lib.create_user("boss", "supersecretpw", role="admin")
    yield appmod, db
    if os.path.exists(db):
        os.remove(db)


def _valid_db_bytes(tmp_path) -> bytes:
    p = tmp_path / "good.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE articles (id INTEGER PRIMARY KEY, url TEXT)")
    c.execute("INSERT INTO articles (url) VALUES ('https://x.test/a')")
    c.commit()
    c.close()
    return p.read_bytes()


def _client(appmod, username=None):
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app)
    if username:
        c.post("/login", data={"username": username, "password": "supersecretpw"},
               follow_redirects=False)
    return c


def _post(client, data, **kw):
    return client.post("/admin/library-backup/upload-db", files={"file": ("x.db", data)},
                       follow_redirects=False, **kw)


def _live_articles(db) -> int | None:
    try:
        c = sqlite3.connect(db)
        n = c.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        c.close()
        return n
    except Exception:
        return None


def test_token_header_is_refused_and_db_untouched(env, tmp_path):
    appmod, db = env
    before = open(db, "rb").read()
    r = _post(_client(appmod), _valid_db_bytes(tmp_path), headers={"X-Save-Token": TOKEN})
    assert r.status_code == 401
    assert open(db, "rb").read() == before


def test_token_query_param_is_refused(env, tmp_path):
    appmod, _ = env
    r = _post(_client(appmod), _valid_db_bytes(tmp_path), params={"token": TOKEN})
    assert r.status_code == 401


def test_member_session_is_refused(env, tmp_path):
    appmod, db = env
    before = open(db, "rb").read()
    r = _post(_client(appmod, "jane"), _valid_db_bytes(tmp_path))
    assert r.status_code == 401
    assert open(db, "rb").read() == before


def test_unauthenticated_is_refused(env, tmp_path):
    appmod, _ = env
    assert _post(_client(appmod), _valid_db_bytes(tmp_path)).status_code == 401


def test_admin_session_still_replaces_with_valid_db(env, tmp_path):
    appmod, db = env
    r = _post(_client(appmod, "boss"), _valid_db_bytes(tmp_path))
    assert r.status_code == 303
    assert r.headers["location"] == "/admin/library-backup?uploaded=1"
    assert _live_articles(db) == 1


def test_admin_session_invalid_file_still_rejected_and_nothing_replaced(env):
    appmod, db = env
    before = open(db, "rb").read()
    r = _post(_client(appmod, "boss"), b"this is not a database")
    assert r.status_code == 400
    assert open(db, "rb").read() == before


def test_backup_now_still_accepts_the_token(env):
    """The daily Railway Cron Service calls /admin/backup-now with the token;
    this PR must not change that route."""
    appmod, _ = env
    r = _client(appmod).post("/admin/backup-now", headers={"X-Save-Token": TOKEN},
                             follow_redirects=False)
    assert r.status_code != 401


def test_upload_page_states_the_100_mb_limit(env):
    appmod, _ = env
    html = _client(appmod, "boss").get("/admin/library-backup").text
    assert "100 MB" in html
