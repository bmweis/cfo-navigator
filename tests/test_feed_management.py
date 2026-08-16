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
import os
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


def test_seed_marks_only_news_excluded_from_queue(lib):
    """Preserves the exact pre-migration behavior of QUEUE_EXCLUDE_CATEGORIES,
    which defaulted to {"News"}."""
    lib.seed_feeds_from_opml(REPO_OPML)
    assert lib.excluded_feed_section_names() == {"News"}


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
    """The whole point of moving exclusion out of a name-matched env var."""
    news = [s for s in seeded.list_feed_sections() if s["name"] == "News"][0]
    seeded.update_feed_section(news["id"], "Headlines", exclude_from_queue=True)
    assert seeded.excluded_feed_section_names() == {"Headlines"}


def test_excluded_sections_fall_back_to_the_legacy_default_when_unseeded(lib):
    """An unseeded DB reporting "nothing is excluded" would start funnelling
    News into the archive queue — a silent behavior change in the wrong
    direction."""
    assert lib.excluded_feed_section_names() == {"News"}


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


def test_feeds_page_requires_admin(app_env):
    from fastapi.testclient import TestClient
    anon = TestClient(app_env.app)
    resp = anon.get("/admin/library/feeds", follow_redirects=False)
    assert resp.status_code in (302, 303, 307)
    assert "/login" in resp.headers["location"]


def test_feeds_page_lists_every_section_and_feed(app_env):
    with _client(app_env) as client:
        html = client.get("/admin/library/feeds").text
    for section in ("News", "Market Insights", "Blogs", "Tools", "Substacks"):
        assert section in html
    assert "Mostly Metrics (CJ Gustafson)" in html
    assert "Not queued" in html          # the News exclusion badge


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
                    data={"name": "Operators", "exclude_from_queue": "1"},
                    follow_redirects=False)
        lib = app_env._lib()
        try:
            created = [s for s in lib.list_feed_sections() if s["name"] == "Operators"][0]
            assert created["exclude_from_queue"] == 1
        finally:
            lib.close()

        client.post(f"/admin/library/feeds/sections/{created['id']}/edit",
                    data={"name": "Operator blogs"}, follow_redirects=False)
        lib = app_env._lib()
        try:
            renamed = lib.get_feed_section(created["id"])
            assert renamed["name"] == "Operator blogs"
            # Unchecked box means the section is queue-eligible again.
            assert renamed["exclude_from_queue"] == 0
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
