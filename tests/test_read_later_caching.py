"""Read Later content caching + manual per-item refresh (2026-08 Reader
cleanliness pass, PR 3 of the backlog investigation).

Covers:
- Library.add_read_later/get_read_later_by_url/update_read_later_content:
  write-once-on-empty guards (a failed fetch never blanks a good cache).
- POST /save-later now fetches and caches content_html at save time
  (through the same shared strip_promotional_chrome-equipped extraction
  path PR 1 fixed), best-effort — a fetch failure never blocks the save.
- POST /read-later/refresh: session-gated (not token), replaces cached
  content on success, never destructive on failure, 404 for a URL not in
  the user's Read Later list.
- _resolve_reader_content's new Read Later cache tier and `is_read_later`
  flag, reached via GET /api/read-article.
- The reader toolbar's Refresh button is present in the rendered page and
  conditional on `d.is_read_later`.
"""
import os
import tempfile

import pytest

from linklib.db import Library
from linklib.extract import PageData


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


def _seed_admin_user(appmod) -> int:
    """A real `users` row, username 'admin' — matching ADMIN_USERNAME so the
    break-glass session _admin_client() logs into above resolves to a real
    user_id via _current_user_id (needed for /read-later/refresh and
    /api/read-article's session-scoped Read Later lookup), not just
    default_admin_user_id()'s token-only fallback (which any admin row, any
    username, already satisfies)."""
    lib = appmod._lib()
    try:
        lib.conn.execute(
            "INSERT INTO users (username, password_hash, role, active, name, email, created_at) "
            "VALUES ('admin','x','admin',1,'Admin','a@example.com','2026-01-01')"
        )
        lib.conn.commit()
        return lib.default_admin_user_id()
    finally:
        lib.close()


_SPONSOR_HTML = (
    "<html><body><article>"
    "<p>Real paragraph one about SPACs.</p>"
    '<div class="sponsor-block"><p>This post is brought to you by Brex.</p></div>'
    "<p>Real paragraph two continuing the analysis.</p>"
    "</article></body></html>"
)


# ---------------------------------------------------------------------------
# Library layer
# ---------------------------------------------------------------------------

def test_add_read_later_stores_content(env):
    lib = Library(os.environ["LINKLIB_DB"])
    lib.add_read_later(user_id=1, url="https://example.com/a", title="A",
                       content="plain text", content_html="<p>html</p>")
    row = lib.get_read_later_by_url(1, "https://example.com/a")
    assert row["content"] == "plain text"
    assert row["content_html"] == "<p>html</p>"
    lib.close()


def test_resave_with_empty_content_does_not_blank_existing_cache(env):
    lib = Library(os.environ["LINKLIB_DB"])
    lib.add_read_later(user_id=1, url="https://example.com/a", title="A",
                       content="good text", content_html="<p>good</p>")
    # A re-save whose own fetch failed (empty content/content_html) must not
    # destroy the previously-cached good copy.
    lib.add_read_later(user_id=1, url="https://example.com/a", title="A (retitled)")
    row = lib.get_read_later_by_url(1, "https://example.com/a")
    assert row["content_html"] == "<p>good</p>"
    assert row["title"] == "A (retitled)"
    lib.close()


def test_resave_with_fresh_content_replaces_stale_cache(env):
    lib = Library(os.environ["LINKLIB_DB"])
    lib.add_read_later(user_id=1, url="https://example.com/a", content_html="<p>old</p>")
    lib.add_read_later(user_id=1, url="https://example.com/a", content_html="<p>new</p>")
    row = lib.get_read_later_by_url(1, "https://example.com/a")
    assert row["content_html"] == "<p>new</p>"
    lib.close()


def test_update_read_later_content_is_non_destructive_on_empty(env):
    lib = Library(os.environ["LINKLIB_DB"])
    lib.add_read_later(user_id=1, url="https://example.com/a", content_html="<p>good</p>")
    ok = lib.update_read_later_content(1, "https://example.com/a", "", "")
    assert ok is False
    row = lib.get_read_later_by_url(1, "https://example.com/a")
    assert row["content_html"] == "<p>good</p>"
    lib.close()


def test_update_read_later_content_replaces_on_success(env):
    lib = Library(os.environ["LINKLIB_DB"])
    lib.add_read_later(user_id=1, url="https://example.com/a", content_html="<p>old</p>")
    ok = lib.update_read_later_content(1, "https://example.com/a", "new text", "<p>new</p>")
    assert ok is True
    row = lib.get_read_later_by_url(1, "https://example.com/a")
    assert row["content_html"] == "<p>new</p>"
    lib.close()


def test_get_read_later_by_url_returns_none_when_absent(env):
    lib = Library(os.environ["LINKLIB_DB"])
    assert lib.get_read_later_by_url(1, "https://example.com/nope") is None
    lib.close()


# ---------------------------------------------------------------------------
# POST /save-later — fetch + cache at save time
# ---------------------------------------------------------------------------

def test_save_later_fetches_and_caches_scrubbed_content(env, monkeypatch):
    uid = _seed_admin_user(env)

    def fake_fetch_page(url, timeout=20):
        return PageData(title="A SPAC Post", content="Real paragraph one. Real paragraph two.",
                        raw_html=_SPONSOR_HTML)
    monkeypatch.setattr("linklib.extract.fetch_page", fake_fetch_page)

    from fastapi.testclient import TestClient
    c = TestClient(env.app, raise_server_exceptions=True)
    r = c.post("/save-later?token=tok123", json={"url": "https://example.com/spac"})
    assert r.status_code == 200
    assert r.json()["ok"] is True

    lib = Library(os.environ["LINKLIB_DB"])
    row = lib.get_read_later_by_url(uid, "https://example.com/spac")
    lib.close()
    assert row is not None
    assert "Brex" not in row["content_html"]
    assert "Real paragraph one" in row["content_html"]
    # Title wasn't supplied by the caller — filled in from the fetched page.
    assert row["title"] == "A SPAC Post"


def test_save_later_prefers_caller_supplied_title_over_fetched(env, monkeypatch):
    uid = _seed_admin_user(env)

    def fake_fetch_page(url, timeout=20):
        return PageData(title="Fetched Title", content="body",
                        raw_html="<html><body><article><p>x</p></article></body></html>")
    monkeypatch.setattr("linklib.extract.fetch_page", fake_fetch_page)

    from fastapi.testclient import TestClient
    c = TestClient(env.app, raise_server_exceptions=True)
    r = c.post("/save-later?token=tok123",
              json={"url": "https://example.com/x", "title": "Caller Title"})
    assert r.status_code == 200
    lib = Library(os.environ["LINKLIB_DB"])
    row = lib.get_read_later_by_url(uid, "https://example.com/x")
    lib.close()
    assert row["title"] == "Caller Title"


def test_save_later_never_blocks_on_fetch_failure(env, monkeypatch):
    uid = _seed_admin_user(env)

    def fake_fetch_page(url, timeout=20):
        raise ConnectionError("no network")
    monkeypatch.setattr("linklib.extract.fetch_page", fake_fetch_page)

    from fastapi.testclient import TestClient
    c = TestClient(env.app, raise_server_exceptions=True)
    r = c.post("/save-later?token=tok123", json={"url": "https://example.com/dead", "title": "T"})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    lib = Library(os.environ["LINKLIB_DB"])
    row = lib.get_read_later_by_url(uid, "https://example.com/dead")
    lib.close()
    assert row is not None
    assert row["content_html"] == ""
    assert row["title"] == "T"


# ---------------------------------------------------------------------------
# POST /read-later/refresh
# ---------------------------------------------------------------------------

def test_refresh_requires_session_not_token(env):
    _seed_admin_user(env)
    from fastapi.testclient import TestClient
    anon = TestClient(env.app, raise_server_exceptions=True)
    r = anon.post("/read-later/refresh", json={"url": "https://example.com/a"})
    assert r.status_code == 401
    # A valid save token alone (no session) must NOT satisfy this route —
    # it's the signed-in Reader UI's own action, not the bookmarklet's.
    r2 = anon.post("/read-later/refresh?token=tok123", json={"url": "https://example.com/a"})
    assert r2.status_code == 401


def test_refresh_replaces_cached_content(env, monkeypatch):
    uid = _seed_admin_user(env)
    lib = Library(os.environ["LINKLIB_DB"])
    lib.add_read_later(user_id=uid, url="https://example.com/a", title="A", content_html="<p>old</p>")
    lib.close()

    def fake_fetch_page(url, timeout=20):
        return PageData(title="A", content="fresh body",
                        raw_html="<html><body><article><p>Fresh body text.</p></article></body></html>")
    monkeypatch.setattr("linklib.extract.fetch_page", fake_fetch_page)

    c = _admin_client(env)
    r = c.post("/read-later/refresh", json={"url": "https://example.com/a"})
    assert r.status_code == 200
    assert r.json()["ok"] is True

    lib = Library(os.environ["LINKLIB_DB"])
    row = lib.get_read_later_by_url(uid, "https://example.com/a")
    lib.close()
    assert "Fresh body text" in row["content_html"]


def test_refresh_failure_leaves_existing_cache_untouched(env, monkeypatch):
    uid = _seed_admin_user(env)
    lib = Library(os.environ["LINKLIB_DB"])
    lib.add_read_later(user_id=uid, url="https://example.com/a", title="A", content_html="<p>good</p>")
    lib.close()

    def fake_fetch_page(url, timeout=20):
        raise ConnectionError("down")
    monkeypatch.setattr("linklib.extract.fetch_page", fake_fetch_page)

    c = _admin_client(env)
    r = c.post("/read-later/refresh", json={"url": "https://example.com/a"})
    assert r.status_code == 200
    assert r.json()["ok"] is False

    lib = Library(os.environ["LINKLIB_DB"])
    row = lib.get_read_later_by_url(uid, "https://example.com/a")
    lib.close()
    assert row["content_html"] == "<p>good</p>"


def test_refresh_404_when_not_in_read_later(env):
    _seed_admin_user(env)
    c = _admin_client(env)
    r = c.post("/read-later/refresh", json={"url": "https://example.com/never-saved"})
    assert r.status_code == 404


def test_refresh_requires_url(env):
    _seed_admin_user(env)
    c = _admin_client(env)
    r = c.post("/read-later/refresh", json={})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# _resolve_reader_content's Read Later cache tier + is_read_later flag
# ---------------------------------------------------------------------------

def test_api_read_article_uses_read_later_cache_without_live_fetch(env, monkeypatch):
    uid = _seed_admin_user(env)
    lib = Library(os.environ["LINKLIB_DB"])
    lib.add_read_later(user_id=uid, url="https://example.com/a", title="Cached Piece",
                       content_html="<p>Cached body text here.</p>")
    lib.close()

    def boom(*a, **k):
        raise AssertionError("should not live-fetch when a Read Later cache exists")
    monkeypatch.setattr("linklib.extract.fetch_page", boom)

    c = _admin_client(env)
    r = c.get("/api/read-article", params={"url": "https://example.com/a"})
    assert r.status_code == 200
    data = r.json()
    assert "Cached body text here" in data["body_html"]
    assert data["is_read_later"] is True
    assert data["has_content"] is True


def test_api_read_article_is_read_later_false_for_plain_feed_url(env, monkeypatch):
    _seed_admin_user(env)

    def fake_fetch_page(url, timeout=20):
        return PageData(title="T", content="body",
                        raw_html="<html><body><article><p>Some text.</p></article></body></html>")
    monkeypatch.setattr("linklib.extract.fetch_page", fake_fetch_page)

    c = _admin_client(env)
    r = c.get("/api/read-article", params={"url": "https://example.com/never-saved-anywhere"})
    assert r.status_code == 200
    assert r.json()["is_read_later"] is False


def test_api_read_article_prefers_archive_over_read_later_cache(env, monkeypatch):
    """An Archive article's own content is always the strict upgrade over a
    Read Later cache for the same URL — is_read_later still reports False in
    that case since the Refresh button wouldn't change what's shown."""
    uid = _seed_admin_user(env)
    lib = Library(os.environ["LINKLIB_DB"])
    from linklib.db import Article
    lib.upsert(Article(url="https://example.com/both", title="Archived Title",
                       saved_at="2026-01-01T00:00:00"))
    lib.set_article_content_html(
        lib.conn.execute("SELECT id FROM articles WHERE url=?", ("https://example.com/both",)).fetchone()[0],
        "<p>Archive content wins.</p>")
    lib.add_read_later(user_id=uid, url="https://example.com/both", title="RL Title",
                       content_html="<p>Read Later content should not show.</p>")
    lib.close()

    def boom(*a, **k):
        raise AssertionError("should not live-fetch when an Archive article exists")
    monkeypatch.setattr("linklib.extract.fetch_page", boom)

    c = _admin_client(env)
    r = c.get("/api/read-article", params={"url": "https://example.com/both"})
    assert r.status_code == 200
    data = r.json()
    assert "Archive content wins" in data["body_html"]
    assert data["is_read_later"] is False


# ---------------------------------------------------------------------------
# Reader toolbar markup
# ---------------------------------------------------------------------------

def test_reader_page_has_refresh_button_and_handler(env):
    c = _admin_client(env)
    html = c.get("/read").text
    assert "rrRefreshReadLater" in html
    assert "rr-refresh-btn" in html
    # Conditional on is_read_later, not unconditionally rendered.
    assert "d.is_read_later" in html
