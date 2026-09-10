"""Reader route moves (PR 6, 2026-09): five `/admin/library/*` GET pages
moved to `/admin/reader/*`, and the archive-backup page moved to
`/admin/library-backup` (a deliberate exception — see webapp/app.py's
ARCHITECTURE.md write-up on why it keeps the word "library"). No
compatibility redirects: every old path must 404, both signed in and signed
out, and every new path must actually render for an authenticated admin.

Two pages were deliberately NOT part of this move — Tag cleanup
(`/admin/library/tags`) and Tagging style (`/admin/library/tag-style`).
They've since merged into a single `/admin/reader/tag-management` page
(PR 7, 2026-09) — see tests/test_tag_management_merge.py for that move's
own coverage; this file stays scoped to the five PR 6 moves.
"""
import os
import pathlib
import shutil
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

REPO_OPML = str(pathlib.Path(__file__).resolve().parents[1] / "preferred_sites.opml")

# (old path, new path) for every one of the six GET admin pages this PR moves.
_RENAMED_PAGES = [
    ("/admin/library/feeds", "/admin/reader/feeds"),
    ("/admin/library/backfill-content", "/admin/reader/backfill-content"),
    ("/admin/library/enrich", "/admin/reader/enrich"),
    ("/admin/library/dedupe", "/admin/reader/dedupe"),
    ("/admin/library/bulk-delete", "/admin/reader/bulk-delete"),
    ("/admin/library/backup", "/admin/library-backup"),
]

@pytest.fixture
def env(monkeypatch, tmp_path):
    opml = tmp_path / "sites.opml"
    shutil.copy(REPO_OPML, opml)
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_SITES_OPML", str(opml))
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib
    import webapp.app as appmod
    importlib.reload(appmod)
    yield appmod
    if os.path.exists(db):
        os.remove(db)


def _admin_client(appmod):
    from fastapi.testclient import TestClient
    client = TestClient(appmod.app, raise_server_exceptions=True)
    client.post("/login", data={"username": "admin", "password": "adminpass"},
                follow_redirects=False)
    return client


def _anon_client(appmod):
    from fastapi.testclient import TestClient
    return TestClient(appmod.app, raise_server_exceptions=True)


@pytest.mark.parametrize("old_path,new_path", _RENAMED_PAGES)
def test_new_path_renders_for_an_authed_admin(env, old_path, new_path):
    with _admin_client(env) as client:
        resp = client.get(new_path)
    assert resp.status_code == 200, (new_path, resp.status_code)


@pytest.mark.parametrize("old_path,new_path", _RENAMED_PAGES)
def test_old_path_404s_signed_in(env, old_path, new_path):
    """No compatibility redirect — the old path is gone outright, even for
    an authenticated admin."""
    with _admin_client(env) as client:
        resp = client.get(old_path, follow_redirects=False)
    assert resp.status_code == 404, (old_path, resp.status_code)


@pytest.mark.parametrize("old_path,new_path", _RENAMED_PAGES)
def test_old_path_404s_signed_out(env, old_path, new_path):
    anon = _anon_client(env)
    resp = anon.get(old_path, follow_redirects=False)
    assert resp.status_code == 404, (old_path, resp.status_code)


@pytest.mark.parametrize("old_path,new_path", _RENAMED_PAGES)
def test_new_path_redirects_to_login_when_signed_out(env, old_path, new_path):
    """The page itself is real (not a 404) but still admin-gated."""
    anon = _anon_client(env)
    resp = anon.get(new_path, follow_redirects=False)
    assert resp.status_code in (302, 303, 307)
    assert "/login" in resp.headers.get("location", "")


def test_admin_reader_bare_prefix_is_not_a_page(env):
    """/admin/reader is not a landing page — no route exists at the bare
    prefix, so it 404s exactly like any other unmatched path (checked
    signed in, so a 404 here can't be mistaken for the login redirect an
    admin-gated-but-real page would give)."""
    with _admin_client(env) as client:
        resp = client.get("/admin/reader", follow_redirects=False)
    assert resp.status_code == 404


def test_backup_now_post_route_is_unaffected(env):
    """POST /admin/backup-now is a different route entirely (the token-authed
    trigger the daily Railway Cron Service calls) — never under
    /admin/library/*, so this PR's rename doesn't touch it. Confirmed it
    still exists and isn't accidentally 404ing after the rename (a missing
    Drive config is expected in this test env, so 502/503 — anything but
    404 — proves the route itself is intact)."""
    with _admin_client(env) as client:
        resp = client.post("/admin/backup-now", follow_redirects=False)
    assert resp.status_code != 404


def test_backup_sub_routes_moved_with_their_parent(env):
    """download-db and upload-db live under the new /admin/library-backup
    prefix now, and are gone from the old /admin/library/backup one."""
    with _admin_client(env) as client:
        ok = client.get("/admin/library-backup/download-db", follow_redirects=False)
        gone = client.get("/admin/library/backup/download-db", follow_redirects=False)
    assert ok.status_code != 404
    assert gone.status_code == 404


def test_backfill_content_start_status_moved(env):
    with _admin_client(env) as client:
        status_new = client.get("/admin/reader/backfill-content/status")
        status_old = client.get("/admin/library/backfill-content/status", follow_redirects=False)
    assert status_new.status_code == 200
    assert status_old.status_code == 404


def test_feeds_sub_routes_moved(env):
    with _admin_client(env) as client:
        new_page = client.get("/admin/reader/feeds/new")
        old_page = client.get("/admin/library/feeds/new", follow_redirects=False)
    assert new_page.status_code == 200
    assert old_page.status_code == 404


def test_hub_nav_has_no_orphans_after_the_move(env):
    """The backup card's move between hub-nav groups (_LIBRARY_TOOLS ->
    System) shouldn't create a new orphan, and the five renamed pages
    should still be carded wherever they were before."""
    assert env.hub_nav_orphans() == []


def test_archive_backup_card_lives_under_system_group(env):
    system_items = next(items for gname, _gdesc, items in env._ADMIN_GROUPS
                         if gname == "System")
    hrefs = [href for href, _title, _desc in system_items]
    assert "/admin/library-backup" in hrefs
    assert not any(href == "/admin/library-backup" for href, _t, _d in env._LIBRARY_TOOLS)


def test_library_page_index_stays_clean(env):
    """A route RENAME, not a new page — width tiers are introspected live
    from each route's own source (_page_index_tier_for), so nothing needed
    updating for the move itself; this just confirms the mechanism still
    finds every renamed route carrying a real tier, not "No tier assigned"."""
    with _admin_client(env) as client:
        html = client.get("/admin/system/page-index").text
    assert "Every page carries a recognized width tier." in html
    assert "No tier assigned" not in html
    for _old, new_path in _RENAMED_PAGES:
        assert f'>{new_path}<' in html
