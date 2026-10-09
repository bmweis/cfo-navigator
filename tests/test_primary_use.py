"""Primary use for Software vendors (issue #624, PR 1).

One required category per vendor, the main reason someone buys it, stored in
`tools.primary_category`. `categories_json` stays the full set. The invariant:
a non-empty primary is always a member of the set. The admin add and edit forms
refuse a save with no primary, the saved set is the primary plus the "Also used
for" ticks, the approve step needs a primary, bulk edit may not remove one, and
MCP serves it as an additive field.
"""
import importlib
import json
import os
import tempfile

import pytest

from linklib import compare, tool_labels
from linklib.db import Library

from tests import test_mcp_toolbox as _base
from tests.test_mcp_toolbox import _call_tool, _dict_result, _list_result, _seed_tool


# --- Library -----------------------------------------------------------------

@pytest.fixture
def lib(tmp_path):
    lib = Library(str(tmp_path / "t.db"))
    yield lib
    lib.close()


def _add(lib, name="Acme", cats=("FP&A", "ERP"), **kw):
    return lib.add_tool(name, "d", f"https://{name.lower()}.example", list(cats), approved=1, **kw)


def test_column_defaults_to_empty_and_is_returned(lib):
    tid = _add(lib)
    assert lib.get_tool(tid)["primary_category"] == ""


def test_add_tool_stores_primary_and_adds_it_to_the_set_if_missing(lib):
    tid = _add(lib, cats=("ERP",), primary_category="FP&A")
    t = lib.get_tool(tid)
    assert t["primary_category"] == "FP&A"
    assert t["categories"] == ["ERP", "FP&A"]


def test_update_tool_none_keeps_primary_and_str_sets_it(lib):
    tid = _add(lib, primary_category="ERP")
    t = lib.get_tool(tid)
    kw = dict(name=t["name"], description="d", url=t["url"], categories=["FP&A", "ERP"])
    lib.update_tool(tid, **kw)                      # caller says nothing about the primary
    assert lib.get_tool(tid)["primary_category"] == "ERP"
    lib.update_tool(tid, primary_category="FP&A", **kw)
    assert lib.get_tool(tid)["primary_category"] == "FP&A"


def test_update_tool_keeps_primary_inside_the_set(lib):
    tid = _add(lib, primary_category="ERP")
    t = lib.get_tool(tid)
    lib.update_tool(tid, name=t["name"], description="d", url=t["url"], categories=["FP&A"])
    got = lib.get_tool(tid)
    assert got["primary_category"] == "ERP" and "ERP" in got["categories"]


def test_set_tool_primary_enforces_invariant_and_can_skip_updated_at(lib):
    tid = _add(lib, cats=("ERP",))
    before = lib.get_tool(tid)["updated_at"]
    lib.set_tool_primary(tid, "Revenue", touch=False)
    t = lib.get_tool(tid)
    assert t["primary_category"] == "Revenue" and "Revenue" in t["categories"]
    assert t["updated_at"] == before
    lib.set_tool_primary(tid, "")
    assert lib.get_tool(tid)["primary_category"] == ""
    with pytest.raises(ValueError):
        lib.set_tool_primary(99999, "ERP")


def test_rename_category_carries_primary(lib):
    cid = lib.add_tool_category("FP&A")
    tid = _add(lib, cats=("FP&A", "ERP"), primary_category="FP&A")
    other = _add(lib, name="Other", cats=("FP&A", "ERP"), primary_category="ERP")
    lib.rename_tool_category(cid, "Planning")
    assert lib.get_tool(tid)["primary_category"] == "Planning"
    assert "Planning" in lib.get_tool(tid)["categories"]
    assert lib.get_tool(other)["primary_category"] == "ERP"


def test_delete_category_clears_primary_only_where_it_was_the_primary(lib):
    cid = lib.add_tool_category("FP&A")
    a = _add(lib, name="A", cats=("FP&A", "ERP"), primary_category="FP&A")
    b = _add(lib, name="B", cats=("FP&A", "ERP"), primary_category="ERP")
    lib.delete_tool_category(cid)
    assert lib.get_tool(a)["primary_category"] == ""
    assert lib.get_tool(a)["categories"] == ["ERP"]
    assert lib.get_tool(b)["primary_category"] == "ERP"


def test_add_category_to_tool_leaves_primary_alone(lib):
    tid = _add(lib, primary_category="ERP")
    lib.add_category_to_tool(tid, "Revenue")
    assert lib.get_tool(tid)["primary_category"] == "ERP"


# --- Admin forms --------------------------------------------------------------

@pytest.fixture
def app_module(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    seed = Library(db)
    seed.seed_voice_prompts()
    for n in ("FP&A", "ERP", "Revenue"):
        seed.add_tool_category(n)
    seed.close()
    import webapp.app as appmod
    importlib.reload(appmod)
    yield appmod
    for ext in ("", "-shm", "-wal"):
        if os.path.exists(db + ext):
            os.remove(db + ext)


@pytest.fixture
def admin(app_module):
    from fastapi.testclient import TestClient
    c = TestClient(app_module.app, raise_server_exceptions=True)
    assert c.post("/login", data={"username": "admin", "password": "adminpass"},
                  follow_redirects=False).status_code in (302, 303)
    return c


def _db():
    return Library(os.environ["LINKLIB_DB"])


def _count():
    l = _db()
    try:
        return l.conn.execute("SELECT COUNT(*) FROM tools").fetchone()[0]
    finally:
        l.close()


def _new_form(**over):
    d = dict(name="Acme", url="https://acme.example", description="d", summary="s")
    d.update(over)
    return d


def _edit_form(t, **over):
    d = dict(name=t["name"], url=t["url"], description="d", summary="s")
    d.update(over)
    return d


def test_new_form_refuses_a_save_with_no_primary_and_writes_nothing(admin):
    r = admin.post("/admin/tools/software/new", data=_new_form(categories=["ERP"]), follow_redirects=False)
    assert r.status_code == 400
    assert "Nothing was saved" in r.text and tool_labels.PRIMARY_USE in r.text
    assert "Choose the main reason someone buys it." in r.text
    assert _count() == 0


def test_new_form_refuses_a_primary_that_is_not_a_category(admin):
    r = admin.post("/admin/tools/software/new", data=_new_form(primary_category="Nope"), follow_redirects=False)
    assert r.status_code == 400 and _count() == 0


def test_new_form_saves_union_of_primary_and_ticks(admin):
    r = admin.post("/admin/tools/software/new",
                   data=_new_form(primary_category="FP&A", categories=["Revenue", "FP&A"]),
                   follow_redirects=False)
    assert r.status_code == 303
    l = _db()
    t = l.list_tools(approved_only=False)[0]
    l.close()
    assert t["primary_category"] == "FP&A"
    assert t["categories"] == ["FP&A", "Revenue"]


def test_new_form_primary_alone_is_enough(admin):
    admin.post("/admin/tools/software/new", data=_new_form(primary_category="ERP"), follow_redirects=False)
    l = _db()
    t = l.list_tools(approved_only=False)[0]
    l.close()
    assert t["categories"] == ["ERP"] and t["primary_category"] == "ERP"


def _tool(cats, primary=""):
    l = _db()
    tid = l.add_tool("Acme", "orig", "https://acme.example", cats, approved=1, summary="s",
                     primary_category=primary)
    t = l.get_tool(tid)
    l.close()
    return t


def test_edit_form_refuses_empty_primary_and_changes_nothing(admin):
    t = _tool(["ERP", "FP&A"], "ERP")
    r = admin.post(f"/tools/software/{t['slug']}/edit", data=_edit_form(t, description="CHANGED"),
                   follow_redirects=False)
    assert r.status_code == 400 and "Nothing was saved" in r.text
    assert "CHANGED" in r.text               # typed text is kept in the boxes
    l = _db()
    got = l.get_tool(t["id"])
    l.close()
    assert got["description"] == "orig" and got["primary_category"] == "ERP"


def test_edit_form_saves_union_and_can_move_the_primary(admin):
    t = _tool(["ERP", "FP&A"], "ERP")
    r = admin.post(f"/tools/software/{t['slug']}/edit",
                   data=_edit_form(t, primary_category="Revenue", categories=["ERP"]),
                   follow_redirects=False)
    assert r.status_code == 303
    l = _db()
    got = l.get_tool(t["id"])
    l.close()
    assert got["primary_category"] == "Revenue"
    assert got["categories"] == ["ERP", "Revenue"]       # FP&A unticked, so dropped


def test_edit_page_preselect_rules(admin):
    single = _tool(["ERP"])
    html = admin.get(f"/tools/software/{single['slug']}/edit").text
    assert '<option value="ERP" selected>' in html
    l = _db()
    multi = l.get_tool(l.add_tool("Multi", "d", "https://multi.example", ["ERP", "FP&A"], approved=1, summary="s"))
    l.close()
    html = admin.get(f"/tools/software/{multi['slug']}/edit").text
    assert "selected>Choose one" in html or '<option value="" disabled selected>Choose one</option>' in html
    assert " selected>ERP" not in html and " selected>FP&amp;A" not in html
    assert tool_labels.ALSO_USED_FOR in html and tool_labels.PRIMARY_USE in html


def test_edit_page_shows_stored_primary(admin):
    t = _tool(["ERP", "FP&A"], "FP&A")
    html = admin.get(f"/tools/software/{t['slug']}/edit").text
    assert '<option value="FP&amp;A" selected>' in html


# --- Approving a pending vendor needs a primary ----------------------------------

def _pending(cats, primary=""):
    l = _db()
    tid = l.add_tool("Pending", "d", "https://pending.example", cats, approved=0, summary="s",
                     primary_category=primary)
    l.close()
    return tid


def test_approve_without_primary_is_refused(admin):
    tid = _pending(["ERP", "FP&A"])
    r = admin.post(f"/admin/tools/software/{tid}/approve", data={}, follow_redirects=False)
    assert r.status_code == 400 and tool_labels.PRIMARY_USE in r.text
    l = _db()
    t = l.get_tool(tid)
    l.close()
    assert t["approved"] == 0 and t["primary_category"] == ""


def test_approve_with_primary_sets_it_and_goes_live(admin):
    tid = _pending(["ERP", "FP&A"])
    r = admin.post(f"/admin/tools/software/{tid}/approve", data={"primary_category": "FP&A"},
                   follow_redirects=False)
    assert r.status_code == 303
    l = _db()
    t = l.get_tool(tid)
    l.close()
    assert t["approved"] == 1 and t["primary_category"] == "FP&A"


def test_pending_row_offers_a_primary_dropdown(admin):
    _pending(["ERP"])
    html = admin.get("/admin/tools/software").text
    assert 'name="primary_category" required' in html


# --- Bulk edit ---------------------------------------------------------------------

def test_bulk_edit_refuses_to_remove_a_primary_and_writes_nothing(admin):
    l = _db()
    a = l.add_tool("A", "d", "https://a.example", ["ERP", "FP&A"], approved=1, primary_category="ERP")
    b = l.add_tool("B", "d", "https://b.example", ["ERP"], approved=1)
    l.close()
    r = admin.post("/admin/tools/software/bulk-edit",
                   json={"ids": [b, a], "field": "categories", "value": ["Revenue"]})
    assert r.status_code == 400
    assert "A (ERP)" in r.json()["message"] and "B (" not in r.json()["message"]
    l = _db()
    assert l.get_tool(b)["categories"] == ["ERP"]
    assert l.get_tool(a)["categories"] == ["ERP", "FP&A"]
    l.close()


def test_bulk_edit_keeps_primary_through_other_fields_and_allowed_category_changes(admin):
    l = _db()
    a = l.add_tool("A", "d", "https://a.example", ["ERP"], approved=1, primary_category="ERP")
    l.close()
    assert admin.post("/admin/tools/software/bulk-edit",
                      json={"ids": [a], "field": "advisor", "value": "1"}).status_code == 200
    assert admin.post("/admin/tools/software/bulk-edit",
                      json={"ids": [a], "field": "categories", "value": ["ERP", "Revenue"]}).status_code == 200
    l = _db()
    t = l.get_tool(a)
    l.close()
    assert t["primary_category"] == "ERP" and t["categories"] == ["ERP", "Revenue"]


# --- Admin list ----------------------------------------------------------------------

def test_admin_list_has_primary_use_column_with_a_no_primary_label(admin):
    _tool(["ERP", "FP&A"], "FP&A")
    l = _db()
    l.add_tool("Bare", "d", "https://bare.example", ["ERP", "FP&A"], approved=1, summary="s")
    l.close()
    html = admin.get("/admin/tools/software").text
    assert 'data-col="software:primary_category"' in html
    assert "No primary yet" in html
    assert f">{tool_labels.PRIMARY_USE}</th>" in html


# --- Public submit is unchanged ------------------------------------------------------

def test_public_submit_stores_an_unapproved_vendor_with_no_primary(app_module):
    from fastapi.testclient import TestClient
    c = TestClient(app_module.app, raise_server_exceptions=True)
    c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    c.post("/tools/submit", data=dict(name="Sub", url="https://sub.example", description="d",
                                      submitted_by="a@b.co", categories=["ERP"]), follow_redirects=False)
    l = _db()
    rows = [t for t in l.list_tools(approved_only=False) if t["name"] == "Sub"]
    l.close()
    assert rows and rows[0]["approved"] == 0 and rows[0]["primary_category"] == ""


# --- MCP ---------------------------------------------------------------------------------

@pytest.fixture
def live_server(monkeypatch):
    yield from _base.live_server.__wrapped__(monkeypatch)


def test_mcp_serves_primary_category_additively(live_server):
    lib = Library(live_server.db_path)
    a = _seed_tool(lib, "Alpha", categories=["ERP", "FP&A"])
    b = _seed_tool(lib, "Beta", categories=["FP&A"])
    lib.set_tool_primary(a["id"], "FP&A")
    lib.close()
    url, tok = live_server.base_url, live_server.member
    got = _dict_result(_call_tool(url, tok, "get_software", {"slug_or_id": a["slug"]}))
    assert got["primary_category"] == "FP&A" and got["categories"] == ["ERP", "FP&A"]
    hits = _list_result(_call_tool(url, tok, "search_software", {"category": "ERP"}))
    assert [h["name"] for h in hits] == ["Alpha"]          # the filter still matches any tag
    assert hits[0]["primary_category"] == "FP&A" and hits[0]["categories"] == ["ERP", "FP&A"]
    cmp_ = _dict_result(_call_tool(url, tok, "compare_software", {"ids": [a["id"], b["id"]]}))
    by_name = {e["name"]: e for e in cmp_["entities"]}
    assert by_name["Alpha"]["primary_category"] == "FP&A"
    assert by_name["Beta"]["primary_category"] == ""        # none yet: empty string, not missing


def test_parity_registry_names_the_new_column():
    assert compare.MCP_PARITY["tools.primary_category"] == "mcp:get_software.primary_category"
