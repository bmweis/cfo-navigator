"""Legacy `-2` tool slug redirects (2026-09) — seven Software directory
entries (DealHub, LiveFlow, Puzzle, Zenskar, m3ter, Digits, Tropic) were
renamed off a spurious `-2` slug suffix via
scripts/rename_dash2_tool_slugs.py. `webapp.app._LEGACY_TOOL_SLUG_REDIRECTS`
301s every old `/tools/software/<old-slug>` URL to the new bare-name slug
rather than letting it 404 — see that dict's own comment for the full
root-cause writeup."""
import os
import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


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


def test_every_old_slug_has_a_redirect_target(env):
    """The redirect map's targets are exactly the seven renamed vendors —
    a drift guard so a future edit to one side isn't forgotten on the
    other (see the script's own RENAMES dict, which this must match)."""
    assert env._LEGACY_TOOL_SLUG_REDIRECTS == {
        "dealhub-2": "dealhub",
        "liveflow-2": "liveflow",
        "puzzle-2": "puzzle",
        "zenskar-2": "zenskar",
        "m3ter-2": "m3ter",
        "digits-2": "digits",
        "tropic-2": "tropic",
    }


def test_old_slug_301_redirects_to_new_slug(env):
    lib_mod = __import__("linklib.db", fromlist=["Library"])
    lib = lib_mod.Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Tropic", "spend mgmt", "https://tropicapp.example",
                            ["Procurement/Spend"], approved=1)
    lib.conn.execute("UPDATE tools SET slug='tropic' WHERE id=?", (tool_id,))
    lib.conn.commit()
    lib.close()

    c = _client(env)
    resp = c.get("/tools/software/tropic-2", follow_redirects=False)
    assert resp.status_code == 301
    assert resp.headers["location"] == "/tools/software/tropic"


def test_redirect_preserves_query_string(env):
    lib_mod = __import__("linklib.db", fromlist=["Library"])
    lib = lib_mod.Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Puzzle", "accounting", "https://puzzle.example",
                            ["ERP"], approved=1)
    lib.conn.execute("UPDATE tools SET slug='puzzle' WHERE id=?", (tool_id,))
    lib.conn.commit()
    lib.close()

    c = _client(env)
    resp = c.get("/tools/software/puzzle-2?suggested=1", follow_redirects=False)
    assert resp.status_code == 301
    assert resp.headers["location"] == "/tools/software/puzzle?suggested=1"


def test_new_slug_renders_the_real_profile_after_redirect(env):
    lib_mod = __import__("linklib.db", fromlist=["Library"])
    lib = lib_mod.Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("LiveFlow", "accounting agents", "https://liveflow.example",
                            ["ERP"], approved=1)
    lib.conn.execute("UPDATE tools SET slug='liveflow' WHERE id=?", (tool_id,))
    lib.conn.commit()
    lib.close()

    c = _client(env)
    resp = c.get("/tools/software/liveflow-2", follow_redirects=True)
    assert resp.status_code == 200
    assert "LiveFlow" in resp.text


def test_unrelated_slug_is_unaffected_by_the_redirect_map(env):
    """A slug that isn't one of the seven legacy entries goes through the
    normal lookup path (404 if unknown) — the redirect map must never
    intercept anything else."""
    c = _client(env)
    resp = c.get("/tools/software/some-unrelated-slug", follow_redirects=False)
    assert resp.status_code == 404
