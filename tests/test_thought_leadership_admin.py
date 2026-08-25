"""Thought Leadership Admin CRUD, Phase 1 (see CLAUDE.md): the four
/thought-leadership columns (Writing, Speaking & Events, Podcasts, Press)
now read from the `thought_leadership` DB table via `/admin/thought-leadership`
CRUD, replacing the pre-Phase-1 hardcoded webapp/thought_leadership_data.py.
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
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    yield appmod
    if os.path.exists(db):
        os.remove(db)


def _client(appmod):
    from fastapi.testclient import TestClient
    return TestClient(appmod.app, raise_server_exceptions=True)


def _admin_client(appmod):
    c = _client(appmod)
    c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    return c


def test_thought_leadership_in_admin_nav(env):
    group_names = [gname for gname, _, _ in env._ADMIN_GROUPS]
    assert "Thought leadership" in group_names
    tl_groups = [items for gname, _, items in env._ADMIN_GROUPS if gname == "Thought leadership"]
    assert len(tl_groups) == 1
    hrefs = [href for href, _, _ in tl_groups[0]]
    assert "/admin/thought-leadership" in hrefs


def test_public_page_requires_no_auth_and_renders_empty_state(env):
    c = _client(env)
    resp = c.get("/thought-leadership")
    assert resp.status_code == 200
    # The one hardcoded photo entry always renders, even with an empty DB table.
    assert "Abacum AI Summit" in resp.text


def test_admin_crud_requires_auth(env):
    c = _client(env)
    assert c.get("/admin/thought-leadership", follow_redirects=False).status_code in (302, 303, 307)
    assert c.post("/admin/thought-leadership/new", data={"type": "press", "title": "x"}).status_code == 401


def test_add_edit_delete_round_trip(env):
    c = _admin_client(env)

    resp = c.post("/admin/thought-leadership/new", data={
        "type": "writing", "title": "A New Essay", "url": "https://example.com/essay",
        "venue": "Example Pub", "date_label": "Jan 2027", "sort_key": "2027-01",
        "description": "A synopsis.", "display_order": "0",
    }, follow_redirects=False)
    assert resp.status_code == 303

    # Shows up in the admin list, filtered and unfiltered.
    resp = c.get("/admin/thought-leadership")
    assert "A New Essay" in resp.text
    resp = c.get("/admin/thought-leadership?type=writing")
    assert "A New Essay" in resp.text
    resp = c.get("/admin/thought-leadership?type=press")
    assert "A New Essay" not in resp.text

    # Shows up on the public page.
    resp = c.get("/thought-leadership")
    assert "A New Essay" in resp.text

    from linklib.db import Library
    lib = Library(env.DB_PATH)
    try:
        rows = lib.list_thought_leadership(type="writing")
    finally:
        lib.close()
    assert len(rows) == 1
    item_id = rows[0]["id"]

    # Edit.
    resp = c.get(f"/admin/thought-leadership/{item_id}/edit")
    assert resp.status_code == 200
    assert "A New Essay" in resp.text

    resp = c.post(f"/admin/thought-leadership/{item_id}/edit", data={
        "type": "writing", "title": "A Renamed Essay", "url": "https://example.com/essay",
        "venue": "Example Pub", "date_label": "Jan 2027", "sort_key": "2027-01",
        "description": "A synopsis.", "display_order": "0",
    }, follow_redirects=False)
    assert resp.status_code == 303

    resp = c.get("/thought-leadership")
    assert "A Renamed Essay" in resp.text
    assert "A New Essay" not in resp.text

    # Delete.
    resp = c.post(f"/admin/thought-leadership/{item_id}/delete", follow_redirects=False)
    assert resp.status_code == 303
    resp = c.get("/thought-leadership")
    assert "A Renamed Essay" not in resp.text


def test_type_is_required_and_validated(env):
    c = _admin_client(env)
    resp = c.post("/admin/thought-leadership/new", data={"type": "bogus", "title": "x"})
    assert resp.status_code == 400


def test_title_is_required(env):
    c = _admin_client(env)
    resp = c.post("/admin/thought-leadership/new", data={"type": "press", "title": "   "})
    assert resp.status_code == 400


def test_photo_entry_not_editable_via_admin(env):
    """The Abacum AI Summit entry is hardcoded (_TL_PHOTO_ENTRY), not a DB
    row — it must not appear as a row in the admin CRUD list (only in the
    page's explanatory footnote), and must still render on the public page."""
    c = _admin_client(env)
    resp = c.get("/admin/thought-leadership")
    # No Edit/Delete row for it — only the one explanatory mention below the table.
    assert resp.text.count("Abacum AI Summit") == 1
    resp = c.get("/thought-leadership")
    assert "Abacum AI Summit" in resp.text


def test_undated_and_tied_sort_key_ordering(env):
    """Undated items float to the top; items sharing a sort_key break ties by
    display_order (ascending) — mirrors the pre-DB TLItem ordering."""
    c = _admin_client(env)
    from linklib.db import Library
    lib = Library(env.DB_PATH)
    try:
        lib.add_thought_leadership("press", "Undated Standing Link", url="", sort_key="", display_order=0)
        lib.add_thought_leadership("press", "First (same month)", sort_key="2026-05", display_order=0)
        lib.add_thought_leadership("press", "Second (same month)", sort_key="2026-05", display_order=1)
        lib.add_thought_leadership("press", "Newer Month", sort_key="2026-06", display_order=0)
    finally:
        lib.close()

    resp = c.get("/thought-leadership")
    body = resp.text
    pos_undated = body.index("Undated Standing Link")
    pos_newer = body.index("Newer Month")
    pos_first = body.index("First (same month)")
    pos_second = body.index("Second (same month)")
    assert pos_undated < pos_newer < pos_first < pos_second


def test_sort_key_is_derived_from_date_label_not_a_form_field(env):
    """Follow-up fix: sort_key is no longer a form field — the add/edit
    forms only expose date_label, and sort_key is computed server-side on
    every save ("Mon YYYY"/"Month YYYY" -> "YYYY-MM")."""
    c = _admin_client(env)

    # The form itself no longer has a sort_key input.
    resp = c.get("/admin/thought-leadership/new")
    assert 'name="sort_key"' not in resp.text
    assert 'name="date_label"' in resp.text

    resp = c.post("/admin/thought-leadership/new", data={
        "type": "press", "title": "Full Month Name Entry", "date_label": "March 2027",
    }, follow_redirects=False)
    assert resp.status_code == 303

    from linklib.db import Library
    lib = Library(env.DB_PATH)
    try:
        rows = lib.list_thought_leadership(type="press")
    finally:
        lib.close()
    row = next(r for r in rows if r["title"] == "Full Month Name Entry")
    assert row["sort_key"] == "2027-03"

    # Edit form doesn't expose sort_key either.
    resp = c.get(f"/admin/thought-leadership/{row['id']}/edit")
    assert 'name="sort_key"' not in resp.text


def test_unparseable_date_label_floats_to_top_with_warning(env):
    """A non-blank date_label that doesn't parse as Mon YYYY gets a blank
    sort_key (floats to top, same as an intentionally undated entry) —
    but is flagged with a visible warning, not silently indistinguishable
    from a deliberate blank."""
    c = _admin_client(env)
    resp = c.post("/admin/thought-leadership/new", data={
        "type": "press", "title": "Weird Date Entry", "date_label": "Q2 2027",
    }, follow_redirects=False)
    assert resp.status_code == 303

    from linklib.db import Library
    lib = Library(env.DB_PATH)
    try:
        rows = lib.list_thought_leadership(type="press")
    finally:
        lib.close()
    row = next(r for r in rows if r["title"] == "Weird Date Entry")
    assert row["sort_key"] == ""

    # Warning icon on the admin list row.
    resp = c.get("/admin/thought-leadership")
    assert "&#9888;" in resp.text
    assert "didn" in resp.text.lower() and "parse" in resp.text.lower()

    # Inline warning on the edit form.
    resp = c.get(f"/admin/thought-leadership/{row['id']}/edit")
    assert "Date label" in resp.text
    assert "didn" in resp.text.lower() and "parse" in resp.text.lower()

    # It floats to the top of its column on the public page, same as a
    # deliberately undated entry.
    resp = c.get("/thought-leadership")
    assert "Weird Date Entry" in resp.text


def test_blank_date_label_has_no_parse_warning(env):
    """A deliberately blank date_label (the standing-link convention) must
    NOT trigger the didn't-parse warning — only a non-blank, unparseable
    value should."""
    c = _admin_client(env)
    resp = c.post("/admin/thought-leadership/new", data={
        "type": "podcast", "title": "Standing Feed Link", "date_label": "",
    }, follow_redirects=False)
    assert resp.status_code == 303

    from linklib.db import Library
    lib = Library(env.DB_PATH)
    try:
        rows = lib.list_thought_leadership(type="podcast")
    finally:
        lib.close()
    row = next(r for r in rows if r["title"] == "Standing Feed Link")
    assert row["sort_key"] == ""

    resp = c.get(f"/admin/thought-leadership/{row['id']}/edit")
    assert "Date label" in resp.text
    assert "didn&rsquo;t parse" not in resp.text

    # And no warning icon on the admin list row for this entry either.
    resp = c.get("/admin/thought-leadership?type=podcast")
    row_html = resp.text[resp.text.index("Standing Feed Link"):]
    assert "&#9888;" not in row_html.split("</tr>")[0]


def test_migration_script_moves_32_of_33_entries(env, tmp_path):
    """The Abacum photo entry (33rd) is deliberately excluded — see
    scripts/archive/migrate_thought_leadership.py's docstring."""
    from linklib.db import Library
    db_path = str(tmp_path / "migrate_test.db")
    lib = Library(db_path)
    lib.close()

    from scripts.archive import migrate_thought_leadership as mig
    planned = mig._planned_rows()
    assert len(planned) == 32

    argv = sys.argv
    try:
        # Preview run (no --apply): must not write anything.
        sys.argv = ["migrate_thought_leadership", "--db", db_path]
        assert mig.main() == 0
        lib = Library(db_path)
        try:
            assert lib.list_thought_leadership() == []
        finally:
            lib.close()

        # Real run.
        sys.argv = ["migrate_thought_leadership", "--db", db_path, "--apply"]
        assert mig.main() == 0
    finally:
        sys.argv = argv

    lib = Library(db_path)
    try:
        rows = lib.list_thought_leadership()
        assert len(rows) == 32
        by_type = {}
        for r in rows:
            by_type[r["type"]] = by_type.get(r["type"], 0) + 1
        assert by_type == {"writing": 6, "speaking": 11, "podcast": 10, "press": 5}
    finally:
        lib.close()

    # Idempotency guard: running again against an already-seeded table is a no-op.
    sys.argv = ["migrate_thought_leadership", "--db", db_path, "--apply"]
    try:
        assert mig.main() == 0
    finally:
        sys.argv = argv
    lib = Library(db_path)
    try:
        assert len(lib.list_thought_leadership()) == 32
    finally:
        lib.close()


def test_edit_page_title_is_not_double_escaped(env):
    """_page() escapes its own title argument internally — the edit route
    must pass the raw title, not a pre-_esc()'d one, or an "&" in the title
    renders as "&amp;amp;" instead of "&amp;". Same bug, same fix, as
    Original Content Phase 3's admin_original_content_edit (see CLAUDE.md's
    Original Content Phase 5 cleanup entry)."""
    c = _admin_client(env)
    resp = c.post("/admin/thought-leadership/new", data={
        "type": "writing", "title": "AI & Finance", "url": "https://example.com/ai-finance",
        "venue": "Example Pub", "date_label": "Jan 2027", "sort_key": "2027-01",
        "description": "A synopsis.", "display_order": "0",
    }, follow_redirects=False)
    assert resp.status_code == 303

    from linklib.db import Library
    lib = Library(env.DB_PATH)
    try:
        item_id = next(r["id"] for r in lib.list_thought_leadership(type="writing")
                        if r["title"] == "AI & Finance")
    finally:
        lib.close()

    html = c.get(f"/admin/thought-leadership/{item_id}/edit").text
    assert "&amp;amp;" not in html
    assert "<title>BMW CFO · Edit AI &amp; Finance</title>" in html
