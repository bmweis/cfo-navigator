"""Regression for a production crash: webapp.app._seed_toolbox (the
@app.on_event("startup") re-seed hook) looked up existing tools/communities
by exact `WHERE url = ?` instead of normalize_url(), unlike
scripts/seed_tools.py and scripts/seed_communities.py (PR #197), which
already made that fix. Once normalize_url()'s root-slash bug was fixed
(making it correctly recognize more variants as duplicates), any existing
row whose stored URL differed from its seed-list entry by exactly a
trailing slash/www/http variant caused this exact-match lookup to miss it,
fall through to add_tool/add_community, and crash the whole app on startup
via the (correct) DuplicateURLError — a real outage, not a hypothetical.

These tests seed a tools/communities row with a URL variant of a real seed
entry, then boot the app through its actual startup event (TestClient's
lifespan) and confirm it comes up healthy instead of crash-looping.
"""
import os
import tempfile

import pytest


@pytest.fixture
def app_client(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    yield db
    if os.path.exists(db):
        os.remove(db)
    for ext in ("-shm", "-wal"):
        p = db + ext
        if os.path.exists(p):
            os.remove(p)


def test_seed_toolbox_survives_a_tool_url_trailing_slash_variant(app_client):
    """Reproduces the "zenskar" production crash: a tool already in the DB
    with a trailing-slash URL variant of its seed-list entry must not crash
    startup."""
    from linklib.db import Library
    from scripts.seed_tools import TOOLS

    seed_entry = TOOLS[0]
    lib = Library(app_client)
    lib.add_tool(seed_entry["name"], seed_entry["description"],
                 seed_entry["url"] + "/",  # trailing-slash variant of the seed URL
                 seed_entry["categories"], approved=1)
    lib.close()

    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    with TestClient(appmod.app) as client:
        r = client.get("/health")
        assert r.status_code == 200


def test_seed_toolbox_survives_a_community_url_www_variant(app_client):
    from linklib.db import Library
    from scripts.seed_communities import COMMUNITIES

    seed_entry = COMMUNITIES[0]
    url = seed_entry["url"]
    variant = url.replace("https://", "https://www.", 1) if "://www." not in url else url + "/"
    lib = Library(app_client)
    lib.add_community(
        name=seed_entry["name"], url=variant, demographic=seed_entry["demographic"],
        cost_band=seed_entry["cost_band"], categories=seed_entry["categories"], approved=1,
    )
    lib.close()

    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    with TestClient(appmod.app) as client:
        r = client.get("/health")
        assert r.status_code == 200


def test_seed_toolbox_still_seeds_on_a_fresh_db(app_client):
    """Not just crash-safety — the normalized lookup must still add tools
    that are genuinely new (empty DB), same as before this fix."""
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    from linklib.db import Library
    with TestClient(appmod.app):
        pass

    lib = Library(app_client)
    tools = lib.list_tools(approved_only=False)
    lib.close()
    assert len(tools) > 0
