"""Homepage Communities gap-collection teaser (Phase 8): a bolded, member-gated
line under the CFO Toolbox card pointing at the Phase 5 gap-collection CTA,
same pattern as the existing "Suggest a piece for the archive" line.
"""
import pathlib
import sys
import tempfile, os

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
    from linklib.db import Library
    lib = Library(db)
    lib.create_user("member1", "supersecret", role="user")
    lib.close()
    yield appmod
    if os.path.exists(db):
        os.remove(db)


def _client(appmod):
    from fastapi.testclient import TestClient
    return TestClient(appmod.app, raise_server_exceptions=True)


def _member_client(appmod):
    c = _client(appmod)
    c.post("/login", data={"username": "member1", "password": "supersecret"}, follow_redirects=False)
    return c


def test_teaser_hidden_from_anonymous_visitors(env):
    c = _client(env)
    r = c.get("/")
    assert r.status_code == 200
    assert "Think finance communities could be better?" not in r.text


def test_teaser_shown_to_members_and_links_to_gap_cta(env):
    c = _member_client(env)
    r = c.get("/")
    assert r.status_code == 200
    assert "Think finance communities could be better?" in r.text
    assert "Tell us where they fall short" in r.text
    assert '<a href="/tools/communities/gap">' in r.text
    # Bolded (more visual weight than the plain "Suggest a piece" line).
    assert '<strong style="color:var(--ink);">Think finance communities could be better?</strong>' in r.text


def test_suggest_a_piece_line_still_renders_alongside_teaser(env):
    c = _member_client(env)
    r = c.get("/")
    assert "Suggest a piece for the archive" in r.text
    assert "Think finance communities could be better?" in r.text
