"""Article purge flow (durability follow-up, 2026-08) — mirrors the manual-
review corrected-URL CSV round trip exactly: export candidates, admin
reviews and re-uploads the approved subset, a preview screen shows
title/URL/word count per article, nothing is deleted until confirmed.

Covers:
- linklib.purge_csv.parse_purge_confirmations_csv: confirmed/skipped/errors
  three-way split, the MAX_PURGE_PER_RUN cap failing the whole import, a
  missing header failing the whole import, an unrecognized confirm_purge
  value treated as an error (not silently skipped), and re-validation
  against a fresh candidate set (not the CSV's own columns).
- Library.articles_eligible_for_purge / count_purge_candidates: only
  near-zero-content articles with no content_html qualify — NOT the same
  set as the ordinary backfill Remaining count.
- Library.delete_article now also cleans up content_refetch_log and
  url_correction_log (a general fix, exercised here since purge is the
  path most likely to actually hit it).
- Library.purge_article: snapshot-then-verify semantics, None for an
  unknown id.
- The three routes: export, preview (auth-gated, re-validates, renders a
  typed-count confirm field), commit (typed-count guard, per-run cap
  defense-in-depth, TOCTOU re-validation, archive_audit_log logging,
  pre-purge backup_now, real deletion).

See ARCHITECTURE.md's "Article purge flow" section and CLAUDE.md's
matching bullet for the full write-up.
"""
import os
import pathlib
import sys
import tempfile
from urllib.parse import unquote

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Article, Library
from linklib.purge_csv import parse_purge_confirmations_csv, MAX_PURGE_PER_RUN, MAX_ROWS

THIN = "Too short to count as real content."
GOOD = ("Real article body with plenty of words in it. " * 20).strip()


@pytest.fixture
def lib(tmp_path):
    db = Library(str(tmp_path / "t.db"))
    try:
        yield db
    finally:
        db.close()


def _thin_article(lib, url="https://example.com/thin"):
    return lib.upsert(Article(url=url, title="Thin", content=THIN))


def _good_article(lib, url="https://example.com/good"):
    return lib.upsert(Article(url=url, title="Good", content=GOOD))


def _candidates_dict(lib):
    return {r["id"]: {"title": r["title"], "url": r["url"], "word_count": r["word_count"]}
            for r in lib.articles_eligible_for_purge()}


# ---------------------------------------------------------------------------
# Library.articles_eligible_for_purge / count_purge_candidates
# ---------------------------------------------------------------------------

def test_thin_article_with_no_content_html_is_a_candidate(lib):
    aid = _thin_article(lib)
    ids = {r["id"] for r in lib.articles_eligible_for_purge()}
    assert aid in ids
    assert lib.count_purge_candidates() == 1


def test_good_article_is_never_a_candidate(lib):
    _good_article(lib)
    assert lib.articles_eligible_for_purge() == []
    assert lib.count_purge_candidates() == 0


def test_thin_article_with_backfilled_content_html_is_not_a_candidate(lib):
    """content_html populated means real structured content exists even if
    the plain-text `content` field is thin — must not be purged."""
    aid = _thin_article(lib)
    lib.set_article_content_html(aid, "<p>real structured content here</p>")
    assert lib.articles_eligible_for_purge() == []


def test_candidate_carries_word_count_and_reason(lib):
    aid = _thin_article(lib)
    lib.set_content_check_flag(aid, True, "too-thin")
    rows = lib.articles_eligible_for_purge()
    assert len(rows) == 1
    assert rows[0]["word_count"] == len(THIN.split())
    assert rows[0]["content_check_reason"] == "too-thin"


def test_purge_candidates_is_not_the_same_set_as_backfill_remaining(lib):
    """The whole point: Remaining is mostly articles with perfectly good
    text waiting on a structure backfill. Purge candidates are the much
    narrower 'genuinely nothing was ever saved' set."""
    _good_article(lib, url="https://example.com/waiting-on-backfill")
    _thin_article(lib, url="https://example.com/truly-empty")
    assert lib.count_content_backfill_remaining() == 2  # both lack content_html
    assert lib.count_purge_candidates() == 1  # only the thin one


# ---------------------------------------------------------------------------
# Library.delete_article's extended cleanup
# ---------------------------------------------------------------------------

def test_delete_article_cleans_up_content_refetch_log_and_url_correction_log(lib):
    aid = _thin_article(lib)
    lib.log_content_refetch_attempt(aid, "failure", reason="too-thin")
    lib.apply_article_url_correction(aid, "https://example.com/corrected")

    assert len(lib.list_content_refetch_log(limit=100)) == 1
    assert len(lib.list_url_correction_log(limit=100)) == 1

    lib.delete_article(aid)

    assert [r for r in lib.list_content_refetch_log(limit=100) if r["article_id"] == aid] == []
    assert [r for r in lib.list_url_correction_log(limit=100) if r["article_id"] == aid] == []


def test_delete_article_does_not_touch_enrichment_cost_or_archive_audit_log(lib):
    """Financial ledger and historical audit trail both deliberately
    outlive a deleted article — see delete_article's docstring."""
    aid = _thin_article(lib)
    lib.record_enrichment_cost(aid, "haiku", input_tokens=10, output_tokens=5, cost_usd=0.001)
    lib.record_archive_audit(None, "delete", aid, detail="test")

    lib.delete_article(aid)

    assert lib.conn.execute("SELECT COUNT(*) FROM enrichment_cost WHERE article_id=?",
                            (aid,)).fetchone()[0] == 1
    assert lib.conn.execute("SELECT COUNT(*) FROM archive_audit_log WHERE item_id=?",
                            (aid,)).fetchone()[0] == 1


# ---------------------------------------------------------------------------
# Library.purge_article
# ---------------------------------------------------------------------------

def test_purge_article_returns_none_for_unknown_id(lib):
    assert lib.purge_article(999999) is None


def test_purge_article_snapshots_then_deletes_and_verifies(lib):
    aid = _thin_article(lib)
    snapshot = lib.purge_article(aid)
    assert snapshot["id"] == aid
    assert snapshot["title"] == "Thin"
    assert snapshot["word_count"] == len(THIN.split())
    assert lib.get_article(aid) is None


# ---------------------------------------------------------------------------
# linklib.purge_csv.parse_purge_confirmations_csv
# ---------------------------------------------------------------------------

def _csv(rows, header=("article_id", "confirm_purge")):
    lines = [",".join(header)]
    lines += [",".join(str(c) for c in r) for r in rows]
    return ("\n".join(lines)).encode("utf-8")


def test_confirmed_skipped_errors_three_way_split():
    candidates = {1: {"title": "A", "url": "https://a", "word_count": 3},
                 2: {"title": "B", "url": "https://b", "word_count": 5}}
    data = _csv([(1, "yes"), (2, ""), (3, "yes")])
    confirmed, skipped, errors = parse_purge_confirmations_csv(data, candidates)
    assert [c["article_id"] for c in confirmed] == [1]
    assert [s["article_id"] for s in skipped] == [2]
    assert len(errors) == 1
    assert "3" in errors[0]["reason"]


def test_recognized_yes_and_no_markers():
    candidates = {i: {"title": "", "url": "", "word_count": 0} for i in range(1, 7)}
    data = _csv([(1, "yes"), (2, "y"), (3, "1"), (4, "x"), (5, "no"), (6, "0")])
    confirmed, skipped, errors = parse_purge_confirmations_csv(data, candidates)
    assert {c["article_id"] for c in confirmed} == {1, 2, 3, 4}
    assert {s["article_id"] for s in skipped} == {5, 6}
    assert errors == []


def test_unrecognized_confirm_value_is_an_error_not_a_silent_skip():
    candidates = {1: {"title": "", "url": "", "word_count": 0}}
    data = _csv([(1, "maybe")])
    confirmed, skipped, errors = parse_purge_confirmations_csv(data, candidates)
    assert confirmed == []
    assert skipped == []
    assert len(errors) == 1
    assert "maybe" in errors[0]["reason"]


def test_article_no_longer_a_candidate_is_an_error():
    """Re-validated against a FRESH candidate set — the CSV's own stale
    columns are never trusted."""
    data = _csv([(1, "yes")])
    confirmed, skipped, errors = parse_purge_confirmations_csv(data, {})  # empty = no candidates
    assert confirmed == []
    assert len(errors) == 1
    assert "no longer a purge candidate" in errors[0]["reason"]


def test_missing_header_column_raises():
    data = b"article_id\n1"
    with pytest.raises(ValueError, match="Missing required column"):
        parse_purge_confirmations_csv(data, {})


def test_empty_file_raises():
    with pytest.raises(ValueError, match="empty"):
        parse_purge_confirmations_csv(b"", {})


def test_exceeding_max_purge_per_run_raises_and_fails_the_whole_import():
    candidates = {i: {"title": "", "url": "", "word_count": 0} for i in range(1, MAX_PURGE_PER_RUN + 2)}
    data = _csv([(i, "yes") for i in range(1, MAX_PURGE_PER_RUN + 2)])
    with pytest.raises(ValueError, match=f"{MAX_PURGE_PER_RUN}-per-run cap"):
        parse_purge_confirmations_csv(data, candidates)


def test_max_rows_cap_reports_an_error_not_a_silent_truncation():
    candidates = {i: {"title": "", "url": "", "word_count": 0} for i in range(1, MAX_ROWS + 5)}
    data = _csv([(i, "no") for i in range(1, MAX_ROWS + 5)])  # all "no" so MAX_PURGE_PER_RUN never trips
    confirmed, skipped, errors = parse_purge_confirmations_csv(data, candidates)
    assert any("MAX_ROWS" not in e["reason"] and "import limit" in e["reason"] for e in errors)


# ---------------------------------------------------------------------------
# Admin routes
# ---------------------------------------------------------------------------

@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_REFRESH_TOKEN", raising=False)
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


def test_export_route_requires_auth(env):
    from fastapi.testclient import TestClient
    c = TestClient(env.app)
    r = c.get("/admin/reader/backfill-content/purge/export.csv", follow_redirects=False)
    assert r.status_code in (302, 303)


def test_export_route_lists_candidates(env):
    lib = env._lib()
    aid = _thin_article(lib)
    lib.close()

    c = _admin_client(env)
    r = c.get("/admin/reader/backfill-content/purge/export.csv")
    assert r.status_code == 200
    assert f"{aid}," in r.text
    assert "confirm_purge" in r.text


def test_preview_route_requires_auth(env):
    from fastapi.testclient import TestClient
    c = TestClient(env.app)
    r = c.post("/admin/reader/backfill-content/purge/import/preview",
               files={"file": ("p.csv", b"article_id,confirm_purge\n1,yes", "text/csv")})
    assert r.status_code == 401


def test_preview_route_shows_confirmed_rows_and_typed_count_field(env):
    lib = env._lib()
    aid = _thin_article(lib)
    lib.close()

    c = _admin_client(env)
    csv_body = f"article_id,confirm_purge\n{aid},yes".encode()
    r = c.post("/admin/reader/backfill-content/purge/import/preview",
              files={"file": ("p.csv", csv_body, "text/csv")})
    assert r.status_code == 200
    assert "Confirmed for deletion (1)" in r.text
    assert "Type <strong>1</strong> to confirm" in r.text
    assert f'value="{aid}"' in r.text
    # nothing deleted yet
    lib = env._lib()
    assert lib.get_article(aid) is not None
    lib.close()


def test_commit_route_requires_matching_typed_count(env):
    lib = env._lib()
    aid = _thin_article(lib)
    lib.close()

    c = _admin_client(env)
    r = c.post("/admin/reader/backfill-content/purge/import/commit",
              data={"article_id": [str(aid)], "confirm_count": "2"}, follow_redirects=False)
    assert r.status_code == 303
    assert "did not match" in unquote(r.headers["location"])

    lib = env._lib()
    assert lib.get_article(aid) is not None  # never deleted
    lib.close()


def test_commit_route_deletes_when_count_matches(env):
    lib = env._lib()
    aid = _thin_article(lib)
    lib.close()

    c = _admin_client(env)
    r = c.post("/admin/reader/backfill-content/purge/import/commit",
              data={"article_id": [str(aid)], "confirm_count": "1"}, follow_redirects=False)
    assert r.status_code == 303
    assert "Purged 1 article" in unquote(r.headers["location"])

    lib = env._lib()
    assert lib.get_article(aid) is None
    lib.close()


def test_commit_route_logs_archive_audit_for_a_real_admin(env):
    """_log_archive_audit silently skips when there's no matching `users`
    row (the break-glass host-password admin login this test client's
    /login otherwise uses — see _log_archive_audit's docstring), so this
    test creates a real admin user first to actually exercise the log
    write, same as every other delete route's audit trail."""
    lib = env._lib()
    aid = _thin_article(lib)
    lib.create_user("realadmin", "realpass", role="admin", name="Real Admin")
    lib.close()

    from fastapi.testclient import TestClient
    c = TestClient(env.app, raise_server_exceptions=True)
    c.post("/login", data={"username": "realadmin", "password": "realpass"}, follow_redirects=False)

    r = c.post("/admin/reader/backfill-content/purge/import/commit",
              data={"article_id": [str(aid)], "confirm_count": "1"}, follow_redirects=False)
    assert r.status_code == 303

    lib = env._lib()
    audit_rows = [row for row in lib.list_archive_audit_log(limit=100) if row["item_id"] == aid]
    assert len(audit_rows) == 1
    assert audit_rows[0]["action"] == "delete"
    assert "purge" in audit_rows[0]["detail"]
    lib.close()


def test_commit_route_skips_article_no_longer_a_candidate(env):
    """TOCTOU: an article backfilled with real content_html between
    preview and commit must not be deleted."""
    lib = env._lib()
    aid = _thin_article(lib)
    lib.set_article_content_html(aid, "<p>real structured content</p>")
    lib.close()

    c = _admin_client(env)
    r = c.post("/admin/reader/backfill-content/purge/import/commit",
              data={"article_id": [str(aid)], "confirm_count": "1"}, follow_redirects=False)
    assert r.status_code == 303
    assert "Purged 0 article" in unquote(r.headers["location"])

    lib = env._lib()
    assert lib.get_article(aid) is not None
    lib.close()


def test_commit_route_enforces_per_run_cap_even_on_a_hand_crafted_post(env):
    lib = env._lib()
    ids = [str(_thin_article(lib, url=f"https://example.com/many-{i}"))
           for i in range(MAX_PURGE_PER_RUN + 1)]
    lib.close()

    c = _admin_client(env)
    r = c.post("/admin/reader/backfill-content/purge/import/commit",
              data={"article_id": ids, "confirm_count": str(len(ids))}, follow_redirects=False)
    assert r.status_code == 303
    assert "per-run cap" in unquote(r.headers["location"])

    lib = env._lib()
    assert lib.count() == len(ids)  # nothing deleted
    lib.close()


def test_commit_route_aborts_if_pre_purge_backup_fails(env, monkeypatch):
    lib = env._lib()
    aid = _thin_article(lib)
    lib.close()

    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("GOOGLE_OAUTH_REFRESH_TOKEN", "rtoken")
    monkeypatch.setattr(env.backup, "backup_now",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("Drive unreachable")))

    c = _admin_client(env)
    r = c.post("/admin/reader/backfill-content/purge/import/commit",
              data={"article_id": [str(aid)], "confirm_count": "1"}, follow_redirects=False)
    assert r.status_code == 303
    assert "Pre-purge backup failed" in unquote(r.headers["location"])

    lib = env._lib()
    assert lib.get_article(aid) is not None  # never deleted
    lib.close()


def test_admin_page_shows_purge_section_and_candidate_count(env):
    lib = env._lib()
    _thin_article(lib)
    lib.close()

    c = _admin_client(env)
    r = c.get("/admin/reader/backfill-content")
    assert r.status_code == 200
    assert "Purge articles (1 candidate)" in r.text
    assert 'action="/admin/reader/backfill-content/purge/import/preview"' in r.text
