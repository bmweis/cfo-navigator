"""Medium-platform Exa fetch tier (Phase 1) — see CLAUDE.md's Medium-platform
investigation bullets, scripts/medium_platform_scale_check.py (the diagnostic
that validated this approach against real production data before it shipped),
and linklib/medium_platform.py's module docstring.

Covers:
- linklib.medium_platform.is_medium_platform_host — suffix-based recognition
  (medium.com itself, any *.medium.com subdomain including author subdomains
  and the link.medium.com short-link redirector, plus the hand-curated
  _MEDIUM_CUSTOM_DOMAINS set), not an exact-string list.
- linklib.medium_platform.find_medium_candidate — Exa search, unrestricted
  domain, title-match validation via the reused domain_migration._titles_match.
- linklib.extract.paragraphs_html_from_text — the Exa-text-to-Reader-HTML
  formatter: headings, paragraph splitting, markdown link/emphasis stripping,
  and the leading Medium-chrome strip (conservative — only ever eats from the
  top, never drops a real paragraph that happens to echo a chrome phrase).
- linklib.pipeline._try_medium_platform / _finish_backfill_after_direct_failure
  — tried before Wayback, the same-domain-carve-out validation split (Exa
  text vs. live re-fetch depending on where the candidate itself resolved),
  exactly one content_refetch_log row per backfill_article_content() call,
  source='medium-search' on success.
- Library.count_medium_search_content.
- Library.articles_needing_content_backfill's new host_suffixes scoping —
  narrows to matching hosts and bypasses the needs-manual-review exclusion
  for those hosts only, while defunct-service exclusion still applies.
- webapp/app.py admin page: "via Medium search" badge and the "Scope to
  host(s)" form field wiring through to host_suffixes.
"""
import pathlib
import sys
import tempfile
import os

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Article, Library
from linklib import pipeline as pl
from linklib import wayback as wayback_mod
from linklib import extract as extract_mod
from linklib import medium_platform as mp_mod
from linklib import domain_migration as dm_mod
from linklib.extract import PageData, paragraphs_html_from_text


def _seed(lib, url="https://medium.com/@someone/a-post-abc123",
          content="Original plain text content.", title="Original Title", author=""):
    art = Article(url=url, title=title, content=content, author=author,
                  saved_at="2026-01-01T00:00:00")
    return lib.upsert(art)


@pytest.fixture
def lib(tmp_path):
    db_path = str(tmp_path / "test.db")
    library = Library(db_path)
    yield library
    library.close()


# ---------------------------------------------------------------------------
# is_medium_platform_host — suffix-based, not exact-string
# ---------------------------------------------------------------------------

def test_is_medium_platform_host_bare_domain():
    assert mp_mod.is_medium_platform_host("https://medium.com/@someone/post-abc123")


def test_is_medium_platform_host_author_subdomain():
    assert mp_mod.is_medium_platform_host("https://pt.medium.com/some-post")
    assert mp_mod.is_medium_platform_host("https://samirkaji.medium.com/some-post")


def test_is_medium_platform_host_link_shortener_subdomain():
    assert mp_mod.is_medium_platform_host("https://link.medium.com/xyz123")


def test_is_medium_platform_host_custom_domain():
    assert mp_mod.is_medium_platform_host("https://bothsidesofthetable.com/some-post-a61764e87626")


def test_is_medium_platform_host_rejects_unrelated():
    assert not mp_mod.is_medium_platform_host("https://example.com/post")
    assert not mp_mod.is_medium_platform_host("https://notmedium.com/post")


def test_is_medium_platform_host_empty_url():
    assert not mp_mod.is_medium_platform_host("")


# ---------------------------------------------------------------------------
# find_medium_candidate — Exa search + title-match validation
# ---------------------------------------------------------------------------

def test_find_medium_candidate_no_api_key(monkeypatch):
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    assert mp_mod.find_medium_candidate(None, "Some Title") == ("", "")


def test_find_medium_candidate_no_title(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "fake-key")
    assert mp_mod.find_medium_candidate(None, "") == ("", "")


def test_find_medium_candidate_returns_title_matching_hit(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "fake-key")

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"results": [
                {"title": "An Unrelated Post", "url": "https://example.com/unrelated", "text": "unrelated"},
                {"title": "My Great Post", "url": "https://techcrunch.com/my-great-post", "text": "real body text"},
            ]}

    monkeypatch.setattr(mp_mod.requests, "post", lambda *a, **kw: _Resp())
    url, text = mp_mod.find_medium_candidate(None, "My Great Post")
    assert url == "https://techcrunch.com/my-great-post"
    assert text == "real body text"


def test_find_medium_candidate_no_match_returns_empty(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "fake-key")

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"results": [{"title": "Totally Unrelated", "url": "https://example.com/x", "text": ""}]}

    monkeypatch.setattr(mp_mod.requests, "post", lambda *a, **kw: _Resp())
    assert mp_mod.find_medium_candidate(None, "My Great Post") == ("", "")


def test_find_medium_candidate_network_error_returns_empty(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "fake-key")

    def _raise(*a, **kw):
        raise ConnectionError("boom")

    monkeypatch.setattr(mp_mod.requests, "post", _raise)
    assert mp_mod.find_medium_candidate(None, "My Great Post") == ("", "")


def test_find_medium_candidate_no_domain_restriction_in_request(monkeypatch):
    """Unlike domain_migration's find_migrated_url, this must NOT restrict
    the Exa search to a single destination domain — Medium articles resolve
    to many different hosts."""
    monkeypatch.setenv("EXA_API_KEY", "fake-key")
    captured = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"results": []}

    def _post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        return _Resp()

    monkeypatch.setattr(mp_mod.requests, "post", _post)
    mp_mod.find_medium_candidate(None, "Some Title")
    assert "includeDomains" not in captured["json"]


# ---------------------------------------------------------------------------
# paragraphs_html_from_text — Exa-text -> Reader HTML formatter
# ---------------------------------------------------------------------------

def test_paragraphs_html_from_text_empty_input():
    assert paragraphs_html_from_text("") == ""
    assert paragraphs_html_from_text("   \n\n  ") == ""


def test_paragraphs_html_from_text_basic_paragraphs():
    text = "First paragraph here.\n\nSecond paragraph here."
    out = paragraphs_html_from_text(text)
    assert out == "<p>First paragraph here.</p><p>Second paragraph here.</p>"


def test_paragraphs_html_from_text_markdown_heading_becomes_h2():
    text = "# A Big Heading\n\nBody paragraph follows."
    out = paragraphs_html_from_text(text)
    assert "<h2>A Big Heading</h2>" in out
    assert "<p>Body paragraph follows.</p>" in out


def test_paragraphs_html_from_text_deep_heading_caps_at_h3():
    text = "#### Deep Heading\n\nBody."
    out = paragraphs_html_from_text(text)
    assert "<h3>Deep Heading</h3>" in out


def test_paragraphs_html_from_text_strips_emphasis_markers():
    text = "Body para\n\nThis has *emphasis* and **strong** and _underscore_ text."
    out = paragraphs_html_from_text(text)
    assert "*" not in out
    assert "_" not in out
    assert "emphasis" in out and "strong" in out and "underscore" in out


def test_paragraphs_html_from_text_links_keep_text_drop_url():
    text = "Intro para\n\nRead [this article](https://example.com/x) for more."
    out = paragraphs_html_from_text(text)
    assert "example.com" not in out
    assert "this article" in out


def test_paragraphs_html_from_text_strips_leading_medium_chrome():
    text = ("Open in app\n\nSign up\n\nGet app\n\nSign up\n\n"
            "# History of Public SaaS Returns\n\n"
            "This is the real first paragraph and it must never be dropped.")
    out = paragraphs_html_from_text(text)
    assert "Open in app" not in out
    assert "Sign up" not in out
    assert "Get app" not in out
    assert "<h2>History of Public SaaS Returns</h2>" in out
    assert "real first paragraph" in out


def test_paragraphs_html_from_text_strips_min_read_byline_anywhere():
    text = "# A Title\n\n9 min read\n\nReal body paragraph text here."
    out = paragraphs_html_from_text(text)
    assert "min read" not in out
    assert "<h2>A Title</h2>" in out
    assert "Real body paragraph" in out


def test_paragraphs_html_from_text_never_drops_paragraph_echoing_chrome_word():
    """Conservative-by-design: a real sentence that happens to contain a
    chrome phrase mid-article must survive — only a line matching a chrome
    phrase IN ITS ENTIRETY, and only while still in the leading run, is
    stripped."""
    text = ("Real opening paragraph of the article, well past any chrome.\n\n"
            "A sentence mentioning that readers should sign up for our "
            "newsletter must never be dropped just because it echoes a "
            "chrome phrase mid-article.")
    out = paragraphs_html_from_text(text)
    assert "sign up for our" in out
    assert "Real opening paragraph" in out


def test_paragraphs_html_from_text_all_chrome_no_real_content_returns_empty():
    text = "Open in app\n\nSign up\n\nGet app"
    assert paragraphs_html_from_text(text) == ""


def test_paragraphs_html_from_text_html_escapes_content():
    text = "Body para\n\nA sentence with <script>alert(1)</script> in it."
    out = paragraphs_html_from_text(text)
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


# ---------------------------------------------------------------------------
# linklib.pipeline — Medium-platform fetch tier
# ---------------------------------------------------------------------------

def test_backfill_uses_medium_tier_before_wayback_on_other_host_match(lib, monkeypatch):
    """A Medium-platform article whose direct fetch fails, and whose Exa
    candidate resolves to some OTHER host, should be validated via a live
    re-fetch of that candidate (same path _try_domain_migration uses) — a
    success must skip Wayback entirely and log exactly ONE row,
    source='medium-search'."""
    article_id = _seed(lib, url="https://medium.com/@vc/some-post-abc123",
                       title="Some VC Post", author="Some VC")
    monkeypatch.setattr(extract_mod, "fetch_page",
                        lambda url: PageData(title="", content="", fetch_error="HTTP 403"))
    monkeypatch.setattr(mp_mod, "find_medium_candidate",
                        lambda lib_, title, author="": ("https://techcrunch.com/some-post", ""))

    candidate_html = ("<html><body><article>" +
                      "<p>Candidate content, definitely long enough to pass the sanity check.</p>" * 15 +
                      "</article></body></html>")
    candidate_page = PageData(title="Some VC Post",
                              content="Candidate content, plenty of real words here. " * 20,
                              blocked=False, raw_html=candidate_html)

    wayback_called = []
    monkeypatch.setattr(wayback_mod, "find_snapshot_verbose",
                        lambda url: wayback_called.append(url) or (None, "unreached"))

    def _fetch_page(url):
        if "techcrunch.com" in url:
            return candidate_page
        return PageData(title="", content="", fetch_error="HTTP 403")
    monkeypatch.setattr(extract_mod, "fetch_page", _fetch_page)

    ok, reason = pl.backfill_article_content(lib, lib.get_article(article_id))
    assert ok is True
    assert wayback_called == [], "must not attempt Wayback once the Medium tier already succeeded"

    log = lib.list_content_refetch_log()
    assert len(log) == 1, "exactly one content_refetch_log row per backfill_article_content() call"
    assert log[0]["status"] == "success"
    assert log[0]["source"] == "medium-search"
    assert "techcrunch.com" in log[0]["detail"]

    row = lib.get_article(article_id)
    assert "Candidate content" in row["content_html"]


def test_backfill_medium_tier_same_domain_carve_out_uses_exa_text(lib, monkeypatch):
    """When Exa's candidate resolves BACK onto a Medium-platform host, a
    live re-fetch would just re-hit the same block — validate against Exa's
    own returned text instead, converted via paragraphs_html_from_text, and
    never call fetch_page for the candidate at all."""
    article_id = _seed(lib, url="https://medium.com/@vc/some-post-def456",
                       title="Another VC Post", author="Another VC")
    monkeypatch.setattr(extract_mod, "fetch_page",
                        lambda url: PageData(title="", content="", fetch_error="HTTP 403"))

    exa_text = ("# Another VC Post\n\n"
                "Real opening paragraph with plenty of words to clear the minimum "
                "content threshold comfortably. " * 10)
    monkeypatch.setattr(mp_mod, "find_medium_candidate",
                        lambda lib_, title, author="": ("https://medium.com/@other/some-post-xyz789", exa_text))

    fetch_calls = []
    real_fetch_page = extract_mod.fetch_page

    def _tracking_fetch_page(url):
        fetch_calls.append(url)
        return real_fetch_page(url) if False else PageData(title="", content="", fetch_error="HTTP 403")
    monkeypatch.setattr(extract_mod, "fetch_page", _tracking_fetch_page)

    wayback_called = []
    monkeypatch.setattr(wayback_mod, "find_snapshot_verbose",
                        lambda url: wayback_called.append(url) or (None, "unreached"))

    ok, reason = pl.backfill_article_content(lib, lib.get_article(article_id))
    assert ok is True
    assert wayback_called == []
    # The candidate URL (medium.com host) must never be live-fetched.
    assert not any("other" in c for c in fetch_calls)

    log = lib.list_content_refetch_log()
    assert len(log) == 1
    assert log[0]["source"] == "medium-search"
    row = lib.get_article(article_id)
    assert "<h2>Another VC Post</h2>" in row["content_html"]
    assert "Real opening paragraph" in row["content_html"]


def test_backfill_medium_tier_same_domain_carve_out_too_thin_falls_through(lib, monkeypatch):
    """Same-domain candidate whose Exa text is too short to clear the
    word-count floor must fall through to Wayback, not be accepted."""
    article_id = _seed(lib, url="https://medium.com/@vc/thin-post", title="Thin Post")
    monkeypatch.setattr(extract_mod, "fetch_page",
                        lambda url: PageData(title="", content="", fetch_error="HTTP 403"))
    monkeypatch.setattr(mp_mod, "find_medium_candidate",
                        lambda lib_, title, author="": ("https://medium.com/@other/thin-post-2", "Too short."))
    monkeypatch.setattr(wayback_mod, "find_snapshot_verbose", lambda url: (None, "no snapshot archived"))

    ok, reason = pl.backfill_article_content(lib, lib.get_article(article_id))
    assert ok is False
    log = lib.list_content_refetch_log()
    assert len(log) == 1
    assert log[0]["source"] == "direct"  # Wayback-path logging convention


def test_backfill_medium_tier_miss_falls_through_to_wayback(lib, monkeypatch):
    """A Medium-platform article where Exa finds no candidate at all must
    fall through to the pre-existing Wayback fallback, still logging
    exactly ONE row."""
    article_id = _seed(lib, url="https://medium.com/@vc/no-match-post", title="No Match Post")
    monkeypatch.setattr(extract_mod, "fetch_page",
                        lambda url: PageData(title="", content="", fetch_error="HTTP 403"))
    monkeypatch.setattr(mp_mod, "find_medium_candidate", lambda lib_, title, author="": ("", ""))

    snap_html = ("<html><body><article>" +
                "<p>Archived snapshot content, long enough to pass sanity check reliably.</p>" * 15 +
                "</article></body></html>")
    monkeypatch.setattr(wayback_mod, "find_snapshot_verbose", lambda url: ("https://web.archive.org/web/x", ""))
    monkeypatch.setattr(wayback_mod, "fetch_snapshot_verbose", lambda snap_url: (snap_html, ""))

    ok, reason = pl.backfill_article_content(lib, lib.get_article(article_id))
    assert ok is True
    log = lib.list_content_refetch_log()
    assert len(log) == 1
    assert log[0]["source"] == "wayback"


def test_backfill_non_medium_host_skips_tier_entirely(lib, monkeypatch):
    """A non-Medium-platform-host article must never even call
    medium_platform.find_medium_candidate."""
    article_id = _seed(lib, url="https://example.com/normal-post", title="Normal")
    monkeypatch.setattr(extract_mod, "fetch_page",
                        lambda url: PageData(title="", content="", fetch_error="HTTP 500"))
    called = []
    monkeypatch.setattr(mp_mod, "find_medium_candidate",
                        lambda lib_, title, author="": called.append(1) or ("", ""))
    monkeypatch.setattr(wayback_mod, "find_snapshot_verbose", lambda url: (None, "no snapshot archived"))

    ok, reason = pl.backfill_article_content(lib, lib.get_article(article_id))
    assert ok is False
    assert called == []


def test_backfill_medium_host_no_title_skips_tier(lib, monkeypatch):
    """No title to search Exa with -> Medium tier is skipped outright,
    straight to Wayback, without ever calling find_medium_candidate."""
    article_id = _seed(lib, url="https://medium.com/@vc/no-title-post", title="")
    monkeypatch.setattr(extract_mod, "fetch_page",
                        lambda url: PageData(title="", content="", fetch_error="HTTP 403"))
    called = []
    monkeypatch.setattr(mp_mod, "find_medium_candidate",
                        lambda lib_, title, author="": called.append(1) or ("", ""))
    monkeypatch.setattr(wayback_mod, "find_snapshot_verbose", lambda url: (None, "no snapshot archived"))

    ok, reason = pl.backfill_article_content(lib, lib.get_article(article_id))
    assert ok is False
    assert called == []


def test_migration_tier_tried_before_medium_tier_when_both_could_apply(lib, monkeypatch):
    """Domain-migration is checked first in _finish_backfill_after_direct_failure
    — a URL matching _DOMAIN_MIGRATIONS must never reach the Medium tier at
    all (this is a documentation/ordering guard, not expected to happen in
    practice since the two domain sets don't overlap)."""
    article_id = _seed(lib, url="https://pointsandfigures.com/x/", title="A Post")
    monkeypatch.setattr(extract_mod, "fetch_page",
                        lambda url: PageData(title="", content="", fetch_error="HTTP 403"))
    monkeypatch.setattr(dm_mod, "find_migrated_url",
                        lambda lib_, domain, title: "https://jeffreycarter.substack.com/p/x")
    medium_called = []
    monkeypatch.setattr(mp_mod, "find_medium_candidate",
                        lambda lib_, title, author="": medium_called.append(1) or ("", ""))

    migrated_html = ("<html><body><article>" +
                     "<p>Migrated content, definitely long enough to pass the sanity check.</p>" * 15 +
                     "</article></body></html>")
    migrated_page = PageData(title="A Post", content="Migrated content, plenty of real words here. " * 20,
                             blocked=False, raw_html=migrated_html)

    def _fetch_page(url):
        if "jeffreycarter.substack.com" in url:
            return migrated_page
        return PageData(title="", content="", fetch_error="HTTP 403")
    monkeypatch.setattr(extract_mod, "fetch_page", _fetch_page)

    ok, reason = pl.backfill_article_content(lib, lib.get_article(article_id))
    assert ok is True
    assert medium_called == [], "migration tier success must short-circuit before the Medium tier is ever tried"


# ---------------------------------------------------------------------------
# Fetch-by-URL tier (2026-08 wrap-up sprint item 1) — tried before search
# ---------------------------------------------------------------------------

def test_is_recognized_blocked_host_covers_medium_and_other_hosts():
    assert mp_mod.is_recognized_blocked_host("https://medium.com/@a/post")
    assert mp_mod.is_recognized_blocked_host("https://www.shockwaveinnovations.com/blog/x")
    assert not mp_mod.is_recognized_blocked_host("https://example.com/post")
    assert not mp_mod.is_recognized_blocked_host("")


def test_fetch_content_by_url_no_api_key(monkeypatch):
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    assert mp_mod.fetch_content_by_url(None, "https://medium.com/@a/post") == ""


def test_fetch_content_by_url_no_url(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "fake-key")
    assert mp_mod.fetch_content_by_url(None, "") == ""


def test_fetch_content_by_url_returns_text_and_hits_contents_endpoint(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "fake-key")
    captured = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"results": [{"url": "https://medium.com/@a/post", "text": "Real article text."}]}

    def _post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return _Resp()

    monkeypatch.setattr(mp_mod.requests, "post", _post)
    text = mp_mod.fetch_content_by_url(None, "https://medium.com/@a/post")
    assert text == "Real article text."
    assert captured["url"] == mp_mod._EXA_CONTENTS_URL
    assert captured["json"]["urls"] == ["https://medium.com/@a/post"]


def test_fetch_content_by_url_no_text_returns_empty(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "fake-key")

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"results": [{"url": "https://medium.com/@a/post", "text": ""}]}

    monkeypatch.setattr(mp_mod.requests, "post", lambda *a, **kw: _Resp())
    assert mp_mod.fetch_content_by_url(None, "https://medium.com/@a/post") == ""


def test_fetch_content_by_url_network_error_returns_empty(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "fake-key")

    def _raise(*a, **kw):
        raise ConnectionError("boom")

    monkeypatch.setattr(mp_mod.requests, "post", _raise)
    assert mp_mod.fetch_content_by_url(None, "https://medium.com/@a/post") == ""


def test_backfill_fetch_by_url_success_skips_search_and_wayback(lib, monkeypatch):
    """A recognized blocked-host article whose current URL Exa can fetch
    directly must be accepted on the word-count floor alone — no title
    search, no Wayback, exactly one log row, source='medium-fetch'."""
    article_id = _seed(lib, url="https://medium.com/both-sides-of-the-table/some-post-abc123",
                       title="Some Post", author="Mark Suster")
    monkeypatch.setattr(extract_mod, "fetch_page",
                        lambda url: PageData(title="", content="", fetch_error="HTTP 403"))

    fetch_text = ("Real opening paragraph with plenty of words to clear the minimum "
                  "content threshold comfortably. " * 10)
    monkeypatch.setattr(mp_mod, "fetch_content_by_url", lambda lib_, url: fetch_text)

    search_called = []
    monkeypatch.setattr(mp_mod, "find_medium_candidate",
                        lambda lib_, title, author="": search_called.append(1) or ("", ""))
    wayback_called = []
    monkeypatch.setattr(wayback_mod, "find_snapshot_verbose",
                        lambda url: wayback_called.append(url) or (None, "unreached"))

    ok, reason = pl.backfill_article_content(lib, lib.get_article(article_id))
    assert ok is True
    assert search_called == [], "must not fall through to search-by-title once fetch-by-URL succeeded"
    assert wayback_called == []

    log = lib.list_content_refetch_log()
    assert len(log) == 1
    assert log[0]["status"] == "success"
    assert log[0]["source"] == "medium-fetch"
    assert "both-sides-of-the-table" in log[0]["detail"]

    row = lib.get_article(article_id)
    assert "Real opening paragraph" in row["content_html"]


def test_backfill_fetch_by_url_thin_result_falls_through_to_search(lib, monkeypatch):
    """A fetch-by-URL result that's too short to clear the word-count floor
    must fall through to search-by-title, not be accepted or treated as a
    final miss."""
    article_id = _seed(lib, url="https://medium.com/@vc/some-post", title="Some Post")
    monkeypatch.setattr(extract_mod, "fetch_page",
                        lambda url: PageData(title="", content="", fetch_error="HTTP 403"))
    monkeypatch.setattr(mp_mod, "fetch_content_by_url", lambda lib_, url: "Too short.")
    monkeypatch.setattr(mp_mod, "find_medium_candidate",
                        lambda lib_, title, author="": ("https://techcrunch.com/some-post", ""))

    candidate_html = ("<html><body><article>" +
                      "<p>Candidate content, definitely long enough to pass the sanity check.</p>" * 15 +
                      "</article></body></html>")
    candidate_page = PageData(title="Some Post",
                              content="Candidate content, plenty of real words here. " * 20,
                              blocked=False, raw_html=candidate_html)

    def _fetch_page(url):
        if "techcrunch.com" in url:
            return candidate_page
        return PageData(title="", content="", fetch_error="HTTP 403")
    monkeypatch.setattr(extract_mod, "fetch_page", _fetch_page)

    ok, reason = pl.backfill_article_content(lib, lib.get_article(article_id))
    assert ok is True
    log = lib.list_content_refetch_log()
    assert len(log) == 1
    assert log[0]["source"] == "medium-search"


def test_backfill_fetch_by_url_miss_falls_through_to_search(lib, monkeypatch):
    """No result at all from fetch-by-URL falls through to search-by-title,
    same as a thin result."""
    article_id = _seed(lib, url="https://medium.com/@vc/some-post", title="Some Post")
    monkeypatch.setattr(extract_mod, "fetch_page",
                        lambda url: PageData(title="", content="", fetch_error="HTTP 403"))
    monkeypatch.setattr(mp_mod, "fetch_content_by_url", lambda lib_, url: "")
    monkeypatch.setattr(mp_mod, "find_medium_candidate",
                        lambda lib_, title, author="": ("https://medium.com/@other/some-post-xyz", ""))
    monkeypatch.setattr(wayback_mod, "find_snapshot_verbose", lambda url: (None, "no snapshot archived"))

    ok, reason = pl.backfill_article_content(lib, lib.get_article(article_id))
    assert ok is False  # too-thin exa-text candidate, same as the pre-existing test above


def test_backfill_fetch_by_url_tried_even_with_no_title(lib, monkeypatch):
    """Fetch-by-URL needs no title (there's no candidate to search for) —
    a title-less article must still get the fetch-by-URL attempt."""
    article_id = _seed(lib, url="https://medium.com/@vc/no-title-post", title="")
    monkeypatch.setattr(extract_mod, "fetch_page",
                        lambda url: PageData(title="", content="", fetch_error="HTTP 403"))

    fetch_calls = []
    fetch_text = "Real opening paragraph with plenty of words. " * 15

    def _fetch_by_url(lib_, url):
        fetch_calls.append(url)
        return fetch_text
    monkeypatch.setattr(mp_mod, "fetch_content_by_url", _fetch_by_url)
    search_called = []
    monkeypatch.setattr(mp_mod, "find_medium_candidate",
                        lambda lib_, title, author="": search_called.append(1) or ("", ""))

    ok, reason = pl.backfill_article_content(lib, lib.get_article(article_id))
    assert ok is True
    assert fetch_calls == ["https://medium.com/@vc/no-title-post"]
    assert search_called == [], "fetch-by-URL succeeded, no need to fall through to search"


def test_backfill_non_medium_host_skips_fetch_by_url_too(lib, monkeypatch):
    """A host outside is_recognized_blocked_host must never even call
    fetch_content_by_url, same as it already never calls
    find_medium_candidate."""
    article_id = _seed(lib, url="https://example.com/normal-post", title="Normal")
    monkeypatch.setattr(extract_mod, "fetch_page",
                        lambda url: PageData(title="", content="", fetch_error="HTTP 500"))
    fetch_called = []
    monkeypatch.setattr(mp_mod, "fetch_content_by_url",
                        lambda lib_, url: fetch_called.append(1) or "")
    monkeypatch.setattr(wayback_mod, "find_snapshot_verbose", lambda url: (None, "no snapshot archived"))

    ok, reason = pl.backfill_article_content(lib, lib.get_article(article_id))
    assert ok is False
    assert fetch_called == []


def test_backfill_shockwave_host_uses_fetch_by_url_tier(lib, monkeypatch):
    """shockwaveinnovations.com is not Medium, but IS a recognized blocked
    host — it must reach the same fetch-by-URL tier."""
    article_id = _seed(lib, url="https://www.shockwaveinnovations.com/blog/some-post",
                       title="Some Post")
    monkeypatch.setattr(extract_mod, "fetch_page",
                        lambda url: PageData(title="", content="", fetch_error="HTTP 403"))
    fetch_text = "Real opening paragraph with plenty of words to clear the floor. " * 10
    monkeypatch.setattr(mp_mod, "fetch_content_by_url", lambda lib_, url: fetch_text)

    ok, reason = pl.backfill_article_content(lib, lib.get_article(article_id))
    assert ok is True
    log = lib.list_content_refetch_log()
    assert log[0]["source"] == "medium-fetch"


def test_count_medium_fetch_content_latest_attempt_only(lib):
    a1 = _seed(lib, url="https://medium.com/@a/one", title="One")
    a2 = _seed(lib, url="https://medium.com/@a/two", title="Two")
    lib.log_content_refetch_attempt(a1, "success", source="medium-fetch")
    lib.log_content_refetch_attempt(a2, "success", source="medium-search")
    assert lib.count_medium_fetch_content() == 1

    lib.log_content_refetch_attempt(a1, "success", source="direct")
    assert lib.count_medium_fetch_content() == 0


def test_admin_page_shows_via_medium_fetch_badge(env):
    lib = env._lib()
    a = _seed(lib, url="https://medium.com/@a/one", title="Medium One")
    lib.log_content_refetch_attempt(a, "success", source="medium-fetch", detail="https://medium.com/@a/one")
    lib.close()

    c = _admin_client(env)
    r = c.get("/admin/library/backfill-content")
    assert "via Medium fetch" in r.text


def test_count_medium_search_content_latest_attempt_only(lib):
    a1 = _seed(lib, url="https://medium.com/@a/one", title="One")
    a2 = _seed(lib, url="https://medium.com/@a/two", title="Two")
    lib.log_content_refetch_attempt(a1, "success", source="medium-search")
    lib.log_content_refetch_attempt(a2, "success", source="direct")
    assert lib.count_medium_search_content() == 1

    lib.log_content_refetch_attempt(a1, "success", source="direct")
    assert lib.count_medium_search_content() == 0


# ---------------------------------------------------------------------------
# Regression: defunct-service behavior is unchanged
# ---------------------------------------------------------------------------

def test_defunct_service_still_skips_medium_tier(lib, monkeypatch):
    article_id = _seed(lib, url="http://feedproxy.google.com/~r/AVc/~3/z/")
    fetch_called = []
    monkeypatch.setattr(extract_mod, "fetch_page", lambda url: fetch_called.append(url) or PageData("", ""))
    medium_called = []
    monkeypatch.setattr(mp_mod, "find_medium_candidate",
                        lambda lib_, title, author="": medium_called.append(1) or ("", ""))

    ok, reason = pl.backfill_article_content(lib, lib.get_article(article_id))
    assert ok is False
    assert reason == "defunct-service"
    assert fetch_called == []
    assert medium_called == []


# ---------------------------------------------------------------------------
# Library.articles_needing_content_backfill — host_suffixes scoping
# ---------------------------------------------------------------------------

def test_host_suffixes_scopes_to_matching_articles_only(lib):
    medium_id = _seed(lib, url="https://medium.com/@a/one", title="Medium One")
    other_id = _seed(lib, url="https://example.com/normal", title="Normal")

    scoped_ids = [r["id"] for r in lib.articles_needing_content_backfill(host_suffixes=["medium.com"])]
    assert medium_id in scoped_ids
    assert other_id not in scoped_ids


def test_host_suffixes_matches_subdomain_hosts(lib):
    sub_id = _seed(lib, url="https://pt.medium.com/some-post", title="Sub One")
    scoped_ids = [r["id"] for r in lib.articles_needing_content_backfill(host_suffixes=["medium.com"])]
    assert sub_id in scoped_ids


def test_host_suffixes_bypasses_manual_review_exclusion_for_matching_hosts(lib):
    """The whole point of host-scoping: re-reach articles stuck in manual
    review because the new tier didn't exist when they were last tried."""
    medium_id = _seed(lib, url="https://medium.com/@a/flagged", title="Flagged")
    for _ in range(Library._MANUAL_REVIEW_ATTEMPT_THRESHOLD):
        lib.log_content_refetch_attempt(medium_id, "failure", reason="bot-challenge")
    assert medium_id in lib._manual_review_article_ids()

    default_ids = [r["id"] for r in lib.articles_needing_content_backfill()]
    assert medium_id not in default_ids, "still excluded from the default unscoped run"

    scoped_ids = [r["id"] for r in lib.articles_needing_content_backfill(host_suffixes=["medium.com"])]
    assert medium_id in scoped_ids, "host-scoped run must bypass the manual-review exclusion"


def test_host_suffixes_still_excludes_defunct_service(lib):
    """Defunct-service exclusion is NOT bypassed by host-scoping — a
    confirmed-dead host can't be un-dead by narrowing the run to it."""
    defunct_id = _seed(lib, url="http://feedproxy.google.com/~r/x/", title="Dead")
    lib.log_content_refetch_attempt(defunct_id, "failure", reason="defunct-service")

    scoped_ids = [r["id"] for r in lib.articles_needing_content_backfill(host_suffixes=["feedproxy.google.com"])]
    assert defunct_id not in scoped_ids


def test_host_suffixes_none_falls_back_to_default_scope(lib):
    medium_id = _seed(lib, url="https://medium.com/@a/one", title="Medium One")
    other_id = _seed(lib, url="https://example.com/normal", title="Normal")
    ids = [r["id"] for r in lib.articles_needing_content_backfill(host_suffixes=None)]
    assert medium_id in ids and other_id in ids


# ---------------------------------------------------------------------------
# Admin page — "via Medium search" badge and host-scope form field
# ---------------------------------------------------------------------------

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


def test_admin_page_shows_via_medium_search_badge(env):
    lib = env._lib()
    a = _seed(lib, url="https://medium.com/@a/one", title="Medium One")
    lib.log_content_refetch_attempt(a, "success", source="medium-search",
                                    detail="https://techcrunch.com/one")
    lib.close()

    c = _admin_client(env)
    r = c.get("/admin/library/backfill-content")
    assert "via Medium search" in r.text


def test_admin_page_has_host_scope_input(env):
    c = _admin_client(env)
    r = c.get("/admin/library/backfill-content")
    assert 'name="host_scope"' in r.text


def test_admin_backfill_start_parses_host_scope_into_suffix_list(env, monkeypatch):
    captured = {}

    def _fake_job(limit, force, host_suffixes=None):
        captured["limit"] = limit
        captured["force"] = force
        captured["host_suffixes"] = host_suffixes

    monkeypatch.setattr(env, "_content_backfill_job", _fake_job)
    c = _admin_client(env)
    resp = c.post("/admin/library/backfill-content/start",
                  data={"limit": "10", "host_scope": "medium.com, bothsidesofthetable.com"},
                  follow_redirects=False)
    assert resp.status_code == 303

    import time as _time
    for _ in range(20):
        if captured:
            break
        _time.sleep(0.05)
    assert captured.get("host_suffixes") == ["medium.com", "bothsidesofthetable.com"]


def test_admin_backfill_start_blank_host_scope_is_none(env, monkeypatch):
    captured = {}

    def _fake_job(limit, force, host_suffixes=None):
        captured["host_suffixes"] = host_suffixes

    monkeypatch.setattr(env, "_content_backfill_job", _fake_job)
    c = _admin_client(env)
    c.post("/admin/library/backfill-content/start", data={"limit": "10"}, follow_redirects=False)

    import time as _time
    for _ in range(20):
        if "host_suffixes" in captured:
            break
        _time.sleep(0.05)
    assert captured.get("host_suffixes") is None
