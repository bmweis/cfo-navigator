"""Regression coverage for webapp.app._seed_toolbox — the
@app.on_event("startup") hook that seeds/re-syncs the CFO Toolbox
(tools/communities/benchmarks + their category vocabularies) on every
process boot. Three separate bugs found in this hook over time, all
covered here rather than split across files since they're all "boot the
app for real and check what _seed_toolbox actually did" tests:

1. URL-lookup crash (first two tests below): _seed_toolbox looked up
   existing tools/communities by exact `WHERE url = ?` instead of
   normalize_url(), unlike scripts/seed_tools.py and
   scripts/seed_communities.py (PR #197), which already made that fix.
   Once normalize_url()'s root-slash bug was fixed (making it correctly
   recognize more variants as duplicates), any existing row whose stored
   URL differed from its seed-list entry by exactly a trailing
   slash/www/http variant caused this exact-match lookup to miss it, fall
   through to add_tool/add_community, and crash the whole app on startup
   via the (correct) DuplicateURLError — a real outage, not a
   hypothetical. These two tests seed a tools/communities row with a URL
   variant of a real seed entry, then boot the app through its actual
   startup event (TestClient's lifespan) and confirm it comes up healthy
   instead of crash-looping.

2. Deleted rows reappearing after a deploy (third test): _seed_toolbox
   used to also INSERT a row for any seed entry missing from the DB,
   which meant a manually deleted tool/community/benchmark — hard-deleted,
   no soft-delete column — silently came back on the very next restart,
   since "missing by URL" was indistinguishable from "never seeded."
   _seed_toolbox now only ever syncs name/description/advisor on a
   *matching* row and never inserts; first-time seeding of a brand-new DB
   is scripts/seed_tools.py's/scripts/seed_communities.py's job, run once
   by hand, not something the startup hook duplicates.

3. Deleted category reappearing after a deploy (fourth test): the same
   bug class one level up, on the community_categories vocabulary table
   rather than a tools/communities row. The community-category seed loop
   had no empty-table guard, unlike the tools-category loop right next to
   it, so it unconditionally re-added any seed-list category missing from
   the DB on every restart — silently undoing a deliberate admin deletion
   at /admin/tools/communities. Both category loops are now gated the same
   way: seed once, on a genuinely empty table, never again.
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


def test_seed_toolbox_no_longer_inserts_on_a_fresh_db(app_client):
    """_seed_toolbox must never INSERT a tools/communities/benchmarks row,
    even against a brand-new (empty) DB — only sync fields on a row that
    already matches by URL. Inserting on "missing" was exactly the bug that
    let a manually deleted tool/community reappear on the next restart,
    since a hard-deleted row and a never-seeded one look identical to a
    URL lookup. First-time seeding is scripts/seed_tools.py's /
    scripts/seed_communities.py's job now, run once by hand."""
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    from linklib.db import Library
    with TestClient(appmod.app):
        pass

    lib = Library(app_client)
    tools = lib.list_tools(approved_only=False)
    communities = lib.list_communities(approved_only=False)
    benchmarks = lib.list_benchmarks()
    lib.close()
    assert tools == []
    assert communities == []
    assert benchmarks == []


def test_seed_toolbox_does_not_reinstate_a_deleted_community_category(app_client):
    """Phase L follow-up: the community-category seed loop used to have no
    empty-table guard (unlike the tools-category loop right above it), so it
    unconditionally re-added any seed-list category missing from the DB on
    every startup — silently undoing a deliberate admin deletion at
    /admin/tools/communities on the very next restart/deploy. Seed once
    (matching the tools-category loop's existing, already-correct pattern:
    only seed when the whole table is empty), then confirm a deleted category
    stays deleted across a second startup pass."""
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    from linklib.db import Library
    from scripts.seed_communities import CATEGORIES

    # First boot: seeds the community_categories vocabulary from empty.
    with TestClient(appmod.app):
        pass

    seeded_name = CATEGORIES[0][0]
    lib = Library(app_client)
    cats = {c["name"]: c["id"] for c in lib.list_community_categories()}
    assert seeded_name in cats  # sanity: first boot did seed it

    # Simulate the admin deliberately deleting it at /admin/tools/communities.
    lib.delete_community_category(cats[seeded_name])
    assert seeded_name not in {c["name"] for c in lib.list_community_categories()}
    lib.close()

    # Second boot (deploy/restart): must NOT reinstate the deleted category.
    importlib.reload(appmod)
    with TestClient(appmod.app):
        pass

    lib = Library(app_client)
    remaining = {c["name"] for c in lib.list_community_categories()}
    lib.close()
    assert seeded_name not in remaining


def test_seed_toolbox_never_reverts_a_regenerated_description(app_client):
    """2026-08 incident regression: _seed_toolbox used to sync BOTH name
    and description on a matching tools row, so any deploy after a tool's
    description was AI-regenerated (making it differ from
    scripts/seed_tools.py's short seed blurb) silently reverted it back —
    81 of 148 seed-listed tools were caught with reverted descriptions
    from a single night's deploys. description sync must be gone
    entirely: a real, regenerated description survives a startup pass
    unchanged, byte for byte."""
    from linklib.db import Library
    from scripts.seed_tools import TOOLS

    seed_entry = TOOLS[0]
    real_description = (
        "A genuinely different, much longer AI-regenerated description that "
        "in no way matches the short seed-list blurb this tool started with."
    )
    lib = Library(app_client)
    tool_id = lib.add_tool(seed_entry["name"], real_description, seed_entry["url"],
                            seed_entry["categories"], approved=1)
    lib.close()

    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    with TestClient(appmod.app):
        pass

    lib = Library(app_client)
    row = lib.get_tool(tool_id)
    lib.close()
    assert row["description"] == real_description
    assert row["description"] != seed_entry["description"]


def test_seed_toolbox_still_syncs_name(app_client):
    """name divergence is still detected on every boot, but — per the
    2026-09 seed-sync-overwrite fix (CLAUDE.md's Part 1 writeup) — it no
    longer silently overwrites the live value the way it used to. The live
    name stays exactly what an admin set it to; the divergence is queued
    as a 'seed-disagreement' review item (via
    Library.add_seed_disagreement_item, routed through a real Library
    method, not a raw unlogged UPDATE) for an explicit human resolution
    instead. Brian still occasionally renames a seed-listed tool by
    editing scripts/seed_tools.py directly (e.g. an "(acquired by ...)"
    suffix) — that divergence now surfaces at /admin/voice/review-queue
    rather than applying itself."""
    from linklib.db import Library
    from scripts.seed_tools import TOOLS

    seed_entry = TOOLS[0]
    lib = Library(app_client)
    tool_id = lib.add_tool("Old Name Before A Rename", seed_entry["description"],
                            seed_entry["url"], seed_entry["categories"], approved=1)
    lib.close()

    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    with TestClient(appmod.app):
        pass

    lib = Library(app_client)
    row = lib.get_tool(tool_id)
    items = lib.list_voice_review_queue()
    lib.close()

    # The live name is untouched — no silent overwrite.
    assert row["name"] == "Old Name Before A Rename"
    assert row["name"] != seed_entry["name"]

    # The divergence is queued instead, with the seed's own proposed name.
    hits = [i for i in items if i["table_name"] == "tools"
            and i["row_id"] == str(tool_id) and i["column_name"] == "name"
            and i["rule"] == "seed-disagreement"]
    assert hits, "expected a queued seed-disagreement item for the diverged tool name"
    assert hits[0]["before_text"] == "Old Name Before A Rename"
    assert hits[0]["after_text"] == seed_entry["name"]
    assert hits[0]["source"] == "startup-sync"
