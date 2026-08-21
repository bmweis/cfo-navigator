"""Public "Key features" card on the Software profile page (Feature Taxonomy
Phase 1b PR 2). Always renders — a coming-soon state for a tool with no
tool_feature_links, real grouped feature names (sentence case, with add-on/AI
indicators) once it has links.
"""
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


def test_coming_soon_state_when_no_feature_links(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    lib.add_tool("Ramp", "Spend", "https://ramp.com", ["Spend"], approved=1, summary="s")
    lib.close()

    r = _client(env).get("/tools/software/ramp")
    assert r.status_code == 200
    assert "Key features" in r.text
    assert "Coming soon" in r.text
    assert "curated feature taxonomy" in r.text


def test_renders_feature_names_sentence_case_single_category(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    cat_id = lib.add_tool_category("ERP")
    tool_id = lib.add_tool("Rillet", "d", "https://rillet.com", ["ERP"], approved=1, summary="s")
    fid = lib.add_category_feature(cat_id, "Real-Time Ledger")
    lib.upsert_tool_feature_link(tool_id, fid, "native", 0, "2026-08-19")
    lib.close()

    r = _client(env).get("/tools/software/rillet")
    assert r.status_code == 200
    assert "Key features" in r.text
    assert "Real-time ledger" in r.text   # sentence case, not Title Case
    assert "Coming soon" not in r.text


def test_add_on_and_ai_indicators_rendered(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    cat_id = lib.add_tool_category("ERP")
    tool_id = lib.add_tool("Rillet", "d", "https://rillet.com", ["ERP"], approved=1, summary="s")
    fid = lib.add_category_feature(cat_id, "AI Bill Capture")
    lib.upsert_tool_feature_link(tool_id, fid, "add_on", 1, "2026-08-19")
    lib.close()

    r = _client(env).get("/tools/software/rillet")
    assert 'tp-feature-tag-addon' in r.text
    assert 'tp-feature-tag-ai' in r.text
    assert "Add-on" in r.text
    assert ">AI<" in r.text


def test_grouped_by_category_when_links_span_multiple_categories(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    erp = lib.add_tool_category("ERP")
    close = lib.add_tool_category("Close Management")
    tool_id = lib.add_tool("Numeric", "d", "https://numeric.io", ["ERP", "Close Management"],
                           approved=1, summary="s")
    f1 = lib.add_category_feature(erp, "Real-Time Ledger")
    f2 = lib.add_category_feature(close, "Reconciliation Automation")
    lib.upsert_tool_feature_link(tool_id, f1, "native", 0, "2026-08-19")
    lib.upsert_tool_feature_link(tool_id, f2, "native", 0, "2026-08-19")
    lib.close()

    r = _client(env).get("/tools/software/numeric")
    assert "tp-feature-group-h" in r.text
    assert "ERP" in r.text
    assert "Close Management" in r.text


def test_retired_feature_link_dropped_from_public_rendering(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    cat_id = lib.add_tool_category("ERP")
    tool_id = lib.add_tool("Rillet", "d", "https://rillet.com", ["ERP"], approved=1, summary="s")
    fid = lib.add_category_feature(cat_id, "Real-Time Ledger")
    lib.upsert_tool_feature_link(tool_id, fid, "native", 0, "2026-08-19")
    lib.retire_category_feature(fid)
    lib.close()

    r = _client(env).get("/tools/software/rillet")
    assert "Coming soon" in r.text
    assert "Real-time ledger" not in r.text
