#!/usr/bin/env python3
"""Read-only probe: what would FP&A Buddy's benchmark pre-step put in front of
the model for one question? (issue #738, Phase B)

For a question it prints, with no model call and no write:
  - the metrics the rules detect and the signals (bands, change over time,
    definition, mixed question) that decide whether a split would run;
  - the documents the pre-step would send first, in order: workbook
    definition cards, then one "Figures:" document per report, each with its
    figure count and `match` mix, so you can see the right rows come first;
  - the first library hits the ordinary retrieval would add after them.

A question that would trigger the split prints "would split" and then shows the
rules-only lookup (the split itself is a model call and is not made here).

The database is opened with SQLite `mode=ro`; the `Library` class is not used
(its constructor creates tables). Library hits are full-text only unless
OPENAI_API_KEY and sqlite-vec are available, as in scripts/probe_digest_rank.py.

Usage (production, over `railway ssh`):
    python -m scripts.probe_prestep --db /data/library.db --question "What is median NRR at $25M ARR?"
    python -m scripts.probe_prestep --db /data/library.db --question "..." --depth deep
"""
from __future__ import annotations

import argparse
import sys

from linklib import benchmark_prestep as bp
from linklib.agent import EFFORT_SETTINGS, _rrf_merge, _safe_fts_query
from linklib.db import Library, resolve_db_path
from scripts.probe_digest_rank import ReadOnlyLib, open_readonly


class _Lib(ReadOnlyLib):
    """ReadOnlyLib plus the few Library reads the pre-step makes."""
    get_article = Library.get_article
    get_article_by_url = Library.get_article_by_url
    list_benchmark_reports = Library.list_benchmark_reports


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", default=None, help="path to library.db (or set LINKLIB_DB)")
    ap.add_argument("--question", required=True)
    ap.add_argument("--depth", choices=sorted(EFFORT_SETTINGS), default="standard")
    args = ap.parse_args(argv)

    conn, vec = open_readonly(resolve_db_path(args.db))
    lib = _Lib(conn, vec)
    s = EFFORT_SETTINGS[args.depth]
    # remaining_usd=0 keeps the split (a model call) from running; the rules-only lookup does.
    ps = bp.plan_turn(lib, args.question, tier=args.depth, settings=s, model=s["model"], remaining_usd=0.0)
    sig = bp.detect(lib, args.question)
    print(f"Question: {args.question}")
    print(f"Detected metrics: {', '.join(sig['metrics']) or 'none'}")
    print(f"Signals: bands={sig['bands']} over_time={sig['over_time']} definition={sig['definition']} "
          f"mixed={sig['mixed']} rest_words={sig['rest_words']}")
    print("Would split: " + (("yes, because " + " and ".join(sig["split_reasons"])) if sig["needs_split"] else "no"))
    if not ps.active:
        print("\nThe pre-step does not run for this question. Retrieval is unchanged.")
    else:
        docs = bp.dedupe_docs([d for sub in ps.subs for d in sub.docs])
        print(f"\nDocuments sent first ({len(docs)}), in order:")
        for i, d in enumerate(docs, 1):
            lines = d["body"].count("\n")
            print(f"  {i}. {d['title']}  [{len(d['body']):,} chars, {lines} lines, link: {d['sent']['url'] or 'none'}]")
        for sub in ps.subs:
            print(f"  lookup: {', '.join(sub.metrics) or 'no metric'}: {sub.rows} figure rows, match mix {sub.match_mix}")
        for n in dict.fromkeys(n for sub in ps.subs for n in sub.notes):
            print(f"  note: {n}")
    fts = lib.search(_safe_fts_query(args.question), limit=s["max_library"] * 2)
    merged = _rrf_merge([fts], limit=s["max_library"])
    print(f"\nFirst library hits at {args.depth} (full-text only):")
    for n, h in enumerate(merged, 1):
        print(f"  {n}. #{h['id']} {h.get('title', '')[:70]}  ({(h.get('published_at') or '')[:10]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
