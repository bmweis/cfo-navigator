"""The Reader box on /admin — the collapsible group that replaced the
standalone /admin/library page (PR 9, 2026-09).

Renamed from test_admin_library_layout.py, and rewritten on two axes.

**Location.** Everything here used to render on its own page. It's now a
nested group inside CFO Toolbox on /admin, so every test reads /admin. The
tests describing the old page's two-column `.lib-cols` flex layout, its
mobile `order` reflow, and its `align-items` axis-flip reset are gone
outright rather than ported: that layout existed to fill a full-width page,
and there's no width to split inside one column of /admin's own two-column
grid. See _reader_admin_quadrants()'s own comment.

**Hardcoded counts.** The old file (and its sibling in
test_feed_cookie_flag.py) asserted literal tool counts — "3 tools",
"2 tools", `count("<details") == 5`. Those broke in PR 520, again in 523,
again in 524, and would have broken again here: four PRs, four legitimate
structural changes, zero real bugs caught. They assert on CONTENTS now —
which tools are present, in which quadrant, in what order relative to each
other — which is the fact actually worth protecting and which survives a
sibling tool being added or removed.
"""
import os
import pathlib
import shutil
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

REPO_OPML = str(pathlib.Path(__file__).resolve().parents[1] / "preferred_sites.opml")


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


def _admin_html(appmod):
    with _admin_client(appmod) as client:
        return client.get("/admin").text


def _disclosure_body(html, label):
    """One <details> group's rendered HTML, found by its summary label and
    sliced by balancing <details>/</details> — not by running to the next
    label, which for the last group would swallow the rest of the page."""
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


QUADRANTS = ["New content", "Existing archive management", "Tag management"]


# --- the page is gone ------------------------------------------------------

def test_admin_library_page_is_gone_for_an_admin(env):
    """No redirect either — a hard 404, same cutover convention every other
    admin route move in this sprint used."""
    with _admin_client(env) as client:
        assert client.get("/admin/library", follow_redirects=False).status_code == 404


def test_admin_library_page_is_gone_signed_out(env):
    """Signed out it 404s too, rather than bouncing to /login and back to a
    page that no longer exists."""
    from fastapi.testclient import TestClient
    anon = TestClient(env.app)
    assert anon.get("/admin/library", follow_redirects=False).status_code == 404


def test_no_admin_page_still_links_to_the_retired_page(env):
    html = _admin_html(env)
    assert 'href="/admin/library"' not in html


# --- the box itself --------------------------------------------------------

def test_reader_group_renders_inside_cfo_toolbox(env):
    """Alongside the Software sub-group and the Communities card, not as a
    top-level group of its own."""
    html = _admin_html(env)
    toolbox = _disclosure_body(html, "CFO Toolbox")
    assert ">Reader</span>" in toolbox
    assert ">Software</span>" in toolbox
    assert 'href="/admin/tools/communities"' in toolbox


def test_every_disclosure_level_loads_collapsed(env):
    """The standing rule, checked at all three nesting levels at once: CFO
    Toolbox, the Reader group inside it, and each quadrant inside that. A
    single stray `open` anywhere in the chain would fail this."""
    html = _admin_html(env)
    assert "<details" in html
    assert "<details class=\"admin-group\" open" not in html
    for label in ["CFO Toolbox", "Reader"] + QUADRANTS:
        body = _disclosure_body(html, label)
        opening = body[:body.index(">") + 1]
        assert " open" not in opening, f"{label} renders expanded"


def test_all_three_quadrants_render_in_the_reader_group(env):
    html = _admin_html(env)
    reader = _disclosure_body(html, "Reader")
    for title in QUADRANTS:
        assert f">{title}</span>" in reader


def test_open_reader_is_a_link_inside_the_box(env):
    """It was a header action beside the retired page's <h1>. With no page
    left to head, it's the first thing inside the Reader group instead —
    above the quadrants, not buried in one of them."""
    html = _admin_html(env)
    reader = _disclosure_body(html, "Reader")
    assert 'href="/read" class="btn btn-ghost"' in reader
    assert "Open Reader" in reader
    assert reader.index("Open Reader") < reader.index(">New content</span>")


def test_open_reader_is_a_stock_navy_ghost_button(env):
    """BRAND.md allows exactly two button styles: navy fill and navy ghost.
    `.btn.btn-ghost` with no colour override is already navy border + navy
    text + transparent fill + navy-wash hover + 10px radius."""
    html = _admin_html(env)
    start = html.index('href="/read" class="btn btn-ghost"')
    button = html[start:html.index("</a>", start)]
    assert "seafoam" not in button
    assert "color:" not in button and "border-color:" not in button
    assert "background" not in button


def test_flow_diagram_is_still_gone(env):
    """The Historical-sweep/Archive-Queue flow diagram (_content_flow_diagram)
    was retired with the queue mechanism itself in PR 3 — there was no diagram
    left for PR 9 to shrink into the box or relocate."""
    html = _admin_html(env)
    assert "_content_flow_diagram" not in html
    assert "How new content reaches the archive" not in html
    assert "lib-top-row" not in html


def test_retired_page_layout_css_is_gone(env):
    """The two-column flex layout and its mobile reflow went with the page."""
    html = _admin_html(env)
    for rule in (".lib-cols{", ".lib-col{", ".lib-quad{", "lib-q-new", "lib-q-tags",
                 "lib-q-existing"):
        assert rule not in html


# --- contents, not counts --------------------------------------------------

def test_every_reader_tool_is_reachable_from_the_box(env):
    """The invariant that matters: no tool silently drops off the admin
    surface. Derived from _LIBRARY_TOOLS itself, so adding one is covered
    without editing this test."""
    import webapp.app as appmod
    html = _admin_html(env)
    reader = _disclosure_body(html, "Reader")
    for href, _title, _desc in appmod._LIBRARY_TOOLS:
        assert f'href="{href}"' in reader, href


def test_each_quadrant_holds_its_specified_tools(env):
    html = _admin_html(env)
    expected = {
        "New content": ["/admin/reader/feeds"],
        "Existing archive management": ["/admin/reader/backfill-content",
                                        "/admin/reader/dedupe",
                                        "/admin/reader/bulk-delete"],
        "Tag management": ["/admin/reader/tag-management", "/admin/reader/enrich"],
    }
    for label, hrefs in expected.items():
        body = _disclosure_body(html, label)
        for href in hrefs:
            assert f'href="{href}"' in body, (label, href)


def test_new_content_quadrant_holds_feeds_card_and_both_capture_pairs(env):
    """Merged, but the two halves stay distinct: a _lib_card over the existing
    accordion pattern, not one blended block. Both capture pairs (Archive and
    Read Later) live here."""
    html = _admin_html(env)
    quadrant = _disclosure_body(html, "New content")
    assert 'href="/admin/reader/feeds"' in quadrant
    assert "Saving to the archive" in quadrant
    assert "Saving to Read Later instead" in quadrant
    # Matched with the closing tag so the intro prose naming both capture
    # methods doesn't count as an extra accordion.
    assert quadrant.count("the bookmarklet</summary>") == 2
    assert quadrant.count("Share-Sheet shortcut</summary>") == 2
    assert "border-radius:14px" in quadrant                   # the _lib_card box


def test_capture_accordions_stay_expandable_and_closed(env):
    html = _admin_html(env)
    quadrant = _disclosure_body(html, "New content")
    for label in ("Desktop&mdash;the bookmarklet",
                  "iPhone / iPad&mdash;Share-Sheet shortcut"):
        at = quadrant.index(label)
        opening = quadrant[quadrant.rindex("<details", 0, at):at]
        assert " open" not in opening


def test_nested_capture_accordions_keep_the_item_level_variant(env):
    """Only the quadrant headers use the group-level component; the nested
    toggles keep their caret-before-label box. See BRAND.md UI components."""
    html = _admin_html(env)
    quadrant = _disclosure_body(html, "New content")
    nested = quadrant[quadrant.index("Desktop&mdash;the bookmarklet") - 700:]
    assert "disclosure-caret" in nested
    assert nested.index("disclosure-caret") < nested.index("Desktop&mdash;the bookmarklet")


def test_saving_articles_is_a_muted_label_not_a_competing_heading(env):
    """A small muted eyebrow rather than a bold navy h3 competing with the
    card headings above it. Checked for both capture pairs' labels."""
    html = _admin_html(env)
    quadrant = _disclosure_body(html, "New content")
    assert "<h3" not in quadrant
    for label_text in ("Saving to the archive", "Saving to Read Later instead"):
        label_at = quadrant.index(label_text)
        label = quadrant[label_at - 200:label_at]
        assert "font-size:12px" in label
        assert "color:var(--muted)" in label
        assert "var(--navy)" not in label


def test_token_warning_is_a_footnote_below_the_accordions(env):
    """Label, intro, accordions, then the warning as caption-weight text —
    below all four accordions (Archive pair + Read Later pair)."""
    html = _admin_html(env)
    quadrant = _disclosure_body(html, "New content")
    label = quadrant.index("Saving to the archive")
    first_accordion = quadrant.index("<details", quadrant.index("<details") + 1)
    warning = quadrant.index("If you ever rotate")
    last_accordion_end = quadrant.rindex("</details>", 0, warning)
    assert label < first_accordion < last_accordion_end < warning

    footnote = quadrant[warning - 200:warning]
    assert "font-size:12.5px" in footnote
    assert "color:var(--muted)" in footnote
    assert "<strong>If you ever rotate" not in quadrant
    assert "LINKLIB_SAVE_TOKEN" in quadrant and "LINKLIB_PUBLIC_BASE" in quadrant


# --- shared component ------------------------------------------------------

def test_quadrants_reuse_the_admin_index_disclosure_component(env):
    """Same `_disclosure_group` row as /admin's own sections — bordered box,
    bold all-caps label plus muted count on the left, caret right-aligned.
    Reused so the two surfaces can't drift."""
    import webapp.app as appmod
    html = _admin_html(env)
    reader = _disclosure_body(html, "Reader")

    rendered = appmod._disclosure_group("Tag management", "<p>x</p>",
                                        count_label="2 tools", nested=True)
    summary = rendered[rendered.index("<summary"):rendered.index("</summary>")]
    assert summary in reader

    assert "justify-content:space-between" in summary
    assert "text-transform:uppercase" in summary
    assert summary.index("text-transform:uppercase") < summary.index("disclosure-caret")
    assert "toggleQuad" not in html and "lib-quad-toggle" not in html


def test_reader_quadrants_and_admin_groups_share_one_component(env):
    """Guards the extraction: the quadrant builder and admin_page() must both
    come from _disclosure_group, not two copies of the same markup."""
    import inspect
    import webapp.app as appmod
    assert "_disclosure_group(" in inspect.getsource(appmod._reader_admin_quadrants)
    assert "_disclosure_group(" in inspect.getsource(appmod.admin_page)


def test_reader_group_badge_covers_every_reader_tool(env):
    """The aggregate badge is built from the same _LIBRARY_TOOLS hrefs the
    quadrants render, so a pending count can't go unrepresented on a
    collapsed group."""
    import inspect
    import webapp.app as appmod
    src = inspect.getsource(appmod.admin_page)
    assert "reader_hrefs = [href for href, _, _ in _LIBRARY_TOOLS]" in src
    assert "badge_hrefs=reader_hrefs" in src


# --- Manage feeds card -----------------------------------------------------

def test_manage_feeds_card_links_to_the_feed_admin_page(env):
    html = _admin_html(env)
    assert 'href="/admin/reader/feeds"' in html
    assert "Manage feeds" in html


def test_manage_feeds_card_reuses_the_link_card_style(env):
    """Same _lib_card component as the rest, not a bespoke box — same
    surface, border, radius, and trailing arrow."""
    html = _admin_html(env)
    card_start = html.index('href="/admin/reader/feeds"')
    card = html[card_start - 200:card_start + 700]
    assert "border-radius:14px" in card
    assert "&rarr;" in card


def test_the_box_is_admin_only(env):
    from fastapi.testclient import TestClient
    anon = TestClient(env.app)
    resp = anon.get("/admin", follow_redirects=False)
    assert resp.status_code in (302, 303, 307)


def test_archive_backup_lives_under_system_not_the_reader_box(env):
    """Reader route moves (PR 6, 2026-09): a whole-DB snapshot is
    accounts/health/plumbing, not archive management. System split into
    Configuration/Health and maintenance (PR 11, 2026-09) — Archive backup
    landed in Health and maintenance."""
    html = _admin_html(env)
    reader = _disclosure_body(html, "Reader")
    assert 'href="/admin/library-backup"' not in reader

    health = _disclosure_body(html, "Health and maintenance")
    assert 'href="/admin/library-backup"' in health
    assert "Archive backup" in health
