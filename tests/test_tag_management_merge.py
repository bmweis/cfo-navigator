"""Tag management merge (PR 7, 2026-09): Tag cleanup (`/admin/library/tags`)
and Tagging style (`/admin/library/tag-style`) merge into a single
`/admin/reader/tag-management` page — two headed sections, no disclosure.
No compatibility redirects: both old paths must 404, signed in and signed
out. Every action from both original pages must still work from the merged
page, and the two old hub-nav cards collapse into one.
"""
import os
import pathlib
import shutil
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

REPO_OPML = str(pathlib.Path(__file__).resolve().parents[1] / "preferred_sites.opml")

_OLD_PATHS = ["/admin/library/tags", "/admin/library/tag-style"]
NEW_PATH = "/admin/reader/tag-management"


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


def test_merged_page_renders_for_an_authed_admin(env):
    with _admin_client(env) as client:
        resp = client.get(NEW_PATH)
    assert resp.status_code == 200
    assert "Tag cleanup" in resp.text
    assert "Tagging style" in resp.text
    # "&" -> "and" in PR 9's typographic sweep (see linklib.voice_review's
    # typography_findings) — the page title was that lint's first fix.
    assert "Tag cleanup and style" in resp.text


def test_merged_page_carries_the_new_intro(env):
    with _admin_client(env) as client:
        html = client.get(NEW_PATH).text
    assert ("This page does two jobs. Tag cleanup fixes tags already on "
            "your saved articles. Tagging style controls how new tags get "
            "chosen automatically.") in html


@pytest.mark.parametrize("old_path", _OLD_PATHS)
def test_old_path_404s_signed_in(env, old_path):
    with _admin_client(env) as client:
        resp = client.get(old_path, follow_redirects=False)
    assert resp.status_code == 404, (old_path, resp.status_code)


@pytest.mark.parametrize("old_path", _OLD_PATHS)
def test_old_path_404s_signed_out(env, old_path):
    anon = _anon_client(env)
    resp = anon.get(old_path, follow_redirects=False)
    assert resp.status_code == 404, (old_path, resp.status_code)


def test_new_path_redirects_to_login_when_signed_out(env):
    anon = _anon_client(env)
    resp = anon.get(NEW_PATH, follow_redirects=False)
    assert resp.status_code in (302, 303, 307)
    assert "/login" in resp.headers.get("location", "")


def test_old_sub_routes_404(env):
    """POST targets moved with their parent — nothing answers at the old
    /admin/library/tags/* or /admin/library/tag-style/* paths any more."""
    with _admin_client(env) as client:
        gone = [
            client.post("/admin/library/tags/rename",
                        data={"old": "a", "new": "b"}, follow_redirects=False),
            client.post("/admin/library/tag-style/save",
                        data={"guide": "x"}, follow_redirects=False),
        ]
    for resp in gone:
        assert resp.status_code == 404


def test_every_tag_cleanup_action_works_from_the_merged_page(env):
    with _admin_client(env) as client:
        r = client.post(f"{NEW_PATH}/tags/suggest-merges", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith(f"{NEW_PATH}?merging=1")

        r = client.post(f"{NEW_PATH}/tags/rename", data={"old": "nope", "new": "nope2"},
                        follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith(NEW_PATH)

        r = client.post(f"{NEW_PATH}/tags/delete", data={"tag": "nonexistent"},
                        follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith(NEW_PATH)

        r = client.post(f"{NEW_PATH}/tags/merge-group",
                        data={"canonical": "x", "merge": "y"}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith(NEW_PATH)

        r = client.post(f"{NEW_PATH}/tags/merge-all", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith(NEW_PATH)


def test_every_tagging_style_action_works_from_the_merged_page(env):
    with _admin_client(env) as client:
        r = client.post(f"{NEW_PATH}/tag-style/objective",
                        data={"objective": "test objective"}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == NEW_PATH
        assert "test objective" in client.get(NEW_PATH).text

        r = client.post(f"{NEW_PATH}/tag-style/save", data={"guide": "my guide"},
                        follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == NEW_PATH
        assert "my guide" in client.get(NEW_PATH).text

        r = client.post(f"{NEW_PATH}/tag-style/generate", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == f"{NEW_PATH}?generating=1"

        r = client.post(f"{NEW_PATH}/tag-style/clear", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == NEW_PATH
        assert "my guide" not in client.get(NEW_PATH).text


def test_hub_nav_has_one_card_where_there_were_two(env):
    hrefs = [href for href, _t, _d in env._LIBRARY_TOOLS]
    assert NEW_PATH in hrefs
    assert "/admin/library/tags" not in hrefs
    assert "/admin/library/tag-style" not in hrefs


def test_hub_nav_has_no_orphans(env):
    assert env.hub_nav_orphans() == []


def test_reader_box_renders_the_merged_card(env):
    """Was /admin/library; that page was retired in PR 9 and its quadrants
    are a Reader group on /admin now."""
    with _admin_client(env) as client:
        html = client.get("/admin").text
    assert f'href="{NEW_PATH}"' in html
    assert "Tag cleanup and style" in html
    assert '/admin/library/tags"' not in html
    assert '/admin/library/tag-style"' not in html


def test_enrich_archive_description_updated(env):
    hrefs = {href: desc for href, _t, desc in env._LIBRARY_TOOLS}
    assert hrefs["/admin/reader/enrich"] == (
        "Uses Claude to draft a summary and tags for each saved article. "
        "This is where new tags get created."
    )


def test_page_index_stays_clean(env):
    with _admin_client(env) as client:
        html = client.get("/admin/system/page-index").text
    assert "Every page carries a recognized width tier." in html
    assert "No tier assigned" not in html
    assert f'>{NEW_PATH}<' in html
