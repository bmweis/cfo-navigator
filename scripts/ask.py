#!/usr/bin/env python3
"""Ask your library an FP&A / finance question, grounded in your saved articles.

    python -m scripts.ask "how should I think about CAC payback for usage-based pricing?"
    python -m scripts.ask --db library.db --sources 10 "what's a healthy net dollar retention?"
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from linklib.db import Library
from linklib.agent import answer_question


def main() -> int:
    ap = argparse.ArgumentParser(description="Ask your library a finance question.")
    ap.add_argument("question")
    ap.add_argument("--db", default="library.db")
    ap.add_argument("--sources", type=int, default=8)
    args = ap.parse_args()

    lib = Library(args.db)
    ans = answer_question(lib, args.question, max_sources=args.sources)
    print("\n" + ans.text + "\n")
    if ans.sources:
        print("Sources:")
        for i, s in enumerate(ans.sources, 1):
            print(f"  [{i}] {s['title']}")
            print(f"      {s['url']}")
    lib.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
