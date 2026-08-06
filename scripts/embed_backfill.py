#!/usr/bin/env python3
"""One-off backfill: embed every library article that doesn't have a
current vector yet (#93).

Usage:
    export OPENAI_API_KEY=...
    python -m scripts.embed_backfill --db library.db

Deliberately a standalone script, not logic folded into the save path —
embed-on-save (linklib.pipeline.embed_article) handles new articles going
forward one at a time; this handles the existing corpus in bulk, once.

Batches BATCH_SIZE articles (default 100) into a single OpenAI embeddings
API call — not one request per article — processed sequentially (not
concurrently, to stay clear of rate limits without per-account tuning).
Each batch's vectors + cost-ledger rows are committed as soon as that batch
succeeds, so an interrupted run can simply be re-invoked: articles whose
current document_text hash already matches what's stored are skipped
(unless --force), the same staleness check embed_article uses.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from linklib.db import Library, resolve_db_path
from linklib import embeddings as embed_mod

BATCH_SIZE = 100
MAX_RETRIES = 3
RETRY_BASE_DELAY = 2.0  # seconds; doubles each retry: 2s, 4s, 8s


def _embed_batch_with_retry(texts: list[str], model: str) -> "embed_mod.EmbedResult | None":
    """Call embed_texts with bounded exponential backoff.

    embed_texts fails fast by design (see its module docstring) and returns
    None uniformly on any error — a missing key, a 429, a 5xx, or something
    permanent — so this can't distinguish "worth retrying" from "never
    going to work" by return value alone. It retries regardless and gives up
    after MAX_RETRIES: for a one-off bulk job, retrying a handful of times
    and moving on to the next batch is the right tradeoff over hanging
    indefinitely or aborting the whole run on one bad batch.
    """
    delay = RETRY_BASE_DELAY
    for attempt in range(1, MAX_RETRIES + 1):
        result = embed_mod.embed_texts(texts, model=model)
        if result is not None:
            return result
        if attempt < MAX_RETRIES:
            print(f"    batch failed (attempt {attempt}/{MAX_RETRIES}), retrying in {delay:.0f}s...")
            time.sleep(delay)
            delay *= 2
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None, help="Path to library.db (or set LINKLIB_DB)")
    ap.add_argument("--limit", type=int, default=100000, help="max articles to consider")
    ap.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    ap.add_argument("--force", action="store_true",
                    help="re-embed every row, not just ones whose content changed")
    ap.add_argument("--model", default=None,
                    help="embedding model override (default: LINKLIB_EMBED_MODEL / "
                         "text-embedding-3-small)")
    args = ap.parse_args()
    args.db = resolve_db_path(args.db)

    if not os.environ.get("OPENAI_API_KEY"):
        print("ERROR: set OPENAI_API_KEY first.", file=sys.stderr)
        return 2

    model = args.model or embed_mod.DEFAULT_MODEL
    lib = Library(args.db)

    if not lib.vector_search_available():
        print("ERROR: sqlite-vec failed to load on this connection, so there's no "
              "vector index to backfill into. Check that the sqlite-vec package is "
              "installed (pip install -r requirements.txt).", file=sys.stderr)
        lib.close()
        return 2

    articles = lib.all_articles(limit=args.limit)
    existing_hashes = {} if args.force else lib.embedding_hashes()

    # Work out what actually needs embedding before spending anything — an
    # accurate up-front count, and no wasted work re-hashing unchanged rows.
    pending: list[tuple[int, str, str]] = []  # (article_id, text, content_hash)
    skipped_empty = 0
    for row in articles:
        text = embed_mod.document_text(row)
        if not text:
            skipped_empty += 1
            continue
        content_hash = embed_mod.content_hash(text)
        if existing_hashes.get(row["id"]) == content_hash:
            continue
        pending.append((row["id"], text, content_hash))

    total = len(pending)
    already_current = len(articles) - total - skipped_empty
    print(f"{len(articles)} articles considered: {total} need embedding, "
          f"{already_current} already current, {skipped_empty} have nothing to embed.")
    if total == 0:
        lib.close()
        return 0

    done = 0
    running_tokens = 0
    running_cost = 0.0
    for start in range(0, total, args.batch_size):
        batch = pending[start:start + args.batch_size]
        texts = [t for _id, t, _h in batch]
        result = _embed_batch_with_retry(texts, model)
        if result is None or len(result.vectors) != len(batch):
            print(f"  [{done}/{total}] batch of {len(batch)} failed after retries — "
                  f"skipping (re-run the script later to retry these rows)")
            continue

        # The API returns one usage total for the whole batch call, not a
        # per-input breakdown — divide evenly across the batch for the
        # per-article ledger row. The RUN total (printed below and summed
        # across batches) stays exact either way; only the per-article split
        # is an approximation, which is fine for an aggregate overhead ledger.
        per_item_tokens = result.input_tokens // len(batch)
        per_item_cost = result.cost_usd / len(batch)
        for (article_id, _text, content_hash), vector in zip(batch, result.vectors):
            lib.upsert_article_embedding(article_id, vector, content_hash, model,
                                         input_tokens=per_item_tokens, cost_usd=per_item_cost)

        done += len(batch)
        running_tokens += result.input_tokens
        running_cost += result.cost_usd
        print(f"  [{done}/{total}] embedded — running total: {running_tokens:,} tokens, "
              f"${running_cost:.4f}")

    print(f"\nEmbedded {done}/{total} articles. Total: {running_tokens:,} tokens, "
          f"${running_cost:.4f}.")
    lib.close()
    return 0 if done == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
