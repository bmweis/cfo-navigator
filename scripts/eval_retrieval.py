#!/usr/bin/env python3
"""Replay flagged FP&A Buddy questions through old vs. new library retrieval
(#93) so a human can judge whether hybrid retrieval actually helps, before
merging or after a fresh embed_backfill run.

"Flagged" = ask_feedback rows rated 'inaccurate' or 'not_helpful' — the
questions where a member said the ORIGINAL answer's sources were bad enough
to flag. This tool makes no Claude calls: it isolates the retrieval step
that #93 actually changed (FTS5-only vs. FTS5+vector hybrid), so the
comparison is cheap, fast, and about the thing under review.

This is a manual-QA tool, not an automated pass/fail eval — there's no
ground-truth relevance labels for a personal archive, so a human has to
judge whether the new candidates are actually better. Same shape as
scripts/enrich_compare.py.

Usage:
    export OPENAI_API_KEY=...   # for the vector column; FTS-only-only works without it
    python -m scripts.eval_retrieval --db library.db

Run this AFTER scripts/embed_backfill.py — otherwise articles_vec is empty
and the vector/hybrid columns just repeat the FTS5 results.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from linklib import embeddings as embed_mod
from linklib.agent import _rrf_merge, _safe_fts_query
from linklib.db import Library, resolve_db_path

FLAGGED_RATINGS = ("inaccurate", "not_helpful")


def _titles(hits: list[dict]) -> list[str]:
    return [h.get("title") or h.get("url") or "?" for h in hits]


def _cited_library_titles(citations_json: str) -> list[str]:
    """What the ORIGINAL flagged answer actually cited from the library
    (type='library' — feed/web citations aren't retrieval candidates here)."""
    try:
        cites = json.loads(citations_json or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(cites, list):
        return []
    return [c.get("title") or c.get("url") or "?" for c in cites
            if isinstance(c, dict) and c.get("type") == "library"]


def _flagged_questions(lib: Library, limit: int) -> list[dict]:
    """Feedback rows rated inaccurate/not_helpful, deduped by question_id (a
    turn could theoretically be rated by more than one member)."""
    seen_qids: set = set()
    flagged: list[dict] = []
    for rating in FLAGGED_RATINGS:
        for row in lib.list_ask_feedback(rating=rating, limit=limit):
            if row["question_id"] in seen_qids:
                continue
            seen_qids.add(row["question_id"])
            flagged.append(row)
    return flagged


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None, help="Path to library.db (or set LINKLIB_DB)")
    ap.add_argument("--top", type=int, default=8, help="candidates to show per retrieval path")
    ap.add_argument("--limit", type=int, default=50, help="max flagged questions to replay")
    args = ap.parse_args()
    args.db = resolve_db_path(args.db)

    lib = Library(args.db)

    vec_available = lib.vector_search_available() and bool(os.environ.get("OPENAI_API_KEY"))
    if not vec_available:
        print("NOTE: OPENAI_API_KEY not set or sqlite-vec unavailable — only the "
              "FTS5-only column will show (nothing to compare yet).\n")

    flagged = _flagged_questions(lib, args.limit)
    if not flagged:
        print("No flagged (inaccurate / not_helpful) FP&A Buddy answers found — "
              "nothing to replay. (Members rate answers from the /ask page.)")
        lib.close()
        return 0

    print(f"Replaying {len(flagged)} flagged question(s) through FTS5-only vs. "
          f"hybrid retrieval (top {args.top} each). No Claude calls made.\n")

    for row in flagged:
        question = row["question"]
        print("=" * 78)
        print(f"Q: {question}")
        note = f"  — {row['comment']}" if row["comment"] else ""
        print(f"   rating: {row['rating']}{note}")
        cited = _cited_library_titles(row.get("citations_json") or "[]")
        print(f"   originally cited: {', '.join(cited) if cited else '(none / pre-citation-snapshot turn)'}")
        print("-" * 78)

        fts_hits = lib.search(_safe_fts_query(question), limit=args.top)
        print(f"OLD  (FTS5-only):    {_titles(fts_hits) or '(none)'}")

        if vec_available:
            embedded = embed_mod.embed_text(question)
            vec_hits = lib.vector_search(embedded.vectors[0], limit=args.top) if embedded else []
            hybrid = _rrf_merge([fts_hits, vec_hits], limit=args.top) if embedded else fts_hits
            print(f"     VECTOR-only:    {_titles(vec_hits) or '(none)'}")
            print(f"NEW  (hybrid RRF):   {_titles(hybrid) or '(none)'}")
        print()

    print("=" * 78)
    print("Judge: for each flagged question, does the hybrid column surface better")
    print("sources than FTS5-only did — especially ones missing from 'originally")
    print("cited' that might have produced a better answer?")
    lib.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
