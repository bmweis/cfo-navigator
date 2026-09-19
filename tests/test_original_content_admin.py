"""Original Content Phase 3 — admin CRUD at /admin/thought-leadership/original."""
import os
import pathlib
import sys
import tempfile

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


class _AllSlugs:
    """A container that reports every slug as present — stands in for
    _og_image_slugs()'s real frozenset wherever a test doesn't care about
    card state."""
    def __contains__(self, _slug):
        return True


@pytest.fixture(autouse=True)
def _og_card_exists_by_default(env, monkeypatch):
    """This file predates the social-share-card publish gate (see
    _oc_publish_gate_error) and uses many synthetic slugs (a-test-piece,
    second-piece, ...) with no real webapp/static/og/<slug>.png committed.
    Default the gate's own card-existence lookup to "present for
    everything" so the pre-existing CRUD/validation tests here don't each
    need a fabricated file — the dedicated gate tests further down
    override this per-test (to an empty frozenset, or one naming a
    specific slug) to exercise the real missing/present states."""
    monkeypatch.setattr(env, "_og_image_slugs", lambda: _AllSlugs())


def _client(appmod):
    from fastapi.testclient import TestClient
    return TestClient(appmod.app, raise_server_exceptions=True)


def _admin_client(appmod):
    c = _client(appmod)
    c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    return c


VALID_FORM = {
    "title": "A Test Piece",
    "slug": "a-test-piece",
    "teaser": "A teaser",
    "tag_label": "Guide",
    # link_label is no longer a real form field (it's derived server-side
    # from tag_label) — submitted here anyway, deliberately mismatched
    # ("Read it" vs. Guide's real "Read the guide"), to prove a direct POST
    # can't desync the two. See test_create_persists_and_reads_back.
    "link_label": "Read it",
    "date_label": "Jan 2027",
    "body_md": "# Hi\n\nBody.",
    "status": "live",
    "featured_home": "1",
    "display_order": "",
}


# -- Auth ----------------------------------------------------------------

def test_list_requires_admin_session(env):
    r = _client(env).get("/admin/thought-leadership/original", follow_redirects=False)
    assert r.status_code in (302, 303)
    assert "/login" in r.headers["location"]


def test_new_form_requires_admin_session(env):
    r = _client(env).get("/admin/thought-leadership/original/new", follow_redirects=False)
    assert r.status_code in (302, 303)
    assert "/login" in r.headers["location"]


def test_post_new_requires_admin_session(env):
    r = _client(env).post("/admin/thought-leadership/original/new", data=VALID_FORM)
    assert r.status_code == 401


def test_admin_session_can_reach_list(env):
    r = _admin_client(env).get("/admin/thought-leadership/original")
    assert r.status_code == 200
    assert "Original content" in r.text


# -- CRUD round-trip -------------------------------------------------------

def test_create_persists_and_reads_back(env):
    c = _admin_client(env)
    r = c.post("/admin/thought-leadership/original/new", data=VALID_FORM, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/admin/thought-leadership/original"

    lib = env._lib()
    try:
        row = lib.get_original_content_by_slug("a-test-piece")
    finally:
        lib.close()
    assert row is not None
    assert row["title"] == "A Test Piece"
    assert row["teaser"] == "A teaser"
    assert row["tag_label"] == "Guide"
    # Derived from the tag, not the submitted "Read it" — a direct POST
    # attempting to set link_label is ignored and the derived value wins.
    assert row["link_label"] == "Read the guide"
    assert row["body_md"] == "# Hi\n\nBody."
    assert row["status"] == "live"
    assert row["featured_home"] == 1
    assert row["sort_key"] == "2027-01"

    list_html = c.get("/admin/thought-leadership/original").text
    assert "A Test Piece" in list_html
    assert "a-test-piece" in list_html


def test_edit_persists_changes(env):
    c = _admin_client(env)
    c.post("/admin/thought-leadership/original/new", data=VALID_FORM, follow_redirects=False)
    lib = env._lib()
    try:
        item_id = lib.get_original_content_by_slug("a-test-piece")["id"]
    finally:
        lib.close()

    edit_form = dict(VALID_FORM)
    edit_form["title"] = "An Edited Title"
    edit_form["status"] = "draft"
    edit_form["display_order"] = "3"
    r = c.post(f"/admin/thought-leadership/original/{item_id}/edit", data=edit_form, follow_redirects=False)
    assert r.status_code == 303

    lib = env._lib()
    try:
        row = lib.get_original_content(item_id)
    finally:
        lib.close()
    assert row["title"] == "An Edited Title"
    assert row["status"] == "draft"
    assert row["display_order"] == 3


def test_edit_form_prefills_existing_values(env):
    c = _admin_client(env)
    c.post("/admin/thought-leadership/original/new", data=VALID_FORM, follow_redirects=False)
    lib = env._lib()
    try:
        item_id = lib.get_original_content_by_slug("a-test-piece")["id"]
    finally:
        lib.close()
    html = c.get(f"/admin/thought-leadership/original/{item_id}/edit").text
    assert 'value="A Test Piece"' in html
    assert 'value="a-test-piece"' in html
    assert "# Hi" in html


def test_delete_removes_row(env):
    c = _admin_client(env)
    c.post("/admin/thought-leadership/original/new", data=VALID_FORM, follow_redirects=False)
    lib = env._lib()
    try:
        item_id = lib.get_original_content_by_slug("a-test-piece")["id"]
    finally:
        lib.close()
    r = c.post(f"/admin/thought-leadership/original/{item_id}/delete", follow_redirects=False)
    assert r.status_code == 303
    lib = env._lib()
    try:
        assert lib.get_original_content(item_id) is None
    finally:
        lib.close()


def test_edit_page_title_is_not_double_escaped(env):
    """_page() escapes its own title argument internally — the edit route
    must pass the raw title, not a pre-_esc()'d one, or an "&" in the title
    renders as "&amp;amp;" instead of "&amp;" (the same class of bug
    CLAUDE.md's "Speaking &amp; Events" fix covers)."""
    c = _admin_client(env)
    form = dict(VALID_FORM)
    form["title"] = "AI & Finance"
    form["slug"] = "ai-and-finance"
    c.post("/admin/thought-leadership/original/new", data=form, follow_redirects=False)
    lib = env._lib()
    try:
        item_id = lib.get_original_content_by_slug("ai-and-finance")["id"]
    finally:
        lib.close()
    html = c.get(f"/admin/thought-leadership/original/{item_id}/edit").text
    assert "&amp;amp;" not in html
    assert "<title>BMW CFO · Edit AI &amp; Finance</title>" in html


def test_edit_of_unknown_id_404s(env):
    c = _admin_client(env)
    r = c.post("/admin/thought-leadership/original/99999/edit", data=VALID_FORM)
    assert r.status_code == 404


# -- Slug validation ---------------------------------------------------------

@pytest.mark.parametrize("bad_slug", ["Bad Slug", "bad_slug", "bad--slug".upper(), "bad slug!", "-leading",
                                        "trailing-", "UPPER-CASE"])
def test_malformed_slug_rejected(env, bad_slug):
    c = _admin_client(env)
    form = dict(VALID_FORM)
    form["slug"] = bad_slug
    r = c.post("/admin/thought-leadership/original/new", data=form)
    assert r.status_code == 400
    assert "lowercase letters" in r.text
    lib = env._lib()
    try:
        assert lib.list_original_content() == []
    finally:
        lib.close()


def test_double_hyphen_slug_rejected(env):
    c = _admin_client(env)
    form = dict(VALID_FORM)
    form["slug"] = "double--hyphen"
    r = c.post("/admin/thought-leadership/original/new", data=form)
    assert r.status_code == 400
    assert "lowercase letters" in r.text


def test_no_reserved_slugs_remain(env):
    """Original Content Phase 4c retired growth-engine-ratio's bespoke
    route — the last of the three original bespoke /thought-leadership/*
    pieces (netsuite-mcp in 4a, ai-hackathon-playbook in 4b,
    growth-engine-ratio in 4c). _OC_RESERVED_SLUGS is now genuinely empty;
    the parametrized test_slug_colliding_with_bespoke_route_rejected this
    test replaces had no reserved slug left to parametrize against, so it
    was removed rather than left with an empty parametrize list."""
    import webapp.app as appmod
    assert appmod._OC_RESERVED_SLUGS == set()


def test_growth_engine_calculator_not_reserved(env):
    """The new standalone /thought-leadership/growth-engine-calculator
    route (Phase 4c) was never part of the original_content system and
    never will be — there's no slug collision to guard against, so it's
    deliberately NOT added to _OC_RESERVED_SLUGS. An admin creating a piece
    with this exact slug succeeds normally (it just won't be reachable at
    /thought-leadership/growth-engine-calculator, since that URL is served
    by the standalone bespoke route, not the catch-all — a real but
    accepted quirk, not something this form is responsible for guarding
    against)."""
    c = _admin_client(env)
    form = dict(VALID_FORM)
    form["slug"] = "growth-engine-calculator"
    r = c.post("/admin/thought-leadership/original/new", data=form, follow_redirects=False)
    assert r.status_code == 303
    lib = env._lib()
    try:
        assert lib.get_original_content_by_slug("growth-engine-calculator") is not None
    finally:
        lib.close()


@pytest.mark.parametrize("freed_slug", ["netsuite-mcp", "ai-hackathon-playbook", "growth-engine-ratio"])
def test_retired_bespoke_slugs_no_longer_reserved(env, freed_slug):
    """Original Content Phase 4a retired the netsuite-mcp bespoke route,
    Phase 4b retired ai-hackathon-playbook's, and Phase 4c retired
    growth-engine-ratio's — all three slugs are ordinary, admin-editable
    slugs now, not a collision with anything. (A pre-existing row with this
    slug, from the Phase 1 seed, would still trip the plain duplicate-slug
    check — this test uses a fresh DB with no such row, to isolate the
    reserved-slug behavior specifically.)"""
    c = _admin_client(env)
    form = dict(VALID_FORM)
    form["slug"] = freed_slug
    r = c.post("/admin/thought-leadership/original/new", data=form, follow_redirects=False)
    assert r.status_code == 303
    lib = env._lib()
    try:
        assert lib.get_original_content_by_slug(freed_slug) is not None
    finally:
        lib.close()


def test_duplicate_slug_rejected(env):
    c = _admin_client(env)
    c.post("/admin/thought-leadership/original/new", data=VALID_FORM, follow_redirects=False)
    form = dict(VALID_FORM)
    form["title"] = "A Different Title"
    r = c.post("/admin/thought-leadership/original/new", data=form)
    assert r.status_code == 400
    assert "already used" in r.text
    lib = env._lib()
    try:
        assert len(lib.list_original_content()) == 1
    finally:
        lib.close()


def test_edit_can_keep_its_own_slug(env):
    """Editing a row without changing its slug must not trip the
    duplicate-slug check against itself."""
    c = _admin_client(env)
    c.post("/admin/thought-leadership/original/new", data=VALID_FORM, follow_redirects=False)
    lib = env._lib()
    try:
        item_id = lib.get_original_content_by_slug("a-test-piece")["id"]
    finally:
        lib.close()
    r = c.post(f"/admin/thought-leadership/original/{item_id}/edit", data=VALID_FORM, follow_redirects=False)
    assert r.status_code == 303


def test_edit_rejects_slug_colliding_with_another_existing_row(env):
    c = _admin_client(env)
    c.post("/admin/thought-leadership/original/new", data=VALID_FORM, follow_redirects=False)
    second = dict(VALID_FORM)
    second["slug"] = "second-piece"
    second["title"] = "Second Piece"
    c.post("/admin/thought-leadership/original/new", data=second, follow_redirects=False)

    lib = env._lib()
    try:
        second_id = lib.get_original_content_by_slug("second-piece")["id"]
    finally:
        lib.close()

    edit_form = dict(second)
    edit_form["slug"] = "a-test-piece"  # collides with the first piece
    r = c.post(f"/admin/thought-leadership/original/{second_id}/edit", data=edit_form)
    assert r.status_code == 400
    assert "already used" in r.text


def test_slug_can_be_changed_on_a_live_piece(env):
    """No restriction beyond uniqueness/collision — slug edits are allowed
    at any time, including on a live piece."""
    c = _admin_client(env)
    c.post("/admin/thought-leadership/original/new", data=VALID_FORM, follow_redirects=False)
    lib = env._lib()
    try:
        item_id = lib.get_original_content_by_slug("a-test-piece")["id"]
    finally:
        lib.close()
    edit_form = dict(VALID_FORM)
    edit_form["slug"] = "a-renamed-piece"
    r = c.post(f"/admin/thought-leadership/original/{item_id}/edit", data=edit_form, follow_redirects=False)
    assert r.status_code == 303
    lib = env._lib()
    try:
        assert lib.get_original_content_by_slug("a-renamed-piece") is not None
        assert lib.get_original_content_by_slug("a-test-piece") is None
    finally:
        lib.close()


def test_edit_form_warns_about_slug_change_breaking_links(env):
    html = _admin_client(env).get("/admin/thought-leadership/original/new").text
    assert "no redirect system" in html or "there&rsquo;s no" in html
    assert "breaks any link" in html


# -- Required-field validation ------------------------------------------------

@pytest.mark.parametrize("missing_field", ["title", "teaser", "tag_label"])
def test_missing_required_field_rejected(env, missing_field):
    c = _admin_client(env)
    form = dict(VALID_FORM)
    form[missing_field] = ""
    r = c.post("/admin/thought-leadership/original/new", data=form)
    assert r.status_code == 400
    lib = env._lib()
    try:
        assert lib.list_original_content() == []
    finally:
        lib.close()


# -- Tag taxonomy (closed enum, derived link_label) ---------------------------

def test_invalid_tag_label_rejected(env):
    c = _admin_client(env)
    form = dict(VALID_FORM)
    form["tag_label"] = "Essay"
    r = c.post("/admin/thought-leadership/original/new", data=form)
    assert r.status_code == 400
    lib = env._lib()
    try:
        assert lib.list_original_content() == []
    finally:
        lib.close()


@pytest.mark.parametrize("tag,expected_link_label", [
    ("Guide", "Read the guide"),
    ("Playbook", "Read the playbook"),
    ("Framework", "Read the framework"),
])
def test_each_valid_tag_derives_its_link_label(env, tag, expected_link_label):
    c = _admin_client(env)
    form = dict(VALID_FORM)
    form["tag_label"] = tag
    r = c.post("/admin/thought-leadership/original/new", data=form, follow_redirects=False)
    assert r.status_code == 303
    lib = env._lib()
    try:
        row = lib.get_original_content_by_slug("a-test-piece")
    finally:
        lib.close()
    assert row["tag_label"] == tag
    assert row["link_label"] == expected_link_label


def test_link_label_is_not_a_submittable_form_field(env):
    html = _admin_client(env).get("/admin/thought-leadership/original/new").text
    assert 'name="link_label"' not in html
    # No tag selected yet on a fresh Add form — the no-tag caption text,
    # not a "no tag was selected" placeholder box.
    assert 'id="oc-link-caption"' in html
    assert "Link text is set from the tag." in html


def test_tag_dropdown_offers_exactly_three_options_with_no_default_selected(env):
    """The Add form shouldn't preselect any of the three tags — a default
    is how records get mislabeled by omission. The disabled placeholder
    (never a submittable value) is what's initially shown instead. Each
    option also carries its own data-link caption text, read by the page's
    one-line JS listener with no fallback string duplicated there."""
    html = _admin_client(env).get("/admin/thought-leadership/original/new").text
    assert 'name="tag_label"' in html
    for tag, link_label in (("Guide", "Read the guide"), ("Playbook", "Read the playbook"),
                             ("Framework", "Read the framework")):
        assert f'<option value="{tag}" data-link="Link: {link_label}">{tag}</option>' in html
    assert '<option value="" disabled selected data-link="Link text is set from the tag.">' in html
    assert "Setup Guide" not in html


def test_edit_form_preselects_the_current_tag(env):
    c = _admin_client(env)
    form = dict(VALID_FORM)
    form["tag_label"] = "Framework"
    c.post("/admin/thought-leadership/original/new", data=form, follow_redirects=False)
    lib = env._lib()
    try:
        item_id = lib.get_original_content_by_slug("a-test-piece")["id"]
    finally:
        lib.close()
    html = c.get(f"/admin/thought-leadership/original/{item_id}/edit").text
    assert '<option value="Framework" selected data-link="Link: Read the framework">Framework</option>' in html
    # Server-rendered on first paint, before any JS runs.
    assert '<p id="oc-link-caption" style="margin:6px 0 0;font-size:12px;color:var(--muted);">Link: Read the framework</p>' in html


def test_live_update_listener_is_the_only_new_js_on_the_form(env):
    """One small vanilla-JS function, wired via a plain onchange attribute
    — no second copy of the tag->link mapping serialized into the page."""
    html = _admin_client(env).get("/admin/thought-leadership/original/new").text
    assert 'onchange="ocLinkCaption(this)"' in html
    assert html.count("<script>") == 1
    assert "function ocLinkCaption(s)" in html
    assert "JSON" not in html.split("<script>", 1)[1]


def test_non_numeric_display_order_rejected(env):
    c = _admin_client(env)
    form = dict(VALID_FORM)
    form["display_order"] = "not-a-number"
    r = c.post("/admin/thought-leadership/original/new", data=form)
    assert r.status_code == 400
    assert "number" in r.text


# -- sort_key derivation -------------------------------------------------------

def test_sort_key_derived_from_date_label_on_create(env):
    c = _admin_client(env)
    form = dict(VALID_FORM)
    form["date_label"] = "March 2027"
    c.post("/admin/thought-leadership/original/new", data=form, follow_redirects=False)
    lib = env._lib()
    try:
        row = lib.get_original_content_by_slug("a-test-piece")
    finally:
        lib.close()
    assert row["sort_key"] == "2027-03"


def test_unparseable_date_label_yields_blank_sort_key_and_warning(env):
    """An unparseable date_label is not a hard rejection — the piece still
    saves, sort_key just comes back blank, and the edit form shows the
    parse warning (same convention as thought_leadership's own form)."""
    c = _admin_client(env)
    form = dict(VALID_FORM)
    form["date_label"] = "not a real date"
    r = c.post("/admin/thought-leadership/original/new", data=form, follow_redirects=False)
    assert r.status_code == 303

    lib = env._lib()
    try:
        row = lib.get_original_content_by_slug("a-test-piece")
    finally:
        lib.close()
    assert row["sort_key"] == ""

    edit_html = c.get(f"/admin/thought-leadership/original/{row['id']}/edit").text
    assert "didn&rsquo;t parse" in edit_html


def test_sort_key_field_not_exposed_as_form_input(env):
    html = _admin_client(env).get("/admin/thought-leadership/original/new").text
    assert 'name="sort_key"' not in html


# -- display_order auto-assignment --------------------------------------------

def test_display_order_blank_auto_assigns_next_value(env):
    c = _admin_client(env)
    first = dict(VALID_FORM)
    c.post("/admin/thought-leadership/original/new", data=first, follow_redirects=False)
    second = dict(VALID_FORM)
    second["slug"] = "second-piece"
    second["title"] = "Second Piece"
    c.post("/admin/thought-leadership/original/new", data=second, follow_redirects=False)

    lib = env._lib()
    try:
        rows = {r["slug"]: r for r in lib.list_original_content()}
    finally:
        lib.close()
    assert rows["a-test-piece"]["display_order"] != rows["second-piece"]["display_order"]


def test_display_order_explicit_value_respected(env):
    c = _admin_client(env)
    form = dict(VALID_FORM)
    form["display_order"] = "7"
    c.post("/admin/thought-leadership/original/new", data=form, follow_redirects=False)
    lib = env._lib()
    try:
        row = lib.get_original_content_by_slug("a-test-piece")
    finally:
        lib.close()
    assert row["display_order"] == 7


# -- Blank body_md (Phase 1 card-rendering regression) ------------------------

def test_blank_body_md_stays_card_only_and_renders_on_homepage(env):
    c = _admin_client(env)
    form = dict(VALID_FORM)
    form["slug"] = "card-only-piece"
    form["body_md"] = ""
    form["featured_home"] = "1"
    c.post("/admin/thought-leadership/original/new", data=form, follow_redirects=False)

    lib = env._lib()
    try:
        row = lib.get_original_content_by_slug("card-only-piece")
    finally:
        lib.close()
    assert row["body_md"] is None

    home_html = c.get("/").text
    assert "A teaser" in home_html

    tl_html = c.get("/thought-leadership").text
    assert "A teaser" in tl_html

    # No page of its own — body_md IS NULL still 404s at the article route.
    r = c.get("/thought-leadership/card-only-piece")
    assert r.status_code == 404


# -- body_md filled in: reachable at /thought-leadership/{slug} (Phase 2 integration) --

def test_body_md_filled_in_and_live_is_reachable_at_its_own_page(env):
    c = _admin_client(env)
    c.post("/admin/thought-leadership/original/new", data=VALID_FORM, follow_redirects=False)
    r = c.get("/thought-leadership/a-test-piece")
    assert r.status_code == 200
    assert "<h1>Hi</h1>" in r.text
    assert "<p>Body.</p>" in r.text


def test_body_md_filled_in_but_draft_404s_for_anonymous_and_200s_for_admin(env):
    c = _admin_client(env)
    form = dict(VALID_FORM)
    form["status"] = "draft"
    c.post("/admin/thought-leadership/original/new", data=form, follow_redirects=False)

    anon_html = _client(env).get("/thought-leadership/a-test-piece")
    assert anon_html.status_code == 404

    admin_html = c.get("/thought-leadership/a-test-piece")
    assert admin_html.status_code == 200


# -- Social share card publish gate (2026-09) ---------------------------------
# Every test below explicitly overrides the file's own autouse
# _og_card_exists_by_default fixture (a second monkeypatch.setattr call in
# the test body — the last one wins) to exercise the gate's real
# missing/present states; every other test in this file relies on that
# fixture's blanket "card present" default instead.

def test_publish_gate_blocks_live_with_no_card(env, monkeypatch):
    monkeypatch.setattr(env, "_og_image_slugs", lambda: frozenset())
    c = _admin_client(env)
    r = c.post("/admin/thought-leadership/original/new", data=VALID_FORM)
    assert r.status_code == 400
    lib = env._lib()
    try:
        assert lib.list_original_content() == []
    finally:
        lib.close()


def test_publish_gate_error_names_file_and_draft_sequence(env, monkeypatch):
    monkeypatch.setattr(env, "_og_image_slugs", lambda: frozenset())
    c = _admin_client(env)
    r = c.post("/admin/thought-leadership/original/new", data=VALID_FORM)
    assert r.status_code == 400
    assert "needs its own share card before it can go live" in r.text
    assert "1200" in r.text and "630" in r.text
    assert "static/og/a-test-piece.png" in r.text
    assert "Save as Draft" in r.text


def test_publish_gate_allows_draft_with_no_card(env, monkeypatch):
    monkeypatch.setattr(env, "_og_image_slugs", lambda: frozenset())
    c = _admin_client(env)
    form = dict(VALID_FORM)
    form["status"] = "draft"
    r = c.post("/admin/thought-leadership/original/new", data=form, follow_redirects=False)
    assert r.status_code == 303
    lib = env._lib()
    try:
        row = lib.get_original_content_by_slug("a-test-piece")
    finally:
        lib.close()
    assert row is not None
    assert row["status"] == "draft"


def test_publish_gate_allows_live_once_card_exists(env, monkeypatch):
    monkeypatch.setattr(env, "_og_image_slugs", lambda: frozenset({"a-test-piece"}))
    c = _admin_client(env)
    r = c.post("/admin/thought-leadership/original/new", data=VALID_FORM, follow_redirects=False)
    assert r.status_code == 303
    lib = env._lib()
    try:
        row = lib.get_original_content_by_slug("a-test-piece")
    finally:
        lib.close()
    assert row["status"] == "live"


def test_publish_gate_applies_on_edit_too(env, monkeypatch):
    """The block isn't add-only — flipping an existing Draft piece to Live
    on edit is gated the same way."""
    monkeypatch.setattr(env, "_og_image_slugs", lambda: frozenset())
    c = _admin_client(env)
    form = dict(VALID_FORM)
    form["status"] = "draft"
    c.post("/admin/thought-leadership/original/new", data=form, follow_redirects=False)
    lib = env._lib()
    try:
        item_id = lib.get_original_content_by_slug("a-test-piece")["id"]
    finally:
        lib.close()
    edit_form = dict(VALID_FORM)
    edit_form["status"] = "live"
    r = c.post(f"/admin/thought-leadership/original/{item_id}/edit", data=edit_form)
    assert r.status_code == 400
    lib = env._lib()
    try:
        assert lib.get_original_content(item_id)["status"] == "draft"
    finally:
        lib.close()


def test_slug_change_to_uncarded_slug_blocked_on_live_piece(env, monkeypatch):
    """Changing a live piece's slug is only safe if the NEW slug also has
    a card — the lookup is slug-keyed, so a rename would otherwise orphan
    the old card and silently fall back to default.png."""
    monkeypatch.setattr(env, "_og_image_slugs", lambda: frozenset({"a-test-piece"}))
    c = _admin_client(env)
    c.post("/admin/thought-leadership/original/new", data=VALID_FORM, follow_redirects=False)
    lib = env._lib()
    try:
        item_id = lib.get_original_content_by_slug("a-test-piece")["id"]
    finally:
        lib.close()

    edit_form = dict(VALID_FORM)
    edit_form["slug"] = "a-renamed-piece"  # still status=live; new slug has no card
    r = c.post(f"/admin/thought-leadership/original/{item_id}/edit", data=edit_form)
    assert r.status_code == 400
    assert "static/og/a-renamed-piece.png" in r.text
    lib = env._lib()
    try:
        assert lib.get_original_content(item_id)["slug"] == "a-test-piece"
    finally:
        lib.close()


def test_status_helper_shows_missing_card_warning(env, monkeypatch):
    monkeypatch.setattr(env, "_og_image_slugs", lambda: frozenset())
    c = _admin_client(env)
    form = dict(VALID_FORM)
    form["status"] = "draft"
    c.post("/admin/thought-leadership/original/new", data=form, follow_redirects=False)
    lib = env._lib()
    try:
        item_id = lib.get_original_content_by_slug("a-test-piece")["id"]
    finally:
        lib.close()
    html = c.get(f"/admin/thought-leadership/original/{item_id}/edit").text
    assert ("No share card yet. Commit a 1200&times;630 PNG at static/og/a-test-piece.png "
            "before setting this to Live.") in html


def test_status_helper_shows_card_found_confirmation(env, monkeypatch):
    monkeypatch.setattr(env, "_og_image_slugs", lambda: frozenset({"a-test-piece"}))
    c = _admin_client(env)
    c.post("/admin/thought-leadership/original/new", data=VALID_FORM, follow_redirects=False)
    lib = env._lib()
    try:
        item_id = lib.get_original_content_by_slug("a-test-piece")["id"]
    finally:
        lib.close()
    html = c.get(f"/admin/thought-leadership/original/{item_id}/edit").text
    assert "Share card found at static/og/a-test-piece.png." in html
    assert "No share card yet" not in html


def test_status_helper_absent_on_fresh_add_form_with_no_slug_typed(env, monkeypatch):
    """Neither message makes sense before a slug exists to check — the
    line is simply absent, not a guess."""
    monkeypatch.setattr(env, "_og_image_slugs", lambda: frozenset())
    html = _admin_client(env).get("/admin/thought-leadership/original/new").text
    assert "No share card yet" not in html
    assert "Share card found" not in html


def test_admin_list_badges_live_row_missing_card(env, monkeypatch):
    monkeypatch.setattr(env, "_og_image_slugs", lambda: frozenset({"has-a-card"}))
    c = _admin_client(env)
    live_no_card = dict(VALID_FORM)
    live_no_card["slug"] = "has-a-card"
    live_no_card["title"] = "Has A Card"
    c.post("/admin/thought-leadership/original/new", data=live_no_card, follow_redirects=False)

    # Now simulate the card going missing after publish (a deleted file) —
    # the gate only stops NEW occurrences, so this is reachable in
    # production too, per the "defense in depth" framing.
    monkeypatch.setattr(env, "_og_image_slugs", lambda: frozenset())
    html = c.get("/admin/thought-leadership/original").text
    assert "No share card" in html


def test_admin_list_does_not_badge_draft_row_missing_card(env, monkeypatch):
    monkeypatch.setattr(env, "_og_image_slugs", lambda: frozenset())
    c = _admin_client(env)
    form = dict(VALID_FORM)
    form["status"] = "draft"
    c.post("/admin/thought-leadership/original/new", data=form, follow_redirects=False)
    html = c.get("/admin/thought-leadership/original").text
    assert "No share card" not in html


def test_admin_list_does_not_badge_live_row_with_a_card(env, monkeypatch):
    monkeypatch.setattr(env, "_og_image_slugs", lambda: frozenset({"a-test-piece"}))
    c = _admin_client(env)
    c.post("/admin/thought-leadership/original/new", data=VALID_FORM, follow_redirects=False)
    html = c.get("/admin/thought-leadership/original").text
    assert "No share card" not in html


def test_status_helper_is_suppressed_when_the_gate_error_banner_shows_the_same_fact(env, monkeypatch):
    """The banner and the helper read the identical fact off the same
    lookup — showing both is the same sentence twice on one screen. When
    THIS rejection is the gate error, the ambient helper line goes away;
    the banner alone carries the message."""
    monkeypatch.setattr(env, "_og_image_slugs", lambda: frozenset())
    c = _admin_client(env)
    r = c.post("/admin/thought-leadership/original/new", data=VALID_FORM)
    assert r.status_code == 400
    assert "needs its own share card before it can go live" in r.text  # the banner
    assert "No share card yet" not in r.text  # the helper, suppressed


def test_status_helper_still_shows_when_a_different_validation_error_fires(env, monkeypatch):
    """A missing card and an unrelated validation failure (bad tag) can
    coexist — the helper isn't repeating the banner in that case, so it
    stays visible rather than being blanket-suppressed on any error."""
    monkeypatch.setattr(env, "_og_image_slugs", lambda: frozenset())
    c = _admin_client(env)
    form = dict(VALID_FORM)
    form["tag_label"] = "Not A Real Tag"
    r = c.post("/admin/thought-leadership/original/new", data=form)
    assert r.status_code == 400
    assert "Choose a tag." in r.text  # the banner is a DIFFERENT message
    assert "No share card yet" in r.text  # so the helper still carries its own fact


def test_gate_error_banner_uses_the_alert_family_not_coral(env, monkeypatch):
    """BRAND.md reserves coral for decorative use, never for a status/error
    state — this banner is a rejected save, so it must use --alert/
    --alert-wash, never --coral/--coral-wash."""
    monkeypatch.setattr(env, "_og_image_slugs", lambda: frozenset())
    c = _admin_client(env)
    r = c.post("/admin/thought-leadership/original/new", data=VALID_FORM)
    assert r.status_code == 400
    assert "var(--alert-wash)" in r.text
    assert "var(--alert)" in r.text
    assert "var(--coral-wash)" not in r.text


def test_rejected_edit_preserves_attempted_status_but_a_reload_confirms_draft(env, monkeypatch):
    """The rejected response's own Status dropdown correctly shows the
    attempted 'Live' (nothing was silently reset in the form) — but the
    write itself never happened: the DB row stays Draft, and a completely
    fresh GET (not the reject response) confirms Draft renders on reload,
    not a half-applied Live."""
    monkeypatch.setattr(env, "_og_image_slugs", lambda: frozenset())
    c = _admin_client(env)
    form = dict(VALID_FORM)
    form["status"] = "draft"
    c.post("/admin/thought-leadership/original/new", data=form, follow_redirects=False)
    lib = env._lib()
    try:
        item_id = lib.get_original_content_by_slug("a-test-piece")["id"]
    finally:
        lib.close()

    edit_form = dict(VALID_FORM)
    edit_form["status"] = "live"
    reject_html = c.post(f"/admin/thought-leadership/original/{item_id}/edit", data=edit_form).text
    assert '<option value="live" selected>Live</option>' in reject_html

    lib = env._lib()
    try:
        assert lib.get_original_content(item_id)["status"] == "draft"
    finally:
        lib.close()

    reload_html = c.get(f"/admin/thought-leadership/original/{item_id}/edit").text
    assert '<option value="draft" selected>Draft</option>' in reload_html
    assert '<option value="live" selected>' not in reload_html
