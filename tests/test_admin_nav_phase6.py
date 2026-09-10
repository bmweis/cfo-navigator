"""Phase 6 of the Exa integration: reorganize the /admin hub's section
groupings. FP&A Buddy's three admin pages (How it works, report, feedback)
consolidate into their own "FP&A Buddy" section (previously split between
System and the old "Features" catch-all); Sail, Don't Row settings move into
CFO Toolbox; Features is removed entirely once it's empty.

Pure reorganization — no route changes, no new functionality. See
tests/test_admin_how_buddy_works.py for the "How FP&A Buddy works" page's
own content tests, and tests/test_game_settings.py for /admin/thought-leadership/game-settings'.

**Superseded in part by the later Phase 6 (Layout Width Fixes, Admin Nav
Restructure, Library Admin Cleanup)**: FP&A Buddy is no longer its own
top-level `_ADMIN_GROUPS` entry — it nests inside CFO Toolbox as a
sub-group (`_FPA_BUDDY_TOOLS`, rendered via a recursive `_group_html()`
call inside `admin_page()`, not stored in `_ADMIN_GROUPS` itself), matching
the public nav's own Toolbox->FP&A Buddy relationship. Features staying
gone and the four routes themselves are all still true and still covered
below.

**Superseded again by the Admin URL restructure (group A)**: Sail, Don't
Row settings moved a second time, out of CFO Toolbox into Thought
leadership — its URL changed from `/admin/game-settings` to
`/admin/thought-leadership/game-settings` in the same move, and the FP&A
Buddy report/feedback pages moved from `/admin/ask-report`/
`/admin/ask-feedback` to `/admin/fpa-buddy/report`/`/admin/fpa-buddy/
feedback`. See test_sail_dont_row_moved_into_thought_leadership below.
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


def _admin_client(appmod):
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app, raise_server_exceptions=True)
    c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    return c


def test_features_section_no_longer_exists(env):
    group_names = [gname for gname, _, _ in env._ADMIN_GROUPS]
    assert "Features" not in group_names


def test_fpa_buddy_is_no_longer_a_top_level_admin_group(env):
    """Superseded by the later Admin Nav Restructure: FP&A Buddy moved from
    a standalone top-level _ADMIN_GROUPS entry into a nested sub-group under
    CFO Toolbox — see test_admin_nav_restructure_phase6.py for the current
    nesting/badge/rendering assertions."""
    group_names = [gname for gname, _, _ in env._ADMIN_GROUPS]
    assert "FP&A Buddy" not in group_names


def test_fpa_buddy_tools_list_has_all_expected_pages(env):
    # The 4th entry (/admin/exa-settings) added in Phase 7 was merged into
    # the System group's single /admin/system/ai card by the admin AI-page
    # consolidation (PR 10) — see tests/test_admin_ai_settings.py for that
    # page's own coverage. This test now pins the original Phase 6 three.
    hrefs = [href for href, _, _ in env._FPA_BUDDY_TOOLS]
    assert hrefs == [
        "/tools/fpa-buddy/how-it-works",
        "/admin/fpa-buddy/report",
        "/admin/fpa-buddy/feedback",
    ]


def test_sail_dont_row_moved_into_thought_leadership(env):
    """Superseded by the Admin URL restructure (group A): Sail, Don't Row
    settings moved a second time, out of CFO Toolbox into Thought
    leadership, alongside Third-party content and Original content — its
    URL moved with it (/admin/game-settings -> /admin/thought-leadership/
    game-settings), so the card had to move too rather than point a CFO
    Toolbox card at a Thought leadership URL."""
    tl_groups = [items for gname, _, items in env._ADMIN_GROUPS if gname == "Thought leadership"]
    assert len(tl_groups) == 1
    hrefs = [href for href, _, _ in tl_groups[0]]
    assert "/admin/thought-leadership/game-settings" in hrefs
    # Wasn't duplicated — it should appear in Thought leadership and nowhere else.
    for gname, _, items in env._ADMIN_GROUPS:
        if gname != "Thought leadership":
            assert "/admin/thought-leadership/game-settings" not in [href for href, _, _ in items]


def test_no_route_appears_in_two_sections(env):
    """None of the four moved pages (or anything else) got duplicated across
    sections during the reorg — every href in _ADMIN_GROUPS is unique."""
    all_hrefs = [href for _, _, items in env._ADMIN_GROUPS for href, _, _ in items]
    assert len(all_hrefs) == len(set(all_hrefs))


def test_admin_hub_renders_new_section_and_drops_features(env):
    c = _admin_client(env)
    resp = c.get("/admin")
    assert resp.status_code == 200
    body = resp.text
    assert "FP&amp;A Buddy" in body
    assert ">Features<" not in body


def test_all_four_moved_routes_still_resolve_at_the_same_urls(env):
    """The reorg only changes section placement — none of the four pages'
    own routes or content should have moved."""
    c = _admin_client(env)
    for path in ("/tools/fpa-buddy/how-it-works", "/admin/fpa-buddy/report",
                 "/admin/fpa-buddy/feedback", "/admin/thought-leadership/game-settings"):
        resp = c.get(path)
        assert resp.status_code == 200, path


# --- 2026-09 grid reorder: Thought leadership + CFO Toolbox move to the
# left column, below Inbox; Brand/voice/content + System shift up to fill
# the vacated top-right slot. Display order only — no route/content change,
# confirmed above the section already covers that for the four moved pages
# and is untouched by this reorder. ---------------------------------------

def _group_heading_index(body: str, name: str) -> int:
    """Locate a top-level admin group's own heading span — not any other
    coincidental occurrence of the same text (e.g. CFO Toolbox also appears
    in the public nav bar, rendered earlier in the page than the admin
    groups themselves)."""
    marker = f'font-weight:600;">{name}</span>'
    idx = body.index(marker, body.index("<h1>Admin</h1>"))
    return idx


def test_admin_grid_reorder_renders_in_the_new_order(env):
    c = _admin_client(env)
    body = c.get("/admin").text
    order = ["Inbox", "Thought leadership", "CFO Toolbox",
             "Brand, voice, and content", "System"]
    indexes = [_group_heading_index(body, name) for name in order]
    assert indexes == sorted(indexes), (
        "expected admin groups to render in order "
        f"{order}, got index positions {indexes}"
    )
