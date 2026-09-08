"""Phase 5b follow-up #2 — Retry backoff + manual URL correction, and the
known-domain-migration fetch tier.

Context (full write-up in ARCHITECTURE.md's "Reader content-structure
backfill" section and CLAUDE.md's matching bullet): everything that wasn't
defunct-service (a Cloudflare-blocked domain, genuine 404 link rot) stayed
in default retry scope forever — every future batch re-attempted it
indefinitely, burning time and Wayback's scarce rate-limit budget. This adds
a second, DISTINCT exclusion tier ("needs-manual-review") after
Library._MANUAL_REVIEW_ATTEMPT_THRESHOLD consecutive failures — unlike
defunct-service, NOT considered permanently dead, so a URL correction (via
the admin page's CSV export/import round trip) resets it and makes it
eligible for automatic retry again. Also adds a known-domain-migration fetch
tier (linklib.domain_migration, tried before Wayback) for articles whose
host is a confirmed migrated domain.

Covers:
- Library._manual_review_article_ids/count/list — threshold actually
  triggers (not before, not after), reason != defunct-service required,
  reset by a URL correction (post-correction attempts start fresh).
- articles_needing_content_backfill/count_content_backfill_remaining exclude
  needs-manual-review in default scope; force=True still reaches it.
- Library.apply_article_url_correction — verified single-row update,
  url_correction_log trace, False on an unknown article_id.
- linklib.manual_review_csv.parse_manual_review_corrections_csv — the
  updates/skipped/errors three-way split.
- linklib.domain_migration.find_migrated_url / _titles_match.
- linklib.pipeline._domain_migration_target / _try_domain_migration /
  _finish_backfill_after_direct_failure — migration tried before Wayback,
  exactly one content_refetch_log row per backfill_article_content() call
  whichever tier succeeds or all fail, source='migration' on a migration
  success.
- webapp/app.py admin page: 5-tile stats grid, needs-manual-review list
  section, "via Migration" badge, and the export/preview/commit route trio
  (a live end-to-end round trip — nothing written until commit is
  confirmed).
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
from linklib import domain_migration as dm_mod
from linklib.extract import PageData
from linklib.manual_review_csv import parse_manual_review_corrections_csv


def _seed(lib, url="https://example.com/piece", content="Original plain text content.", title="Original Title"):
    art = Article(url=url, title=title, content=content, saved_at="2026-01-01T00:00:00")
    return lib.upsert(art)


@pytest.fixture
def lib(tmp_path):
    db_path = str(tmp_path / "test.db")
    library = Library(db_path)
    yield library
    library.close()


# ---------------------------------------------------------------------------
# Retry-cap threshold — needs-manual-review tier
# ---------------------------------------------------------------------------

def test_manual_review_not_flagged_below_threshold(lib):
    a = _seed(lib)
    for _ in range(Library._MANUAL_REVIEW_ATTEMPT_THRESHOLD - 1):
        lib.log_content_refetch_attempt(a, "failure", reason="fetch-error")
    assert a not in lib._manual_review_article_ids()
    assert lib.count_articles_needing_manual_review() == 0


def test_manual_review_flagged_at_threshold(lib):
    a = _seed(lib)
    for _ in range(Library._MANUAL_REVIEW_ATTEMPT_THRESHOLD):
        lib.log_content_refetch_attempt(a, "failure", reason="fetch-error")
    assert a in lib._manual_review_article_ids()
    assert lib.count_articles_needing_manual_review() == 1


def test_manual_review_excludes_defunct_service_reason(lib):
    """defunct-service is a separate, permanent exclusion — must never be
    folded into needs-manual-review even after 3+ attempts."""
    a = _seed(lib, url="http://feedproxy.google.com/~r/x/")
    for _ in range(Library._MANUAL_REVIEW_ATTEMPT_THRESHOLD + 2):
        lib.log_content_refetch_attempt(a, "failure", reason="defunct-service")
    assert a not in lib._manual_review_article_ids()
    assert lib.count_permanently_excluded_content() == 1


def test_manual_review_not_flagged_if_latest_attempt_succeeded(lib):
    a = _seed(lib)
    for _ in range(Library._MANUAL_REVIEW_ATTEMPT_THRESHOLD):
        lib.log_content_refetch_attempt(a, "failure", reason="fetch-error")
    lib.log_content_refetch_attempt(a, "success", source="direct")
    assert a not in lib._manual_review_article_ids()


def test_articles_needing_content_backfill_excludes_manual_review_by_default(lib):
    normal_id = _seed(lib, url="https://example.com/normal")
    flagged_id = _seed(lib, url="https://example.com/flagged")
    for _ in range(Library._MANUAL_REVIEW_ATTEMPT_THRESHOLD):
        lib.log_content_refetch_attempt(flagged_id, "failure", reason="fetch-error")

    default_ids = [r["id"] for r in lib.articles_needing_content_backfill()]
    assert normal_id in default_ids
    assert flagged_id not in default_ids

    forced_ids = [r["id"] for r in lib.articles_needing_content_backfill(force=True)]
    assert flagged_id in forced_ids, "force=True must still reach a needs-manual-review article"


def test_count_content_backfill_remaining_excludes_manual_review(lib):
    _seed(lib, url="https://example.com/normal2")
    flagged_id = _seed(lib, url="https://example.com/flagged2")
    assert lib.count_content_backfill_remaining() == 2
    for _ in range(Library._MANUAL_REVIEW_ATTEMPT_THRESHOLD):
        lib.log_content_refetch_attempt(flagged_id, "failure", reason="fetch-error")
    assert lib.count_content_backfill_remaining() == 1


def test_list_articles_needing_manual_review_shows_real_failure_reason(lib):
    """The list surfaces the article's REAL last failure reason/detail, not
    a synthetic 'needs-manual-review' tag."""
    a = _seed(lib, url="https://example.com/blocked", title="Blocked Piece")
    lib.log_content_refetch_attempt(a, "failure", reason="fetch-error", detail="x")
    lib.log_content_refetch_attempt(a, "failure", reason="bot-challenge", detail="Cloudflare challenge")
    lib.log_content_refetch_attempt(a, "failure", reason="bot-challenge", detail="Cloudflare challenge again")

    rows = lib.list_articles_needing_manual_review()
    assert len(rows) == 1
    row = rows[0]
    assert row["article_id"] == a
    assert row["title"] == "Blocked Piece"
    assert row["current_url"] == "https://example.com/blocked"
    assert row["reason"] == "bot-challenge"
    assert row["attempt_count"] == 3


# ---------------------------------------------------------------------------
# apply_article_url_correction — durable trace, attempt-count reset
# ---------------------------------------------------------------------------

def test_apply_article_url_correction_updates_url_and_logs_trace(lib):
    a = _seed(lib, url="https://old-domain.example/post")
    ok = lib.apply_article_url_correction(a, "https://new-domain.example/post", source="csv-import")
    assert ok is True

    row = lib.get_article(a)
    assert row["url"] == "https://new-domain.example/post"

    log = lib.list_url_correction_log()
    assert len(log) == 1
    assert log[0]["article_id"] == a
    assert log[0]["old_url"] == "https://old-domain.example/post"
    assert log[0]["new_url"] == "https://new-domain.example/post"
    assert log[0]["source"] == "csv-import"


def test_apply_article_url_correction_unknown_article_returns_false(lib):
    assert lib.apply_article_url_correction(999999, "https://example.com/x") is False
    assert lib.list_url_correction_log() == []


def test_url_correction_resets_manual_review_status(lib):
    """The core behavior the CSV-import flow depends on: correcting a
    needs-manual-review article's URL must make it eligible for default-
    scope retry again — WITHOUT deleting the pre-correction failure
    history."""
    a = _seed(lib, url="https://dead.example/post")
    for _ in range(Library._MANUAL_REVIEW_ATTEMPT_THRESHOLD):
        lib.log_content_refetch_attempt(a, "failure", reason="bot-challenge")
    assert a in lib._manual_review_article_ids()

    lib.apply_article_url_correction(a, "https://new.example/post")
    assert a not in lib._manual_review_article_ids(), \
        "a corrected article must re-qualify for automatic retry immediately"

    # Full pre-correction failure history must still be intact (non-destructive).
    assert len(lib.list_content_refetch_log()) == Library._MANUAL_REVIEW_ATTEMPT_THRESHOLD

    # And it's back in the default backfill scope.
    default_ids = [r["id"] for r in lib.articles_needing_content_backfill()]
    assert a in default_ids

    # A fresh run of failures against the NEW url re-triggers the tier again
    # (attempt count counts from the correction, not from zero forever).
    for _ in range(Library._MANUAL_REVIEW_ATTEMPT_THRESHOLD):
        lib.log_content_refetch_attempt(a, "failure", reason="fetch-error")
    assert a in lib._manual_review_article_ids()


# ---------------------------------------------------------------------------
# linklib.manual_review_csv — export/import parsing
# ---------------------------------------------------------------------------

def test_parse_manual_review_csv_valid_update():
    current_urls = {1: "https://old.example/a"}
    data = b"article_id,corrected_url\r\n1,https://new.example/a\r\n"
    updates, skipped, errors = parse_manual_review_corrections_csv(data, current_urls)
    assert updates == [{"article_id": 1, "current_url": "https://old.example/a",
                        "corrected_url": "https://new.example/a"}]
    assert skipped == []
    assert errors == []


def test_parse_manual_review_csv_blank_corrected_url_is_skipped_not_error():
    current_urls = {1: "https://old.example/a"}
    data = b"article_id,corrected_url\r\n1,\r\n"
    updates, skipped, errors = parse_manual_review_corrections_csv(data, current_urls)
    assert updates == []
    assert errors == []
    assert len(skipped) == 1


def test_parse_manual_review_csv_identical_url_is_skipped_not_error():
    current_urls = {1: "https://old.example/a"}
    data = b"article_id,corrected_url\r\n1,https://old.example/a\r\n"
    updates, skipped, errors = parse_manual_review_corrections_csv(data, current_urls)
    assert updates == []
    assert len(skipped) == 1


def test_parse_manual_review_csv_malformed_url_is_error():
    current_urls = {1: "https://old.example/a"}
    data = b"article_id,corrected_url\r\n1,not-a-url\r\n"
    updates, skipped, errors = parse_manual_review_corrections_csv(data, current_urls)
    assert updates == []
    assert len(errors) == 1
    assert "not-a-url" in errors[0]["raw"]


def test_parse_manual_review_csv_unrecognized_article_id_is_error():
    current_urls = {1: "https://old.example/a"}
    data = b"article_id,corrected_url\r\n99,https://new.example/z\r\n"
    updates, skipped, errors = parse_manual_review_corrections_csv(data, current_urls)
    assert updates == []
    assert len(errors) == 1


def test_parse_manual_review_csv_missing_required_column_raises():
    with pytest.raises(ValueError):
        parse_manual_review_corrections_csv(b"foo,bar\r\n1,2\r\n", {1: "https://x"})


# ---------------------------------------------------------------------------
# linklib.domain_migration — title matching + Exa search
# ---------------------------------------------------------------------------

def test_titles_match_exact():
    assert dm_mod._titles_match("My Great Post", "My Great Post")


def test_titles_match_reformatted():
    assert dm_mod._titles_match("My Great Post: A Story", "my great post a story")


def test_titles_match_rejects_unrelated():
    assert not dm_mod._titles_match("My Great Post", "Completely Different Article")


def test_find_migrated_url_no_api_key(monkeypatch):
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    url, cost = dm_mod.find_migrated_url(None, "new.example", "Some Title")
    assert url is None
    assert cost == 0.0


def test_find_migrated_url_returns_title_matching_hit(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "fake-key")

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"results": [
                {"title": "An Unrelated Post", "url": "https://new.example/unrelated"},
                {"title": "My Great Post", "url": "https://new.example/my-great-post"},
            ]}

    monkeypatch.setattr(dm_mod.requests, "post", lambda *a, **kw: _Resp())
    url, cost = dm_mod.find_migrated_url(None, "new.example", "My Great Post")
    assert url == "https://new.example/my-great-post"
    assert cost >= 0.0


def test_find_migrated_url_no_match_returns_none(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "fake-key")

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"results": [{"title": "Totally Unrelated", "url": "https://new.example/x"}]}

    monkeypatch.setattr(dm_mod.requests, "post", lambda *a, **kw: _Resp())
    url, cost = dm_mod.find_migrated_url(None, "new.example", "My Great Post")
    assert url is None


def test_find_migrated_url_network_error_returns_none(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "fake-key")

    def _raise(*a, **kw):
        raise ConnectionError("boom")

    monkeypatch.setattr(dm_mod.requests, "post", _raise)
    url, cost = dm_mod.find_migrated_url(None, "new.example", "My Great Post")
    assert url is None
    assert cost == 0.0


# ---------------------------------------------------------------------------
# linklib.pipeline — domain-migration fetch tier
# ---------------------------------------------------------------------------

def test_domain_migration_target_detection():
    assert pl._domain_migration_target("https://pointsandfigures.com/2020/01/01/x/") == "jeffreycarter.substack.com"
    assert pl._domain_migration_target("https://www.avc.com/2020/01/x.html") == "avc.xyz"
    assert pl._domain_migration_target("https://karenroterdavis.com/2020/01/01/x/") == "karenroterdavis.wordpress.com"
    assert pl._domain_migration_target("https://calacanis.com/2020/01/01/x/") == "calacanis.substack.com"
    assert pl._domain_migration_target("https://www.newageaccounting.ai/p/some-post") == "substack.newageaccounting.ai"
    assert pl._domain_migration_target("https://example.com/normal") == ""


def test_backfill_uses_migration_tier_before_wayback_on_match(lib, monkeypatch):
    """A migration-domain article whose direct fetch fails should try the
    migration tier FIRST — a success there must skip Wayback entirely and
    log exactly ONE content_refetch_log row, source='migration'."""
    article_id = _seed(lib, url="https://pointsandfigures.com/2020/01/01/some-post/",
                       title="Some Post")
    monkeypatch.setattr(extract_mod, "fetch_page",
                        lambda url: PageData(title="", content="", fetch_error="HTTP 403"))

    monkeypatch.setattr(dm_mod, "find_migrated_url",
                        lambda lib_, domain, title: ("https://jeffreycarter.substack.com/p/some-post", 0.005))

    migrated_html = ("<html><body><article>" +
                     "<p>Migrated content, definitely long enough to pass the sanity check.</p>" * 15 +
                     "</article></body></html>")
    migrated_page = PageData(title="Some Post", content="Migrated content, plenty of real words here. " * 20,
                             blocked=False, raw_html=migrated_html)

    wayback_called = []
    monkeypatch.setattr(wayback_mod, "find_snapshot_verbose",
                        lambda url: wayback_called.append(url) or (None, "unreached"))

    def _fetch_page(url):
        if "jeffreycarter.substack.com" in url:
            return migrated_page
        return PageData(title="", content="", fetch_error="HTTP 403")
    monkeypatch.setattr(extract_mod, "fetch_page", _fetch_page)

    ok, reason = pl.backfill_article_content(lib, lib.get_article(article_id))
    assert ok is True
    assert wayback_called == [], "must not attempt Wayback once the migration tier already succeeded"

    log = lib.list_content_refetch_log()
    assert len(log) == 1, "exactly one content_refetch_log row per backfill_article_content() call"
    assert log[0]["status"] == "success"
    assert log[0]["source"] == "migration"
    assert "jeffreycarter.substack.com" in log[0]["detail"]

    row = lib.get_article(article_id)
    assert "Migrated content" in row["content_html"]


def test_backfill_migration_miss_falls_through_to_wayback(lib, monkeypatch):
    """A migration-domain article where Exa finds no match must fall
    through to the pre-existing Wayback fallback, still logging exactly
    ONE row (no double-log between the two tiers)."""
    article_id = _seed(lib, url="https://avc.com/2020/01/x.html", title="Some AVC Post")
    monkeypatch.setattr(extract_mod, "fetch_page",
                        lambda url: PageData(title="", content="", fetch_error="HTTP 403"))
    monkeypatch.setattr(dm_mod, "find_migrated_url", lambda lib_, domain, title: (None, 0.0))

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


def test_backfill_no_migration_domain_match_skips_tier_entirely(lib, monkeypatch):
    """A non-migration-domain article must never even call
    domain_migration.find_migrated_url."""
    article_id = _seed(lib, url="https://example.com/normal-post", title="Normal")
    monkeypatch.setattr(extract_mod, "fetch_page",
                        lambda url: PageData(title="", content="", fetch_error="HTTP 500"))
    called = []
    monkeypatch.setattr(dm_mod, "find_migrated_url",
                        lambda lib_, domain, title: (called.append(domain) or None, 0.0))
    monkeypatch.setattr(wayback_mod, "find_snapshot_verbose", lambda url: (None, "no snapshot archived"))

    ok, reason = pl.backfill_article_content(lib, lib.get_article(article_id))
    assert ok is False
    assert called == []


def test_backfill_migration_domain_but_no_title_skips_tier(lib, monkeypatch):
    """No title to search Exa with -> migration tier is skipped outright,
    straight to Wayback, without ever calling find_migrated_url."""
    article_id = _seed(lib, url="https://pointsandfigures.com/x/", title="")
    monkeypatch.setattr(extract_mod, "fetch_page",
                        lambda url: PageData(title="", content="", fetch_error="HTTP 403"))
    called = []
    monkeypatch.setattr(dm_mod, "find_migrated_url",
                        lambda lib_, domain, title: (called.append(1) or None, 0.0))
    monkeypatch.setattr(wayback_mod, "find_snapshot_verbose", lambda url: (None, "no snapshot archived"))

    ok, reason = pl.backfill_article_content(lib, lib.get_article(article_id))
    assert ok is False
    assert called == []


def test_count_migration_content_latest_attempt_only(lib):
    a1 = _seed(lib, url="https://example.com/one")
    a2 = _seed(lib, url="https://example.com/two")
    lib.log_content_refetch_attempt(a1, "success", source="migration")
    lib.log_content_refetch_attempt(a2, "success", source="direct")
    assert lib.count_migration_content() == 1

    lib.log_content_refetch_attempt(a1, "success", source="direct")
    assert lib.count_migration_content() == 0


# ---------------------------------------------------------------------------
# Regression: defunct-service behavior is unchanged by any of the above
# ---------------------------------------------------------------------------

def test_defunct_service_still_skips_fetch_and_wayback_and_migration(lib, monkeypatch):
    article_id = _seed(lib, url="http://feedproxy.google.com/~r/AVc/~3/z/")
    fetch_called = []
    monkeypatch.setattr(extract_mod, "fetch_page", lambda url: fetch_called.append(url) or PageData("", ""))
    migration_called = []
    monkeypatch.setattr(dm_mod, "find_migrated_url",
                        lambda lib_, domain, title: (migration_called.append(1) or None, 0.0))

    ok, reason = pl.backfill_article_content(lib, lib.get_article(article_id))
    assert ok is False
    assert reason == "defunct-service"
    assert fetch_called == []
    assert migration_called == []


# ---------------------------------------------------------------------------
# Admin page — 5-tile stats grid, needs-manual-review list, via Migration
# badge, and the export/preview/commit CSV round trip
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


def test_admin_page_shows_needs_review_tile_and_section(env):
    lib = env._lib()
    a = _seed(lib, url="https://example.com/flagged3", title="Flagged Article")
    for _ in range(Library._MANUAL_REVIEW_ATTEMPT_THRESHOLD):
        lib.log_content_refetch_attempt(a, "failure", reason="bot-challenge", detail="Cloudflare")
    lib.close()

    c = _admin_client(env)
    r = c.get("/admin/library/backfill-content")
    assert r.status_code == 200
    assert "Needs review" in r.text
    assert "Needs manual review (1)" in r.text
    assert "Flagged Article" in r.text
    assert "Bot challenge" in r.text


def test_admin_page_shows_via_migration_badge(env):
    lib = env._lib()
    a = _seed(lib, url="https://jeffreycarter.substack.com/p/x")
    lib.log_content_refetch_attempt(a, "success", source="migration",
                                    detail="https://jeffreycarter.substack.com/p/x")
    lib.close()

    c = _admin_client(env)
    r = c.get("/admin/library/backfill-content")
    assert "via Migration" in r.text


def test_admin_page_5_stat_tiles_use_responsive_grid_not_fixed_columns(env):
    """Regression guard for the documented mobile-CSS-Grid-blowout pattern
    (Phase P): a fixed repeat(N,1fr) can't shrink below its content's
    min-width and forces horizontal scroll on narrow viewports."""
    c = _admin_client(env)
    r = c.get("/admin/library/backfill-content")
    assert "repeat(auto-fit,minmax(130px,1fr))" in r.text
    assert "repeat(3,1fr)" not in r.text


def test_manual_review_csv_export_import_round_trip_live(env):
    """Live end-to-end verification per the acceptance criteria: export,
    correct a URL, import (preview then commit), and confirm the article is
    requeued for the next backfill run with its NEW url — and that nothing
    is written until commit is confirmed."""
    lib = env._lib()
    a = _seed(lib, url="https://old-blog.example/post-1", title="Needs A New Home")
    for _ in range(Library._MANUAL_REVIEW_ATTEMPT_THRESHOLD):
        lib.log_content_refetch_attempt(a, "failure", reason="bot-challenge")
    lib.close()

    c = _admin_client(env)

    # 1. Export.
    export_resp = c.get("/admin/library/backfill-content/manual-review/export.csv")
    assert export_resp.status_code == 200
    assert "article_id,title,current_url,reason,attempt_count,last_attempted_at,corrected_url" in export_resp.text
    assert f"{a}," in export_resp.text
    assert "old-blog.example/post-1" in export_resp.text

    # 2. Edit the CSV — fill in corrected_url.
    edited_csv = (
        "article_id,title,current_url,reason,attempt_count,last_attempted_at,corrected_url\r\n"
        f"{a},Needs A New Home,https://old-blog.example/post-1,bot-challenge,3,,https://new-blog.example/post-1\r\n"
    )

    # 3. Preview — nothing written yet.
    preview_resp = c.post(
        "/admin/library/backfill-content/manual-review/import/preview",
        files={"file": ("corrections.csv", edited_csv, "text/csv")},
    )
    assert preview_resp.status_code == 200
    assert "Ready to apply (1)" in preview_resp.text
    assert "new-blog.example/post-1" in preview_resp.text

    lib2 = env._lib()
    unchanged = lib2.get_article(a)
    lib2.close()
    assert unchanged["url"] == "https://old-blog.example/post-1", \
        "preview must not write anything to the database"

    # 4. Confirm — commit.
    commit_resp = c.post(
        "/admin/library/backfill-content/manual-review/import/commit",
        data={"article_id": [str(a)], "corrected_url": ["https://new-blog.example/post-1"]},
        follow_redirects=False,
    )
    assert commit_resp.status_code == 303

    lib3 = env._lib()
    corrected = lib3.get_article(a)
    assert corrected["url"] == "https://new-blog.example/post-1"
    log = lib3.list_url_correction_log()
    assert len(log) == 1
    assert log[0]["new_url"] == "https://new-blog.example/post-1"
    # Requeued: no longer needs-manual-review, back in default backfill scope
    # — the next run will target the NEW url via the article's own row.
    assert a not in lib3._manual_review_article_ids()
    default_ids = [r["id"] for r in lib3.articles_needing_content_backfill()]
    assert a in default_ids
    requeued_row = lib3.get_article(a)
    assert requeued_row["url"] == "https://new-blog.example/post-1"
    lib3.close()


def test_manual_review_import_commit_alone_without_preview_still_requires_form_data(env):
    c = _admin_client(env)
    resp = c.post("/admin/library/backfill-content/manual-review/import/commit",
                  data={}, follow_redirects=False)
    assert resp.status_code == 303
    assert "error=" in resp.headers["location"]


def test_manual_review_import_commit_url_collision_does_not_500_batch(env):
    """Regression test for the bare-500 bug: a corrected_url that collides
    with another article's already-saved URL (articles.url is UNIQUE) must
    fail cleanly with a readable per-row message, while an earlier AND a
    later clean row in the same batch both still commit — the collision
    must not abort the whole request the way it did before this fix."""
    lib = env._lib()
    # Already independently saved — this is what the middle row will collide with.
    existing_id = _seed(lib, url="https://k9ventures.com/2020/01/some-post",
                        title="Existing K9 post")

    before_id = _seed(lib, url="https://example.com/before", title="Before")
    colliding_id = _seed(lib, url="https://exitround.com/post-2", title="Exitround post")
    after_id = _seed(lib, url="https://example2.com/after", title="After")
    for aid in (before_id, colliding_id, after_id):
        for _ in range(Library._MANUAL_REVIEW_ATTEMPT_THRESHOLD):
            lib.log_content_refetch_attempt(aid, "failure", reason="bot-challenge")
    lib.close()

    c = _admin_client(env)
    resp = c.post(
        "/admin/library/backfill-content/manual-review/import/commit",
        data={
            "article_id": [str(before_id), str(colliding_id), str(after_id)],
            "corrected_url": [
                "https://www.example.com/before",
                "https://k9ventures.com/2020/01/some-post",  # collides with existing_id
                "https://www.example2.com/after",
            ],
        },
        follow_redirects=False,
    )
    # No bare 500 — a normal redirect back to the admin page with a message,
    # same shape as every other partial-failure batch.
    assert resp.status_code == 303
    assert "msg=" in resp.headers["location"]

    lib2 = env._lib()
    # The clean row BEFORE the collision committed.
    assert lib2.get_article(before_id)["url"] == "https://www.example.com/before"
    # The clean row AFTER the collision also committed — the loop kept going.
    assert lib2.get_article(after_id)["url"] == "https://www.example2.com/after"
    # The colliding row itself was left untouched, not silently dropped or
    # half-applied — it stays exactly where it was, still flagged.
    assert lib2.get_article(colliding_id)["url"] == "https://exitround.com/post-2"
    assert colliding_id in lib2._manual_review_article_ids()
    # The pre-existing article that owns the contested URL is unaffected.
    assert lib2.get_article(existing_id)["url"] == "https://k9ventures.com/2020/01/some-post"
    lib2.close()

    # The redirect message names the collision with a readable reason.
    from urllib.parse import unquote
    location = resp.headers["location"]
    msg = unquote(location.split("msg=", 1)[1])
    assert f"already belongs to article #{existing_id}" in msg
    assert "Applied 2 corrections" in msg
