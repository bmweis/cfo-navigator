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

import requests

from linklib.db import Article, Library
from linklib import pipeline as pl
from linklib.extract import PageData, assess_extraction_quality, looks_like_bot_challenge, _describe_fetch_error
from linklib import extract as extract_mod
from linklib import wayback as wayback_mod


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
    must be logged as a fetch-error failure with the specific underlying
    reason (see PageData.fetch_error), and the article's existing
    content/content_html must survive completely unchanged."""
    article_id = _seed(lib, content="Existing content that must survive.")
    monkeypatch.setattr(extract_mod, "fetch_page", lambda url: PageData(title="", content="", fetch_error="HTTP 404"))
    # Explicit, deterministic "no Wayback fallback available" — not relying
    # on the test environment's network being unreachable. See
    # test_fetch_reliability.py for the dedicated Wayback-fallback tests.
    monkeypatch.setattr(wayback_mod, "find_snapshot_verbose", lambda url: (None, "no snapshot archived"))

    ok, reason = pl.backfill_article_content(lib, lib.get_article(article_id))
    assert ok is False
    assert reason == "fetch-error"

    row = lib.get_article(article_id)
    assert row["content"] == "Existing content that must survive."
    assert row["content_html"] == ""

    log = lib.list_content_refetch_log()
    assert log and log[0]["status"] == "failure" and log[0]["reason"] == "fetch-error"
    assert log[0]["detail"] == "HTTP 404 (wayback: no snapshot archived)", \
        "the specific fetch failure reason must be captured, not just the generic category"


# ---------------------------------------------------------------------------
# extract._describe_fetch_error / fetch_page — the actual root cause behind
# a previously-generic "fetch-error" with no detail: fetch_page swallowed
# the exception entirely (`except Exception:` with no `as exc`). Fixed by
# capturing it into PageData.fetch_error so a batch of failures can be told
# apart (independent dead links vs. one host systematically blocking this
# tool) instead of all looking identical.
# ---------------------------------------------------------------------------

def test_describe_fetch_error_http_status():
    resp = requests.Response()
    resp.status_code = 404
    exc = requests.exceptions.HTTPError(response=resp)
    assert _describe_fetch_error(exc) == "HTTP 404"


def test_describe_fetch_error_timeout():
    exc = requests.exceptions.Timeout("Connection timed out")
    assert _describe_fetch_error(exc) == "timeout"


def test_describe_fetch_error_connection_error():
    exc = requests.exceptions.ConnectionError("Name or service not known")
    assert "connection error" in _describe_fetch_error(exc)


def test_fetch_page_populates_fetch_error_on_real_failure(monkeypatch):
    """End-to-end through the real fetch_page(), not a hand-built PageData —
    confirms the fix actually wires into the function production calls."""
    def _raise(*a, **kw):
        resp = requests.Response()
        resp.status_code = 500
        raise requests.exceptions.HTTPError(response=resp)

    monkeypatch.setattr(extract_mod.requests, "get", _raise)
    page = extract_mod.fetch_page("https://example.com/dead")
    assert page.content == ""
    assert page.fetch_error == "HTTP 500"


def test_backfill_article_content_paywall_failure_preserves_existing(lib, monkeypatch):
    article_id = _seed(lib, content="Existing content that must survive.")
    html = "<html><body><p>Subscribe to read the rest of this post.</p></body></html>"
    page = PageData(title="Paywalled", content="Subscribe to read the rest of this post.",
                    blocked=True, raw_html=html)
    monkeypatch.setattr(extract_mod, "fetch_page", lambda url: page)
    monkeypatch.setattr(wayback_mod, "find_snapshot_verbose", lambda url: (None, "no snapshot archived"))

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
    monkeypatch.setattr(wayback_mod, "find_snapshot_verbose", lambda url: (None, "no snapshot archived"))

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


def test_content_refetch_failure_domains_groups_by_host(lib):
    """Four failures from the same host should surface as one clustered
    entry (count=4), not four independent-looking rows — exactly the signal
    that distinguishes 'one source systematically failing' from 'a handful
    of unrelated dead links'."""
    ids = {
        "https://boardtips.example.com/a": None,
        "https://boardtips.example.com/b": None,
        "https://boardtips.example.com/c": None,
        "https://boardtips.example.com/d": None,
        "https://unrelated.example.org/x": None,
    }
    for url in ids:
        ids[url] = _seed(lib, url=url)
    for url, aid in ids.items():
        lib.log_content_refetch_attempt(aid, "failure", reason="fetch-error", detail="HTTP 403")

    domains = lib.content_refetch_failure_domains()
    by_domain = {d["domain"]: d["count"] for d in domains}
    assert by_domain["boardtips.example.com"] == 4
    assert by_domain["unrelated.example.org"] == 1


def test_content_refetch_failure_domains_strips_www(lib):
    a1 = _seed(lib, url="https://www.example.com/one")
    a2 = _seed(lib, url="https://example.com/two")
    lib.log_content_refetch_attempt(a1, "failure", reason="fetch-error")
    lib.log_content_refetch_attempt(a2, "failure", reason="fetch-error")

    domains = lib.content_refetch_failure_domains()
    assert len(domains) == 1
    assert domains[0]["domain"] == "example.com"
    assert domains[0]["count"] == 2


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
    r = c.get("/admin/reader/backfill-content/status")
    assert r.status_code == 401


def test_content_backfill_admin_page_renders(env):
    c = _admin_client(env)
    r = c.get("/admin/reader/backfill-content")
    assert r.status_code == 200
    assert "Reader content backfill" in r.text
    assert 'action="/admin/reader/backfill-content/start"' in r.text


def test_content_backfill_admin_page_shows_domain_clustering_banner(env):
    lib = env._lib()
    for i in range(4):
        aid = _seed(lib, url=f"https://boardtips.example.com/tip-{i}")
        lib.log_content_refetch_attempt(aid, "failure", reason="fetch-error", detail="HTTP 403")
    lib.close()

    c = _admin_client(env)
    r = c.get("/admin/reader/backfill-content")
    assert r.status_code == 200
    assert "Failures clustering on one source" in r.text
    assert "boardtips.example.com" in r.text
    assert "HTTP 403" in r.text, "the specific failure detail should show in the recent-attempts log"


def test_content_backfill_admin_page_no_clustering_banner_for_single_failures(env):
    lib = env._lib()
    aid = _seed(lib, url="https://onlyone.example.com/piece")
    lib.log_content_refetch_attempt(aid, "failure", reason="fetch-error", detail="HTTP 404")
    lib.close()

    c = _admin_client(env)
    r = c.get("/admin/reader/backfill-content")
    assert r.status_code == 200
    assert "Failures clustering on one source" not in r.text, \
        "a single failure from a source is an ordinary dead link, not a clustering signal"


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
