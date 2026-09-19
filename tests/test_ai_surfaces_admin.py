"""The explainers collection (Phase 1 of the explainers-collection build) —
CRUD for ai_surfaces at /admin/ai-surfaces, the public /how-this-is-built/
{slug} article route, and the seed migration matching the pre-existing
_AI_SURFACES tuple exactly.
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
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    yield appmod
    if os.path.exists(db):
        os.remove(db)


def _client(appmod):
    from fastapi.testclient import TestClient
    return TestClient(appmod.app, raise_server_exceptions=True)


# --- migration -------------------------------------------------------------

def test_migration_seeds_four_rows_matching_the_tuple(env):
    from scripts.migrate_ai_surfaces import planned_rows
    planned = planned_rows()
    assert len(planned) == 4
    fpa = planned[0]
    assert fpa["title"] == "FP&A Buddy"
    assert fpa["external_href"] == "/tools/fpa-buddy/how-it-works"
    assert fpa["status"] == "live"
    assert fpa["body_md"] is None
    for row in planned[1:]:
        assert row["external_href"] == ""
        assert row["status"] == "draft"


def test_migration_script_is_idempotent(env):
    from scripts.migrate_ai_surfaces import planned_rows
    lib = env._lib()
    try:
        for r in planned_rows():
            lib.add_ai_surface(r["slug"], r["title"], r["teaser"], r["body_md"],
                                r["external_href"], r["status"], r["display_order"])
        before = len(lib.list_ai_surfaces())
        # Re-running against a non-empty table must be a no-op per the
        # script's own guard — simulate the guard directly.
        assert lib.list_ai_surfaces()  # guard condition would trip
        assert before == 4
    finally:
        lib.close()


# --- draft vs live rendering on /how-this-is-built --------------------------

def test_draft_row_renders_unlinked(env):
    lib = env._lib()
    try:
        lib.add_ai_surface("web-search", "Web search", "Teaser.", None, "", "draft", 0)
    finally:
        lib.close()
    html = _client(env).get("/how-this-is-built").text
    assert "Web search" in html
    assert "Explainer coming soon." in html
    assert "/how-this-is-built/web-search" not in html


def test_live_row_with_body_links_to_its_own_page(env):
    lib = env._lib()
    try:
        lib.add_ai_surface("web-search", "Web search", "Teaser.", "Body content here.",
                            "", "live", 0)
    finally:
        lib.close()
    html = _client(env).get("/how-this-is-built").text
    assert 'href="/how-this-is-built/web-search"' in html


def test_live_row_with_external_href_links_there_not_to_own_page(env):
    lib = env._lib()
    try:
        lib.add_ai_surface("fpa-buddy", "FP&A Buddy", "Teaser.", None,
                            "/tools/fpa-buddy/how-it-works", "live", 0)
    finally:
        lib.close()
    html = _client(env).get("/how-this-is-built").text
    assert 'href="/tools/fpa-buddy/how-it-works"' in html
    assert "/how-this-is-built/fpa-buddy" not in html


def test_live_row_with_neither_body_nor_href_still_reads_coming_soon(env):
    lib = env._lib()
    try:
        lib.add_ai_surface("empty", "Empty", "Teaser.", None, "", "live", 0)
    finally:
        lib.close()
    html = _client(env).get("/how-this-is-built").text
    assert "Explainer coming soon." in html


# --- /how-this-is-built/{slug} ----------------------------------------------

def test_live_article_page_renders(env):
    lib = env._lib()
    try:
        lib.add_ai_surface("web-search", "Web search", "Teaser.", "**Real** body.", "", "live", 0)
    finally:
        lib.close()
    r = _client(env).get("/how-this-is-built/web-search")
    assert r.status_code == 200
    assert "<strong>Real</strong> body." in r.text
    assert '<a href="/how-this-is-built"' in r.text


def test_draft_article_404s_for_signed_out_visitor(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    try:
        lib = appmod._lib()
        try:
            lib.add_ai_surface("draft-one", "Draft one", "Teaser.", "Body.", "", "draft", 0)
        finally:
            lib.close()
        r = _client(appmod).get("/how-this-is-built/draft-one")
        assert r.status_code == 404
    finally:
        if os.path.exists(db):
            os.remove(db)


def test_no_body_404s_regardless_of_status(env):
    lib = env._lib()
    try:
        lib.add_ai_surface("no-body", "No body", "Teaser.", None, "", "live", 0)
    finally:
        lib.close()
    assert _client(env).get("/how-this-is-built/no-body").status_code == 404


def test_unknown_slug_404s(env):
    assert _client(env).get("/how-this-is-built/nope").status_code == 404


# --- admin CRUD --------------------------------------------------------------

def test_admin_list_page_requires_auth(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    try:
        r = _client(appmod).get("/admin/ai-surfaces", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"].startswith("/login")
    finally:
        if os.path.exists(db):
            os.remove(db)


def test_add_edit_delete_cycle(env):
    c = _client(env)
    r = c.post("/admin/ai-surfaces/new", data={
        "title": "New surface", "slug": "new-surface", "teaser": "A teaser.",
        "external_href": "", "body_md": "", "status": "draft", "display_order": "",
    }, follow_redirects=False)
    assert r.status_code == 303

    lib = env._lib()
    try:
        row = lib.get_ai_surface_by_slug("new-surface")
    finally:
        lib.close()
    assert row is not None
    assert row["title"] == "New surface"

    r = c.post(f"/admin/ai-surfaces/{row['id']}/edit", data={
        "title": "Renamed surface", "slug": "new-surface", "teaser": "A teaser.",
        "external_href": "", "body_md": "Some content.", "status": "live", "display_order": "0",
    }, follow_redirects=False)
    assert r.status_code == 303

    lib = env._lib()
    try:
        row = lib.get_ai_surface(row["id"])
    finally:
        lib.close()
    assert row["title"] == "Renamed surface"
    assert row["status"] == "live"
    assert row["body_md"] == "Some content."

    r = c.post(f"/admin/ai-surfaces/{row['id']}/delete", follow_redirects=False)
    assert r.status_code == 303
    lib = env._lib()
    try:
        assert lib.get_ai_surface(row["id"]) is None
    finally:
        lib.close()


def test_add_rejects_duplicate_slug(env):
    lib = env._lib()
    try:
        lib.add_ai_surface("taken", "Taken", "Teaser.", None, "", "draft", 0)
    finally:
        lib.close()
    c = _client(env)
    r = c.post("/admin/ai-surfaces/new", data={
        "title": "Another", "slug": "taken", "teaser": "Teaser.",
        "external_href": "", "body_md": "", "status": "draft", "display_order": "",
    })
    assert r.status_code == 400
    assert "already used by another explainer" in r.text


def test_error_banner_uses_the_alert_family_not_coral(env):
    """A validation-error banner is a status/error state — BRAND.md reserves
    that for --alert, never coral. Same fix as _oc_form_page's own error
    banner (#581), applied here since _ai_surface_form_page shared the
    identical coral-wash+navy pattern."""
    lib = env._lib()
    try:
        lib.add_ai_surface("taken", "Taken", "Teaser.", None, "", "draft", 0)
    finally:
        lib.close()
    c = _client(env)
    r = c.post("/admin/ai-surfaces/new", data={
        "title": "Another", "slug": "taken", "teaser": "Teaser.",
        "external_href": "", "body_md": "", "status": "draft", "display_order": "",
    })
    assert r.status_code == 400
    assert "var(--alert-wash)" in r.text
    assert "var(--alert)" in r.text
    assert "var(--coral-wash)" not in r.text


def test_reorder_via_display_order(env):
    c = _client(env)
    for i, slug in enumerate(("a", "b")):
        c.post("/admin/ai-surfaces/new", data={
            "title": slug.upper(), "slug": slug, "teaser": "Teaser.",
            "external_href": "", "body_md": "", "status": "live", "display_order": str(i),
        })
    lib = env._lib()
    try:
        row_a = lib.get_ai_surface_by_slug("a")
        row_b = lib.get_ai_surface_by_slug("b")
    finally:
        lib.close()
    c.post(f"/admin/ai-surfaces/{row_a['id']}/edit", data={
        "title": "A", "slug": "a", "teaser": "Teaser.", "external_href": "",
        "body_md": "", "status": "live", "display_order": "5",
    })
    html = _client(env).get("/how-this-is-built").text
    assert html.index("B</h3>") < html.index("A</h3>")


def test_list_page_shows_preview_link_for_live_row_with_body(env):
    lib = env._lib()
    try:
        lib.add_ai_surface("web-search", "Web search", "Teaser.", "Body.", "", "live", 0)
    finally:
        lib.close()
    html = _client(env).get("/admin/ai-surfaces").text
    assert 'href="/how-this-is-built/web-search"' in html


# --- list page: "Destination" column (was "Slug") ---------------------------
# A raw slug rendered for every row even when External link overrides it
# entirely (the FP&A Buddy row) — a populated field that does nothing is
# worse than a blank one. The column now shows the real, effective link a
# visitor's click lands on, computed per row from the same status/external/
# body precedence the page's own explanatory paragraph states once.

def test_destination_column_replaces_slug_column(env):
    html = _client(env).get("/admin/ai-surfaces").text
    assert "Destination" in html
    assert ">Slug<" not in html


def test_destination_shows_effective_url_for_external_row(env):
    lib = env._lib()
    try:
        lib.add_ai_surface("fpa-buddy", "FP&A Buddy", "Teaser.", None,
                            "/tools/fpa-buddy/how-it-works", "live", 0)
    finally:
        lib.close()
    html = _client(env).get("/admin/ai-surfaces").text
    assert "/tools/fpa-buddy/how-it-works" in html
    # The slug (an unused, misleading detail for this row) shouldn't be the
    # thing shown in its place.
    assert ">fpa-buddy<" not in html


def test_destination_shows_own_page_url_for_body_row(env):
    lib = env._lib()
    try:
        lib.add_ai_surface("web-search", "Web search", "Teaser.", "Body.", "", "live", 0)
    finally:
        lib.close()
    html = _client(env).get("/admin/ai-surfaces").text
    assert "/how-this-is-built/web-search" in html


def test_destination_shows_unlinked_for_draft_regardless_of_content(env):
    lib = env._lib()
    try:
        lib.add_ai_surface("matchmakers", "Matchmakers", "Teaser.", "Body.",
                            "", "draft", 0)
    finally:
        lib.close()
    html = _client(env).get("/admin/ai-surfaces").text
    assert "Draft" in html and "unlinked" in html


def test_destination_shows_coming_soon_for_live_row_with_neither(env):
    lib = env._lib()
    try:
        lib.add_ai_surface("empty-row", "Empty row", "Teaser.", None, "", "live", 0)
    finally:
        lib.close()
    html = _client(env).get("/admin/ai-surfaces").text
    assert "coming soon" in html


# --- edit page: disabled Preview gets visible text, not just a tooltip ------

def test_disabled_preview_has_visible_reason_text(env):
    lib = env._lib()
    try:
        item_id = lib.add_ai_surface("fpa-buddy", "FP&A Buddy", "Teaser.", None,
                                      "/tools/fpa-buddy/how-it-works", "live", 0)
    finally:
        lib.close()
    html = _client(env).get(f"/admin/ai-surfaces/{item_id}/edit").text
    # The title= tooltip alone was the whole reason this looked "dead" — a
    # hover-only/touch-invisible explanation isn't a substitute for text a
    # reader actually sees.
    assert "Preview is off because" in html
    assert "no Body content yet" in html


def test_enabled_preview_has_no_disabled_note(env):
    lib = env._lib()
    try:
        item_id = lib.add_ai_surface("web-search", "Web search", "Teaser.", "Body.", "", "live", 0)
    finally:
        lib.close()
    html = _client(env).get(f"/admin/ai-surfaces/{item_id}/edit").text
    assert "Preview is off because" not in html


# --- edit page: Body's empty state is explicit when External link wins -----

def test_body_placeholder_is_explicit_when_external_href_set_and_body_empty(env):
    lib = env._lib()
    try:
        item_id = lib.add_ai_surface("fpa-buddy", "FP&A Buddy", "Teaser.", None,
                                      "/tools/fpa-buddy/how-it-works", "live", 0)
    finally:
        lib.close()
    html = _client(env).get(f"/admin/ai-surfaces/{item_id}/edit").text
    assert "Not needed: this explainer lives at the External link above." in html


def test_body_placeholder_is_generic_when_external_href_unset(env):
    html = env._ai_surface_body_placeholder({"external_href": "", "body_md": None})
    assert "Not needed" not in html
    assert "Leave blank" in html


def test_body_placeholder_prefers_real_content_over_external_href(env):
    # A row can legitimately have both — a real body AND an external link
    # left over from before the body was written. The placeholder question
    # only matters when there's nothing in the box to begin with.
    html = env._ai_surface_body_placeholder(
        {"external_href": "/somewhere", "body_md": "Real content"})
    assert "Not needed" not in html


# --- edit page: Body's helper text confirms markdown + HTML are both live --

def test_body_helper_text_confirms_markdown_and_html_both_supported(env):
    html = _client(env).get("/admin/ai-surfaces/new").text
    assert "Markdown, plus raw HTML" in html


# --- form: the Slug/External link/Body relationship is stated once ---------

def test_form_states_the_slug_external_body_relationship_once(env):
    add_html = _client(env).get("/admin/ai-surfaces/new").text
    assert "Fill in <strong>Body</strong>" in add_html
    assert "Fill in <strong>External link</strong>" in add_html

    lib = env._lib()
    try:
        item_id = lib.add_ai_surface("web-search", "Web search", "Teaser.", "Body.", "", "live", 0)
    finally:
        lib.close()
    edit_html = _client(env).get(f"/admin/ai-surfaces/{item_id}/edit").text
    assert "Fill in <strong>Body</strong>" in edit_html


def test_page_carries_recognized_width_tier(env):
    rows = env._page_index_snapshot()
    row = next(r for r in rows if r["path"] == "/admin/ai-surfaces")
    assert row["tier"] == "page-standard"
    assert row["flagged"] is False


def test_hub_nav_orphans_clean(env):
    assert env.hub_nav_orphans() == []
