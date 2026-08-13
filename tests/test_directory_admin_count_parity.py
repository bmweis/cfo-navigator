"""Public directory vs. admin approved-count parity (Phase K remainder).

The public Software directory (/tools/software) and the admin "Approved
software" table (/admin/tools/software) both render from
Library.list_tools(approved_only=True) — same underlying query, different
presentation. Their counts should therefore always agree; if they diverge,
that's a real bug (e.g. one route gaining a silent extra filter) worth
catching immediately rather than discovering by chance later. Same story for
Communities (/tools/communities vs. /admin/tools/communities), which both
render from Library.list_communities(approved_only=True).

This is defense-in-depth, not a fix for a known bug — it asserts against the
actual rendered HTTP responses (per CLAUDE.md's "validate what the browser
actually receives" lesson), not the library layer directly, so it would catch
a route-level filter drifting out of sync even though both currently call the
exact same Library method.
"""
import json
import os
import re
import tempfile

import pytest


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


def test_software_public_and_admin_counts_match(admin_client):
    client, appmod, db = admin_client
    lib = appmod._lib()
    try:
        for i in range(3):
            lib.add_tool(f"Tool {i}", f"Description {i}", f"https://tool{i}.example.com",
                         ["FP&A"], approved=1)
        # A pending (unapproved) submission should count in neither total.
        lib.add_tool("Pending Tool", "Pending desc", "https://pending.example.com",
                     ["FP&A"], approved=0)
    finally:
        lib.close()

    public = client.get("/tools/software")
    assert public.status_code == 200
    m = re.search(r"var ALL_TOOLS = (\[.*?\]);\n", public.text, re.S)
    assert m, "couldn't find ALL_TOOLS in the rendered public directory page"
    public_tools = json.loads(m.group(1))

    admin = client.get("/admin/tools/software")
    assert admin.status_code == 200
    admin_rows = admin.text.count('class="software-row-cb"')

    assert len(public_tools) == 3
    assert admin_rows == 3
    assert len(public_tools) == admin_rows


def test_communities_public_and_admin_counts_match(admin_client):
    client, appmod, db = admin_client
    lib = appmod._lib()
    try:
        for i in range(4):
            lib.add_community(f"Community {i}", f"https://community{i}.example.com",
                              "Finance leaders", "free", ["Peer group"], approved=1)
        lib.add_community("Pending Community", "https://pending-community.example.com",
                          "Finance leaders", "free", ["Peer group"], approved=0)
    finally:
        lib.close()

    public = client.get("/tools/communities")
    assert public.status_code == 200
    m = re.search(r"var ALL_COMMUNITIES = (\[.*?\]);\n", public.text, re.S)
    assert m, "couldn't find ALL_COMMUNITIES in the rendered public directory page"
    public_communities = json.loads(m.group(1))

    admin = client.get("/admin/tools/communities")
    assert admin.status_code == 200
    admin_rows = admin.text.count('class="communities-row-cb"')

    assert len(public_communities) == 4
    assert admin_rows == 4
    assert len(public_communities) == admin_rows
