"""Phase 3 of the Exa integration: a small "Web search powered by Exa" caption
on /library/ask's client-side citation list (srcListHtml in webapp/app.py).
Phase 7 tightened the gating condition: a web-type citation alone is no
longer enough (the native web_search_20250305 fallback also produces
type "web" citations when Exa is off) — it must also carry provider "exa".
Server-rendered surfaces (/ask/history, /library/past-questions,
/admin/ask-feedback) are untouched — this is presentation-only on the
live-rendering page.
"""
import pathlib
import sys
import tempfile
import os

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

CAPTION = "Web search powered by Exa"


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


def _member_client(appmod):
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app, raise_server_exceptions=True)
    c.post("/login", data={"username": "member1", "password": "supersecret"}, follow_redirects=False)
    return c


def _admin_client(appmod):
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app, raise_server_exceptions=True)
    c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    return c


def test_library_ask_ships_the_conditional_exa_caption(env):
    """The live-rendering page's JS carries the caption logic, gated on the
    turn's citations including a web-type entry produced by Exa specifically
    (provider === 'exa') — not shown for a Library/Feed-only turn, and not
    shown when the native web_search_20250305 fallback handled the web tier
    instead (Phase 7)."""
    c = _member_client(env)
    resp = c.get("/library/ask")
    assert resp.status_code == 200
    body = resp.text
    assert CAPTION in body
    assert "c.type === 'web' && c.provider === 'exa'" in body
    assert "ask-src-caption" in body


def test_ask_history_has_no_exa_caption(env):
    c = _member_client(env)
    resp = c.get("/ask/history")
    assert resp.status_code == 200
    assert CAPTION not in resp.text


def test_past_questions_has_no_exa_caption(env):
    c = _member_client(env)
    resp = c.get("/library/past-questions")
    assert resp.status_code == 200
    assert CAPTION not in resp.text


def test_admin_ask_feedback_has_no_exa_caption(env):
    c = _admin_client(env)
    resp = c.get("/admin/ask-feedback")
    assert resp.status_code == 200
    assert CAPTION not in resp.text
