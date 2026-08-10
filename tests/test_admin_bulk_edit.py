"""Software admin rename (/admin/tools -> /admin/tools/software) and the shared
column-picker/bulk-edit routes for the Communities and Software admin tables."""
import json
import os
import tempfile
from html.parser import HTMLParser

import pytest


class _AttrFinder(HTMLParser):
    """Parses HTML the same way a browser's attribute parser does—finds the
    onchange="" attribute for a given element id and returns its fully
    HTML-unescaped value (HTMLParser unescapes entities in attribute values
    automatically). Used to catch the class of bug where an unescaped "
    inside an attribute value truncates the attribute instead of a mere
    substring check, which can't tell a whole attribute from a cut-off one."""
    def __init__(self, target_id):
        super().__init__(convert_charrefs=True)
        self.target_id = target_id
        self.found = None

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if d.get("id") == self.target_id:
            self.found = d.get("onchange")


@pytest.fixture
def admin_client(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    client = TestClient(appmod.app, raise_server_exceptions=True)
    client.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    yield client, appmod, db
    if os.path.exists(db):
        os.remove(db)


# --- rename smoke test -------------------------------------------------------

def test_admin_software_rename_hard_cutover(admin_client):
    client, appmod, db = admin_client
    r = client.get("/admin/tools/software")
    assert r.status_code == 200
    assert "Software" in r.text

    old = client.get("/admin/tools")
    assert old.status_code == 404

    older = client.get("/admin/software")
    assert older.status_code == 404


# --- Software bulk edit -------------------------------------------------------

def test_software_bulk_edit_applies_only_to_selected_rows(admin_client):
    client, appmod, db = admin_client
    from linklib.db import Library
    lib = Library(db)
    t1 = lib.add_tool("Tool A", "desc", "https://a.example", [], approved=1)
    t2 = lib.add_tool("Tool B", "desc", "https://b.example", [], approved=1)
    lib.close()

    r = client.post("/admin/tools/software/bulk-edit", json={"ids": [t1, t2], "field": "advisor", "value": "1"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    lib = Library(db)
    assert lib.get_tool(t1)["advisor"] == 1
    assert lib.get_tool(t2)["advisor"] == 1
    lib.close()


def test_software_bulk_edit_rejects_non_allowlisted_field(admin_client):
    client, appmod, db = admin_client
    from linklib.db import Library
    lib = Library(db)
    t1 = lib.add_tool("Tool A", "desc", "https://a.example", [], approved=1)
    lib.close()

    r = client.post("/admin/tools/software/bulk-edit", json={"ids": [t1], "field": "name", "value": "HACKED"})
    assert r.status_code == 400

    r2 = client.post("/admin/tools/software/bulk-edit", json={"ids": [t1], "field": "url", "value": "https://evil.example"})
    assert r2.status_code == 400

    lib = Library(db)
    tool = lib.get_tool(t1)
    assert tool["name"] == "Tool A"
    assert tool["url"] == "https://a.example"
    lib.close()


def test_software_bulk_edit_requires_auth(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    client = TestClient(appmod.app, raise_server_exceptions=False)
    r = client.post("/admin/tools/software/bulk-edit", json={"ids": [1], "field": "advisor", "value": "1"})
    assert r.status_code == 401
    if os.path.exists(db):
        os.remove(db)


# --- Software bulk delete -----------------------------------------------------

def test_software_bulk_delete_check_flags_outside_competitor_references(admin_client):
    client, appmod, db = admin_client
    from linklib.db import Library
    lib = Library(db)
    t1 = lib.add_tool("Tool A", "desc", "https://a.example", [], approved=1)
    t2 = lib.add_tool("Tool B", "desc", "https://b.example", [], approved=1)
    t3 = lib.add_tool("Tool C", "desc", "https://c.example", [], approved=1)
    lib.add_tool_competitor(t1, t3)  # C references A as a competitor
    lib.close()

    # Deleting just A should warn that C references it.
    r = client.post("/admin/tools/software/bulk-delete-check", json={"ids": [t1]})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["tools"] == [{"id": t1, "name": "Tool A"}]
    assert body["warnings"] == [{"name": "Tool A", "referenced_by": ["Tool C"]}]

    # Deleting A and B together (neither references the other) shouldn't
    # warn about each other, but should still warn about C referencing A.
    r2 = client.post("/admin/tools/software/bulk-delete-check", json={"ids": [t1, t2]})
    assert r2.status_code == 200
    body2 = r2.json()
    assert {t["name"] for t in body2["tools"]} == {"Tool A", "Tool B"}
    assert body2["warnings"] == [{"name": "Tool A", "referenced_by": ["Tool C"]}]

    # Deleting A and C together: C is the only referencer and it's also
    # being deleted, so no warning should surface.
    r3 = client.post("/admin/tools/software/bulk-delete-check", json={"ids": [t1, t3]})
    assert r3.status_code == 200
    assert r3.json()["warnings"] == []


def test_software_bulk_delete_removes_selected_rows_via_delete_tool(admin_client):
    client, appmod, db = admin_client
    from linklib.db import Library
    lib = Library(db)
    t1 = lib.add_tool("Tool A", "desc", "https://a.example", [], approved=1)
    t2 = lib.add_tool("Tool B", "desc", "https://b.example", [], approved=1)
    t3 = lib.add_tool("Tool C", "desc", "https://c.example", [], approved=1)
    lib.add_tool_competitor(t1, t3)
    lib.close()

    r = client.post("/admin/tools/software/bulk-delete", json={"ids": [t1, t2]})
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    lib = Library(db)
    assert lib.get_tool(t1) is None
    assert lib.get_tool(t2) is None
    assert lib.get_tool(t3) is not None
    # delete_tool cascades tool_competitors rows referencing the deleted tool.
    assert lib.list_tool_competitors(t3) == []
    lib.close()


def test_software_bulk_delete_requires_auth(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    client = TestClient(appmod.app, raise_server_exceptions=False)
    r = client.post("/admin/tools/software/bulk-delete-check", json={"ids": [1]})
    assert r.status_code == 401
    r2 = client.post("/admin/tools/software/bulk-delete", json={"ids": [1]})
    assert r2.status_code == 401
    if os.path.exists(db):
        os.remove(db)


# --- Communities bulk edit ----------------------------------------------------

def test_communities_bulk_edit_applies_only_to_selected_rows(admin_client):
    client, appmod, db = admin_client
    from linklib.db import Library
    lib = Library(db)
    c1 = lib.add_community(name="Comm A", url="https://ca.example", demographic="CFOs",
                            cost_band="Free", categories=[], approved=1)
    c2 = lib.add_community(name="Comm B", url="https://cb.example", demographic="CFOs",
                            cost_band="Free", categories=[], approved=1)
    lib.close()

    r = client.post("/admin/tools/communities/bulk-edit",
                     json={"ids": [c1], "field": "cost_band", "value": "<$1k/yr"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    lib = Library(db)
    assert lib.get_community(c1)["cost_band"] == "<$1k/yr"
    assert lib.get_community(c2)["cost_band"] == "Free"   # unselected row untouched
    lib.close()


def test_communities_bulk_edit_rejects_non_allowlisted_field(admin_client):
    client, appmod, db = admin_client
    from linklib.db import Library
    lib = Library(db)
    c1 = lib.add_community(name="Comm A", url="https://ca.example", demographic="CFOs",
                            cost_band="Free", categories=[], approved=1)
    lib.close()

    r = client.post("/admin/tools/communities/bulk-edit",
                     json={"ids": [c1], "field": "name", "value": "HACKED"})
    assert r.status_code == 400

    r2 = client.post("/admin/tools/communities/bulk-edit",
                      json={"ids": [c1], "field": "notes", "value": "HACKED"})
    assert r2.status_code == 400

    lib = Library(db)
    comm = lib.get_community(c1)
    assert comm["name"] == "Comm A"
    lib.close()


def test_communities_bulk_edit_rejects_invalid_enum_value(admin_client):
    client, appmod, db = admin_client
    from linklib.db import Library
    lib = Library(db)
    c1 = lib.add_community(name="Comm A", url="https://ca.example", demographic="CFOs",
                            cost_band="Free", categories=[], approved=1)
    lib.close()

    r = client.post("/admin/tools/communities/bulk-edit",
                     json={"ids": [c1], "field": "cost_band", "value": "NOT_A_REAL_BAND"})
    assert r.status_code == 400

    lib = Library(db)
    assert lib.get_community(c1)["cost_band"] == "Free"
    lib.close()


def test_communities_bulk_edit_categories(admin_client):
    client, appmod, db = admin_client
    from linklib.db import Library
    lib = Library(db)
    lib.add_community_category("Peer Group")
    c1 = lib.add_community(name="Comm A", url="https://ca.example", demographic="CFOs",
                            cost_band="Free", categories=[], approved=1)
    lib.close()

    r = client.post("/admin/tools/communities/bulk-edit",
                     json={"ids": [c1], "field": "categories", "value": ["Peer Group"]})
    assert r.status_code == 200

    lib = Library(db)
    assert lib.get_community(c1)["categories"] == ["Peer Group"]
    lib.close()


# --- Communities bulk delete --------------------------------------------------

def test_communities_bulk_delete_check_flags_outside_competitor_references(admin_client):
    client, appmod, db = admin_client
    from linklib.db import Library
    lib = Library(db)
    c1 = lib.add_community(name="Comm A", url="https://ca.example", demographic="CFOs",
                            cost_band="Free", categories=[], approved=1)
    c2 = lib.add_community(name="Comm B", url="https://cb.example", demographic="CFOs",
                            cost_band="Free", categories=[], approved=1)
    c3 = lib.add_community(name="Comm C", url="https://cc.example", demographic="CFOs",
                            cost_band="Free", categories=[], approved=1)
    lib.add_community_competitor(c1, c3)  # C references A as a similar community
    lib.close()

    r = client.post("/admin/tools/communities/bulk-delete-check", json={"ids": [c1]})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["tools"] == [{"id": c1, "name": "Comm A"}]
    assert body["warnings"] == [{"name": "Comm A", "referenced_by": ["Comm C"]}]

    # Deleting the referencer alongside the referenced row clears the warning.
    r2 = client.post("/admin/tools/communities/bulk-delete-check", json={"ids": [c1, c3]})
    assert r2.status_code == 200
    assert r2.json()["warnings"] == []

    r3 = client.post("/admin/tools/communities/bulk-delete-check", json={"ids": [c2]})
    assert r3.status_code == 200
    assert r3.json()["warnings"] == []


def test_communities_bulk_delete_removes_selected_rows_via_delete_community(admin_client):
    client, appmod, db = admin_client
    from linklib.db import Library
    lib = Library(db)
    c1 = lib.add_community(name="Comm A", url="https://ca.example", demographic="CFOs",
                            cost_band="Free", categories=[], approved=1)
    c2 = lib.add_community(name="Comm B", url="https://cb.example", demographic="CFOs",
                            cost_band="Free", categories=[], approved=1)
    c3 = lib.add_community(name="Comm C", url="https://cc.example", demographic="CFOs",
                            cost_band="Free", categories=[], approved=1)
    lib.add_community_competitor(c1, c3)
    lib.close()

    r = client.post("/admin/tools/communities/bulk-delete", json={"ids": [c1, c2]})
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    lib = Library(db)
    assert lib.get_community(c1) is None
    assert lib.get_community(c2) is None
    assert lib.get_community(c3) is not None
    assert lib.list_community_competitors(c3) == []
    lib.close()


def test_communities_bulk_delete_requires_auth(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    client = TestClient(appmod.app, raise_server_exceptions=False)
    r = client.post("/admin/tools/communities/bulk-delete-check", json={"ids": [1]})
    assert r.status_code == 401
    r2 = client.post("/admin/tools/communities/bulk-delete", json={"ids": [1]})
    assert r2.status_code == 401
    if os.path.exists(db):
        os.remove(db)


def test_get_community_sorts_categories_alphabetically(admin_client):
    client, appmod, db = admin_client
    from linklib.db import Library
    lib = Library(db)
    c1 = lib.add_community(name="Comm A", url="https://ca.example", demographic="CFOs",
                            cost_band="Free", categories=["Peer Group", "CPE", "Board Prep"], approved=1)
    assert lib.get_community(c1)["categories"] == ["Board Prep", "CPE", "Peer Group"]
    lib.close()


# --- column picker / bulk-edit markup rendering -------------------------------

def test_software_page_renders_column_picker_and_bulk_edit_markup(admin_client):
    client, appmod, db = admin_client
    from linklib.db import Library
    lib = Library(db)
    lib.add_tool("Tool A", "desc", "https://a.example", [], approved=1)
    lib.close()

    r = client.get("/admin/tools/software")
    assert 'class="software-row-cb"' in r.text
    assert "software-bulk-panel" in r.text
    assert 'colpick-software-summary' in r.text


def test_communities_page_renders_column_picker_and_bulk_edit_markup(admin_client):
    client, appmod, db = admin_client
    from linklib.db import Library
    lib = Library(db)
    lib.add_community(name="Comm A", url="https://ca.example", demographic="CFOs",
                       cost_band="Free", categories=[], approved=1)
    lib.close()

    r = client.get("/admin/tools/communities")
    assert 'class="communities-row-cb"' in r.text
    assert "communities-bulk-panel" in r.text
    assert "colpick-communities-cost_band" in r.text


# --- column-picker onchange attribute is well-formed, not truncated ----------
#
# _admin_column_picker_html builds onchange="toggleColumn(...,{json.dumps(...)})"
# inside a double-quoted HTML attribute. json.dumps() also uses double quotes,
# so an unescaped array there closes the attribute at its first element and
# leaves toggleColumn's 4th argument cut off mid-array—the checkbox's onchange
# then either does nothing or throws (Unexpected end of input) instead of
# running toggleColumn, no matter how correct toggleColumn's own JS is. A
# substring check (`'colpick-software-url' in r.text`, as in the tests above)
# can't catch this—it doesn't care where the attribute actually ends. Parsing
# with html.parser, the same way a browser does, can.

def test_software_column_picker_onchange_is_not_truncated(admin_client):
    client, appmod, db = admin_client
    from linklib.db import Library
    lib = Library(db)
    lib.add_tool("Tool A", "desc", "https://a.example", [], approved=1)
    lib.close()

    r = client.get("/admin/tools/software")
    finder = _AttrFinder("colpick-software-summary")
    finder.feed(r.text)
    onchange = finder.found
    assert onchange is not None, "colpick-software-summary checkbox not found"
    assert onchange.startswith("toggleColumn('software','summary',this.checked,")
    assert onchange.endswith(")")
    # The 4th argument must itself be valid, complete JSON—not truncated at
    # the first embedded double quote.
    array_json = onchange[len("toggleColumn('software','summary',this.checked,"):-1]
    parsed = json.loads(array_json)
    assert "summary" in parsed and "categories" in parsed


def test_communities_column_picker_onchange_is_not_truncated(admin_client):
    client, appmod, db = admin_client
    from linklib.db import Library
    lib = Library(db)
    lib.add_community(name="Comm A", url="https://ca.example", demographic="CFOs",
                       cost_band="Free", categories=[], approved=1)
    lib.close()

    r = client.get("/admin/tools/communities")
    finder = _AttrFinder("colpick-communities-cost_band")
    finder.feed(r.text)
    onchange = finder.found
    assert onchange is not None, "colpick-communities-cost_band checkbox not found"
    assert onchange.startswith("toggleColumn('communities','cost_band',this.checked,")
    assert onchange.endswith(")")
    array_json = onchange[len("toggleColumn('communities','cost_band',this.checked,"):-1]
    parsed = json.loads(array_json)
    assert "cost_band" in parsed and "reach" in parsed


# --- shared admin JS block's escaped apostrophe renders as valid JS ----------
#
# _ADMIN_BULK_EDIT_JS is a plain (non-f-string) Python triple-quoted string,
# so Python itself resolves escape sequences in it before the JS ever reaches
# the browser. \\' in the Python source (one escaped backslash + a literal
# quote) is what produces the JS-valid \' (backslash + quote) in the actual
# response; \' alone in the Python source collapses to a bare ' with no
# backslash at all, which prematurely closes the enclosing single-quoted JS
# string and breaks every function in the shared block (this exact class of
# regression shipped once already). A substring check for the Python source
# spelling can't distinguish these—only asserting on the rendered response
# text (what Python actually produced) can.

def test_shared_admin_js_apostrophe_escape_is_valid_js(admin_client):
    client, appmod, db = admin_client
    r = client.get("/admin/tools/software")
    assert r"Couldn\'t load delete preview" in r.text
    assert "Couldn't load delete preview" not in r.text
