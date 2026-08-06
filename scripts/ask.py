#!/usr/bin/env python3
"""Ask your library an FP&A / finance question, grounded in your saved articles.

    python -m scripts.ask "how should I think about CAC payback for usage-based pricing?"
    python -m scripts.ask --db library.db --effort deep "what's a healthy net dollar retention?"
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from linklib.db import Library, resolve_db_path
from linklib.agent import answer_question


def main() -> int:
    ap = argparse.ArgumentParser(description="Ask your library a finance question.")
    ap.add_argument("question")
    ap.add_argument("--db", default=None, help="Path to library.db (or set LINKLIB_DB)")
    # Source counts are tier-driven now (EFFORT_SETTINGS), not a free-standing
    # number — the old --sources flag mapped to a parameter answer_question
    # lost in the Quick/Standard/Deep redesign, which crashed every CLI run.
    ap.add_argument("--effort", choices=["quick", "standard", "deep"], default="standard")
    args = ap.parse_args()
    args.db = resolve_db_path(args.db)

    lib = Library(args.db)
    ans = answer_question(lib, args.question, effort=args.effort)
    print("\n" + ans.text + "\n")
    if ans.citations:
        # Cited sources, numbered to match the [n] markers in the answer.
        print("Sources:")
        for c in ans.citations:
            print(f"  [{c['n']}] {c['title']}")
            print(f"      {c['url']}")
    elif ans.sources:
        # No citation metadata (e.g. the model cited nothing) — fall back to
        # listing what was retrieved, as before.
        print("Retrieved (uncited):")
        for i, s in enumerate(ans.sources, 1):
            print(f"  [{i}] {s['title']}")
            print(f"      {s['url']}")
    lib.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
