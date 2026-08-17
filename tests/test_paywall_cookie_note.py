"""The feeds table's descriptive "Paywall cookie" note, and the New content
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
from linklib.feed import PAYWALLED_DOMAINS, parse_opml

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

def test_note_defaults_to_empty_for_every_seeded_feed(seeded):
    """No retroactive flagging — same precedent as every other added column."""
    assert all(f["paywall_cookie_note"] == "" for f in seeded.list_feeds())


def test_seeding_annotates_every_known_paywalled_feed(seeded):
    result = seeded.seed_paywall_cookie_notes()
    assert result["seeded"] is True

    annotated = {f["name"]: f["paywall_cookie_note"]
                 for f in seeded.list_feeds() if f["paywall_cookie_note"]}
    assert len(annotated) == result["feeds"]
    assert annotated, "expected at least one paywalled feed in the curated OPML"

    # Every annotated feed really is on a known-paywalled domain, and every
    # known-paywalled subscribed feed got annotated — not just the one that
    # prompted the feature.
    for f in seeded.list_feeds():
        on_paywalled_domain = any(
            dom in f"{f['xml_url']} {f['html_url']}" for dom in PAYWALLED_DOMAINS)
        assert bool(f["paywall_cookie_note"]) is on_paywalled_domain


def test_seeded_note_names_the_env_var_and_holds_no_cookie_value(seeded):
    """The note is a pointer, not a secret. It must name where to look and
    carry nothing that resembles a cookie."""
    seeded.seed_paywall_cookie_notes()
    notes = {f["paywall_cookie_note"] for f in seeded.list_feeds()
             if f["paywall_cookie_note"]}
    assert notes == {"Cookie auth via LINKLIB_AUTH_COOKIES"}
    for note in notes:
        assert "=" not in note, "a '=' suggests a literal cookie pair"
        assert ";" not in note


def test_mostly_metrics_specifically_gets_a_note(seeded):
    """The feed that prompted the feature, called out by name so a future
    change to PAYWALLED_DOMAINS can't silently drop it."""
    seeded.seed_paywall_cookie_notes()
    mm = [f for f in seeded.list_feeds() if "mostlymetrics.com" in f["xml_url"]]
    assert len(mm) == 1
    assert mm[0]["paywall_cookie_note"] == "Cookie auth via LINKLIB_AUTH_COOKIES"
    # And its URL is untouched by the annotation pass.
    assert mm[0]["xml_url"] == "https://www.mostlymetrics.com/feed"


def test_seeding_is_flag_guarded_not_emptiness_guarded(seeded):
    """A deliberately cleared note stays cleared across restarts.

    This is the _seed_toolbox bug in miniature: an "is it empty" check looks
    identical on a fresh DB and then resurrects the note on the next boot.
    """
    seeded.seed_paywall_cookie_notes()
    mm = [f for f in seeded.list_feeds() if "mostlymetrics.com" in f["xml_url"]][0]
    seeded.set_feed_paywall_cookie_note(mm["id"], "")

    again = seeded.seed_paywall_cookie_notes()
    assert again["seeded"] is False
    assert again["feeds"] == 0

    after = seeded.get_feed(mm["id"])
    assert after["paywall_cookie_note"] == ""


def test_seeding_does_not_burn_its_flag_on_an_empty_feeds_table(lib):
    """An unreadable OPML on one boot must not permanently skip the notes for
    every feed seeded afterwards."""
    assert lib.list_feeds() == []
    first = lib.seed_paywall_cookie_notes()
    assert first == {"seeded": False, "feeds": 0}

    lib.seed_feeds_from_opml(REPO_OPML)
    second = lib.seed_paywall_cookie_notes()
    assert second["seeded"] is True
    assert second["feeds"] > 0


def test_seeding_never_overwrites_an_existing_note(seeded):
    mm = [f for f in seeded.list_feeds() if "mostlymetrics.com" in f["xml_url"]][0]
    seeded.set_feed_paywall_cookie_note(mm["id"], "Hand-written note")
    seeded.seed_paywall_cookie_notes()
    assert seeded.get_feed(mm["id"])["paywall_cookie_note"] == "Hand-written note"


# ---------------------------------------------------------------------------
# The note stays out of the OPML
# ---------------------------------------------------------------------------

def test_note_is_absent_from_the_generated_opml(seeded, tmp_path):
    """The OPML has no field for it, exactly like exclude_from_queue. A note
    edit must not churn the generated file."""
    seeded.seed_paywall_cookie_notes()
    assert "paywall_cookie_note" not in seeded.opml_xml()
    assert "LINKLIB_AUTH_COOKIES" not in seeded.opml_xml()


def test_editing_a_note_leaves_the_generated_opml_byte_identical(seeded):
    before = seeded.opml_xml()
    mm = [f for f in seeded.list_feeds() if "mostlymetrics.com" in f["xml_url"]][0]
    seeded.set_feed_paywall_cookie_note(mm["id"], "Something completely different")
    assert seeded.opml_xml() == before


def test_the_note_column_survives_an_opml_round_trip(seeded, tmp_path):
    """Regenerating and reparsing the file must not disturb stored notes —
    the file is a cache of the DB, not the other way round."""
    seeded.seed_paywall_cookie_notes()
    before = {f["xml_url"]: f["paywall_cookie_note"] for f in seeded.list_feeds()}

    out = tmp_path / "regen.opml"
    seeded.write_opml(str(out))
    assert len(parse_opml(str(out))) == len(before)

    after = {f["xml_url"]: f["paywall_cookie_note"] for f in seeded.list_feeds()}
    assert after == before


# ---------------------------------------------------------------------------
# Narrow writers can't rewrite a URL in passing
# ---------------------------------------------------------------------------

def test_setting_a_note_touches_only_that_column(seeded):
    mm = [f for f in seeded.list_feeds() if "mostlymetrics.com" in f["xml_url"]][0]
    seeded.set_feed_paywall_cookie_note(mm["id"], "Cookie auth via LINKLIB_AUTH_COOKIES")
    after = seeded.get_feed(mm["id"])
    for field in ("xml_url", "html_url", "name", "section_id", "exclude_from_queue"):
        assert after[field] == mm[field]


def test_note_round_trips_verbatim_through_add_and_update(lib):
    sid = lib.add_feed_section("Substacks")
    note = "Cookie auth via LINKLIB_AUTH_COOKIES (beehiiv session)"
    fid = lib.add_feed(sid, "Paid", "https://paid.example/feed?tok=abc&u=1",
                       "https://paid.example/", paywall_cookie_note=note)
    assert lib.get_feed(fid)["paywall_cookie_note"] == note
    # And the tokenized URL guarantee still holds alongside it.
    assert lib.get_feed(fid)["xml_url"] == "https://paid.example/feed?tok=abc&u=1"

    lib.update_feed(fid, sid, "Paid", "https://paid.example/feed?tok=abc&u=1",
                    "https://paid.example/", paywall_cookie_note=note)
    assert lib.get_feed(fid)["paywall_cookie_note"] == note
    assert lib.get_feed(fid)["xml_url"] == "https://paid.example/feed?tok=abc&u=1"


# ---------------------------------------------------------------------------
# Admin page rendering
# ---------------------------------------------------------------------------

def test_feed_table_has_a_paywall_cookie_column(app_env):
    with _client(app_env) as client:
        html = client.get("/admin/library/feeds").text
    assert ">Paywall cookie</th>" in html


def test_indicator_renders_only_for_annotated_feeds(app_env):
    """Sparse marks, not a grid of mostly-empty cells."""
    with _client(app_env) as client:
        html = client.get("/admin/library/feeds").text
        lib = app_env._lib()
        try:
            feeds = lib.list_feeds()
        finally:
            lib.close()

    expected = sum(1 for f in feeds if f["paywall_cookie_note"])
    assert 0 < expected < len(feeds), "need a mix of annotated and plain feeds"
    # The badge markup appears once per annotated feed: once in the CSS rule
    # block, then once per row.
    assert html.count('class="ff-cookie-badge"') == expected


def test_note_text_rides_on_the_title_attribute_not_inline(app_env):
    with _client(app_env) as client:
        html = client.get("/admin/library/feeds").text
        lib = app_env._lib()
        try:
            note = [f for f in lib.list_feeds() if f["paywall_cookie_note"]][0]["paywall_cookie_note"]
        finally:
            lib.close()

    assert f'title="{note}"' in html
    # The badge carries an accessible name too — a bare title on a span is not
    # reliably announced.
    assert f'aria-label="Paywall cookie. {note}"' in html
    # The note never renders as visible cell text.
    assert f'<td class="ff-cookie">{note}' not in html


def test_page_footnote_explains_the_column(app_env):
    with _client(app_env) as client:
        html = client.get("/admin/library/feeds").text
    assert "<strong>Paywall cookie</strong> marks a feed" in html
    assert "the cookie value itself never lives in this database" in html


def test_mobile_labels_only_the_cells_that_carry_a_badge(app_env):
    """An unconditional ::before would print the label above an empty cell on
    every unpaywalled feed."""
    with _client(app_env) as client:
        html = client.get("/admin/library/feeds").text
    assert '.ff-cookie:has(.ff-cookie-badge)::before' in html
    assert '.ff-cookie:not(:has(.ff-cookie-badge)){display:none;}' in html


def test_edit_form_round_trips_the_note(app_env):
    with _client(app_env) as client:
        lib = app_env._lib()
        try:
            feed = [f for f in lib.list_feeds() if f["paywall_cookie_note"]][0]
        finally:
            lib.close()
        html = client.get(f"/admin/library/feeds/{feed['id']}/edit").text
    assert 'name="paywall_cookie_note"' in html
    assert f'value="{feed["paywall_cookie_note"]}"' in html


def test_form_helper_copy_warns_against_pasting_the_cookie(app_env):
    with _client(app_env) as client:
        html = client.get("/admin/library/feeds/new").text
    assert "not the cookie value itself" in html
    assert "keep secrets out of it" in html


def test_editing_a_feed_without_touching_the_note_keeps_it(app_env):
    """update_feed writes the column on every call, so the form has to
    round-trip it — a save that only renames must not blank the note."""
    with _client(app_env) as client:
        lib = app_env._lib()
        try:
            feed = [f for f in lib.list_feeds() if f["paywall_cookie_note"]][0]
        finally:
            lib.close()
        note, fid = feed["paywall_cookie_note"], feed["id"]

        resp = client.post(f"/admin/library/feeds/{fid}/edit", data={
            "name": "Renamed", "xml_url": feed["xml_url"],
            "html_url": feed["html_url"], "section_id": str(feed["section_id"]),
            "paywall_cookie_note": note,
        }, follow_redirects=False)
    assert resp.status_code == 303

    lib = app_env._lib()
    try:
        after = lib.get_feed(fid)
    finally:
        lib.close()
    assert after["name"] == "Renamed"
    assert after["paywall_cookie_note"] == note
    assert after["xml_url"] == feed["xml_url"]


def test_clearing_the_note_through_the_form_really_clears_it(app_env):
    with _client(app_env) as client:
        lib = app_env._lib()
        try:
            feed = [f for f in lib.list_feeds() if f["paywall_cookie_note"]][0]
        finally:
            lib.close()
        fid = feed["id"]

        client.post(f"/admin/library/feeds/{fid}/edit", data={
            "name": feed["name"], "xml_url": feed["xml_url"],
            "html_url": feed["html_url"], "section_id": str(feed["section_id"]),
            "paywall_cookie_note": "",
        }, follow_redirects=False)

    lib = app_env._lib()
    try:
        assert lib.get_feed(fid)["paywall_cookie_note"] == ""
    finally:
        lib.close()


def test_startup_hook_seeds_the_notes(app_env):
    with _client(app_env):
        lib = app_env._lib()
        try:
            annotated = [f for f in lib.list_feeds() if f["paywall_cookie_note"]]
        finally:
            lib.close()
    assert annotated, "the startup hook should have annotated the paywalled feeds"


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
