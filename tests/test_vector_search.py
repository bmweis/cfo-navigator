"""Tests for the vector-search layer added to linklib/db.py (#93): the
articles_vec virtual table, upsert_article_embedding, vector_search, the
embedding_hashes/embedding_content_hash staleness lookups, delete_article
cleanup, and graceful degradation when sqlite-vec isn't available.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import embeddings as embed_mod
from linklib.db import Article, Library


@pytest.fixture
def lib(tmp_path):
    lib = Library(str(tmp_path / "t.db"))
    yield lib
    lib.close()


def _vec(*head: float) -> list[float]:
    """A full-width (EMBED_DIM) vector with the given leading values and
    zeros padding the rest — the articles_vec table is fixed-width, so every
    inserted vector must match embed_mod.EMBED_DIM exactly."""
    v = list(head)
    return v + [0.0] * (embed_mod.EMBED_DIM - len(v))


def test_vector_search_available_on_a_fresh_db(lib):
    # sqlite-vec is a hard dependency (requirements.txt) — on any environment
    # that installed it correctly, this must be True.
    assert lib.vector_search_available() is True


def test_upsert_and_vector_search_roundtrip(lib):
    a1 = lib.upsert(Article(url="https://ex.com/a", title="A"))
    a2 = lib.upsert(Article(url="https://ex.com/b", title="B"))
    lib.upsert_article_embedding(a1, _vec(1.0, 0.0), "hashA", "text-embedding-3-small")
    lib.upsert_article_embedding(a2, _vec(0.0, 1.0), "hashB", "text-embedding-3-small")

    # Query close to a1's vector — a1 should rank first.
    hits = lib.vector_search(_vec(0.9, 0.1), limit=5)
    assert [h["id"] for h in hits] == [a1, a2]
    assert hits[0]["title"] == "A"


def test_vector_search_empty_vector_is_a_noop(lib):
    assert lib.vector_search([], limit=5) == []


def test_vector_search_respects_limit(lib):
    ids = [lib.upsert(Article(url=f"https://ex.com/{i}", title=str(i))) for i in range(5)]
    for i, aid in zip(range(5), ids):
        lib.upsert_article_embedding(aid, _vec(float(i)), f"h{i}", "m")
    hits = lib.vector_search(_vec(0.0), limit=2)
    assert len(hits) == 2


def test_upsert_article_embedding_overwrites_prior_vector(lib):
    """A re-embed (content changed) must replace the old vector, not error
    out on a duplicate rowid — vec0 rejects INSERT OR REPLACE on a duplicate
    rowid, so upsert_article_embedding does delete-then-insert internally."""
    aid = lib.upsert(Article(url="https://ex.com/a", title="A"))
    lib.upsert_article_embedding(aid, _vec(1.0, 0.0), "hash1", "m")
    lib.upsert_article_embedding(aid, _vec(0.0, 1.0), "hash2", "m")  # re-embed
    assert lib.embedding_hashes()[aid] == "hash2"
    # Nearest to the NEW vector should be this article, confirming the old
    # vector was actually replaced rather than sitting alongside it.
    hits = lib.vector_search(_vec(0.0, 0.9), limit=1)
    assert hits[0]["id"] == aid


def test_embedding_hashes_bulk_map(lib):
    a1 = lib.upsert(Article(url="https://ex.com/a"))
    a2 = lib.upsert(Article(url="https://ex.com/b"))
    lib.upsert_article_embedding(a1, _vec(1.0), "h1", "m")
    lib.upsert_article_embedding(a2, _vec(0.0, 1.0), "h2", "m")
    assert lib.embedding_hashes() == {a1: "h1", a2: "h2"}


def test_embedding_content_hash_single_lookup(lib):
    aid = lib.upsert(Article(url="https://ex.com/a"))
    assert lib.embedding_content_hash(aid) is None
    lib.upsert_article_embedding(aid, _vec(1.0), "hash1", "m")
    assert lib.embedding_content_hash(aid) == "hash1"


def test_upsert_article_embedding_records_overhead_cost(lib):
    aid = lib.upsert(Article(url="https://ex.com/a"))
    lib.upsert_article_embedding(aid, _vec(1.0), "h", "text-embedding-3-small",
                                 input_tokens=500, cost_usd=0.00001)
    row = lib.conn.execute(
        "SELECT input_tokens, cost_usd, model FROM article_embeddings WHERE article_id=?", (aid,)
    ).fetchone()
    assert row["input_tokens"] == 500
    assert row["cost_usd"] == pytest.approx(0.00001)
    assert row["model"] == "text-embedding-3-small"


def test_delete_article_cleans_up_vector_and_ledger(lib):
    aid = lib.upsert(Article(url="https://ex.com/a"))
    lib.upsert_article_embedding(aid, _vec(1.0), "h", "m")
    lib.delete_article(aid)
    assert lib.embedding_hashes() == {}
    assert lib.vector_search(_vec(1.0), limit=5) == []


def test_vector_search_unavailable_degrades_to_empty(lib, monkeypatch):
    """Simulate an environment where sqlite-vec failed to load — every
    vector-search call must return an empty result rather than raise, so
    callers (agent.retrieve) fall back to FTS5-only cleanly."""
    monkeypatch.setattr(lib, "_vec_available", False)
    assert lib.vector_search(_vec(1.0), limit=5) == []
    assert lib.upsert_article_embedding(1, _vec(1.0), "h", "m") is False


def test_delete_article_safe_when_vector_search_unavailable(lib, monkeypatch):
    aid = lib.upsert(Article(url="https://ex.com/a"))
    monkeypatch.setattr(lib, "_vec_available", False)
    lib.delete_article(aid)  # must not raise even though it can't touch articles_vec
    assert lib.conn.execute("SELECT COUNT(*) FROM articles WHERE id=?", (aid,)).fetchone()[0] == 0
