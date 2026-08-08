"""Deletion audit trail for the Software (tools) and Communities directories
(tool_audit_log / community_audit_log). Both tables are hard-delete-only —
tools and communities have no deleted_at soft-delete column — so the audit
write happens inside Library.delete_tool/delete_community themselves,
snapshotting name/url/categories immediately before the DELETE, rather than
at each call site the way archive_audit_log/contact_audit_log are logged.
That guarantees every current delete path (the single-row admin Delete
button, bulk delete, a pending submission's Reject, and a name-duplicate
merge's "delete the loser" step) is covered without relying on each route
remembering to log it separately."""
import os
import tempfile

import pytest

from linklib.db import Library


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


# --- Library.delete_tool / delete_community: direct unit coverage -----------

def test_delete_tool_writes_an_audit_row_with_a_snapshot(lib):
    tool_id = lib.add_tool("Acme", "desc", "https://acme.example",
                            ["Revenue"], approved=1)
    lib.delete_tool(tool_id, admin_id=7)

    rows = lib.list_tool_audit_log()
    assert len(rows) == 1
    row = rows[0]
    assert row["admin_id"] == 7
    assert row["action"] == "delete"
    assert row["item_id"] == tool_id
    assert "Acme" in row["detail"]
    assert "https://acme.example" in row["detail"]
    assert "Revenue" in row["detail"]
    # The row is really gone — the snapshot in `detail` is the only record.
    assert lib.get_tool(tool_id) is None


def test_delete_tool_custom_action_is_recorded(lib):
    keep = lib.add_tool("Keep Co", "desc", "https://keep.example", [], approved=1)
    loser = lib.add_tool("Loser Co", "desc", "https://loser.example", [], approved=1)
    lib.delete_tool(loser, admin_id=1, action="merge")

    rows = lib.list_tool_audit_log()
    assert rows[0]["action"] == "merge"
    assert rows[0]["item_id"] == loser
    assert lib.get_tool(keep) is not None  # untouched


def test_delete_tool_with_no_matching_row_does_not_crash_or_log(lib):
    # Deleting an id that doesn't exist (e.g. a double-submit) must not
    # raise, and must not write a bogus audit row with no real snapshot.
    lib.delete_tool(999999, admin_id=1)
    assert lib.list_tool_audit_log() == []


def test_delete_community_writes_an_audit_row_with_a_snapshot(lib):
    community_id = lib.add_community(
        name="Finance Leaders", url="https://fl.example",
        demographic="CFOs", cost_band="Free", categories=["Networking"],
        approved=1,
    )
    lib.delete_community(community_id, admin_id=3, action="reject")

    rows = lib.list_community_audit_log()
    assert len(rows) == 1
    row = rows[0]
    assert row["admin_id"] == 3
    assert row["action"] == "reject"
    assert row["item_id"] == community_id
    assert "Finance Leaders" in row["detail"]
    assert "https://fl.example" in row["detail"]
    assert "Networking" in row["detail"]
    assert lib.get_community(community_id) is None


# --- End-to-end through the admin routes -------------------------------------

def test_admin_single_delete_route_logs_audit(admin_client):
    client, appmod, db = admin_client
    lib = Library(db)
    tool_id = lib.add_tool("Route Co", "desc", "https://route.example", [], approved=1)
    lib.close()

    r = client.post(f"/admin/tools/{tool_id}/delete", data={"redirect_to": "/admin/tools/software"},
                     follow_redirects=False)
    assert r.status_code == 303

    lib = Library(db)
    rows = lib.list_tool_audit_log()
    lib.close()
    assert len(rows) == 1
    assert rows[0]["item_id"] == tool_id
    assert rows[0]["action"] == "delete"


def test_admin_bulk_delete_route_logs_one_row_per_tool(admin_client):
    client, appmod, db = admin_client
    lib = Library(db)
    t1 = lib.add_tool("Bulk A", "desc", "https://bulka.example", [], approved=1)
    t2 = lib.add_tool("Bulk B", "desc", "https://bulkb.example", [], approved=1)
    lib.close()

    r = client.post("/admin/tools/software/bulk-delete", json={"ids": [t1, t2]})
    assert r.status_code == 200

    lib = Library(db)
    rows = lib.list_tool_audit_log()
    lib.close()
    assert {row["item_id"] for row in rows} == {t1, t2}


def test_admin_tools_reject_route_logs_reject_action(admin_client):
    client, appmod, db = admin_client
    lib = Library(db)
    tool_id = lib.add_tool("Pending Co", "desc", "https://pending.example", [], approved=0)
    lib.close()

    r = client.post(f"/admin/tools/{tool_id}/reject", follow_redirects=False)
    assert r.status_code == 303

    lib = Library(db)
    rows = lib.list_tool_audit_log()
    lib.close()
    assert len(rows) == 1
    assert rows[0]["action"] == "reject"
    assert rows[0]["item_id"] == tool_id


def test_admin_name_duplicate_merge_logs_merge_action(admin_client):
    client, appmod, db = admin_client
    lib = Library(db)
    keep_id = lib.add_tool("Keep Merge Co", "desc", "https://keepmerge.example", [], approved=1)
    delete_id = lib.add_tool("Keep Merge Co (Duplicate)", "desc",
                              "https://deletemerge.example", [], approved=1)
    lib.close()

    r = client.post("/admin/tools/name-duplicates/merge",
                     data={"keep_id": keep_id, "delete_id": delete_id}, follow_redirects=False)
    assert r.status_code == 303

    lib = Library(db)
    rows = lib.list_tool_audit_log()
    lib.close()
    assert len(rows) == 1
    assert rows[0]["action"] == "merge"
    assert rows[0]["item_id"] == delete_id


def test_admin_community_single_delete_route_logs_audit(admin_client):
    client, appmod, db = admin_client
    lib = Library(db)
    community_id = lib.add_community(
        name="Route Community", url="https://routecommunity.example",
        demographic="CFOs", cost_band="Free", categories=[], approved=1,
    )
    lib.close()

    r = client.post(f"/admin/tools/communities/{community_id}/delete", follow_redirects=False)
    assert r.status_code == 303

    lib = Library(db)
    rows = lib.list_community_audit_log()
    lib.close()
    assert len(rows) == 1
    assert rows[0]["item_id"] == community_id
    assert rows[0]["action"] == "delete"


def test_admin_community_bulk_delete_route_logs_one_row_per_community(admin_client):
    client, appmod, db = admin_client
    lib = Library(db)
    c1 = lib.add_community(name="Bulk C1", url="https://bulkc1.example",
                            demographic="CFOs", cost_band="Free", categories=[], approved=1)
    c2 = lib.add_community(name="Bulk C2", url="https://bulkc2.example",
                            demographic="CFOs", cost_band="Free", categories=[], approved=1)
    lib.close()

    r = client.post("/admin/tools/communities/bulk-delete", json={"ids": [c1, c2]})
    assert r.status_code == 200

    lib = Library(db)
    rows = lib.list_community_audit_log()
    lib.close()
    assert {row["item_id"] for row in rows} == {c1, c2}


def test_admin_communities_reject_route_logs_reject_action(admin_client):
    client, appmod, db = admin_client
    lib = Library(db)
    community_id = lib.add_community(
        name="Pending Community", url="https://pendingcommunity.example",
        demographic="CFOs", cost_band="Free", categories=[], approved=0,
    )
    lib.close()

    r = client.post(f"/admin/tools/communities/{community_id}/reject", follow_redirects=False)
    assert r.status_code == 303

    lib = Library(db)
    rows = lib.list_community_audit_log()
    lib.close()
    assert len(rows) == 1
    assert rows[0]["action"] == "reject"
    assert rows[0]["item_id"] == community_id
