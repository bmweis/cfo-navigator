"""Reader search lives in the list pane's header, not the left rail.

Correcting placement drift introduced by the Phase 5 merge, not a redesign.
Established by git archaeology (see ARCHITECTURE.md's "Search placement"
note for the full evidence):

- The pre-merge Archive page rendered search in a page-width header bar
  directly above the list, in the same column as the cards. That page had
  no rail at all.
- Phase 5's own ARCHITECTURE.md describes Saved search under its *middle
  list pane* bullet, and its exhaustive left-rail inventory (quick views,
  plus a Sources tree in Feed view only) never mentions search — while the
  same commit's code put the form in the rail.
- The mechanism was assignment to a variable named `sources_html`, which
  exists to hold Feed's Sources tree and is only ever interpolated into
  `rail_html`. No commit anywhere evaluates rail-vs-list-pane placement.
- The Feed search box's own CSS carried `padding:14px 22px` — the *list
  pane's* 22px gutter, not the rail's 14px — while rendering in the rail.

Caveat recorded honestly: the `Feed.dc.html` design export is not in this
repo and could not be re-read when this was fixed. Every second-hand account
of it in git history places its (decorative) magnifying glass in the
list-pane header, and none describes a search box in a rail.
"""
import json
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
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    yield appmod
    if os.path.exists(db):
        os.remove(db)


def _admin_client(appmod):
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app, raise_server_exceptions=True)
    c.post("/login", data={"username": "admin", "password": "adminpass"},
           follow_redirects=False)
    return c


def _seed(appmod, tags=("alpha", "beta")):
    lib = appmod._lib()
    try:
        lib.conn.execute(
            "INSERT INTO articles (url,title,source,summary,content,tags_json,tags_text,"
            "saved_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("https://example.com/a", "A Saved Piece", "CFO Weekly", "Summary.", "Body. " * 60,
             json.dumps(list(tags)), " ".join(tags), "2026-08-01", "2026-08-01", "2026-08-01"),
        )
        lib.conn.commit()
    finally:
        lib.close()


def _fake_feed(monkeypatch):
    import linklib.feed as feed_mod
    monkeypatch.setattr(feed_mod, "get_feed_items", lambda *a, **k: ([{
        "url": "https://example.com/i", "title": "An Item", "source": "CFO Dive",
        "category": "News", "published_at": "2026-08-14", "summary": "Summary.",
    }], ["News"]))


def _rail(html):
    """The rail's markup: <div class="rr-rail" id="rr-rail"> ... </div>, up to
    the resize handle that follows it in the shell."""
    m = re.search(r'<div class="rr-rail" id="rr-rail">(.*?)<div class="rr-resize"', html, re.S)
    assert m, "reader shell should contain a left rail"
    return m.group(1)


def _list_header(html):
    m = re.search(r'<div class="rr-list-header">(.*?)</div>\s*<div id="rr-alert-wrap"', html, re.S)
    assert m, "list pane should contain a header"
    return m.group(1)


# ---------------------------------------------------------------------------
# Feed view
# ---------------------------------------------------------------------------

def test_feed_search_renders_in_list_header_not_rail(env, monkeypatch):
    _fake_feed(monkeypatch)
    html = _admin_client(env).get("/read?view=feed").text
    assert 'id="rr-feed-search"' in _list_header(html)
    assert 'id="rr-feed-search"' not in _rail(html)


def test_feed_rail_keeps_its_sources_tree(env, monkeypatch):
    """Only search moves. The Sources tree is a filter vocabulary and stays."""
    _fake_feed(monkeypatch)
    rail = _rail(_admin_client(env).get("/read?view=feed").text)
    assert "Sources" in rail
    assert 'class="rr-cat"' in rail


def test_feed_search_keeps_its_client_side_mechanism(env, monkeypatch):
    """Placement changed; the mechanism did not. Feed search still filters
    client-side through rrApplyFilter, composing with category/source."""
    _fake_feed(monkeypatch)
    html = _admin_client(env).get("/read?view=feed").text
    assert 'oninput="rrApplyFilter()"' in html
    assert "rrFeedCat" in html and "rrFeedSrc" in html


# ---------------------------------------------------------------------------
# Saved view
# ---------------------------------------------------------------------------

def test_saved_search_renders_in_list_header_not_rail(env):
    _seed(env)
    html = _admin_client(env).get("/read?view=saved").text
    header = _list_header(html)
    assert 'name="q"' in header
    assert 'action="/read"' in header
    assert 'name="q"' not in _rail(html)


def test_saved_search_keeps_its_server_get_mechanism(env):
    """Still a plain GET reload, matching this codebase's server-rendered
    search convention — only the placement moved."""
    _seed(env)
    header = _list_header(_admin_client(env).get("/read?view=saved").text)
    assert 'method="get"' in header
    assert '<input type="hidden" name="view" value="saved">' in header


def test_saved_search_preserves_the_active_query(env):
    _seed(env)
    header = _list_header(_admin_client(env).get("/read?view=saved&q=alpha").text)
    assert 'value="alpha"' in header


def test_saved_rail_keeps_the_tagbar_relabelled(env):
    """The tag bar is Saved's filter vocabulary — Feed's Sources counterpart —
    so it stays in the rail. Its heading was "Search", which described the form
    that used to sit above it; with the form gone it's relabelled to match."""
    _seed(env)
    rail = _rail(_admin_client(env).get("/read?view=saved").text)
    assert 'class="rr-tagbar"' in rail
    assert "Tags" in rail
    assert ">Search<" not in rail, "stale heading left behind by the moved form"


# ---------------------------------------------------------------------------
# Read Later renders no search, same as before.
# ---------------------------------------------------------------------------

def test_readlater_renders_no_search(env):
    html = _admin_client(env).get("/read?view=readlater").text
    assert 'name="q"' not in _list_header(html)
    assert 'id="rr-feed-search"' not in html


# ---------------------------------------------------------------------------
# The CSS fingerprint that gave the drift away is gone.
# ---------------------------------------------------------------------------

def test_search_no_longer_carries_the_list_panes_gutter_while_in_the_rail(env):
    """`.rr-search-form{padding:14px 22px 0}` put the list pane's 22px gutter
    on a box rendering inside a 232px rail whose own gutter is 14px. The
    replacement takes its horizontal padding from .rr-list-header instead."""
    html = _admin_client(env).get("/read").text
    # Assert the rules are gone, not the string — the replacement's own comment
    # names the old class to explain what it replaced.
    assert ".rr-search-form{" not in html
    assert ".rr-search-form input" not in html
    assert ".rr-list-search{margin:12px 0 0;" in html
