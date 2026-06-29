"""Near-duplicate feedback loop: the dedupe checker learns from accept/reject.

Decisions are stored per unordered URL pair. Rejected pairs are suppressed from
future scans; past calls are handed to the Claude verifier as guidance.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Library
from linklib import dedupe as dd


@pytest.fixture
def lib(tmp_path):
    db = Library(str(tmp_path / "t.db"))
    try:
        yield db
    finally:
        db.close()


def _a(aid, title, url):
    return {"id": aid, "title": title, "url": url, "summary": "", "published_at": "2025-03-01"}


def test_decision_roundtrip_and_counts(lib):
    a = _a(1, "How to hire a VP of Sales", "https://x.com/a")
    b = _a(2, "When to hire your first VP of Sales", "https://x.com/b")
    lib.record_dedupe_decision(a, b, "distinct", "SaaStr")
    assert lib.dedupe_decision_counts() == (0, 1)
    # order-independent: same pair, reversed, upserts (not a new row)
    lib.record_dedupe_decision(b, a, "distinct", "SaaStr")
    assert lib.dedupe_decision_counts() == (0, 1)
    # distinct_pairs uses the same key the dedupe module computes
    assert dd._pair_key(a, b) in lib.distinct_pairs()
    assert dd._pair_key(b, a) in lib.distinct_pairs()


def test_verdict_can_flip(lib):
    a = _a(1, "T1", "https://x.com/a")
    b = _a(2, "T2", "https://x.com/b")
    lib.record_dedupe_decision(a, b, "distinct", "S")
    lib.record_dedupe_decision(a, b, "dup", "S")
    assert lib.dedupe_decision_counts() == (1, 0)
    assert dd._pair_key(a, b) not in lib.distinct_pairs()


def test_recent_decisions_for_teaching(lib):
    lib.record_dedupe_decision(_a(1, "A", "u1"), _a(2, "B", "u2"), "distinct", "SaaStr")
    lib.record_dedupe_decision(_a(3, "C", "u3"), _a(4, "D", "u4"), "dup", "SaaStr")
    rows = lib.dedupe_decisions(limit=10)
    assert len(rows) == 2
    assert {r["verdict"] for r in rows} == {"dup", "distinct"}


def test_verify_prunes_known_distinct_pairs(lib, monkeypatch):
    # A candidate cluster of two; the curator already said they're NOT dupes.
    a = _a(1, "Dear SaaStr: How Can I Become a Better VP of Sales?", "https://x.com/a")
    b = _a(2, "Dear SaaStr: How Do I Become a Great Sales Rep?", "https://x.com/b")
    lib.record_dedupe_decision(a, b, "distinct", "SaaStr")
    # Even with no API key (fallback path), the rejected pair is pruned away.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out, _status = dd.verify_clusters([[a, b]], source="SaaStr",
                                      distinct_pairs=lib.distinct_pairs())
    assert out == []


def test_learned_block_renders_both_verdicts():
    decisions = [
        {"title_a": "VP of Sales", "title_b": "Sales Rep", "verdict": "distinct"},
        {"title_a": "10% Rule talk", "title_b": "World-Class CS talk", "verdict": "dup"},
    ]
    block = dd._learned_block(decisions)
    assert "NOT duplicates" in block and "ARE duplicates" in block
    assert "VP of Sales" in block and "World-Class CS talk" in block
    assert dd._learned_block([]) == ""
