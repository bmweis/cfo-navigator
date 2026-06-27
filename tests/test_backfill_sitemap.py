"""Tests for the one-time historical sweep (linklib/queue.py sitemap path).

Network is mocked: we pin the pure parsing/filtering logic and the scan's
date-window + dedupe behavior, since coverage in the wild is best-effort.
"""
import pathlib
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import queue as q
from linklib.db import Article, Library
from linklib.feed import FeedMeta


def test_parse_lastmod_variants():
    assert q._parse_lastmod("2025-03-01") == datetime(2025, 3, 1, tzinfo=timezone.utc)
    assert q._parse_lastmod("2025-03-01T10:30:00Z").year == 2025
    assert q._parse_lastmod("2025-03-01T10:30:00+00:00").month == 3
    assert q._parse_lastmod("2025-03-01T10:30:00.123Z") is not None  # fractional seconds
    assert q._parse_lastmod("") is None
    assert q._parse_lastmod("not-a-date") is None


def test_looks_like_post():
    assert q._looks_like_post("https://b.com/great-saas-post")
    assert not q._looks_like_post("https://b.com/tag/saas")
    assert not q._looks_like_post("https://b.com/author/jane/")
    assert not q._looks_like_post("https://b.com/page/3")


def test_parse_sitemap_xml_urlset():
    xml = b"""<?xml version="1.0"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://b.com/a</loc><lastmod>2025-03-01</lastmod></url>
      <url><loc>https://b.com/b</loc></url>
    </urlset>"""
    kind, entries = q.parse_sitemap_xml(xml)
    assert kind == "urlset"
    assert entries[0]["url"] == "https://b.com/a"
    assert entries[0]["lastmod"] == datetime(2025, 3, 1, tzinfo=timezone.utc)
    assert entries[1]["lastmod"] is None


def test_parse_sitemap_xml_index():
    xml = b"""<?xml version="1.0"?>
    <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <sitemap><loc>https://b.com/sitemap-posts.xml</loc></sitemap>
    </sitemapindex>"""
    kind, entries = q.parse_sitemap_xml(xml)
    assert kind == "sitemapindex"
    assert entries[0]["url"] == "https://b.com/sitemap-posts.xml"


def test_parse_sitemap_xml_garbage():
    assert q.parse_sitemap_xml(b"<not xml") == ("", [])


def test_scan_filters_by_window_and_dedupes(tmp_path, monkeypatch):
    lib = Library(str(tmp_path / "t.db"))
    # Already in the library — must be skipped even though it's in the window.
    lib.upsert(Article(url="https://b.com/post-saved"))

    entries = [
        {"url": "https://b.com/post-new", "lastmod": datetime(2025, 3, 1, tzinfo=timezone.utc)},
        {"url": "https://b.com/post-old", "lastmod": datetime(2024, 1, 1, tzinfo=timezone.utc)},
        {"url": "https://b.com/tag/saas", "lastmod": datetime(2025, 2, 1, tzinfo=timezone.utc)},
        {"url": "https://b.com/post-undated", "lastmod": None},
        {"url": "https://b.com/post-saved", "lastmod": datetime(2025, 1, 1, tzinfo=timezone.utc)},
    ]
    monkeypatch.setattr(q, "discover_sitemaps", lambda site: ["https://b.com/sitemap.xml"])
    monkeypatch.setattr(q, "fetch_sitemap_entries", lambda sm: entries)

    feeds = [FeedMeta(name="Blog", xml_url="", html_url="https://b.com", category="Blogs")]
    cutoff = datetime(2024, 8, 1, tzinfo=timezone.utc)

    report = q.scan_sitemaps_into_queue(lib, feeds, cutoff, enrich=False)
    st = report[0]
    assert st["candidates"] == 1          # only post-new clears the window + filters
    assert st["added"] == 1
    assert st["undated"] == 1             # post-undated counted but skipped

    queued = lib.list_queue()
    assert len(queued) == 1
    assert queued[0]["url"] == "https://b.com/post-new"
    assert queued[0]["origin"] == "backfill:Blog"
    lib.close()


def test_scan_dry_run_counts_without_saving(tmp_path, monkeypatch):
    lib = Library(str(tmp_path / "t.db"))
    entries = [{"url": "https://b.com/post-new",
                "lastmod": datetime(2025, 3, 1, tzinfo=timezone.utc)}]
    monkeypatch.setattr(q, "discover_sitemaps", lambda site: ["https://b.com/sitemap.xml"])
    monkeypatch.setattr(q, "fetch_sitemap_entries", lambda sm: entries)

    feeds = [FeedMeta(name="Blog", xml_url="", html_url="https://b.com", category="Blogs")]
    cutoff = datetime(2024, 8, 1, tzinfo=timezone.utc)

    report = q.scan_sitemaps_into_queue(lib, feeds, cutoff, enrich=False, dry_run=True)
    assert report[0]["candidates"] == 1
    assert report[0]["added"] == 0
    assert lib.queue_count() == 0          # nothing saved in dry run
    lib.close()


def test_scan_handles_source_without_sitemap(tmp_path, monkeypatch):
    lib = Library(str(tmp_path / "t.db"))
    monkeypatch.setattr(q, "discover_sitemaps", lambda site: ["https://b.com/sitemap.xml"])
    monkeypatch.setattr(q, "fetch_sitemap_entries", lambda sm: [])

    feeds = [FeedMeta(name="Blog", xml_url="", html_url="https://b.com", category="Blogs")]
    report = q.scan_sitemaps_into_queue(lib, feeds, datetime(2024, 8, 1, tzinfo=timezone.utc),
                                        enrich=False)
    assert report[0]["note"] == "no usable sitemap"
    assert report[0]["candidates"] == 0
    lib.close()
