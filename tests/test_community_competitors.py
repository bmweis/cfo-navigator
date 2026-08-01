"""Similar-communities cross-links (Competitors/Similar-communities upgrade):
linklib.db's community_competitors table/helpers, the admin curation UI on
/tools/communities/{slug}/edit, and the public "Similar communities" section
on /tools/communities/{slug}. Mirrors tests/test_software_competitors.py —
same shape, own table.
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


def _add_community(lib, name, url, categories, approved=1):
    cid = lib.add_community(name, url, "CFO", "free", categories)
    if approved:
        lib.approve_community(cid)
    return cid


# -- linklib.db --------------------------------------------------------------

def test_add_competitor_is_symmetric(lib):
    a = _add_community(lib, "Alpha Guild", "https://a.example", ["Finance"])
    b = _add_community(lib, "Beta Guild", "https://b.example", ["Finance"])
    lib.add_community_competitor(a, b)
    assert [c["name"] for c in lib.list_community_competitors(a)] == ["Beta Guild"]
    assert [c["name"] for c in lib.list_community_competitors(b)] == ["Alpha Guild"]


def test_add_competitor_either_direction_dedupes(lib):
    a = _add_community(lib, "Alpha Guild", "https://a.example", ["Finance"])
    b = _add_community(lib, "Beta Guild", "https://b.example", ["Finance"])
    lib.add_community_competitor(a, b)
    lib.add_community_competitor(b, a)
    assert len(lib.list_community_competitors(a)) == 1


def test_self_competitor_rejected(lib):
    a = _add_community(lib, "Alpha Guild", "https://a.example", ["Finance"])
    with pytest.raises(ValueError):
        lib.add_community_competitor(a, a)


def test_remove_competitor(lib):
    a = _add_community(lib, "Alpha Guild", "https://a.example", ["Finance"])
    b = _add_community(lib, "Beta Guild", "https://b.example", ["Finance"])
    lib.add_community_competitor(a, b)
    lib.remove_community_competitor(b, a)  # remove from the other direction
    assert lib.list_community_competitors(a) == []


def test_list_competitors_excludes_unapproved(lib):
    a = _add_community(lib, "Alpha Guild", "https://a.example", ["Finance"])
    pending = _add_community(lib, "Stealth Guild", "https://stealth.example", ["Finance"], approved=0)
    lib.add_community_competitor(a, pending)
    assert lib.list_community_competitors(a) == []


def test_suggest_ranks_by_overlap_and_excludes_self_and_existing(lib):
    target = _add_community(lib, "Vareto Circle", "https://vareto.example", ["Finance", "HR"])
    best = _add_community(lib, "Pigment Circle", "https://pigment.example", ["Finance", "HR"])
    ok = _add_community(lib, "Runway Circle", "https://runway.example", ["Finance"])
    already = _add_community(lib, "Datarails Circle", "https://datarails.example", ["Finance"])
    _add_community(lib, "Marketing Guild", "https://marketing.example", ["Marketing"])
    lib.add_community_competitor(target, already)

    suggestions = lib.suggest_community_competitors(target)
    names = [s["name"] for s in suggestions]
    assert names[0] == "Pigment Circle"          # 2 shared tags, ranked first
    assert "Runway Circle" in names              # 1 shared tag
    assert "Datarails Circle" not in names       # already curated, excluded
    assert "Marketing Guild" not in names        # no overlap at all


def test_suggest_empty_when_community_has_no_categories(lib):
    a = _add_community(lib, "Mystery Guild", "https://mystery.example", [])
    _add_community(lib, "Runway Circle", "https://runway.example", ["Finance"])
    assert lib.suggest_community_competitors(a) == []


def test_delete_community_cleans_up_competitors(lib):
    a = _add_community(lib, "Alpha Guild", "https://a.example", ["Finance"])
    b = _add_community(lib, "Beta Guild", "https://b.example", ["Finance"])
    lib.add_community_competitor(a, b)
    lib.delete_community(a)
    assert lib.list_community_competitors(b) == []


# -- admin routes --------------------------------------------------------------

def test_admin_can_add_and_remove_competitor(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = _add_community(lib, "Alpha Guild", "https://a.example", ["Finance"])
    a_slug = lib.get_community(a)["slug"]
    b = _add_community(lib, "Beta Guild", "https://b.example", ["Finance"])
    lib.close()

    client = _client(env)
    _login(client)

    r = client.post(f"/admin/tools/communities/{a}/competitors/add", data={"competitor_id": str(b)},
                     follow_redirects=False)
    assert r.status_code == 303

    r = client.get(f"/tools/communities/{a_slug}/edit")
    assert "Beta Guild" in r.text

    r = client.post(f"/admin/tools/communities/{a}/competitors/{b}/remove", follow_redirects=False)
    assert r.status_code == 303
    r = client.get(f"/tools/communities/{a_slug}/edit")
    assert "No similar communities curated yet." in r.text


def test_admin_can_add_selected_competitors_in_bulk(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = _add_community(lib, "Alpha Guild", "https://a.example", ["Finance"])
    a_slug = lib.get_community(a)["slug"]
    b = _add_community(lib, "Beta Guild", "https://b.example", ["Finance"])
    c = _add_community(lib, "Gamma Guild", "https://g.example", ["Finance"])
    lib.close()

    client = _client(env)
    _login(client)

    r = client.post(f"/admin/tools/communities/{a}/competitors/add-selected",
                     data={"competitor_id": [str(b), str(c)], "ai_drafted_fields": "competitors"},
                     follow_redirects=False)
    assert r.status_code == 303

    lib = Library(os.environ["LINKLIB_DB"])
    names = {x["name"] for x in lib.list_community_competitors(a)}
    assert names == {"Beta Guild", "Gamma Guild"}
    reviews = lib.list_field_reviews("community", a)
    assert "competitors" in reviews
    lib.close()


def test_generate_matches_unavailable_without_api_key(env, monkeypatch):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = _add_community(lib, "Alpha Guild", "https://a.example", ["Finance"])
    _add_community(lib, "Beta Guild", "https://b.example", ["Finance"])
    lib.close()
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    client = _client(env)
    _login(client)
    r = client.post(f"/admin/tools/communities/{a}/competitors/generate-matches")
    assert r.status_code == 503
    assert r.json()["ok"] is False


def test_generate_matches_returns_matched_ids(env, monkeypatch):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = _add_community(lib, "Alpha Guild", "https://a.example", ["Finance"])
    b = _add_community(lib, "Beta Guild", "https://b.example", ["Finance"])
    lib.close()

    import linklib.enrich as enrich_mod
    from linklib.enrich import CompetitorMatchResult

    def fake_generate_competitor_matches(name, description, candidates, model=enrich_mod.DEFAULT_MODEL):
        return CompetitorMatchResult(competitor_ids=[c["id"] for c in candidates], model="fake", cost_usd=0.0)

    monkeypatch.setattr(enrich_mod, "generate_competitor_matches", fake_generate_competitor_matches)

    client = _client(env)
    _login(client)
    r = client.post(f"/admin/tools/communities/{a}/competitors/generate-matches")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "competitor_ids": [b]}


def test_profile_page_shows_similar_communities(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = _add_community(lib, "Alpha Guild", "https://a.example", ["Finance"])
    a_slug = lib.get_community(a)["slug"]
    b = _add_community(lib, "Beta Guild", "https://b.example", ["Finance"])
    lib.add_community_competitor(a, b)
    lib.close()

    client = _client(env)
    r = client.get(f"/tools/communities/{a_slug}")
    assert r.status_code == 200
    assert "Similar communities" in r.text
    assert "Beta Guild" in r.text


def test_profile_page_hides_similar_communities_when_empty(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = _add_community(lib, "Alpha Guild", "https://a.example", ["Finance"])
    a_slug = lib.get_community(a)["slug"]
    lib.close()

    client = _client(env)
    r = client.get(f"/tools/communities/{a_slug}")
    assert r.status_code == 200
    assert "Similar communities" not in r.text
