"""Wayback 429-retry sweep — targets articles stuck because the Wayback
fallback tier itself got rate-limited (HTTP 429) on a prior backfill
attempt, rather than any of the "real" failure reasons the ordinary
default-scope sweep already re-attempts.

Context: linklib/wayback.py's own investigation found archive.org's
Availability API rate-limits broadly and unpredictably. A direct-fetch
failure whose Wayback fallback also hit a 429 is logged as an ordinary
content_refetch_log failure row (reason=the original direct-fetch reason,
detail="...(wayback: HTTP 429)") — there's no dedicated status/reason for
this, and nothing was retrying these once archive.org's rate limit
cleared. See Library._wayback_429_retry_ids's docstring.

Covers:
- Library._wayback_429_retry_ids()/count_wayback_429_retry_candidates()/
  list_wayback_429_retry_candidates() find exactly the articles whose
  LATEST attempt failed with a Wayback 429 in the detail, and only those —
  not an article whose latest attempt succeeded, not one whose latest
  failure was some other reason (including a non-429 Wayback failure), and
  not one already re-fetched successfully since its 429.
- The set is idempotent/self-clearing: once an article's latest attempt
  becomes a success, it drops out with no further code needed.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Article, Library


def _seed(lib, url="https://example.com/piece"):
    art = Article(url=url, title="Original Title", content="Original plain text content.",
                  saved_at="2026-01-01T00:00:00")
    return lib.upsert(art)


@pytest.fixture
def lib(tmp_path):
    db_path = str(tmp_path / "test.db")
    library = Library(db_path)
    yield library
    library.close()


def test_finds_article_whose_latest_failure_was_a_wayback_429(lib):
    article_id = _seed(lib, "https://example.com/a")
    lib.log_content_refetch_attempt(
        article_id, "failure", reason="fetch-error",
        detail="HTTP 403 (wayback: HTTP 429)", source="direct")

    assert lib.count_wayback_429_retry_candidates() == 1
    candidates = lib.list_wayback_429_retry_candidates()
    assert [r["id"] for r in candidates] == [article_id]


def test_finds_bare_wayback_429_detail_with_no_direct_detail_prefix(lib):
    """_finish_backfill_via_wayback formats detail differently when the
    original direct-fetch failure carried no detail of its own: just
    "wayback: HTTP 429", no parenthesis/prefix."""
    article_id = _seed(lib)
    lib.log_content_refetch_attempt(
        article_id, "failure", reason="too-thin",
        detail="wayback: HTTP 429", source="direct")

    assert lib.count_wayback_429_retry_candidates() == 1


def test_excludes_article_whose_latest_attempt_succeeded(lib):
    article_id = _seed(lib)
    lib.log_content_refetch_attempt(
        article_id, "failure", reason="fetch-error",
        detail="HTTP 403 (wayback: HTTP 429)", source="direct")
    # A later run recovered it.
    lib.set_article_content_html(article_id, "<p>Real structured content.</p>")
    lib.log_content_refetch_attempt(article_id, "success", source="direct")

    assert lib.count_wayback_429_retry_candidates() == 0


def test_excludes_article_whose_latest_failure_was_not_a_wayback_429(lib):
    article_id = _seed(lib)
    lib.log_content_refetch_attempt(
        article_id, "failure", reason="fetch-error",
        detail="HTTP 403 (wayback: no snapshot archived)", source="direct")

    assert lib.count_wayback_429_retry_candidates() == 0


def test_excludes_article_whose_latest_failure_was_a_different_wayback_error(lib):
    """A non-429 Wayback failure (connection error, timeout, no snapshot)
    is a different, already-handled diagnostic signal — not this sweep's
    job."""
    article_id = _seed(lib)
    lib.log_content_refetch_attempt(
        article_id, "failure", reason="fetch-error",
        detail="HTTP 403 (wayback: connection error: boom)", source="direct")

    assert lib.count_wayback_429_retry_candidates() == 0


def test_only_the_latest_attempt_matters(lib):
    """An article that hit a Wayback 429 once, then later failed for an
    unrelated reason with no Wayback 429 involved, should not show up —
    only the most recent content_refetch_log row per article counts."""
    article_id = _seed(lib)
    lib.log_content_refetch_attempt(
        article_id, "failure", reason="fetch-error",
        detail="HTTP 403 (wayback: HTTP 429)", source="direct")
    lib.log_content_refetch_attempt(
        article_id, "failure", reason="too-thin", detail="", source="direct")

    assert lib.count_wayback_429_retry_candidates() == 0


def test_multiple_articles_and_full_row_shape(lib):
    a1 = _seed(lib, "https://example.com/one")
    a2 = _seed(lib, "https://example.com/two")
    _seed(lib, "https://example.com/three")  # never attempted — not a candidate
    lib.log_content_refetch_attempt(
        a1, "failure", reason="fetch-error", detail="wayback: HTTP 429", source="direct")
    lib.log_content_refetch_attempt(
        a2, "failure", reason="bot-challenge", detail="wayback: HTTP 429", source="direct")

    assert lib.count_wayback_429_retry_candidates() == 2
    rows = lib.list_wayback_429_retry_candidates()
    ids = sorted(r["id"] for r in rows)
    assert ids == sorted([a1, a2])
    # Full article dicts — same shape backfill_article_content() expects.
    for row in rows:
        assert "url" in row and "title" in row
