"""Phase 5b — Reader content-structure backfill: reprocess already-saved
articles so extract_reader_html()'s structure (paragraphs, images, links)
applies to the ~4,500 pre-#322 saves, not just live fetches.

Covers:
- linklib.extract.assess_extraction_quality()'s three failure reasons
  (paywall, bot-challenge, too-thin), each asserted independently — in
  particular a DEDICATED test for the bot-challenge marker-list path (HTTP
  200, no paywall markers, so PageData.blocked is False) rather than folding
  it into the paywall test, since it's the one check with no prior track
  record in this codebase.
- linklib.pipeline.backfill_article_content(): happy path, dead-URL failure,
  paywall failure, bot-challenge failure — every failure case leaves the
  article's existing content/content_html untouched and writes a
  content_refetch_log row.
- The admin job's stop control and resumability (articles_needing_content_
  backfill's default scope skips already-succeeded rows).
- _resolve_reader_content preferring a populated content_html over plain
  `content`.

See ARCHITECTURE.md's Reader content-backfill note and CLAUDE.md's matching
Phase 5b bullet for the full write-up.
"""
import pathlib
import sys
import tempfile
import os

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Article, Library
from linklib import pipeline as pl
from linklib.extract import PageData, assess_extraction_quality, looks_like_bot_challenge
from linklib import extract as extract_mod


@pytest.fixture
def lib(tmp_path):
    db_path = str(tmp_path / "test.db")
    library = Library(db_path)
    yield library
    library.close()


def _seed(lib, url="https://example.com/piece", content="Original plain text content."):
    art = Article(url=url, title="Original Title", content=content,
                  saved_at="2026-01-01T00:00:00")
    return lib.upsert(art)


# ---------------------------------------------------------------------------
# assess_extraction_quality — the three sanity-check reasons
# ---------------------------------------------------------------------------

def test_assess_extraction_quality_ok_on_real_content():
    html = "<html><body><article>" + "<p>Real paragraph text here.</p>" * 20 + "</article></body></html>"
    plain = ("Real paragraph text here. " * 20).strip()
    ok, reason = assess_extraction_quality(html, plain, blocked=False)
    assert ok is True
    assert reason == ""


def test_assess_extraction_quality_flags_paywall_via_blocked_flag():
    """The pre-existing looks_paywalled()/PageData.blocked path — HTTP 200,
    but the fetch already determined it's a logged-out preview."""
    html = "<html><body><p>This post is for paid subscribers only.</p></body></html>"
    ok, reason = assess_extraction_quality(html, "This post is for paid subscribers only.", blocked=True)
    assert ok is False
    assert reason == "paywall"


def test_assess_extraction_quality_flags_bot_challenge_dedicated():
    """DEDICATED bot-challenge test, kept separate from the paywall test per
    explicit sign-off: HTTP 200, no paywall markers present (blocked=False
    confirmed), a Cloudflare-style challenge phrase in the HTML — must be
    logged as a distinguishable 'bot-challenge' reason, not lumped in with
    'paywall' or 'too-thin'."""
    html = ("<html><head><title>Just a moment...</title></head><body>"
            "<div id='challenge-running'>Checking your browser before accessing example.com.</div>"
            "<script>cf_chl_opt = {};</script></body></html>")
    plain = "Checking your browser before accessing example.com."
    # Confirm this page does NOT trip the paywall heuristic — this must be a
    # genuinely new, independent detection path, not a paywall case in disguise.
    assert looks_like_bot_challenge(html) is True
    ok, reason = assess_extraction_quality(html, plain, blocked=False)
    assert ok is False
    assert reason == "bot-challenge"
    assert reason != "paywall"


def test_assess_extraction_quality_flags_too_thin():
    html = "<html><body><p>Short.</p></body></html>"
    ok, reason = assess_extraction_quality(html, "Short.", blocked=False)
    assert ok is False
    assert reason == "too-thin"


# ---------------------------------------------------------------------------
# pipeline.backfill_article_content — never destructive on failure
# ---------------------------------------------------------------------------

def test_backfill_article_content_success(lib, monkeypatch):
    article_id = _seed(lib)
    html = "<html><body><article>" + "<p>Fresh paragraph content, definitely long enough.</p>" * 15 + "</article></body></html>"
    page = PageData(title="Fresh Title", content="Fresh paragraph content, definitely long enough. " * 15,
                    blocked=False, raw_html=html)
    monkeypatch.setattr(extract_mod, "fetch_page", lambda url: page)

    ok, reason = pl.backfill_article_content(lib, lib.get_article(article_id))
    assert ok is True
    assert reason == ""

    row = lib.get_article(article_id)
    assert row["content_html"], "structured HTML should now be stored"
    assert "<p>" in row["content_html"]
    assert row["content"] == "Original plain text content.", "plain-text content must stay untouched"

    log = lib.list_content_refetch_log()
    assert log and log[0]["status"] == "success"


def test_backfill_article_content_dead_url_never_destructive(lib, monkeypatch):
    """A deliberately-broken/dead URL: fetch_page's own contract is to
    swallow request errors and return an empty PageData, not raise — this
    must be logged as a fetch-error failure, and the article's existing
    content/content_html must survive completely unchanged."""
    article_id = _seed(lib, content="Existing content that must survive.")
    monkeypatch.setattr(extract_mod, "fetch_page", lambda url: PageData(title="", content=""))

    ok, reason = pl.backfill_article_content(lib, lib.get_article(article_id))
    assert ok is False
    assert reason == "fetch-error"

    row = lib.get_article(article_id)
    assert row["content"] == "Existing content that must survive."
    assert row["content_html"] == ""

    log = lib.list_content_refetch_log()
    assert log and log[0]["status"] == "failure" and log[0]["reason"] == "fetch-error"


def test_backfill_article_content_paywall_failure_preserves_existing(lib, monkeypatch):
    article_id = _seed(lib, content="Existing content that must survive.")
    html = "<html><body><p>Subscribe to read the rest of this post.</p></body></html>"
    page = PageData(title="Paywalled", content="Subscribe to read the rest of this post.",
                    blocked=True, raw_html=html)
    monkeypatch.setattr(extract_mod, "fetch_page", lambda url: page)

    ok, reason = pl.backfill_article_content(lib, lib.get_article(article_id))
    assert ok is False
    assert reason == "paywall"

    row = lib.get_article(article_id)
    assert row["content"] == "Existing content that must survive."
    assert row["content_html"] == ""


def test_backfill_article_content_bot_challenge_failure_dedicated(lib, monkeypatch):
    """DEDICATED bot-challenge test at the pipeline layer, mirroring the
    assess_extraction_quality test above — exercises the genuinely new
    marker-list path end to end, not riding behind the paywall case."""
    article_id = _seed(lib, content="Existing content that must survive.")
    html = ("<html><head><title>Just a moment...</title></head><body>"
            "Checking your browser before accessing this site.</body></html>")
    page = PageData(title="Just a moment...", content="Checking your browser before accessing this site.",
                    blocked=False, raw_html=html)
    monkeypatch.setattr(extract_mod, "fetch_page", lambda url: page)

    ok, reason = pl.backfill_article_content(lib, lib.get_article(article_id))
    assert ok is False
    assert reason == "bot-challenge"

    row = lib.get_article(article_id)
    assert row["content"] == "Existing content that must survive."
    assert row["content_html"] == ""

    log = lib.list_content_refetch_log()
    assert log and log[0]["status"] == "failure" and log[0]["reason"] == "bot-challenge"


# ---------------------------------------------------------------------------
# Resumability / scope
# ---------------------------------------------------------------------------

def test_articles_needing_content_backfill_skips_already_done(lib):
    id1 = _seed(lib, url="https://example.com/one")
    id2 = _seed(lib, url="https://example.com/two")
    lib.set_article_content_html(id1, "<p>Already structured.</p>")

    scope = lib.articles_needing_content_backfill()
    ids = [r["id"] for r in scope]
    assert id1 not in ids
    assert id2 in ids

    forced = lib.articles_needing_content_backfill(force=True)
    forced_ids = [r["id"] for r in forced]
    assert id1 in forced_ids and id2 in forced_ids


def test_content_refetch_failure_counts_uses_latest_attempt_only(lib):
    article_id = _seed(lib)
    lib.log_content_refetch_attempt(article_id, "failure", reason="paywall")
    lib.log_content_refetch_attempt(article_id, "success")

    counts = lib.content_refetch_failure_counts()
    assert counts == {}, "the article's most recent attempt succeeded, so it shouldn't count as a failure"


# ---------------------------------------------------------------------------
# Admin job: stop control
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


def test_content_backfill_job_stops_between_articles(env, monkeypatch):
    lib = env._lib()
    ids = [_seed(lib, url=f"https://example.com/stop-{i}") for i in range(5)]
    lib.close()

    def _fake_backfill(lib_arg, row):
        # First article processed: request a stop. The loop must finish this
        # iteration's bookkeeping and then exit before touching article 2+.
        env._job_set("content_backfill", stop_requested=True)
        return True, ""

    monkeypatch.setattr(env, "time", type("T", (), {"sleep": staticmethod(lambda *_: None)}))
    import linklib.pipeline as pl_mod
    monkeypatch.setattr(pl_mod, "backfill_article_content", _fake_backfill)

    env._content_backfill_job(limit=100000, force=False)

    status = env._job_get("content_backfill")
    assert status["stopped"] is True
    assert status["running"] is False
    assert status["done"] == 1, "should stop after the first article, not run all 5"


def test_content_backfill_status_route_requires_auth(env):
    from fastapi.testclient import TestClient
    c = TestClient(env.app)
    r = c.get("/admin/library/backfill-content/status")
    assert r.status_code == 401


def test_content_backfill_admin_page_renders(env):
    c = _admin_client(env)
    r = c.get("/admin/library/backfill-content")
    assert r.status_code == 200
    assert "Reader content backfill" in r.text
    assert 'action="/admin/library/backfill-content/start"' in r.text


# ---------------------------------------------------------------------------
# _resolve_reader_content prefers content_html when populated
# ---------------------------------------------------------------------------

def test_resolve_reader_content_prefers_backfilled_content_html(env):
    lib = env._lib()
    article_id = _seed(lib, content="Flat plain text that should be ignored now.")
    lib.set_article_content_html(article_id, "<p>Structured paragraph.</p><img src='https://example.com/x.png'>")
    lib.close()

    data = env._resolve_reader_content(id=article_id)
    assert data is not None
    assert "<img" in data["body_html"]
    assert "Structured paragraph." in data["body_html"]
    assert "Flat plain text that should be ignored now." not in data["body_html"]
