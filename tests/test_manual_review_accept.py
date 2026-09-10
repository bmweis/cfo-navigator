"""Durability audit item 4 — a per-article "Accept as final" override for
the needs-manual-review tier's one real false-positive: an article with
real-but-short content fails assess_extraction_quality() identically
forever, and no URL correction can fix that (the URL is already correct).

Covers:
- Library.accept_article_content(): composes with _manual_review_article_ids
  (an 'accepted' row as the latest attempt removes the article from that
  list) and with articles_needing_content_backfill/
  count_content_backfill_remaining (durably excluded from automatic retry
  too, not just hidden from the list).
- Library.unaccept_article_content(): reversible, additive (never deletes
  the 'accepted' row), restores manual-review visibility.
- list_accepted_content / count_content_accepted.
- The two admin routes, per-article, auth-gated.

See ARCHITECTURE.md's "'Accept as final' manual-review override" section
and CLAUDE.md's matching durability-audit-item-4 bullet.
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


def _seed(lib, url="https://example.com/short-piece"):
    aid = lib.upsert(Article(url=url, title="Short piece", content="Real but short."))
    return aid


def _make_needs_review(lib, aid, reason="too-thin"):
    for _ in range(Library._MANUAL_REVIEW_ATTEMPT_THRESHOLD):
        lib.log_content_refetch_attempt(aid, "failure", reason=reason)


# ---------------------------------------------------------------------------
# accept_article_content
# ---------------------------------------------------------------------------

def test_accept_returns_false_for_unknown_article(lib):
    assert lib.accept_article_content(999999) is False


def test_accept_removes_article_from_manual_review(lib):
    aid = _seed(lib)
    _make_needs_review(lib, aid)
    assert aid in lib._manual_review_article_ids()

    assert lib.accept_article_content(aid) is True
    assert aid not in lib._manual_review_article_ids()
    assert lib.count_articles_needing_manual_review() == 0


def test_accept_copies_prior_failure_reason_onto_the_accepted_row(lib):
    aid = _seed(lib)
    _make_needs_review(lib, aid, reason="too-thin")
    lib.accept_article_content(aid)

    rows = lib.list_accepted_content()
    assert len(rows) == 1
    assert rows[0]["article_id"] == aid
    assert rows[0]["reason"] == "too-thin"
    assert lib.count_content_accepted() == 1


def test_accept_durably_excludes_from_automatic_backfill_scope(lib):
    """Not just hidden from the manual-review list — a future backfill run
    must not silently re-attempt and re-fail an accepted article."""
    aid = _seed(lib)
    _make_needs_review(lib, aid)
    lib.accept_article_content(aid)

    scope_ids = {r["id"] for r in lib.articles_needing_content_backfill()}
    assert aid not in scope_ids


def test_accept_excludes_from_remaining_count(lib):
    aid = _seed(lib)
    _make_needs_review(lib, aid)
    before = lib.count_content_backfill_remaining()
    lib.accept_article_content(aid)
    after = lib.count_content_backfill_remaining()
    assert after == before  # was already excluded as manual-review; stays excluded as accepted


def test_accept_is_per_article_not_bulk(lib):
    """No bulk accept helper exists — accept_article_content only ever
    takes one article_id, by design."""
    import inspect
    sig = inspect.signature(Library.accept_article_content)
    params = [p for p in sig.parameters if p != "self"]
    assert params == ["article_id"]


# ---------------------------------------------------------------------------
# unaccept_article_content
# ---------------------------------------------------------------------------

def test_unaccept_returns_false_when_never_accepted(lib):
    aid = _seed(lib)
    assert lib.unaccept_article_content(aid) is False


def test_unaccept_restores_manual_review_visibility(lib):
    aid = _seed(lib)
    _make_needs_review(lib, aid, reason="paywall")
    lib.accept_article_content(aid)
    assert aid not in lib._manual_review_article_ids()

    assert lib.unaccept_article_content(aid) is True
    assert aid in lib._manual_review_article_ids()
    row = [r for r in lib.list_articles_needing_manual_review() if r["article_id"] == aid][0]
    assert row["reason"] == "paywall"


def test_unaccept_is_additive_not_destructive(lib):
    """The 'accepted' row is never deleted — the accept-then-reverse stays
    visible in the log's full history."""
    aid = _seed(lib)
    _make_needs_review(lib, aid)
    lib.accept_article_content(aid)
    lib.unaccept_article_content(aid)

    statuses = [r["status"] for r in lib.list_content_refetch_log(limit=100)
                if r["article_id"] == aid]
    assert "accepted" in statuses
    assert lib.count_content_accepted() == 0  # no longer the LATEST status


def test_unaccept_restores_automatic_backfill_eligibility_only_via_manual_review_path(lib):
    """After unaccept, the article is back in scope for whatever it would
    have been in before acceptance — here, still excluded, but now via
    needs-manual-review rather than accepted."""
    aid = _seed(lib)
    _make_needs_review(lib, aid)
    lib.accept_article_content(aid)
    lib.unaccept_article_content(aid)

    scope_ids = {r["id"] for r in lib.articles_needing_content_backfill()}
    assert aid not in scope_ids
    assert aid in lib._manual_review_article_ids()


# ---------------------------------------------------------------------------
# Admin routes
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


def test_accept_route_requires_auth(env):
    from fastapi.testclient import TestClient
    c = TestClient(env.app)
    r = c.post("/admin/reader/backfill-content/1/accept", follow_redirects=False)
    assert r.status_code in (302, 303, 401)
    assert r.status_code != 200


def test_accept_route_accepts_and_redirects(env):
    lib = env._lib()
    aid = _seed(lib)
    _make_needs_review(lib, aid)
    lib.close()

    c = _admin_client(env)
    r = c.post(f"/admin/reader/backfill-content/{aid}/accept", follow_redirects=False)
    assert r.status_code == 303

    lib = env._lib()
    assert aid not in lib._manual_review_article_ids()
    lib.close()


def test_unaccept_route_reverses(env):
    lib = env._lib()
    aid = _seed(lib)
    _make_needs_review(lib, aid)
    lib.accept_article_content(aid)
    lib.close()

    c = _admin_client(env)
    r = c.post(f"/admin/reader/backfill-content/{aid}/unaccept", follow_redirects=False)
    assert r.status_code == 303

    lib = env._lib()
    assert aid in lib._manual_review_article_ids()
    lib.close()


def test_admin_page_shows_accept_button_on_manual_review_row(env):
    lib = env._lib()
    aid = _seed(lib)
    _make_needs_review(lib, aid)
    lib.close()

    c = _admin_client(env)
    r = c.get("/admin/reader/backfill-content")
    assert r.status_code == 200
    assert f'/admin/reader/backfill-content/{aid}/accept' in r.text
    assert "Accept as final" in r.text


def test_admin_page_shows_accepted_section_with_undo(env):
    lib = env._lib()
    aid = _seed(lib)
    _make_needs_review(lib, aid)
    lib.accept_article_content(aid)
    lib.close()

    c = _admin_client(env)
    r = c.get("/admin/reader/backfill-content")
    assert r.status_code == 200
    assert "Accepted as final" in r.text
    assert f'/admin/reader/backfill-content/{aid}/unaccept' in r.text
    assert "Undo" in r.text
