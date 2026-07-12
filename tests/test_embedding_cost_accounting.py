"""Pins the overhead-vs-user-cap cost distinction that #93's plan promised:

- Embed-ON-SAVE cost (linklib.pipeline.embed_article) is Brian's overhead —
  it lands on article_embeddings.cost_usd and must NEVER be summed into
  ask_questions, so it can never count toward a user's monthly Ask cap.
- Query-time embedding cost (embedding the retrieval QUESTION, inside
  agent.answer_question) is a user-cap cost — it folds into the turn's
  cost_usd like the follow-up rewrite's cost already does, and DOES count
  toward the monthly cap SUM.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import agent
from linklib import embeddings as embed_mod
from linklib.db import Article, Library
from linklib.pipeline import embed_article


@pytest.fixture
def lib(tmp_path):
    lib = Library(str(tmp_path / "t.db"))
    yield lib
    lib.close()


def test_embed_on_save_cost_is_overhead_not_user_cap(lib, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    fake = embed_mod.EmbedResult(vectors=[[0.1, 0.2] + [0.0] * (embed_mod.EMBED_DIM - 2)],
                                 input_tokens=800, cost_usd=0.000016)
    monkeypatch.setattr(embed_mod, "embed_texts", lambda texts, model=embed_mod.DEFAULT_MODEL: fake)

    aid = lib.upsert(Article(url="https://ex.com/a", title="A", summary="S"))
    uid = lib.create_user("brian", "pw", role="admin")

    ok = embed_article(lib, aid)
    assert ok is True

    # The cost landed on the overhead ledger...
    row = lib.conn.execute(
        "SELECT cost_usd FROM article_embeddings WHERE article_id=?", (aid,)
    ).fetchone()
    assert row["cost_usd"] == pytest.approx(0.000016)

    # ...and nowhere in ask_questions — a user with zero Ask activity still
    # shows $0 spent, proving embed-on-save never touches their cap.
    assert lib.ask_cost_all_time(uid) == 0.0
    assert lib.ask_cost_this_month(uid) == 0.0
    assert lib.conn.execute("SELECT COUNT(*) FROM ask_questions").fetchone()[0] == 0


def test_query_time_embed_cost_counts_toward_user_cap(lib):
    uid = lib.create_user("brian", "pw", role="admin")
    row_id = lib.record_ask_question(
        uid, "q", "a", "claude-sonnet-4-6", "standard", True, False, True,
        cost_usd=0.02, embed_input_tokens=15, embed_cost_usd=0.0000003,
    )
    # embed_cost_usd is stored broken out on the row...
    row = lib.conn.execute(
        "SELECT embed_input_tokens, embed_cost_usd, cost_usd FROM ask_questions WHERE id=?",
        (row_id,),
    ).fetchone()
    assert row["embed_input_tokens"] == 15
    assert row["embed_cost_usd"] == pytest.approx(0.0000003)
    # ...but is already folded into cost_usd (the turn total the cap checks),
    # exactly like rewrite_cost_usd — the caller doesn't add it separately.
    assert row["cost_usd"] == pytest.approx(0.02)
    assert lib.ask_cost_this_month(uid) == pytest.approx(0.02)


def test_answer_question_folds_embed_cost_into_turn_total(monkeypatch):
    """answer_question's Answer.cost_usd must include the query-embedding
    cost that retrieve() reports, the same way it already includes the
    follow-up rewrite's cost — pinning agent.py's call-site wiring, not
    retrieve() itself (see test_hybrid_retrieval.py for that)."""
    def fake_retrieve(lib, question, max_sources=8):
        return [], 7, 0.0000005
    monkeypatch.setattr(agent, "retrieve", fake_retrieve)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    ans = agent.answer_question(None, "a question", use_web=False, use_library=True)
    assert ans.embed_input_tokens == 7
    assert ans.embed_cost_usd == pytest.approx(0.0000005)
    assert ans.cost_usd == pytest.approx(0.0000005)
