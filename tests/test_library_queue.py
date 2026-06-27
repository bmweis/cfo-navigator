"""Functional tests for the Library Queue staging layer (linklib/db.py).

The queue holds proposed saves until they're reviewed. These tests pin the
contract the web UI and the backfill sweep both depend on: idempotent dedupe
against the library and the queue, dismiss-without-resurface, and promotion
that preserves enrichment.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Article, Library


@pytest.fixture
def lib(tmp_path):
    db = Library(str(tmp_path / "test.db"))
    try:
        yield db
    finally:
        db.close()


def test_add_and_list(lib):
    assert lib.add_to_queue("https://ex.com/a", title="A", source="Blog",
                            summary="s", suggested_tags=["saas", "saas", " arr "],
                            origin="feed") is True
    pending = lib.list_queue()
    assert len(pending) == 1
    row = pending[0]
    assert row["url"] == "https://ex.com/a"
    assert row["suggested_tags"] == ["arr", "saas"]  # cleaned + sorted + deduped
    assert lib.queue_count() == 1


def test_dedupe_against_queue_and_library(lib):
    assert lib.add_to_queue("https://ex.com/a") is True
    # Same URL again → no-op.
    assert lib.add_to_queue("https://ex.com/a") is False
    # A URL already saved in the library is never queued.
    lib.upsert(Article(url="https://ex.com/saved"))
    assert lib.add_to_queue("https://ex.com/saved") is False
    assert lib.queue_count() == 1


def test_dismiss_does_not_resurface(lib):
    lib.add_to_queue("https://ex.com/a")
    lib.dismiss_queue_item("https://ex.com/a")
    assert lib.queue_count(status="pending") == 0
    assert lib.queue_count(status="dismissed") == 1
    # A later sweep proposing the same URL is suppressed.
    assert lib.add_to_queue("https://ex.com/a") is False


def test_promote_moves_into_library_and_preserves_enrichment(lib):
    lib.add_to_queue("https://ex.com/a", title="Deep Dive", source="Blog",
                     summary="the summary", content="full text",
                     suggested_tags=["saas"], enriched=True)
    article_id = lib.promote_queue_item("https://ex.com/a")
    assert article_id > 0
    # Gone from the queue, present in the library with enrichment intact.
    assert lib.queue_count(status="pending") == 0
    hits = lib.search("")
    assert len(hits) == 1
    art = hits[0]
    assert art["url"] == "https://ex.com/a"
    assert art["summary"] == "the summary"
    assert art["enriched"] == 1
    assert art["tags"] == ["saas"]


def test_promote_with_tag_override(lib):
    lib.add_to_queue("https://ex.com/a", suggested_tags=["saas"], enriched=True)
    lib.promote_queue_item("https://ex.com/a", tags=["arr", "benchmarks"])
    art = lib.search("")[0]
    assert art["tags"] == ["arr", "benchmarks"]


def test_promote_unknown_url_is_noop(lib):
    assert lib.promote_queue_item("https://ex.com/missing") == 0


def test_article_urls_and_last_saved_at(lib):
    assert lib.last_saved_at() is None
    lib.upsert(Article(url="https://ex.com/x", saved_at="2024-09-01T00:00:00+00:00"))
    lib.upsert(Article(url="https://ex.com/y", saved_at="2025-01-15T00:00:00+00:00"))
    assert lib.article_urls() == {"https://ex.com/x", "https://ex.com/y"}
    assert lib.last_saved_at() == "2025-01-15T00:00:00+00:00"
