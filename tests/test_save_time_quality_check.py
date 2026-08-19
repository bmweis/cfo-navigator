"""Durability audit item 1 — ingest_url() now runs
extract.assess_extraction_quality() on every real fetch and flags the row
instead of silently storing a bad result. Covers:

- A thin/paywalled/bot-challenged/fetch-failed save still saves (never
  blocked) but sets needs_content_check + content_check_reason and logs a
  content_refetch_log row with source='save'.
- A good fetch leaves the flag clear.
- The flag is only trusted when this fetch's content is what actually got
  stored — a bad resave of an article that already had good content (write-
  once merge in upsert()) must NOT mis-flag it.
- set_article_content_html (a later successful backfill) clears the flag.
- Library.count_needs_content_check() reflects the flagged rows.

See CLAUDE.md's "Durability audit item 1" bullet and ARCHITECTURE.md's
articles/content_refetch_log table rows for the full write-up.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import pipeline
from linklib.db import Article, Library
from linklib.extract import PageData

GOOD = ("Real article body with plenty of words in it. " * 20).strip()
THIN = "Too short."


@pytest.fixture
def lib(tmp_path):
    db = Library(str(tmp_path / "t.db"))
    try:
        yield db
    finally:
        db.close()


def _good_page():
    return PageData(title="Real", content=GOOD,
                    raw_html=f"<html><body><article><p>{GOOD}</p></article></body></html>",
                    blocked=False)


def _thin_page():
    return PageData(title="Stub", content=THIN,
                    raw_html=f"<html><body><p>{THIN}</p></body></html>", blocked=False)


def _failed_fetch_page():
    return PageData(title="", content="", fetch_error="timeout")


def test_thin_save_still_saves_but_gets_flagged(lib, monkeypatch):
    monkeypatch.setattr("linklib.extract.fetch_page", lambda *a, **k: _thin_page())
    row = pipeline.ingest_url(lib, "https://ex.com/thin", do_enrich=False)

    stored = lib.get_article(row["id"])
    assert stored["content"] == THIN  # never blocked
    assert stored["needs_content_check"] == 1
    assert stored["content_check_reason"] == "too-thin"
    assert lib.count_needs_content_check() == 1

    log_rows = lib.list_content_refetch_log(limit=10)
    assert any(r["article_id"] == row["id"] and r["status"] == "failure"
               and r["reason"] == "too-thin" and r["source"] == "save"
               for r in log_rows)


def test_fetch_error_saves_and_flags_with_fetch_error_reason(lib, monkeypatch):
    monkeypatch.setattr("linklib.extract.fetch_page", lambda *a, **k: _failed_fetch_page())
    row = pipeline.ingest_url(lib, "https://ex.com/dead", do_enrich=False)

    stored = lib.get_article(row["id"])
    assert stored["needs_content_check"] == 1
    assert stored["content_check_reason"] == "fetch-error"


def test_good_save_leaves_flag_clear(lib, monkeypatch):
    monkeypatch.setattr("linklib.extract.fetch_page", lambda *a, **k: _good_page())
    row = pipeline.ingest_url(lib, "https://ex.com/good", do_enrich=False)

    stored = lib.get_article(row["id"])
    assert stored["needs_content_check"] == 0
    assert stored["content_check_reason"] == ""
    assert lib.count_needs_content_check() == 0


def test_bad_resave_does_not_flag_an_article_with_existing_good_content(lib, monkeypatch):
    """upsert() is write-once for content — a resave that fetches badly must
    not mark an already-good article as needing a check, since the bad fetch
    never actually overwrote the stored content."""
    aid = lib.upsert(Article(url="https://ex.com/keeper", title="K", content=GOOD))
    monkeypatch.setattr("linklib.extract.fetch_page", lambda *a, **k: _thin_page())

    pipeline.ingest_url(lib, "https://ex.com/keeper", do_enrich=False)

    stored = lib.get_article(aid)
    assert stored["content"] == GOOD
    assert stored["needs_content_check"] == 0
    assert stored["content_check_reason"] == ""


def test_set_article_content_html_clears_the_flag(lib, monkeypatch):
    monkeypatch.setattr("linklib.extract.fetch_page", lambda *a, **k: _thin_page())
    row = pipeline.ingest_url(lib, "https://ex.com/thin2", do_enrich=False)
    assert lib.get_article(row["id"])["needs_content_check"] == 1

    lib.set_article_content_html(row["id"], "<p>real structured content</p>")

    stored = lib.get_article(row["id"])
    assert stored["needs_content_check"] == 0
    assert stored["content_check_reason"] == ""
    assert lib.count_needs_content_check() == 0


def test_fetch_fulltext_false_never_touches_the_flag(lib, monkeypatch):
    """A notes-only save (no fetch) has no quality signal to act on — the
    flag must be left alone, not zeroed out from underneath a real one."""
    def _boom(*a, **k):
        raise AssertionError("fetch_page should not be called")
    monkeypatch.setattr("linklib.extract.fetch_page", _boom)

    row = pipeline.ingest_url(lib, "https://ex.com/notes-only", fetch_fulltext=False,
                              do_enrich=False)
    stored = lib.get_article(row["id"])
    assert stored["needs_content_check"] == 0
