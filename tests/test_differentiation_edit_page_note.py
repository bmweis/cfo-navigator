"""Fast-follow to the Differentiation content-exclusion fix (see CLAUDE.md /
tests/test_differentiation_content_exclusions.py): a small UI-only helper
note on the tool edit view, near the Competitive differentiation field,
telling the editor the field is generated from the Description and
competitor list already on the page — not independently researched or
citation-grounded — so a Description edit/regenerate needs a fresh
Differentiation "Generate summary" pass too, since it won't update on its
own. No prompt or generation logic touched; this only covers the rendered
edit page.
"""
import os
import tempfile

import pytest


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


def test_differentiation_field_has_source_note_on_edit_page(env):
    from linklib.db import Library
    lib_ = Library(os.environ["LINKLIB_DB"])
    lib_.add_tool("Runway", "A tool.", "https://runway.com", ["FP&A"], approved=1)
    lib_.close()

    client = _client(env)
    _login(client)
    r = client.get("/tools/software/runway/edit")
    assert r.status_code == 200

    body = r.text
    # The note must sit near the Competitive differentiation field, not
    # anywhere else on the page — assert it's between that field's textarea
    # opening tag and the next field section.
    diff_block_start = body.index('id="gen-host-tool-differentiation"')
    diff_block_end = body.index('<div style="margin-top:32px;padding-top:24px;border-top:1px solid var(--line);">\n  <h2 style="font-size:16px;font-weight:600;margin:0 0 16px;">Screenshots')
    diff_block = body[diff_block_start:diff_block_end]

    assert "Description and competitor list" in diff_block
    assert "won't update on its own" in diff_block
