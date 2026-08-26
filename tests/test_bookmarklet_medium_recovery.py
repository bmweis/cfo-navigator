"""Bookmarklet title/content-extraction fix, Phase 1 (2026-08) — routing
ingest_url and _resolve_reader_content through the existing Medium-platform
Exa recovery tier (linklib/medium_platform.py, linklib.pipeline.
_try_medium_platform) for a recognized Cloudflare-blocked host, the same way
the Reader content backfill already does.

Confirmed via scripts/trace_medium_tier.py against a real stuck production
article (medium.com): is_recognized_blocked_host=True, and a live re-trace
of _try_medium_platform() succeeded (source='medium-fetch', 7,371 chars of
real structured content for the exact URL that had been falling back to
storing its own URL as the title).

Covers:
- linklib.pipeline.medium_recovery — the new thin wrapper: gates on
  is_recognized_blocked_host itself (no Exa call for a non-blocked host),
  returns None on any tier miss, and on a hit returns content_html/content/
  title/candidate_url/source built from the SAME _try_medium_platform the
  backfill job already uses (reused as-is, not reimplemented).
- linklib.pipeline.ingest_url — a save-time direct-fetch failure against a
  recognized blocked host now tries medium_recovery before falling back to
  storing the raw URL as the title; a non-blocked host's failure, or a
  blocked host's own tier miss, is unchanged from the pre-fix behavior.
- webapp._resolve_reader_content — a live-fetch failure against a
  recognized blocked host now tries the same recovery, ordered before the
  existing Wayback fallback, before giving up with "Content could not be
  extracted"; a non-blocked host is unchanged.
"""
import pathlib
import sys
import tempfile
import os

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Library
from linklib import pipeline as pl
from linklib import extract as extract_mod
from linklib import medium_platform as mp_mod
from linklib.extract import PageData

_MEDIUM_URL = "https://medium.com/@fdestin/what-founders-really-want-from-vcs-235d60801d4"
_NORMAL_URL = "https://example.com/some-paywalled-piece"

# What a real Exa contents.text pull looks like for this kind of article:
# the title as a bare Title-Case leading line (no markdown), then real body
# paragraphs — matching paragraphs_html_from_text's own heading heuristic.
_RECOVERED_TEXT = (
    "What Founders Really Want From VCs\n\n"
    "Founders consistently tell us the same thing when we ask what they "
    "actually want from a venture partner beyond the check itself: honest "
    "feedback, fast decisions, and real help finding the next ten "
    "customers.\n\n"
    "They want a partner who responds quickly, tells them the truth even "
    "when it is uncomfortable, and does not disappear the moment the term "
    "sheet is signed. Founders also want a partner who remembers what the "
    "company actually does between board meetings, rather than relearning "
    "the pitch every quarter, and who picks up the phone on a bad week, "
    "not just a good one."
)


@pytest.fixture
def lib(tmp_path):
    db_path = str(tmp_path / "test.db")
    library = Library(db_path)
    yield library
    library.close()


# ---------------------------------------------------------------------------
# medium_recovery
# ---------------------------------------------------------------------------

def test_medium_recovery_skips_non_blocked_host(lib, monkeypatch):
    called = []
    monkeypatch.setattr(mp_mod, "fetch_content_by_url",
                        lambda lib_, url: called.append(url) or "")
    assert pl.medium_recovery(lib, _NORMAL_URL, "Some Title") is None
    assert called == [], "a non-blocked host must never spend an Exa call"


def test_medium_recovery_hit_returns_title_content_and_html(lib, monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "fake-key")
    monkeypatch.setattr(mp_mod, "fetch_content_by_url",
                        lambda lib_, url: _RECOVERED_TEXT)

    result = pl.medium_recovery(lib, _MEDIUM_URL, "")
    assert result is not None
    assert result["source"] == "medium-fetch"
    assert result["candidate_url"] == _MEDIUM_URL
    assert result["title"] == "What Founders Really Want From VCs"
    assert "Founders consistently tell us" in result["content"]
    assert "<h" in result["content_html"] or "<p" in result["content_html"]
    # content is plain text, not raw HTML — the ingest/search/enrichment
    # pipeline's contract (see extract._extract_content's own docstring).
    assert "<p>" not in result["content"] and "<h" not in result["content"]


def test_medium_recovery_miss_returns_none(lib, monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "fake-key")
    monkeypatch.setattr(mp_mod, "fetch_content_by_url", lambda lib_, url: "")
    monkeypatch.setattr(mp_mod, "find_medium_candidate",
                        lambda lib_, title, author="": ("", ""))
    assert pl.medium_recovery(lib, _MEDIUM_URL, "Some Title") is None


# ---------------------------------------------------------------------------
# ingest_url
# ---------------------------------------------------------------------------

def test_ingest_url_recovers_title_and_content_on_blocked_host(lib, monkeypatch):
    """The bug report's exact reproduction: a direct fetch of a medium.com
    URL fails outright (Cloudflare block), and the Medium tier recovers
    both a real title and real content instead of falling back to the raw
    URL and an empty/thin body."""
    monkeypatch.setattr(extract_mod, "fetch_page",
                        lambda url: PageData(title="", content="", fetch_error="HTTP 403"))
    monkeypatch.setenv("EXA_API_KEY", "fake-key")
    monkeypatch.setattr(mp_mod, "fetch_content_by_url",
                        lambda lib_, url: _RECOVERED_TEXT)

    result = pl.ingest_url(lib, _MEDIUM_URL, do_enrich=False)
    article_id = result["id"]

    assert result["title"] == "What Founders Really Want From VCs"
    assert result["title"] != _MEDIUM_URL
    assert "Founders consistently tell us" in result["content"]

    stored = lib.get_article(article_id)
    assert stored["title"] == "What Founders Really Want From VCs"
    assert stored["content_html"], "structured HTML should be stored via set_article_content_html"
    assert stored["needs_content_check"] == 0

    log_rows = lib.list_content_refetch_log()
    matching = [r for r in log_rows if r["article_id"] == article_id]
    assert any(r["status"] == "success" and r["source"] == "medium-fetch" for r in matching)


def test_ingest_url_falls_back_to_url_title_when_medium_tier_also_misses(lib, monkeypatch):
    """Regression: when the Medium tier itself comes up empty, behavior is
    unchanged from before this fix — the raw URL is still the last-resort
    title, and a failure row is still logged with source='save'."""
    monkeypatch.setattr(extract_mod, "fetch_page",
                        lambda url: PageData(title="", content="", fetch_error="HTTP 403"))
    monkeypatch.setenv("EXA_API_KEY", "fake-key")
    monkeypatch.setattr(mp_mod, "fetch_content_by_url", lambda lib_, url: "")
    monkeypatch.setattr(mp_mod, "find_medium_candidate",
                        lambda lib_, title, author="": ("", ""))

    result = pl.ingest_url(lib, _MEDIUM_URL, do_enrich=False)
    article_id = result["id"]

    assert result["title"] == _MEDIUM_URL
    stored = lib.get_article(article_id)
    assert stored["content_html"] == ""
    assert stored["needs_content_check"] == 1
    assert stored["content_check_reason"] == "fetch-error"

    log_rows = lib.list_content_refetch_log()
    matching = [r for r in log_rows if r["article_id"] == article_id]
    assert any(r["status"] == "failure" and r["source"] == "save" for r in matching)
    assert not any(r["source"] in ("medium-fetch", "medium-search") for r in matching)


def test_ingest_url_non_blocked_host_never_calls_medium_tier(lib, monkeypatch):
    """Regression: a non-Medium host's fetch failure (e.g. an ordinary
    paywall) must behave byte-for-byte as before this fix — no Exa call, no
    behavior change."""
    called = []
    monkeypatch.setattr(extract_mod, "fetch_page",
                        lambda url: PageData(title="", content="", fetch_error="HTTP 403"))
    monkeypatch.setattr(mp_mod, "fetch_content_by_url",
                        lambda lib_, url: called.append(url) or "should never be reached")

    result = pl.ingest_url(lib, _NORMAL_URL, do_enrich=False)
    assert called == []
    assert result["title"] == _NORMAL_URL

    stored = lib.get_article(result["id"])
    assert stored["needs_content_check"] == 1


def test_ingest_url_successful_direct_fetch_never_calls_medium_tier(lib, monkeypatch):
    """Regression: when the direct fetch succeeds and passes the quality
    check, the Medium tier must never be consulted at all, blocked host or
    not — this fix only activates on a quality failure."""
    called = []
    good_html = "<html><head><title>Real Title</title></head><body><article>" \
                "<p>" + ("Real content. " * 40) + "</p></article></body></html>"
    monkeypatch.setattr(extract_mod, "fetch_page",
                        lambda url: extract_mod._page_data_from_html(good_html))
    monkeypatch.setattr(mp_mod, "fetch_content_by_url",
                        lambda lib_, url: called.append(url) or "should never be reached")

    result = pl.ingest_url(lib, _MEDIUM_URL, do_enrich=False)
    assert called == []
    assert result["title"] == "Real Title"


# ---------------------------------------------------------------------------
# webapp._resolve_reader_content
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


def test_resolve_reader_content_recovers_on_blocked_host(env, monkeypatch):
    monkeypatch.setattr(extract_mod, "fetch_page",
                        lambda url: PageData(title="", content="", fetch_error="HTTP 403"))
    monkeypatch.setenv("EXA_API_KEY", "fake-key")
    monkeypatch.setattr(mp_mod, "fetch_content_by_url",
                        lambda lib_, url: _RECOVERED_TEXT)

    data = env._resolve_reader_content(url=_MEDIUM_URL)
    assert data is not None
    assert data["has_content"] is True
    assert data["content_via"] == "medium-fetch"
    assert data["title"] == "What Founders Really Want From VCs"
    assert "Founders consistently tell us" in data["body_html"]


def test_resolve_reader_content_medium_tier_before_wayback(env, monkeypatch):
    """Ordering: the Medium tier is tried first, same as
    _finish_backfill_after_direct_failure — a Wayback snapshot lookup that
    would otherwise succeed must never even be reached once the Medium tier
    itself hits."""
    import linklib.wayback as wayback_module

    monkeypatch.setattr(extract_mod, "fetch_page",
                        lambda url: PageData(title="", content="", fetch_error="HTTP 403"))
    monkeypatch.setenv("EXA_API_KEY", "fake-key")
    monkeypatch.setattr(mp_mod, "fetch_content_by_url",
                        lambda lib_, url: _RECOVERED_TEXT)

    wayback_called = []
    monkeypatch.setattr(wayback_module, "find_snapshot",
                        lambda url: wayback_called.append(url) or None)

    data = env._resolve_reader_content(url=_MEDIUM_URL)
    assert data["content_via"] == "medium-fetch"
    assert wayback_called == [], "Wayback should never be reached once the Medium tier hits"


def test_resolve_reader_content_falls_through_to_wayback_when_medium_tier_misses(env, monkeypatch):
    """When the Medium tier itself misses, the existing Wayback fallback
    still runs exactly as before — this fix doesn't remove that safety net,
    just tries a better option first."""
    import linklib.wayback as wayback_module

    monkeypatch.setattr(extract_mod, "fetch_page",
                        lambda url: PageData(title="", content="", fetch_error="HTTP 403"))
    monkeypatch.setenv("EXA_API_KEY", "fake-key")
    monkeypatch.setattr(mp_mod, "fetch_content_by_url", lambda lib_, url: "")
    monkeypatch.setattr(mp_mod, "find_medium_candidate",
                        lambda lib_, title, author="": ("", ""))
    monkeypatch.setattr(wayback_module, "find_snapshot",
                        lambda url: "https://web.archive.org/web/x")
    snap_html = "<html><body><article><p>Archived reader content.</p></article></body></html>"
    monkeypatch.setattr(wayback_module, "fetch_snapshot", lambda snap_url: snap_html)

    data = env._resolve_reader_content(url=_MEDIUM_URL)
    assert data["has_content"] is True
    assert data["content_via"] == "wayback"
    assert "Archived reader content." in data["body_html"]


def test_resolve_reader_content_non_blocked_host_unaffected(env, monkeypatch):
    """Regression: a non-blocked host's live-fetch failure must behave
    byte-for-byte as before this fix (matches the pre-existing
    test_resolve_reader_content_no_wayback_snapshot_shows_no_content case)."""
    called = []
    monkeypatch.setattr(extract_mod, "fetch_page",
                        lambda url: PageData(title="", content="", fetch_error="HTTP 404"))
    monkeypatch.setattr(mp_mod, "fetch_content_by_url",
                        lambda lib_, url: called.append(url) or "should never be reached")

    import linklib.wayback as wayback_module
    monkeypatch.setattr(wayback_module, "find_snapshot", lambda url: None)

    data = env._resolve_reader_content(url=_NORMAL_URL)
    assert called == []
    assert data["has_content"] is False
    assert data["content_via"] == "cache"
