"""A Software vendor Name that is a web address is refused at save (issue #698).

Refused: contains "://", or starts with "www." or "http" (case-insensitive).
Allowed: a bare domain-style name such as "cfo.ai". The check runs in the save
route before any write, so before a slug is derived (the name slug of
"https://cfo.ai" would be "httpscfoai", the fallback when the URL field has no
parseable host).
"""
import importlib
import os
import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Library, _slugify
from linklib.voice_review import mechanical_findings, typography_findings_plain

MSG = "This looks like a web address. Enter the vendor's name."


@pytest.fixture
def app_module(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    seed = Library(db)
    seed.seed_voice_prompts()
    seed.close()
    import webapp.app as appmod
    importlib.reload(appmod)
    yield appmod
    for ext in ("", "-shm", "-wal"):
        if os.path.exists(db + ext):
            os.remove(db + ext)


@pytest.fixture
def admin(app_module):
    from fastapi.testclient import TestClient
    c = TestClient(app_module.app, raise_server_exceptions=True)
    r = c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    assert r.status_code in (302, 303)
    return c


def _names():
    lib = Library(os.environ["LINKLIB_DB"])
    try:
        return [t["name"] for t in lib.list_tools()] if hasattr(lib, "list_tools") else []
    finally:
        lib.close()


def _count():
    import sqlite3
    c = sqlite3.connect(os.environ["LINKLIB_DB"])
    try:
        return c.execute("SELECT COUNT(*) FROM tools").fetchone()[0]
    finally:
        c.close()


def _new(admin, name):
    return admin.post("/admin/tools/software/new", data=dict(
        name=name, url="https://acme.example", description="d", summary="s", primary_category="FP&A"),
        follow_redirects=False)


def _tool():
    lib = Library(os.environ["LINKLIB_DB"])
    tid = lib.add_tool("Acme", "orig", "https://acme.example", [], approved=1, summary="orig s")
    row = lib.get_tool(tid)
    lib.close()
    return tid, row["slug"]


def _edit(admin, slug, name):
    return admin.post(f"/tools/software/{slug}/edit", data=dict(
        name=name, url="https://acme.example", description="NEW", summary="NEW S", primary_category="FP&A"),
        follow_redirects=False)


URLS = ["https://cfo.ai", "http://cfo.ai", "www.cfo.ai", "WWW.CFO.AI", "  https://cfo.ai  ",
        "Visit https://cfo.ai", "http://x.com", "https:cfo.ai", "HTTPS:cfo.ai"]


@pytest.mark.parametrize("name", URLS)
def test_new_form_refuses_a_web_address(admin, name):
    before = _count()
    r = _new(admin, name)
    assert r.status_code == 400
    assert MSG in r.text
    assert "Nothing was saved" in r.text
    assert _count() == before


@pytest.mark.parametrize("name", URLS)
def test_edit_form_refuses_a_web_address(admin, name):
    tid, slug = _tool()
    r = _edit(admin, slug, name)
    assert r.status_code == 400
    assert MSG in r.text
    lib = Library(os.environ["LINKLIB_DB"])
    row = lib.get_tool(tid)
    lib.close()
    assert row["name"] == "Acme" and row["description"] == "orig"


def test_refusal_keeps_what_was_typed(admin):
    r = _new(admin, "https://cfo.ai")
    assert 'value="https://cfo.ai"' in r.text


@pytest.mark.parametrize("name", ["cfo.ai", "HTTPie", "Httpbin"])
def test_real_names_save_on_new(admin, name):
    r = _new(admin, name)
    assert r.status_code in (302, 303)
    assert _count() == 1


@pytest.mark.parametrize("name", ["cfo.ai", "HTTPie"])
def test_real_names_save_on_edit(admin, name):
    tid, slug = _tool()
    r = _edit(admin, slug, name)
    assert r.status_code in (302, 303)
    lib = Library(os.environ["LINKLIB_DB"])
    assert lib.get_tool(tid)["name"] == name
    lib.close()


def test_check_runs_before_any_slug_is_made(app_module):
    """The name slug a URL would have produced, and that the refusal happens
    in the route helper, ahead of add_tool/update_tool."""
    assert _slugify("https://cfo.ai") == "httpscfoai"
    assert app_module._tool_name_refusals({"name": "https://cfo.ai"}) == [("Name", MSG)]
    assert app_module._tool_name_refusals({"name": "cfo.ai"}) == []
    assert app_module._tool_name_refusals({"name": "HTTPie"}) == []


def test_message_passes_the_voice_lint():
    assert not mechanical_findings(MSG)
    assert not typography_findings_plain(MSG)
    assert " — " not in MSG
