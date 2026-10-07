"""Homepage polish: the MCP line becomes plain text, Recent highlights is one
row of three (cap 3), Original content keeps its 2x2 (cap 4) with the same
slot line and refusal, and the admin hub lists Original content first."""
import importlib
import os
import pathlib
import re
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

OLD_MCP = "Use it here, or connect it to your own AI assistant. The whole toolbox runs over MCP."
NEW_MCP = "Access the toolbox here directly or connect it to your own AI assistant with the CFO Navigator MCP."


@pytest.fixture
def env(monkeypatch, tmp_path):
    import shutil
    opml = tmp_path / "sites.opml"
    shutil.copy(pathlib.Path(__file__).resolve().parents[1] / "preferred_sites.opml", opml)
    monkeypatch.setenv("LINKLIB_SITES_OPML", str(opml))
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import webapp.app as appmod
    importlib.reload(appmod)
    yield appmod
    if os.path.exists(db):
        os.remove(db)


def _client(appmod, admin=False):
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app)
    if admin:
        c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    return c


def _tl(lib, n, flagged=True):
    ids = []
    for i in range(n):
        ids.append(lib.add_thought_leadership(
            "writing", f"Highlight {i}", f"https://example.com/{i}", "Forbes",
            f"Jan 202{i}", f"202{i}-01", "A summary.", featured_home=flagged))
    return ids


def _oc(lib, n, status="draft", flagged=True, start=0):
    ids = []
    for i in range(start, start + n):
        ids.append(lib.add_original_content(
            f"piece-{i}", f"Piece {i}", "Teaser.", "Guide", "Read the guide",
            "body", status, flagged, "June 2026", f"2026-0{i + 1}", i))
    return ids


# ---- item 1: MCP line --------------------------------------------------

def test_homepage_mcp_line_is_plain_text(env):
    html = _client(env).get("/").text
    assert NEW_MCP in html
    assert OLD_MCP not in html
    i = html.index(NEW_MCP)
    tag = html[html.rindex("<p", 0, i):i]
    assert "coral" not in tag and "background" not in tag and "border:1px" not in tag
    assert "var(--muted)" in tag


def test_tools_page_keeps_its_callout(env):
    html = _client(env).get("/tools").text
    assert OLD_MCP in html and "var(--coral-wash)" in html


# ---- item 2: three-column highlights ----------------------------------

def test_highlights_markup_is_three_columns_with_clamped_summary(env):
    lib = env._lib()
    try:
        _tl(lib, 3)
    finally:
        lib.close()
    html = _client(env).get("/").text
    assert "@container (min-width:640px)" in html
    assert "repeat(3,minmax(0,1fr))" in html
    assert "-webkit-line-clamp:3" in html
    assert html.count('class="home-hl-desc"') == 3


def test_render_backstop_shows_only_three_when_four_are_flagged(env):
    lib = env._lib()
    try:
        _tl(lib, 4)          # direct writes bypass the route refusal
    finally:
        lib.close()
    html = _client(env).get("/").text
    assert html.count('class="home-hl-desc"') == 3
    body = html.split("</style>")[-1]
    # newest first: 3, 2, 1 show; the oldest (0) is the one the backstop drops
    assert "Highlight 3" in body and "Highlight 1" in body and "Highlight 0" not in body


# ---- item 3: limits ----------------------------------------------------

def test_fourth_highlight_is_refused_naming_the_three(env):
    lib = env._lib()
    try:
        _tl(lib, 3)
    finally:
        lib.close()
    r = _client(env, admin=True).post("/admin/thought-leadership/third-party/new", data={
        "type": "writing", "title": "Fourth", "featured_home": "1"}, follow_redirects=False)
    assert r.status_code == 400
    for t in ("Highlight 0", "Highlight 1", "Highlight 2"):
        assert t in r.text
    assert "All 3 homepage highlight slots are in use" in r.text
    assert "Unflag one" in r.text


def test_unflag_then_flag_works(env):
    lib = env._lib()
    try:
        ids = _tl(lib, 3)
    finally:
        lib.close()
    c = _client(env, admin=True)
    base = {"type": "writing", "venue": "Forbes", "date_label": "Jan 2020", "display_order": "0"}
    r = c.post(f"/admin/thought-leadership/third-party/{ids[0]}/edit",
               data={**base, "title": "Highlight 0"}, follow_redirects=False)   # unflag
    assert r.status_code == 303
    r = c.post("/admin/thought-leadership/third-party/new",
               data={"type": "writing", "title": "Fourth", "featured_home": "1"}, follow_redirects=False)
    assert r.status_code == 303


def test_over_limit_highlights_show_a_notice_and_nothing_is_unflagged(env):
    lib = env._lib()
    try:
        _tl(lib, 4)
    finally:
        lib.close()
    html = _client(env, admin=True).get("/admin/thought-leadership/third-party").text
    assert "Homepage highlights: 4 of 3 slots used." in html
    assert "not showing on the homepage" in html and "Hidden: Highlight 0" in html
    lib = env._lib()
    try:
        assert lib.count_featured_home() == 4
    finally:
        lib.close()


def test_oc_slot_line_and_fifth_flag_refused(env):
    lib = env._lib()
    try:
        _oc(lib, 4)
    finally:
        lib.close()
    c = _client(env, admin=True)
    assert "Homepage original content: 4 of 4 slots used." in c.get("/admin/thought-leadership/original").text
    r = c.post("/admin/thought-leadership/original/new", data={
        "title": "Fifth", "slug": "fifth", "teaser": "t", "tag_label": "Guide",
        "status": "draft", "featured_home": "1"}, follow_redirects=False)
    assert r.status_code == 400
    assert "All 4 homepage original content slots are in use" in r.text
    assert "Piece 0 (draft)" in r.text


def test_oc_edit_refuses_new_flag_but_allows_resaving_a_flagged_row(env):
    lib = env._lib()
    try:
        ids = _oc(lib, 4)
        extra = _oc(lib, 1, flagged=False, start=4)[0]
    finally:
        lib.close()
    c = _client(env, admin=True)
    base = {"tag_label": "Guide", "teaser": "t", "status": "draft", "display_order": "9"}
    r = c.post(f"/admin/thought-leadership/original/{extra}/edit",
               data={**base, "title": "Piece 4", "slug": "piece-4", "featured_home": "1"}, follow_redirects=False)
    assert r.status_code == 400
    r = c.post(f"/admin/thought-leadership/original/{ids[0]}/edit",
               data={**base, "title": "Piece 0 renamed", "slug": "piece-0", "featured_home": "1"},
               follow_redirects=False)
    assert r.status_code == 303


def test_going_live_is_never_blocked_by_the_slot_limit(env):
    lib = env._lib()
    try:
        _oc(lib, 3, status="live")
        draft = lib.add_original_content("chart-of-accounts", "Chart", "t", "Guide", "Read the guide",
                                         "body", "draft", True, "June 2026", "2026-06", 5)
    finally:
        lib.close()
    r = _client(env, admin=True).post(
        f"/admin/thought-leadership/original/{draft}/edit",
        data={"title": "Chart", "slug": "chart-of-accounts", "teaser": "t", "tag_label": "Guide",
              "status": "live", "featured_home": "1", "display_order": "5"}, follow_redirects=False)
    assert r.status_code == 303        # flagged draft -> live at 4 of 4: allowed


def test_over_limit_original_content_notice(env):
    lib = env._lib()
    try:
        _oc(lib, 5, status="live")
    finally:
        lib.close()
    html = _client(env, admin=True).get("/admin/thought-leadership/original").text
    assert "Homepage original content: 5 of 4 slots used." in html
    assert "not showing on the homepage" in html and "Hidden: Piece 4" in html


# ---- item 4: hub order -------------------------------------------------

def test_hub_lists_original_content_before_third_party(env):
    html = _client(env, admin=True).get("/admin").text
    a = html.index('href="/admin/thought-leadership/original"')
    b = html.index('href="/admin/thought-leadership/third-party"')
    c = html.index('href="/admin/thought-leadership/game-settings"')
    assert a < b < c


def test_hub_nav_orphans_unchanged(env):
    assert env.hub_nav_orphans() == []
