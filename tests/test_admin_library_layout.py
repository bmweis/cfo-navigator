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


# DOM order is column-major since the two-independent-columns change: left
# column (new, tags) then right column (existing, backup). Slicing a quadrant
# means stopping at whichever marker actually follows it, not at a fixed one.
_QUADRANT_ORDER = ["lib-q-new", "lib-q-tags", "lib-q-existing", "lib-q-backup"]


def _quadrant(html, cls):
    start = html.index(f'class="{cls}"')
    after = _QUADRANT_ORDER[_QUADRANT_ORDER.index(cls) + 1:]
    ends = [html.index(f'class="{c}"') for c in after if f'class="{c}"' in html]
    return html[start:min(ends)] if ends else html[start:]


def test_open_reader_is_a_header_button_not_a_callout_box(env):
    """The seafoam callout card is gone; Open Reader is a header-adjacent
    action beside the H1."""
    html = _library_html(env)
    assert "The day-to-day reading surface" not in html      # callout copy gone
    assert 'href="/read" class="btn btn-ghost"' in html
    assert "Open Reader" in html
    # It sits in the H1's flex row, before the intro paragraph.
    assert html.index("Open Reader") < html.index("The tools below cover")


def test_open_reader_is_a_stock_navy_ghost_button(env):
    """BRAND.md allows exactly two button styles: navy fill and navy ghost.
    An earlier round tinted this seafoam; `.btn.btn-ghost` with no colour
    override is already navy border + navy text + transparent + navy-wash
    hover + 10px radius."""
    html = _library_html(env)
    start = html.index('href="/read" class="btn btn-ghost"')
    button = html[start:html.index("</a>", start)]
    assert "seafoam" not in button
    assert "color:" not in button and "border-color:" not in button
    assert "background" not in button


def test_flow_diagram_is_full_width_above_the_columns(env):
    html = _library_html(env)
    assert "lib-top-row" not in html                          # the two-up row is gone
    flow = html.index("How new content reaches the archive")
    cols = html.index('class="lib-cols"')
    assert flow < cols


def test_layout_is_two_independent_columns_not_a_coupled_grid(env):
    """Reverses the earlier named-grid-areas approach: a real 2-row grid makes
    both cells in a row share that row's height, so expanding one quadrant
    pushed the next row down in BOTH columns. Column independence is the
    deliberate trade-off; row-2 heading alignment is no longer guaranteed."""
    html = _library_html(env)
    assert "grid-template-areas" not in html
    assert "grid-template-rows" not in html
    assert '.lib-cols{display:flex;gap:28px;align-items:flex-start;}' in html
    assert '.lib-col{flex:1 1 0;min-width:0;display:flex;flex-direction:column' in html
    for cls in ("lib-q-new", "lib-q-existing", "lib-q-tags", "lib-q-backup"):
        assert f'class="{cls}"' in html


def test_columns_pair_the_right_quadrants(env):
    """Left: New content then Tag management. Right: Existing archive
    management then Archive additions & backup."""
    html = _library_html(env)
    cols = html[html.index('class="lib-cols"'):]
    left = cols.index("lib-q-new")
    tags = cols.index("lib-q-tags")
    right = cols.index("lib-q-existing")
    backup = cols.index("lib-q-backup")
    assert left < tags < right < backup            # column-major DOM order


def test_mobile_collapses_to_one_column_in_reading_order(env):
    """DOM order is column-major (new, tags, existing, backup) but the required
    reading order is new, existing, tags, backup — `display:contents` on the
    column wrappers plus `order` is what interleaves them."""
    html = _library_html(env)
    assert "@media (max-width:900px)" in html
    assert ".lib-col{display:contents;}" in html
    for cls, order in (("lib-q-new", 1), ("lib-q-existing", 2),
                       ("lib-q-tags", 3), ("lib-q-backup", 4)):
        assert f".{cls}{{order:{order};}}" in html


def test_new_content_quadrant_holds_feeds_card_and_both_accordions(env):
    """Merged, but the two halves stay distinct: a _lib_card over the existing
    accordion pattern, not one blended block."""
    html = _library_html(env)
    quadrant = _quadrant(html, "lib-q-new")
    assert "New content" in quadrant
    assert 'href="/admin/library/feeds"' in quadrant
    assert "Saving articles from anywhere" in quadrant
    # 3 now: the collapsible quadrant itself plus bookmarklet + Share Sheet.
    assert quadrant.count("<details") == 3
    assert "border-radius:14px" in quadrant                   # the _lib_card box


def test_saving_articles_is_a_muted_label_not_a_competing_heading(env):
    """It sat as a bold navy h3, reading like a section nested in a section and
    competing with the card headings right above it. Now a small muted eyebrow,
    the same idiom the flow diagram's own label uses."""
    html = _library_html(env)
    quadrant = _quadrant(html, "lib-q-new")
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
    quadrant = _quadrant(html, "lib-q-new")

    label = quadrant.index("Saving articles from anywhere")
    # Skip the quadrant's own <details> wrapper; the capture accordions are the
    # two inside it.
    first_accordion = quadrant.index("<details", quadrant.index("<details") + 1)
    warning = quadrant.index("If you ever rotate")
    share_sheet_end = quadrant.index("</details>", quadrant.index("Share-Sheet shortcut"))
    assert label < first_accordion < share_sheet_end < warning

    footnote = quadrant[warning - 200:warning]
    assert "font-size:12.5px" in footnote
    assert "color:var(--muted)" in footnote
    # Content is unchanged apart from the pointer now facing up, not down.
    assert "<strong>If you ever rotate" not in quadrant
    assert "LINKLIB_SAVE_TOKEN" in quadrant and "LINKLIB_PUBLIC_BASE" in quadrant


def test_all_four_quadrants_are_collapsible_and_closed_by_default(env):
    """Landing on the page shows a tidy 2x2 of four header rows. Native
    <details> with no `open` attribute."""
    html = _library_html(env)
    grid = html[html.index('class="lib-cols"'):]
    assert grid.count('class="admin-group lib-quad"') == 4
    assert 'class="admin-group lib-quad" open' not in grid
    for title in ("New content", "Existing archive management",
                  "Tag management", "Archive additions &amp; backup"):
        assert f">{title}</span>" in grid


def test_quadrants_reuse_the_admin_index_disclosure_component(env):
    """Same `_disclosure_group` row as /admin's sections — bordered box, bold
    all-caps label plus muted count on the left, caret right-aligned — rather
    than a bespoke header. Reused so the two surfaces can't drift."""
    import webapp.app as appmod

    html = _library_html(env)
    grid = html[html.index('class="lib-cols"'):]

    # The component's own output, rendered directly, appears on the page.
    rendered = appmod._disclosure_group("Tag management", "<p>x</p>",
                                        count_label="3 tools",
                                        extra_class="lib-quad")
    summary = rendered[rendered.index("<summary"):rendered.index("</summary>")]
    assert summary in grid

    # Right-aligned caret and all-caps label are the component's doing.
    assert "justify-content:space-between" in summary
    assert "text-transform:uppercase" in summary
    assert summary.index("text-transform:uppercase") < summary.index("disclosure-caret")
    # No bespoke toggle script.
    assert "toggleQuad" not in html and "lib-quad-toggle" not in html


def test_admin_index_and_library_quadrants_share_one_component(env):
    """Guards the extraction: /admin's groups and the Library quadrants must
    both come from _disclosure_group, not two copies of the same markup."""
    import inspect
    import webapp.app as appmod

    src = inspect.getsource(appmod.admin_library)
    assert "_disclosure_group(" in src
    admin_src = inspect.getsource(appmod.admin_page)
    assert "_disclosure_group(" in admin_src


def test_nested_capture_accordions_keep_the_item_level_variant(env):
    """Only the quadrant headers move to the group-level component; the nested
    toggles keep their caret-before-label box. See BRAND.md UI components."""
    html = _library_html(env)
    quadrant = _quadrant(html, "lib-q-new")
    nested = quadrant[quadrant.index("Desktop&mdash;the bookmarklet") - 700:]
    assert "disclosure-caret" in nested
    # Item-level: caret precedes the label, no right-alignment.
    caret = nested.index("disclosure-caret")
    label = nested.index("Desktop&mdash;the bookmarklet")
    assert caret < label


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
