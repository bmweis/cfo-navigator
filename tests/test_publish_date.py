"""Publish-date extraction.

Static-site sitemaps stamp every URL with the build date, so a backfill can
land a whole source on one wrong day. extract._extract_published reads the
true date from the page instead.

(The queue-era `redate_from_article_pages` repair tool — for already-queued
rows in the now-retired Archive Queue — was removed along with
linklib/queue.py; see PR 3.)
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import extract


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


