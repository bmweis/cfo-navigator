"""Bulk delete (one-off cleanup batches, 2026-08) — the CSV export/preview/
commit round trip for a specific, admin-known list of URLs, plus the
single-article "Remove from library" action wired into the Reader toolbar.

Covers:
- linklib.library_delete_csv.parse_library_delete_csv: confirmed/skipped/
  errors three-way split, the MAX_DELETE_PER_RUN cap failing the whole
  import, a missing header failing the whole import, an unrecognized
  confirm_delete value treated as an error (not silently skipped), a
  duplicate url in the same file flagged as an error, and resolution
  against a live resolve_url callback rather than a fixed candidate set.
- Library.get_article_by_url: the resolution lookup this tool (and the
  Reader's own "already saved?" check) is built on.
- The three routes: template download, preview (auth-gated, re-resolves
  by URL, renders a typed-count confirm field), commit (typed-count guard,
  per-run cap defense-in-depth, TOCTOU re-validation via URL, archive_audit_
  log logging, pre-delete backup_now, real deletion via Library.
  purge_article — the same delete mechanism the Purge tool uses).
- POST /library/{article_id}/delete stays reachable from an AJAX context
  (the Reader toolbar's "Remove from library" button): a redirect response
  still resolves to r.ok once fetch follows it, so no route-signature
  change was needed to wire it up.

See CLAUDE.md's Reader-toolbar-delete bullet and linklib.library_delete_csv's
module docstring for the full write-up.
"""
import os
import pathlib
import sys
import tempfile
from urllib.parse import unquote

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Article, Library
from linklib.library_delete_csv import parse_library_delete_csv, MAX_DELETE_PER_RUN, MAX_ROWS

GOOD = ("Real article body with plenty of words in it. " * 20).strip()


@pytest.fixture
def lib(tmp_path):
    db = Library(str(tmp_path / "t.db"))
    try:
        yield db
    finally:
        db.close()


def _article(lib, url="https://example.com/keep-me"):
    return lib.upsert(Article(url=url, title="Some Article", content=GOOD))


# ---------------------------------------------------------------------------
# Library.get_article_by_url
# ---------------------------------------------------------------------------

def test_get_article_by_url_matches_the_natural_key(lib):
    aid = _article(lib, url="https://example.com/a")
    row = lib.get_article_by_url("https://example.com/a")
    assert row is not None
    assert row["id"] == aid


def test_get_article_by_url_returns_none_for_unknown_url(lib):
    assert lib.get_article_by_url("https://example.com/nope") is None


# ---------------------------------------------------------------------------
# linklib.library_delete_csv.parse_library_delete_csv
# ---------------------------------------------------------------------------

def _csv(rows, header=("url", "confirm_delete")):
    lines = [",".join(header)]
    lines += [",".join(str(c) for c in r) for r in rows]
    return ("\n".join(lines)).encode("utf-8")


def _resolver(known: dict):
    """known: {url: {"id","title","word_count"}}"""
    def _resolve(url):
        return known.get(url)
    return _resolve


def test_confirmed_skipped_errors_three_way_split():
    known = {
        "https://a": {"id": 1, "title": "A", "word_count": 3},
        "https://b": {"id": 2, "title": "B", "word_count": 5},
    }
    data = _csv([("https://a", "yes"), ("https://b", ""), ("https://c", "yes")])
    confirmed, skipped, errors = parse_library_delete_csv(data, _resolver(known))
    assert [c["article_id"] for c in confirmed] == [1]
    assert [s["url"] for s in skipped] == ["https://b"]
    assert len(errors) == 1
    assert "https://c" in errors[0]["reason"]


def test_recognized_yes_and_no_markers():
    known = {f"https://{i}": {"id": i, "title": "", "word_count": 0} for i in range(1, 7)}
    data = _csv([("https://1", "yes"), ("https://2", "y"), ("https://3", "1"),
                 ("https://4", "x"), ("https://5", "no"), ("https://6", "0")])
    confirmed, skipped, errors = parse_library_delete_csv(data, _resolver(known))
    assert {c["article_id"] for c in confirmed} == {1, 2, 3, 4}
    assert {s["url"] for s in skipped} == {"https://5", "https://6"}
    assert errors == []


def test_unrecognized_confirm_value_is_an_error_not_a_silent_skip():
    known = {"https://a": {"id": 1, "title": "", "word_count": 0}}
    data = _csv([("https://a", "maybe")])
    confirmed, skipped, errors = parse_library_delete_csv(data, _resolver(known))
    assert confirmed == []
    assert skipped == []
    assert len(errors) == 1
    assert "maybe" in errors[0]["reason"]


def test_url_that_does_not_resolve_is_an_error():
    data = _csv([("https://nope", "yes")])
    confirmed, skipped, errors = parse_library_delete_csv(data, _resolver({}))
    assert confirmed == []
    assert len(errors) == 1
    assert "doesn't match any article" in errors[0]["reason"]


def test_duplicate_url_in_same_file_is_an_error_on_the_second_occurrence():
    known = {"https://a": {"id": 1, "title": "", "word_count": 0}}
    data = _csv([("https://a", "yes"), ("https://a", "yes")])
    confirmed, skipped, errors = parse_library_delete_csv(data, _resolver(known))
    assert [c["article_id"] for c in confirmed] == [1]
    assert len(errors) == 1
    assert "more than once" in errors[0]["reason"]


def test_blank_url_is_an_error():
    data = _csv([("", "yes")])
    confirmed, skipped, errors = parse_library_delete_csv(data, _resolver({}))
    assert confirmed == []
    assert len(errors) == 1
    assert "blank" in errors[0]["reason"]


def test_missing_header_column_raises():
    data = b"url\nhttps://a"
    with pytest.raises(ValueError, match="Missing required column"):
        parse_library_delete_csv(data, _resolver({}))


def test_empty_file_raises():
    with pytest.raises(ValueError, match="empty"):
        parse_library_delete_csv(b"", _resolver({}))


def test_exceeding_max_delete_per_run_raises_and_fails_the_whole_import():
    known = {f"https://{i}": {"id": i, "title": "", "word_count": 0}
             for i in range(1, MAX_DELETE_PER_RUN + 2)}
    data = _csv([(f"https://{i}", "yes") for i in range(1, MAX_DELETE_PER_RUN + 2)])
    with pytest.raises(ValueError, match=f"{MAX_DELETE_PER_RUN}-per-run cap"):
        parse_library_delete_csv(data, _resolver(known))


def test_max_rows_cap_reports_an_error_not_a_silent_truncation():
    known = {f"https://{i}": {"id": i, "title": "", "word_count": 0} for i in range(1, MAX_ROWS + 5)}
    data = _csv([(f"https://{i}", "no") for i in range(1, MAX_ROWS + 5)])  # all "no", cap never trips
    confirmed, skipped, errors = parse_library_delete_csv(data, _resolver(known))
    assert any("import limit" in e["reason"] for e in errors)


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


def test_page_requires_auth(env):
    from fastapi.testclient import TestClient
    c = TestClient(env.app)
    r = c.get("/admin/reader/bulk-delete", follow_redirects=False)
    assert r.status_code in (302, 303)


def test_page_lists_the_tool(env):
    c = _admin_client(env)
    r = c.get("/admin/reader/bulk-delete")
    assert r.status_code == 200
    assert "Bulk delete articles" in r.text


def test_template_download_has_the_two_required_columns(env):
    c = _admin_client(env)
    r = c.get("/admin/reader/bulk-delete/template.csv")
    assert r.status_code == 200
    assert r.text.strip() == "url,confirm_delete"


def test_preview_route_requires_auth(env):
    from fastapi.testclient import TestClient
    c = TestClient(env.app)
    r = c.post("/admin/reader/bulk-delete/preview",
               files={"file": ("d.csv", b"url,confirm_delete\nhttps://a,yes", "text/csv")})
    assert r.status_code == 401


def test_preview_route_shows_confirmed_rows_and_typed_count_field(env):
    lib = env._lib()
    aid = _article(lib, url="https://example.com/target")
    lib.close()

    c = _admin_client(env)
    csv_body = b"url,confirm_delete\nhttps://example.com/target,yes"
    r = c.post("/admin/reader/bulk-delete/preview",
              files={"file": ("d.csv", csv_body, "text/csv")})
    assert r.status_code == 200
    assert "Confirmed for deletion (1)" in r.text
    assert "Type <strong>1</strong> to confirm" in r.text
    assert f'value="{aid}"' in r.text
    # nothing deleted yet
    lib = env._lib()
    assert lib.get_article(aid) is not None
    lib.close()


def test_preview_route_flags_unmatched_url_as_an_error(env):
    c = _admin_client(env)
    csv_body = b"url,confirm_delete\nhttps://example.com/never-saved,yes"
    r = c.post("/admin/reader/bulk-delete/preview",
              files={"file": ("d.csv", csv_body, "text/csv")})
    assert r.status_code == 200
    assert "Errors (1)" in r.text
    assert "Nothing to delete" in r.text


def test_commit_route_requires_matching_typed_count(env):
    lib = env._lib()
    aid = _article(lib, url="https://example.com/target")
    url = "https://example.com/target"
    lib.close()

    c = _admin_client(env)
    r = c.post("/admin/reader/bulk-delete/commit",
              data={"article_id": [str(aid)], "url": [url], "confirm_count": "2"},
              follow_redirects=False)
    assert r.status_code == 303
    assert "did not match" in unquote(r.headers["location"])

    lib = env._lib()
    assert lib.get_article(aid) is not None  # never deleted
    lib.close()


def test_commit_route_deletes_when_count_matches(env):
    lib = env._lib()
    aid = _article(lib, url="https://example.com/target")
    url = "https://example.com/target"
    lib.close()

    c = _admin_client(env)
    r = c.post("/admin/reader/bulk-delete/commit",
              data={"article_id": [str(aid)], "url": [url], "confirm_count": "1"},
              follow_redirects=False)
    assert r.status_code == 303
    assert "Deleted 1 article" in unquote(r.headers["location"])

    lib = env._lib()
    assert lib.get_article(aid) is None
    lib.close()


def test_commit_route_logs_archive_audit_for_a_real_admin(env):
    lib = env._lib()
    aid = _article(lib, url="https://example.com/target")
    url = "https://example.com/target"
    lib.create_user("realadmin", "realpass", role="admin", name="Real Admin")
    lib.close()

    from fastapi.testclient import TestClient
    c = TestClient(env.app, raise_server_exceptions=True)
    c.post("/login", data={"username": "realadmin", "password": "realpass"}, follow_redirects=False)

    r = c.post("/admin/reader/bulk-delete/commit",
              data={"article_id": [str(aid)], "url": [url], "confirm_count": "1"},
              follow_redirects=False)
    assert r.status_code == 303

    lib = env._lib()
    audit_rows = [row for row in lib.list_archive_audit_log(limit=100) if row["item_id"] == aid]
    assert len(audit_rows) == 1
    assert audit_rows[0]["action"] == "delete"
    assert "bulk-delete" in audit_rows[0]["detail"]
    lib.close()


def test_commit_route_skips_row_whose_url_changed_since_preview(env):
    """TOCTOU: if the article's URL was corrected between preview and
    commit, the (article_id, url) pair no longer matches and the row is
    skipped, not deleted."""
    lib = env._lib()
    aid = _article(lib, url="https://example.com/target")
    lib.apply_article_url_correction(aid, "https://example.com/moved")
    lib.close()

    c = _admin_client(env)
    r = c.post("/admin/reader/bulk-delete/commit",
              data={"article_id": [str(aid)], "url": ["https://example.com/target"],
                    "confirm_count": "1"},
              follow_redirects=False)
    assert r.status_code == 303
    assert "Deleted 0 article" in unquote(r.headers["location"])

    lib = env._lib()
    assert lib.get_article(aid) is not None
    lib.close()


def test_commit_route_enforces_per_run_cap_even_on_a_hand_crafted_post(env):
    lib = env._lib()
    pairs = [(str(_article(lib, url=f"https://example.com/many-{i}")),
              f"https://example.com/many-{i}") for i in range(MAX_DELETE_PER_RUN + 1)]
    lib.close()

    c = _admin_client(env)
    r = c.post("/admin/reader/bulk-delete/commit",
              data={"article_id": [p[0] for p in pairs], "url": [p[1] for p in pairs],
                    "confirm_count": str(len(pairs))},
              follow_redirects=False)
    assert r.status_code == 303
    assert "per-run cap" in unquote(r.headers["location"])

    lib = env._lib()
    assert lib.count() == len(pairs)  # nothing deleted
    lib.close()


def test_commit_route_aborts_if_pre_delete_backup_fails(env, monkeypatch):
    lib = env._lib()
    aid = _article(lib, url="https://example.com/target")
    url = "https://example.com/target"
    lib.close()

    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("GOOGLE_OAUTH_REFRESH_TOKEN", "rtoken")
    monkeypatch.setattr(env.backup, "backup_now",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("Drive unreachable")))

    c = _admin_client(env)
    r = c.post("/admin/reader/bulk-delete/commit",
              data={"article_id": [str(aid)], "url": [url], "confirm_count": "1"},
              follow_redirects=False)
    assert r.status_code == 303
    assert "Pre-delete backup failed" in unquote(r.headers["location"])

    lib = env._lib()
    assert lib.get_article(aid) is not None  # never deleted
    lib.close()


# ---------------------------------------------------------------------------
# POST /library/{article_id}/delete reachable from the Reader toolbar's AJAX
# call — pre-existing route (Phase 5-era, previously with zero callers, same
# shape the tag-editing route was in before Phase 5c wired it up).
# ---------------------------------------------------------------------------

def test_single_article_delete_route_works_and_is_admin_only(env):
    lib = env._lib()
    aid = _article(lib, url="https://example.com/single")
    lib.close()

    from fastapi.testclient import TestClient
    anon = TestClient(env.app, raise_server_exceptions=True)
    r = anon.post(f"/library/{aid}/delete", follow_redirects=False)
    assert r.status_code == 401

    c = _admin_client(env)
    # fetch()-style follow: this is what the Reader's rrDeleteCurrent() relies
    # on — a redirect response still resolves as r.ok once followed.
    r = c.post(f"/library/{aid}/delete", follow_redirects=True)
    assert r.status_code == 200

    lib = env._lib()
    assert lib.get_article(aid) is None
    lib.close()


def test_reader_toolbar_shows_remove_button_for_a_saved_article(env):
    lib = env._lib()
    _article(lib, url="https://example.com/toolbar-check")
    lib.close()

    c = _admin_client(env)
    r = c.get("/read")
    assert r.status_code == 200
    assert "rrDeleteCurrent" in r.text
    assert "Remove from library" in r.text
