"""Community gap-collection teaser and "Suggest a piece" prompt (Phase 8,
relocated in the homepage hero restructure): both are bolded/plain,
member-gated lines that used to sit at the bottom of the homepage and now
live on the pages they actually point at — Communities (gap CTA) and Library
(Archive submission).
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


def test_homepage_no_longer_shows_either_prompt_anonymous(env):
    c = _client(env)
    r = c.get("/")
    assert r.status_code == 200
    assert "Think finance communities could be better?" not in r.text
    assert "Suggest a piece for the archive" not in r.text


def test_homepage_no_longer_shows_either_prompt_member(env):
    c = _member_client(env)
    r = c.get("/")
    assert r.status_code == 200
    assert "Think finance communities could be better?" not in r.text
    assert "Suggest a piece for the archive" not in r.text


def test_top_of_page_teaser_removed_as_duplicate_of_bottom_cta(env):
    # The bolded top-of-page teaser duplicated the always-visible bottom CTA
    # card, so it was removed; only the quiz link remains up top.
    for client in (_client(env), _member_client(env)):
        r = client.get("/tools/communities")
        assert r.status_code == 200
        assert '<strong style="color:var(--ink);">Think finance communities could be better?</strong>' not in r.text
        assert "Not sure which community's for you? " in r.text
        assert '<a href="/tools/communities/find"' in r.text


def test_top_of_page_links_shown_on_communities_page(env):
    c = _client(env)
    r = c.get("/tools/communities")
    assert r.status_code == 200
    assert "Think finance communities could be better?" not in r.text
    assert "Don't see the right fit? " in r.text
    assert '<a id="comm-gap-link" href="/tools/communities/gap"' in r.text


def test_bottom_submit_link_auth_aware_on_communities_page(env):
    c = _client(env)
    r = c.get("/tools/communities")
    assert r.status_code == 200
    assert "Know a community that belongs here?" in r.text
    assert "Sign in to submit" in r.text
    assert "Suggest a community" not in r.text

    m = _member_client(env)
    r = m.get("/tools/communities")
    assert r.status_code == 200
    assert 'href="/tools/communities/submit"' in r.text
    assert "Submit it for review" in r.text


def test_suggest_a_piece_hidden_from_anonymous_visitors_on_library_page(env):
    c = _client(env)
    r = c.get("/library", follow_redirects=False)
    assert r.status_code in (302, 303)  # /library redirects signed-out visitors to /login


def test_suggest_a_piece_shown_to_members_on_library_page(env):
    c = _member_client(env)
    r = c.get("/library")
    assert r.status_code == 200
    assert "Suggest a piece for the archive" in r.text
    assert '<a href="/library/submit">' in r.text
