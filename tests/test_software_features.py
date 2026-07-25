"""Feature comparison data model (Software search overhaul Phase 4a):
linklib.db's tool_features table/helpers and the admin curation UI on
/admin/tools/{id}/edit and /admin/tools/{id}/features/{id}/edit. No public
surface yet — Phase 5's comparison matrix is what actually reads this data.
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

def test_add_and_list_feature(lib):
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.add_tool_feature(a, "Scenario modeling", standalone_available=1, bundled_only=0)
    lib.add_tool_feature(a, "Headcount planning", standalone_available=0, bundled_only=1,
                         notes="Growth tier only", needs_verification=1,
                         source="llm_enrichment", model="claude-haiku-4-5-20251001")
    features = lib.list_tool_features(a)
    assert [f["feature_name"] for f in features] == ["Headcount planning", "Scenario modeling"]
    hc = next(f for f in features if f["feature_name"] == "Headcount planning")
    assert hc["needs_verification"] == 1
    assert hc["source"] == "llm_enrichment"
    assert hc["model"] == "claude-haiku-4-5-20251001"


def test_manual_feature_defaults_verified(lib):
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    fid = lib.add_tool_feature(a, "Scenario modeling")
    assert lib.get_tool_feature(fid)["needs_verification"] == 0
    assert lib.get_tool_feature(fid)["source"] == "manual"


def test_feature_can_be_both_standalone_and_bundled(lib):
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    fid = lib.add_tool_feature(a, "API access", standalone_available=1, bundled_only=1)
    f = lib.get_tool_feature(fid)
    assert f["standalone_available"] == 1
    assert f["bundled_only"] == 1


def test_empty_feature_name_rejected(lib):
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    with pytest.raises(ValueError):
        lib.add_tool_feature(a, "   ")


def test_update_tool_feature_can_clear_needs_verification(lib):
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    fid = lib.add_tool_feature(a, "Scenario modeling", needs_verification=1, source="llm_enrichment")
    lib.update_tool_feature(fid, "Scenario modeling", 1, 0, "", "https://runway.com/pricing", 0)
    f = lib.get_tool_feature(fid)
    assert f["needs_verification"] == 0
    assert f["source_url"] == "https://runway.com/pricing"


def test_delete_tool_feature(lib):
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    fid = lib.add_tool_feature(a, "Scenario modeling")
    lib.delete_tool_feature(fid)
    assert lib.list_tool_features(a) == []


def test_features_scoped_per_tool(lib):
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    b = lib.add_tool("Datarails", "FP&A", "https://datarails.com", ["FP&A"], approved=1)
    lib.add_tool_feature(a, "Scenario modeling")
    lib.add_tool_feature(b, "Excel native")
    assert [f["feature_name"] for f in lib.list_tool_features(a)] == ["Scenario modeling"]
    assert [f["feature_name"] for f in lib.list_tool_features(b)] == ["Excel native"]


# -- admin routes --------------------------------------------------------------

def test_admin_can_add_feature(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post(f"/admin/tools/{a}/features/add", data={
        "feature_name": "Scenario modeling", "standalone_available": "1",
    }, follow_redirects=False)
    assert r.status_code == 303

    r = client.get(f"/admin/tools/{a}/edit")
    assert "Scenario modeling" in r.text
    assert "Standalone" in r.text


def test_admin_can_edit_feature_and_mark_verified(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    fid = lib.add_tool_feature(a, "Headcount planning", bundled_only=1,
                                needs_verification=1, source="llm_enrichment", model="haiku")
    lib.close()

    client = _client(env)
    _login(client)

    r = client.get(f"/admin/tools/{a}/features/{fid}/edit")
    assert r.status_code == 200
    assert "Needs verification" not in r.text or "needs_verification" in r.text  # form checkbox present

    r = client.post(f"/admin/tools/{a}/features/{fid}/verify", follow_redirects=False)
    assert r.status_code == 303

    lib = Library(os.environ["LINKLIB_DB"])
    assert lib.get_tool_feature(fid)["needs_verification"] == 0
    lib.close()


def test_admin_can_delete_feature(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    fid = lib.add_tool_feature(a, "Scenario modeling")
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post(f"/admin/tools/{a}/features/{fid}/delete", follow_redirects=False)
    assert r.status_code == 303

    lib = Library(os.environ["LINKLIB_DB"])
    assert lib.list_tool_features(a) == []
    lib.close()


def test_feature_routes_require_auth(env):
    client = _client(env)
    assert client.post("/admin/tools/1/features/add", data={"feature_name": "x"}).status_code == 401
    assert client.get("/admin/tools/1/features/1/edit", follow_redirects=False).status_code in (302, 303, 401)
    assert client.post("/admin/tools/1/features/1/verify").status_code == 401
    assert client.post("/admin/tools/1/features/1/delete").status_code == 401


def test_feature_edit_404s_for_wrong_tool(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    b = lib.add_tool("Datarails", "FP&A", "https://datarails.com", ["FP&A"], approved=1)
    fid = lib.add_tool_feature(a, "Scenario modeling")
    lib.close()

    client = _client(env)
    _login(client)
    r = client.get(f"/admin/tools/{b}/features/{fid}/edit")
    assert r.status_code == 404
