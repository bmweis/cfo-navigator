"""Durability audit item 3 — a durable start/finish record for the three
_JOB_STATE-backed background jobs (re-enrich, Historical sweep, Reader
content backfill), so a Railway redeploy/crash doesn't erase whether a job
last succeeded, failed, or ever ran.

Covers:
- Library.start_job_run/finish_job_run/latest_job_run/list_job_run_log.
- Each of the three job functions (_enrich_job, _backfill_job,
  _content_backfill_job) writes a start row and a matching finish row on
  success, on failure, and (content backfill only) on a deliberate stop.
- _JOB_STATE itself is untouched by any of this (still the live-progress
  source of truth).
- _job_run_banner()'s three states (never run / success / failure) render
  on each job's own admin page.

See ARCHITECTURE.md's job_run_log table row and CLAUDE.md's matching
durability-audit-item-3 bullet.
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


# ---------------------------------------------------------------------------
# Library methods
# ---------------------------------------------------------------------------

def test_start_and_finish_job_run_round_trip(lib):
    run_id = lib.start_job_run("enrich")
    assert isinstance(run_id, int)

    running = lib.latest_job_run("enrich")
    assert running["status"] == "running"
    assert running["finished_at"] == ""
    assert running["started_at"]

    lib.finish_job_run(run_id, "success", summary="10/10 enriched")
    done = lib.latest_job_run("enrich")
    assert done["status"] == "success"
    assert done["summary"] == "10/10 enriched"
    assert done["finished_at"]


def test_latest_job_run_returns_none_when_never_run(lib):
    assert lib.latest_job_run("enrich") is None


def test_latest_job_run_is_most_recent_by_started_at(lib):
    r1 = lib.start_job_run("enrich")
    lib.finish_job_run(r1, "success", summary="first")
    r2 = lib.start_job_run("enrich")
    lib.finish_job_run(r2, "failure", error="boom")

    latest = lib.latest_job_run("enrich")
    assert latest["id"] == r2
    assert latest["status"] == "failure"
    assert latest["error"] == "boom"


def test_job_run_log_is_scoped_per_job_name(lib):
    lib.finish_job_run(lib.start_job_run("enrich"), "success", summary="enrich run")
    lib.finish_job_run(lib.start_job_run("backfill"), "success", summary="backfill run")

    assert lib.latest_job_run("enrich")["summary"] == "enrich run"
    assert lib.latest_job_run("backfill")["summary"] == "backfill run"
    assert lib.latest_job_run("content_backfill") is None


def test_list_job_run_log_most_recent_first(lib):
    for i in range(3):
        rid = lib.start_job_run("content_backfill")
        lib.finish_job_run(rid, "success", summary=f"run {i}")

    rows = lib.list_job_run_log("content_backfill")
    assert len(rows) == 3
    assert [r["summary"] for r in rows] == ["run 2", "run 1", "run 0"]


def test_a_crashed_run_leaves_finished_at_empty(lib):
    """The whole point: start_job_run is called, the process dies before
    finish_job_run — the row stays status='running', finished_at=''
    forever, which is itself the durable signal a crash happened."""
    lib.start_job_run("enrich")
    stuck = lib.latest_job_run("enrich")
    assert stuck["status"] == "running"
    assert stuck["finished_at"] == ""


# ---------------------------------------------------------------------------
# The three job functions write job_run_log rows
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


def test_enrich_job_writes_success_run(env, monkeypatch):
    lib = env._lib()
    lib.upsert(Article(url="https://example.com/a", title="A", content="x"))
    lib.close()

    import linklib.pipeline as pl_mod
    monkeypatch.setattr(pl_mod, "enrich_library", lambda *a, **k: None)
    monkeypatch.setattr(env.backup, "maybe_backup", lambda *a, **k: None)

    env._enrich_job(force=False, model="fake-model", limit=100)

    lib = env._lib()
    run = lib.latest_job_run("enrich")
    lib.close()
    assert run["status"] == "success"
    assert run["finished_at"]
    assert "enriched" in run["summary"]
    # _JOB_STATE unaffected in shape — still the live-progress source
    assert env._job_get("enrich")["running"] is False


def test_enrich_job_writes_failure_run(env, monkeypatch):
    import linklib.pipeline as pl_mod

    def _boom(*a, **k):
        raise RuntimeError("API key missing")
    monkeypatch.setattr(pl_mod, "enrich_library", _boom)

    env._enrich_job(force=False, model="fake-model", limit=100)

    lib = env._lib()
    run = lib.latest_job_run("enrich")
    lib.close()
    assert run["status"] == "failure"
    assert "API key missing" in run["error"]


def test_content_backfill_job_writes_stopped_run(env, monkeypatch):
    lib = env._lib()
    for i in range(5):
        lib.upsert(Article(url=f"https://example.com/stop-{i}", title=str(i)))
    lib.close()

    def _fake_backfill(lib_arg, row):
        env._job_set("content_backfill", stop_requested=True)
        return True, ""

    monkeypatch.setattr(env, "time", type("T", (), {"sleep": staticmethod(lambda *_: None)}))
    import linklib.pipeline as pl_mod
    monkeypatch.setattr(pl_mod, "backfill_article_content", _fake_backfill)

    env._content_backfill_job(limit=100000, force=False)

    lib = env._lib()
    run = lib.latest_job_run("content_backfill")
    lib.close()
    assert run["status"] == "stopped"
    assert run["finished_at"]
    assert "stopped after 1" in run["summary"]


def test_content_backfill_job_writes_success_run(env, monkeypatch):
    import linklib.pipeline as pl_mod
    monkeypatch.setattr(pl_mod, "backfill_article_content", lambda lib, row: (True, ""))
    monkeypatch.setattr(env.backup, "maybe_backup", lambda *a, **k: None)

    env._content_backfill_job(limit=100000, force=False)

    lib = env._lib()
    run = lib.latest_job_run("content_backfill")
    lib.close()
    assert run["status"] == "success"


# ---------------------------------------------------------------------------
# Admin-page banner
# ---------------------------------------------------------------------------

def _admin_client(appmod):
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app, raise_server_exceptions=True)
    c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    return c


def test_enrich_page_shows_never_run_banner(env):
    c = _admin_client(env)
    r = c.get("/admin/library/enrich")
    assert r.status_code == 200
    assert "No run recorded yet." in r.text


def test_enrich_page_shows_success_banner(env):
    lib = env._lib()
    lib.finish_job_run(lib.start_job_run("enrich"), "success", summary="7/7 enriched")
    lib.close()

    c = _admin_client(env)
    r = c.get("/admin/library/enrich")
    assert r.status_code == 200
    assert "succeeded" in r.text
    assert "7/7 enriched" in r.text


def test_content_backfill_page_shows_failure_banner(env):
    lib = env._lib()
    lib.finish_job_run(lib.start_job_run("content_backfill"), "failure", error="timeout")
    lib.close()

    c = _admin_client(env)
    r = c.get("/admin/library/backfill-content")
    assert r.status_code == 200
    assert "failed" in r.text
    assert "timeout" in r.text


def test_queue_page_shows_backfill_banner(env):
    lib = env._lib()
    lib.finish_job_run(lib.start_job_run("backfill"), "success", summary="3 article(s) added across 5 source(s)")
    lib.close()

    c = _admin_client(env)
    r = c.get("/admin/library/queue")
    assert r.status_code == 200
    assert "3 article(s) added across 5 source(s)" in r.text
