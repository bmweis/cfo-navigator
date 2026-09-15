"""/current-feed — the public mixtape-tracklist page listing the writers
and publications Brian actually reads, derived live from the `feeds`
table.

The property worth protecting: this page has NO hardcoded names and NO
hardcoded counts — everything renders from whatever is actually in the
`feeds`/`feed_sections` tables at request time. Add a feed to Blogs or
Substacks, it appears; drop one, it's gone; add one to a third,
unrecognized section, and it's visibly flagged for an admin rather than
silently disappearing.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Library


@pytest.fixture
def lib(tmp_path):
    db = Library(str(tmp_path / "t.db"))
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def env(monkeypatch, tmp_path):
    db = str(tmp_path / "app.db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib
    import webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    client = TestClient(appmod.app)
    yield appmod, client


def _seed(appmod, *, blogs=(), substacks=(), other_section=None, other_feeds=()):
    lib = appmod._lib()
    try:
        blogs_id = lib.add_feed_section("Blogs")
        subs_id = lib.add_feed_section("Substacks")
        for name, xml, html in blogs:
            lib.add_feed(blogs_id, name, xml, html)
        for name, xml, html in substacks:
            lib.add_feed(subs_id, name, xml, html)
        if other_section:
            other_id = lib.add_feed_section(other_section)
            for name, xml, html in other_feeds:
                lib.add_feed(other_id, name, xml, html)
    finally:
        lib.close()


def _login(client):
    client.post("/login", data={"username": "admin", "password": "adminpass"})


# --- rendering, derived from live data -----------------------------------

def test_renders_both_sides_from_live_feeds(env):
    appmod, client = env
    _seed(
        appmod,
        blogs=[("Old Blog Writer", "https://oldblog.example/feed", "https://oldblog.example/")],
        substacks=[("New Substack Writer", "https://newsub.example/feed", "https://newsub.example/")],
    )
    r = client.get("/current-feed")
    assert r.status_code == 200
    html = r.text
    assert "Old Blog Writer" in html
    assert "New Substack Writer" in html
    assert "Old School" in html
    assert "New School" in html
    assert "Side A" in html
    assert "Side B" in html


def test_links_go_to_the_homepage_not_the_raw_feed(env):
    """Every track links to html_url, never xml_url — a visitor should land
    on a readable page, not an RSS/Atom dump."""
    appmod, client = env
    _seed(
        appmod,
        blogs=[("Kellblog", "http://kellblog.com/feed/", "https://www.kellblog.com/")],
    )
    html = client.get("/current-feed").text
    assert 'href="https://www.kellblog.com/"' in html
    assert "kellblog.com/feed" not in html


def test_track_links_open_in_a_new_tab(env):
    appmod, client = env
    _seed(appmod, blogs=[("Old Blog Writer", "https://oldblog.example/feed", "https://oldblog.example/")])
    html = client.get("/current-feed").text
    assert 'href="https://oldblog.example/" target="_blank" rel="noopener"' in html


def test_new_feed_appears_with_no_code_change(env):
    """The core promise: add a feed, it shows up. No fixture pre-seeds this
    name anywhere in the app — it only exists because this test added it."""
    appmod, client = env
    _seed(appmod)
    lib = appmod._lib()
    try:
        sections = {s["name"]: s["id"] for s in lib.list_feed_sections()}
        lib.add_feed(sections["Substacks"], "Brand New Writer",
                     "https://brandnew.example/feed", "https://brandnew.example/")
    finally:
        lib.close()
    html = client.get("/current-feed").text
    assert "Brand New Writer" in html


def test_removed_feed_disappears(env):
    appmod, client = env
    _seed(appmod, blogs=[("Soon Gone", "https://gone.example/feed", "https://gone.example/")])
    assert "Soon Gone" in client.get("/current-feed").text
    lib = appmod._lib()
    try:
        feed = lib.find_feed_by_url("https://gone.example/feed")
        lib.delete_feed(feed["id"])
    finally:
        lib.close()
    assert "Soon Gone" not in client.get("/current-feed").text


def test_empty_side_renders_gracefully(env):
    appmod, client = env
    _seed(appmod, blogs=[("Solo Blogger", "https://solo.example/feed", "https://solo.example/")])
    r = client.get("/current-feed")
    assert r.status_code == 200
    assert "Nothing here yet." in r.text


# --- section exclusion ----------------------------------------------------

def test_news_and_market_insights_never_appear(env):
    appmod, client = env
    _seed(
        appmod,
        blogs=[("A Blogger", "https://a.example/feed", "https://a.example/")],
        other_section="News",
        other_feeds=[("Crunchbase News", "https://cb.example/feed", "https://cb.example/")],
    )
    lib = appmod._lib()
    try:
        market_id = lib.add_feed_section("Market Insights")
        lib.add_feed(market_id, "Public Comps", "https://pc.example/feed", "https://pc.example/")
    finally:
        lib.close()
    html = client.get("/current-feed").text
    assert "Crunchbase News" not in html
    assert "Public Comps" not in html


def test_unknown_section_is_invisible_to_an_anonymous_visitor(env):
    appmod, client = env
    _seed(
        appmod,
        blogs=[("A Blogger", "https://a.example/feed", "https://a.example/")],
        other_section="Tools",
        other_feeds=[("Mystery Feed", "https://mystery.example/feed", "https://mystery.example/")],
    )
    html = client.get("/current-feed").text
    assert "Mystery Feed" not in html
    assert "Admin only" not in html


def test_unknown_section_surfaces_admin_only_not_silently(env):
    """The brief's own gate: an unknown section must be VISIBLE, not silent.
    It's visible to an admin as a named, counted banner rather than the
    feed itself appearing on either side (which would be a guess this code
    has no basis for making)."""
    appmod, client = env
    _seed(
        appmod,
        blogs=[("A Blogger", "https://a.example/feed", "https://a.example/")],
        other_section="Tools",
        other_feeds=[("Mystery Feed", "https://mystery.example/feed", "https://mystery.example/")],
    )
    _login(client)
    html = client.get("/current-feed").text
    assert "Admin only" in html
    assert "Tools (1 feed)" in html
    # still doesn't guess which side it belongs on
    assert "Mystery Feed" not in html


def test_unknown_section_detector_directly(env):
    appmod, client = env
    feeds = [
        {"section_name": "Blogs"},
        {"section_name": "Substacks"},
        {"section_name": "News"},
        {"section_name": "Market Insights"},
        {"section_name": "Tools"},
        {"section_name": "Tools"},
        {"section_name": "Podcasts"},
    ]
    result = appmod._current_feed_unknown_sections(feeds)
    assert result == [("Podcasts", 1), ("Tools", 2)]


def test_no_unknown_sections_no_banner(env):
    appmod, client = env
    _seed(appmod, blogs=[("A Blogger", "https://a.example/feed", "https://a.example/")])
    _login(client)
    html = client.get("/current-feed").text
    assert "Admin only" not in html


# --- coral, div balance, mechanical checks --------------------------------

def test_exactly_one_coral_flip_marker(env):
    appmod, client = env
    _seed(
        appmod,
        blogs=[("A Blogger", "https://a.example/feed", "https://a.example/")],
        substacks=[("A Substacker", "https://b.example/feed", "https://b.example/")],
    )
    html = client.get("/current-feed").text
    # counted on the <h2 class="..."> element itself, not the CSS rule
    # in the page's own <style> block (which also mentions the class name)
    assert html.count('class="cf-side-heading cf-side-heading--flip"') == 1


def test_div_tags_balance(env):
    appmod, client = env
    _seed(
        appmod,
        blogs=[("A Blogger", "https://a.example/feed", "https://a.example/")],
        substacks=[("A Substacker", "https://b.example/feed", "https://b.example/")],
        other_section="Tools",
        other_feeds=[("Mystery Feed", "https://mystery.example/feed", "https://mystery.example/")],
    )
    _login(client)
    html = client.get("/current-feed").text
    assert html.count("<div") == html.count("</div>")


def test_coral_moment_problems_clean(env):
    appmod, client = env
    _seed(
        appmod,
        blogs=[("A Blogger", "https://a.example/feed", "https://a.example/")],
        substacks=[("A Substacker", "https://b.example/feed", "https://b.example/")],
    )
    assert appmod.coral_moment_problems() == []


def test_page_index_assigns_a_recognized_tier(env):
    appmod, client = env
    rows = appmod._page_index_snapshot()
    row = next(r for r in rows if r["path"] == "/current-feed")
    assert not row["flagged"]
    assert row["tier"] == "page-standard"
