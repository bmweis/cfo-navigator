"""/current-feed — the public mixtape-tracklist page listing the writers
and publications Brian actually reads, derived live from the `feeds`
table.

The property worth protecting: this page has NO hardcoded names and NO
hardcoded counts — everything renders from whatever is actually in the
`feeds` table at request time, driven by three per-feed columns
(show_on_current_feed, current_feed_side, current_feed_order), not by
section-name matching. Add a shown feed, it appears; drop one, it's gone;
a hidden feed never appears on either side, but is named in the page's own
footnote (grouped by section) with a plain disclosure that FP&A Buddy
still searches it — the exclusion is presentational only. Within a side,
tracks sort by current_feed_order ascending, tie-broken by feed id.
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
    """Boot the app against a temp DB and a temp OPML.

    LINKLIB_SITES_OPML must point at a scratch path — booting the app runs
    the seed-and-regenerate startup hook, which otherwise writes into the
    repo's own preferred_sites.opml on every test in this file (this fixture
    was missing that env var from when this page first shipped; every run
    of this suite was silently overwriting the tracked file until this was
    caught and fixed). See tests/test_feed_management.py's own fixture
    comment for the same convention.
    """
    db = str(tmp_path / "app.db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_SITES_OPML", str(tmp_path / "feeds.opml"))
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib
    import webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    client = TestClient(appmod.app)
    yield appmod, client


def _add(lib, section_id, name, xml, html, *, side="", order=0):
    fid = lib.add_feed(section_id, name, xml, html)
    if side:
        lib.set_feed_current_feed_display(fid, True, side, order)
    return fid


def _seed(appmod, *, old_school=(), new_school=(), hidden_section=None, hidden_feeds=()):
    """old_school/new_school are (name, xml, html) tuples shown on their
    respective side. hidden_feeds sit in `hidden_section` with
    show_on_current_feed left at its column default (0/unshown)."""
    lib = appmod._lib()
    try:
        blogs_id = lib.add_feed_section("Blogs")
        subs_id = lib.add_feed_section("Substacks")
        for name, xml, html in old_school:
            _add(lib, blogs_id, name, xml, html, side="old_school")
        for name, xml, html in new_school:
            _add(lib, subs_id, name, xml, html, side="new_school")
        if hidden_section:
            hidden_id = lib.add_feed_section(hidden_section)
            for name, xml, html in hidden_feeds:
                lib.add_feed(hidden_id, name, xml, html)
    finally:
        lib.close()


def _login(client):
    client.post("/login", data={"username": "admin", "password": "adminpass"})


# --- rendering, derived from live data -----------------------------------

def test_renders_both_sides_from_live_feeds(env):
    appmod, client = env
    _seed(
        appmod,
        old_school=[("Old Blog Writer", "https://oldblog.example/feed", "https://oldblog.example/")],
        new_school=[("New Substack Writer", "https://newsub.example/feed", "https://newsub.example/")],
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
    _seed(appmod, old_school=[("Kellblog", "http://kellblog.com/feed/", "https://www.kellblog.com/")])
    html = client.get("/current-feed").text
    assert 'href="https://www.kellblog.com/"' in html
    assert "kellblog.com/feed" not in html


def test_track_links_open_in_a_new_tab(env):
    appmod, client = env
    _seed(appmod, old_school=[("Old Blog Writer", "https://oldblog.example/feed", "https://oldblog.example/")])
    html = client.get("/current-feed").text
    assert 'href="https://oldblog.example/" target="_blank" rel="noopener"' in html


def test_new_feed_appears_once_marked_shown(env):
    """A feed only appears once deliberately marked shown — matching the
    "new feeds default to not shown" decision. No fixture pre-seeds this
    name anywhere in the app — it only exists because this test added it."""
    appmod, client = env
    _seed(appmod)
    lib = appmod._lib()
    try:
        sections = {s["name"]: s["id"] for s in lib.list_feed_sections()}
        fid = lib.add_feed(sections["Substacks"], "Brand New Writer",
                            "https://brandnew.example/feed", "https://brandnew.example/")
        # not yet marked shown — must not appear as a track
        html_before = client.get("/current-feed").text
        assert 'href="https://brandnew.example/"' not in html_before
        lib.set_feed_current_feed_display(fid, True, "new_school")
    finally:
        lib.close()
    html = client.get("/current-feed").text
    assert 'href="https://brandnew.example/"' in html


def test_new_feed_defaults_to_not_shown(env):
    """add_feed's own default, independent of the route above — a plain
    add with no explicit show/side argument must not appear as a track
    (it's still named in the footnote, since it's a real hidden source —
    see the footnote tests below)."""
    appmod, client = env
    lib = appmod._lib()
    try:
        sid = lib.add_feed_section("Blogs")
        lib.add_feed(sid, "Unreviewed Feed", "https://unreviewed.example/feed", "https://unreviewed.example/")
    finally:
        lib.close()
    html = client.get("/current-feed").text
    assert 'href="https://unreviewed.example/"' not in html


def test_removed_feed_disappears(env):
    appmod, client = env
    _seed(appmod, old_school=[("Soon Gone", "https://gone.example/feed", "https://gone.example/")])
    assert "Soon Gone" in client.get("/current-feed").text
    lib = appmod._lib()
    try:
        feed = lib.find_feed_by_url("https://gone.example/feed")
        lib.delete_feed(feed["id"])
    finally:
        lib.close()
    assert "Soon Gone" not in client.get("/current-feed").text


def test_unshowing_a_feed_removes_it_with_no_code_change(env):
    """The show flag itself is what's live — flip it off, the feed
    disappears from the tracklist without being deleted."""
    appmod, client = env
    _seed(appmod, old_school=[("Toggle Me", "https://toggle.example/feed", "https://toggle.example/")])
    assert 'href="https://toggle.example/"' in client.get("/current-feed").text
    lib = appmod._lib()
    try:
        feed = lib.find_feed_by_url("https://toggle.example/feed")
        lib.set_feed_current_feed_display(feed["id"], False, "")
    finally:
        lib.close()
    assert 'href="https://toggle.example/"' not in client.get("/current-feed").text


def test_empty_side_renders_gracefully(env):
    appmod, client = env
    _seed(appmod, old_school=[("Solo Blogger", "https://solo.example/feed", "https://solo.example/")])
    r = client.get("/current-feed")
    assert r.status_code == 200
    assert "Nothing here yet." in r.text


def test_renaming_a_feed_changes_how_it_shows_up(env):
    """Answers a direct question: the page has no separate display-name
    field — it renders feeds.name verbatim, so editing a feed's name (e.g.
    to the "Blog (Author)" convention) changes /current-feed immediately,
    with no code change."""
    appmod, client = env
    _seed(appmod, old_school=[("Kellblog", "http://kellblog.com/feed/", "https://www.kellblog.com/")])
    lib = appmod._lib()
    try:
        feed = lib.find_feed_by_url("http://kellblog.com/feed/")
        lib.update_feed(feed["id"], feed["section_id"], "Kellblog (Dave Kellogg)",
                         feed["xml_url"], feed["html_url"],
                         show_on_current_feed=True, current_feed_side="old_school")
    finally:
        lib.close()
    html = client.get("/current-feed").text
    assert "Kellblog (Dave Kellogg)" in html
    assert "Kellblog</a>" not in html


# --- the hidden-feed footnote ---------------------------------------------

def test_hidden_feeds_never_appear_on_a_side(env):
    appmod, client = env
    _seed(
        appmod,
        old_school=[("A Blogger", "https://a.example/feed", "https://a.example/")],
        hidden_section="News",
        hidden_feeds=[("Crunchbase News", "https://cb.example/feed", "https://cb.example/")],
    )
    html = client.get("/current-feed").text
    # named in the footnote, but not rendered as a track
    assert "Crunchbase News" in html
    assert 'href="https://cb.example/"' not in html


def test_footnote_names_hidden_sources_grouped_by_section(env):
    appmod, client = env
    _seed(
        appmod,
        old_school=[("A Blogger", "https://a.example/feed", "https://a.example/")],
        hidden_section="News",
        hidden_feeds=[
            ("Crunchbase News", "https://cb.example/feed", "https://cb.example/"),
            ("TechCrunch", "https://tc.example/feed", "https://tc.example/"),
        ],
    )
    lib = appmod._lib()
    try:
        market_id = lib.add_feed_section("Market Insights")
        lib.add_feed(market_id, "Public Comps", "https://pc.example/feed", "https://pc.example/")
    finally:
        lib.close()
    html = client.get("/current-feed").text
    assert "Not on the tape" in html
    assert "News: Crunchbase News, TechCrunch" in html
    assert "Market Insights: Public Comps" in html
    assert "FP&amp;A Buddy still searches every one of them" in html


def test_no_footnote_when_nothing_is_hidden(env):
    appmod, client = env
    _seed(
        appmod,
        old_school=[("A Blogger", "https://a.example/feed", "https://a.example/")],
        new_school=[("A Substacker", "https://b.example/feed", "https://b.example/")],
    )
    html = client.get("/current-feed").text
    assert "Not on the tape" not in html


def test_hidden_footnote_helper_directly(env):
    appmod, client = env
    feeds = [
        {"show_on_current_feed": 1, "section_name": "Blogs", "name": "Shown One"},
        {"show_on_current_feed": 0, "section_name": "News", "name": "TechCrunch"},
        {"show_on_current_feed": 0, "section_name": "News", "name": "Crunchbase News"},
        {"show_on_current_feed": 0, "section_name": "Market Insights", "name": "Public Comps"},
    ]
    html = appmod._current_feed_hidden_footnote(feeds)
    assert "Not on the tape" in html
    assert "News: Crunchbase News, TechCrunch" in html
    assert "Market Insights: Public Comps" in html
    assert "Shown One" not in html


def test_hidden_footnote_empty_when_nothing_hidden():
    import webapp.app as appmod
    assert appmod._current_feed_hidden_footnote([
        {"show_on_current_feed": 1, "section_name": "Blogs", "name": "Shown One"},
    ]) == ""


# --- typeface, coral, div balance, mechanical checks ----------------------

def test_track_names_use_the_sticker_typeface_not_the_wordmark(env):
    """Caveat (--font-sticker), not Permanent Marker (--font-wordmark) —
    per explicit direction: Permanent Marker is built for a word or two,
    not a list of varying-length names."""
    appmod, client = env
    _seed(appmod, old_school=[("A Blogger", "https://a.example/feed", "https://a.example/")])
    html = client.get("/current-feed").text
    assert "var(--font-sticker)" in appmod._CURRENT_FEED_CSS
    assert "var(--font-wordmark)" not in appmod._CURRENT_FEED_CSS
    # the nav logo legitimately uses --font-wordmark sitewide; this test
    # is specifically about the tracklist's own CSS, not the whole page
    assert html  # sanity: the route still rendered


def test_no_coral_on_the_side_divider(env):
    """The Side A/Side B split itself carries no coral — it's structure,
    not something to spend the page's one coral moment on."""
    appmod, client = env
    _seed(
        appmod,
        old_school=[("A Blogger", "https://a.example/feed", "https://a.example/")],
        new_school=[("A Substacker", "https://b.example/feed", "https://b.example/")],
    )
    html = client.get("/current-feed").text
    assert "cf-side-heading--flip" not in html


def test_div_tags_balance(env):
    appmod, client = env
    _seed(
        appmod,
        old_school=[("A Blogger", "https://a.example/feed", "https://a.example/")],
        new_school=[("A Substacker", "https://b.example/feed", "https://b.example/")],
        hidden_section="Tools",
        hidden_feeds=[("Mystery Feed", "https://mystery.example/feed", "https://mystery.example/")],
    )
    _login(client)
    html = client.get("/current-feed").text
    assert html.count("<div") == html.count("</div>")


def test_coral_moment_problems_clean(env):
    appmod, client = env
    _seed(
        appmod,
        old_school=[("A Blogger", "https://a.example/feed", "https://a.example/")],
        new_school=[("A Substacker", "https://b.example/feed", "https://b.example/")],
    )
    assert appmod.coral_moment_problems() == []


def test_page_index_assigns_a_recognized_tier(env):
    appmod, client = env
    rows = appmod._page_index_snapshot()
    row = next(r for r in rows if r["path"] == "/current-feed")
    assert not row["flagged"]
    assert row["tier"] == "page-standard"


# --- admin surfacing on /admin/reader/feeds -------------------------------

def test_admin_feeds_page_has_current_feed_column(env):
    appmod, client = env
    _seed(appmod, old_school=[("A Blogger", "https://a.example/feed", "https://a.example/")])
    _login(client)
    html = client.get("/admin/reader/feeds").text
    assert "Current Feed" in html
    assert 'name="current_feed"' in html
    assert 'value="old_school" selected' in html


def test_toggling_current_feed_from_the_admin_table(env):
    appmod, client = env
    lib = appmod._lib()
    try:
        sid = lib.add_feed_section("Blogs")
        fid = lib.add_feed(sid, "A Blogger", "https://a.example/feed", "https://a.example/")
    finally:
        lib.close()
    _login(client)
    r = client.post(f"/admin/reader/feeds/{fid}/current-feed", data={"current_feed": "old_school"})
    assert r.status_code in (200, 303)
    assert 'href="https://a.example/"' in client.get("/current-feed").text

    r2 = client.post(f"/admin/reader/feeds/{fid}/current-feed", data={"current_feed": ""})
    assert r2.status_code in (200, 303)
    assert 'href="https://a.example/"' not in client.get("/current-feed").text


def test_seed_current_feed_sides_from_existing_sections(lib):
    """The one-time seed that makes /current-feed work immediately on an
    existing database: Blogs -> old_school, Substacks -> new_school,
    everything else left at the column default (hidden)."""
    blogs_id = lib.add_feed_section("Blogs")
    subs_id = lib.add_feed_section("Substacks")
    news_id = lib.add_feed_section("News")
    lib.add_feed(blogs_id, "A Blogger", "https://a.example/feed", "https://a.example/")
    lib.add_feed(subs_id, "A Substacker", "https://b.example/feed", "https://b.example/")
    lib.add_feed(news_id, "A News Feed", "https://c.example/feed", "https://c.example/")

    result = lib.seed_current_feed_sides()
    assert result == {"seeded": True, "feeds": 2}

    feeds = {f["name"]: f for f in lib.list_feeds()}
    assert feeds["A Blogger"]["show_on_current_feed"] == 1
    assert feeds["A Blogger"]["current_feed_side"] == "old_school"
    assert feeds["A Substacker"]["show_on_current_feed"] == 1
    assert feeds["A Substacker"]["current_feed_side"] == "new_school"
    assert feeds["A News Feed"]["show_on_current_feed"] == 0
    assert feeds["A News Feed"]["current_feed_side"] == ""

    # settings-flagged: running again is a no-op even if something changed
    lib.set_feed_current_feed_display(feeds["A News Feed"]["id"], False, "")
    result2 = lib.seed_current_feed_sides()
    assert result2 == {"seeded": False, "feeds": 0}


def test_seed_current_feed_sides_does_not_resurrect_a_deliberate_change(lib):
    """A feed Brian deliberately hid must stay hidden across a re-seed —
    same non-emptiness-check discipline as seed_paywall_cookie_flags."""
    blogs_id = lib.add_feed_section("Blogs")
    fid = lib.add_feed(blogs_id, "A Blogger", "https://a.example/feed", "https://a.example/")
    lib.seed_current_feed_sides()
    assert lib.get_feed(fid)["show_on_current_feed"] == 1

    lib.set_feed_current_feed_display(fid, False, "")
    lib.seed_current_feed_sides()  # already flagged — must not re-run
    assert lib.get_feed(fid)["show_on_current_feed"] == 0


# --- display order ----------------------------------------------------------

def test_tracks_sort_by_current_feed_order_within_a_side(env):
    appmod, client = env
    lib = appmod._lib()
    try:
        sid = lib.add_feed_section("Blogs")
        _add(lib, sid, "Third", "https://c.example/feed", "https://c.example/",
             side="old_school", order=2)
        _add(lib, sid, "First", "https://a.example/feed", "https://a.example/",
             side="old_school", order=0)
        _add(lib, sid, "Second", "https://b.example/feed", "https://b.example/",
             side="old_school", order=1)
    finally:
        lib.close()
    html = client.get("/current-feed").text
    assert html.index("First") < html.index("Second") < html.index("Third")


def test_order_ties_fall_back_to_feed_id_and_stay_stable(env):
    """Two feeds sharing an order value must render in a fixed sequence —
    not whatever order SQLite happens to return them in — and that sequence
    must not change between requests."""
    appmod, client = env
    lib = appmod._lib()
    try:
        sid = lib.add_feed_section("Blogs")
        first_id = _add(lib, sid, "Tied First", "https://a.example/feed",
                         "https://a.example/", side="old_school", order=0)
        second_id = _add(lib, sid, "Tied Second", "https://b.example/feed",
                          "https://b.example/", side="old_school", order=0)
        assert first_id < second_id
    finally:
        lib.close()
    html1 = client.get("/current-feed").text
    html2 = client.get("/current-feed").text
    assert html1.index("Tied First") < html1.index("Tied Second")
    assert html1 == html2


def test_reordering_from_the_admin_table_changes_the_tracklist(env):
    appmod, client = env
    lib = appmod._lib()
    try:
        sid = lib.add_feed_section("Blogs")
        a_id = _add(lib, sid, "Alpha", "https://a.example/feed", "https://a.example/",
                    side="old_school", order=0)
        _add(lib, sid, "Beta", "https://b.example/feed", "https://b.example/",
             side="old_school", order=1)
    finally:
        lib.close()
    html = client.get("/current-feed").text
    assert html.index("Alpha") < html.index("Beta")

    _login(client)
    client.post(f"/admin/reader/feeds/{a_id}/current-feed",
                data={"current_feed": "old_school", "current_feed_order": "5"})
    html2 = client.get("/current-feed").text
    assert html2.index("Beta") < html2.index("Alpha")


def test_hidden_feeds_order_value_is_inert(env):
    """A hidden feed's order is stored (not cleared) but does nothing — it
    never renders as a track, hidden or shown, until show_on_current_feed
    flips on. (Its name still appears in the page's hidden-feed footnote,
    same as any other hidden feed — that's the existing disclosure, not
    something order affects.)"""
    appmod, client = env
    lib = appmod._lib()
    try:
        sid = lib.add_feed_section("Blogs")
        fid = lib.add_feed(sid, "Waiting In The Wings", "https://a.example/feed",
                            "https://a.example/")
        lib.set_feed_current_feed_display(fid, False, "", 99)
        assert lib.get_feed(fid)["current_feed_order"] == 99
    finally:
        lib.close()
    assert 'href="https://a.example/"' not in client.get("/current-feed").text


def test_admin_table_current_feed_order_field_present(env):
    """The Order input is visible and populated even for a Hidden feed —
    left visible-but-ignored rather than hidden by JS when the side dropdown
    reads Hidden, so a position can be set ahead of turning a feed on."""
    appmod, client = env
    lib = appmod._lib()
    try:
        sid = lib.add_feed_section("Blogs")
        shown_id = lib.add_feed(sid, "Shown One", "https://a.example/feed", "https://a.example/")
        lib.set_feed_current_feed_display(shown_id, True, "old_school", 3)
        hidden_id = lib.add_feed(sid, "Hidden One", "https://b.example/feed", "https://b.example/")
        lib.set_feed_current_feed_display(hidden_id, False, "", 7)
    finally:
        lib.close()
    _login(client)
    html = client.get("/admin/reader/feeds").text
    assert "Order</th>" in html
    assert 'name="current_feed_order"' in html
    assert f'form="cf-form-{shown_id}" value="3"' in html
    # Hidden feed's stored order still renders in the field, not blanked.
    assert f'form="cf-form-{hidden_id}" value="7"' in html


def test_add_edit_form_has_current_feed_order_field(env):
    appmod, client = env
    lib = appmod._lib()
    try:
        lib.add_feed_section("Blogs")
    finally:
        lib.close()
    _login(client)
    add_html = client.get("/admin/reader/feeds/new").text
    assert 'name="current_feed_order"' in add_html
    assert 'value="0"' in add_html


def test_new_feed_can_be_created_with_an_explicit_order(env, monkeypatch):
    from linklib.feed import FeedProbe
    monkeypatch.setattr("linklib.feed.probe_feed",
                        lambda url, **k: FeedProbe(True, title="New With Order",
                                                   html_url="https://new.example/"))
    appmod, client = env
    lib = appmod._lib()
    try:
        sid = lib.add_feed_section("Blogs")
    finally:
        lib.close()
    _login(client)
    r = client.post("/admin/reader/feeds/new", data={
        "xml_url": "https://new.example/feed", "name": "New With Order",
        "section_id": str(sid), "current_feed": "old_school",
        "current_feed_order": "42",
    }, follow_redirects=False)
    assert r.status_code in (200, 303), r.text
    lib = appmod._lib()
    try:
        feed = lib.find_feed_by_url("https://new.example/feed")
    finally:
        lib.close()
    assert feed["current_feed_order"] == 42


def test_edit_form_round_trips_current_feed_order(env):
    """A save that doesn't touch Order must not silently reset it to 0 — the
    edit form has to carry the current value forward, same contract as
    update_feed's other current_feed_* fields."""
    appmod, client = env
    lib = appmod._lib()
    try:
        sid = lib.add_feed_section("Blogs")
        fid = lib.add_feed(sid, "Editable", "https://a.example/feed", "https://a.example/")
        lib.set_feed_current_feed_display(fid, True, "old_school", 8)
    finally:
        lib.close()
    _login(client)
    edit_html = client.get(f"/admin/reader/feeds/{fid}/edit").text
    assert 'value="8"' in edit_html
    client.post(f"/admin/reader/feeds/{fid}/edit", data={
        "xml_url": "https://a.example/feed", "name": "Editable Renamed",
        "section_id": str(sid), "current_feed": "old_school",
        "current_feed_order": "8",
    }, follow_redirects=False)
    lib = appmod._lib()
    try:
        feed = lib.get_feed(fid)
    finally:
        lib.close()
    assert feed["current_feed_order"] == 8
    assert feed["name"] == "Editable Renamed"


def test_seed_current_feed_order_matches_existing_render_order(lib):
    """Seeding must not move anything — it numbers shown feeds within each
    side in the exact order they already rendered in (list_feeds()'s own
    section/feed ordering), before this column ever existed."""
    blogs_id = lib.add_feed_section("Blogs")
    subs_id = lib.add_feed_section("Substacks")
    a = lib.add_feed(blogs_id, "A Blogger", "https://a.example/feed", "https://a.example/")
    b = lib.add_feed(blogs_id, "B Blogger", "https://b.example/feed", "https://b.example/")
    c = lib.add_feed(subs_id, "A Substacker", "https://c.example/feed", "https://c.example/")
    lib.seed_current_feed_sides()

    result = lib.seed_current_feed_order()
    assert result["seeded"] is True
    assert result["feeds"] == 3

    feeds = {f["id"]: f for f in lib.list_feeds()}
    assert feeds[a]["current_feed_order"] == 0
    assert feeds[b]["current_feed_order"] == 1
    assert feeds[c]["current_feed_order"] == 0  # its own side, numbered independently

    # settings-flagged — a later reorder must survive a re-run
    lib.set_feed_current_feed_display(a, True, "old_school", 99)
    result2 = lib.seed_current_feed_order()
    assert result2 == {"seeded": False, "feeds": 0}
    assert lib.get_feed(a)["current_feed_order"] == 99


def test_seed_current_feed_order_noops_when_nothing_shown(lib):
    lib.add_feed_section("Blogs")
    result = lib.seed_current_feed_order()
    assert result == {"seeded": False, "feeds": 0}
