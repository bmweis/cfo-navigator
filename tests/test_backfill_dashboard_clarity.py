"""Dashboard clarity pass for /admin/reader/backfill-content (2026-08).

Covers:
- Library.remaining_content_backfill_breakdown(): never-attempted vs.
  attempted-and-failed (grouped by latest reason), matching the exact id
  set count_content_backfill_remaining() counts.
- The partition arithmetic: Structured + Remaining + Needs review +
  Defunct service + Accepted as final sums to every article with a saved
  URL, with "Accepted as final" correctly excluded from BOTH Structured
  and Remaining (the real question this pass answers).
- The admin page: segmented bar renders, tile anchors only appear when the
  linked-to section actually has rows, and the live-status box now renders
  ABOVE the historical job-run banner (the bug this pass fixes).

See ARCHITECTURE.md's "Dashboard clarity pass" section.
"""
import os
import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Article, Library


@pytest.fixture
def lib(tmp_path):
    db = Library(str(tmp_path / "t.db"))
    try:
        yield db
    finally:
        db.close()


def _seed(lib, url, title="Piece", content="Some real plain text content here."):
    return lib.upsert(Article(url=url, title=title, content=content))


# ---------------------------------------------------------------------------
# remaining_content_backfill_breakdown
# ---------------------------------------------------------------------------

def test_breakdown_empty_when_nothing_remaining(lib):
    assert lib.remaining_content_backfill_breakdown() == {
        "never_attempted": 0, "attempted_failed": 0, "by_reason": {},
    }


def test_breakdown_counts_never_attempted(lib):
    _seed(lib, "https://example.com/a")
    _seed(lib, "https://example.com/b")
    bd = lib.remaining_content_backfill_breakdown()
    assert bd["never_attempted"] == 2
    assert bd["attempted_failed"] == 0
    assert bd["by_reason"] == {}


def test_breakdown_groups_attempted_failed_by_latest_reason(lib):
    a1 = _seed(lib, "https://example.com/a")
    a2 = _seed(lib, "https://example.com/b")
    a3 = _seed(lib, "https://example.com/c")
    lib.log_content_refetch_attempt(a1, "failure", reason="fetch-error")
    lib.log_content_refetch_attempt(a2, "failure", reason="fetch-error")
    lib.log_content_refetch_attempt(a3, "failure", reason="too-thin")

    bd = lib.remaining_content_backfill_breakdown()
    assert bd["never_attempted"] == 0
    assert bd["attempted_failed"] == 3
    assert bd["by_reason"] == {"fetch-error": 2, "too-thin": 1}


def test_breakdown_uses_latest_attempt_reason_only(lib):
    """An article that failed once for one reason and again for another
    counts under its LATEST reason only, not both."""
    a1 = _seed(lib, "https://example.com/a")
    lib.log_content_refetch_attempt(a1, "failure", reason="too-thin")
    lib.log_content_refetch_attempt(a1, "failure", reason="bot-challenge")

    bd = lib.remaining_content_backfill_breakdown()
    assert bd["attempted_failed"] == 1
    assert bd["by_reason"] == {"bot-challenge": 1}


def test_breakdown_excludes_needs_review_defunct_and_accepted(lib):
    """Matches count_content_backfill_remaining()'s own scope exactly — an
    article that's crossed into Needs review, Defunct service, or Accepted
    as final must not also show up in this breakdown."""
    needs_review = _seed(lib, "https://example.com/review")
    for _ in range(Library._MANUAL_REVIEW_ATTEMPT_THRESHOLD):
        lib.log_content_refetch_attempt(needs_review, "failure", reason="bot-challenge")

    defunct = _seed(lib, "https://example.com/dead")
    lib.log_content_refetch_attempt(defunct, "failure", reason="defunct-service")

    accepted = _seed(lib, "https://example.com/accepted")
    lib.accept_article_content(accepted)

    still_remaining = _seed(lib, "https://example.com/still-remaining")
    lib.log_content_refetch_attempt(still_remaining, "failure", reason="paywall")

    bd = lib.remaining_content_backfill_breakdown()
    assert bd["never_attempted"] == 0
    assert bd["attempted_failed"] == 1
    assert bd["by_reason"] == {"paywall": 1}
    assert lib.count_content_backfill_remaining() == 1


def test_breakdown_matches_remaining_count_total(lib):
    for i in range(3):
        _seed(lib, f"https://example.com/never-{i}")
    a = _seed(lib, "https://example.com/failed-once")
    lib.log_content_refetch_attempt(a, "failure", reason="fetch-error")

    bd = lib.remaining_content_backfill_breakdown()
    assert bd["never_attempted"] + bd["attempted_failed"] == lib.count_content_backfill_remaining() == 4


# ---------------------------------------------------------------------------
# The partition itself: Accepted as final is its own bucket, not folded
# into Structured or hidden from Remaining
# ---------------------------------------------------------------------------

def test_accepted_article_is_excluded_from_structured_count(lib):
    """The real question this pass answers: accept_article_content() never
    sets content_html, so an accepted article must NOT be counted as
    Structured."""
    aid = _seed(lib, "https://example.com/short-real")
    lib.accept_article_content(aid)
    assert lib.count_content_accepted() == 1
    assert lib.count_structured_content() == 0
    assert lib.count_content_backfill_remaining() == 0


def test_five_buckets_sum_to_every_article_with_a_url(lib):
    structured = _seed(lib, "https://example.com/structured")
    lib.set_article_content_html(structured, "<p>Real structure.</p>")

    remaining = _seed(lib, "https://example.com/remaining")

    needs_review = _seed(lib, "https://example.com/review")
    for _ in range(Library._MANUAL_REVIEW_ATTEMPT_THRESHOLD):
        lib.log_content_refetch_attempt(needs_review, "failure", reason="bot-challenge")

    defunct = _seed(lib, "https://example.com/dead")
    lib.log_content_refetch_attempt(defunct, "failure", reason="defunct-service")

    accepted = _seed(lib, "https://example.com/accepted")
    lib.accept_article_content(accepted)

    no_url = lib.upsert(Article(url="", title="No URL", content="x"))

    total_with_url = 5  # structured, remaining, needs_review, defunct, accepted
    partition = (
        lib.count_structured_content()
        + lib.count_content_backfill_remaining()
        + lib.count_articles_needing_manual_review()
        + lib.count_permanently_excluded_content()
        + lib.count_content_accepted()
    )
    assert partition == total_with_url
    assert lib.count() == total_with_url + 1  # + the no-URL article
    assert no_url  # sanity: the no-url article was actually inserted


# ---------------------------------------------------------------------------
# Admin page
# ---------------------------------------------------------------------------

@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib
    import webapp.app as appmod
    importlib.reload(appmod)
    yield appmod
    if os.path.exists(db):
        os.remove(db)


def _admin_client(appmod):
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app, raise_server_exceptions=True)
    c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    return c


def test_admin_page_renders_segmented_bar(env):
    lib = env._lib()
    _seed(lib, "https://example.com/a")
    lib.close()

    c = _admin_client(env)
    r = c.get("/admin/reader/backfill-content")
    assert r.status_code == 200
    assert "here&rsquo;s how" in r.text
    assert "break down" in r.text or "breaks down" in r.text


def test_admin_page_no_standalone_total_articles_card(env):
    """2026-08 review round-trip: the full-width 'Total articles' card was
    replaced with a plain lead-in sentence above the bar — it must not
    still render as its own bordered card."""
    lib = env._lib()
    no_url = lib.upsert(Article(url="", title="No URL", content="x"))
    lib.close()
    assert no_url

    c = _admin_client(env)
    r = c.get("/admin/reader/backfill-content")
    assert r.status_code == 200
    assert ">Total articles<" not in r.text
    assert "1 unreachable" in r.text
    assert "here&rsquo;s how the other" in r.text


def test_admin_page_lead_sentence_omits_unreachable_clause_when_zero(env):
    c = _admin_client(env)
    r = c.get("/admin/reader/backfill-content")
    assert r.status_code == 200
    assert "unreachable" not in r.text
    assert "0 articles total" in r.text


def test_admin_page_needs_review_tile_links_to_section_only_when_present(env):
    c = _admin_client(env)
    r = c.get("/admin/reader/backfill-content")
    assert r.status_code == 200
    assert 'href="#manual-review"' not in r.text

    lib = env._lib()
    a = _seed(lib, "https://example.com/flagged")
    for _ in range(Library._MANUAL_REVIEW_ATTEMPT_THRESHOLD):
        lib.log_content_refetch_attempt(a, "failure", reason="bot-challenge")
    lib.close()

    r2 = c.get("/admin/reader/backfill-content")
    assert 'href="#manual-review"' in r2.text
    assert 'id="manual-review"' in r2.text


def test_admin_page_accepted_tile_links_to_section_only_when_present(env):
    c = _admin_client(env)
    r = c.get("/admin/reader/backfill-content")
    assert 'href="#accepted-content"' not in r.text

    lib = env._lib()
    a = _seed(lib, "https://example.com/short")
    lib.accept_article_content(a)
    lib.close()

    r2 = c.get("/admin/reader/backfill-content")
    assert 'href="#accepted-content"' in r2.text


def test_admin_page_flagged_at_save_is_a_separate_overlay_not_a_sixth_tile(env):
    c = _admin_client(env)
    r = c.get("/admin/reader/backfill-content")
    assert "not one of the five buckets above" in r.text


def test_admin_page_no_spaced_em_dash_in_new_overlay_copy(env):
    c = _admin_client(env)
    r = c.get("/admin/reader/backfill-content")
    assert " &mdash; " not in r.text, "em dash must be unspaced per the em-dash policy"


def test_admin_page_structured_tile_dom_still_matches_legacy_regex(env):
    """Regression guard: test_fetch_reliability.py's
    test_admin_page_structured_stat_not_inflated_by_defunct_exclusion greps
    for r'([\\d,]+)</div>\\s*<div[^>]*>Structured</div>' — the tooltip
    must render AFTER the label div, never between the value and label."""
    import re
    c = _admin_client(env)
    r = c.get("/admin/reader/backfill-content")
    m = re.search(r'([\d,]+)</div>\s*<div[^>]*>Structured</div>', r.text)
    assert m is not None
    assert m.group(1) == "0"


# ---------------------------------------------------------------------------
# Banner order fix — live status must render ABOVE the historical job-run
# banner, not below it
# ---------------------------------------------------------------------------

def test_live_status_renders_above_historical_crash_banner(env):
    lib = env._lib()
    lib.start_job_run("content_backfill")
    lib.close()
    env._job_set("content_backfill", running=True, done=3, total=10, ok=3, failed=0)

    c = _admin_client(env)
    r = c.get("/admin/reader/backfill-content")
    assert r.status_code == 200
    poll_idx = r.text.index('id="poll-container"')
    live_status_idx = r.text.index("Content backfill in progress")
    still_in_progress_idx = r.text.index("still in progress")
    assert poll_idx < still_in_progress_idx, \
        "the live progress box must render before the historical job-run banner"
    assert live_status_idx < still_in_progress_idx


def test_crash_banner_alone_still_renders_when_not_live(env):
    """No live _JOB_STATE run behind the open row -> the crash banner still
    renders on its own (order-swap must not suppress it)."""
    lib = env._lib()
    lib.start_job_run("content_backfill")
    lib.close()
    assert env._job_get("content_backfill").get("running") is not True

    c = _admin_client(env)
    r = c.get("/admin/reader/backfill-content")
    assert r.status_code == 200
    assert "never finished" in r.text
    assert "likely interrupted" in r.text
