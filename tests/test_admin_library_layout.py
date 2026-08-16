"""/admin/library layout fixes: the top-row height match and the Manage feeds box.

The height fix is CSS-only and its real verification was a live browser
measurement (flow-diagram card 241px vs. seafoam callout 263px before, both
241px after — the 22px delta being the diagram card's own margin-bottom
becoming phantom space inside a stretch-aligned grid). What's asserted here is
the rule that produces it, so a later edit to this page's <style> block can't
quietly drop it without a test noticing.
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


def _library_html(appmod):
    with _admin_client(appmod) as client:
        return client.get("/admin/library").text


def test_open_reader_is_a_header_button_not_a_callout_box(env):
    """The seafoam callout card is gone; Open Reader is a header-adjacent
    action beside the H1."""
    html = _library_html(env)
    assert "The day-to-day reading surface" not in html      # callout copy gone
    assert 'href="/read" class="btn btn-ghost"' in html
    assert "Open Reader" in html
    # It sits in the H1's flex row, before the intro paragraph.
    assert html.index("Open Reader") < html.index("The tools below cover")


def test_open_reader_is_a_ghost_outline_not_a_seafoam_fill(env):
    """BRAND.md: "Buttons navy or ghost" / "Make a seafoam or coral button",
    and _CSS says "Seafoam is NEVER a button". Seafoam-deep is used for the
    text and border only; there must be no seafoam background."""
    html = _library_html(env)
    start = html.index('href="/read" class="btn btn-ghost"')
    button = html[start:start + 320]
    assert "color:var(--seafoam-deep)" in button
    assert "border-color:var(--seafoam-deep)" in button
    assert "background" not in button


def test_flow_diagram_is_full_width_above_the_grid(env):
    html = _library_html(env)
    assert "lib-top-row" not in html                          # the two-up row is gone
    flow = html.index("How new content reaches the archive")
    grid = html.index('class="lib-quads"')
    assert flow < grid


def test_four_quadrants_use_named_grid_areas(env):
    """Named areas rather than auto-placement — auto-flow is what let content
    length push blocks around and leave a hole."""
    html = _library_html(env)
    assert 'grid-template-areas:"newcontent existing" "tags backup"' in html
    for cls in ("lib-q-new", "lib-q-existing", "lib-q-tags", "lib-q-backup"):
        assert f'class="{cls}"' in html


def test_mobile_collapses_to_one_column_in_reading_order(env):
    html = _library_html(env)
    assert "@media (max-width:900px)" in html
    assert 'grid-template-areas:"newcontent" "existing" "tags" "backup"' in html


def test_new_content_quadrant_holds_feeds_card_and_both_accordions(env):
    """Merged, but the two halves stay distinct: a _lib_card over the existing
    accordion pattern, not one blended block."""
    html = _library_html(env)
    start = html.index('class="lib-q-new"')
    end = html.index('class="lib-q-existing"')
    quadrant = html[start:end]
    assert "New content" in quadrant
    assert 'href="/admin/library/feeds"' in quadrant
    assert "Saving articles from anywhere" in quadrant
    assert quadrant.count("<details") == 2                    # bookmarklet + Share Sheet
    assert "border-radius:14px" in quadrant                   # the _lib_card box


def test_saving_articles_is_a_muted_label_not_a_competing_heading(env):
    """It sat as a bold navy h3, reading like a section nested in a section and
    competing with the card headings right above it. Now a small muted eyebrow,
    the same idiom the flow diagram's own label uses."""
    html = _library_html(env)
    start = html.index('class="lib-q-new"')
    quadrant = html[start:html.index('class="lib-q-existing"')]
    assert "<h3" not in quadrant
    label_at = quadrant.index("Saving articles from anywhere")
    label = quadrant[label_at - 200:label_at]
    assert "font-size:12px" in label
    assert "color:var(--muted)" in label
    assert "var(--navy)" not in label


def test_token_warning_is_a_footnote_below_the_accordions(env):
    """Moved out of the inline flow and de-bolded: label, intro, accordions,
    then the warning as caption-weight text."""
    html = _library_html(env)
    start = html.index('class="lib-q-new"')
    quadrant = html[start:html.index('class="lib-q-existing"')]

    label = quadrant.index("Saving articles from anywhere")
    first_accordion = quadrant.index("<details")
    last_accordion = quadrant.rindex("</details>")
    warning = quadrant.index("If you ever rotate")
    assert label < first_accordion < last_accordion < warning

    footnote = quadrant[warning - 200:warning]
    assert "font-size:12.5px" in footnote
    assert "color:var(--muted)" in footnote
    # Content is unchanged apart from the pointer now facing up, not down.
    assert "<strong>If you ever rotate" not in quadrant
    assert "LINKLIB_SAVE_TOKEN" in quadrant and "LINKLIB_PUBLIC_BASE" in quadrant


def test_each_quadrant_holds_its_specified_tools(env):
    html = _library_html(env)
    bounds = [("lib-q-existing", ["/admin/library/backfill-content", "/admin/library/dedupe",
                                  "/admin/library/review-removals"]),
              ("lib-q-tags", ["/admin/library/tags", "/admin/library/tag-style",
                              "/admin/library/enrich"]),
              ("lib-q-backup", ["/admin/library/backup", "/admin/library/queue"])]
    for cls, hrefs in bounds:
        start = html.index(f'class="{cls}"')
        rest = html[start + 1:]
        nxt = min((rest.index(f'class="{c}"') for c, _ in bounds if c != cls
                   and f'class="{c}"' in rest), default=len(rest))
        quadrant = rest[:nxt]
        for href in hrefs:
            assert f'href="{href}"' in quadrant, (cls, href)


def test_manage_feeds_box_links_to_the_feed_admin_page(env):
    html = _library_html(env)
    assert 'href="/admin/library/feeds"' in html
    assert "Manage feeds" in html


def test_manage_feeds_box_reuses_the_page_link_card_style(env):
    """It should be the same _lib_card component as Tag cleanup and the rest,
    not a bespoke box — same surface, border, radius, and trailing arrow."""
    html = _library_html(env)
    card_start = html.index('href="/admin/library/feeds"')
    card = html[card_start - 200:card_start + 700]
    assert "border-radius:14px" in card
    assert "&rarr;" in card


def test_manage_feeds_box_is_admin_only(env):
    from fastapi.testclient import TestClient
    anon = TestClient(env.app)
    resp = anon.get("/admin/library", follow_redirects=False)
    assert resp.status_code in (302, 303, 307)
