"""Suggest-content link: /tools/fpa-buddy's client-side srcListHtml() (the
citation-list renderer for each live answer) always appends a small nudge
pointing at /library/submit — including, deliberately, when a turn comes back
with zero citations. See CLAUDE.md for the feature write-up.

This is JS shipped to the browser, not server-rendered per-turn HTML, so the
regression coverage here (like tests/test_ask_exa_caption.py's own pattern)
checks that the page ships the right JS logic rather than asserting on a
specific rendered turn.
"""
import pathlib
import sys
import tempfile
import os

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

SUGGEST_TEXT = "Know a source that should be here?"
SUGGEST_LINK = 'href="/library/submit"'


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


def test_fpa_buddy_page_ships_the_suggest_content_link(env):
    """The page's JS carries the suggest-content nudge and links to
    /library/submit."""
    c = _member_client(env)
    resp = c.get("/tools/fpa-buddy")
    assert resp.status_code == 200
    body = resp.text
    assert SUGGEST_TEXT in body
    assert SUGGEST_LINK in body


def test_suggest_link_renders_even_with_zero_citations(env):
    """srcListHtml() must not early-return an empty string on an empty
    citations list — that's the exact case (Buddy found nothing) the nudge
    is meant to catch. The JS source itself is asserted here since the
    citation list is rendered client-side, not server-rendered per turn."""
    c = _member_client(env)
    resp = c.get("/tools/fpa-buddy")
    body = resp.text
    start = body.index("function srcListHtml(d)")
    end = body.index("function fbRowHtml", start)
    fn_src = body[start:end]
    assert "if (!items.length) return suggestLine;" in fn_src
    assert "if (!items.length) return '';" not in fn_src


def test_suggest_link_coexists_with_exa_caption(env):
    """When both the Exa caption and the suggest line are present, both
    should render (order: citation pills, then Exa caption, then the
    suggest nudge) — not one clobbering the other."""
    c = _member_client(env)
    resp = c.get("/tools/fpa-buddy")
    body = resp.text
    exa_idx = body.index("Web search powered by Exa")
    suggest_idx = body.index(SUGGEST_TEXT)
    assert exa_idx < suggest_idx


def test_mocked_illustrative_example_does_not_carry_the_suggest_link(env):
    """The hardcoded mocked example near the top of the page (clearly labeled
    'not a captured real answer') is static markup, not rendered through
    srcListHtml() — confirm the suggest link doesn't leak into it."""
    c = _member_client(env)
    resp = c.get("/tools/fpa-buddy")
    body = resp.text
    example_start = body.index('class="ask-example"')
    example_end = body.index('class="ask-example-caption"')
    example_html = body[example_start:example_end]
    assert SUGGEST_TEXT not in example_html
    assert "ask-src-static" in example_html  # sanity: still the static fake sources


def test_library_submit_stale_comment_is_fixed(env):
    """The 'Public for now' comment (inaccurate — the route is member-gated)
    is gone from the source."""
    src = pathlib.Path("webapp/app.py").read_text()
    assert "Public for now; the handler is self-contained" not in src


def test_library_submit_still_member_gated_for_signed_out_visitor(env):
    from fastapi.testclient import TestClient
    c = TestClient(env.app, raise_server_exceptions=True)
    resp = c.get("/library/submit", follow_redirects=False)
    assert resp.status_code in (302, 303)
    assert "/login" in resp.headers.get("location", "")


def test_library_submit_reachable_for_signed_in_member(env):
    c = _member_client(env)
    resp = c.get("/library/submit")
    assert resp.status_code == 200
    assert "Suggest a piece for the archive" in resp.text
