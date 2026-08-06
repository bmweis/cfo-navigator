#!/usr/bin/env python3
"""Compare enrichment quality across models on a single article.

Runs the exact production enrichment prompt (linklib/enrich.py) through two or
more models and prints the summaries + tags side by side — so you can judge
whether the quality delta is worth the cost difference before committing to a
full re-enrichment of the library.

This makes real API calls (a few cents). Set ANTHROPIC_API_KEY first.

Pick the article one of three ways:
    python -m scripts.enrich_compare --url https://www.mostlymetrics.com/p/...
    python -m scripts.enrich_compare --db library.db --id 42
    python -m scripts.enrich_compare --db library.db          # auto-pick a sample

Default models: Opus vs Sonnet. Override with --models a,b,c.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from linklib import enrich as enrich_mod
from linklib.db import Library, resolve_db_path

DEFAULT_MODELS = "claude-opus-4-8,claude-sonnet-4-6"


def _pick_sample(lib: Library) -> dict | None:
    """Choose a representative article: one with enough stored body text that
    the models have something real to work with."""
    best = None
    for row in lib.all_articles():
        if len(row.get("content") or "") >= 1200:
            return row          # first substantial one is fine
        if best is None and (row.get("content") or row.get("summary")):
            best = row
    return best


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="", help="article URL to fetch and enrich")
    ap.add_argument("--db", default=os.environ.get("LINKLIB_DB", ""),
                    help="library DB (to pull a saved article / vocab) — optional, "
                         "falls back to LINKLIB_DB if set")
    ap.add_argument("--id", type=int, default=0, help="article id within --db")
    ap.add_argument("--query", default="",
                    help="pick the top library search hit for this query "
                         "(e.g. an Ask question) — needs --db")
    ap.add_argument("--models", default=DEFAULT_MODELS,
                    help="comma-separated model IDs to compare")
    args = ap.parse_args()
    if args.db:
        args.db = resolve_db_path(args.db)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: set ANTHROPIC_API_KEY first (this makes real API calls).", file=sys.stderr)
        return 2

    lib = Library(args.db) if args.db else None
    vocab = lib.known_tags() if lib else []

    # Resolve the article: explicit URL, a saved row by id, or an auto-pick.
    title = text = url = ""
    if args.url:
        from linklib.extract import fetch_page
        page = fetch_page(args.url)
        title, text, url = page.title, page.content, args.url
    elif lib and args.query:
        hits = lib.search(args.query, limit=1)
        if not hits:
            print(f"ERROR: no library hits for {args.query!r}", file=sys.stderr)
            return 2
        row = hits[0]
        title, text, url = row["title"], row["content"] or row["summary"], row["url"]
        print(f"(top hit for {args.query!r})")
    elif lib and args.id:
        row = next((r for r in lib.all_articles() if r["id"] == args.id), None)
        if not row:
            print(f"ERROR: no article with id {args.id} in {args.db}", file=sys.stderr)
            return 2
        title, text, url = row["title"], row["content"] or row["summary"], row["url"]
    elif lib:
        row = _pick_sample(lib)
        if not row:
            print("ERROR: no articles with usable text in that DB.", file=sys.stderr)
            return 2
        title, text, url = row["title"], row["content"] or row["summary"], row["url"]
    else:
        print("ERROR: pass --url, or --db (optionally with --id).", file=sys.stderr)
        return 2

    if not (text or title):
        print("ERROR: couldn't get any article text to enrich.", file=sys.stderr)
        return 2

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    print("=" * 78)
    print(f"ARTICLE: {title[:72]}")
    print(f"URL:     {url}")
    print(f"Text:    {len(text or '')} chars  |  vocab: {len(vocab)} known tags")
    print("=" * 78)

    for model in models:
        t0 = time.time()
        result = enrich_mod.enrich(title or url, text or title, known_tags=vocab, model=model)
        dt = time.time() - t0
        print(f"\n### {model}   ({dt:.1f}s)")
        print("-" * 78)
        if result is None:
            print("(no result — model error or unavailable)")
            continue
        print("SUMMARY:")
        print(f"  {result.summary}")
        print(f"TAGS: {', '.join(result.tags)}")

    print("\n" + "=" * 78)
    print("Judge: which summary is more specific and searchable? Which tags hew")
    print("closest to your vocabulary? Then decide if the cost delta is worth it.")
    if lib:
        lib.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
