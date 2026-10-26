#!/usr/bin/env python3
"""Read-only probe: do the loaded benchmark digests rank where FP&A Buddy will
actually look?

For one question, prints the top 15 library hits at each depth (quick, standard,
deep) with the full-text rank, the vector rank, whether FP&A Buddy would SEND the
row to the model, how many grounding characters it would get, the published date,
the title and the source. Rows you are watching are marked `>>>`. Then it prints how
much of the depth's global character cap the library sources use, and how much is
left for feed and web results (a warning appears when that is near zero).

It uses the real retrieval: `Library.search` and `Library.vector_search` run
unchanged, merged with `agent._rrf_merge` and budgeted with
`agent._build_source_documents`, so the ranking and the character budget are the
ones a question really gets. Nothing is copied.

Read-only by construction:
  - The database is opened with SQLite `mode=ro`. The `Library` class is NOT
    used, because its constructor creates tables.
  - The only network call is ONE OpenAI embedding of the question. With no
    OPENAI_API_KEY (or no sqlite-vec) it says so and prints the full-text half only.
  - No Claude call, no Exa call, nothing is written.

Usage (production, over `railway ssh`):
    python -m scripts.probe_digest_rank --db /data/library.db --question "What was median net revenue retention in the 2025 SaaS benchmarks?" --watch 4801,4802
    python -m scripts.probe_digest_rank --db /data/library.db --question "..." --watch-text "High Alpha"

`--watch` takes article ids (comma-separated, or repeat the flag).
`--watch-text` marks every article whose title, source or summary contains the
text (case-insensitive).
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

from linklib.agent import (EFFORT_SETTINGS, _build_source_documents, _rrf_merge,
                           _safe_fts_query)
from linklib.db import Library

DEPTHS = ("quick", "standard", "deep")
SHOW = 15                    # rows printed per depth
DEEP_POOL = 2000             # candidate depth used to rank a watched row below the top 15
LOW_LEFT_CHARS = 500         # warn when fewer characters than this are left for feed and web


class ReadOnlyLib:
    """Just enough of `Library` for `search` and `vector_search`, borrowing the
    real methods so the ranking is the real one without the constructor's writes."""
    _row_to_dict = staticmethod(Library._row_to_dict)
    search = Library.search
    vector_search = Library.vector_search

    def __init__(self, conn: sqlite3.Connection, vec_available: bool):
        self.conn = conn
        self._vec_available = vec_available

    def vector_search_available(self) -> bool:
        return self._vec_available


def open_readonly(path: str) -> tuple[sqlite3.Connection, bool]:
    """(connection, vector_search_available). Never creates or alters anything."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    vec = False
    try:
        import sqlite_vec
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        vec = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='articles_vec'").fetchone() is not None
    except Exception:
        vec = False
    return conn, vec


def watched_ids(conn: sqlite3.Connection, ids: list[int], text: str) -> dict[int, str]:
    """{article_id: why} for --watch ids and --watch-text matches."""
    out: dict[int, str] = {}
    for i in ids:
        out[i] = "id"
    if text.strip():
        like = f"%{text.strip().lower()}%"
        for r in conn.execute(
                "SELECT id FROM articles WHERE lower(title) LIKE ? OR lower(source) LIKE ? "
                "OR lower(summary) LIKE ?", (like, like, like)):
            out.setdefault(r["id"], "text")
    return out


def probe_depth(lib: ReadOnlyLib, question: str, vector: list[float] | None, depth: str) -> dict:
    """The real retrieval for one depth, with each row's per-list rank."""
    s = EFFORT_SETTINGS[depth]
    max_lib = s["max_library"]
    fts = lib.search(_safe_fts_query(question), limit=max_lib * 2)
    vec = lib.vector_search(vector, limit=max_lib * 2) if (vector and lib.vector_search_available()) else []
    merged = _rrf_merge([fts, vec], limit=SHOW)
    sent = merged[:max_lib]
    _blocks, sent_docs = _build_source_documents(
        sent, [], [], source_chars=s["source_chars"], global_chars=s["global_chars"])
    # _build_source_documents drops a source whose budget ran out, so sent_docs
    # says what was really grounded; chars come from the document text itself.
    chars = {}
    for blk, doc in zip(_blocks, sent_docs):
        chars[doc.get("article_id")] = len(blk["source"]["data"])
    fts_rank = {h["id"]: n for n, h in enumerate(fts, 1)}
    vec_rank = {h["id"]: n for n, h in enumerate(vec, 1)}
    rows = []
    for n, h in enumerate(merged, 1):
        in_sent = h["id"] in {x["id"] for x in sent}
        grounded = h["id"] in chars
        rows.append({
            "rank": n, "id": h["id"], "fts": fts_rank.get(h["id"]), "vec": vec_rank.get(h["id"]),
            "status": ("SENT" if grounded else "budget") if in_sent else "not sent",
            "chars": chars.get(h["id"], 0),
            "published": (h.get("published_at") or "")[:10],
            "title": h.get("title") or "", "source": h.get("source") or "",
        })
    used = sum(chars.values())
    return {"depth": depth, "settings": s, "rows": rows, "used": used,
            "left": s["global_chars"] - used, "vector_used": bool(vec)}


def deep_ranks(lib: ReadOnlyLib, question: str, vector: list[float] | None, ids: list[int]) -> dict[int, tuple]:
    """(full-text rank, vector rank) in a deep candidate pool, for watched rows
    that fell outside the top 15. None means not in the pool."""
    fts = {h["id"]: n for n, h in enumerate(lib.search(_safe_fts_query(question), limit=DEEP_POOL), 1)}
    vec = {}
    if vector and lib.vector_search_available():
        vec = {h["id"]: n for n, h in enumerate(lib.vector_search(vector, limit=DEEP_POOL), 1)}
    return {i: (fts.get(i), vec.get(i)) for i in ids}


def _cell(v, width: int) -> str:
    return str(v if v is not None else "-").rjust(width)


def render(results: list[dict], watch: dict[int, str], lib: ReadOnlyLib, question: str,
           vector: list[float] | None, titles: dict[int, str]) -> list[str]:
    out: list[str] = []
    for r in results:
        s = r["settings"]
        out.append("")
        out.append(f"== {r['depth']}: library {s['max_library']} sources, {s['source_chars']:,} chars each, "
                   f"{s['global_chars']:,} chars total across library, feed and web ==")
        out.append(f"{'':3} {'rank':>4} {'id':>6} {'fts':>4} {'vec':>4}  {'status':8} {'chars':>6}  "
                   f"{'published':10}  {'title':60}  source")
        for row in r["rows"]:
            mark = ">>>" if row["id"] in watch else "   "
            title = (row["title"][:57] + "...") if len(row["title"]) > 60 else row["title"]
            out.append(f"{mark} {_cell(row['rank'], 4)} {_cell(row['id'], 6)} {_cell(row['fts'], 4)} "
                       f"{_cell(row['vec'], 4)}  {row['status']:8} {_cell(row['chars'], 6)}  "
                       f"{row['published']:10}  {title:60}  {row['source']}")
        out.append(f"library characters used: {r['used']:,} of {s['global_chars']:,}; "
                   f"left for feed and web: {r['left']:,}")
        if r["left"] < LOW_LEFT_CHARS:
            out.append(f"WARNING: only {max(r['left'], 0):,} characters are left for feed and web sources at "
                       f"{r['depth']} depth; they will be dropped or cut.")
        shown = {row["id"] for row in r["rows"]}
        missing = [i for i in watch if i not in shown]
        if missing:
            deep = deep_ranks(lib, question, vector, missing)
            for i in missing:
                f, v = deep[i]
                name = titles.get(i)
                if name is None:
                    out.append(f">>> watched #{i}: no article with that id.")
                else:
                    out.append(f">>> watched #{i} ({name[:50]}): not in the top {SHOW}. "
                               f"Full-text rank {f if f else 'not in the top ' + str(DEEP_POOL)}, "
                               f"vector rank {v if v else 'not in the top ' + str(DEEP_POOL) if vector else 'n/a'}.")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", required=True, help="Absolute path to library.db")
    ap.add_argument("--question", required=True)
    ap.add_argument("--watch", action="append", default=[], help="Article ids to mark (comma-separated or repeated)")
    ap.add_argument("--watch-text", default="", help="Mark articles whose title, source or summary contains this text")
    args = ap.parse_args(argv)

    if not os.path.isabs(args.db):
        sys.exit(f"--db must be an absolute path (got {args.db!r}), e.g. /data/library.db")
    if not os.path.exists(args.db):
        sys.exit(f"Database file not found at {args.db}")
    try:
        ids = [int(x) for chunk in args.watch for x in chunk.split(",") if x.strip()]
    except ValueError:
        sys.exit("--watch takes integer article ids, comma-separated.")

    conn, vec_ok = open_readonly(args.db)
    try:
        lib = ReadOnlyLib(conn, vec_ok)
        watch = watched_ids(conn, ids, args.watch_text)
        titles = {r["id"]: r["title"] for r in conn.execute("SELECT id, title FROM articles")} if watch else {}
        print(f"Database (read-only): {args.db}")
        print(f"Question: {args.question}")
        if watch:
            print("Watching: " + ", ".join(f"#{i}" for i in sorted(watch)))

        vector = None
        if not vec_ok:
            print("Vector half skipped: sqlite-vec is unavailable or this database has no articles_vec table. "
                  "Full-text only.")
        else:
            from linklib.embeddings import embed_text
            emb = embed_text(args.question)
            if emb is None or not emb.vectors:
                print("Vector half skipped: no OPENAI_API_KEY, or the embedding call failed. Full-text only.")
            else:
                vector = emb.vectors[0]
                print(f"Question embedded once ({emb.input_tokens} tokens, ${emb.cost_usd:.6f}).")

        results = [probe_depth(lib, args.question, vector, d) for d in DEPTHS]
        for line in render(results, watch, lib, args.question, vector, titles):
            print(line)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
