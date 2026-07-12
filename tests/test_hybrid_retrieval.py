"""Tests for hybrid library retrieval (#93): the reciprocal-rank-fusion
merge in linklib/agent.py, and agent.retrieve()'s FTS5 + vector-search
wiring, including its fallback-to-FTS5-only contract when vector search is
unavailable or the query embedding call fails.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import agent
from linklib import embeddings as embed_mod
from linklib.db import Article, Library


@pytest.fixture
def lib(tmp_path):
    lib = Library(str(tmp_path / "t.db"))
    yield lib
    lib.close()


# --- _rrf_merge (pure function, no DB) ----------------------------------------

def test_rrf_merge_ranks_items_in_both_lists_highest():
    a = {"id": 1, "title": "A"}
    b = {"id": 2, "title": "B"}
    c = {"id": 3, "title": "C"}
    # a: rank 0 in both lists. b: rank 1 in list one only. c: rank 0 in list
    # two only. a should win; between b and c, c (top of a list) should beat
    # b (second place in a list) under standard RRF weighting.
    fts = [a, b]
    vec = [c, a]
    merged = agent._rrf_merge([fts, vec], limit=10)
    assert merged[0]["id"] == 1
    ids = [m["id"] for m in merged]
    assert set(ids) == {1, 2, 3}


def test_rrf_merge_dedupes_by_id():
    a = {"id": 1, "title": "A"}
    merged = agent._rrf_merge([[a], [a]], limit=10)
    assert len(merged) == 1


def test_rrf_merge_respects_limit():
    items = [{"id": i, "title": str(i)} for i in range(20)]
    merged = agent._rrf_merge([items], limit=5)
    assert len(merged) == 5


def test_rrf_merge_empty_lists():
    assert agent._rrf_merge([[], []], limit=5) == []


def test_rrf_merge_item_missing_id_is_skipped():
    merged = agent._rrf_merge([[{"title": "no id"}]], limit=5)
    assert merged == []


# --- agent.retrieve(): hybrid wiring ------------------------------------------

def test_retrieve_falls_back_to_fts_only_when_vector_unavailable(lib, monkeypatch):
    lib.upsert(Article(url="https://ex.com/a", title="SaaS NRR benchmarks",
                       summary="net revenue retention"))
    monkeypatch.setattr(lib, "_vec_available", False)

    hits, embed_in, embed_cost = agent.retrieve(lib, "NRR benchmarks", max_sources=5)
    assert len(hits) == 1
    assert embed_in == 0
    assert embed_cost == 0.0


def test_retrieve_falls_back_when_embedding_call_fails(lib, monkeypatch):
    lib.upsert(Article(url="https://ex.com/a", title="SaaS NRR benchmarks",
                       summary="net revenue retention"))
    monkeypatch.setattr(embed_mod, "embed_text", lambda q, model=None: None)

    hits, embed_in, embed_cost = agent.retrieve(lib, "NRR benchmarks", max_sources=5)
    assert len(hits) == 1
    assert embed_in == 0
    assert embed_cost == 0.0


def test_retrieve_merges_fts_and_vector_hits(lib, monkeypatch):
    # "keyword_match" shares no useful terms with the query but is the exact
    # keyword match FTS finds; "semantic_match" is what vector search alone
    # would surface (simulated — sqlite-vec's actual ANN math isn't under
    # test here, only that agent.retrieve wires a vector hit into the merge).
    kw_id = lib.upsert(Article(url="https://ex.com/kw", title="burn multiple benchmarks",
                               summary="burn multiple by stage"))
    sem_id = lib.upsert(Article(url="https://ex.com/sem", title="runway math",
                                summary="how long the money lasts"))
    vector = [1.0] + [0.0] * (embed_mod.EMBED_DIM - 1)
    lib.upsert_article_embedding(sem_id, vector, "h", "text-embedding-3-small")

    fake_result = embed_mod.EmbedResult(vectors=[vector], input_tokens=12, cost_usd=0.0000002)
    monkeypatch.setattr(embed_mod, "embed_text", lambda q, model=None: fake_result)

    hits, embed_in, embed_cost = agent.retrieve(lib, "burn multiple", max_sources=5)
    ids = {h["id"] for h in hits}
    assert kw_id in ids          # FTS path still finds the keyword match
    assert sem_id in ids         # vector path contributes the semantic-only match
    assert embed_in == 12
    assert embed_cost == pytest.approx(0.0000002)


def test_retrieve_use_library_false_never_calls_retrieve(monkeypatch):
    """answer_question must not call retrieve() at all when use_library is
    False — pinning the call site, not retrieve() itself."""
    def boom(*a, **k):
        raise AssertionError("retrieve() must not run when use_library=False")
    monkeypatch.setattr(agent, "retrieve", boom)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    ans = agent.answer_question(None, "a question", use_library=False, use_web=False)
    assert ans.embed_input_tokens == 0
    assert ans.embed_cost_usd == 0.0
