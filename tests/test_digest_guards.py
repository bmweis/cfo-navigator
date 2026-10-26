"""Benchmark digests must stay as written.

A row tagged `benchmark-digest` is hand-handed text (a Claude-written digest of
a third-party report). Four automatic mechanisms would silently undo it, so each
skips the tag, even when forced:

- the enrich backfill and forced re-enrich (a Claude summary replaces the digest's)
- the Reader content backfill (it would fetch the report landing page into
  content_html, which the Reader prefers over the stored text)
- the article purge candidate list (a short digest can sit under the 60-word floor)
- near-duplicate clustering (digests of one report read alike)

These tests use only existing public APIs and invented data (publisher: "Example
Benchmarks 2099"), so they run unchanged against the code before the guards.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import enrich as enrich_mod
from linklib import pipeline
from linklib.db import Article, Library

TAG = "benchmark-digest"
BODY = ("Example Benchmarks 2099 digest. Median net revenue retention was 101 percent. " * 30).strip()


@pytest.fixture
def lib(tmp_path):
    db = Library(str(tmp_path / "t.db"))
    try:
        yield db
    finally:
        db.close()


def _digest(lib, slug, *, enriched=False, content=BODY, title=None):
    return lib.upsert(Article(
        url=f"https://example-benchmarks.test/report-2099?digest={slug}",
        title=title or f"Example Benchmarks 2099: {slug}",
        source="Example Benchmarks 2099", summary=f"Digest summary for {slug}.",
        content=content, tags=[TAG], enriched=enriched,
        published_at="2099-01-01T00:00:00+00:00"))


def _plain(lib, slug="ordinary"):
    return lib.upsert(Article(url=f"https://blog.test/{slug}", title=f"Ordinary {slug}",
                              content=BODY, enriched=False))


def test_unenriched_skips_digests(lib):
    d = _digest(lib, "growth", enriched=False)
    p = _plain(lib)
    ids = {r["id"] for r in lib.unenriched()}
    assert p in ids
    assert d not in ids


def test_forced_re_enrich_skips_digests(lib, monkeypatch):
    d = _digest(lib, "retention", enriched=True)
    _plain(lib)
    seen = []

    def fake_enrich(title, text, **kw):
        seen.append(title)
        return None

    monkeypatch.setattr(enrich_mod, "enrich", fake_enrich)
    pipeline.enrich_library(lib, force=True, fetch=False)
    assert any("Ordinary" in t for t in seen), "the ordinary article should still be re-enriched"
    assert not any("Example Benchmarks 2099" in t for t in seen), seen
    assert lib.get_article(d)["summary"] == "Digest summary for retention."


def test_non_forced_enrich_never_touches_an_unenriched_digest(lib, monkeypatch):
    _digest(lib, "efficiency", enriched=False)
    seen = []
    monkeypatch.setattr(enrich_mod, "enrich", lambda title, text, **kw: seen.append(title))
    pipeline.enrich_library(lib, force=False, fetch=False)
    assert seen == []


def test_reader_backfill_skips_digests_in_every_scope(lib):
    d = _digest(lib, "gtm")
    p = _plain(lib)
    for kwargs in ({}, {"force": True}, {"host_suffixes": ["example-benchmarks.test"]},
                   {"host_suffixes": ["example-benchmarks.test"], "force": True}):
        ids = {r["id"] for r in lib.articles_needing_content_backfill(**kwargs)}
        assert d not in ids, kwargs
    assert p in {r["id"] for r in lib.articles_needing_content_backfill()}


def test_remaining_count_ignores_digests(lib):
    _plain(lib)
    before = lib.count_content_backfill_remaining()
    _digest(lib, "one")
    _digest(lib, "two")
    assert lib.count_content_backfill_remaining() == before


def test_a_short_digest_is_not_a_purge_candidate(lib):
    d = _digest(lib, "short", content="Only a few words here.")
    ids = {r["id"] for r in lib.articles_eligible_for_purge()}
    assert d not in ids


def test_dedupe_does_not_cluster_digests_of_one_report(lib):
    from linklib import dedupe
    a = _digest(lib, "growth", title="Example Benchmarks 2099: Growth benchmarks")
    b = _digest(lib, "growth-2", title="Example Benchmarks 2099: Growth benchmarks, part two")
    rows = [lib.get_article(a), lib.get_article(b)]
    for r in rows:
        r["summary"] = "Median growth benchmark for example software companies in 2099."
    # Control: without the tag, these two read as near-duplicates.
    untagged = [dict(r, tags=[]) for r in rows]
    assert dedupe.find_clusters(untagged), "fixture should look like a duplicate pair"
    assert dedupe.find_clusters(rows) == []
