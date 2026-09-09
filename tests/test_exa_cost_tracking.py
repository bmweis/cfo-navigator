"""Exa cost-tracking foundation (pre-dashboard, 2026-09).

Two gaps closed here, both flagged by the AI usage/cost dashboard's own
Step 0 investigation:

1. `Answer.exa_cost_usd`/`exa_result_count` (linklib/agent.py's retrieve_exa)
   were always computed on every Buddy turn but never persisted to
   `ask_questions` — unlike `embed_cost_usd`/`rewrite_cost_usd`, which each
   have their own column and are recorded on every call. Covered here at
   both the storage layer (record_ask_question) and end to end through
   `POST /ask` -> webapp.ask_orchestrator.run_ask, the same two-layer split
   test_embedding_cost_accounting.py already uses for embed_cost_usd.

2. The Reader content backfill's fallback tiers (linklib/domain_migration.py,
   linklib/medium_platform.py) call Exa with zero cost computation at all —
   not tracked anywhere, not even content_refetch_log. Covered here via the
   real module functions (mocked HTTP only), asserting a real
   compute_exa_cost() figure lands on the content_refetch_log row
   linklib.pipeline._finish_backfill_after_direct_failure writes.
"""
import os
import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import agent
from linklib import domain_migration as dm_mod
from linklib import medium_platform as mp_mod
from linklib import pipeline
from linklib.db import Library
from linklib.pricing import compute_exa_cost


@pytest.fixture
def lib(tmp_path):
    lib = Library(str(tmp_path / "t.db"))
    yield lib
    lib.close()


# ---------------------------------------------------------------------------
# Fix 1a: storage layer — record_ask_question persists exa_result_count/
# exa_cost_usd, already folded into cost_usd, same convention as embed_*.
# ---------------------------------------------------------------------------

def test_record_ask_question_persists_exa_cost_and_result_count(lib):
    uid = lib.create_user("brian", "pw", role="admin")
    row_id = lib.record_ask_question(
        uid, "q", "a", "claude-sonnet-4-6", "standard", True, False, True,
        cost_usd=0.015, exa_result_count=6, exa_cost_usd=0.007,
    )
    row = lib.conn.execute(
        "SELECT exa_result_count, exa_cost_usd, cost_usd FROM ask_questions WHERE id=?",
        (row_id,),
    ).fetchone()
    assert row["exa_result_count"] == 6
    assert row["exa_cost_usd"] == pytest.approx(0.007)
    # Already folded into cost_usd (the turn total the monthly cap sums) —
    # the caller doesn't add it separately, same as rewrite_cost_usd/
    # embed_cost_usd.
    assert row["cost_usd"] == pytest.approx(0.015)
    assert lib.ask_cost_this_month(uid) == pytest.approx(0.015)


def test_record_ask_question_exa_fields_default_to_zero(lib):
    """A caller that never mentions Exa (library/feed-only turn) gets a
    plain zero row, not a missing column or a crash — matching every other
    *_cost_usd breakout column's own default."""
    uid = lib.create_user("brian", "pw", role="admin")
    row_id = lib.record_ask_question(
        uid, "q", "a", "claude-sonnet-4-6", "standard", True, False, False, cost_usd=0.01)
    row = lib.conn.execute(
        "SELECT exa_result_count, exa_cost_usd FROM ask_questions WHERE id=?", (row_id,)
    ).fetchone()
    assert row["exa_result_count"] == 0
    assert row["exa_cost_usd"] == 0.0


# ---------------------------------------------------------------------------
# Fix 1b: end to end — a real Buddy turn (POST /ask -> run_ask) actually
# passes Answer.exa_cost_usd/exa_result_count through to the stored row,
# instead of silently dropping them the way ask_orchestrator.run_ask used to.
# ---------------------------------------------------------------------------

@pytest.fixture
def app_env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SAVE_TOKEN", "tok-secret")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    lib = Library(db)
    uid = lib.create_user("member1", "supersecret", role="user", name="Member One")
    lib.close()
    yield appmod, uid
    if os.path.exists(db):
        os.remove(db)


def test_post_ask_persists_exa_cost_and_result_count(app_env, monkeypatch):
    """A turn whose retrieval actually used Exa (Answer.exa_result_count/
    exa_cost_usd populated) must land on the stored ask_questions row via
    the real POST /ask -> run_ask -> record_ask_question path — this is
    the regression the dashboard's Step 0 investigation flagged: the field
    was computed and then silently dropped before this fix."""
    appmod, uid = app_env
    from fastapi.testclient import TestClient

    def fake_answer(lib, question, **kwargs):
        return agent.Answer(
            text="stub answer with a web source [1]", model="claude-sonnet-4-6",
            cost_usd=0.021, exa_result_count=4, exa_cost_usd=0.007,
        )

    monkeypatch.setattr(agent, "answer_question", fake_answer)

    c = TestClient(appmod.app, raise_server_exceptions=True)
    c.post("/login", data={"username": "member1", "password": "supersecret"},
           follow_redirects=False)
    resp = c.post("/ask", json={"question": "What's our NRR?"})
    assert resp.status_code == 200
    turn_id = resp.json()["turn_id"]

    lib = Library(os.environ["LINKLIB_DB"])
    try:
        row = lib.conn.execute(
            "SELECT exa_result_count, exa_cost_usd, cost_usd FROM ask_questions WHERE id=?",
            (turn_id,),
        ).fetchone()
    finally:
        lib.close()
    assert row["exa_result_count"] == 4
    assert row["exa_cost_usd"] == pytest.approx(0.007)
    assert row["cost_usd"] == pytest.approx(0.021)


def test_post_ask_zero_exa_when_answer_never_used_exa(app_env, monkeypatch):
    """A turn that never touched Exa (library-only, or Exa disabled/no key)
    stores a plain zero — no false-positive cost recorded."""
    appmod, uid = app_env
    from fastapi.testclient import TestClient

    def fake_answer(lib, question, **kwargs):
        return agent.Answer(text="library-only answer", model="claude-sonnet-4-6", cost_usd=0.01)

    monkeypatch.setattr(agent, "answer_question", fake_answer)

    c = TestClient(appmod.app, raise_server_exceptions=True)
    c.post("/login", data={"username": "member1", "password": "supersecret"},
           follow_redirects=False)
    resp = c.post("/ask", json={"question": "What's our burn?"})
    turn_id = resp.json()["turn_id"]

    lib = Library(os.environ["LINKLIB_DB"])
    try:
        row = lib.conn.execute(
            "SELECT exa_result_count, exa_cost_usd FROM ask_questions WHERE id=?", (turn_id,)
        ).fetchone()
    finally:
        lib.close()
    assert row["exa_result_count"] == 0
    assert row["exa_cost_usd"] == 0.0


# ---------------------------------------------------------------------------
# Fix 2: Reader content backfill's domain-migration and Medium-platform
# tiers now compute + log real Exa cost, via content_refetch_log.
# ---------------------------------------------------------------------------

class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_domain_migration_find_migrated_url_returns_real_cost(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "fake")
    results = [{"url": "https://new.example/x", "title": "My Great Post"}]
    monkeypatch.setattr(dm_mod.requests, "post",
                        lambda *a, **k: _FakeResp({"results": results}))

    url, cost = dm_mod.find_migrated_url(None, "new.example", "My Great Post")
    assert url == "https://new.example/x"
    assert cost == pytest.approx(compute_exa_cost("search", num_results=1))
    assert cost > 0


def test_domain_migration_find_migrated_url_miss_still_reports_real_cost():
    """A genuine Exa call that comes back with no title match still cost
    money — the miss must not silently report $0."""
    def fake_post(*a, **k):
        return _FakeResp({"results": [{"url": "https://new.example/y", "title": "Unrelated"}]})

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("EXA_API_KEY", "fake")
        mp.setattr(dm_mod.requests, "post", fake_post)
        url, cost = dm_mod.find_migrated_url(None, "new.example", "My Great Post")
    assert url is None
    assert cost == pytest.approx(compute_exa_cost("search", num_results=1))
    assert cost > 0


def test_medium_platform_fetch_content_by_url_returns_real_cost(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "fake")
    monkeypatch.setattr(mp_mod.requests, "post",
                        lambda *a, **k: _FakeResp({"results": [{"text": "x " * 400}]}))

    text, cost = mp_mod.fetch_content_by_url(None, "https://medium.com/@a/post")
    assert text.strip()
    assert cost > 0


def test_medium_platform_find_medium_candidate_returns_real_cost(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "fake")
    results = [{"url": "https://other.example/z", "title": "My Great Post", "text": "body"}]
    monkeypatch.setattr(mp_mod.requests, "post",
                        lambda *a, **k: _FakeResp({"results": results}))

    url, text, cost = mp_mod.find_medium_candidate(None, "My Great Post")
    assert url == "https://other.example/z"
    assert cost == pytest.approx(compute_exa_cost("search", num_results=1))
    assert cost > 0


def test_domain_migration_tier_logs_exa_cost_to_content_refetch_log(lib, monkeypatch):
    """The Reader backfill's domain-migration tier success path
    (linklib.pipeline._try_domain_migration/_finish_backfill_after_direct_
    failure) writes its real Exa spend onto the content_refetch_log row —
    previously not tracked anywhere at all."""
    from linklib.db import Article
    from linklib.extract import PageData

    aid = lib.upsert(Article(url="https://pointsandfigures.com/i", title="Some Title"))

    monkeypatch.setattr(dm_mod, "find_migrated_url",
                        lambda lib_, dom, title: ("https://jeffreycarter.substack.com/p/x", 0.0091))

    good_html = ("<html><body><article><p>" +
                 ("Real substantial article body. " * 40) + "</p></article></body></html>")

    def fake_fetch_page(url, *a, **k):
        if url == "https://pointsandfigures.com/i":
            # The direct fetch of the ORIGINAL url must fail — only then
            # does backfill_article_content ever try the migration tier.
            return PageData(title="", content="", raw_html="", fetch_error="HTTP 404")
        return PageData(title="X", content="Real substantial article body. " * 40,
                        raw_html=good_html)

    monkeypatch.setattr("linklib.extract.fetch_page", fake_fetch_page)

    ok, reason = pipeline.backfill_article_content(lib, lib.get_article(aid))
    assert ok is True

    row = lib.conn.execute(
        "SELECT status, source, exa_cost_usd FROM content_refetch_log "
        "WHERE article_id=? ORDER BY id DESC LIMIT 1", (aid,)
    ).fetchone()
    assert row["status"] == "success"
    assert row["source"] == "migration"
    assert row["exa_cost_usd"] == pytest.approx(0.0091)


def test_medium_platform_tier_miss_still_logs_accumulated_exa_cost(lib, monkeypatch):
    """Even when every tier misses and the call falls through to Wayback
    (which itself has no Exa cost), whatever the migration/Medium tiers
    actually spent along the way must still land on the final logged row —
    money spent on a miss isn't free just because nothing higher up
    succeeded."""
    from linklib.db import Article
    from linklib.extract import PageData

    aid = lib.upsert(Article(url="https://medium.com/@a/stuck-post", title="Stuck Post"))

    monkeypatch.setattr("linklib.extract.fetch_page",
                        lambda *a, **k: PageData(title="", content="", raw_html="",
                                                 fetch_error="HTTP 403"))
    monkeypatch.setattr(mp_mod, "fetch_content_by_url",
                        lambda lib_, url: ("", 0.001))
    monkeypatch.setattr(mp_mod, "find_medium_candidate",
                        lambda lib_, title, author="": ("", "", 0.008))
    monkeypatch.setattr("linklib.wayback.find_snapshot_verbose",
                        lambda url: (None, "no snapshot"))

    ok, reason = pipeline.backfill_article_content(lib, lib.get_article(aid))
    assert ok is False

    row = lib.conn.execute(
        "SELECT status, exa_cost_usd FROM content_refetch_log "
        "WHERE article_id=? ORDER BY id DESC LIMIT 1", (aid,)
    ).fetchone()
    assert row["status"] == "failure"
    # 0.001 (fetch-by-url) + 0.008 (search-by-title) — both misses, both real spend.
    assert row["exa_cost_usd"] == pytest.approx(0.009)


def test_content_refetch_log_direct_success_has_zero_exa_cost(lib, monkeypatch):
    """A plain direct-fetch success never reaches Exa at all — the column
    stays 0, not left blank or omitted."""
    from linklib.db import Article
    from linklib.extract import PageData

    aid = lib.upsert(Article(url="https://ordinary.example/a", title="Ordinary"))
    monkeypatch.setattr("linklib.extract.fetch_page",
                        lambda *a, **k: PageData(
                            title="X", content="Real substantial article body. " * 40,
                            raw_html="<html><body><article><p>" +
                                     ("Real substantial article body. " * 40) +
                                     "</p></article></body></html>"))

    ok, reason = pipeline.backfill_article_content(lib, lib.get_article(aid))
    assert ok is True
    row = lib.conn.execute(
        "SELECT source, exa_cost_usd FROM content_refetch_log "
        "WHERE article_id=? ORDER BY id DESC LIMIT 1", (aid,)
    ).fetchone()
    assert row["source"] == "direct"
    assert row["exa_cost_usd"] == 0.0
