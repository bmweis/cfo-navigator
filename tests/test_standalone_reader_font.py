"""Regression coverage for retiring Source Serif 4 sitewide (2026-09
follow-up to the merged-reader typography saga in test_reader_expand_mode.py).

Brian's standing rule: only Outfit or DM Sans, ever, for content/reading
typography — no third font, no exceptions. The standalone single-article
reader at GET /read/{article_id} (_READER_TMPL/_READER_CSS, a separate,
older template from the merged Feed/Archive/Read Later reader at /read) was
the one remaining holdout still rendering body copy in Source Serif 4.
Fixed: _READER_CSS's body{} rule and its Google Fonts @import both moved to
DM Sans; .reader-meta h1 was already Outfit and is unchanged.
"""
import os
import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Library, Article


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


def _admin_client(appmod):
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app, raise_server_exceptions=True)
    c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    return c


def _seed_article(appmod):
    lib = Library(os.environ["LINKLIB_DB"])
    article_id = lib.upsert(Article(
        url="https://example.com/standalone-reader-font-test",
        title="Standalone Reader Font Test",
        content="Plain body text for checking the standalone reader's font.",
    ))
    lib.close()
    return article_id


def test_standalone_reader_has_no_serif_font_declaration(env):
    """_READER_CSS must not actually declare Source Serif 4 anywhere — not
    the Google Fonts @import, not a font-family value. (A historical-note
    comment mentioning the retired font by name is fine and expected — this
    checks the real CSS, not prose.)"""
    c = _admin_client(env)
    article_id = _seed_article(env)
    r = c.get(f"/read/{article_id}")
    assert r.status_code == 200
    html = r.text
    assert "Source+Serif" not in html
    assert "font-family:'Source Serif 4'" not in html
    assert "'Source Serif 4',Georgia,serif" not in html


def test_standalone_reader_body_uses_dm_sans(env):
    """The body{} rule (which .reader-body/.reader-empty inherit from, since
    neither sets its own font-family) must declare DM Sans, matching the
    sitewide default and BRAND.md's "Body | DM Sans | 16px/1.65" row."""
    c = _admin_client(env)
    article_id = _seed_article(env)
    html = c.get(f"/read/{article_id}").text
    body_rule_start = html.index("body{background:var(--bg)")
    body_rule_end = html.index("}", body_rule_start)
    body_rule = html[body_rule_start:body_rule_end + 1]
    assert "'DM Sans'" in body_rule


def test_standalone_reader_title_stays_outfit(env):
    """.reader-meta h1 was already Outfit before this fix and must stay
    that way — only the body font changed."""
    c = _admin_client(env)
    article_id = _seed_article(env)
    html = c.get(f"/read/{article_id}").text
    assert ".reader-meta h1{font-family:'Outfit'" in html


def test_brand_check_allowlist_no_longer_permits_source_serif_4():
    """linklib/brand_check.py's ALLOWED_FONTS must have dropped 'Source
    Serif 4' — otherwise a future reintroduction would silently pass
    test_brand_standards.py instead of being caught as a regression."""
    from linklib import brand_check
    assert "Source Serif 4" not in brand_check.ALLOWED_FONTS
    assert "Outfit" in brand_check.ALLOWED_FONTS
    assert "DM Sans" in brand_check.ALLOWED_FONTS
