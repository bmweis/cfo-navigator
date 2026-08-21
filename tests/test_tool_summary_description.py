"""Description-length follow-up: `tools.description` grows into a full
~8-12 sentence profile-page write-up; `tools.summary` is a new short 2-3
sentence field for the directory card and client-side search. Covers the
schema migration/backfill, the DB write paths (add/update/quick-edit —
including the bulk-edit echo-through that must never blank summary), the
two-part `generate_tool_description` LLM draft, and the admin UI wiring.
"""
import os
import pathlib
import sys
import tempfile
import types

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import enrich
from linklib.db import Library


# -- schema / backfill -----------------------------------------------------------

def test_new_tool_has_empty_summary_by_default(tmp_path):
    lib = Library(str(tmp_path / "t.db"))
    tool_id = lib.add_tool("Runway", "FP&A platform", "https://runway.com", [], approved=1)
    tool = lib.get_tool(tool_id)
    assert tool["summary"] == ""
    lib.close()


def test_add_tool_accepts_summary(tmp_path):
    lib = Library(str(tmp_path / "t.db"))
    tool_id = lib.add_tool("Runway", "Long description here.", "https://runway.com", [],
                            approved=1, summary="Short summary.")
    tool = lib.get_tool(tool_id)
    assert tool["summary"] == "Short summary."
    lib.close()


def test_migration_backfills_summary_from_description(tmp_path):
    db_path = str(tmp_path / "t.db")
    lib = Library(db_path)
    # A row added without a summary (the pre-migration shape — description only).
    tool_id = lib.add_tool("Runway", "Old short description.", "https://runway.com", [], approved=1)
    assert lib.get_tool(tool_id)["summary"] == ""
    lib.close()

    # Reopening the DB re-runs the migration/backfill step.
    lib2 = Library(db_path)
    tool = lib2.get_tool(tool_id)
    assert tool["summary"] == "Old short description."
    lib2.close()


def test_backfill_never_overwrites_a_real_summary(tmp_path):
    db_path = str(tmp_path / "t.db")
    lib = Library(db_path)
    tool_id = lib.add_tool("Runway", "Long description.", "https://runway.com", [],
                            approved=1, summary="Real drafted summary.")
    lib.close()

    lib2 = Library(db_path)  # re-run migration/backfill
    assert lib2.get_tool(tool_id)["summary"] == "Real drafted summary."
    lib2.close()


# -- DB write paths ---------------------------------------------------------------

def test_update_tool_writes_summary(tmp_path):
    lib = Library(str(tmp_path / "t.db"))
    tool_id = lib.add_tool("Runway", "desc", "https://runway.com", [], approved=1, summary="old summary")
    lib.update_tool(tool_id, "Runway", "new long description", "https://runway.com", [],
                    summary="new short summary")
    tool = lib.get_tool(tool_id)
    assert tool["description"] == "new long description"
    assert tool["summary"] == "new short summary"
    lib.close()


def test_quick_update_tool_writes_summary(tmp_path):
    lib = Library(str(tmp_path / "t.db"))
    tool_id = lib.add_tool("Runway", "desc", "https://runway.com", [], approved=1, summary="old summary")
    lib.quick_update_tool(tool_id, "new desc", 0, "", "", summary="new summary")
    tool = lib.get_tool(tool_id)
    assert tool["description"] == "new desc"
    assert tool["summary"] == "new summary"
    lib.close()


# -- generate_tool_description (mocked Claude call) --------------------------------

def _mock_anthropic(monkeypatch, payload_json):
    def _create(**kw):
        class _Block:
            type = "text"
            text = payload_json
        usage = types.SimpleNamespace(
            input_tokens=300, output_tokens=200,
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
        )
        return types.SimpleNamespace(content=[_Block()], usage=usage)
    fake = types.SimpleNamespace(Anthropic=lambda *a, **k: types.SimpleNamespace(
        messages=types.SimpleNamespace(create=lambda **kw: _create(**kw))))
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")


def _mock_fetch_page(monkeypatch, content=""):
    from linklib import extract
    monkeypatch.setattr(extract, "fetch_page", lambda url, **kw: types.SimpleNamespace(content=content))


def test_generate_tool_description_parses_both_fields(monkeypatch):
    _mock_fetch_page(monkeypatch, "Runway is an FP&A platform for finance teams.")
    _mock_anthropic(monkeypatch,
        '{"description": "Runway is a financial planning platform built for finance teams at '
        'growth-stage companies. It centralizes headcount, revenue, and expense planning '
        'into a single collaborative model.", '
        '"summary": "Runway is an FP&A platform for growth-stage finance teams. It centralizes '
        'headcount and revenue planning into one collaborative model."}')
    draft = enrich.generate_tool_description("Runway", "https://runway.com")
    assert draft is not None
    assert "financial planning platform" in draft.description
    assert "FP&A platform" in draft.summary
    assert draft.low_confidence is False
    assert draft.cost_usd > 0


def test_generate_tool_description_low_confidence_when_no_page_content(monkeypatch):
    _mock_fetch_page(monkeypatch, "")
    _mock_anthropic(monkeypatch, '{"description": "A finance tool.", "summary": "A finance tool."}')
    draft = enrich.generate_tool_description("Runway", "https://runway.com")
    assert draft is not None
    assert draft.low_confidence is True


def test_generate_tool_description_returns_none_without_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    draft = enrich.generate_tool_description("Runway", "https://runway.com")
    assert draft is None


# -- admin routes -------------------------------------------------------------------

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


def test_admin_add_tool_requires_summary(env):
    client = _client(env)
    _login(client)
    r = client.post("/admin/tools/software/new", data={
        "name": "Runway", "url": "https://runway.com", "description": "Long description.",
    })
    assert r.status_code == 400
    assert "summary" in r.json()["detail"].lower()


def test_admin_add_tool_saves_summary(env):
    client = _client(env)
    _login(client)
    r = client.post("/admin/tools/software/new", data={
        "name": "Runway", "url": "https://runway.com", "description": "Long description.",
        "summary": "Short summary.",
    }, follow_redirects=False)
    assert r.status_code == 303

    lib = Library(os.environ["LINKLIB_DB"])
    tool = lib.list_tools(approved_only=True)[0]
    assert tool["summary"] == "Short summary."
    lib.close()


def test_public_submission_uses_description_as_initial_summary(env):
    client = _client(env)
    _login(client)  # local dev: no password gate distinction, but _is_member covers it
    r = client.post("/tools/submit", data={
        "name": "Runway", "url": "https://runway.com", "description": "A short pitch.",
        "submitted_by": "brian@example.com",
    }, follow_redirects=False)
    assert r.status_code == 303

    lib = Library(os.environ["LINKLIB_DB"])
    tool = lib.list_tools(approved_only=False)[0]
    assert tool["summary"] == "A short pitch."
    lib.close()


def test_bulk_edit_does_not_blank_summary(env):
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "Long description.", "https://runway.com", [],
                           approved=1, summary="Real summary text.")
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post("/admin/tools/software/bulk-edit", json={
        "ids": [tool_id], "field": "advisor", "value": "1",
    })
    assert r.status_code == 200

    lib = Library(os.environ["LINKLIB_DB"])
    tool = lib.get_tool(tool_id)
    assert tool["advisor"] == 1
    assert tool["summary"] == "Real summary text."   # not wiped by the bulk toggle
    lib.close()


def test_quick_edit_route_saves_summary(env):
    lib = Library(os.environ["LINKLIB_DB"])
    tool_id = lib.add_tool("Runway", "Long description.", "https://runway.com", [],
                           approved=1, summary="Old summary.")
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post(f"/admin/tools/software/{tool_id}/quick-edit", json={
        "description": "Updated long description.", "summary": "Updated summary.",
    })
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True
    assert d["tool"]["summary"] == "Updated summary."

    lib = Library(os.environ["LINKLIB_DB"])
    assert lib.get_tool(tool_id)["summary"] == "Updated summary."
    lib.close()


def test_generate_description_route_returns_summary(env, monkeypatch):
    _mock_fetch_page(monkeypatch, "Runway is an FP&A platform.")
    _mock_anthropic(monkeypatch, '{"description": "A long description.", "summary": "A short summary."}')
    client = _client(env)
    _login(client)
    r = client.post("/admin/tools/software/generate-description", json={
        "name": "Runway", "url": "https://runway.com",
    })
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True
    assert d["description"] == "A long description."
    assert d["summary"] == "A short summary."


def test_directory_page_serializes_summary(env):
    lib = Library(os.environ["LINKLIB_DB"])
    lib.add_tool("Runway", "Long description.", "https://runway.com", [],
                approved=1, summary="Short card summary.")
    lib.close()

    client = _client(env)
    _login(client)
    r = client.get("/tools/software")
    assert r.status_code == 200
    assert "Short card summary." in r.text
