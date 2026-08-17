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

def test_feed_table_has_a_cookie_column_with_a_checkbox(app_env):
    with _client(app_env) as client:
        html = client.get("/admin/library/feeds").text
    assert ">Cookie</th>" in html
    assert 'name="has_paywall_cookie"' in html


def test_cookie_cell_is_a_plain_checkbox_like_its_neighbours(app_env):
    """No badge, no icon, no separate hover element — the lock badge was a
    leftover from the free-text era, where it triggered the per-row tooltip."""
    with _client(app_env) as client:
        html = client.get("/admin/library/feeds").text
    assert "ff-cookie-badge" not in html
    assert "ff-cookie-form" not in html
    assert '<td class="ff-cookie">' in html


def test_every_row_has_a_cookie_checkbox_and_only_paywalled_ones_are_ticked(app_env):
    with _client(app_env) as client:
        html = client.get("/admin/library/feeds").text
        lib = app_env._lib()
        try:
            feeds = lib.list_feeds()
        finally:
            lib.close()
    expected = sum(1 for f in feeds if f["has_paywall_cookie"])
    assert 0 < expected < len(feeds)
    assert html.count('name="has_paywall_cookie"') == len(feeds)
    assert html.count('aria-label="Cookie:') == len(feeds)


def test_cookie_checkbox_is_labelled_like_read_only_and_subscriber(app_env):
    """All three boolean columns use the same aria-label shape and carry no
    per-row title, so one doesn't read differently from the others."""
    with _client(app_env) as client:
        html = client.get("/admin/library/feeds").text
        lib = app_env._lib()
        try:
            name = lib.list_feeds()[0]["name"]
        finally:
            lib.close()
    for label in ("Read only", "Cookie", "Subscriber"):
        assert f'aria-label="{label}: {name}"' in html


def test_footnote_explains_the_column_once(app_env):
    with _client(app_env) as client:
        html = client.get("/admin/library/feeds").text
    assert "<strong>Cookie</strong> marks a feed whose full text needs" in html
    assert "No cookie value is ever stored in this database" in html


def test_no_free_text_note_field_remains(app_env):
    """The whole point of the change: no per-row text entry anywhere."""
    with _client(app_env) as client:
        table = client.get("/admin/library/feeds").text
        form = client.get("/admin/library/feeds/new").text
    for html in (table, form):
        assert 'name="paywall_cookie_note"' not in html


def test_cookie_row_toggle_posts_and_persists(app_env):
    with _client(app_env) as client:
        lib = app_env._lib()
        try:
            feed = [f for f in lib.list_feeds() if not f["has_paywall_cookie"]][0]
        finally:
            lib.close()
        fid = feed["id"]
        resp = client.post(f"/admin/library/feeds/{fid}/cookie",
                           data={"has_paywall_cookie": "1"}, follow_redirects=False)
        assert resp.status_code == 303

        lib = app_env._lib()
        try:
            after = lib.get_feed(fid)
        finally:
            lib.close()
    assert after["has_paywall_cookie"] == 1
    assert after["xml_url"] == feed["xml_url"]


def test_row_toggle_unchecked_clears_it(app_env):
    with _client(app_env) as client:
        lib = app_env._lib()
        try:
            feed = [f for f in lib.list_feeds() if f["has_paywall_cookie"]][0]
        finally:
            lib.close()
        fid = feed["id"]
        client.post(f"/admin/library/feeds/{fid}/cookie", data={},
                    follow_redirects=False)
        lib = app_env._lib()
        try:
            assert lib.get_feed(fid)["has_paywall_cookie"] == 0
        finally:
            lib.close()


def test_edit_form_round_trips_the_cookie_flag(app_env):
    with _client(app_env) as client:
        lib = app_env._lib()
        try:
            feed = [f for f in lib.list_feeds() if f["has_paywall_cookie"]][0]
        finally:
            lib.close()
        html = client.get(f"/admin/library/feeds/{feed['id']}/edit").text
    assert 'name="has_paywall_cookie" value="1" checked' in html


def test_editing_a_feed_without_touching_the_cookie_flag_keeps_it(app_env):
    with _client(app_env) as client:
        lib = app_env._lib()
        try:
            feed = [f for f in lib.list_feeds() if f["has_paywall_cookie"]][0]
        finally:
            lib.close()
        fid = feed["id"]
        client.post(f"/admin/library/feeds/{fid}/edit", data={
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
    stays left."""
    with _client(app_env) as client:
        html = client.get("/admin/library/feeds").text
    for label in ("Read only", "Cookie", "Subscriber"):
        assert f'text-align:center;">{label}</th>' in html
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
        html = client.get("/admin/library/feeds").text
    assert ">Subscriber</th>" in html
    assert 'name="has_active_subscription"' in html


def test_row_checkbox_reflects_the_seeded_state(app_env):
    with _client(app_env) as client:
        html = client.get("/admin/library/feeds").text
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

        resp = client.post(f"/admin/library/feeds/{fid}/subscription",
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

        client.post(f"/admin/library/feeds/{fid}/subscription", data={},
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
        html = client.get(f"/admin/library/feeds/{feed['id']}/edit").text
    assert 'name="has_active_subscription" value="1" checked' in html


def test_editing_a_feed_without_touching_the_flag_keeps_it(app_env):
    with _client(app_env) as client:
        lib = app_env._lib()
        try:
            feed = [f for f in lib.list_feeds() if f["has_active_subscription"]][0]
        finally:
            lib.close()
        fid = feed["id"]

        client.post(f"/admin/library/feeds/{fid}/edit", data={
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
        html = client.get("/admin/library/feeds/new").text
    assert "as a note to yourself" in html
    assert "doesn&#x27;t affect fetching" in html or \
           "doesn't affect fetching" in html
    assert "Nothing in the app reads this" in html


def test_page_footnote_explains_the_flag(app_env):
    with _client(app_env) as client:
        html = client.get("/admin/library/feeds").text
    assert "<strong>Subscriber</strong> marks whether you currently pay" in html


def test_mobile_labels_the_subscription_cell_unconditionally(app_env):
    """Unlike the cookie cell, a checkbox carries meaning in both states, so an
    unchecked box still needs its label in the stacked layout."""
    with _client(app_env) as client:
        html = client.get("/admin/library/feeds").text
    assert '.ff-sub::before{content:"Subscriber"' in html


# ---------------------------------------------------------------------------
# Part 2: the New content quadrant's count
# ---------------------------------------------------------------------------

# DOM order is column-major: left column (new, tags) then right (existing,
# backup). Matching on `class="..."` rather than the bare class name is what
# keeps this off the CSS rule block, which writes `.lib-q-new{`.
_QUADRANT_ORDER = ["lib-q-new", "lib-q-tags", "lib-q-existing", "lib-q-backup"]


def _quadrant(html, cls):
    start = html.index(f'class="{cls}"')
    after = _QUADRANT_ORDER[_QUADRANT_ORDER.index(cls) + 1:]
    ends = [html.index(f'class="{c}"') for c in after if f'class="{c}"' in html]
    return html[start:min(ends)] if ends else html[start:]


def test_new_content_quadrant_counts_three_tools(app_env):
    """One _lib_card plus the two capture-method accordions."""
    with _client(app_env) as client:
        html = client.get("/admin/library").text
    quadrant = _quadrant(html, "lib-q-new")
    assert "3 tools" in quadrant
    assert "1 tool" not in quadrant


def test_other_quadrants_still_count_their_real_cards(app_env):
    """The override is scoped to one quadrant; the rest stay literal."""
    with _client(app_env) as client:
        html = client.get("/admin/library").text
    assert "3 tools" in _quadrant(html, "lib-q-existing")
    assert "2 tools" in _quadrant(html, "lib-q-backup")
    assert "3 tools" in _quadrant(html, "lib-q-tags")


def test_shared_disclosure_component_has_no_count_override(app_env):
    """The override lives on admin_library's local _lib_quadrant closure, not
    on the component /admin shares with it. _disclosure_group only ever
    receives a finished `count_label` string, so there is no parameter through
    which a fabricated count could reach the index page."""
    import inspect
    import webapp.app as appmod
    params = inspect.signature(appmod._disclosure_group).parameters
    assert "count_override" not in params
    assert "count_label" in params


def test_admin_index_counts_are_unchanged_by_rendering_the_library(app_env):
    """Rendering /admin/library must not perturb /admin's own counts.

    Compared before and after in one process rather than against hardcoded
    numbers: /admin's CFO Toolbox group renders a nested count that isn't
    len(items), so pinning literals here would encode a wrong assumption about
    a page this PR doesn't touch.
    """
    import re
    with _client(app_env) as client:
        before = re.findall(r">(\d+) tools?</span>", client.get("/admin").text)
        client.get("/admin/library")
        after = re.findall(r">(\d+) tools?</span>", client.get("/admin").text)

    assert before, "expected /admin to render counted groups"
    assert before == after
    # And the override's value isn't simply everywhere already.
    assert before.count("3") < len(before)
