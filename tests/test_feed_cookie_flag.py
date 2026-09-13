"""The feeds table's descriptive "Cookie" note, and the New content
quadrant's count override.

The property worth protecting here is that this column is DESCRIPTIVE ONLY.
It exists so a source that quietly stops returning full text points at the env
var to go check, and it must never become a second home for the cookie itself:
the secret stays in LINKLIB_AUTH_COOKIES, where extract._auth_cookies() reads
it. Nothing in the app writes a cookie value into this column, and the seeded
note is a label, not a credential.

The second property is the seeding guard. A note the admin deliberately clears
must stay cleared across restarts, which an "is it empty" check cannot deliver
(empty and never-set are indistinguishable) — the same bug _seed_toolbox
shipped and had to fix. So seeding is settings-flagged, exactly like
seed_feeds_from_opml.
"""
import pathlib
import shutil
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Library
from linklib.feed import PAYWALLED_DOMAINS

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


def _client(appmod):
    from fastapi.testclient import TestClient
    client = TestClient(appmod.app, raise_server_exceptions=True)
    client.post("/login", data={"username": "admin", "password": "adminpass"},
                follow_redirects=False)
    return client


@pytest.fixture
def app_env(monkeypatch, tmp_path):
    """Boot the app against a temp DB and a temp OPML copy.

    The OPML copy matters: the startup hook regenerates the file, so the
    default path would have these tests rewriting the repo's own working tree.
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
    return appmod


# ---------------------------------------------------------------------------
# Column + seeding
# ---------------------------------------------------------------------------

def test_flag_defaults_off_before_seeding(lib):
    """No retroactive flagging — same precedent as every added column."""
    lib.seed_feeds_from_opml(REPO_OPML)
    assert all(f["has_paywall_cookie"] == 0 for f in lib.list_feeds())


def test_seeding_flags_every_known_paywalled_feed(seeded):
    result = seeded.seed_paywall_cookie_flags()
    assert result["seeded"] is True

    flagged = [f for f in seeded.list_feeds() if f["has_paywall_cookie"]]
    assert len(flagged) == result["feeds"]
    assert flagged, "expected paywalled feeds in the curated OPML"

    for f in seeded.list_feeds():
        on_paywalled_domain = any(
            dom in f"{f['xml_url']} {f['html_url']}" for dom in PAYWALLED_DOMAINS)
        assert bool(f["has_paywall_cookie"]) is on_paywalled_domain


def test_seeding_flags_exactly_the_three_expected_feeds(seeded):
    """The set must not change shape when the free-text note became a boolean."""
    seeded.seed_paywall_cookie_flags()
    names = sorted(f["name"] for f in seeded.list_feeds() if f["has_paywall_cookie"])
    assert names == ["Ben Thompson (Stratechery)", "Mostly Metrics (CJ Gustafson)",
                     "Public Comps"]


def test_migrates_a_legacy_note_even_off_a_paywalled_domain(seeded):
    """The migration arm: an existing DB's hand-written note becomes a tick,
    whatever domain it sits on."""
    other = [f for f in seeded.list_feeds()
             if not any(d in f["xml_url"] for d in PAYWALLED_DOMAINS)][0]
    # The retired column is dropped on fresh databases now, so recreate the
    # pre-migration shape this arm exists to handle.
    seeded.conn.execute(
        "ALTER TABLE feeds ADD COLUMN paywall_cookie_note TEXT NOT NULL DEFAULT ''")
    seeded.conn.execute("UPDATE feeds SET paywall_cookie_note=? WHERE id=?",
                        ("Cookie auth via LINKLIB_AUTH_COOKIES", other["id"]))
    seeded.conn.commit()

    seeded.seed_paywall_cookie_flags()
    assert seeded.get_feed(other["id"])["has_paywall_cookie"] == 1


def test_mostly_metrics_specifically_gets_the_flag(seeded):
    seeded.seed_paywall_cookie_flags()
    mm = [f for f in seeded.list_feeds() if "mostlymetrics.com" in f["xml_url"]]
    assert len(mm) == 1
    assert mm[0]["has_paywall_cookie"] == 1
    assert mm[0]["xml_url"] == "https://www.mostlymetrics.com/feed"


def test_seeding_is_flag_guarded_not_emptiness_guarded(seeded):
    """A deliberately unticked box stays unticked across restarts."""
    seeded.seed_paywall_cookie_flags()
    mm = [f for f in seeded.list_feeds() if f["has_paywall_cookie"]][0]
    seeded.set_feed_paywall_cookie(mm["id"], False)

    again = seeded.seed_paywall_cookie_flags()
    assert again["seeded"] is False
    assert seeded.get_feed(mm["id"])["has_paywall_cookie"] == 0


def test_seeding_does_not_burn_its_flag_on_an_empty_feeds_table(lib):
    assert lib.seed_paywall_cookie_flags() == {"seeded": False, "feeds": 0}
    lib.seed_feeds_from_opml(REPO_OPML)
    assert lib.seed_paywall_cookie_flags()["seeded"] is True


def test_setting_the_cookie_flag_touches_only_that_column(seeded):
    mm = [f for f in seeded.list_feeds() if "mostlymetrics.com" in f["xml_url"]][0]
    seeded.set_feed_paywall_cookie(mm["id"], True)
    after = seeded.get_feed(mm["id"])
    for field in ("xml_url", "html_url", "name", "section_id",
                  "exclude_from_queue", "has_active_subscription"):
        assert after[field] == mm[field]


def test_cookie_flag_is_absent_from_the_generated_opml(seeded):
    seeded.seed_paywall_cookie_flags()
    assert "has_paywall_cookie" not in seeded.opml_xml()
    assert "LINKLIB_AUTH_COOKIES" not in seeded.opml_xml()


def test_toggling_the_cookie_flag_leaves_the_opml_byte_identical(seeded):
    before = seeded.opml_xml()
    mm = [f for f in seeded.list_feeds() if "mostlymetrics.com" in f["xml_url"]][0]
    seeded.set_feed_paywall_cookie(mm["id"], True)
    assert seeded.opml_xml() == before


def test_flag_round_trips_through_add_and_update(lib):
    sid = lib.add_feed_section("Substacks")
    fid = lib.add_feed(sid, "Paid", "https://paid.example/feed?tok=abc&u=1",
                       "https://paid.example/", has_paywall_cookie=True)
    assert lib.get_feed(fid)["has_paywall_cookie"] == 1
    assert lib.get_feed(fid)["xml_url"] == "https://paid.example/feed?tok=abc&u=1"

    lib.update_feed(fid, sid, "Paid", "https://paid.example/feed?tok=abc&u=1",
                    "https://paid.example/", has_paywall_cookie=True)
    assert lib.get_feed(fid)["has_paywall_cookie"] == 1
    assert lib.get_feed(fid)["xml_url"] == "https://paid.example/feed?tok=abc&u=1"


def test_no_cookie_value_can_reach_the_database(seeded):
    """The column is a boolean now, so there is nowhere for a cookie string to
    be stored even by mistake."""
    seeded.seed_paywall_cookie_flags()
    for f in seeded.list_feeds():
        assert f["has_paywall_cookie"] in (0, 1)


# ---------------------------------------------------------------------------
# Admin page rendering
# ---------------------------------------------------------------------------

def test_feed_table_has_a_cookie_column_with_no_checkbox(app_env):
    """2026-08: the Cookie column is a computed, read-only indicator now —
    there is nothing left for an admin to tick."""
    with _client(app_env) as client:
        html = client.get("/admin/reader/feeds").text
    assert ">Cookie</th>" in html
    assert 'name="has_paywall_cookie"' not in html


def test_cookie_cell_has_no_input_or_form(app_env):
    with _client(app_env) as client:
        html = client.get("/admin/reader/feeds").text
    start = html.index('<td class="ff-cookie">')
    end = html.index("</td>", start)
    cell = html[start:end]
    assert "<input" not in cell
    assert "<form" not in cell


def test_every_row_shows_a_computed_cookie_indicator(app_env, monkeypatch):
    """One indicator per row; only the domain with a configured env var reads
    as configured."""
    from linklib import extract as extract_mod
    monkeypatch.setattr(extract_mod, "_COOKIE_DOMAINS", ("mostlymetrics.com",))
    monkeypatch.setenv(extract_mod._cookie_env_var("mostlymetrics.com"), "sid=1")
    with _client(app_env) as client:
        html = client.get("/admin/reader/feeds").text
        lib = app_env._lib()
        try:
            feeds = lib.list_feeds()
        finally:
            lib.close()
    assert html.count('class="ff-cookie"') == len(feeds)
    assert html.count("configured") >= 1
    mm_start = html.index("Mostly Metrics")
    span_start = html.index('aria-label="Cookie for', mm_start)
    assert "not configured" not in html[span_start:span_start + 120]


def test_cookie_indicator_has_its_own_aria_label_shape(app_env):
    """Deliberately different from Subscriber's aria-label — this is
    a computed fact, not a per-row control, so it isn't held to the same
    "boolean checkbox" labelling convention. (Read only's own checkbox and
    aria-label were retired along with the Archive Queue itself — 2026-09,
    PR 3 — so it's no longer part of this comparison.)"""
    with _client(app_env) as client:
        html = client.get("/admin/reader/feeds").text
        lib = app_env._lib()
        try:
            name = lib.list_feeds()[0]["name"]
        finally:
            lib.close()
    assert f'aria-label="Subscriber: {name}"' in html
    assert f'aria-label="Cookie for {name}:' in html


def test_footnote_explains_the_column_once(app_env):
    with _client(app_env) as client:
        html = client.get("/admin/reader/feeds").text
    assert "<strong>Cookie</strong> shows whether this feed's domain has a subscriber cookie set up right now" in html
    assert "The cookie value itself is never stored in this database" in html


def test_cookie_route_is_gone(app_env):
    """The old POST .../cookie toggle route was removed along with the
    checkbox it served — there's nothing left for it to write."""
    with _client(app_env) as client:
        lib = app_env._lib()
        try:
            feed = lib.list_feeds()[0]
        finally:
            lib.close()
        resp = client.post(f"/admin/reader/feeds/{feed['id']}/cookie",
                           data={"has_paywall_cookie": "1"}, follow_redirects=False)
    assert resp.status_code in (404, 405)


def test_has_paywall_cookie_column_is_frozen_but_still_present(app_env):
    """Non-destructive retirement: the column stays in the schema and keeps
    whatever value it last had, but nothing writes it from the admin UI
    anymore."""
    with _client(app_env) as client:
        lib = app_env._lib()
        try:
            feed = [f for f in lib.list_feeds() if f["has_paywall_cookie"]][0]
        finally:
            lib.close()
        fid = feed["id"]
        client.post(f"/admin/reader/feeds/{fid}/edit", data={
            "name": "Renamed Again", "xml_url": feed["xml_url"],
            "html_url": feed["html_url"], "section_id": str(feed["section_id"]),
        }, follow_redirects=False)
        lib = app_env._lib()
        try:
            after = lib.get_feed(fid)
        finally:
            lib.close()
    assert after["has_paywall_cookie"] == 1
    assert after["name"] == "Renamed Again"


def test_no_free_text_note_field_remains(app_env):
    """The whole point of the change: no per-row text entry anywhere."""
    with _client(app_env) as client:
        table = client.get("/admin/reader/feeds").text
        form = client.get("/admin/reader/feeds/new").text
    for html in (table, form):
        assert 'name="paywall_cookie_note"' not in html


def test_edit_form_shows_a_computed_readout_not_a_checkbox(app_env):
    with _client(app_env) as client:
        lib = app_env._lib()
        try:
            feed = lib.list_feeds()[0]
        finally:
            lib.close()
        html = client.get(f"/admin/reader/feeds/{feed['id']}/edit").text
    assert 'name="has_paywall_cookie"' not in html
    assert ("Cookie configured for this domain" in html
            or "No cookie configured for this domain" in html)


def test_editing_a_feed_without_touching_the_cookie_flag_keeps_it(app_env):
    with _client(app_env) as client:
        lib = app_env._lib()
        try:
            feed = [f for f in lib.list_feeds() if f["has_paywall_cookie"]][0]
        finally:
            lib.close()
        fid = feed["id"]
        client.post(f"/admin/reader/feeds/{fid}/edit", data={
            "name": "Renamed", "xml_url": feed["xml_url"],
            "html_url": feed["html_url"], "section_id": str(feed["section_id"]),
            "has_paywall_cookie": "1",
        }, follow_redirects=False)
        lib = app_env._lib()
        try:
            after = lib.get_feed(fid)
        finally:
            lib.close()
    assert after["has_paywall_cookie"] == 1
    assert after["name"] == "Renamed"


def test_startup_hook_seeds_the_flags(app_env):
    with _client(app_env):
        lib = app_env._lib()
        try:
            flagged = [f for f in lib.list_feeds() if f["has_paywall_cookie"]]
        finally:
            lib.close()
    assert flagged


def test_checkbox_columns_are_centre_justified(app_env):
    """BRAND.md: checkbox/boolean-indicator columns centre, everything else
    stays left. (Read only came out of the table entirely along with the
    Archive Queue itself — 2026-09, PR 3.)"""
    with _client(app_env) as client:
        html = client.get("/admin/reader/feeds").text
    for label in ("Cookie", "Subscriber"):
        assert f'text-align:center;">{label}</th>' in html
    assert "Read only</th>" not in html
    assert '<th style="width:18%;">Name</th>' in html
    assert '<th style="width:14%;">Section</th>' in html


# ---------------------------------------------------------------------------
# has_active_subscription — informational only
# ---------------------------------------------------------------------------

def test_subscription_flag_defaults_off_for_every_seeded_feed(seeded):
    assert all(f["has_active_subscription"] == 0 for f in seeded.list_feeds())


def test_seeding_marks_only_the_subscribed_source(seeded):
    result = seeded.seed_active_subscriptions()
    assert result["seeded"] is True

    on = [f for f in seeded.list_feeds() if f["has_active_subscription"]]
    assert [f["xml_url"] for f in on] == ["https://www.mostlymetrics.com/feed"]
    assert result["feeds"] == 1


def test_paywalled_but_unsubscribed_sources_stay_off(seeded):
    """The distinction the flag exists to record: Stratechery and Public Comps
    are paywalled (they get a cookie note) but not subscribed."""
    seeded.seed_paywall_cookie_flags()
    seeded.seed_active_subscriptions()
    for f in seeded.list_feeds():
        host = f["xml_url"]
        if "stratechery.com" in host or "publiccomps.com" in host:
            assert f["has_paywall_cookie"] == 1, "expected the cookie flag"
            assert f["has_active_subscription"] == 0


def test_subscription_seeding_is_flag_guarded(seeded):
    """A deliberately unchecked box stays unchecked across restarts."""
    seeded.seed_active_subscriptions()
    mm = [f for f in seeded.list_feeds() if f["has_active_subscription"]][0]
    seeded.set_feed_active_subscription(mm["id"], False)

    again = seeded.seed_active_subscriptions()
    assert again["seeded"] is False
    assert seeded.get_feed(mm["id"])["has_active_subscription"] == 0


def test_subscription_seeding_does_not_burn_its_flag_on_an_empty_table(lib):
    assert lib.seed_active_subscriptions() == {"seeded": False, "feeds": 0}
    lib.seed_feeds_from_opml(REPO_OPML)
    assert lib.seed_active_subscriptions()["seeded"] is True


def test_setting_the_flag_touches_only_that_column(seeded):
    mm = [f for f in seeded.list_feeds() if "mostlymetrics.com" in f["xml_url"]][0]
    seeded.set_feed_active_subscription(mm["id"], True)
    after = seeded.get_feed(mm["id"])
    for field in ("xml_url", "html_url", "name", "section_id",
                  "exclude_from_queue", "has_paywall_cookie"):
        assert after[field] == mm[field]


def test_flag_is_absent_from_the_generated_opml(seeded):
    seeded.seed_active_subscriptions()
    assert "has_active_subscription" not in seeded.opml_xml()


def test_toggling_the_flag_leaves_the_generated_opml_byte_identical(seeded):
    before = seeded.opml_xml()
    mm = [f for f in seeded.list_feeds() if "mostlymetrics.com" in f["xml_url"]][0]
    seeded.set_feed_active_subscription(mm["id"], True)
    assert seeded.opml_xml() == before


def test_nothing_outside_the_admin_surface_reads_the_flag(app_env):
    """Informational only. If a future change makes it functional that should
    be a deliberate decision, not something inherited from the column
    existing — so this pins the current contract."""
    import pathlib as _pl
    root = _pl.Path(__file__).resolve().parents[1]
    readers = []
    for path in list((root / "linklib").glob("*.py")) + list((root / "scripts").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "has_active_subscription" in text and path.name != "db.py":
            readers.append(path.name)
    assert readers == [], f"unexpected readers of the flag: {readers}"


def test_feed_table_has_an_active_subscription_column(app_env):
    with _client(app_env) as client:
        html = client.get("/admin/reader/feeds").text
    assert ">Subscriber</th>" in html
    assert 'name="has_active_subscription"' in html


def test_row_checkbox_reflects_the_seeded_state(app_env):
    with _client(app_env) as client:
        html = client.get("/admin/reader/feeds").text
        lib = app_env._lib()
        try:
            feeds = lib.list_feeds()
        finally:
            lib.close()
    expected = sum(1 for f in feeds if f["has_active_subscription"])
    assert expected == 1
    assert html.count('aria-label="Subscriber:') == len(feeds)


def test_row_toggle_posts_and_persists(app_env):
    with _client(app_env) as client:
        lib = app_env._lib()
        try:
            feed = [f for f in lib.list_feeds() if not f["has_active_subscription"]][0]
        finally:
            lib.close()
        fid = feed["id"]

        resp = client.post(f"/admin/reader/feeds/{fid}/subscription",
                           data={"has_active_subscription": "1"},
                           follow_redirects=False)
        assert resp.status_code == 303

        lib = app_env._lib()
        try:
            after = lib.get_feed(fid)
        finally:
            lib.close()
    assert after["has_active_subscription"] == 1
    assert after["xml_url"] == feed["xml_url"]


def test_row_toggle_unchecked_posts_nothing_and_clears(app_env):
    """An unchecked box posts no field at all, which is the off state."""
    with _client(app_env) as client:
        lib = app_env._lib()
        try:
            feed = [f for f in lib.list_feeds() if f["has_active_subscription"]][0]
        finally:
            lib.close()
        fid = feed["id"]

        client.post(f"/admin/reader/feeds/{fid}/subscription", data={},
                    follow_redirects=False)

        lib = app_env._lib()
        try:
            assert lib.get_feed(fid)["has_active_subscription"] == 0
        finally:
            lib.close()


def test_edit_form_round_trips_the_flag(app_env):
    with _client(app_env) as client:
        lib = app_env._lib()
        try:
            feed = [f for f in lib.list_feeds() if f["has_active_subscription"]][0]
        finally:
            lib.close()
        html = client.get(f"/admin/reader/feeds/{feed['id']}/edit").text
    assert 'name="has_active_subscription" value="1" checked' in html


def test_editing_a_feed_without_touching_the_flag_keeps_it(app_env):
    with _client(app_env) as client:
        lib = app_env._lib()
        try:
            feed = [f for f in lib.list_feeds() if f["has_active_subscription"]][0]
        finally:
            lib.close()
        fid = feed["id"]

        client.post(f"/admin/reader/feeds/{fid}/edit", data={
            "name": "Renamed", "xml_url": feed["xml_url"],
            "html_url": feed["html_url"], "section_id": str(feed["section_id"]),
            "has_paywall_cookie": "1" if feed["has_paywall_cookie"] else "",
            "has_active_subscription": "1",
        }, follow_redirects=False)

        lib = app_env._lib()
        try:
            after = lib.get_feed(fid)
        finally:
            lib.close()
    assert after["has_active_subscription"] == 1
    assert after["name"] == "Renamed"


def test_form_helper_copy_says_it_is_informational(app_env):
    with _client(app_env) as client:
        html = client.get("/admin/reader/feeds/new").text
    assert "as a note to yourself" in html
    assert "doesn&#x27;t affect fetching" in html or \
           "doesn't affect fetching" in html
    assert "Nothing in the app reads this" in html


def test_page_footnote_explains_the_flag(app_env):
    with _client(app_env) as client:
        html = client.get("/admin/reader/feeds").text
    assert "<strong>Subscriber</strong> marks whether you currently pay" in html


def test_mobile_labels_the_subscription_cell_unconditionally(app_env):
    """Unlike the cookie cell, a checkbox carries meaning in both states, so an
    unchecked box still needs its label in the stacked layout."""
    with _client(app_env) as client:
        html = client.get("/admin/reader/feeds").text
    assert '.ff-sub::before{content:"Subscriber"' in html


# ---------------------------------------------------------------------------
# Part 2: the Reader box's quadrants
# ---------------------------------------------------------------------------

# Rewritten in PR 9 (2026-09) on two counts.
#
# First, location: these quadrants used to render on a standalone
# /admin/library page inside `class="lib-q-*"` wrappers that existed only to
# drive that page's two-column flex layout. The page is gone and so are the
# wrappers — the quadrants are now a nested Reader group on /admin, so the
# slicing helper keys off each quadrant's own visible summary label instead.
#
# Second, and the reason this section is worth reading before editing it: the
# tests here used to assert hardcoded tool counts ("3 tools", "2 tools").
# That broke in PR 520, again in PR 523, again in PR 524, and would have broken
# again here — four times in four PRs, every time for a legitimate structural
# change, never once catching a real bug. A test that fails whenever the
# structure it describes legitimately changes is a tax, not a safety net. They
# assert on CONTENTS now: which tools land in which quadrant. That's the fact
# worth protecting (a tool silently vanishing from the admin surface, or
# landing under the wrong heading), and it survives adding or removing a
# sibling tool without an edit.

_QUADRANTS = ["New content", "Existing archive management", "Tag management"]


def _disclosure_body(html, label):
    """The rendered HTML of one <details> group, found by its summary label.

    Slices by balancing <details>/</details> from the label backwards to its
    own opening tag, rather than running to the next sibling label. The last
    quadrant has no sibling after it, so a label-to-label slice would swallow
    every group rendered below it on /admin — which is exactly how an earlier
    draft of this test "found" Archive backup inside the Tag management
    quadrant.
    """
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


def _quadrant(html, label):
    return _disclosure_body(html, label)


def test_every_reader_tool_renders_in_exactly_one_quadrant(app_env):
    """No tool goes missing, and none is duplicated across quadrants.

    The real risk this guards: a card quietly dropping out of the admin
    surface (its page still routed, but no longer reachable by clicking) —
    exactly the class of gap the hub-nav orphan detector exists for, checked
    here from the other direction.
    """
    import webapp.app as appmod
    with _client(app_env) as client:
        html = client.get("/admin").text
    quadrants = {label: _quadrant(html, label) for label in _QUADRANTS}
    for href, _title, _desc in appmod._LIBRARY_TOOLS:
        holding = [label for label, body in quadrants.items() if f'href="{href}"' in body]
        assert len(holding) == 1, f"{href} appears in {holding or 'no quadrant'}"


def test_quadrants_hold_the_tools_they_are_named_for(app_env):
    """Placement, not count — a tool under the wrong heading is the bug."""
    with _client(app_env) as client:
        html = client.get("/admin").text
    assert 'href="/admin/reader/feeds"' in _quadrant(html, "New content")

    existing = _quadrant(html, "Existing archive management")
    assert 'href="/admin/reader/backfill-content"' in existing
    assert 'href="/admin/reader/dedupe"' in existing
    assert 'href="/admin/reader/bulk-delete"' in existing

    tags = _quadrant(html, "Tag management")
    assert 'href="/admin/reader/tag-management"' in tags
    assert 'href="/admin/reader/enrich"' in tags


def test_new_content_quadrant_carries_the_capture_instructions(app_env):
    """The bookmarklet and Share-Sheet accordions live with the tool that
    brings new material in, and stay expandable rather than always-open."""
    quadrant = None
    with _client(app_env) as client:
        quadrant = _quadrant(client.get("/admin").text, "New content")
    assert "Saving to the archive" in quadrant
    assert "Saving to Read Later instead" in quadrant
    # Matched with the closing tag so the intro prose ("a bookmarklet and a
    # Share-Sheet shortcut") doesn't count as a third accordion.
    assert quadrant.count("Share-Sheet shortcut</summary>") == 2
    assert quadrant.count("the bookmarklet</summary>") == 2


def test_archive_backup_is_not_one_of_the_quadrants(app_env):
    """Its card moved to the System hub-nav group in PR 6 — a whole-DB
    snapshot is plumbing, not archive management."""
    with _client(app_env) as client:
        html = client.get("/admin").text
    for label in _QUADRANTS:
        assert 'href="/admin/library-backup"' not in _quadrant(html, label)


def test_shared_disclosure_component_has_no_count_override(app_env):
    """The override lives on _reader_admin_quadrants' local _lib_quadrant
    closure, not on the component /admin shares with it. _disclosure_group
    only ever receives a finished `count_label` string, so there is no
    parameter through which a fabricated count could reach a group header."""
    import inspect
    import webapp.app as appmod
    params = inspect.signature(appmod._disclosure_group).parameters
    assert "count_override" not in params
    assert "count_label" in params
