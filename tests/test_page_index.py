"""Page Index (/admin/system/page-index): a live map of every HTML page route
and its width tier, introspected from `app.routes` rather than a maintained
list — see webapp/app.py's `_page_index_snapshot()`.
"""
import pathlib
import sys
import tempfile
import os

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    yield appmod
    if os.path.exists(db):
        os.remove(db)


def _client(appmod):
    from fastapi.testclient import TestClient
    return TestClient(appmod.app, raise_server_exceptions=True)


def _admin_client(appmod):
    c = _client(appmod)
    c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    return c


def _member_client(appmod):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    lib.create_user("member1", "supersecret", role="user")
    lib.close()
    c = _client(appmod)
    c.post("/login", data={"username": "member1", "password": "supersecret"}, follow_redirects=False)
    return c


def test_page_index_requires_admin(env):
    anon = _client(env)
    r = anon.get("/admin/system/page-index", follow_redirects=False)
    assert r.status_code == 303 and "/login" in r.headers["location"]

    member = _member_client(env)
    r = member.get("/admin/system/page-index", follow_redirects=False)
    assert r.status_code == 303 and "/login" in r.headers["location"]


def test_page_index_loads_for_admin(env):
    c = _admin_client(env)
    r = c.get("/admin/system/page-index")
    assert r.status_code == 200
    assert "Page index" in r.text
    assert "app.routes" in r.text


def test_page_index_excludes_non_page_endpoints(env):
    """Redirect stubs, JSON/AJAX APIs, and POST-only action routes are not
    HTML pages and shouldn't appear in the snapshot."""
    rows = env._page_index_snapshot()
    paths = {r["path"] for r in rows}
    non_pages = [
        "/health",            # health check, plain dict/JSON
        "/api/search",        # JSON search API
        "/growth-engine-ratio",  # legacy redirect stub
        "/archive",              # legacy redirect stub
        "/logout",               # redirects to /
        "/bookmarklet",          # PlainTextResponse, not a page
        "/static/{filename}",    # static asset serving
    ]
    for path in non_pages:
        assert path not in paths, f"{path} should be filtered out as a non-page endpoint"


def test_page_index_includes_known_pages(env):
    rows = env._page_index_snapshot()
    paths = {r["path"] for r in rows}
    # /admin/library is deliberately absent — that page was retired in PR 9
    # (2026-09), its quadrants folded into a Reader group on /admin itself.
    for path in ["/", "/thought-leadership", "/admin/reader/feeds", "/admin",
                 "/admin/system/page-index", "/tools/fpa-buddy"]:
        assert path in paths


def test_page_index_recognizes_reader_shell_as_custom_exception(env):
    """`/read` (Phase 5's merged three-pane Reader shell) is a bespoke
    full-bleed layout that never uses the `.page`/`.page-standard` classes —
    same reasoning the old /library/archive and /library/feed carried
    before the merge."""
    rows = {r["path"]: r for r in env._page_index_snapshot()}
    assert rows["/read"]["tier"] == "custom exception"
    assert rows["/read"]["flagged"] is False


def test_page_index_recognizes_read_article_as_page_standard(env):
    """`/read/{article_id}` is a fully standalone template (_READER_TMPL/
    _READER_CSS) that never uses the `.page`/`.page-standard` classes, so
    the live-source regex can't detect its tier on its own. As of PR 13's
    width-tier collapse (2026-09) it tracks the Standard tier (1300px), not
    the new, narrower Content tier — its two-column layout (a 760px reading
    column plus a 220px sticky TOC) needs more headroom than Content's
    900px leaves — so it's mapped to that real tier name (not flagged)
    rather than surfacing as a false "no tier assigned" flag."""
    rows = {r["path"]: r for r in env._page_index_snapshot()}
    assert rows["/read/{article_id}"]["flagged"] is False
    assert rows["/read/{article_id}"]["tier"] == "page-standard"


def test_page_index_flags_a_newly_added_untiered_route(env):
    """Simulates someone adding a new page and forgetting to tier it — added
    and removed on the live app object, no source file changes. This is the
    drift-detection the feature exists for, and now the only real-world way
    to see a "no tier assigned" flag, since every currently-shipped untiered
    route (`/read`, plus the two custom-exception layouts) has a documented
    exception above."""
    from fastapi.responses import HTMLResponse

    @env.app.get("/test-temp-untiered-route", response_class=HTMLResponse)
    def _temp_route():
        return HTMLResponse('<div class="page">no tier here</div>')

    try:
        rows = {r["path"]: r for r in env._page_index_snapshot()}
        assert "/test-temp-untiered-route" in rows
        assert rows["/test-temp-untiered-route"]["flagged"] is True
    finally:
        env.app.router.routes = [
            r for r in env.app.router.routes
            if getattr(r, "path", None) != "/test-temp-untiered-route"
        ]


def test_page_index_recognized_tiers_are_valid(env):
    valid = {"page-standard", "page-content", "page-form", "custom exception"}
    for row in env._page_index_snapshot():
        if row["flagged"]:
            continue
        for tier in row["tier"].split(","):
            assert tier in valid, f"{row['path']} has unrecognized tier value {tier!r}"
