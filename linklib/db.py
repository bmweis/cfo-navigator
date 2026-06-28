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
    enrich_model TEXT NOT NULL DEFAULT '',      -- model that produced the enrichment
    enrich_rules TEXT NOT NULL DEFAULT '',      -- ENRICH_RULES_VERSION used
    in_scope    INTEGER NOT NULL DEFAULT 1,     -- 0 = flagged off-audience for review
    scope_reason TEXT NOT NULL DEFAULT '',      -- why it was flagged in/out of scope
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

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL DEFAULT '',
    role          TEXT NOT NULL DEFAULT 'user',   -- 'user' or 'admin'
    active        INTEGER NOT NULL DEFAULT 1,
    name          TEXT NOT NULL DEFAULT '',
    email         TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL DEFAULT '',
    last_login_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS contacts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL DEFAULT '',
    email      TEXT NOT NULL DEFAULT '',
    message    TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tools (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL DEFAULT '',
    slug            TEXT NOT NULL UNIQUE DEFAULT '',
    description     TEXT NOT NULL DEFAULT '',
    url             TEXT NOT NULL DEFAULT '',
    categories_json TEXT NOT NULL DEFAULT '[]',
    approved        INTEGER NOT NULL DEFAULT 0,
    advisor         INTEGER NOT NULL DEFAULT 0,
    submitted_by    TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_tools_approved ON tools(approved);

CREATE TABLE IF NOT EXISTS read_later (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    url         TEXT NOT NULL UNIQUE,
    title       TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL DEFAULT '',
    summary     TEXT NOT NULL DEFAULT '',
    published_at TEXT,
    added_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tool_leads (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_id      INTEGER NOT NULL,
    tool_name    TEXT NOT NULL DEFAULT '',
    name         TEXT NOT NULL DEFAULT '',
    email        TEXT NOT NULL DEFAULT '',
    company      TEXT NOT NULL DEFAULT '',
    company_size TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tool_leads_tool_id ON tool_leads(tool_id);
CREATE INDEX IF NOT EXISTS idx_tool_leads_created  ON tool_leads(created_at);

-- Staging area for proposed library additions (the "Library Queue"). Candidates
-- — from the live feed or a one-time historical sweep — land here enriched but
-- unsaved, so they can be reviewed before they enter the library (and the Ask
-- corpus). URL is the natural key, matching `articles`. `content` (third-party
-- full text) is an internal enrichment/search input only; the resale-safe
-- surface is `summary` + tags. Promoting a row moves it into `articles`,
-- preserving any enrichment already paid for.
CREATE TABLE IF NOT EXISTS library_queue (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    url           TEXT NOT NULL UNIQUE,
    title         TEXT NOT NULL DEFAULT '',
    author        TEXT NOT NULL DEFAULT '',
    source        TEXT NOT NULL DEFAULT '',
    summary       TEXT NOT NULL DEFAULT '',      -- enriched summary (resale-safe asset)
    content       TEXT NOT NULL DEFAULT '',      -- full text, internal input only
    suggested_tags_json TEXT NOT NULL DEFAULT '[]',
    published_at  TEXT,
    origin        TEXT NOT NULL DEFAULT '',       -- 'feed' | 'backfill:<source>'
    status        TEXT NOT NULL DEFAULT 'pending',-- 'pending' | 'dismissed'
    enriched      INTEGER NOT NULL DEFAULT 0,     -- 1 once a Claude summary/tags applied
    enrich_model  TEXT NOT NULL DEFAULT '',       -- model that produced the enrichment
    enrich_rules  TEXT NOT NULL DEFAULT '',       -- ENRICH_RULES_VERSION used
    created_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_queue_status ON library_queue(status);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# Query params that identify a campaign/referrer, not the article — dropped so
# the same piece arriving via two share links collapses to one row.
_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "utm_name", "utm_reader", "ref", "ref_src", "ref_url", "source",
    "fbclid", "gclid", "mc_cid", "mc_eid", "_hsenc", "_hsmi", "igshid", "cmpid",
    "spm", "ncid", "_bhlid", "amp",
}


def normalize_url(url: str) -> str:
    """Canonicalize a URL for dedup so trivial variants of the same article map
    to one key: force https, drop a leading 'www.', strip the fragment and common
    tracking params, and remove a trailing slash. Fetching still works because
    requests follows the resulting redirect. Best-effort — returns the stripped
    input if it can't be parsed."""
    from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
    url = (url or "").strip()
    if not url:
        return ""
    try:
        parts = urlsplit(url)
    except Exception:
        return url
    if not parts.scheme or not parts.netloc:
        return url.rstrip("/") or url
    host = parts.netloc.lower()
    if host.endswith(":80"):
        host = host[:-3]
    elif host.endswith(":443"):
        host = host[:-4]
    if host.startswith("www."):
        host = host[4:]
    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if k.lower() not in _TRACKING_PARAMS]
    path = parts.path
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return urlunsplit(("https", host, path, urlencode(kept), ""))


def _slugify(name: str) -> str:
    import re
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    return slug[:80]


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
    enrich_model: str = ""
    enrich_rules: str = ""

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
        # Migrate: add columns that were added after initial schema
        for _col_sql in [
            "ALTER TABLE tools ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE tools ADD COLUMN advisor INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE tools ADD COLUMN vendor_email TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE tools ADD COLUMN promoted INTEGER NOT NULL DEFAULT 0",
            # Enrichment provenance — added after the queue shipped, so existing
            # articles/library_queue tables need these backfilled.
            "ALTER TABLE articles ADD COLUMN enrich_model TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE articles ADD COLUMN enrich_rules TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE library_queue ADD COLUMN enrich_model TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE library_queue ADD COLUMN enrich_rules TEXT NOT NULL DEFAULT ''",
            # Audience-scope review — flag off-audience rows (e.g. how-to-get-into-VC).
            "ALTER TABLE articles ADD COLUMN in_scope INTEGER NOT NULL DEFAULT 1",
            "ALTER TABLE articles ADD COLUMN scope_reason TEXT NOT NULL DEFAULT ''",
        ]:
            try:
                self.conn.execute(_col_sql)
                self.conn.commit()
            except sqlite3.OperationalError:
                pass

    # -- writes -------------------------------------------------------------

    def upsert(self, art: Article) -> int:
        """Insert a new article or merge into an existing one (by URL).

        On conflict we union the tag lists and fill any empty fields, so
        re-running the import — or an article living on several boards — is
        safe and idempotent. The URL is canonicalized first so trivial variants
        (http/https, www, trailing slash, tracking params) merge into one row.
        """
        art.url = normalize_url(art.url)
        cur = self.conn.execute("SELECT * FROM articles WHERE url = ?", (art.url,))
        existing = cur.fetchone()
        now = _now()

        if existing is None:
            self.conn.execute(
                """INSERT INTO articles
                   (url, title, author, source, summary, content, notes,
                    tags_json, tags_text, published_at, saved_at, feedly_id,
                    enriched, enrich_model, enrich_rules, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (art.url, art.title, art.author, art.source, art.summary,
                 art.content, art.notes, json.dumps(art.tags), art.tags_text(),
                 art.published_at, art.saved_at, art.feedly_id,
                 int(art.enriched), art.enrich_model, art.enrich_rules, now, now),
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
            "enrich_model": existing["enrich_model"] or art.enrich_model,
            "enrich_rules": existing["enrich_rules"] or art.enrich_rules,
        }
        self.conn.execute(
            """UPDATE articles SET title=?, author=?, source=?, summary=?,
               content=?, notes=?, tags_json=?, tags_text=?, published_at=?,
               saved_at=?, feedly_id=?, enriched=?, enrich_model=?, enrich_rules=?,
               updated_at=? WHERE id=?""",
            (merged["title"], merged["author"], merged["source"], merged["summary"],
             merged["content"], merged["notes"], merged["tags_json"], merged["tags_text"],
             merged["published_at"], merged["saved_at"], merged["feedly_id"],
             merged["enriched"], merged["enrich_model"], merged["enrich_rules"],
             now, existing["id"]),
        )
        self.conn.commit()
        return existing["id"]

    def update_tags(self, article_id: int, tags: list[str]) -> None:
        """Replace the tag list on an article (hard-replace, not union)."""
        clean = sorted(set(t.strip() for t in tags if t.strip()))
        self.conn.execute(
            "UPDATE articles SET tags_json=?, tags_text=?, updated_at=? WHERE id=?",
            (json.dumps(clean), " ".join(clean), _now(), article_id),
        )
        self.conn.commit()

    def delete_article(self, article_id: int) -> None:
        """Permanently remove an article. FTS is updated by the articles_ad trigger."""
        self.conn.execute("DELETE FROM articles WHERE id=?", (article_id,))
        self.conn.commit()

    def rename_tag(self, old: str, new: str) -> int:
        """Rename a tag across the whole library. If `new` already exists on an
        article, the two merge (deduped). Returns the number of articles changed.
        Updating tags_text fires the FTS trigger, so search stays in sync."""
        old, new = old.strip(), new.strip()
        if not old or not new or old == new:
            return 0
        changed = 0
        for row in self.conn.execute(
            "SELECT id, tags_json FROM articles WHERE tags_json LIKE ?", (f'%"{old}"%',)
        ).fetchall():
            tags = json.loads(row["tags_json"]) or []
            if old not in tags:
                continue
            merged = sorted(set(new if t == old else t for t in tags))
            self.conn.execute(
                "UPDATE articles SET tags_json=?, tags_text=?, updated_at=? WHERE id=?",
                (json.dumps(merged), " ".join(merged), _now(), row["id"]),
            )
            changed += 1
        self.conn.commit()
        return changed

    def delete_tag(self, tag: str) -> int:
        """Remove a tag from every article. Returns the number of articles changed."""
        tag = tag.strip()
        if not tag:
            return 0
        changed = 0
        for row in self.conn.execute(
            "SELECT id, tags_json FROM articles WHERE tags_json LIKE ?", (f'%"{tag}"%',)
        ).fetchall():
            tags = json.loads(row["tags_json"]) or []
            if tag not in tags:
                continue
            kept = sorted(t for t in tags if t != tag)
            self.conn.execute(
                "UPDATE articles SET tags_json=?, tags_text=?, updated_at=? WHERE id=?",
                (json.dumps(kept), " ".join(kept), _now(), row["id"]),
            )
            changed += 1
        self.conn.commit()
        return changed

    def apply_enrichment(self, article_id: int, summary: str, tags: list[str],
                         model: str = "", rules: str = "",
                         in_scope: bool = True, scope_reason: str = "") -> None:
        row = self.conn.execute("SELECT summary, tags_json FROM articles WHERE id=?", (article_id,)).fetchone()
        if row is None:
            return
        merged_tags = sorted(set(json.loads(row["tags_json"]) or []) | set(tags))
        self.conn.execute(
            "UPDATE articles SET summary=?, tags_json=?, tags_text=?, enriched=1, "
            "enrich_model=?, enrich_rules=?, in_scope=?, scope_reason=?, updated_at=? WHERE id=?",
            (summary or row["summary"], json.dumps(merged_tags), " ".join(merged_tags),
             model, rules, int(in_scope), scope_reason, _now(), article_id),
        )
        self.conn.commit()

    # -- audience-scope review (Phase 3) ---------------------------------------

    def list_flagged(self, limit: int = 2000) -> list[dict]:
        """Articles the enricher flagged as off-audience (in_scope=0), for review."""
        rows = self.conn.execute(
            "SELECT * FROM articles WHERE in_scope=0 ORDER BY id LIMIT ?", (limit,)
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def flagged_count(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM articles WHERE in_scope=0"
        ).fetchone()[0]

    def keep_article(self, article_id: int) -> None:
        """Clear an out-of-scope flag — a false positive you want to keep."""
        self.conn.execute(
            "UPDATE articles SET in_scope=1, updated_at=? WHERE id=?", (_now(), article_id)
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

    def all_articles(self, limit: int = 100000) -> list[dict]:
        """Every row, oldest first. Used by a forced re-enrichment pass that
        re-runs even already-enriched articles (e.g. to standardize the whole
        library on a more capable model)."""
        rows = self.conn.execute(
            "SELECT * FROM articles ORDER BY id LIMIT ?", (limit,)
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

    def get_setting(self, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO settings (key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.conn.commit()

    # -- users / accounts ------------------------------------------------------

    def create_user(self, username: str, password: str, role: str = "user",
                    name: str = "", email: str = "") -> int:
        """Create an account. Raises sqlite3.IntegrityError if the username exists."""
        from .passwords import hash_password
        username = (username or "").strip().lower()
        role = role if role in ("user", "admin") else "user"
        cur = self.conn.execute(
            "INSERT INTO users (username, password_hash, role, active, name, email, created_at) "
            "VALUES (?,?,?,1,?,?,?)",
            (username, hash_password(password), role, name.strip(), email.strip(), _now()),
        )
        self.conn.commit()
        return cur.lastrowid

    def authenticate(self, username: str, password: str) -> Optional[dict]:
        """Return the user dict on a correct password for an active account, else None."""
        from .passwords import verify_password
        row = self.conn.execute(
            "SELECT * FROM users WHERE username=?", ((username or "").strip().lower(),)
        ).fetchone()
        if not row or not row["active"] or not verify_password(password, row["password_hash"]):
            return None
        self.conn.execute("UPDATE users SET last_login_at=? WHERE id=?", (_now(), row["id"]))
        self.conn.commit()
        d = dict(row)
        d.pop("password_hash", None)
        return d

    def list_users(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, username, role, active, name, email, created_at, last_login_at "
            "FROM users ORDER BY role DESC, username"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_user(self, username: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT id, username, role, active, name, email, created_at, last_login_at "
            "FROM users WHERE username=?", ((username or "").strip().lower(),)
        ).fetchone()
        return dict(row) if row else None

    def set_user_active(self, user_id: int, active: bool) -> None:
        self.conn.execute("UPDATE users SET active=? WHERE id=?", (int(active), user_id))
        self.conn.commit()

    def set_user_password(self, user_id: int, password: str) -> None:
        from .passwords import hash_password
        self.conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                          (hash_password(password), user_id))
        self.conn.commit()

    def delete_user(self, user_id: int) -> None:
        self.conn.execute("DELETE FROM users WHERE id=?", (user_id,))
        self.conn.commit()

    def count_users(self, role: Optional[str] = None) -> int:
        if role:
            return self.conn.execute("SELECT COUNT(*) FROM users WHERE role=?", (role,)).fetchone()[0]
        return self.conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def save_contact(self, name: str, email: str, message: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO contacts (name, email, message, created_at) VALUES (?,?,?,?)",
            (name.strip(), email.strip(), message.strip(), _now()),
        )
        self.conn.commit()
        return cur.lastrowid

    def list_contacts(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM contacts ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    # -- tools directory ---------------------------------------------------

    def add_tool(self, name: str, description: str, url: str,
                 categories: list[str], submitted_by: str = "",
                 approved: int = 0, advisor: int = 0,
                 promoted: int = 0, vendor_email: str = "") -> int:
        base = _slugify(name)
        slug = base
        suffix = 2
        while self.conn.execute("SELECT 1 FROM tools WHERE slug=?", (slug,)).fetchone():
            slug = f"{base}-{suffix}"
            suffix += 1
        now = _now()
        cur = self.conn.execute(
            """INSERT INTO tools (name, slug, description, url, categories_json,
               approved, advisor, submitted_by, created_at, updated_at, promoted, vendor_email)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (name.strip(), slug, description.strip(), url.strip(),
             json.dumps(categories), approved, advisor, submitted_by.strip(), now, now,
             promoted, vendor_email.strip()),
        )
        self.conn.commit()
        return cur.lastrowid

    def list_tools(self, approved_only: bool = True) -> list[dict]:
        if approved_only:
            rows = self.conn.execute(
                "SELECT * FROM tools WHERE approved=1 ORDER BY name"
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM tools ORDER BY approved, created_at DESC"
            ).fetchall()
        return [self._tool_to_dict(r) for r in rows]

    def get_tool(self, tool_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM tools WHERE id=?", (tool_id,)).fetchone()
        return self._tool_to_dict(row) if row else None

    def update_tool(self, tool_id: int, name: str, description: str,
                    url: str, categories: list[str], advisor: int = 0,
                    promoted: int = 0, vendor_email: str = "") -> None:
        self.conn.execute(
            """UPDATE tools SET name=?, description=?, url=?, categories_json=?,
               advisor=?, promoted=?, vendor_email=?, updated_at=? WHERE id=?""",
            (name.strip(), description.strip(), url.strip(),
             json.dumps(categories), advisor, promoted, vendor_email.strip(), _now(), tool_id),
        )
        self.conn.commit()

    def approve_tool(self, tool_id: int) -> None:
        self.conn.execute("UPDATE tools SET approved=1 WHERE id=?", (tool_id,))
        self.conn.commit()

    def delete_tool(self, tool_id: int) -> None:
        self.conn.execute("DELETE FROM tools WHERE id=?", (tool_id,))
        self.conn.commit()

    @staticmethod
    def _tool_to_dict(r: sqlite3.Row) -> dict:
        d = dict(r)
        d["categories"] = json.loads(d.pop("categories_json", "[]") or "[]")
        return d

    # -- read later ------------------------------------------------------------

    def add_read_later(self, url: str, title: str = "", source: str = "",
                       summary: str = "", published_at: str | None = None) -> None:
        self.conn.execute(
            """INSERT INTO read_later (url, title, source, summary, published_at, added_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(url) DO UPDATE SET
                   title=excluded.title, source=excluded.source,
                   summary=excluded.summary, published_at=excluded.published_at""",
            (url, title, source, summary, published_at, _now()),
        )
        self.conn.commit()

    def remove_read_later(self, url: str) -> None:
        self.conn.execute("DELETE FROM read_later WHERE url=?", (url,))
        self.conn.commit()

    def list_read_later(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM read_later ORDER BY added_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def read_later_urls(self) -> set[str]:
        return {r[0] for r in self.conn.execute("SELECT url FROM read_later").fetchall()}

    # -- library queue ---------------------------------------------------------

    def article_urls(self) -> set[str]:
        """Every URL already in the library — the dedupe set for the queue."""
        return {r[0] for r in self.conn.execute("SELECT url FROM articles")}

    def queue_urls(self) -> set[str]:
        """Every URL in the queue (pending OR dismissed), so we never re-surface
        a candidate you've already saved or rejected."""
        return {r[0] for r in self.conn.execute("SELECT url FROM library_queue")}

    def last_saved_at(self) -> Optional[str]:
        """The most recent `saved_at` in the library — i.e. your saves cutoff.

        Used by the one-time historical sweep to know how far back to reach.
        """
        row = self.conn.execute("SELECT MAX(saved_at) FROM articles").fetchone()
        return row[0] if row and row[0] else None

    def add_to_queue(self, url: str, title: str = "", author: str = "",
                     source: str = "", summary: str = "", content: str = "",
                     suggested_tags: Optional[list[str]] = None,
                     published_at: Optional[str] = None, origin: str = "",
                     enriched: bool = False, enrich_model: str = "",
                     enrich_rules: str = "") -> bool:
        """Queue a candidate. No-op (returns False) if the URL is already in the
        library or already queued — keeps the queue idempotent like `upsert`.
        The URL is canonicalized first so trivial variants collapse to one."""
        url = normalize_url(url)
        if not url:
            return False
        if self.conn.execute("SELECT 1 FROM articles WHERE url=?", (url,)).fetchone():
            return False
        if self.conn.execute("SELECT 1 FROM library_queue WHERE url=?", (url,)).fetchone():
            return False
        tags = sorted(set(t.strip() for t in (suggested_tags or []) if t.strip()))
        self.conn.execute(
            """INSERT INTO library_queue
               (url, title, author, source, summary, content, suggested_tags_json,
                published_at, origin, status, enriched, enrich_model, enrich_rules,
                created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (url, title, author, source, summary, content, json.dumps(tags),
             published_at, origin, "pending", int(enriched), enrich_model,
             enrich_rules, _now()),
        )
        self.conn.commit()
        return True

    def list_queue(self, status: str = "pending", limit: int = 2000) -> list[dict]:
        rows = self.conn.execute(
            """SELECT * FROM library_queue WHERE status=?
               ORDER BY COALESCE(published_at,'') DESC, id DESC LIMIT ?""",
            (status, limit),
        ).fetchall()
        return [self._queue_to_dict(r) for r in rows]

    def queue_count(self, status: str = "pending") -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM library_queue WHERE status=?", (status,)
        ).fetchone()[0]

    def dismiss_queue_item(self, url: str) -> None:
        """Reject a candidate. It stays in the table as 'dismissed' so a later
        sweep won't propose it again."""
        self.conn.execute(
            "UPDATE library_queue SET status='dismissed' WHERE url=?", (url,)
        )
        self.conn.commit()

    def remove_from_queue(self, url: str) -> None:
        self.conn.execute("DELETE FROM library_queue WHERE url=?", (url,))
        self.conn.commit()

    def update_queue_published(self, url: str, published_at: str) -> None:
        """Correct a queued candidate's publish date (e.g. after re-reading it from
        the article page when the sitemap date was a build stamp)."""
        self.conn.execute(
            "UPDATE library_queue SET published_at=? WHERE url=?", (published_at, url)
        )
        self.conn.commit()

    def update_article_published(self, article_id: int, published_at: str) -> None:
        """Correct a saved article's publish date."""
        self.conn.execute(
            "UPDATE articles SET published_at=?, updated_at=? WHERE id=?",
            (published_at, _now(), article_id),
        )
        self.conn.commit()

    def promote_queue_item(self, url: str, tags: Optional[list[str]] = None) -> int:
        """Move a queued candidate into the library, preserving its enrichment,
        then drop it from the queue. `tags`, if given, overrides the suggestions
        (so your edits at review time win). Returns the article id, or 0 if the
        URL isn't queued."""
        row = self.conn.execute(
            "SELECT * FROM library_queue WHERE url=?", (url,)
        ).fetchone()
        if row is None:
            return 0
        use_tags = tags if tags is not None else json.loads(row["suggested_tags_json"] or "[]")
        art = Article(
            url=row["url"], title=row["title"], author=row["author"],
            source=row["source"], summary=row["summary"], content=row["content"],
            tags=use_tags, published_at=row["published_at"],
            saved_at=_now(), enriched=bool(row["enriched"]),
            enrich_model=row["enrich_model"], enrich_rules=row["enrich_rules"],
        )
        article_id = self.upsert(art)
        self.conn.execute("DELETE FROM library_queue WHERE url=?", (url,))
        self.conn.commit()
        return article_id

    @staticmethod
    def _queue_to_dict(r: sqlite3.Row) -> dict:
        d = dict(r)
        d["suggested_tags"] = json.loads(d.pop("suggested_tags_json", "[]") or "[]")
        return d

    # -- tool leads ------------------------------------------------------------

    def save_tool_lead(self, tool_id: int, tool_name: str, name: str,
                       email: str, company: str, company_size: str) -> int:
        cur = self.conn.execute(
            """INSERT INTO tool_leads (tool_id, tool_name, name, email, company, company_size, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (tool_id, tool_name.strip(), name.strip(), email.strip(),
             company.strip(), company_size.strip(), _now()),
        )
        self.conn.commit()
        return cur.lastrowid

    def list_tool_leads(self, tool_id: int | None = None) -> list[dict]:
        if tool_id is not None:
            rows = self.conn.execute(
                "SELECT * FROM tool_leads WHERE tool_id=? ORDER BY created_at DESC", (tool_id,)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM tool_leads ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_tool_lead_counts(self) -> dict:
        rows = self.conn.execute(
            "SELECT tool_id, COUNT(*) as n FROM tool_leads GROUP BY tool_id"
        ).fetchall()
        return {r["tool_id"]: r["n"] for r in rows}

    def close(self) -> None:
        self.conn.close()


@contextmanager
def open_library(path: str = DEFAULT_DB_PATH) -> Iterator[Library]:
    lib = Library(path)
    try:
        yield lib
    finally:
        lib.close()
