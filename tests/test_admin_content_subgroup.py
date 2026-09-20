"""The "Content" sub-group under "Brand, voice, and content" on /admin —
Homepage, About, How this is built, and AI surfaces nest inside it, the
same nested-`<details>` mechanism CFO Toolbox already uses for its own
Software/Community/FP&A Buddy/Reader sub-groups. Verbal identity, Email
templates, and Brand standards stay direct children of "Brand, voice, and
content" — they're voice/design standards, not content pages.

Follows the same "assert on contents, not hardcoded counts" discipline
test_admin_reader_box.py's own docstring documents — a raw count broke
repeatedly across earlier admin-nav PRs for exactly this reason.
"""
import os
import pathlib
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


def _admin_html(appmod):
    with _admin_client(appmod) as client:
        return client.get("/admin").text


def _disclosure_body(html, label):
    """Same balanced-<details> slicer test_admin_reader_box.py uses —
    finding a group by running to the next label's text would swallow
    everything up to it, wrong for a group that isn't last on the page."""
    start = html.rindex("<details", 0, html.index(f">{label}</span>"))
    depth, i = 0, start
    while i < len(html):
        nxt_open = html.find("<details", i + 1)
        nxt_close = html.find("</details>", i + 1)
        if nxt_close == -1:
            break
        if nxt_open != -1 and nxt_open < nxt_close:
            depth += 1
            i = nxt_open
        else:
            if depth == 0:
                return html[start:nxt_close]
            depth -= 1
            i = nxt_close
    raise AssertionError(f"unbalanced <details> around {label!r}")


CONTENT_PAGES = ["Homepage", "About", "How this is built", "AI surfaces"]
STANDARDS_CARDS = ["Verbal identity", "Email templates", "Brand standards"]


def test_content_subgroup_renders_inside_brand_voice_and_content(env):
    html = _admin_html(env)
    parent = _disclosure_body(html, "Brand, voice, and content")
    assert ">Content</span>" in parent


def test_all_four_content_pages_render_inside_the_content_subgroup(env):
    html = _admin_html(env)
    content = _disclosure_body(html, "Content")
    for title in CONTENT_PAGES:
        assert f">{title}</span>" in content, title


def test_standards_cards_stay_direct_children_not_nested(env):
    """Verbal identity/Email templates/Brand standards are voice/design
    standards, not content pages — they must render inside "Brand, voice,
    and content" directly, NOT inside the nested Content sub-group."""
    html = _admin_html(env)
    content = _disclosure_body(html, "Content")
    parent = _disclosure_body(html, "Brand, voice, and content")
    for title in STANDARDS_CARDS:
        assert f">{title}</span>" not in content, title
        assert f">{title}</span>" in parent, title


def test_content_subgroup_and_its_parent_load_collapsed(env):
    html = _admin_html(env)
    assert "<details" in html
    assert '<details class="admin-group" open' not in html
    for label in ["Brand, voice, and content", "Content"]:
        body = _disclosure_body(html, label)
        opening = body[: body.index(">") + 1]
        assert " open" not in opening, f"{label} renders expanded"


def test_content_tools_constant_has_the_four_expected_hrefs(env):
    hrefs = [href for href, _, _ in env._CONTENT_TOOLS]
    assert hrefs == [
        "/admin/copy/homepage",
        "/admin/copy/about",
        "/admin/copy/how-this-is-built",
        "/admin/ai-surfaces",
    ]


def test_brand_voice_and_content_group_keeps_only_the_three_standards_cards(env):
    """_ADMIN_GROUPS' own static tuple for this group should still list only
    the three original standards cards plus the voice review queue directly
    (the review queue, 2026-09, is a fourth genuine standards-adjacent page —
    a distinct concern from _CONTENT_TOOLS' four content pages, which stay
    out, spliced back in as a nested sub-group at render time inside
    admin_page() (same shape as _SOFTWARE_TOOLS/_COMMUNITIES_TOOLS/
    _FPA_BUDDY_TOOLS), not left as static items here)."""
    items = next(items for gname, _, items in env._ADMIN_GROUPS
                 if gname == "Brand, voice, and content")
    hrefs = [href for href, _, _ in items]
    assert hrefs == ["/admin/voice", "/admin/voice/review-queue", "/admin/emails", "/admin/brand"]


def test_hub_nav_orphans_clean_after_content_nesting(env):
    """The exact failure mode flagged before building this: adding a nested
    sub-group (_CONTENT_TOOLS split out of _ADMIN_GROUPS' static items)
    without also adding it to _hub_nav_all_hrefs() makes every one of its
    four pages a false-positive orphan, since the generic per-_ADMIN_GROUPS
    sweep in _hub_nav_all_hrefs() can no longer see them there."""
    assert env.hub_nav_orphans() == []
