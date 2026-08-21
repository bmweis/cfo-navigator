"""/bookmarklet — 2026-08 wrap-up sprint item 2.

Covers:
- The `javascript:` snippet /bookmarklet renders is syntactically valid JS
  (node --check against the real resolved response text, not a guess at the
  source) — the prior bug was an unbalanced closing brace
  (`body:JSON.stringify({url:u,tags:t})}}` closed the fetch-options object
  TWICE before `.then` ever ran), which made every copy of the snippet a
  silent no-op: a syntax error, never thrown anywhere visible.
- A structural brace/paren-balance assertion as a belt-and-suspenders check
  that doesn't depend on Node being on PATH (mirrors the standing
  "skip, don't fail, when Node is unavailable" convention from
  tests/test_admin_js_syntax.py).
- The fetch chain now has a `.catch`, so a network/CORS failure alerts
  visibly instead of silently doing nothing.
- `/save` answers a real CORS preflight (OPTIONS) and sends
  Access-Control-Allow-Origin on the actual POST response — confirmed
  broken in production (a bolster.com-hosted bookmarklet run got
  "TypeError: Failed to fetch", the classic CORS-rejection signature) — and
  that no other route picks up a stray CORS header from the same
  middleware.
"""
import os
import shutil
import tempfile

import pytest

os.environ.setdefault("LINKLIB_DB", tempfile.mktemp(suffix=".db"))

from webapp import checks

pytestmark_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not on PATH in this environment"
)


@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    monkeypatch.setenv("LINKLIB_SAVE_TOKEN", "tok123")
    import importlib
    import webapp.app as appmod
    importlib.reload(appmod)
    yield appmod
    if os.path.exists(db):
        os.remove(db)


def _admin_client(appmod):
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app, raise_server_exceptions=True)
    c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    return c


def _bookmarklet_js(appmod) -> str:
    c = _admin_client(appmod)
    r = c.get("/bookmarklet")
    assert r.status_code == 200
    assert r.text.startswith("javascript:")
    return r.text[len("javascript:"):]


def _assert_balanced(text: str) -> None:
    """Structural check, independent of Node — the exact class of bug the
    old snippet had (one extra closing brace) is a brace-count mismatch."""
    assert text.count("{") == text.count("}"), \
        f"unbalanced braces: {text.count('{')} open vs {text.count('}')} close"
    assert text.count("(") == text.count(")"), \
        f"unbalanced parens: {text.count('(')} open vs {text.count(')')} close"


def test_bookmarklet_js_is_brace_and_paren_balanced(env):
    _assert_balanced(_bookmarklet_js(env))


def test_bookmarklet_has_catch_on_the_fetch_chain(env):
    js = _bookmarklet_js(env)
    assert ".catch(" in js


def test_bookmarklet_fetch_options_object_closes_exactly_once(env):
    """Regression test for the specific bug: JSON.stringify(...) must be
    followed by exactly one closing brace (for the fetch options object)
    before .then, not two."""
    js = _bookmarklet_js(env)
    assert "body:JSON.stringify({url:u,tags:t})}).then(" in js
    assert "body:JSON.stringify({url:u,tags:t})}})" not in js


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH in this environment")
def test_bookmarklet_js_parses_as_valid_javascript_via_node(env):
    js = _bookmarklet_js(env)
    problem = checks._node_check(js)
    assert problem is None, problem


# ---------------------------------------------------------------------------
# /save CORS — preflight + actual response headers
# ---------------------------------------------------------------------------

def test_save_answers_options_preflight_with_cors_headers(env):
    from fastapi.testclient import TestClient
    c = TestClient(env.app, raise_server_exceptions=True)
    r = c.options("/save", headers={
        "Origin": "https://bolster.com",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    })
    assert r.status_code == 204
    assert r.headers.get("access-control-allow-origin") == "*"
    assert "POST" in r.headers.get("access-control-allow-methods", "")
    assert "content-type" in r.headers.get("access-control-allow-headers", "").lower()


def test_save_post_response_carries_cors_header(env):
    from fastapi.testclient import TestClient
    c = TestClient(env.app, raise_server_exceptions=True)
    r = c.post("/save?token=tok123", json={"url": "https://example.com/some-article"},
              headers={"Origin": "https://bolster.com"})
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "*"


def test_save_error_response_also_carries_cors_header(env):
    """A bad/missing token still needs the CORS header on its 401, or the
    browser reports a CORS failure instead of surfacing the real error."""
    from fastapi.testclient import TestClient
    c = TestClient(env.app, raise_server_exceptions=True)
    r = c.post("/save", json={"url": "https://example.com/x"},
              headers={"Origin": "https://bolster.com"})
    assert r.status_code == 401
    assert r.headers.get("access-control-allow-origin") == "*"


def test_other_routes_do_not_get_a_stray_cors_header(env):
    from fastapi.testclient import TestClient
    c = TestClient(env.app, raise_server_exceptions=True)
    r = c.get("/health")
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers.keys()}
