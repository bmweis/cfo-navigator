"""category_features.definition / pointer_note must never be shortened by a save.

The inline editor on /admin/tools/software/features used to cap both fields at
maxlength="500" while production stored definitions up to 1,470 characters,
and rendered them in <input type="text">, which strips line breaks on submit.
These tests round-trip a long, multi-paragraph value through the real editor
markup and the real save route, and pin the server-side limit's refusal.
"""
import html
import os
import pathlib
import re
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

# > 1,470 (the longest stored production definition), with line breaks,
# quotes, an ampersand, and angle brackets so escaping is exercised too.
LONG_DEFINITION = (
    "Continuous reconciliation of bank, card, and subledger activity against the GL.\n\n"
    + " ".join(f'Clause {i}: "matching" rules <tier {i}> for AP & AR exceptions.' for i in range(40))
    + "\n\nSecond paragraph that must survive the save intact."
)
assert len(LONG_DEFINITION) > 1470


@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib
    import webapp.app as appmod
    importlib.reload(appmod)
    yield appmod, db
    if os.path.exists(db):
        os.remove(db)


def _client(appmod):
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app, raise_server_exceptions=True)
    r = c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    assert r.status_code in (302, 303)
    return c


def _seed(db, definition, pointer_note=""):
    from linklib.db import Library
    lib = Library(db)
    try:
        cat = lib.add_tool_category("Close Management")
        fid = lib.add_category_feature(cat, "Continuous reconciliation", definition, pointer_note)
        return cat, fid
    finally:
        lib.close()


def _stored(db, fid):
    from linklib.db import Library
    lib = Library(db)
    try:
        return dict(lib.conn.execute(
            "SELECT definition, pointer_note FROM category_features WHERE id=?", (fid,)).fetchone())
    finally:
        lib.close()


def _editor_field(page_html, fid, name):
    """The value the browser would submit for this feature's field: the
    textarea bound to the feature's edit form, HTML-unescaped. Fails loudly if
    the field is an <input> (which would strip newlines) or carries any
    maxlength at all (a browser silently cuts a paste to it)."""
    m = re.search(
        rf'<textarea name="{name}" form="feat-edit-{fid}"([^>]*)>(.*?)</textarea>',
        page_html, re.S)
    assert m, f"{name} for feature {fid} is not a textarea bound to its edit form"
    attrs, body = m.group(1), m.group(2)
    assert "maxlength" not in attrs, "a maxlength silently cuts a paste"
    return html.unescape(body)


def test_long_definition_round_trips_through_the_editor_unchanged(env):
    appmod, db = env
    cat, fid = _seed(db, LONG_DEFINITION, pointer_note=LONG_DEFINITION)
    c = _client(appmod)
    page = c.get(f"/admin/tools/software/features?open_ids={cat}").text
    definition = _editor_field(page, fid, "definition")
    pointer_note = _editor_field(page, fid, "pointer_note")
    assert definition == LONG_DEFINITION
    r = c.post(f"/admin/tools/software/features/{fid}/edit", data={
        "category_id": str(cat), "name": "Continuous reconciliation",
        "definition": definition, "pointer_note": pointer_note, "sort_order": "10",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert "error=" not in r.headers["location"]
    stored = _stored(db, fid)
    assert stored["definition"] == LONG_DEFINITION
    assert stored["pointer_note"] == LONG_DEFINITION


def test_add_form_accepts_a_long_definition(env):
    appmod, db = env
    from linklib.db import Library
    lib = Library(db)
    cat = lib.add_tool_category("ERP")
    lib.close()
    c = _client(appmod)
    page = c.get("/admin/tools/software/features").text
    add_form = page.split('action="/admin/tools/software/features/new"')[1].split("</form>")[0]
    for name in ("definition", "pointer_note"):
        tag = re.search(rf'<textarea name="{name}"[^>]*>', add_form).group(0)
        assert "maxlength" not in tag
    r = c.post("/admin/tools/software/features/new", data={
        "category_id": str(cat), "name": "Ledger", "definition": LONG_DEFINITION, "pointer_note": "",
    }, follow_redirects=False)
    assert "error=" not in r.headers["location"]
    lib = Library(db)
    try:
        row = lib.conn.execute("SELECT definition FROM category_features WHERE name='Ledger'").fetchone()
    finally:
        lib.close()
    assert row["definition"] == LONG_DEFINITION


def test_over_limit_save_is_refused_visibly_and_leaves_the_stored_value_alone(env):
    appmod, db = env
    from linklib.db import Library
    cat, fid = _seed(db, LONG_DEFINITION)
    too_long = "a" * (Library.CATEGORY_FEATURE_TEXT_MAX + 1)
    c = _client(appmod)
    r = c.post(f"/admin/tools/software/features/{fid}/edit", data={
        "category_id": str(cat), "name": "Continuous reconciliation",
        "definition": too_long, "pointer_note": "", "sort_order": "10",
    }, follow_redirects=False)
    loc = r.headers["location"]
    assert "error=" in loc
    shown = c.get(loc).text
    assert f"{Library.CATEGORY_FEATURE_TEXT_MAX:,}" in shown
    assert f"{len(too_long):,}" in shown
    assert _stored(db, fid)["definition"] == LONG_DEFINITION


def test_library_limit_is_the_same_number_the_inputs_advertise(env):
    appmod, _db = env
    from linklib.db import Library
    assert appmod._FEATURE_TEXT_MAX == Library.CATEGORY_FEATURE_TEXT_MAX
    assert Library.CATEGORY_FEATURE_TEXT_MAX > 1470


def test_library_refuses_rather_than_truncates(tmp_path):
    from linklib.db import Library
    lib = Library(str(tmp_path / "t.db"))
    try:
        cat = lib.add_tool_category("ERP")
        fid = lib.add_category_feature(cat, "X", "keep me")
        with pytest.raises(ValueError, match="limit"):
            lib.update_category_feature(fid, "X", "b" * (Library.CATEGORY_FEATURE_TEXT_MAX + 1), "", 10)
        with pytest.raises(ValueError, match="limit"):
            lib.add_category_feature(cat, "Y", "", "c" * (Library.CATEGORY_FEATURE_TEXT_MAX + 1))
        assert lib.conn.execute("SELECT definition FROM category_features WHERE id=?", (fid,)).fetchone()[0] == "keep me"
    finally:
        lib.close()


def test_edited_review_queue_approval_keeps_the_proposed_definition(env):
    """Edit-then-approve used to rebuild the new-feature payload without its
    definition, so the proposer's definition was dropped on approval."""
    appmod, db = env
    from linklib.db import Library
    lib = Library(db)
    try:
        cat = lib.add_tool_category("ERP")
        tool_id = lib.add_tool("Rillet", "d", "https://rillet.com", ["ERP"], approved=1, summary="s")
        qid = lib.add_feature_review_queue_item(
            source="scan", proposal_type="new_feature", category_id=cat,
            payload={"category_id": cat,
                     "feature": {"name": "Proposed", "definition": LONG_DEFINITION, "pointer_note": ""},
                     "links": [{"tool_id": tool_id, "availability": "native", "ai_enabled": 0,
                                "verified_as_of": "2026-09-01", "note": "", "source_url": ""}]},
        )
    finally:
        lib.close()
    c = _client(appmod)
    page = c.get("/admin/tools/software/feature-review-queue").text
    m = re.search(r'<textarea name="definition"[^>]*>(.*?)</textarea>', page, re.S)
    assert m and html.unescape(m.group(1)) == LONG_DEFINITION
    r = c.post(f"/admin/tools/software/feature-review-queue/{qid}/approve", data={
        "feature_name": "Proposed (edited)", "definition": html.unescape(m.group(1)), "pointer_note": "",
        "n_links": "1", "link_0_tool_id": str(tool_id), "link_0_availability": "native",
        "link_0_verified_as_of": "2026-09-01",
    }, follow_redirects=False)
    assert r.status_code == 303
    lib = Library(db)
    try:
        row = lib.conn.execute(
            "SELECT definition FROM category_features WHERE name='Proposed (edited)'").fetchone()
    finally:
        lib.close()
    assert row is not None and row["definition"] == LONG_DEFINITION
