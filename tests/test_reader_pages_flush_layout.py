"""Benchmark digests and Benchmark figures sit flush left on .page-standard,
like /admin/library-backup and /admin/reader/backfill-content, and the hub
puts both loaders under Reader > Manage archive.

The two pages used to wrap everything under the h1 in
`max-width:900px;margin:0 auto`, so the intro, forms and tables were centered
(about 166px in at 1280px) while the back link and h1 stayed at the left edge.
"""
import importlib
import os
import pathlib
import re
import shutil
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
REPO_OPML = str(pathlib.Path(__file__).resolve().parents[1] / "preferred_sites.opml")
PAGES = ["/admin/reader/figures", "/admin/reader/digests", "/admin/reader/bulk-delete"]
CENTERED = re.compile(r"max-width:\s*\d+px;\s*margin:\s*0 auto")


@pytest.fixture
def env(monkeypatch, tmp_path):
    opml = tmp_path / "sites.opml"
    shutil.copy(REPO_OPML, opml)
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_SITES_OPML", str(opml))
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import webapp.app as appmod
    importlib.reload(appmod)
    yield appmod
    if os.path.exists(db):
        os.remove(db)


def _client(appmod):
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    return c


@pytest.mark.parametrize("path", PAGES)
def test_page_has_no_centering_wrapper(env, path):
    with _client(env) as c:
        html = c.get(path).text
    page = html[html.index('class="page page-standard"'):]
    assert not CENTERED.search(page), f"{path} still centers its content"


def test_hub_group_is_named_manage_archive_and_holds_both_loaders(env):
    with _client(env) as c:
        html = c.get("/admin").text
    assert "Existing archive management" not in html
    assert ">Manage archive</span>" in html

    def body(label):
        start = html.rindex("<details", 0, html.index(f">{label}</span>"))
        depth, i = 0, start
        while True:
            o, cl = html.find("<details", i + 1), html.find("</details>", i + 1)
            if o != -1 and o < cl:
                depth, i = depth + 1, o
            elif depth == 0:
                return html[start:cl]
            else:
                depth, i = depth - 1, cl

    manage, add = body("Manage archive"), body("Add content")
    order = ["/admin/reader/backfill-content", "/admin/reader/dedupe", "/admin/reader/bulk-delete",
             "/admin/reader/digests", "/admin/reader/figures"]
    positions = [manage.index(f'href="{h}"') for h in order]
    assert positions == sorted(positions)
    for h in ("/admin/reader/digests", "/admin/reader/figures"):
        assert f'href="{h}"' not in add
    assert "Saving to the archive" in add
    for label in ("Manage archive", "Add content"):
        assert " open" not in body(label)[: body(label).index(">") + 1]


def test_hub_nav_orphans_stay_clean(env):
    assert env.hub_nav_orphans() == []
