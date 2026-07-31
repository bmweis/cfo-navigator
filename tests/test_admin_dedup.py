"""URL-based duplicate blocking on save (Phase 3), both Communities and
Software: Library-level enforcement in add_tool/update_tool/add_community/
update_community, and the admin routes that surface it as a blocking error
with a link to the conflicting entry."""
import os
import tempfile

import pytest

from linklib.db import DuplicateURLError, Library


@pytest.fixture
def lib():
    db = tempfile.mktemp(suffix=".db")
    library = Library(db)
    yield library
    library.close()
    if os.path.exists(db):
        os.remove(db)


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


# --- Library-level: software (tools) -----------------------------------------

def test_add_tool_blocks_exact_duplicate_url(lib):
    lib.add_tool("Tool A", "desc", "https://vendor.example", [], approved=1)
    with pytest.raises(DuplicateURLError):
        lib.add_tool("Tool B", "desc", "https://vendor.example", [], approved=1)


@pytest.mark.parametrize("variant", [
    "https://www.vendor.example/pricing",
    "http://vendor.example/pricing",
    "https://vendor.example/pricing/",
    "https://VENDOR.example/pricing",
])
def test_add_tool_blocks_normalized_variant(lib, variant):
    # normalize_url() intentionally preserves the bare-root "/" vs "" distinction
    # (see linklib/db.py), so these variants use a real path to exercise the
    # protocol/www/trailing-slash/case normalization it does perform.
    lib.add_tool("Tool A", "desc", "https://vendor.example/pricing", [], approved=1)
    with pytest.raises(DuplicateURLError):
        lib.add_tool("Tool B", "desc", variant, [], approved=1)


def test_add_tool_allows_distinct_urls(lib):
    lib.add_tool("Tool A", "desc", "https://vendor-a.example", [], approved=1)
    tool_id = lib.add_tool("Tool B", "desc", "https://vendor-b.example", [], approved=1)
    assert tool_id


def test_update_tool_blocks_changing_url_to_existing_duplicate(lib):
    lib.add_tool("Tool A", "desc", "https://vendor-a.example", [], approved=1)
    tool_b = lib.add_tool("Tool B", "desc", "https://vendor-b.example", [], approved=1)
    with pytest.raises(DuplicateURLError):
        lib.update_tool(tool_b, "Tool B", "desc", "https://vendor-a.example", [])


def test_update_tool_allows_resaving_unchanged_url(lib):
    tool_id = lib.add_tool("Tool A", "desc", "https://vendor-a.example", [], approved=1)
    # Re-saving the same tool with its own (unchanged) URL must never block —
    # this is exactly what the bulk-edit routes do on every field they don't touch.
    lib.update_tool(tool_id, "Tool A Renamed", "desc", "https://vendor-a.example", [])
    assert lib.get_tool(tool_id)["name"] == "Tool A Renamed"


def test_update_tool_allows_url_change_to_a_free_url(lib):
    tool_id = lib.add_tool("Tool A", "desc", "https://vendor-a.example", [], approved=1)
    lib.update_tool(tool_id, "Tool A", "desc", "https://vendor-a-new.example", [])
    assert lib.get_tool(tool_id)["url"] == "https://vendor-a-new.example"


# --- Library-level: communities ----------------------------------------------

def test_add_community_blocks_exact_duplicate_url(lib):
    lib.add_community(name="Comm A", url="https://comm.example", demographic="CFOs",
                       cost_band="Free", categories=[], approved=1)
    with pytest.raises(DuplicateURLError):
        lib.add_community(name="Comm B", url="https://comm.example", demographic="CFOs",
                           cost_band="Free", categories=[], approved=1)


def test_add_community_blocks_normalized_variant(lib):
    lib.add_community(name="Comm A", url="https://comm.example/join", demographic="CFOs",
                       cost_band="Free", categories=[], approved=1)
    with pytest.raises(DuplicateURLError):
        lib.add_community(name="Comm B", url="https://www.comm.example/join/", demographic="CFOs",
                           cost_band="Free", categories=[], approved=1)


def test_update_community_blocks_changing_url_to_existing_duplicate(lib):
    lib.add_community(name="Comm A", url="https://comm-a.example", demographic="CFOs",
                       cost_band="Free", categories=[], approved=1)
    comm_b = lib.add_community(name="Comm B", url="https://comm-b.example", demographic="CFOs",
                                cost_band="Free", categories=[], approved=1)
    with pytest.raises(DuplicateURLError):
        lib.update_community(comm_b, name="Comm B", url="https://comm-a.example", demographic="CFOs",
                              cost_band="Free", categories=[])


def test_update_community_allows_resaving_unchanged_url(lib):
    comm_id = lib.add_community(name="Comm A", url="https://comm-a.example", demographic="CFOs",
                                 cost_band="Free", categories=[], approved=1)
    lib.update_community(comm_id, name="Comm A", url="https://comm-a.example", demographic="CFOs",
                          cost_band="<$1k/yr", categories=[])
    assert lib.get_community(comm_id)["cost_band"] == "<$1k/yr"


def test_duplicate_error_carries_conflicting_entry_info(lib):
    comm_id = lib.add_community(name="Comm A", url="https://comm.example", demographic="CFOs",
                                 cost_band="Free", categories=[], approved=1)
    with pytest.raises(DuplicateURLError) as exc_info:
        lib.add_community(name="Comm B", url="https://comm.example", demographic="CFOs",
                           cost_band="Free", categories=[], approved=1)
    assert exc_info.value.entry_id == comm_id
    assert exc_info.value.name == "Comm A"
    assert exc_info.value.entry_type == "community"


# --- Route-level: software ----------------------------------------------------

def test_software_create_blocked_on_duplicate_url(admin_client):
    client, appmod, db = admin_client
    lib = Library(db)
    tool_id = lib.add_tool("Existing Tool", "desc", "https://vendor.example", [], approved=1)
    lib.close()

    r = client.post("/admin/tools/new", data={
        "name": "New Tool", "url": "https://vendor.example", "description": "desc", "summary": "desc",
    })
    assert r.status_code == 400
    assert "already exists" in r.json()["detail"]
    assert f"/admin/tools/{tool_id}/edit" in r.json()["detail"]


def test_software_edit_blocked_when_changing_url_to_duplicate(admin_client):
    client, appmod, db = admin_client
    lib = Library(db)
    tool_a = lib.add_tool("Tool A", "desc", "https://vendor-a.example", [], approved=1)
    tool_b = lib.add_tool("Tool B", "desc", "https://vendor-b.example", [], approved=1)
    lib.close()

    r = client.post(f"/admin/tools/{tool_b}/edit", data={
        "name": "Tool B", "url": "https://vendor-a.example", "description": "desc", "summary": "desc",
    })
    assert r.status_code == 400
    assert f"/admin/tools/{tool_a}/edit" in r.json()["detail"]


def test_software_edit_allowed_when_url_unchanged(admin_client):
    client, appmod, db = admin_client
    lib = Library(db)
    tool_id = lib.add_tool("Tool A", "desc", "https://vendor-a.example", [], approved=1)
    lib.close()

    r = client.post(f"/admin/tools/{tool_id}/edit", data={
        "name": "Tool A Updated", "url": "https://vendor-a.example", "description": "new desc", "summary": "new desc",
    }, follow_redirects=False)
    assert r.status_code == 303

    lib = Library(db)
    assert lib.get_tool(tool_id)["name"] == "Tool A Updated"
    lib.close()


# --- Route-level: communities --------------------------------------------------

def test_communities_create_blocked_on_duplicate_url(admin_client):
    client, appmod, db = admin_client
    lib = Library(db)
    comm_id = lib.add_community(name="Existing Comm", url="https://comm.example",
                                 demographic="CFOs", cost_band="Free", categories=[], approved=1)
    lib.close()

    r = client.post("/admin/tools/communities/new", data={
        "name": "New Comm", "url": "https://comm.example", "demographic": "CFOs",
    })
    assert r.status_code == 400
    assert "already exists" in r.json()["detail"]
    assert f"/admin/tools/communities/{comm_id}/edit" in r.json()["detail"]


def test_communities_edit_blocked_when_changing_url_to_duplicate(admin_client):
    client, appmod, db = admin_client
    lib = Library(db)
    comm_a = lib.add_community(name="Comm A", url="https://comm-a.example",
                                demographic="CFOs", cost_band="Free", categories=[], approved=1)
    comm_b = lib.add_community(name="Comm B", url="https://comm-b.example",
                                demographic="CFOs", cost_band="Free", categories=[], approved=1)
    lib.close()

    r = client.post(f"/admin/tools/communities/{comm_b}/edit", data={
        "name": "Comm B", "url": "https://comm-a.example", "demographic": "CFOs",
    })
    assert r.status_code == 400
    assert f"/admin/tools/communities/{comm_a}/edit" in r.json()["detail"]


def test_communities_edit_allowed_when_url_unchanged(admin_client):
    client, appmod, db = admin_client
    lib = Library(db)
    comm_id = lib.add_community(name="Comm A", url="https://comm-a.example",
                                 demographic="CFOs", cost_band="Free", categories=[], approved=1)
    lib.close()

    r = client.post(f"/admin/tools/communities/{comm_id}/edit", data={
        "name": "Comm A Updated", "url": "https://comm-a.example", "demographic": "CFOs",
    }, follow_redirects=False)
    assert r.status_code == 303

    lib = Library(db)
    assert lib.get_community(comm_id)["name"] == "Comm A Updated"
    lib.close()


# --- Regression: bulk-edit (Phase 1) must never trip on url-unchanged resaves --

def test_bulk_edit_unaffected_by_dedup_check(admin_client):
    client, appmod, db = admin_client
    lib = Library(db)
    t1 = lib.add_tool("Tool A", "desc", "https://a.example", [], approved=1)
    t2 = lib.add_tool("Tool B", "desc", "https://b.example", [], approved=1)
    lib.close()

    r = client.post("/admin/tools/software/bulk-edit", json={"ids": [t1, t2], "field": "advisor", "value": "1"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}


# --- Seed script idempotency (normalize_url-based lookup) ---------------------

def test_seed_tools_second_run_does_not_crash():
    """scripts.seed_tools uses an exact-string lookup no longer — switching to
    normalize_url() means a normalized-duplicate is recognized as 'existing'
    rather than tripping Library.add_tool's new DuplicateURLError on rerun."""
    import runpy
    db = tempfile.mktemp(suffix=".db")
    try:
        import sys
        argv = sys.argv
        sys.argv = ["seed_tools.py", "--db", db]
        try:
            runpy.run_module("scripts.seed_tools", run_name="__main__")
            runpy.run_module("scripts.seed_tools", run_name="__main__")
        finally:
            sys.argv = argv
    finally:
        if os.path.exists(db):
            os.remove(db)


def test_seed_communities_second_run_does_not_crash():
    import runpy
    db = tempfile.mktemp(suffix=".db")
    try:
        import sys
        argv = sys.argv
        sys.argv = ["seed_communities.py", "--db", db]
        try:
            runpy.run_module("scripts.seed_communities", run_name="__main__")
            runpy.run_module("scripts.seed_communities", run_name="__main__")
        finally:
            sys.argv = argv
    finally:
        if os.path.exists(db):
            os.remove(db)
