"""Competitor cross-links + differentiation copy (Software search overhaul
Phase 3): linklib.db's tool_competitors table/helpers, the admin curation UI
on /tools/software/{slug}/edit, and the public "Competitors" section on
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
    assert lib.get_tool(a)["competitive_differentiation"] == "Faster onboarding."


# -- admin routes --------------------------------------------------------------

def test_admin_can_add_and_remove_competitor(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    a_slug = lib.get_tool(a)["slug"]
    b = lib.add_tool("Datarails", "FP&A", "https://datarails.com", ["FP&A"], approved=1)
    lib.close()

    client = _client(env)
    _login(client)

    r = client.post(f"/admin/tools/software/{a}/competitors/add", data={"competitor_id": str(b)},
                     follow_redirects=False)
    assert r.status_code == 303

    r = client.get(f"/tools/software/{a_slug}/edit")
    assert "Datarails" in r.text

    r = client.post(f"/admin/tools/software/{a}/competitors/{b}/remove", follow_redirects=False)
    assert r.status_code == 303
    r = client.get(f"/tools/software/{a_slug}/edit")
    assert "No competitors curated yet." in r.text


def test_admin_can_add_selected_competitors_in_bulk(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    a_slug = lib.get_tool(a)["slug"]
    b = lib.add_tool("Datarails", "FP&A", "https://datarails.com", ["FP&A"], approved=1)
    c = lib.add_tool("Pigment", "FP&A", "https://pigment.com", ["FP&A"], approved=1)
    lib.close()

    client = _client(env)
    _login(client)

    r = client.post(f"/admin/tools/software/{a}/competitors/add-selected",
                     data={"competitor_id": [str(b), str(c)], "ai_drafted_fields": "competitors"},
                     follow_redirects=False)
    assert r.status_code == 303

    lib = Library(os.environ["LINKLIB_DB"])
    names = {t["name"] for t in lib.list_tool_competitors(a)}
    assert names == {"Datarails", "Pigment"}
    reviews = lib.list_field_reviews("tool", a)
    assert "competitors" in reviews
    lib.close()


def test_generate_matches_unavailable_without_api_key(env, monkeypatch):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.add_tool("Datarails", "FP&A", "https://datarails.com", ["FP&A"], approved=1)
    lib.close()
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    client = _client(env)
    _login(client)
    r = client.post(f"/admin/tools/software/{a}/competitors/generate-matches")
    assert r.status_code == 503
    assert r.json()["ok"] is False


def test_generate_matches_returns_matched_ids(env, monkeypatch):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    b = lib.add_tool("Datarails", "FP&A", "https://datarails.com", ["FP&A"], approved=1)
    lib.close()

    import linklib.enrich as enrich_mod
    from linklib.enrich import CompetitorMatchResult

    def fake_generate_competitor_matches(name, description, candidates, model=enrich_mod.DEFAULT_MODEL):
        return CompetitorMatchResult(competitor_ids=[c["id"] for c in candidates], model="fake", cost_usd=0.0)

    monkeypatch.setattr(enrich_mod, "generate_competitor_matches", fake_generate_competitor_matches)

    client = _client(env)
    _login(client)
    r = client.post(f"/admin/tools/software/{a}/competitors/generate-matches")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "competitor_ids": [b]}


def test_admin_edit_saves_competitive_differentiation(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    a_slug = lib.get_tool(a)["slug"]
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post(f"/tools/software/{a_slug}/edit", data={
        "name": "Runway", "url": "https://runway.com", "description": "FP&A", "summary": "FP&A",
        "competitive_differentiation": "Human-readable formulas, real-time sync.",
    }, follow_redirects=False)
    assert r.status_code == 303

    lib = Library(os.environ["LINKLIB_DB"])
    assert lib.get_tool(a)["competitive_differentiation"] == "Human-readable formulas, real-time sync."
    lib.close()


def test_admin_edit_unauthenticated_rejected(env):
    r = _client(env).post("/admin/tools/software/1/competitors/add", data={"competitor_id": "2"})
    assert r.status_code == 401


def test_bulk_edit_does_not_clobber_competitive_differentiation(env):
    """The Software bulk-edit panel resaves every field via update_tool on
    every call — competitive_differentiation deliberately lives on a separate narrow
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
    assert tool["competitive_differentiation"] == "Keeps its edge on real-time sync."
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
    assert "Competitors" in r.text
    assert "/tools/software/datarails" in r.text
    assert "Datarails" in r.text
    assert "Bottom line" in r.text
    assert "Human-readable formulas set it apart." in r.text


def test_profile_page_shows_placeholders_for_sections_when_empty(env):
    """Radical-transparency review standard: an empty section used to be
    omitted from the page entirely for every viewer. It now shows an
    honest placeholder instead of vanishing."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Solo Co", "No competitors curated.", "https://solo.example", [], approved=1)
    a_slug = lib.get_tool(a)["slug"]
    lib.close()

    r = _client(env).get(f"/tools/software/{a_slug}")
    assert r.status_code == 200
    assert "Competitors not available." in r.text
    assert "Bottom line not available." in r.text
