"""SQLite + FTS5 storage layer for the link library.

This is the durable spine. Everything else (Feedly import, going-forward
capture, web UI, Claude access) reads and writes through here.

The same schema works whether the DB is a local file or a hosted
libSQL/Turso/D1 database later — only the connection changes.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, Optional

DEFAULT_DB_PATH = "library.db"

# Columns that get indexed for full-text search. tags_text is a flattened
# copy of the tags list so board names are searchable too.
_FTS_COLUMNS = ("title", "author", "source", "summary", "content", "notes", "tags_text")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    url         TEXT NOT NULL UNIQUE,
    title       TEXT NOT NULL DEFAULT '',
    author      TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL DEFAULT '',      -- publication / feed name
    summary     TEXT NOT NULL DEFAULT '',      -- Feedly snippet or Claude-generated
    content     TEXT NOT NULL DEFAULT '',      -- full text, if fetched
    notes       TEXT NOT NULL DEFAULT '',      -- your own highlights/annotations
    tags_json   TEXT NOT NULL DEFAULT '[]',    -- structured list of board/tag names
    tags_text   TEXT NOT NULL DEFAULT '',      -- flattened copy for FTS
    published_at TEXT,                          -- ISO 8601, when the article was published
    saved_at    TEXT,                          -- ISO 8601, when you saved it in Feedly
    feedly_id   TEXT,                           -- original Feedly entry id, for dedupe
    enriched    INTEGER NOT NULL DEFAULT 0,    -- 1 once Claude summary/tags applied
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_articles_saved_at ON articles(saved_at);
CREATE INDEX IF NOT EXISTS idx_articles_enriched ON articles(enriched);

-- External-content FTS5 index mirroring the searchable columns above.
CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
    title, author, source, summary, content, notes, tags_text,
    content='articles',
    content_rowid='id',
    tokenize='porter unicode61'
);

-- Triggers keep the FTS index in sync with the base table.
CREATE TRIGGER IF NOT EXISTS articles_ai AFTER INSERT ON articles BEGIN
    INSERT INTO articles_fts(rowid, title, author, source, summary, content, notes, tags_text)
    VALUES (new.id, new.title, new.author, new.source, new.summary, new.content, new.notes, new.tags_text);
END;
CREATE TRIGGER IF NOT EXISTS articles_ad AFTER DELETE ON articles BEGIN
    INSERT INTO articles_fts(articles_fts, rowid, title, author, source, summary, content, notes, tags_text)
    VALUES ('delete', old.id, old.title, old.author, old.source, old.summary, old.content, old.notes, old.tags_text);
END;
CREATE TRIGGER IF NOT EXISTS articles_au AFTER UPDATE ON articles BEGIN
    INSERT INTO articles_fts(articles_fts, rowid, title, author, source, summary, content, notes, tags_text)
    VALUES ('delete', old.id, old.title, old.author, old.source, old.summary, old.content, old.notes, old.tags_text);
    INSERT INTO articles_fts(rowid, title, author, source, summary, content, notes, tags_text)
    VALUES (new.id, new.title, new.author, new.source, new.summary, new.content, new.notes, new.tags_text);
END;
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Article:
    """One saved item. URL is the natural key used for dedupe."""
    url: str
    title: str = ""
    author: str = ""
    source: str = ""
    summary: str = ""
    content: str = ""
    notes: str = ""
    tags: list[str] = field(default_factory=list)
    published_at: Optional[str] = None
    saved_at: Optional[str] = None
    feedly_id: Optional[str] = None
    enriched: bool = False

    def tags_text(self) -> str:
        return " ".join(self.tags)


class Library:
    def __init__(self, path: str = DEFAULT_DB_PATH):
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA foreign_keys=ON;")
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    # -- writes -------------------------------------------------------------

    def upsert(self, art: Article) -> int:
        """Insert a new article or merge into an existing one (by URL).

        On conflict we union the tag lists and fill any empty fields, so
        re-running the import — or an article living on several boards — is
        safe and idempotent.
        """
        cur = self.conn.execute("SELECT * FROM articles WHERE url = ?", (art.url,))
        existing = cur.fetchone()
        now = _now()

        if existing is None:
            self.conn.execute(
                """INSERT INTO articles
                   (url, title, author, source, summary, content, notes,
                    tags_json, tags_text, published_at, saved_at, feedly_id,
                    enriched, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (art.url, art.title, art.author, art.source, art.summary,
                 art.content, art.notes, json.dumps(art.tags), art.tags_text(),
                 art.published_at, art.saved_at, art.feedly_id,
                 int(art.enriched), now, now),
            )
            self.conn.commit()
            return self.conn.execute("SELECT id FROM articles WHERE url = ?", (art.url,)).fetchone()[0]

        # Merge: union tags, keep first non-empty scalar value.
        merged_tags = sorted(set(json.loads(existing["tags_json"]) or []) | set(art.tags))
        merged = {
            "title": existing["title"] or art.title,
            "author": existing["author"] or art.author,
            "source": existing["source"] or art.source,
            "summary": existing["summary"] or art.summary,
            "content": existing["content"] or art.content,
            "notes": "\n".join(p for p in [existing["notes"], art.notes] if p).strip(),
            "tags_json": json.dumps(merged_tags),
            "tags_text": " ".join(merged_tags),
            "published_at": existing["published_at"] or art.published_at,
            "saved_at": existing["saved_at"] or art.saved_at,
            "feedly_id": existing["feedly_id"] or art.feedly_id,
            "enriched": existing["enriched"] or int(art.enriched),
        }
        self.conn.execute(
            """UPDATE articles SET title=?, author=?, source=?, summary=?,
               content=?, notes=?, tags_json=?, tags_text=?, published_at=?,
               saved_at=?, feedly_id=?, enriched=?, updated_at=? WHERE id=?""",
            (merged["title"], merged["author"], merged["source"], merged["summary"],
             merged["content"], merged["notes"], merged["tags_json"], merged["tags_text"],
             merged["published_at"], merged["saved_at"], merged["feedly_id"],
             merged["enriched"], now, existing["id"]),
        )
        self.conn.commit()
        return existing["id"]

    def apply_enrichment(self, article_id: int, summary: str, tags: list[str]) -> None:
        row = self.conn.execute("SELECT summary, tags_json FROM articles WHERE id=?", (article_id,)).fetchone()
        if row is None:
            return
        merged_tags = sorted(set(json.loads(row["tags_json"]) or []) | set(tags))
        self.conn.execute(
            "UPDATE articles SET summary=?, tags_json=?, tags_text=?, enriched=1, updated_at=? WHERE id=?",
            (summary or row["summary"], json.dumps(merged_tags), " ".join(merged_tags), _now(), article_id),
        )
        self.conn.commit()

    # -- reads --------------------------------------------------------------

    def update_content(self, article_id: int, content: str) -> None:
        """Store fetched full text (FTS reindexes via trigger)."""
        if not content:
            return
        self.conn.execute(
            "UPDATE articles SET content=?, updated_at=? WHERE id=?",
            (content, _now(), article_id),
        )
        self.conn.commit()

    def known_tags(self, limit: int = 80) -> list[str]:
        """The existing tag vocabulary (your board taxonomy), most-used first.

        Passed to the enricher so new articles get tagged in your own language.
        """
        return [t for t, _ in self.all_tags()[:limit]]

    def search(self, query: str, limit: int = 50) -> list[dict]:
        """Full-text search ranked by relevance (bm25)."""
        if not query.strip():
            rows = self.conn.execute(
                "SELECT * FROM articles ORDER BY saved_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [self._row_to_dict(r) for r in rows]
        rows = self.conn.execute(
            """SELECT a.*, bm25(articles_fts) AS rank
               FROM articles_fts
               JOIN articles a ON a.id = articles_fts.rowid
               WHERE articles_fts MATCH ?
               ORDER BY rank
               LIMIT ?""",
            (query, limit),
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def unenriched(self, limit: int = 1000) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM articles WHERE enriched=0 ORDER BY id LIMIT ?", (limit,)
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]

    def all_tags(self) -> list[tuple[str, int]]:
        counts: dict[str, int] = {}
        for (tj,) in self.conn.execute("SELECT tags_json FROM articles"):
            for t in json.loads(tj) or []:
                counts[t] = counts.get(t, 0) + 1
        return sorted(counts.items(), key=lambda kv: -kv[1])

    @staticmethod
    def _row_to_dict(r: sqlite3.Row) -> dict:
        d = dict(r)
        d["tags"] = json.loads(d.pop("tags_json", "[]") or "[]")
        d.pop("tags_text", None)
        return d

    def close(self) -> None:
        self.conn.close()


@contextmanager
def open_library(path: str = DEFAULT_DB_PATH) -> Iterator[Library]:
    lib = Library(path)
    try:
        yield lib
    finally:
        lib.close()
