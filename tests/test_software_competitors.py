"""Competitor cross-links + differentiation copy (Software search overhaul
Phase 3): linklib.db's tool_competitors table/helpers, the admin curation UI
on /admin/tools/{id}/edit, and the public "Closest competitors" section on
/tools/software/{slug}.
"""
import os
import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


@pytest.fixture
def lib(tmp_path):
    from linklib.db import Library
    db = Library(str(tmp_path / "t.db"))
    try:
        yield db
    finally:
        db.close()


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


def _login(client):
    r = client.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    assert r.status_code in (302, 303)


# -- linklib.db --------------------------------------------------------------

def test_add_competitor_is_symmetric(lib):
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    b = lib.add_tool("Datarails", "FP&A", "https://datarails.com", ["FP&A"], approved=1)
    lib.add_tool_competitor(a, b)
    assert [t["name"] for t in lib.list_tool_competitors(a)] == ["Datarails"]
    assert [t["name"] for t in lib.list_tool_competitors(b)] == ["Runway"]


def test_add_competitor_either_direction_dedupes(lib):
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    b = lib.add_tool("Datarails", "FP&A", "https://datarails.com", ["FP&A"], approved=1)
    lib.add_tool_competitor(a, b)
    lib.add_tool_competitor(b, a)
    assert len(lib.list_tool_competitors(a)) == 1


def test_self_competitor_rejected(lib):
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    with pytest.raises(ValueError):
        lib.add_tool_competitor(a, a)


def test_remove_competitor(lib):
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    b = lib.add_tool("Datarails", "FP&A", "https://datarails.com", ["FP&A"], approved=1)
    lib.add_tool_competitor(a, b)
    lib.remove_tool_competitor(b, a)  # remove from the other direction
    assert lib.list_tool_competitors(a) == []


def test_list_competitors_excludes_unapproved(lib):
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    pending = lib.add_tool("Stealth Co", "FP&A", "https://stealth.example", ["FP&A"], approved=0)
    lib.add_tool_competitor(a, pending)
    assert lib.list_tool_competitors(a) == []


def test_suggest_ranks_by_overlap_and_excludes_self_and_existing(lib):
    target = lib.add_tool("Vareto", "FP&A", "https://vareto.com", ["FP&A", "Headcount Planning"], approved=1)
    best = lib.add_tool("Pigment", "FP&A+HC", "https://pigment.com", ["FP&A", "Headcount Planning"], approved=1)
    ok = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    already = lib.add_tool("Datarails", "FP&A", "https://datarails.com", ["FP&A"], approved=1)
    lib.add_tool("Ramp", "Spend", "https://ramp.com", ["Procurement/Spend"], approved=1)
    lib.add_tool_competitor(target, already)

    suggestions = lib.suggest_tool_competitors(target)
    names = [s["name"] for s in suggestions]
    assert names[0] == "Pigment"          # 2 shared tags, ranked first
    assert "Runway" in names              # 1 shared tag
    assert "Datarails" not in names       # already curated, excluded
    assert "Ramp" not in names            # no overlap at all


def test_suggest_empty_when_tool_has_no_categories(lib):
    a = lib.add_tool("Mystery Co", "No tags", "https://mystery.example", [], approved=1)
    lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    assert lib.suggest_tool_competitors(a) == []


def test_update_tool_differentiation(lib):
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.update_tool_differentiation(a, "  Faster onboarding.  ")
    assert lib.get_tool(a)["differentiation_note"] == "Faster onboarding."


# -- admin routes --------------------------------------------------------------

def test_admin_can_add_and_remove_competitor(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    b = lib.add_tool("Datarails", "FP&A", "https://datarails.com", ["FP&A"], approved=1)
    lib.close()

    client = _client(env)
    _login(client)

    r = client.post(f"/admin/tools/{a}/competitors/add", data={"competitor_id": str(b)},
                     follow_redirects=False)
    assert r.status_code == 303

    r = client.get(f"/admin/tools/{a}/edit")
    assert "Datarails" in r.text

    r = client.post(f"/admin/tools/{a}/competitors/{b}/remove", follow_redirects=False)
    assert r.status_code == 303
    r = client.get(f"/admin/tools/{a}/edit")
    assert "No competitors curated yet." in r.text


def test_admin_edit_saves_differentiation_note(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post(f"/admin/tools/{a}/edit", data={
        "name": "Runway", "url": "https://runway.com", "description": "FP&A", "summary": "FP&A",
        "differentiation_note": "Human-readable formulas, real-time sync.",
    }, follow_redirects=False)
    assert r.status_code == 303

    lib = Library(os.environ["LINKLIB_DB"])
    assert lib.get_tool(a)["differentiation_note"] == "Human-readable formulas, real-time sync."
    lib.close()


def test_admin_edit_unauthenticated_rejected(env):
    r = _client(env).post("/admin/tools/1/competitors/add", data={"competitor_id": "2"})
    assert r.status_code == 401


def test_bulk_edit_does_not_clobber_differentiation_note(env):
    """The Software bulk-edit panel resaves every field via update_tool on
    every call — differentiation_note deliberately lives on a separate narrow
    update method so bulk edit can never blank it out just by not knowing
    about it."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.update_tool_differentiation(a, "Keeps its edge on real-time sync.")
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post("/admin/tools/software/bulk-edit", json={"ids": [a], "field": "advisor", "value": "1"})
    assert r.status_code == 200

    lib = Library(os.environ["LINKLIB_DB"])
    tool = lib.get_tool(a)
    assert tool["advisor"] == 1
    assert tool["differentiation_note"] == "Keeps its edge on real-time sync."
    lib.close()


# -- public profile page --------------------------------------------------------

def test_profile_page_shows_competitors_and_differentiation(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A built for high-growth teams.", "https://runway.com", ["FP&A"], approved=1)
    b = lib.add_tool("Datarails", "FP&A inside Excel.", "https://datarails.com", ["FP&A"], approved=1)
    lib.add_tool_competitor(a, b)
    lib.update_tool_differentiation(a, "Human-readable formulas set it apart.")
    lib.close()

    r = _client(env).get("/tools/software/runway")
    assert r.status_code == 200
    assert "Closest competitors" in r.text
    assert "/tools/software/datarails" in r.text
    assert "Datarails" in r.text
    assert "How this differs" in r.text
    assert "Human-readable formulas set it apart." in r.text


def test_profile_page_hides_sections_when_empty(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    lib.add_tool("Solo Co", "No competitors curated.", "https://solo.example", [], approved=1)
    lib.close()

    r = _client(env).get("/tools/software/solo-co")
    assert r.status_code == 200
    assert "Closest competitors" not in r.text
    assert "How this differs" not in r.text
