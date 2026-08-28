"""/read-later-bookmarklet + POST /save-later — the Read Later counterpart
to /bookmarklet + POST /save.

Covers:
- /read-later-bookmarklet renders a syntactically valid `javascript:` snippet
  (node --check against the real resolved response text, same convention as
  test_bookmarklet.py), with balanced braces/parens as a Node-independent
  belt-and-suspenders check, and a `.catch` on the fetch chain.
- /read-later-bookmarklet is login-gated (401 unauthenticated), same as
  /bookmarklet.
- POST /save-later accepts the same token auth as /save (X-Save-Token or
  ?token=) with no login required, and writes into the per-user `read_later`
  list — attributed to the earliest admin account
  (Library.default_admin_user_id), since a token-only request has no session
  to resolve a user_id from.
- POST /save-later never touches the Archive (`articles` table) — no
  ingest_url, no enrichment.
- The `_save_cors` middleware now also covers /save-later (preflight OPTIONS
  + Access-Control-Allow-Origin on the real response, error responses
  included), and still leaves every other route alone.
"""
import os
import shutil
import tempfile

import pytest

os.environ.setdefault("LINKLIB_DB", tempfile.mktemp(suffix=".db"))

from webapp import checks


@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    monkeypatch.setenv("LINKLIB_SAVE_TOKEN", "tok123")
    import importlib
    import webapp.app as appmod
    importlib.reload(appmod)
    yield appmod
    if os.path.exists(db):
        os.remove(db)


def _admin_client(appmod):
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app, raise_server_exceptions=True)
    c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    return c


def _bookmarklet_js(appmod) -> str:
    c = _admin_client(appmod)
    r = c.get("/read-later-bookmarklet")
    assert r.status_code == 200
    assert r.text.startswith("javascript:")
    return r.text[len("javascript:"):]


def _assert_balanced(text: str) -> None:
    assert text.count("{") == text.count("}"), \
        f"unbalanced braces: {text.count('{')} open vs {text.count('}')} close"
    assert text.count("(") == text.count(")"), \
        f"unbalanced parens: {text.count('(')} open vs {text.count(')')} close"


def test_read_later_bookmarklet_requires_login(env):
    from fastapi.testclient import TestClient
    c = TestClient(env.app, raise_server_exceptions=True)
    r = c.get("/read-later-bookmarklet")
    assert r.status_code == 401


def test_read_later_bookmarklet_js_is_brace_and_paren_balanced(env):
    _assert_balanced(_bookmarklet_js(env))


def test_read_later_bookmarklet_has_catch_on_the_fetch_chain(env):
    js = _bookmarklet_js(env)
    assert ".catch(" in js


def test_read_later_bookmarklet_posts_to_save_later(env):
    js = _bookmarklet_js(env)
    assert "/save-later" in js
    assert "title" in js  # document.title rides along


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH in this environment")
def test_read_later_bookmarklet_js_parses_as_valid_javascript_via_node(env):
    js = _bookmarklet_js(env)
    problem = checks._node_check(js)
    assert problem is None, problem


# ---------------------------------------------------------------------------
# POST /save-later — token auth, user resolution, no Archive write
# ---------------------------------------------------------------------------

def _seed_admin_user(appmod) -> int:
    lib = appmod._lib()
    try:
        lib.conn.execute(
            "INSERT INTO users (username, password_hash, role, active, name, email, created_at) "
            "VALUES ('brian','x','admin',1,'Brian','b@example.com','2026-01-01')"
        )
        lib.conn.commit()
        return lib.default_admin_user_id()
    finally:
        lib.close()


def test_save_later_requires_valid_token(env):
    from fastapi.testclient import TestClient
    c = TestClient(env.app, raise_server_exceptions=True)
    r = c.post("/save-later", json={"url": "https://example.com/x"})
    assert r.status_code == 401


def test_save_later_writes_into_read_later_not_archive(env):
    from fastapi.testclient import TestClient
    uid = _seed_admin_user(env)
    c = TestClient(env.app, raise_server_exceptions=True)
    r = c.post("/save-later?token=tok123",
               json={"url": "https://example.com/some-article", "title": "Some Article"})
    assert r.status_code == 200
    assert r.json()["ok"] is True

    lib = env._lib()
    try:
        rl = lib.list_read_later(uid)
        assert len(rl) == 1
        assert rl[0]["url"] == "https://example.com/some-article"
        assert rl[0]["title"] == "Some Article"
        # Never lands in the Archive.
        assert lib.count() == 0
    finally:
        lib.close()


def test_save_later_attributes_to_earliest_admin_user(env):
    from fastapi.testclient import TestClient
    lib = env._lib()
    lib.conn.execute(
        "INSERT INTO users (username, password_hash, role, active, name, email, created_at) "
        "VALUES ('first_admin','x','admin',1,'First','f@example.com','2026-01-01')"
    )
    lib.conn.execute(
        "INSERT INTO users (username, password_hash, role, active, name, email, created_at) "
        "VALUES ('second_admin','x','admin',1,'Second','s@example.com','2026-02-01')"
    )
    lib.conn.commit()
    first_id = lib.get_user("first_admin")["id"]
    lib.close()

    c = TestClient(env.app, raise_server_exceptions=True)
    r = c.post("/save-later?token=tok123", json={"url": "https://example.com/y"})
    assert r.status_code == 200

    lib = env._lib()
    try:
        rl = lib.list_read_later(first_id)
        assert len(rl) == 1
    finally:
        lib.close()


def test_save_later_requires_url(env):
    from fastapi.testclient import TestClient
    _seed_admin_user(env)
    c = TestClient(env.app, raise_server_exceptions=True)
    r = c.post("/save-later?token=tok123", json={})
    assert r.status_code == 400


def test_save_later_with_no_admin_account_returns_503(env):
    from fastapi.testclient import TestClient
    c = TestClient(env.app, raise_server_exceptions=True)
    r = c.post("/save-later?token=tok123", json={"url": "https://example.com/z"})
    assert r.status_code == 503


# ---------------------------------------------------------------------------
# _save_cors now also covers /save-later
# ---------------------------------------------------------------------------

def test_save_later_answers_options_preflight_with_cors_headers(env):
    from fastapi.testclient import TestClient
    c = TestClient(env.app, raise_server_exceptions=True)
    r = c.options("/save-later", headers={
        "Origin": "https://bolster.com",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    })
    assert r.status_code == 204
    assert r.headers.get("access-control-allow-origin") == "*"


def test_save_later_post_response_carries_cors_header(env):
    from fastapi.testclient import TestClient
    _seed_admin_user(env)
    c = TestClient(env.app, raise_server_exceptions=True)
    r = c.post("/save-later?token=tok123", json={"url": "https://example.com/some-article"},
              headers={"Origin": "https://bolster.com"})
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "*"


def test_save_later_error_response_also_carries_cors_header(env):
    from fastapi.testclient import TestClient
    c = TestClient(env.app, raise_server_exceptions=True)
    r = c.post("/save-later", json={"url": "https://example.com/x"},
              headers={"Origin": "https://bolster.com"})
    assert r.status_code == 401
    assert r.headers.get("access-control-allow-origin") == "*"
