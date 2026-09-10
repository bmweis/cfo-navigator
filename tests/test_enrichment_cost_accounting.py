"""Enrichment overhead-cost ledger (issue #105, scoped down from a general
ledger once article_embeddings turned out to be the only real precedent —
see the Phase 0 investigation on #105).

Pins the same overhead-vs-user-cap distinction test_embedding_cost_accounting.py
already pins for embeddings: enrichment_cost.cost_usd is Brian's overhead and
must never be summed into ask_questions or count toward a user's monthly Ask
cap.
"""
import pathlib
import sys
import types

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import enrich
from linklib.db import Article, Library
from linklib import pipeline


@pytest.fixture
def lib(tmp_path):
    lib = Library(str(tmp_path / "t.db"))
    yield lib
    lib.close()


def _mock_anthropic(monkeypatch, payload_json, input_tokens=100, output_tokens=40):
    def _create(**kw):
        class _Block:
            type = "text"
            text = payload_json
        usage = types.SimpleNamespace(
            input_tokens=input_tokens, output_tokens=output_tokens,
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
        )
        return types.SimpleNamespace(content=[_Block()], usage=usage)
    fake = types.SimpleNamespace(Anthropic=lambda *a, **k: types.SimpleNamespace(
        messages=types.SimpleNamespace(create=lambda **kw: _create(**kw))))
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")


_PAYLOAD = '{"summary": "s", "tags": ["finance"]}'


def test_enrich_returns_real_usage_and_cost(monkeypatch):
    _mock_anthropic(monkeypatch, _PAYLOAD, input_tokens=1000, output_tokens=200)
    result = enrich.enrich("Title", "Some article text", model="claude-haiku-4-5-20251001")
    assert result is not None
    assert result.input_tokens == 1000
    assert result.output_tokens == 200
    assert result.cost_usd > 0


def test_enrich_without_api_key_returns_none(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert enrich.enrich("Title", "text") is None


def test_ingest_url_records_enrichment_cost(lib, monkeypatch):
    _mock_anthropic(monkeypatch, _PAYLOAD, input_tokens=500, output_tokens=100)
    row = pipeline.ingest_url(lib, "https://ex.com/a", fetch_fulltext=False)
    article_id = row["id"]

    ledger_row = lib.conn.execute(
        "SELECT article_id, model, input_tokens, output_tokens, cost_usd FROM enrichment_cost "
        "WHERE article_id=?", (article_id,)
    ).fetchone()
    assert ledger_row is not None
    assert ledger_row["input_tokens"] == 500
    assert ledger_row["output_tokens"] == 100
    assert ledger_row["cost_usd"] > 0

    # Overhead, not user-cap: a fresh library's ask_questions table is untouched.
    assert lib.conn.execute("SELECT COUNT(*) FROM ask_questions").fetchone()[0] == 0


def test_enrich_library_backfill_records_one_row_per_article(lib, monkeypatch):
    _mock_anthropic(monkeypatch, _PAYLOAD)
    aid1 = lib.upsert(Article(url="https://ex.com/a", title="A", content="text one"))
    aid2 = lib.upsert(Article(url="https://ex.com/b", title="B", content="text two"))

    done = pipeline.enrich_library(lib, fetch=False)
    assert done == 2

    rows = lib.conn.execute("SELECT article_id FROM enrichment_cost").fetchall()
    assert {r["article_id"] for r in rows} == {aid1, aid2}


def test_costed_enrichment_with_no_article_yet_records_null_article_id(lib, monkeypatch):
    """`record_enrichment_cost(None, ...)` is the documented shape for
    enrichment spend that has no `articles.id` yet to attach to — used by
    every batch-generation script (scripts/regen_ai_drafted_fields.py and
    friends), not just the now-retired Archive Queue's own candidates."""
    _mock_anthropic(monkeypatch, _PAYLOAD, input_tokens=300, output_tokens=60)
    result = enrich.enrich("C", "Some candidate text", model="claude-opus-4-8")
    assert result is not None
    assert result.cost_usd > 0

    lib.record_enrichment_cost(None, result.model, input_tokens=result.input_tokens,
                               output_tokens=result.output_tokens, cost_usd=result.cost_usd)

    row = lib.conn.execute(
        "SELECT article_id, cost_usd FROM enrichment_cost WHERE article_id IS NULL"
    ).fetchone()
    assert row is not None
    assert row["cost_usd"] > 0


def test_overhead_cost_helpers(lib):
    lib.record_enrichment_cost(1, "claude-haiku-4-5-20251001",
                               input_tokens=1000, output_tokens=200, cost_usd=0.002)
    lib.record_enrichment_cost(None, "claude-opus-4-8",
                               input_tokens=500, output_tokens=100, cost_usd=0.01)

    assert lib.enrichment_cost_total() == pytest.approx(0.012)

    breakdown = {b["source"]: b for b in lib.overhead_cost_breakdown()}
    assert breakdown["Enrichment"]["count"] == 2
    assert breakdown["Enrichment"]["total_cost"] == pytest.approx(0.012)
    assert breakdown["Embeddings"]["count"] == 0

    by_month = lib.overhead_cost_by_month()
    assert len(by_month) == 1
    assert by_month[0]["cost_usd"] == pytest.approx(0.012)


def test_enrichment_cost_never_counts_toward_ask_cap(lib):
    uid = lib.create_user("brian", "pw", role="admin")
    lib.record_enrichment_cost(1, "claude-haiku-4-5-20251001", cost_usd=5.00)
    assert lib.ask_cost_all_time(uid) == 0.0
    assert lib.ask_cost_this_month(uid) == 0.0
