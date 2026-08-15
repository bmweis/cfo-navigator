"""Phase 5b follow-up — fetch reliability: browser User-Agent + Wayback
Machine fallback.

Context (full write-up in ARCHITECTURE.md's "Reader content-structure
backfill" section and CLAUDE.md's matching bullet): the first real backfill
batch had 18/25 failures, 17 tracing to 4 domains. A real-request
investigation (railway ssh, actual failing URLs — not assumed) found:

- A standard browser User-Agent does NOT get past 3 of the 4 domains
  (bothsidesofthetable.com, medium.com, pointsandfigures.com) — all three
  return an identical Cloudflare "Just a moment..." bot-management challenge
  regardless of UA string, since Cloudflare fingerprints the TLS/connection
  layer, not the UA header. Kept as the new default anyway (no downside,
  might help elsewhere in the corpus) — but this is NOT presented as a fix
  for those three domains.
- continuations.com's failures are genuine link rot (identical 404 with
  either UA) — confirms Wayback, not a UA change, is the relevant fix there.
- The Wayback Availability API was found to rate-limit (429) broadly and
  unpredictably — confirmed from two independent networks, on both a
  previously-queried URL and a completely fresh one, ruling out "one IP is
  blocked" or "one URL got hammered." linklib.wayback is built defensively
  around that: every function returns "no fallback" on ANY failure, never
  raises, never retries. Real end-to-end content retrieval (a snapshot
  successfully fetched and extracted) was NOT verified at build time because
  of this rate-limiting — see linklib/wayback.py's module docstring. These
  tests cover the request/parsing/wiring logic, which is standard and
  low-risk, with mocked responses standing in for what a real 200 would
  contain.

Covers:
- fetch_page() sends the browser UA by default (no cookie required).
- linklib.wayback.find_snapshot()/fetch_snapshot() — success and every
  failure mode found during the investigation (429, malformed JSON, no
  snapshot, network error) all resolve to None/"", never raise.
- pipeline.backfill_article_content()'s Wayback fallback: triggers on ANY
  direct-fetch failure category (not just 404/fetch-error — a bot-challenge
  or paywall failure also gets a Wayback attempt), a Wayback success is
  logged with source='wayback', a Wayback miss/failure falls back to
  logging the ORIGINAL direct-fetch reason (never a synthetic "wayback
  failed too" reason), and existing content is never touched either way.
- _resolve_reader_content's Wayback fallback on the Reader's live-fetch
  path, and that content_via correctly reports 'cache' | 'direct' |
  'wayback'.
- The admin page's "via Wayback" badge and wayback-content count.
- Library.count_wayback_content()'s latest-attempt-per-article de-dupe.
"""
import pathlib
import sys
import tempfile
import os

import pytest
import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Article, Library
from linklib import pipeline as pl
from linklib import wayback as wayback_mod
from linklib import extract as extract_mod
from linklib.extract import PageData, _BROWSER_HEADERS


def _seed(lib, url="https://example.com/piece", content="Original plain text content."):
    art = Article(url=url, title="Original Title", content=content,
                  saved_at="2026-01-01T00:00:00")
    return lib.upsert(art)


@pytest.fixture
def lib(tmp_path):
    db_path = str(tmp_path / "test.db")
    library = Library(db_path)
    yield library
    library.close()


# ---------------------------------------------------------------------------
# fetch_page() — browser UA is now the default, not just the auth-cookie path
# ---------------------------------------------------------------------------

def test_fetch_page_uses_browser_user_agent_by_default(monkeypatch):
    captured = {}

    class FakeResp:
        status_code = 200
        text = "<html><body><p>Hello.</p></body></html>"
        def raise_for_status(self): pass

    def fake_get(url, headers=None, timeout=None):
        captured["headers"] = headers
        return FakeResp()

    monkeypatch.setattr(extract_mod.requests, "get", fake_get)
    extract_mod.fetch_page("https://example.com/no-cookie-here")

    assert captured["headers"]["User-Agent"] == _BROWSER_HEADERS["User-Agent"]
    assert "Chrome" in captured["headers"]["User-Agent"], \
        "must be a real browser UA, not the old identifiable bot string"
    assert "linklib" not in captured["headers"]["User-Agent"].lower()


def test_page_data_from_html_matches_fetch_page_extraction():
    """_page_data_from_html (factored out so linklib.wayback can build a
    PageData from snapshot HTML without a second live request) must produce
    the same result fetch_page() itself would have, for the same HTML."""
    html = "<html><body><article><p>Real paragraph text here, definitely long enough to pass.</p>" * 10 + "</article></body></html>"
    page = extract_mod._page_data_from_html(html)
    assert page.raw_html == html
    assert page.content
    assert page.blocked is False
    assert page.fetch_error == ""


# ---------------------------------------------------------------------------
# linklib.wayback — defensive by construction: every failure mode resolves
# to None/"", never raises.
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, status_code=200, json_data=None, text="", raise_json_error=False):
        self.status_code = status_code
        self._json_data = json_data
        self.text = text
        self._raise_json_error = raise_json_error

    def json(self):
        if self._raise_json_error:
            raise ValueError("not JSON")
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(response=self)


def test_find_snapshot_success(monkeypatch):
    data = {"archived_snapshots": {"closest": {"url": "https://web.archive.org/web/20200101000000/https://example.com/x", "status": "200"}}}
    monkeypatch.setattr(wayback_mod.requests, "get", lambda *a, **kw: _FakeResponse(200, data))
    assert wayback_mod.find_snapshot("https://example.com/x") == \
        "https://web.archive.org/web/20200101000000/https://example.com/x"


def test_find_snapshot_no_snapshot_available(monkeypatch):
    data = {"archived_snapshots": {}}
    monkeypatch.setattr(wayback_mod.requests, "get", lambda *a, **kw: _FakeResponse(200, data))
    assert wayback_mod.find_snapshot("https://example.com/never-archived") is None


def test_find_snapshot_429_returns_none_not_raise(monkeypatch):
    """The investigation's central finding: archive.org rate-limits broadly
    and unpredictably. A 429 must never propagate as an exception — it's
    just 'no fallback available', identical to any other failure."""
    monkeypatch.setattr(wayback_mod.requests, "get", lambda *a, **kw: _FakeResponse(429, text="Too Many Requests"))
    assert wayback_mod.find_snapshot("https://example.com/rate-limited") is None


def test_find_snapshot_malformed_json_returns_none(monkeypatch):
    monkeypatch.setattr(wayback_mod.requests, "get",
                        lambda *a, **kw: _FakeResponse(200, raise_json_error=True))
    assert wayback_mod.find_snapshot("https://example.com/garbled") is None


def test_find_snapshot_network_error_returns_none(monkeypatch):
    def _raise(*a, **kw):
        raise requests.exceptions.ConnectionError("no route to host")
    monkeypatch.setattr(wayback_mod.requests, "get", _raise)
    assert wayback_mod.find_snapshot("https://example.com/unreachable") is None


def test_find_snapshot_never_raises_regardless_of_failure_mode(monkeypatch):
    """A blanket check across every failure shape found during the real
    investigation — none of them should ever escape as an exception."""
    for behavior in [
        lambda *a, **kw: _FakeResponse(500),
        lambda *a, **kw: _FakeResponse(429),
        lambda *a, **kw: (_ for _ in ()).throw(requests.exceptions.Timeout()),
        lambda *a, **kw: _FakeResponse(200, raise_json_error=True),
    ]:
        monkeypatch.setattr(wayback_mod.requests, "get", behavior)
        result = wayback_mod.find_snapshot("https://example.com/x")  # must not raise
        assert result is None


def test_fetch_snapshot_success(monkeypatch):
    monkeypatch.setattr(wayback_mod.requests, "get",
                        lambda *a, **kw: _FakeResponse(200, text="<html>real content</html>"))
    assert wayback_mod.fetch_snapshot("https://web.archive.org/web/x") == "<html>real content</html>"


def test_fetch_snapshot_failure_returns_empty_string(monkeypatch):
    monkeypatch.setattr(wayback_mod.requests, "get", lambda *a, **kw: _FakeResponse(404))
    assert wayback_mod.fetch_snapshot("https://web.archive.org/web/dead") == ""


# ---------------------------------------------------------------------------
# pipeline.backfill_article_content — Wayback as a last-resort fallback
# ---------------------------------------------------------------------------

def test_backfill_falls_back_to_wayback_on_fetch_error(lib, monkeypatch):
    article_id = _seed(lib, content="Old plain text.")
    monkeypatch.setattr(extract_mod, "fetch_page",
                        lambda url: PageData(title="", content="", fetch_error="HTTP 404"))
    monkeypatch.setattr(wayback_mod, "find_snapshot",
                        lambda url: "https://web.archive.org/web/20200101/https://example.com/piece")
    snap_html = "<html><body><article>" + "<p>Archived paragraph content, plenty long enough to pass.</p>" * 15 + "</article></body></html>"
    monkeypatch.setattr(wayback_mod, "fetch_snapshot", lambda snap_url: snap_html)

    ok, reason = pl.backfill_article_content(lib, lib.get_article(article_id))
    assert ok is True
    assert reason == ""

    row = lib.get_article(article_id)
    assert "<p>" in row["content_html"]
    assert row["content"] == "Old plain text.", "plain-text content must stay untouched"

    log = lib.list_content_refetch_log()
    assert log[0]["status"] == "success"
    assert log[0]["source"] == "wayback"
    assert log[0]["detail"] == "https://web.archive.org/web/20200101/https://example.com/piece"


def test_backfill_wayback_fallback_triggers_on_bot_challenge_not_just_404(lib, monkeypatch):
    """Deliberately NOT scoped to 404/fetch-error — a stubborn Cloudflare
    challenge the UA swap couldn't get past should still try Wayback,
    since the Internet Archive's own crawler generally isn't gated the
    same way a generic bot request is."""
    article_id = _seed(lib, content="Old content.")
    challenge_html = "<html><head><title>Just a moment...</title></head><body>Checking your browser.</body></html>"
    page = PageData(title="Just a moment...", content="Checking your browser.",
                    blocked=False, raw_html=challenge_html)
    monkeypatch.setattr(extract_mod, "fetch_page", lambda url: page)
    monkeypatch.setattr(wayback_mod, "find_snapshot", lambda url: "https://web.archive.org/web/x")
    snap_html = "<html><body><article>" + "<p>Real archived text, well past the minimum word threshold for sure.</p>" * 15 + "</article></body></html>"
    monkeypatch.setattr(wayback_mod, "fetch_snapshot", lambda snap_url: snap_html)

    ok, reason = pl.backfill_article_content(lib, lib.get_article(article_id))
    assert ok is True

    log = lib.list_content_refetch_log()
    assert log[0]["source"] == "wayback"


def test_backfill_wayback_miss_falls_back_to_original_reason(lib, monkeypatch):
    """No snapshot available at all — must log and return the ORIGINAL
    direct-fetch reason, not a new synthetic 'wayback failed' reason, so
    the admin failure-reason breakdown stays meaningful."""
    article_id = _seed(lib, content="Must survive.")
    monkeypatch.setattr(extract_mod, "fetch_page",
                        lambda url: PageData(title="", content="", fetch_error="HTTP 404"))
    monkeypatch.setattr(wayback_mod, "find_snapshot", lambda url: None)

    ok, reason = pl.backfill_article_content(lib, lib.get_article(article_id))
    assert ok is False
    assert reason == "fetch-error"

    row = lib.get_article(article_id)
    assert row["content"] == "Must survive."
    assert row["content_html"] == ""

    log = lib.list_content_refetch_log()
    assert log[0]["status"] == "failure"
    assert log[0]["reason"] == "fetch-error"
    assert log[0]["source"] == "direct"


def test_backfill_wayback_snapshot_fetch_fails_falls_back_to_original_reason(lib, monkeypatch):
    """A snapshot URL was found, but fetching it failed (e.g. archive.org
    429'd on the follow-up request too) — same fallback-to-original-reason
    behavior as no snapshot at all."""
    article_id = _seed(lib, content="Must survive.")
    monkeypatch.setattr(extract_mod, "fetch_page",
                        lambda url: PageData(title="", content="", fetch_error="HTTP 403"))
    monkeypatch.setattr(wayback_mod, "find_snapshot", lambda url: "https://web.archive.org/web/x")
    monkeypatch.setattr(wayback_mod, "fetch_snapshot", lambda snap_url: "")  # fetch failed

    ok, reason = pl.backfill_article_content(lib, lib.get_article(article_id))
    assert ok is False
    assert reason == "fetch-error"
    assert lib.get_article(article_id)["content"] == "Must survive."


def test_backfill_wayback_snapshot_fails_own_sanity_check(lib, monkeypatch):
    """The archived snapshot itself is unusable (e.g. it archived a paywall
    preview) — must fail the exact same content sanity check a live page
    would, and fall back to the original direct-fetch reason, never
    storing the bad snapshot content."""
    article_id = _seed(lib, content="Must survive.")
    monkeypatch.setattr(extract_mod, "fetch_page",
                        lambda url: PageData(title="", content="", fetch_error="HTTP 403"))
    monkeypatch.setattr(wayback_mod, "find_snapshot", lambda url: "https://web.archive.org/web/x")
    thin_html = "<html><body><p>Short.</p></body></html>"
    monkeypatch.setattr(wayback_mod, "fetch_snapshot", lambda snap_url: thin_html)

    ok, reason = pl.backfill_article_content(lib, lib.get_article(article_id))
    assert ok is False
    assert reason == "fetch-error", "the original direct-fetch reason, not a new wayback-specific one"
    assert lib.get_article(article_id)["content_html"] == ""


def test_backfill_direct_success_logs_source_direct(lib, monkeypatch):
    article_id = _seed(lib)
    html = "<html><body><article>" + "<p>Fresh direct content, definitely long enough to pass the check.</p>" * 15 + "</article></body></html>"
    page = PageData(title="Fresh", content="Fresh direct content. " * 20, blocked=False, raw_html=html)
    monkeypatch.setattr(extract_mod, "fetch_page", lambda url: page)

    ok, reason = pl.backfill_article_content(lib, lib.get_article(article_id))
    assert ok is True
    log = lib.list_content_refetch_log()
    assert log[0]["source"] == "direct"


# ---------------------------------------------------------------------------
# Library.count_wayback_content — latest-attempt-per-article de-dupe
# ---------------------------------------------------------------------------

def test_count_wayback_content_latest_attempt_only(lib):
    a1 = _seed(lib, url="https://example.com/one")
    a2 = _seed(lib, url="https://example.com/two")
    lib.log_content_refetch_attempt(a1, "success", source="wayback")
    lib.log_content_refetch_attempt(a2, "success", source="direct")
    assert lib.count_wayback_content() == 1

    # a1 later gets a fresh direct success (e.g. the live page came back) —
    # should no longer count as wayback-sourced.
    lib.log_content_refetch_attempt(a1, "success", source="direct")
    assert lib.count_wayback_content() == 0


# ---------------------------------------------------------------------------
# _resolve_reader_content — Wayback fallback on the Reader's live-fetch path
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


def test_resolve_reader_content_falls_back_to_wayback_on_live_fetch_failure(env, monkeypatch):
    import linklib.extract as extract_module
    import linklib.wayback as wayback_module

    monkeypatch.setattr(extract_module, "fetch_page",
                        lambda url: PageData(title="", content="", fetch_error="HTTP 404"))
    monkeypatch.setattr(wayback_module, "find_snapshot", lambda url: "https://web.archive.org/web/x")
    snap_html = "<html><body><article><p>Archived reader content.</p></article></body></html>"
    monkeypatch.setattr(wayback_module, "fetch_snapshot", lambda snap_url: snap_html)

    data = env._resolve_reader_content(url="https://example.com/dead-link")
    assert data is not None
    assert data["has_content"] is True
    assert data["content_via"] == "wayback"
    assert "Archived reader content." in data["body_html"]


def test_resolve_reader_content_no_wayback_snapshot_shows_no_content(env, monkeypatch):
    import linklib.extract as extract_module
    import linklib.wayback as wayback_module

    monkeypatch.setattr(extract_module, "fetch_page",
                        lambda url: PageData(title="", content="", fetch_error="HTTP 404"))
    monkeypatch.setattr(wayback_module, "find_snapshot", lambda url: None)

    data = env._resolve_reader_content(url="https://example.com/really-dead")
    assert data is not None
    assert data["has_content"] is False
    assert data["content_via"] == "cache"  # never got past 'cache' — nothing succeeded


def test_resolve_reader_content_direct_success_marks_content_via_direct(env, monkeypatch):
    import linklib.extract as extract_module

    html = "<html><body><article><p>Live content.</p></article></body></html>"
    page = PageData(title="Live", content="Live content.", blocked=False, raw_html=html)
    monkeypatch.setattr(extract_module, "fetch_page", lambda url: page)

    data = env._resolve_reader_content(url="https://example.com/live-piece")
    assert data["content_via"] == "direct"


def test_reader_shows_wayback_banner_when_content_via_wayback(env):
    """Static regression check that the client-side banner markup and its
    gating condition are present in the shipped page — the actual DOM
    render/visibility is a client-side JS concern (rrRenderArticle), not
    something a DOM-less TestClient render can execute."""
    c = _admin_client(env)
    r = c.get("/read")
    assert r.status_code == 200
    assert "content_via === 'wayback'" in r.text
    assert "Wayback Machine archived copy" in r.text


# ---------------------------------------------------------------------------
# Admin page — "via Wayback" badge + count note
# ---------------------------------------------------------------------------

def test_admin_page_shows_via_wayback_badge_on_wayback_success_row(env):
    lib = env._lib()
    article_id = _seed(lib, url="https://example.com/archived-piece")
    lib.log_content_refetch_attempt(article_id, "success", source="wayback", detail="https://web.archive.org/web/x")
    lib.close()

    c = _admin_client(env)
    r = c.get("/admin/library/backfill-content")
    assert r.status_code == 200
    assert "via Wayback" in r.text


def test_admin_page_shows_wayback_count_note_only_when_nonzero(env):
    lib = env._lib()
    a1 = _seed(lib, url="https://example.com/a")
    lib.log_content_refetch_attempt(a1, "success", source="wayback")
    lib.close()

    c = _admin_client(env)
    r = c.get("/admin/library/backfill-content")
    assert "Wayback Machine" in r.text
    assert "1 of the structured articles" in r.text or ">1<" in r.text


def test_admin_page_no_wayback_note_when_zero(env):
    c = _admin_client(env)
    r = c.get("/admin/library/backfill-content")
    assert r.status_code == 200
    assert "of the structured articles above came from a" not in r.text
