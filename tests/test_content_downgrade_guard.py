"""Stored full text must never be replaced by a paywalled/degraded fetch.

An un-cookied fetch of a paywalled domain does not fail: it returns HTTP 200
with a truncated preview that reads as legitimate content. Every path that
writes article content is already safe from this, but for two different
reasons, and neither reason is enforced by anything a reader would notice:

  * `ingest_url` and the enrichment backfill are WRITE-ONCE BY CONSTRUCTION.
    `upsert` merges with `existing["content"] or art.content`, and the
    enrichment backfill only fetches under `if fetch and not text`. Both
    protections live in a single expression that a refactor could remove
    silently.
  * The three `content_html` writers (Reader backfill, domain-migration tier,
    Wayback fallback) are GUARDED, via `assess_extraction_quality`, which
    returns `(False, "paywall")` the moment `PageData.blocked` is set.

These tests pin both properties. No new guards were added: adding them would
be redundant code implying a danger that does not exist. What was missing was
proof that the existing protections hold, which is what this file is.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import pipeline
from linklib.db import Article, Library
from linklib.extract import PageData, assess_extraction_quality, looks_paywalled

GOOD = ("Real article body. " * 200).strip()
# A logged-out preview: real HTML, HTTP 200, and one of extract._PAYWALL_MARKERS.
PREVIEW_HTML = "<html><body><p>This post is for paid subscribers.</p></body></html>"
PREVIEW_TEXT = "This post is for paid subscribers."


@pytest.fixture
def lib(tmp_path):
    db = Library(str(tmp_path / "t.db"))
    try:
        yield db
    finally:
        db.close()


def _preview_page():
    """What fetch_page returns for an un-cookied paywalled fetch: a 200, real
    text, and blocked=True."""
    return PageData(title="Paywalled", content=PREVIEW_TEXT,
                    raw_html=PREVIEW_HTML, blocked=True)


def _good_page():
    return PageData(title="Real", content=GOOD,
                    raw_html=f"<html><body><article><p>{GOOD}</p></article></body></html>",
                    blocked=False)


# ---------------------------------------------------------------------------
# The shared classifier
# ---------------------------------------------------------------------------

def test_a_preview_is_classified_as_paywalled():
    """The premise the rest of this file rests on."""
    assert looks_paywalled(PREVIEW_HTML, PREVIEW_TEXT) is True
    ok, reason = assess_extraction_quality(PREVIEW_HTML, PREVIEW_TEXT, blocked=True)
    assert (ok, reason) == (False, "paywall")


def test_real_content_is_not_classified_as_paywalled():
    page = _good_page()
    ok, reason = assess_extraction_quality(page.raw_html, page.content, page.blocked)
    assert (ok, reason) == (True, "")


# ---------------------------------------------------------------------------
# Write-once by construction: ingest_url
# ---------------------------------------------------------------------------

def test_upsert_never_replaces_existing_content(lib):
    """`existing["content"] or art.content` — the whole protection for
    ingest_url, pinned so a refactor to a plain overwrite fails here."""
    aid = lib.upsert(Article(url="https://paywalled.example/a", title="A", content=GOOD))
    lib.upsert(Article(url="https://paywalled.example/a", title="A", content=PREVIEW_TEXT))
    row = [r for r in lib.search("", limit=1000) if r["id"] == aid][0]
    assert row["content"] == GOOD


def test_upsert_still_fills_content_when_empty(lib):
    """The flip side: write-once must not mean write-never."""
    aid = lib.upsert(Article(url="https://ok.example/a", title="A", content=""))
    lib.upsert(Article(url="https://ok.example/a", title="A", content=GOOD))
    row = [r for r in lib.search("", limit=1000) if r["id"] == aid][0]
    assert row["content"] == GOOD


def test_ingest_url_does_not_downgrade_a_stored_article(lib, monkeypatch):
    """End to end through the real ingest path, with the fetch returning a
    preview for an article that already has full text."""
    aid = lib.upsert(Article(url="https://paywalled.example/b", title="B", content=GOOD))
    monkeypatch.setattr("linklib.extract.fetch_page", lambda *a, **k: _preview_page())

    pipeline.ingest_url(lib, "https://paywalled.example/b", do_enrich=False)

    row = [r for r in lib.search("", limit=1000) if r["id"] == aid][0]
    assert row["content"] == GOOD
    assert PREVIEW_TEXT not in row["content"]


# ---------------------------------------------------------------------------
# Write-once by construction: the enrichment backfill
# ---------------------------------------------------------------------------

def test_enrichment_backfill_does_not_refetch_an_article_that_has_content(lib, monkeypatch):
    """`if fetch and not text` is the protection. If it ever became an
    unconditional fetch, the preview below would overwrite GOOD."""
    aid = lib.upsert(Article(url="https://paywalled.example/c", title="C", content=GOOD))
    lib.conn.execute("UPDATE articles SET enriched=0 WHERE id=?", (aid,))
    lib.conn.commit()

    calls = []

    def _spy(url, *a, **k):
        calls.append(url)
        return _preview_page()

    monkeypatch.setattr("linklib.extract.fetch_page", _spy)
    monkeypatch.setattr("linklib.enrich.enrich", lambda *a, **k: None)

    pipeline.enrich_library(lib, limit=10, fetch=True)

    assert calls == [], "an article with stored content must not be re-fetched"
    row = [r for r in lib.search("", limit=1000) if r["id"] == aid][0]
    assert row["content"] == GOOD


def test_update_content_ignores_an_empty_write(lib):
    """The last line of defence inside the setter itself."""
    aid = lib.upsert(Article(url="https://ok.example/d", title="D", content=GOOD))
    lib.update_content(aid, "")
    row = [r for r in lib.search("", limit=1000) if r["id"] == aid][0]
    assert row["content"] == GOOD


# ---------------------------------------------------------------------------
# Guarded: the three content_html writers
# ---------------------------------------------------------------------------

def _article_with_html(lib, url, html="<article><p>stored structure</p></article>"):
    aid = lib.upsert(Article(url=url, title="T", content=GOOD))
    lib.set_article_content_html(aid, html)
    return aid


def test_reader_backfill_refuses_a_paywalled_refetch(lib, monkeypatch):
    """force=True can reach an article that already has content_html; a
    paywalled result must leave it alone and log the reason instead."""
    aid = _article_with_html(lib, "https://paywalled.example/e")
    before = lib.get_article(aid)["content_html"]
    monkeypatch.setattr("linklib.extract.fetch_page", lambda *a, **k: _preview_page())

    ok, reason = pipeline.backfill_article_content(lib, lib.get_article(aid))

    assert ok is False
    assert reason == "paywall"
    assert lib.get_article(aid)["content_html"] == before


def test_reader_backfill_logs_the_refusal_rather_than_failing_silently(lib, monkeypatch):
    aid = _article_with_html(lib, "https://paywalled.example/f")
    monkeypatch.setattr("linklib.extract.fetch_page", lambda *a, **k: _preview_page())

    pipeline.backfill_article_content(lib, lib.get_article(aid))

    reasons = [r["reason"] for r in lib.list_content_refetch_log(limit=10)]
    assert "paywall" in reasons


def test_reader_backfill_still_stores_a_good_refetch(lib, monkeypatch):
    """The guard must not be so broad that it blocks legitimate updates."""
    aid = _article_with_html(lib, "https://ok.example/g", html="")
    monkeypatch.setattr("linklib.extract.fetch_page", lambda *a, **k: _good_page())

    ok, reason = pipeline.backfill_article_content(lib, lib.get_article(aid))

    assert (ok, reason) == (True, "")
    assert lib.get_article(aid)["content_html"]


def test_wayback_fallback_refuses_a_paywalled_snapshot(lib, monkeypatch):
    """An archived paywall preview has to clear the same bar a live page does."""
    aid = _article_with_html(lib, "https://paywalled.example/h")
    before = lib.get_article(aid)["content_html"]

    monkeypatch.setattr("linklib.extract.fetch_page",
                        lambda *a, **k: PageData(title="", content="", raw_html="",
                                                 fetch_error="HTTP 403"))
    monkeypatch.setattr("linklib.wayback.find_snapshot_verbose",
                        lambda url: ("https://web.archive.org/x", ""))
    monkeypatch.setattr("linklib.wayback.fetch_snapshot_verbose",
                        lambda url: (PREVIEW_HTML, ""))

    ok, _reason = pipeline.backfill_article_content(lib, lib.get_article(aid))

    assert ok is False
    assert lib.get_article(aid)["content_html"] == before


def test_migration_tier_refuses_a_paywalled_candidate(lib, monkeypatch):
    """The domain-migration tier runs assess_extraction_quality on its
    candidate before storing, same as every other writer."""
    aid = _article_with_html(lib, "https://pointsandfigures.com/i")
    before = lib.get_article(aid)["content_html"]

    monkeypatch.setattr("linklib.domain_migration.find_migrated_url",
                        lambda lib_, dom, title: ("https://jeffreycarter.substack.com/p/x", 0.007))
    monkeypatch.setattr("linklib.extract.fetch_page", lambda *a, **k: _preview_page())

    ok, _structured, _url, _cost = pipeline._try_domain_migration(
        lib, "jeffreycarter.substack.com", "Some Title",
        "https://pointsandfigures.com/i")

    assert ok is False
    assert lib.get_article(aid)["content_html"] == before
