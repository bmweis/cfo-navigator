"""Publish-date extraction + queue re-date repair.

Static-site sitemaps stamp every URL with the build date, so the backfill can
land a whole source on one wrong day. extract._extract_published reads the true
date from the page; redate_from_article_pages repairs already-queued rows.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import extract, queue as q
from linklib.db import Article, Library
from linklib.extract import PageData


def test_published_from_og_meta():
    html = '<meta property="article:published_time" content="2024-03-15T09:00:00Z">'
    assert extract._extract_published(html).startswith("2024-03-15")


def test_published_from_time_tag():
    html = '<article><time datetime="2023-11-02">Nov 2, 2023</time>...'
    assert extract._extract_published(html).startswith("2023-11-02")


def test_published_from_jsonld():
    html = '<script type="application/ld+json">{"datePublished":"2022-07-01T12:00:00+00:00"}</script>'
    assert extract._extract_published(html).startswith("2022-07-01")


def test_future_or_garbage_date_rejected():
    assert extract._extract_published('<time datetime="2999-01-01">x</time>') == ""
    assert extract._extract_published('<time datetime="not-a-date">x</time>') == ""
    assert extract._extract_published("<p>no date here</p>") == ""


@pytest.fixture
def lib(tmp_path):
    db = Library(str(tmp_path / "t.db"))
    try:
        yield db
    finally:
        db.close()


def test_redate_fixes_queue_and_articles(lib, monkeypatch):
    # A queued item and a saved article, both stamped with a wrong (build) date.
    lib.add_to_queue("https://tomtunguz.com/a", title="A", source="Tomasz Tunguz (Redpoint)",
                     published_at="2026-06-27T00:00:00+00:00")
    aid = lib.upsert(Article(url="https://tomtunguz.com/b", title="B",
                             source="Tomasz Tunguz (Redpoint)",
                             published_at="2026-06-27T00:00:00+00:00"))
    # An unrelated source must be left alone.
    lib.add_to_queue("https://saastr.com/x", title="X", source="SaaStr",
                     published_at="2026-06-27T00:00:00+00:00")

    real = {"https://tomtunguz.com/a": "2024-01-10T00:00:00+00:00",
            "https://tomtunguz.com/b": "2023-05-04T00:00:00+00:00"}
    monkeypatch.setattr(extract, "fetch_page",
                        lambda url, timeout=20: PageData(title="", content="x",
                                                         published=real.get(url, "")))

    stats = q.redate_from_article_pages(lib, "Tomasz")
    assert stats["updated"] == 2

    queued = {r["url"]: r for r in lib.list_queue()}
    assert queued["https://tomtunguz.com/a"]["published_at"].startswith("2024-01-10")
    assert queued["https://saastr.com/x"]["published_at"].startswith("2026-06-27")  # untouched

    art = next(a for a in lib.all_articles() if a["id"] == aid)
    assert art["published_at"].startswith("2023-05-04")
