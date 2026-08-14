"""Homepage Restructure phase: the homepage's consolidated Thought
Leadership section (flagship cards + "Recent highlights" one-per-type grid +
bullets, replacing both the old standalone card and the Phase 3 addendum's
separate recency-pin teaser), the repurposed "Feature on homepage" checkbox
that now selects each type's representative entry, the sidebar Toolbox panel
and admin-only Reader-access placeholder, and the "Speaking &amp; Events"
double-escaping fix on /thought-leadership.
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


def _client(appmod):
    from fastapi.testclient import TestClient
    return TestClient(appmod.app, raise_server_exceptions=True)


def _admin_client(appmod):
    c = _client(appmod)
    c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    return c


def test_homepage_has_one_consolidated_thought_leadership_section(env):
    html = _client(env).get("/").text
    # Exactly one section — old standalone card + old separate teaser are gone.
    # (">Thought Leadership<" alone would also match the top nav link, so
    # "What I write about" — the section's own unique heading — is the
    # reliable one-section signal here.)
    assert html.count("What I write about") == 1
    assert "See all Thought Leadership" in html
    assert 'href="/thought-leadership"' in html
    # The 3 flagship pieces, using the shared _tl_fcard/.tl-card treatment.
    assert "The Growth Engine Ratio" in html
    assert "Sail, Don&rsquo;t Row" in html
    assert "Connecting Claude to NetSuite" in html
    assert 'class="tl-card"' in html
    # Sail Don't Row correction: real playbook copy/link, not the design
    # file's arcade-game placeholder copy.
    assert "Play it" not in html
    assert "Read the playbook" in html
    assert ">Playbook<" in html
    assert ">Interactive<" not in html
    # Exact bullet copy, not rephrased.
    assert "AI in finance&mdash;separating signal from noise, tracking what&rsquo;s changing." in html
    assert ("Frameworks myself and others have built, real opinions, and stories from the "
            "trenches.") in html
    assert ("How to move from scorekeeper to strategic partner: stop reporting what happened, "
            "start shaping what&rsquo;s next.") in html
    assert ("Showing up for the finance community&mdash;hosting my own podcast, speaking on "
            "panels, co-chairing demo days and events.") in html
    assert "The F Suite" not in html


def test_toolbox_panel_present_and_matches_design(env):
    html = _client(env).get("/").text
    assert ">CFO Toolbox<" in html
    assert "Everything in the toolbox" in html
    assert "See the full toolbox" in html
    # Design-fidelity note: the Homepage Restructure design file this panel
    # was rebuilt from does not include a "building" sticker on it (unlike
    # the Phase-3-era panel it replaced) — its absence here is intentional,
    # not the regression the earlier Phase 3 sticker-drop was.
    assert "🚧 building" not in html


def test_reader_access_placeholder_admin_only(env):
    anon_html = _client(env).get("/").text
    assert "Reader access" not in anon_html

    c = _client(env)
    c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    admin_html = c.get("/").text
    assert "Reader access" in admin_html
    assert "Admin only" in admin_html
    assert "build pending" in admin_html


def test_add_and_edit_forms_have_feature_on_homepage_checkbox(env):
    c = _admin_client(env)
    add_html = c.get("/admin/thought-leadership/new").text
    assert 'name="featured_home"' in add_html
    assert "Feature on homepage" in add_html

    lib = env._lib()
    try:
        item_id = lib.add_thought_leadership("writing", "A Piece", "https://example.com/piece",
                                              "Forbes", "Jan 2026", "2026-01")
    finally:
        lib.close()
    edit_html = c.get(f"/admin/thought-leadership/{item_id}/edit").text
    assert 'name="featured_home"' in edit_html
    assert "Feature on homepage" in edit_html


def test_featured_home_checkbox_persists_via_add_and_edit_routes(env):
    c = _admin_client(env)
    c.post("/admin/thought-leadership/new", data={
        "type": "writing", "title": "Pinned Piece", "url": "https://example.com/pinned",
        "venue": "Forbes", "date_label": "Jan 2020", "display_order": "",
        "featured_home": "1",
    }, follow_redirects=False)
    lib = env._lib()
    try:
        items = lib.list_thought_leadership(type="writing")
    finally:
        lib.close()
    assert len(items) == 1
    assert items[0]["featured_home"] == 1

    item_id = items[0]["id"]
    c.post(f"/admin/thought-leadership/{item_id}/edit", data={
        "type": "writing", "title": "Pinned Piece", "url": "https://example.com/pinned",
        "venue": "Forbes", "date_label": "Jan 2020", "display_order": "0",
    }, follow_redirects=False)
    lib = env._lib()
    try:
        it = lib.get_thought_leadership(item_id)
    finally:
        lib.close()
    assert it["featured_home"] == 0


def test_representative_prefers_checked_entry_over_more_recent_unchecked(env):
    lib = env._lib()
    try:
        lib.add_thought_leadership("writing", "Newer Unchecked", "https://example.com/newer",
                                   "Forbes", "Aug 2026", "2026-08", featured_home=False)
        lib.add_thought_leadership("writing", "Older Checked", "https://example.com/older",
                                   "Forbes", "Jan 2020", "2020-01", featured_home=True)
    finally:
        lib.close()
    rep = env._lib()
    try:
        result = rep.get_thought_leadership_representative("writing")
    finally:
        rep.close()
    assert result["title"] == "Older Checked"


def test_representative_falls_back_to_most_recent_when_none_checked(env):
    lib = env._lib()
    try:
        lib.add_thought_leadership("podcast", "Old Pod", "https://example.com/old",
                                   "Cash Flow Show", "Jan 2020", "2020-01")
        lib.add_thought_leadership("podcast", "New Pod", "https://example.com/new",
                                   "Cash Flow Show", "Aug 2026", "2026-08")
    finally:
        lib.close()
    rep = env._lib()
    try:
        result = rep.get_thought_leadership_representative("podcast")
    finally:
        rep.close()
    assert result["title"] == "New Pod"


def test_representative_is_none_when_type_has_no_entries(env):
    lib = env._lib()
    try:
        result = lib.get_thought_leadership_representative("press")
    finally:
        lib.close()
    assert result is None


def test_two_checked_entries_same_type_most_recently_updated_wins(env):
    lib = env._lib()
    try:
        a_id = lib.add_thought_leadership("press", "A", "https://example.com/a",
                                          "TechCrunch", "Jan 2026", "2026-01", featured_home=True)
        lib.add_thought_leadership("press", "B", "https://example.com/b",
                                   "Forbes", "Feb 2026", "2026-02", featured_home=True)
        # Re-save A so it's now the most recently updated of the two checked entries.
        lib.update_thought_leadership(a_id, "press", "A", "https://example.com/a", "TechCrunch",
                                      "Jan 2026", "2026-01", "", False, 0, featured_home=True)
    finally:
        lib.close()
    rep = env._lib()
    try:
        result = rep.get_thought_leadership_representative("press")
    finally:
        rep.close()
    assert result["title"] == "A"


def test_homepage_type_breakdown_renders_representative_and_handles_empty_type(env):
    lib = env._lib()
    try:
        lib.add_thought_leadership("writing", "Featured Writing Piece", "https://example.com/w",
                                   "Forbes", "Jan 2026", "2026-01", featured_home=True)
        # No "speaking", "podcast", or "press" entries at all — those columns
        # must not break the page.
    finally:
        lib.close()
    resp = _client(env).get("/")
    assert resp.status_code == 200
    html = resp.text
    assert "Featured Writing Piece" in html
    assert 'href="https://example.com/w"' in html


def test_homepage_type_breakdown_empty_db_does_not_break_page(env):
    resp = _client(env).get("/")
    assert resp.status_code == 200
    assert ">Thought Leadership<" in resp.text


def test_hero_polish_avatar_size(env):
    html = _client(env).get("/").text
    assert "width:200px;height:200px" in html


def test_speaking_and_events_renders_without_double_escaping(env):
    html = _client(env).get("/thought-leadership").text
    assert "&amp;amp;" not in html
    assert "Speaking &amp; Events" in html  # correctly single-escaped in the raw HTML
