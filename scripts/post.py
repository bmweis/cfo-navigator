#!/usr/bin/env python3
"""Draft a LinkedIn post in Brian's voice from a saved article or a topic.

    python -m scripts.post --id 42
    python -m scripts.post --url "https://kellblog.com/..." --mode self_promo
    python -m scripts.post --topic "why CAC payback misleads in usage-based pricing"
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from linklib.db import Library
from linklib.social import draft_post


def main() -> int:
    ap = argparse.ArgumentParser(description="Draft a LinkedIn post in your voice.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--id", type=int)
    g.add_argument("--url")
    g.add_argument("--topic")
    ap.add_argument("--db", default="library.db")
    ap.add_argument("--mode", default="original", choices=["original", "self_promo", "amplification"])
    args = ap.parse_args()

    lib = Library(args.db)
    d = draft_post(lib, article_id=args.id, url=args.url, topic=args.topic, mode=args.mode)
    print("\n" + d.post + "\n")
    if d.based_on:
        print("— based on:")
        for r in d.based_on:
            print(f"   {r['title']}  ({r['url']})")
    lib.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
