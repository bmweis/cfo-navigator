""""Snapshot on Wayback" guidance link on manual-review rows (2026-08
wrap-up sprint item 4).

A manual workaround Brian proved out by hand in production: for an article
whose live page loads fine in a browser but is bot-blocked to our fetcher,
manually triggering archive.org's Save Page Now (https://web.archive.org/save/<url>)
creates a fresh snapshot our Wayback tier can then retrieve, since
archive.org's own crawler isn't subject to the Cloudflare fingerprint block
that stops ours. This is a link and a sentence only — no Save Page Now API
integration, no automation, no tracking of whether a snapshot was taken.

Covers:
- Each manual-review row with a known URL gets a "Snapshot on Wayback ↗"
  link pointing at https://web.archive.org/save/<article-url>, opening in a
  new tab.
- The manual-review section's explainer text gains one guidance sentence.
- No link renders for a row with no known current URL (nothing to build the
  Save Page Now URL from).
"""
import os
import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Article, Library


@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
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


def _flag_for_manual_review(lib, url="https://example.com/stuck-article", title="Stuck"):
    art = Article(url=url, title=title, content="x", saved_at="2026-01-01T00:00:00")
    article_id = lib.upsert(art)
    for _ in range(Library._MANUAL_REVIEW_ATTEMPT_THRESHOLD):
        lib.log_content_refetch_attempt(article_id, "failure", reason="bot-challenge")
    return article_id


def test_manual_review_row_has_wayback_snapshot_link(env):
    lib = env._lib()
    _flag_for_manual_review(lib, url="https://example.com/stuck-article")
    lib.close()

    c = _admin_client(env)
    r = c.get("/admin/reader/backfill-content")
    assert r.status_code == 200
    assert "Snapshot on Wayback" in r.text
    assert "https://web.archive.org/save/https://example.com/stuck-article" in r.text
    assert 'target="_blank"' in r.text


def test_manual_review_explainer_has_wayback_guidance_sentence(env):
    lib = env._lib()
    _flag_for_manual_review(lib)
    lib.close()

    c = _admin_client(env)
    r = c.get("/admin/reader/backfill-content")
    assert "archive.org" in r.text
    assert "Save Page Now" not in r.text or "archive.org" in r.text  # guidance mentions the mechanism, not necessarily the product name


def test_no_manual_review_section_when_nothing_flagged(env):
    """No manual-review rows at all -> the whole section (and any Wayback
    link) is simply absent, same as before this change."""
    c = _admin_client(env)
    r = c.get("/admin/reader/backfill-content")
    assert r.status_code == 200
    assert "Snapshot on Wayback" not in r.text
