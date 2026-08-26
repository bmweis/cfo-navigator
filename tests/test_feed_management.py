"""Feed management: the feed_sections/feeds tables, OPML regeneration, and the
/admin/library/feeds CRUD page.

The load-bearing property this file protects is that preferred_sites.opml is
now GENERATED but still consumed unmodified by four separate systems
(feed.parse_opml, sources.preferred_domains, queue.scan_feed_into_queue,
authcheck). A generator that drifts from the hand-written format breaks the
Reader's Feed view and FP&A Buddy's web-search allowlist at the same time, with
no error anywhere — so the round-trip tests below compare the generated file
against the repo's own curated copy through both parsers, not just for
well-formedness.

Note the fixtures point LINKLIB_SITES_OPML at a tmp copy. Booting the app runs
the seed-and-regenerate startup hook, which would otherwise write into the
repo's working tree during a test run.
"""
import pathlib
import shutil
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Library
from linklib.feed import FeedProbe, parse_opml, probe_feed
from linklib.sources import preferred_domains

REPO_OPML = str(pathlib.Path(__file__).resolve().parents[1] / "preferred_sites.opml")


@pytest.fixture
def lib(tmp_path):
    db = Library(str(tmp_path / "t.db"))
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def seeded(lib):
    lib.seed_feeds_from_opml(REPO_OPML)
    return lib


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------

def test_seed_imports_every_section_and_feed(lib):
    result = lib.seed_feeds_from_opml(REPO_OPML)
    assert result["seeded"] is True
    assert result["feeds"] == len(parse_opml(REPO_OPML))
    assert [s["name"] for s in lib.list_feed_sections()] == [
        "News", "Market Insights", "Blogs", "Tools", "Substacks"]


def test_seed_marks_only_the_two_news_feeds_read_only(lib):
    """Preserves the exact pre-migration behavior of QUEUE_EXCLUDE_CATEGORIES,
    which defaulted to {"News"} — now expressed per feed rather than per
    section, so the set of excluded SOURCES must come out identical."""
    lib.seed_feeds_from_opml(REPO_OPML)
    assert lib.excluded_feed_urls() == {
        "https://news.crunchbase.com/sections/enterprise/feed/",
        "https://techcrunch.com/enterprise/feed/",
    }
    excluded_names = {f["name"] for f in lib.list_feeds() if f["exclude_from_queue"]}
    assert excluded_names == {"Enterprise Archives - Crunchbase News", "TechCrunch"}


def test_seed_runs_once_and_never_resurrects_a_deleted_feed(seeded):
    """The guard is a settings flag, not an emptiness check. An emptiness
    check would look identical on a fresh DB but re-import the whole file on
    the next restart, undoing a deliberate deletion — the exact bug
    _seed_toolbox shipped and had to fix."""
    feed = seeded.list_feeds()[0]
    seeded.delete_feed(feed["id"])

    again = seeded.seed_feeds_from_opml(REPO_OPML)

    assert again["seeded"] is False
    assert seeded.find_feed_by_url(feed["xml_url"]) is None


def test_seed_does_not_burn_the_flag_on_an_unreadable_file(lib, tmp_path):
    """A missing/broken OPML shouldn't permanently consume the one seed
    opportunity — a later boot with a readable file still gets its chance."""
    assert lib.seed_feeds_from_opml(str(tmp_path / "nope.opml"))["seeded"] is False
    assert lib.seed_feeds_from_opml(REPO_OPML)["seeded"] is True


# ---------------------------------------------------------------------------
# OPML regeneration — the round trip that protects all four consumers
# ---------------------------------------------------------------------------

def test_generated_opml_is_byte_identical_to_the_curated_repo_copy(seeded, tmp_path):
    out = tmp_path / "gen.opml"
    seeded.write_opml(str(out))
    assert out.read_text() == pathlib.Path(REPO_OPML).read_text()


def test_generated_opml_parses_identically_for_the_reader(seeded, tmp_path):
    out = tmp_path / "gen.opml"
    seeded.write_opml(str(out))
    shape = lambda fs: sorted((f.category, f.name, f.xml_url, f.html_url) for f in fs)
    assert shape(parse_opml(str(out))) == shape(parse_opml(REPO_OPML))


def test_generated_opml_yields_the_same_buddy_allowlist(seeded, tmp_path):
    out = tmp_path / "gen.opml"
    seeded.write_opml(str(out))
    assert preferred_domains(str(out)) == preferred_domains(REPO_OPML)


def test_section_outlines_never_carry_an_htmlurl(seeded, tmp_path):
    """sources.preferred_domains walks every outline at any depth and reads
    `htmlUrl or xmlUrl`, so an htmlUrl on a SECTION outline would inject a
    bogus domain into FP&A Buddy's web-search allowlist."""
    import xml.etree.ElementTree as ET

    out = tmp_path / "gen.opml"
    seeded.write_opml(str(out))
    body = ET.parse(str(out)).getroot().find("body")
    for section in body:
        assert section.get("htmlUrl") is None
        assert section.get("xmlUrl") is None


def test_write_opml_noops_on_an_empty_feeds_table(lib, tmp_path):
    """A fresh deploy must not overwrite the curated repo copy with an empty
    subscription list before the seed has run."""
    out = tmp_path / "gen.opml"
    shutil.copy(REPO_OPML, out)
    assert lib.write_opml(str(out)) is False
    assert out.read_text() == pathlib.Path(REPO_OPML).read_text()


def test_write_opml_skips_the_write_when_content_is_unchanged(seeded, tmp_path):
    out = tmp_path / "gen.opml"
    assert seeded.write_opml(str(out)) is True     # first write creates it
    assert seeded.write_opml(str(out)) is False    # second is a no-op


def test_write_opml_clears_the_buddy_allowlist_cache(seeded, tmp_path):
    """preferred_domains is @lru_cache'd and read once per process. Without
    this clear, a newly added source would be missing from FP&A Buddy's
    allowlist until the next deploy, silently."""
    out = tmp_path / "gen.opml"
    seeded.write_opml(str(out))
    before = preferred_domains(str(out))          # populates the cache
    assert "example.com" not in before

    section_id = seeded.list_feed_sections()[0]["id"]
    seeded.add_feed(section_id, "Example", "https://example.com/feed",
                    "https://example.com/")
    seeded.write_opml(str(out))

    assert "example.com" in preferred_domains(str(out))


def test_generated_opml_survives_ampersands_and_quotes_in_names(lib, tmp_path):
    out = tmp_path / "gen.opml"
    sid = lib.add_feed_section('Ops & "Finance"')
    lib.add_feed(sid, 'Smith & Co "Weekly"', "https://smithco.example/feed",
                 "https://smithco.example/")
    lib.write_opml(str(out))

    feeds = parse_opml(str(out))
    assert feeds[0].category == 'Ops & "Finance"'
    assert feeds[0].name == 'Smith & Co "Weekly"'


# ---------------------------------------------------------------------------
# Section / feed CRUD
# ---------------------------------------------------------------------------

def test_delete_section_refuses_while_it_still_holds_feeds(seeded):
    section = seeded.list_feed_sections()[0]
    with pytest.raises(ValueError):
        seeded.delete_feed_section(section["id"])
    assert seeded.get_feed_section(section["id"]) is not None


def test_delete_section_succeeds_once_empty(seeded):
    section = seeded.list_feed_sections()[0]
    for feed in seeded.list_feeds(section_id=section["id"]):
        seeded.delete_feed(feed["id"])
    seeded.delete_feed_section(section["id"])
    assert seeded.get_feed_section(section["id"]) is None


def test_renaming_a_section_does_not_change_queue_exclusion(seeded):
    """The whole point of moving exclusion off a name match."""
    before = seeded.excluded_feed_urls()
    news = [s for s in seeded.list_feed_sections() if s["name"] == "News"][0]
    seeded.rename_feed_section(news["id"], "Headlines")
    assert seeded.excluded_feed_urls() == before


def test_moving_a_feed_between_sections_does_not_change_its_exclusion(seeded):
    """Exclusion belongs to the feed, so regrouping must not disturb it."""
    feed = [f for f in seeded.list_feeds() if f["exclude_from_queue"]][0]
    target = [s for s in seeded.list_feed_sections() if s["name"] == "Blogs"][0]
    seeded.move_feed_to_section(feed["id"], target["id"])
    assert seeded.get_feed(feed["id"])["exclude_from_queue"] == 1
    assert feed["xml_url"] in seeded.excluded_feed_urls()


def test_one_feed_can_be_read_only_without_its_section_mates(seeded):
    blogs = [s for s in seeded.list_feed_sections() if s["name"] == "Blogs"][0]
    in_blogs = seeded.list_feeds(section_id=blogs["id"])
    seeded.set_feed_excluded(in_blogs[0]["id"], True)
    still_eligible = [f for f in seeded.list_feeds(section_id=blogs["id"])
                      if not f["exclude_from_queue"]]
    assert len(still_eligible) == len(in_blogs) - 1


def test_unseeded_db_is_distinguishable_from_nothing_excluded(lib):
    """An unseeded DB reporting an empty exclusion set would start funnelling
    News into the archive queue. has_feeds() is what separates the two."""
    assert lib.has_feeds() is False
    assert lib.excluded_feed_urls() == set()


def test_moving_a_feed_between_sections_moves_it_in_the_opml(seeded, tmp_path):
    feed = seeded.list_feeds()[0]
    target = [s for s in seeded.list_feed_sections() if s["id"] != feed["section_id"]][0]
    seeded.update_feed(feed["id"], target["id"], feed["name"],
                       feed["xml_url"], feed["html_url"])

    out = tmp_path / "gen.opml"
    seeded.write_opml(str(out))
    moved = [f for f in parse_opml(str(out)) if f.xml_url == feed["xml_url"]][0]
    assert moved.category == target["name"]


# ---------------------------------------------------------------------------
# Verbatim URL round trip
#
# Some feeds are paid subscriptions whose feed URL carries a per-subscriber
# token. Nothing in the seed -> DB -> OPML path may normalize, trim, re-encode,
# or otherwise touch that string: a rewritten token is a silently dead feed.
#
# Note on the current data: as of this writing NO feed in preferred_sites.opml
# actually has a query string — Mostly Metrics is stored as the plain
# https://www.mostlymetrics.com/feed, and its paywall is handled by a cookie
# (LINKLIB_COOKIE_MOSTLYMETRICS_COM, applied in extract.fetch_page), not by a URL token.
# These tests therefore pin both: the real stored string, and a synthetic
# tokenized URL that proves the guarantee holds if one is ever added.
# ---------------------------------------------------------------------------

MOSTLY_METRICS_URL = "https://www.mostlymetrics.com/feed"

TOKENIZED = ("https://www.example-paid.com/feed?auth_token=abc123XYZ"
             "&user_id=42&format=rss")


def test_mostly_metrics_url_survives_seed_to_db_to_opml_unchanged(lib, tmp_path):
    """Pins the exact stored string end to end."""
    stored_in_file = [f.xml_url for f in parse_opml(REPO_OPML)
                      if "mostlymetrics" in f.xml_url]
    assert stored_in_file == [MOSTLY_METRICS_URL]

    lib.seed_feeds_from_opml(REPO_OPML)
    in_db = lib.find_feed_by_url(MOSTLY_METRICS_URL)
    assert in_db is not None
    assert in_db["xml_url"] == MOSTLY_METRICS_URL

    out = tmp_path / "gen.opml"
    lib.write_opml(str(out))
    regenerated = [f.xml_url for f in parse_opml(str(out))
                   if "mostlymetrics" in f.xml_url]
    assert regenerated == [MOSTLY_METRICS_URL]


def test_a_tokenized_url_round_trips_through_db_and_opml_exactly(lib, tmp_path):
    sid = lib.add_feed_section("Paid")
    lib.add_feed(sid, "Paid Newsletter", TOKENIZED, "https://www.example-paid.com/")

    assert lib.find_feed_by_url(TOKENIZED)["xml_url"] == TOKENIZED

    out = tmp_path / "gen.opml"
    lib.write_opml(str(out))
    from_file = [f.xml_url for f in parse_opml(str(out))]
    assert TOKENIZED in from_file, from_file


def test_editing_a_feeds_name_leaves_a_tokenized_url_untouched(lib, tmp_path):
    sid = lib.add_feed_section("Paid")
    fid = lib.add_feed(sid, "Paid Newsletter", TOKENIZED, "https://www.example-paid.com/")

    feed = lib.get_feed(fid)
    lib.update_feed(fid, sid, "Renamed", feed["xml_url"], feed["html_url"])

    assert lib.get_feed(fid)["xml_url"] == TOKENIZED


def test_moving_a_feed_between_sections_never_rewrites_its_url(lib):
    """move_feed_to_section touches section_id only, so the table's per-row
    dropdown can't disturb a token."""
    a = lib.add_feed_section("Paid")
    b = lib.add_feed_section("Other")
    fid = lib.add_feed(a, "Paid Newsletter", TOKENIZED)

    lib.move_feed_to_section(fid, b)

    assert lib.get_feed(fid)["xml_url"] == TOKENIZED


def test_read_only_toggle_never_rewrites_the_url(lib):
    sid = lib.add_feed_section("Paid")
    fid = lib.add_feed(sid, "Paid Newsletter", TOKENIZED)

    lib.set_feed_excluded(fid, True)

    assert lib.get_feed(fid)["xml_url"] == TOKENIZED


def test_probe_does_not_reject_a_url_carrying_a_token(monkeypatch):
    """Validation must not treat query params as malformed."""
    xml = b"""<?xml version="1.0"?><rss version="2.0"><channel>
      <title>Paid Newsletter</title><link>https://www.example-paid.com/</link>
    </channel></rss>"""
    seen = {}

    def _fake_get(url, *a, **k):
        seen["url"] = url
        return _FakeResponse(xml)

    monkeypatch.setattr("linklib.feed.requests.get", _fake_get)
    result = probe_feed(TOKENIZED)
    assert result.ok is True
    # The probe must request the URL exactly as given, token intact.
    assert seen["url"] == TOKENIZED


# ---------------------------------------------------------------------------
# Add/edit validation
# ---------------------------------------------------------------------------

def test_probe_rejects_a_feedly_proxy_url():
    """These silently yield nothing forever (feed._fetch_feed skips them), so
    they have to be caught at save time rather than stored."""
    result = probe_feed("https://feedly.com/web/feeds/abc")
    assert result.ok is False
    assert "Feedly" in result.error


def test_probe_rejects_a_non_http_url():
    assert probe_feed("ftp://example.com/feed").ok is False
    assert probe_feed("").ok is False


def test_probe_accepts_rss_and_reports_title_and_site(monkeypatch):
    xml = b"""<?xml version="1.0"?><rss version="2.0"><channel>
      <title>Mostly Metrics</title><link>https://www.mostlymetrics.com/</link>
      <item><title>A post</title><link>https://x.example/1</link></item>
    </channel></rss>"""
    monkeypatch.setattr("linklib.feed.requests.get",
                        lambda *a, **k: _FakeResponse(xml))
    result = probe_feed("https://www.mostlymetrics.com/feed")
    assert result.ok is True
    assert result.title == "Mostly Metrics"
    assert result.html_url == "https://www.mostlymetrics.com/"
    assert result.item_count == 1


def test_probe_accepts_atom(monkeypatch):
    xml = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
      <title>Tomasz Tunguz</title><link href="https://tomtunguz.com/"/>
      <entry><title>A post</title></entry></feed>"""
    monkeypatch.setattr("linklib.feed.requests.get",
                        lambda *a, **k: _FakeResponse(xml))
    result = probe_feed("https://tomtunguz.com/index.xml")
    assert result.ok is True
    assert result.title == "Tomasz Tunguz"
    assert result.html_url == "https://tomtunguz.com/"


def test_probe_rejects_a_page_that_is_not_a_feed(monkeypatch):
    monkeypatch.setattr("linklib.feed.requests.get",
                        lambda *a, **k: _FakeResponse(b"<html><body>hi</body></html>"))
    result = probe_feed("https://example.com/")
    assert result.ok is False
    assert "feed" in result.error.lower()


def test_probe_accepts_a_valid_but_currently_empty_feed(monkeypatch):
    """Low-volume sources legitimately sit empty between posts. That's not a
    reason to refuse the subscription."""
    xml = b"""<?xml version="1.0"?><rss version="2.0"><channel>
      <title>Quiet Blog</title><link>https://quiet.example/</link></channel></rss>"""
    monkeypatch.setattr("linklib.feed.requests.get",
                        lambda *a, **k: _FakeResponse(xml))
    assert probe_feed("https://quiet.example/feed").ok is True


class _FakeResponse:
    def __init__(self, content: bytes):
        self.content = content

    def raise_for_status(self):
        return None


# ---------------------------------------------------------------------------
# Admin routes
# ---------------------------------------------------------------------------

@pytest.fixture
def app_env(monkeypatch, tmp_path):
    """Boot the app against a temp DB and a temp OPML.

    Pointing LINKLIB_SITES_OPML at a copy matters: the startup hook seeds and
    regenerates, so leaving it on the default would have every route test in
    this file writing into the repo's own preferred_sites.opml.
    """
    opml = tmp_path / "sites.opml"
    shutil.copy(REPO_OPML, opml)
    monkeypatch.setenv("LINKLIB_DB", str(tmp_path / "app.db"))
    monkeypatch.setenv("LINKLIB_SITES_OPML", str(opml))
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib
    import webapp.app as appmod
    importlib.reload(appmod)
    appmod._OPML_FOR_TESTS = str(opml)
    return appmod


def _client(appmod):
    from fastapi.testclient import TestClient
    client = TestClient(appmod.app, raise_server_exceptions=True)
    client.post("/login", data={"username": "admin", "password": "adminpass"},
                follow_redirects=False)
    return client


def test_startup_hook_seeds_the_tables(app_env):
    with _client(app_env):
        lib = app_env._lib()
        try:
            assert len(lib.list_feeds()) == len(parse_opml(REPO_OPML))
        finally:
            lib.close()


def test_subscriber_access_control_lives_on_the_feeds_page(monkeypatch, tmp_path):
    """Relocated from /admin/library: it probes a recent post per paywalled
    source, so it belongs with feed management. Only renders when at least
    one LINKLIB_COOKIE_<DOMAIN> variable is configured."""
    import importlib
    import webapp.app as appmod
    from linklib import extract as extract_mod

    opml = tmp_path / "sites.opml"
    shutil.copy(REPO_OPML, opml)
    monkeypatch.setenv("LINKLIB_DB", str(tmp_path / "auth.db"))
    monkeypatch.setenv("LINKLIB_SITES_OPML", str(opml))
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    monkeypatch.setenv(extract_mod._cookie_env_var("mostlymetrics.com"), "substack.sid=x")
    importlib.reload(appmod)

    with _client(appmod) as client:
        feeds = client.get("/admin/library/feeds").text
        library = client.get("/admin/library").text

    assert "Re-check subscriber access" in feeds
    assert "Re-check subscriber access" not in library
    # Now in the header's action group beside "+ Add feed", not stranded above
    # the H1 on a line of its own.
    assert feeds.index("<h1") < feeds.index("Re-check subscriber access")
    assert feeds.index("Re-check subscriber access") < feeds.index("+ Add feed")
    assert '<div class="ff-head-actions">' in feeds
    # Exactly one trigger: the coral panel no longer renders its own copy.
    assert feeds.count("Re-check subscriber access") == 1


def test_recheck_redirects_back_to_feeds(app_env):
    """The route path is unchanged (the Reader's own banner posts to it too);
    only the redirect target follows the control."""
    with _client(app_env) as client:
        resp = client.post("/admin/auth/recheck", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/library/feeds"


def test_feeds_page_requires_admin(app_env):
    from fastapi.testclient import TestClient
    anon = TestClient(app_env.app)
    resp = anon.get("/admin/library/feeds", follow_redirects=False)
    assert resp.status_code in (302, 303, 307)
    assert "/login" in resp.headers["location"]


def test_feeds_page_lists_every_feed_in_one_flat_table(app_env):
    with _client(app_env) as client:
        html = client.get("/admin/library/feeds").text
    for section in ("News", "Market Insights", "Blogs", "Tools", "Substacks"):
        assert section in html           # as dropdown options and section rows
    assert "Mostly Metrics (CJ Gustafson)" in html
    # One table, one row per feed — not a bordered box per section.
    assert html.count('class="ff-row"') == len(parse_opml(REPO_OPML))
    assert "Manage sections" in html


def test_sections_render_as_a_table_with_disabled_remove_when_non_empty(app_env):
    """Remove is disabled rather than absent on a section that still holds
    feeds, and there's no per-row explanatory line — the count plus the
    disabled state carry it."""
    with _client(app_env) as client:
        html = client.get("/admin/library/feeds").text

    assert '<table class="fs-table">' in html
    assert html.count('class="fs-row"') == 5          # one row per section
    assert html.count('class="btn btn-ghost fs-remove-off"') == 5   # all five non-empty
    assert "disabled" in html
    # The old per-row line is gone.
    assert "to another section to remove it." not in html


def test_an_emptied_section_gets_a_live_remove_button(app_env):
    with _client(app_env) as client:
        client.post("/admin/library/feeds/sections/new",
                    data={"name": "Operators"}, follow_redirects=False)
        html = client.get("/admin/library/feeds").text

    assert html.count('class="fs-row"') == 6
    # The new empty section has a real Remove form; the other five stay disabled.
    assert html.count('class="btn btn-ghost fs-remove-off"') == 5
    assert 'class="btn btn-ghost fs-remove"' in html


def test_feeds_page_shows_the_mostly_metrics_url_exactly(app_env):
    with _client(app_env) as client:
        html = client.get("/admin/library/feeds").text
    assert MOSTLY_METRICS_URL in html


def test_row_dropdown_moves_a_feed_and_row_checkbox_sets_read_only(app_env):
    with _client(app_env) as client:
        lib = app_env._lib()
        try:
            feed = [f for f in lib.list_feeds() if not f["exclude_from_queue"]][0]
            target = [s for s in lib.list_feed_sections()
                      if s["id"] != feed["section_id"]][0]
        finally:
            lib.close()

        client.post(f"/admin/library/feeds/{feed['id']}/section",
                    data={"section_id": str(target["id"])}, follow_redirects=False)
        client.post(f"/admin/library/feeds/{feed['id']}/read-only",
                    data={"exclude_from_queue": "1"}, follow_redirects=False)

        lib = app_env._lib()
        try:
            moved = lib.get_feed(feed["id"])
            assert moved["section_id"] == target["id"]
            assert moved["exclude_from_queue"] == 1
            assert moved["xml_url"] == feed["xml_url"]   # never rewritten
        finally:
            lib.close()

        # Unchecked posts no field at all, which is the off state.
        client.post(f"/admin/library/feeds/{feed['id']}/read-only",
                    data={}, follow_redirects=False)
        lib = app_env._lib()
        try:
            assert lib.get_feed(feed["id"])["exclude_from_queue"] == 0
        finally:
            lib.close()


def test_add_feed_writes_through_to_the_opml_file(app_env, monkeypatch):
    monkeypatch.setattr("linklib.feed.probe_feed",
                        lambda url, **k: FeedProbe(True, title="Example",
                                                   html_url="https://example.com/"))
    with _client(app_env) as client:
        lib = app_env._lib()
        try:
            section_id = lib.list_feed_sections()[0]["id"]
        finally:
            lib.close()
        resp = client.post("/admin/library/feeds/new",
                           data={"name": "Example", "xml_url": "https://example.com/feed",
                                 "html_url": "", "section_id": str(section_id)},
                           follow_redirects=False)
    assert resp.status_code == 303

    on_disk = parse_opml(app_env._OPML_FOR_TESTS)
    assert "https://example.com/feed" in [f.xml_url for f in on_disk]
    assert "example.com" in preferred_domains(app_env._OPML_FOR_TESTS)


def test_add_feed_rejects_an_invalid_url_without_saving(app_env, monkeypatch):
    monkeypatch.setattr("linklib.feed.probe_feed",
                        lambda url, **k: FeedProbe(False, error="That URL responded, but it isn't a valid RSS or Atom feed."))
    with _client(app_env) as client:
        lib = app_env._lib()
        try:
            section_id = lib.list_feed_sections()[0]["id"]
            before = len(lib.list_feeds())
        finally:
            lib.close()
        resp = client.post("/admin/library/feeds/new",
                           data={"name": "Bad", "xml_url": "https://example.com/nope",
                                 "html_url": "", "section_id": str(section_id)})
        assert resp.status_code == 400
        assert "isn&#x27;t a valid RSS or Atom feed" in resp.text or "isn't a valid RSS" in resp.text
        # The rejected values come back in the form rather than being lost.
        assert "https://example.com/nope" in resp.text

        lib = app_env._lib()
        try:
            assert len(lib.list_feeds()) == before
        finally:
            lib.close()


def test_add_feed_rejects_a_duplicate_url(app_env):
    with _client(app_env) as client:
        lib = app_env._lib()
        try:
            section_id = lib.list_feed_sections()[0]["id"]
            existing = lib.list_feeds()[0]["xml_url"]
        finally:
            lib.close()
        resp = client.post("/admin/library/feeds/new",
                           data={"name": "Dupe", "xml_url": existing,
                                 "html_url": "", "section_id": str(section_id)})
    assert resp.status_code == 400
    assert "already in your list" in resp.text


def test_edit_feed_does_not_reprobe_when_the_url_is_unchanged(app_env, monkeypatch):
    """A rename or a section move shouldn't fail because the source happens to
    be down today."""
    def _boom(*a, **k):
        raise AssertionError("probe_feed should not run for an unchanged URL")

    monkeypatch.setattr("linklib.feed.probe_feed", _boom)
    with _client(app_env) as client:
        lib = app_env._lib()
        try:
            feed = lib.list_feeds()[0]
        finally:
            lib.close()
        resp = client.post(f"/admin/library/feeds/{feed['id']}/edit",
                           data={"name": "Renamed", "xml_url": feed["xml_url"],
                                 "html_url": feed["html_url"],
                                 "section_id": str(feed["section_id"])},
                           follow_redirects=False)
    assert resp.status_code == 303
    assert "Renamed" in [f.name for f in parse_opml(app_env._OPML_FOR_TESTS)]


def test_delete_feed_removes_it_from_the_opml(app_env):
    with _client(app_env) as client:
        lib = app_env._lib()
        try:
            feed = lib.list_feeds()[0]
        finally:
            lib.close()
        client.post(f"/admin/library/feeds/{feed['id']}/delete", follow_redirects=False)

    assert feed["xml_url"] not in [f.xml_url for f in parse_opml(app_env._OPML_FOR_TESTS)]


def test_delete_section_is_blocked_while_it_holds_feeds(app_env):
    with _client(app_env) as client:
        lib = app_env._lib()
        try:
            section = lib.list_feed_sections()[0]
        finally:
            lib.close()
        resp = client.post(f"/admin/library/feeds/sections/{section['id']}/delete",
                           follow_redirects=False)
        assert resp.status_code == 303
        assert "error=" in resp.headers["location"]

        lib = app_env._lib()
        try:
            assert lib.get_feed_section(section["id"]) is not None
        finally:
            lib.close()


def test_section_crud_round_trip(app_env):
    with _client(app_env) as client:
        client.post("/admin/library/feeds/sections/new",
                    data={"name": "Operators"}, follow_redirects=False)
        lib = app_env._lib()
        try:
            created = [s for s in lib.list_feed_sections() if s["name"] == "Operators"][0]
            # Sections carry no settings of their own any more.
            assert "exclude_from_queue" not in created
        finally:
            lib.close()

        client.post(f"/admin/library/feeds/sections/{created['id']}/rename",
                    data={"name": "Operator blogs"}, follow_redirects=False)
        lib = app_env._lib()
        try:
            assert lib.get_feed_section(created["id"])["name"] == "Operator blogs"
        finally:
            lib.close()

        client.post(f"/admin/library/feeds/sections/{created['id']}/delete",
                    follow_redirects=False)
        lib = app_env._lib()
        try:
            assert lib.get_feed_section(created["id"]) is None
        finally:
            lib.close()


def test_duplicate_section_name_is_rejected(app_env):
    with _client(app_env) as client:
        resp = client.post("/admin/library/feeds/sections/new",
                           data={"name": "blogs"}, follow_redirects=False)
    assert "error=" in resp.headers["location"]


# ---------------------------------------------------------------------------
# Archive-queue exclusion (replaces QUEUE_EXCLUDE_CATEGORIES)
# ---------------------------------------------------------------------------

def _fake_items():
    """Two News items and one Blogs item, shaped like feed.get_feed_items output."""
    return ([
        {"url": "https://news.crunchbase.com/a", "title": "CB", "source": "Crunchbase",
         "category": "News", "feed_url": "https://news.crunchbase.com/sections/enterprise/feed/"},
        {"url": "https://techcrunch.com/b", "title": "TC", "source": "TechCrunch",
         "category": "News", "feed_url": "https://techcrunch.com/enterprise/feed/"},
        {"url": "https://kellblog.com/c", "title": "Kell", "source": "Kellblog",
         "category": "Blogs", "feed_url": "http://kellblog.com/feed/"},
    ], [])


def test_queue_scan_skips_read_only_feeds_and_keeps_the_rest(seeded, monkeypatch):
    from linklib import queue as qmod

    monkeypatch.setattr("linklib.feed.get_feed_items", lambda *a, **k: _fake_items())
    queued = []
    monkeypatch.setattr(qmod, "_enrich_candidate",
                        lambda item, vocab, **k: dict(url=item["url"], title="", source="",
                                                      summary="", content="", suggested_tags=[],
                                                      published_at=None, origin="feed",
                                                      enriched=False, enrich_model="",
                                                      enrich_rules="", in_scope=True,
                                                      input_tokens=0, output_tokens=0,
                                                      cost_usd=0.0))
    monkeypatch.setattr(seeded, "add_to_queue",
                        lambda **kw: (queued.append(kw["url"]), True)[1])

    stats = qmod.scan_feed_into_queue(seeded, "ignored.opml", enrich=False)

    assert stats["scanned"] == 3
    assert queued == ["https://kellblog.com/c"]   # both News feeds skipped


def test_queue_scan_follows_the_feed_not_the_section_name(seeded, monkeypatch):
    """Renaming News, or moving a News feed into Blogs, must not change what
    the queue skips — the old name-matched set got this wrong."""
    from linklib import queue as qmod

    news_section = [s for s in seeded.list_feed_sections() if s["name"] == "News"][0]
    blogs = [s for s in seeded.list_feed_sections() if s["name"] == "Blogs"][0]
    tc = seeded.find_feed_by_url("https://techcrunch.com/enterprise/feed/")
    seeded.move_feed_to_section(tc["id"], blogs["id"])
    seeded.rename_feed_section(news_section["id"], "Headlines")

    monkeypatch.setattr("linklib.feed.get_feed_items", lambda *a, **k: _fake_items())
    queued = []
    monkeypatch.setattr(qmod, "_enrich_candidate",
                        lambda item, vocab, **k: dict(url=item["url"], title="", source="",
                                                      summary="", content="", suggested_tags=[],
                                                      published_at=None, origin="feed",
                                                      enriched=False, enrich_model="",
                                                      enrich_rules="", in_scope=True,
                                                      input_tokens=0, output_tokens=0,
                                                      cost_usd=0.0))
    monkeypatch.setattr(seeded, "add_to_queue",
                        lambda **kw: (queued.append(kw["url"]), True)[1])

    qmod.scan_feed_into_queue(seeded, "ignored.opml", enrich=False)

    assert queued == ["https://kellblog.com/c"]


def test_sitemap_sweep_skips_read_only_feeds(seeded, monkeypatch):
    from linklib import queue as qmod
    from linklib.feed import parse_opml as _parse

    # Network mocked per the standing convention in test_backfill_sitemap.py —
    # this test only cares about which feeds are skipped for being read-only,
    # not what a real sitemap fetch returns for the rest.
    monkeypatch.setattr(qmod, "discover_sitemaps", lambda site: [])
    monkeypatch.setattr(qmod, "fetch_sitemap_entries", lambda sm: [])

    feeds = _parse(REPO_OPML)
    report = qmod.scan_sitemaps_into_queue(seeded, feeds, "2020-01-01", dry_run=True)
    skipped = [r["source"] for r in report if "read-only" in r.get("note", "")]
    assert set(skipped) == {"Enterprise Archives - Crunchbase News", "TechCrunch"}
