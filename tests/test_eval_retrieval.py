"""Tests for scripts/eval_retrieval.py (#93): the manual-QA replay tool that
compares FTS5-only vs. hybrid retrieval on previously-flagged FP&A Buddy
questions. No Claude calls, no real network — the embedding client is faked.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import scripts.eval_retrieval as ev
from linklib import embeddings as embed_mod
from linklib.db import Article, Library


@pytest.fixture
def lib(tmp_path):
    lib = Library(str(tmp_path / "t.db"))
    yield lib
    lib.close()


def test_no_flagged_questions_prints_nothing_to_replay(lib, capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["eval_retrieval.py", "--db", lib.path])
    rc = ev.main()
    assert rc == 0
    assert "nothing to replay" in capsys.readouterr().out


def test_flagged_question_shows_fts_and_hybrid_columns(lib, capsys, monkeypatch):
    kw_id = lib.upsert(Article(url="https://ex.com/kw", title="burn multiple benchmarks",
                               summary="burn multiple by stage"))
    sem_id = lib.upsert(Article(url="https://ex.com/sem", title="runway math",
                                summary="how long the money lasts"))
    vector = [1.0] + [0.0] * (embed_mod.EMBED_DIM - 1)
    lib.upsert_article_embedding(sem_id, vector, "h", "text-embedding-3-small")

    uid = lib.create_user("brian", "pw", role="admin")
    qid = lib.record_ask_question(
        uid, "burn multiple", "an answer that missed runway context",
        "claude-sonnet-4-6", "standard", True, False, True, cost_usd=0.02,
        citations=[{"n": 1, "title": "burn multiple benchmarks",
                   "url": "https://ex.com/kw", "type": "library", "article_id": kw_id}],
    )
    lib.record_ask_feedback(qid, uid, "not_helpful", comment="missed the runway angle")

    fake_result = embed_mod.EmbedResult(vectors=[vector], input_tokens=5, cost_usd=0.0000001)
    monkeypatch.setattr(embed_mod, "embed_text", lambda q, model=None: fake_result)
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    monkeypatch.setattr(sys, "argv", ["eval_retrieval.py", "--db", lib.path])

    rc = ev.main()
    out = capsys.readouterr().out
    assert rc == 0
    assert "burn multiple" in out
    assert "not_helpful" in out
    assert "missed the runway angle" in out
    assert "burn multiple benchmarks" in out    # originally cited + FTS hit
    assert "runway math" in out                 # the vector-only / hybrid contribution


def test_helpful_rated_questions_are_not_replayed(lib, capsys, monkeypatch):
    uid = lib.create_user("brian", "pw", role="admin")
    qid = lib.record_ask_question(uid, "q", "a", "m", "standard", True, False, True)
    lib.record_ask_feedback(qid, uid, "helpful")

    monkeypatch.setattr(sys, "argv", ["eval_retrieval.py", "--db", lib.path])
    rc = ev.main()
    assert rc == 0
    assert "nothing to replay" in capsys.readouterr().out


def test_missing_openai_key_falls_back_to_fts_only(lib, capsys, monkeypatch):
    uid = lib.create_user("brian", "pw", role="admin")
    lib.upsert(Article(url="https://ex.com/a", title="A match", summary="q terms"))
    qid = lib.record_ask_question(uid, "q terms", "a", "m", "standard", True, False, True)
    lib.record_ask_feedback(qid, uid, "inaccurate")

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(sys, "argv", ["eval_retrieval.py", "--db", lib.path])
    rc = ev.main()
    out = capsys.readouterr().out
    assert rc == 0
    assert "OLD  (FTS5-only)" in out
    assert "VECTOR-only" not in out   # no key -> no vector column attempted
