"""One table format for every admin page (2026-09).

Brian's rule: light-blue header row, white rows, a line between rows, and a
rounded border. It lives in ONE CSS block in _CSS (scoped to main.admin-main)
so no table has to remember it. These tests pin the two things that make it
hold: the block exists with those properties, and every admin page's <main>
carries admin-main, whatever `active` section the route passes to _page().
"""
import os
import pathlib
import re
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Library


@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    lib = Library(db)
    lib.add_tool_category("FP&A", "Planning")
    lib.add_community_category("Peer groups", "desc")
    tid = lib.add_tool("Tool", "A planning tool.", "https://t.example", ["FP&A"])
    lib.approve_tool(tid)
    lib.add_community("Comm", "https://c.example", "CFOs", "Free", ["Peer groups"], approved=1)
    lib.add_benchmark("Bench", "https://b.example", "desc")
    lib.add_thought_leadership("writing", "TL", url="https://tl.example", date_label="Jun 2026")
    lib.add_original_content("oc", "OC", teaser="t", tag_label="Guide", link_label="Read the guide")
    lib.add_ai_surface("ai", "AI", teaser="t")
    lib.add_voice_review_item("tools", tid, "description", "buzzword", "seamless")
    lib.close()
    import importlib
    import webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "admin", "password": "adminpass"})
    yield appmod, c
    if os.path.exists(db):
        os.remove(db)


def _main_classes(html: str) -> str:
    m = re.search(r'<main class="([^"]*)"', html)
    return m.group(1) if m else ""


def test_admin_path_detection():
    from webapp.app import _is_admin_path
    assert _is_admin_path("/admin")
    assert _is_admin_path("/admin/tools/software")
    assert _is_admin_path("/tools/software/acme/edit")
    assert _is_admin_path("/tools/communities/acme/edit")
    assert not _is_admin_path("/tools/software")
    assert not _is_admin_path("/tools/fpa-buddy/how-it-works")
    assert not _is_admin_path("/")


def test_the_one_table_format_lives_in_shared_css():
    from webapp.app import _CSS
    assert "--table-border:" in _CSS
    block = _CSS[_CSS.index(".admin-main table{"):]
    block = block[:block.index("@media(max-width:700px)")]
    assert "border:1px solid var(--table-border)!important" in block
    assert "border-radius:12px!important" in block
    assert "background:var(--accent-light)!important" in block  # header row
    assert ".admin-main table tr{background:var(--surface);}" in block  # white rows
    assert "td{border-top:1px solid var(--line)!important" in block  # row lines
    # Sticky-column tables carry the frame on their scroll wrapper instead.
    assert ".admin-main .table-frame{" in block


def test_every_admin_page_with_a_table_is_scoped_to_the_format(env):
    appmod, c = env
    paths = sorted({r.path for r in appmod.app.routes
                    if getattr(r, "methods", None) and "GET" in r.methods
                    and r.path.startswith("/admin") and "{" not in r.path
                    and not r.path.endswith((".csv", "download-db"))})
    checked = 0
    for path in paths:
        r = c.get(path)
        if r.status_code != 200 or "<table" not in r.text:
            continue
        checked += 1
        assert "admin-main" in _main_classes(r.text).split(), path
    # Toolbox and thought-leadership admin pages pass their own `active`
    # section, which is how they fell outside the format before; make sure
    # the sweep actually reached them.
    assert checked >= 20


def test_toolbox_admin_page_is_scoped_even_without_active_admin(env):
    _, c = env
    html = c.get("/admin/tools/software").text
    assert "admin-main" in _main_classes(html).split()


def test_public_pages_keep_their_own_table_treatment(env):
    _, c = env
    for path in ("/", "/tools/software", "/tools/fpa-buddy/how-it-works"):
        assert "admin-main" not in _main_classes(c.get(path).text).split(), path


def test_sticky_tables_frame_their_wrapper_not_the_table(env):
    _, c = env
    html = c.get("/admin/tools/software").text
    wrap = html[html.index('id="cmp-scroll-wrap"') - 200:html.index('id="cmp-scroll-wrap"')]
    assert 'class="table-frame"' in wrap
