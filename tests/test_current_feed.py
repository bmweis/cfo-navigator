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
import re
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
    assert "Timeless Classics" in html
    assert "The New Generation" in html
    assert '<span class="cf-ab-box" aria-hidden="true">A</span>' in html
    assert '<span class="cf-ab-box" aria-hidden="true">B</span>' in html
    # the old standalone "Side A"/"Side B" eyebrow and the pre-cassette
    # "Old School"/"New School" copy are both gone, replaced by the boxed
    # letter beside the side's mixtape-theme name
    assert "Side A" not in html
    assert "Side B" not in html
    assert "Old School" not in html
    assert "New School" not in html


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
    assert "<li>News: Crunchbase News, TechCrunch</li>" in html
    assert "<li>Market Insights: Public Comps</li>" in html
    assert "time is finite&mdash;so the Buddy also pulls from a few other trusted sites" in html


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
    assert "<li>News: Crunchbase News, TechCrunch</li>" in html
    assert "<li>Market Insights: Public Comps</li>" in html
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
    # Search the page body only: the shared <head> CSS carries comments
    # (e.g. "Secondary format") that would otherwise match these names.
    html = html[html.index("<main"):]
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
    """The up/down arrows (admin_feeds_move_order), not a typed number, are
    now the only way to change a feed's position — see CLAUDE.md's Current
    Feed display-order note for why the old typed field was removed."""
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
    client.post(f"/admin/reader/feeds/{a_id}/order-move", data={"direction": "down"})
    html2 = client.get("/current-feed").text
    assert html2.index("Beta") < html2.index("Alpha")


def test_move_order_renumbers_the_whole_side_densely(env):
    """Every move renumbers the whole side to 0..N-1, not just the two
    swapped rows — the self-healing behavior that closes the exact bug a
    typed number input could produce (two feeds silently sharing one
    order value)."""
    appmod, client = env
    lib = appmod._lib()
    try:
        sid = lib.add_feed_section("Blogs")
        a_id = _add(lib, sid, "Alpha", "https://a.example/feed", "https://a.example/",
                    side="old_school", order=1)
        b_id = _add(lib, sid, "Beta", "https://b.example/feed", "https://b.example/",
                    side="old_school", order=1)   # duplicate, pre-existing
        c_id = _add(lib, sid, "Gamma", "https://c.example/feed", "https://c.example/",
                    side="old_school", order=5)
    finally:
        lib.close()
    _login(client)
    # Nudge Alpha (tied with Beta at order 1, but wins the (order, id)
    # tie-break since its id is lower) up — a no-op boundary move, since
    # it's already first — but it still forces the renumber.
    client.post(f"/admin/reader/feeds/{a_id}/order-move", data={"direction": "up"})
    lib = appmod._lib()
    try:
        orders = {f["id"]: f["current_feed_order"] for f in lib.list_feeds()}
    finally:
        lib.close()
    assert sorted(orders[i] for i in (a_id, b_id, c_id)) == [0, 1, 2]
    assert len(set(orders[i] for i in (a_id, b_id, c_id))) == 3   # no duplicates survive


def test_move_up_is_a_noop_at_the_top(env):
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
    _login(client)
    client.post(f"/admin/reader/feeds/{a_id}/order-move", data={"direction": "up"})
    html = client.get("/current-feed").text
    assert html.index("Alpha") < html.index("Beta")   # unchanged


def test_move_order_is_a_noop_for_a_hidden_feed(env):
    """A hidden feed's order is inert — a move request for it changes
    nothing, matching the arrows being disabled for it in the admin UI."""
    appmod, client = env
    lib = appmod._lib()
    try:
        sid = lib.add_feed_section("Blogs")
        fid = lib.add_feed(sid, "Waiting In The Wings", "https://a.example/feed",
                            "https://a.example/")
        lib.set_feed_current_feed_display(fid, False, "", 3)
    finally:
        lib.close()
    _login(client)
    r = client.post(f"/admin/reader/feeds/{fid}/order-move", data={"direction": "down"},
                    follow_redirects=False)
    assert r.status_code == 303
    lib = appmod._lib()
    try:
        assert lib.get_feed(fid)["current_feed_order"] == 3
    finally:
        lib.close()


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


def test_admin_table_shows_order_arrows_not_a_number_field(env):
    """The Order column is up/down arrows now (2026-09) — no typed number
    field survives, since a typed number auto-saved on every keystroke and
    could silently duplicate another feed's order value. A feed alone in
    its side has both arrows disabled (nothing to swap with); a Hidden
    feed has both disabled too, regardless of its stored order value."""
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
    assert '>Order<' in html   # the sortable <th> header (a <span> indicator follows the label)
    assert 'name="current_feed_order"' not in html
    assert 'type="number"' not in html
    # Both feeds are alone in their own state (Shown One is the only shown
    # feed on old_school; Hidden One is hidden) — every arrow is disabled,
    # so there's no order-move <form> to click for either one.
    assert f'/admin/reader/feeds/{shown_id}/order-move' not in html
    assert f'/admin/reader/feeds/{hidden_id}/order-move' not in html
    assert 'Move up: Shown One' in html
    assert 'Move down: Shown One' in html
    assert 'Move up: Hidden One' in html
    assert 'Move down: Hidden One' in html
    assert html.count("disabled") >= 4


def test_order_arrows_reflect_position_within_the_side(env):
    """Up is disabled for whichever feed is first, down for whichever is
    last — a real, non-boundary feed in the middle has both enabled."""
    appmod, client = env
    lib = appmod._lib()
    try:
        sid = lib.add_feed_section("Blogs")
        first_id = _add(lib, sid, "First", "https://a.example/feed", "https://a.example/",
                        side="old_school", order=0)
        mid_id = _add(lib, sid, "Middle", "https://b.example/feed", "https://b.example/",
                      side="old_school", order=1)
        last_id = _add(lib, sid, "Last", "https://c.example/feed", "https://c.example/",
                       side="old_school", order=2)
    finally:
        lib.close()
    _login(client)
    html = client.get("/admin/reader/feeds").text

    def _arrow_states(feed_id, name):
        """(up_disabled, down_disabled) for a feed, found via its own
        aria-labels — robust to either arrow being a disabled <button> with
        no <form> around it."""
        section_marker = f"/admin/reader/feeds/{feed_id}/section"
        i = html.index(section_marker)
        start = html.rindex('<tr class="ff-row"', 0, i)
        end = html.index("</tr>", i) + len("</tr>")
        row = html[start:end]
        up = re.search(rf'<button[^>]*aria-label="Move up: {re.escape(name)}"[^>]*>', row)
        down = re.search(rf'<button[^>]*aria-label="Move down: {re.escape(name)}"[^>]*>', row)
        return ("disabled" in up.group(0), "disabled" in down.group(0))

    assert _arrow_states(first_id, "First") == (True, False)    # up disabled, down enabled
    assert _arrow_states(mid_id, "Middle") == (False, False)    # both enabled
    assert _arrow_states(last_id, "Last") == (False, True)      # up enabled, down disabled


def test_order_column_is_sortable_by_side_then_position(env):
    """The real bug behind a 2026-09 report: a feed's row position in this
    admin table (Library.list_feeds() orders by section/feed id, not by
    current_feed_order) has no relationship to its actual position within
    its Current Feed side — a feed can sit near the bottom of the table
    while genuinely being rank 1 (order=0) on /current-feed, so a correctly-
    disabled up arrow reads as broken with nothing else to explain why.

    Fixed at the root (2026-09 follow-up) by making the Order column itself
    sortable: its data-order sort key groups by side first (old_school,
    then new_school, then Hidden) and only then by position within it — the
    exact sequence the arrows move a feed through. This pins that key
    against a deliberately out-of-table-order current_feed_order assignment
    — the exact shape of the real report (Fred Wilson's USV feed genuinely
    held order=0 while sitting mid-table by id) — and confirms the earlier
    "N of M" rank readout (retired once sorting made it redundant) is
    genuinely gone."""
    appmod, client = env
    lib = appmod._lib()
    try:
        sid = lib.add_feed_section("Blogs")
        # Added in this order (so table/id order is A,B,C,D,E), but C is
        # given the LOWEST current_feed_order — genuinely first on
        # /current-feed despite sitting third in the table.
        a = _add(lib, sid, "A", "https://a.example/feed", "https://a.example/", side="old_school", order=1)
        b = _add(lib, sid, "B", "https://b.example/feed", "https://b.example/", side="old_school", order=2)
        c = _add(lib, sid, "C", "https://c.example/feed", "https://c.example/", side="old_school", order=0)
        d = _add(lib, sid, "D", "https://d.example/feed", "https://d.example/", side="old_school", order=3)
        e = _add(lib, sid, "E", "https://e.example/feed", "https://e.example/", side="old_school", order=4)
    finally:
        lib.close()
    _login(client)
    html = client.get("/admin/reader/feeds").text

    def _row(feed_id):
        marker = f"/admin/reader/feeds/{feed_id}/section"
        i = html.index(marker)
        start = html.rindex('<tr class="ff-row"', 0, i)
        end = html.index("</tr>", i) + len("</tr>")
        return html[start:end]

    def _data_order(feed_id):
        m = re.search(r'data-order="([^"]+)"', _row(feed_id))
        return m.group(1)

    # data-order sorts C first (order=0), regardless of its table position —
    # the same value the arrows/renumbering logic already keys off.
    assert sorted([_data_order(x) for x in (a, b, c, d, e)]) == [
        _data_order(c), _data_order(a), _data_order(b), _data_order(d), _data_order(e)]

    # The old rank text ("N of M") is retired — sorting by Order answers the
    # same question directly now, so there's nothing left to render here.
    assert not re.search(r"\d+ of \d+", _row(a))

    # C's up arrow is correctly disabled, matching its real (order=0) rank —
    # not a bug, just previously invisible without a way to see its position.
    up = re.search(r'<button[^>]*aria-label="Move up: C"[^>]*>', _row(c))
    assert "disabled" in up.group(0)


def test_order_column_headers_are_clickable_and_carry_a_sort_indicator(env):
    """The six sortable columns (Name/Section/Cookie/Subscriber/Current
    Feed/Order) each get a clickable <th data-sort="..."> with a visible
    indicator span; URL and Actions have nothing worth sorting by and stay
    plain."""
    appmod, client = env
    lib = appmod._lib()
    try:
        sid = lib.add_feed_section("Blogs")
        _add(lib, sid, "A", "https://a.example/feed", "https://a.example/")
    finally:
        lib.close()
    _login(client)
    html = client.get("/admin/reader/feeds").text
    for field in ("name", "section", "cookie", "subscriber", "current-feed", "order"):
        assert f'data-sort="{field}"' in html
        assert f"ffSortBy('{field}')" in html
    thead_start = html.index("<thead>")
    thead_end = html.index("</thead>") + len("</thead>")
    thead = html[thead_start:thead_end]
    assert thead.count('class="ff-sort-ind"') == 6
    assert "function ffSortBy" in html
    assert "function ffApplySort" in html


def test_order_arrows_render_at_a_consistent_size_disabled_or_not(env):
    """2026-09 regression: a disabled arrow (a bare <button>, no wrapping
    <form>) rendered visibly taller than an enabled one (<form>-wrapped)
    in the same inline-flex row, because the container's default
    align-items:stretch let the two differently-boxed children diverge.
    Fixed by pinning align-items:center on the wrapping span — this pins
    that the fix is still in place, since the failure mode is invisible to
    a plain HTML-content assertion and only shows up as a rendered size
    difference. (2026-09 follow-up: the cell used to have an outer span
    wrapping both the arrows AND a rank readout; the rank readout is
    retired now that Order is sortable, leaving one plain arrow-wrapping
    span — this still pins align-items:center on it.)"""
    appmod, client = env
    lib = appmod._lib()
    try:
        sid = lib.add_feed_section("Blogs")
        first_id = _add(lib, sid, "First", "https://a.example/feed", "https://a.example/",
                        side="old_school", order=0)
        _add(lib, sid, "Last", "https://b.example/feed", "https://b.example/",
             side="old_school", order=1)
    finally:
        lib.close()
    _login(client)
    html = client.get("/admin/reader/feeds").text
    idx = html.index(f"/admin/reader/feeds/{first_id}/section")
    start = html.rindex('<tr class="ff-row"', 0, idx)
    end = html.index("</tr>", idx) + len("</tr>")
    row = html[start:end]
    order_cell_start = row.index('<td class="ff-order">')
    order_cell = row[order_cell_start:row.index("</td>", order_cell_start)]
    assert 'align-items:center' in order_cell


def test_add_edit_form_has_no_current_feed_order_field(env):
    """The typed Order field is gone from the add/edit forms entirely —
    reordering is the admin table's arrows' job now, not a form field's."""
    appmod, client = env
    lib = appmod._lib()
    try:
        lib.add_feed_section("Blogs")
    finally:
        lib.close()
    _login(client)
    add_html = client.get("/admin/reader/feeds/new").text
    assert 'name="current_feed_order"' not in add_html
    assert "Current Feed order" not in add_html


def test_new_feed_shown_on_a_side_is_appended_to_the_end(env, monkeypatch):
    """A brand-new feed has no arrows to click yet, so it lands at the end
    of its chosen side automatically."""
    from linklib.feed import FeedProbe
    monkeypatch.setattr("linklib.feed.probe_feed",
                        lambda url, **k: FeedProbe(True, title="New Feed",
                                                   html_url="https://new.example/"))
    appmod, client = env
    lib = appmod._lib()
    try:
        sid = lib.add_feed_section("Blogs")
        _add(lib, sid, "Already Here", "https://a.example/feed", "https://a.example/",
             side="old_school", order=0)
    finally:
        lib.close()
    _login(client)
    r = client.post("/admin/reader/feeds/new", data={
        "xml_url": "https://new.example/feed", "name": "New Feed",
        "section_id": str(sid), "current_feed": "old_school",
    }, follow_redirects=False)
    assert r.status_code in (200, 303), r.text
    lib = appmod._lib()
    try:
        feed = lib.find_feed_by_url("https://new.example/feed")
    finally:
        lib.close()
    assert feed["current_feed_order"] == 1   # appended after the one already there


def test_edit_form_save_keeps_order_when_side_is_unchanged(env):
    """A save that doesn't change Current Feed side must not disturb the
    stored order — there's no field to submit it through any more
    (reordering is the admin table's arrows' job), so the edit route has
    to preserve it itself whenever the side comes back unchanged."""
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
    assert "current_feed_order" not in edit_html
    client.post(f"/admin/reader/feeds/{fid}/edit", data={
        "xml_url": "https://a.example/feed", "name": "Editable Renamed",
        "section_id": str(sid), "current_feed": "old_school",
    }, follow_redirects=False)
    lib = appmod._lib()
    try:
        feed = lib.get_feed(fid)
    finally:
        lib.close()
    assert feed["current_feed_order"] == 8
    assert feed["name"] == "Editable Renamed"


def test_edit_form_save_appends_to_the_end_when_side_changes(env):
    """Moving a feed from Hidden to a real side (or between sides) via the
    edit form appends it to the end of the new side, rather than keeping a
    stale order value from wherever it used to be."""
    appmod, client = env
    lib = appmod._lib()
    try:
        sid = lib.add_feed_section("Blogs")
        _add(lib, sid, "Already Here", "https://a.example/feed", "https://a.example/",
             side="new_school", order=0)
        fid = lib.add_feed(sid, "Moving In", "https://b.example/feed", "https://b.example/")
        lib.set_feed_current_feed_display(fid, False, "", 99)   # hidden, stale order
    finally:
        lib.close()
    _login(client)
    client.post(f"/admin/reader/feeds/{fid}/edit", data={
        "xml_url": "https://b.example/feed", "name": "Moving In",
        "section_id": str(sid), "current_feed": "new_school",
    }, follow_redirects=False)
    lib = appmod._lib()
    try:
        feed = lib.get_feed(fid)
    finally:
        lib.close()
    assert feed["current_feed_order"] == 1   # appended after the one already there


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


# --- cassette J-card visual treatment (2026-09) ---------------------------

def test_tape_card_panel_wraps_both_sides(env):
    """The tracklist sits inside a single paper-card panel (.cf-tape-card)
    wrapping .cf-sides — the approved treatment (card only, no plastic
    case, per BRAND.md §4's new page-scoped exception)."""
    appmod, client = env
    _seed(
        appmod,
        old_school=[("Old Blog Writer", "https://oldblog.example/feed", "https://oldblog.example/")],
        new_school=[("New Substack Writer", "https://newsub.example/feed", "https://newsub.example/")],
    )
    html = client.get("/current-feed").text
    assert '<div class="cf-tape-card">' in html
    assert re.search(r'<div class="cf-tape-card">\s*<div class="cf-sides">', html)
    # no plastic-case markup anywhere — that half of the proposal was
    # explicitly not shipped
    assert "cf-tape-case" not in html
    assert "tape-case" not in html


def test_tape_card_css_has_tilt_shadow_and_mobile_flatten(env):
    appmod, client = env
    _seed(appmod, old_school=[("Old Blog Writer", "https://oldblog.example/feed", "https://oldblog.example/")])
    html = client.get("/current-feed").text
    assert ".cf-tape-card{" in html
    assert "transform:rotate(-0.6deg)" in html
    assert "box-shadow:" in html
    # mobile: tilt/shadow/rounding all flatten, no case to strip since none shipped
    assert "@media(max-width:430px)" in html
    mobile_block = html.split("@media(max-width:430px)", 1)[1]
    assert "transform:none" in mobile_block


def test_ruled_lines_are_dotted_not_solid(env):
    """Track dividers read as printed form ruling (dotted), not a plain
    solid content divider — darkened/thickened from an earlier, fainter
    mockup pass specifically so they don't read as "a weak border"."""
    appmod, client = env
    _seed(appmod, old_school=[("Old Blog Writer", "https://oldblog.example/feed", "https://oldblog.example/")])
    html = client.get("/current-feed").text
    assert "border-bottom:1.5px dotted" in html


def test_boxed_letter_sits_beside_the_side_name_on_one_line(env):
    """The strongest cassette cue (the boxed A/B letter) is kept, merged
    onto one line with the side's own heading — no standalone "Side A"
    eyebrow and no preprinted "Date/Time / Noise Reduction" form-label
    text (both cut on direct feedback: three labels for one side name)."""
    appmod, client = env
    _seed(appmod, old_school=[("Old Blog Writer", "https://oldblog.example/feed", "https://oldblog.example/")])
    html = client.get("/current-feed").text
    assert re.search(
        r'<div class="cf-side-header">\s*'
        r'<span class="cf-ab-box" aria-hidden="true">A</span>\s*'
        r'<h2 class="cf-side-heading">Timeless Classics</h2>',
        html,
    )
    assert "Date/Time" not in html
    assert "Noise Reduction" not in html
    assert "NOISE REDUCTION" not in html


def test_no_coral_on_the_cassette_treatment(env):
    """The panel and its shadow are achromatic, matching the black-ink-
    on-white-card reference — no coral moment spent on this page. Scoped
    to the page's own <div class="page page-standard">...</div> body, not
    the full response — the sitewide :root token block legitimately
    defines --coral/--coral-deep for every page and would false-positive
    a whole-response substring check."""
    appmod, client = env
    _seed(appmod, old_school=[("Old Blog Writer", "https://oldblog.example/feed", "https://oldblog.example/")])
    html = client.get("/current-feed").text
    body = html.split('<div class="page page-standard">', 1)[1].split("<style>", 1)[0]
    assert "coral" not in body.lower()


def test_admin_dropdown_uses_the_same_side_names_as_the_page(env):
    """One vocabulary in both places — the admin Current Feed dropdown on
    /admin/reader/feeds must say what /current-feed itself says, not the
    retired "Old school"/"New school" copy."""
    appmod, client = env
    _seed(appmod, old_school=[("Old Blog Writer", "https://oldblog.example/feed", "https://oldblog.example/")])
    _login(client)
    html = client.get("/admin/reader/feeds").text
    assert '<option value="old_school" selected>Timeless Classics</option>' in html
    assert '<option value="new_school">The New Generation</option>' in html
    assert "Old school" not in html
    assert "New school" not in html


# --- copy revision + "Last mixed" stamp (2026-09) --------------------------

def test_h1_is_sentence_case(env):
    """BRAND.md §3.2 — page titles are sentence case; "Current Feed" (title
    case) was the one holdout on this page."""
    appmod, client = env
    _seed(appmod, old_school=[("Old Blog Writer", "https://oldblog.example/feed", "https://oldblog.example/")])
    html = client.get("/current-feed").text
    assert "<h1" in html
    h1 = html[html.index("<h1"):html.index("</h1>") + 5]
    assert ">Current feed<" in h1
    assert ">Current Feed<" not in h1


def test_intro_copy_matches_the_approved_text(env):
    appmod, client = env
    _seed(appmod, old_school=[("Old Blog Writer", "https://oldblog.example/feed", "https://oldblog.example/")])
    html = client.get("/current-feed").text
    assert ("Remember that friend who had the best mixtape? The one you couldn't "
            "get enough of and seemed to have the best stuff you didn't know "
            "existed.") in html
    assert ("While I can&#x27;t give you direct access to my feed" in html
            or "While I can't give you direct access to my feed" in html)
    assert "split into two eras" in html
    assert "Every name links to the writer's own site, not the raw feed." in html
    assert "And the list is dynamic, changing as I change my own reading list." in html
    # the retired copy is gone, not just superseded
    assert "You know that friend whose mixtape you" not in html
    assert "I can't hand you my feed" not in html


def test_demo_track_cta_links_to_contact(env):
    appmod, client = env
    _seed(appmod, old_school=[("Old Blog Writer", "https://oldblog.example/feed", "https://oldblog.example/")])
    html = client.get("/current-feed").text
    assert re.search(
        r'Have something you think I should add to the list\? '
        r'<a href="/contact">Send me the demo track</a> and you might see it '
        r'show up on a future update\.',
        html,
    )


def test_stamp_shows_the_most_recently_added_feeds_date(env):
    """MAX(feeds.created_at) — the date a feed was ADDED, never touched by
    update_feed(). Editing an existing feed must not move the stamp."""
    appmod, client = env
    lib = appmod._lib()
    try:
        sid = lib.add_feed_section("Blogs")
        old_id = lib.add_feed(sid, "Old One", "https://old.example/feed", "https://old.example/")
        lib.conn.execute("UPDATE feeds SET created_at=? WHERE id=?", ("2020-01-01T00:00:00+00:00", old_id))
        new_id = lib.add_feed(sid, "New One", "https://new.example/feed", "https://new.example/")
        lib.conn.execute("UPDATE feeds SET created_at=? WHERE id=?", ("2026-09-15T12:00:00+00:00", new_id))
        lib.conn.commit()
        lib.set_feed_current_feed_display(old_id, True, "old_school", 0)
        lib.set_feed_current_feed_display(new_id, True, "old_school", 1)
        # editing the OLDER feed must not move the stamp forward
        lib.update_feed(old_id, sid, "Old One (renamed)", "https://old.example/feed",
                        "https://old.example/")
    finally:
        lib.close()
    html = client.get("/current-feed").text
    assert "Last mixed" in html
    assert "15 Sep 2026" in html


def test_stamp_is_a_write_on_cassette_label_in_the_lower_right(env):
    appmod, client = env
    _seed(appmod, old_school=[("Old Blog Writer", "https://oldblog.example/feed", "https://oldblog.example/")])
    html = client.get("/current-feed").text
    assert '<div class="cf-stamp-row"><div class="cf-stamp" aria-hidden="true">' in html
    assert '<span class="cf-stamp-label">Last mixed</span>' in html
    assert "justify-content:flex-end" in html   # sits in the tape card's lower-right
    assert ".cf-stamp{background:#fff;" in html


def test_stamp_is_absent_when_there_are_no_feeds(env):
    """No feeds at all -> no created_at data -> no stamp. This must never
    fall back to a different timestamp; it's real data or nothing."""
    appmod, client = env
    html = client.get("/current-feed").text
    assert "Last mixed" not in html
    assert 'class="cf-stamp-row"' not in html
    assert 'class="cf-stamp"' not in html


def test_stamp_date_helper_ignores_unparseable_created_at(env):
    import webapp.app as appmod
    assert appmod._current_feed_stamp_date([{"created_at": "not-a-date"}]) == ""
    assert appmod._current_feed_stamp_date([{"created_at": ""}]) == ""
    assert appmod._current_feed_stamp_date([]) == ""
    assert appmod._current_feed_stamp_date(
        [{"created_at": "2026-09-15T12:00:00+00:00"}]) == "15 Sep 2026"
