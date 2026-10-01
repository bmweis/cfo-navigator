"""Homepage "Original content" block: a fixed 2x2 grid with a section header,
capped at four pieces. Measured cause of the old three-across layout: the
shared .tl-featured rule (auto-fill, 220px floor) fit only 3 tracks in the
~820px homepage column."""
import importlib
import os
import pathlib
import re
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import webapp.app as appmod
    importlib.reload(appmod)
    yield appmod
    if os.path.exists(db):
        os.remove(db)


def _seed(appmod, n, *, featured=True, status="live"):
    lib = appmod._lib()
    try:
        for i in range(n):
            lib.add_original_content(
                f"piece-{i}", f"Piece {i}", "Teaser.", "Guide", "Read the guide",
                "body", status, featured, "June 2026", f"2026-0{i + 1}", i)
    finally:
        lib.close()


def _home(appmod):
    from fastapi.testclient import TestClient
    return TestClient(appmod.app).get("/").text


def _home_grid(html):
    m = re.search(r'<div class="home-oc-wrap">(.*?)</div>\s*</div>', html, re.S)
    return m.group(1) if m else ""


def test_four_pieces_render_in_two_column_grid_with_header(env):
    _seed(env, 4)
    html = _home(env)
    wrap = _home_grid(html)
    assert 'class="home-tl-highlights-label">Original content<' in wrap
    assert 'class="tl-featured tl-featured-home"' in wrap
    assert wrap.count('class="tl-card"') == 4
    css = html[html.index(".tl-featured.tl-featured-home"):]
    assert "repeat(2,minmax(0,1fr))" in css
    assert "auto-fit" not in css.split("}")[0]


def test_home_block_is_capped_at_four_newest_by_existing_order(env):
    _seed(env, 6)
    lib = env._lib()
    try:
        slugs = [r["slug"] for r in lib.list_original_content_for_home()]
    finally:
        lib.close()
    assert slugs == ["piece-0", "piece-1", "piece-2", "piece-3"]
    assert _home(env).count('class="tl-card"') == 4


def test_fewer_than_four_still_renders_two_column_grid(env):
    _seed(env, 3)
    wrap = _home_grid(_home(env))
    assert wrap.count('class="tl-card"') == 3
    assert "tl-featured-home" in wrap


def test_no_live_pieces_omits_header_and_grid(env):
    _seed(env, 2, status="draft")
    html = _home(env)
    assert "home-oc-wrap" not in html.split("</style>")[-1]
    assert ">Original content<" not in html


def test_thought_leadership_page_keeps_its_own_grid(env):
    from fastapi.testclient import TestClient
    _seed(env, 4)
    html = TestClient(env.app).get("/thought-leadership").text
    assert "tl-featured-home" not in html.split("</style>")[-1]
