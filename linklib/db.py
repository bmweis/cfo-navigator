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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterator, Optional

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

-- The controlled vocabulary of category pills shown on /tools. Independent of
-- which tools currently use them, so a category can be created empty and
-- tagged onto tools afterward — unlike article tags (all_tags()), which are
-- purely derived from usage. sort_order controls pill/checkbox display order.
CREATE TABLE IF NOT EXISTS tool_categories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    sort_order  INTEGER NOT NULL DEFAULT 0
);

-- Benchmarking Resources section on /tools. coverage: 'Private'|'Public'|'Both'.
-- pricing: 'free'|'paid'|'freemium' (only 'paid'/'freemium' render a $ badge).
CREATE TABLE IF NOT EXISTS benchmarks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL DEFAULT '',
    url         TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    coverage    TEXT NOT NULL DEFAULT 'Private',
    pricing     TEXT NOT NULL DEFAULT 'free',
    sort_order  INTEGER NOT NULL DEFAULT 0
);

-- Personal bookmark list — private per user, never shared with other users
-- or with the admin-curated Archive. user_id has no NOT NULL/UNIQUE
-- constraint here on purpose: on a fresh DB every row gets a real user_id at
-- write time, but a pre-existing DB's rows predate this column (see the
-- table-recreation migration in Library.__init__ for how those get
-- backfilled). The per-(user_id, url) uniqueness is enforced by
-- idx_read_later_user_url in _POST_MIGRATION_INDEXES rather than inline here
-- — see the note above password_reset_requests for why a unique constraint
-- on a migration-only column can't live in this script.
CREATE TABLE IF NOT EXISTS read_later (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER,
    url         TEXT NOT NULL,
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

-- Self-service "forgot password" requests, filed from /login. When the account
-- has an email on file, token_hash (sha256 of the emailed token — never the
-- raw token, so a DB leak alone can't be used to reset a password) +
-- expires_at power a real self-service reset link (see /reset-password).
-- Either way the request also shows up for Brian on /admin/users, so he can
-- reset it by hand — auto-resolves, see resolve_password_resets_for_user —
-- or dismiss it as a false alarm.
CREATE TABLE IF NOT EXISTS password_reset_requests (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    username    TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    resolved_at TEXT NOT NULL DEFAULT '',   -- '' = still pending
    token_hash  TEXT NOT NULL DEFAULT '',   -- '' = no self-service link (e.g. no email on file)
    expires_at  TEXT NOT NULL DEFAULT ''    -- '' = no expiry (legacy rows / no token)
);

CREATE INDEX IF NOT EXISTS idx_pwreset_resolved ON password_reset_requests(resolved_at);
-- NOTE: no CREATE INDEX on token_hash here. This CREATE TABLE is IF NOT
-- EXISTS — on any DB where this table already existed before token_hash was
-- added, this whole block is a no-op and the column doesn't exist yet (it's
-- only added below, by the ALTER TABLE migration, which runs AFTER this
-- entire script). An index here would reference a column that isn't there
-- yet on any pre-existing DB and crash on every boot. Any index on a column
-- that's only ever added via the ALTER TABLE migration list below must be
-- created in _POST_MIGRATION_INDEXES instead, never in this schema string.

-- Durable record of failed outbound-email attempts (contact form, tool
-- submissions, welcome emails, password resets, warm intros). Every send
-- path is best-effort (a broken mailer must never block the underlying DB
-- write), but "best-effort" must not mean "silent" — this table plus the
-- /admin/email-failures badge is how a broken send actually surfaces instead
-- of only ever appearing in a Railway log line nobody's watching.
CREATE TABLE IF NOT EXISTS email_failures (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    context     TEXT NOT NULL DEFAULT '',   -- e.g. 'contact', 'tool_submission', 'welcome', 'password_reset'
    detail      TEXT NOT NULL DEFAULT '',   -- str(exception)
    created_at  TEXT NOT NULL,
    resolved_at TEXT NOT NULL DEFAULT ''    -- '' = still pending
);

CREATE INDEX IF NOT EXISTS idx_email_failures_resolved ON email_failures(resolved_at);

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

-- Curator feedback on near-duplicate judgments. Each row is one decision about a
-- *pair* of articles: 'dup' (the curator removed one as a duplicate) or
-- 'distinct' (the curator said "not a duplicate"). `pair_key` is the two URLs
-- sorted + joined, so a decision about a pair is stored once regardless of order
-- and can be upserted. Used to (a) suppress pairs already judged distinct from
-- future scans and (b) teach the Claude verifier the curator's calls.
CREATE TABLE IF NOT EXISTS dedupe_decisions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    pair_key   TEXT NOT NULL UNIQUE,
    url_a      TEXT NOT NULL DEFAULT '',
    url_b      TEXT NOT NULL DEFAULT '',
    title_a    TEXT NOT NULL DEFAULT '',
    title_b    TEXT NOT NULL DEFAULT '',
    source     TEXT NOT NULL DEFAULT '',
    verdict    TEXT NOT NULL DEFAULT 'distinct',   -- 'dup' | 'distinct'
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_dedupe_verdict ON dedupe_decisions(verdict);

-- Single shared table backing all three Ask/FP&A Buddy surfaces: the admin
-- report, a user's own history, and the public community Q&A browse view.
-- One row per API call (one per turn in a follow-up conversation), grouped by
-- conversation_id. Real cost is computed from actual token usage at call
-- time (see linklib.pricing) — never an estimate.
CREATE TABLE IF NOT EXISTS ask_questions (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id       TEXT NOT NULL DEFAULT '',   -- groups follow-up turns; = str(id) of the first turn
    turn_index            INTEGER NOT NULL DEFAULT 0,
    user_id               INTEGER NOT NULL,
    question              TEXT NOT NULL DEFAULT '',
    answer                TEXT NOT NULL DEFAULT '',
    model                 TEXT NOT NULL DEFAULT '',
    effort                TEXT NOT NULL DEFAULT '',
    use_library           INTEGER NOT NULL DEFAULT 1,
    use_feed              INTEGER NOT NULL DEFAULT 0,
    use_web               INTEGER NOT NULL DEFAULT 1,
    input_tokens          INTEGER NOT NULL DEFAULT 0,
    output_tokens         INTEGER NOT NULL DEFAULT 0,
    cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens     INTEGER NOT NULL DEFAULT 0,
    cost_usd              REAL NOT NULL DEFAULT 0,
    hidden_public         INTEGER NOT NULL DEFAULT 0,  -- admin removed from the community view only
    anonymized            INTEGER NOT NULL DEFAULT 0,  -- asker name hidden on the community view only
    created_at            TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ask_questions_user ON ask_questions(user_id);
CREATE INDEX IF NOT EXISTS idx_ask_questions_created ON ask_questions(created_at);
CREATE INDEX IF NOT EXISTS idx_ask_questions_conversation ON ask_questions(conversation_id);

-- "Sail, Don't Row" — one row per rank (Deckhand/Mate/First Mate/Skipper), the
-- tunable knobs the game engine reads instead of hardcoded constants, so Brian
-- can rebalance pacing/difficulty from /admin without a redeploy. Seeded with
-- the Phase 0 proposal on first run (see _seed_game_settings); the DB is the
-- source of truth after that.
-- Round 2 (post-playtesting) removed manual rowing and the Stamina resource
-- entirely — difficulty now comes from a speed ramp instead. drift_speed is
-- repurposed as the base forward speed at t=0 (ramps up via the
-- speed_ramp_per_sec column, added by migration below) and sail_speed is now
-- an additive bonus while in a gust, on top of the current ramped base —
-- rather than the old absolute override speed. row_speed/stamina_drain_per_sec/
-- stamina_regen_per_sec are unused dead columns kept only because this
-- codebase's migrations are additive-only (see the ALTER TABLE list in
-- __init__) — no code reads or writes them anymore.
CREATE TABLE IF NOT EXISTS game_rank_settings (
    rank                   TEXT PRIMARY KEY,       -- 'deckhand' | 'mate' | 'first_mate' | 'skipper'
    label                  TEXT NOT NULL,           -- 'Deckhand', 'Mate', ...
    difficulty_label       TEXT NOT NULL,           -- 'Easy', 'Medium', 'Hard', 'Expert'
    collision_limit        INTEGER NOT NULL,        -- hits before sunk; 0 = no penalty (practice mode)
    grace_window           INTEGER NOT NULL DEFAULT 1, -- 1 = brief invincibility after a hit; 0 = none (Skipper)
    par_time_seconds       INTEGER NOT NULL,        -- target full-course finish time
    gust_coverage_pct      REAL NOT NULL,           -- % of course length covered by wind gusts
    obstacle_density       REAL NOT NULL,           -- obstacles per 1000 world-units of channel
    drift_speed            REAL NOT NULL,           -- world-units/sec, base speed at t=0 (ramps up)
    row_speed              REAL NOT NULL,           -- unused since Round 2 (rowing removed)
    sail_speed             REAL NOT NULL,           -- world-units/sec, additive bonus while in a gust
    stamina_drain_per_sec  REAL NOT NULL,           -- unused since Round 2 (Stamina removed)
    stamina_regen_per_sec  REAL NOT NULL,           -- unused since Round 2 (Stamina removed)
    sort_order             INTEGER NOT NULL DEFAULT 0,
    updated_at             TEXT NOT NULL DEFAULT ''
);

-- "Sail, Don't Row" — one row per submitted run, backing the public
-- leaderboard. Round 2 flipped this from per-rank tables to a single
-- combined list (each row tagged with the rank it was played on, shown as
-- a badge) — see list_game_leaderboard. difficulty_index/difficulty_label
-- are computed once at write time from that week's rank settings and frozen
-- on the row — display never recomputes them from current settings, so a
-- later admin retune can't retroactively relabel a past week's runs.
-- Score/distance/time/hits are client-reported (this is a client-
-- authoritative DOM+CSS game with no server-side simulation, same trust
-- model as obstacle placement) — bounds-checked at write time, but not
-- defended against a determined cheater.
CREATE TABLE IF NOT EXISTS game_runs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           INTEGER NOT NULL,
    rank              TEXT NOT NULL,             -- 'deckhand' | 'mate' | 'first_mate' | 'skipper'
    score             INTEGER NOT NULL,          -- 0-100, primary leaderboard sort key
    distance_fraction REAL NOT NULL,             -- 0.0-1.0
    finished          INTEGER NOT NULL DEFAULT 0,-- 1 = reached Nantucket, 0 = sunk/partial
    time_seconds      REAL NOT NULL,
    efficiency_pct    REAL NOT NULL,             -- unused since Round 2 (Stamina removed); always 0
    course_week       TEXT NOT NULL,             -- ISO week, e.g. '2026-W27'
    difficulty_index  INTEGER NOT NULL,          -- 0-100, frozen at write time
    difficulty_label  TEXT NOT NULL,             -- 'Fair Winds' | 'Choppy Waters' | 'Rough Seas' | 'Storm Warning'
    created_at        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_game_runs_leaderboard ON game_runs(rank, course_week, score DESC);
CREATE INDEX IF NOT EXISTS idx_game_runs_user ON game_runs(user_id);

-- Audit trail for admin curation of the Archive (the `articles` table).
-- Every admin add/edit/delete on the Archive writes one row here — who,
-- what action, which item, when. item_id is nullable because a few admin
-- actions (bulk tag rename/merge/delete) touch many articles at once; those
-- log a single summary row (item_id NULL, detail describing the change)
-- rather than one row per affected article. admin_id is nullable for the
-- same reason _current_user_id can return None: the break-glass host-
-- password admin login has no matching `users` row on some installs.
CREATE TABLE IF NOT EXISTS archive_audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id   INTEGER,
    action     TEXT NOT NULL,             -- 'add' | 'edit' | 'delete'
    item_id    INTEGER,                    -- articles.id; NULL for a bulk operation
    detail     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_archive_audit_admin ON archive_audit_log(admin_id);
CREATE INDEX IF NOT EXISTS idx_archive_audit_created ON archive_audit_log(created_at);
"""

# Indexes that reference a column added via the ALTER TABLE migration list in
# Library.__init__, rather than one present in every CREATE TABLE from day
# one. These must run AFTER that migration loop, not inside _SCHEMA — see the
# comment above password_reset_requests in _SCHEMA for the incident that
# happens if one lands there instead (crashes on boot against any DB where
# the table pre-dates the column, because CREATE TABLE IF NOT EXISTS is a
# no-op there and the column isn't added until the migration loop runs).
_POST_MIGRATION_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_pwreset_token ON password_reset_requests(token_hash)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_read_later_user_url ON read_later(user_id, url)",
]


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
            # Per-user Ask dollar-cap override. NULL = inherit the global default
            # (settings['ask_default_cap_usd']) rather than a hardcoded per-user value.
            "ALTER TABLE users ADD COLUMN ask_cap_usd REAL",
            # Warm Intro: a distinct opt-in from `advisor` (which just marks Brian as
            # a formal advisor to the company) — the intro button only shows when
            # this is on AND a vendor contact email exists.
            "ALTER TABLE tools ADD COLUMN warm_intro_enabled INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE tools ADD COLUMN vendor_name TEXT NOT NULL DEFAULT ''",
            # Round 2: rowing/Stamina removed, difficulty now comes from a speed
            # ramp instead — drift_speed/sail_speed columns are repurposed
            # (see the CREATE TABLE comment above), this is the one genuinely
            # new column the ramp needs.
            "ALTER TABLE game_rank_settings ADD COLUMN speed_ramp_per_sec REAL NOT NULL DEFAULT 0",
            # Round 2: collisions now directly affect score, so the leaderboard
            # (and the outcome screen's new Hits stat) needs the count on the row.
            "ALTER TABLE game_runs ADD COLUMN hits INTEGER NOT NULL DEFAULT 0",
            # Shark pursuit hazard (Mate+ only, Martha's Vineyard leg onward) —
            # rank-tunable cruise/lunge behavior. Deckhand's row gets 0s since
            # the shark never spawns there; harmless, never read for that rank.
            "ALTER TABLE game_rank_settings ADD COLUMN shark_cruise_distance REAL NOT NULL DEFAULT 0",
            "ALTER TABLE game_rank_settings ADD COLUMN shark_lunge_interval_sec REAL NOT NULL DEFAULT 0",
            "ALTER TABLE game_rank_settings ADD COLUMN shark_lunge_speed REAL NOT NULL DEFAULT 0",
            "ALTER TABLE game_rank_settings ADD COLUMN shark_lunge_duration_sec REAL NOT NULL DEFAULT 0",
            # Self-service password reset link — added after the request/notify-Brian
            # flow shipped, so existing pending requests just get '' (no link).
            "ALTER TABLE password_reset_requests ADD COLUMN token_hash TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE password_reset_requests ADD COLUMN expires_at TEXT NOT NULL DEFAULT ''",
        ]:
            try:
                self.conn.execute(_col_sql)
                self.conn.commit()
            except sqlite3.OperationalError:
                pass
        # read_later predates per-user scoping (no user_id column, UNIQUE(url)
        # inline constraint) — a plain ALTER TABLE ADD COLUMN can't fix the
        # uniqueness half of that, so it gets its own table-recreation
        # migration rather than a line in the loop above. Must run before
        # _POST_MIGRATION_INDEXES, which assumes user_id already exists.
        self._migrate_read_later_user_scope()
        # Indexes on any column added by the ALTER TABLE loop above must be
        # created here, never inside _SCHEMA — see the NOTE above the
        # password_reset_requests table in _SCHEMA for why (a real incident:
        # an index on token_hash inside _SCHEMA broke every boot against a
        # pre-existing DB, since _SCHEMA's CREATE TABLE IF NOT EXISTS is a
        # no-op there and the column doesn't land until this loop runs).
        for _idx_sql in _POST_MIGRATION_INDEXES:
            self.conn.execute(_idx_sql)
        self.conn.commit()

    def _migrate_read_later_user_scope(self) -> None:
        """One-time table recreation for DBs whose read_later predates
        per-user scoping. It shipped as a single shared list (UNIQUE(url),
        no owner column) back when only one admin used it; now that other
        signed-in users can read/save independently, every row needs an
        owner. SQLite can't drop or alter a UNIQUE constraint in place, so
        this renames the old table aside, recreates it with user_id, copies
        the data across (attributed to the earliest admin account, since
        that's who saved it under the old single-user design), and drops
        the old table. A no-op on a fresh DB, where _SCHEMA already created
        read_later with user_id."""
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(read_later)").fetchall()}
        if "user_id" in cols:
            return
        default_admin = self.conn.execute(
            "SELECT id FROM users WHERE role='admin' ORDER BY id LIMIT 1"
        ).fetchone()
        default_user_id = default_admin[0] if default_admin else None
        self.conn.execute("ALTER TABLE read_later RENAME TO read_later_legacy")
        self.conn.execute(
            """CREATE TABLE read_later (
                   id          INTEGER PRIMARY KEY AUTOINCREMENT,
                   user_id     INTEGER,
                   url         TEXT NOT NULL,
                   title       TEXT NOT NULL DEFAULT '',
                   source      TEXT NOT NULL DEFAULT '',
                   summary     TEXT NOT NULL DEFAULT '',
                   published_at TEXT,
                   added_at    TEXT NOT NULL
               )"""
        )
        self.conn.execute(
            """INSERT INTO read_later (user_id, url, title, source, summary, published_at, added_at)
               SELECT ?, url, title, source, summary, published_at, added_at FROM read_later_legacy""",
            (default_user_id,),
        )
        self.conn.execute("DROP TABLE read_later_legacy")
        self.conn.commit()

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

    def recent_articles_by_source(self, source: str, limit: int = 20) -> list[dict]:
        """Most-recently-saved articles from one source — the 'what I keep' examples
        for predicting which queued candidates the curator would approve."""
        rows = self.conn.execute(
            "SELECT * FROM articles WHERE source=? "
            "ORDER BY COALESCE(saved_at,'') DESC, id DESC LIMIT ?", (source, limit)
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def articles_by_source(self, source: str) -> list[dict]:
        """Every saved article from one source — used by near-duplicate clustering."""
        rows = self.conn.execute(
            "SELECT * FROM articles WHERE source=? ORDER BY COALESCE(published_at,'') DESC, id DESC",
            (source,)
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def article_sources(self) -> list[tuple[str, int]]:
        """Distinct sources with article counts, most first."""
        rows = self.conn.execute(
            "SELECT COALESCE(NULLIF(source,''),'(none)') AS s, COUNT(*) n "
            "FROM articles GROUP BY s ORDER BY n DESC"
        ).fetchall()
        return [(r["s"], r["n"]) for r in rows]

    def recent_articles(self, limit: int = 40) -> list[dict]:
        """Most-recently-saved articles across the whole library — the fallback
        taste profile when one source has few prior approvals."""
        rows = self.conn.execute(
            "SELECT * FROM articles ORDER BY COALESCE(saved_at,'') DESC, id DESC LIMIT ?",
            (limit,)
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

    # --- near-duplicate feedback ------------------------------------------

    @staticmethod
    def _pair_key(url_a: str, url_b: str) -> str:
        """Order-independent key for a pair of article URLs."""
        return "\n".join(sorted([url_a or "", url_b or ""]))

    def record_dedupe_decision(self, a: dict, b: dict, verdict: str,
                               source: str = "") -> None:
        """Remember the curator's call on a pair: 'dup' or 'distinct'. Upserts on
        the unordered URL pair, so re-deciding overwrites the prior verdict."""
        ua, ub = a.get("url", ""), b.get("url", "")
        # store with url_a/title_a being the lexicographically smaller url, stable
        first_a = (ua or "") <= (ub or "")
        xa, xb = (a, b) if first_a else (b, a)
        self.conn.execute(
            "INSERT INTO dedupe_decisions (pair_key, url_a, url_b, title_a, title_b, "
            "source, verdict, created_at) VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(pair_key) DO UPDATE SET verdict=excluded.verdict, "
            "created_at=excluded.created_at",
            (self._pair_key(ua, ub), xa.get("url", ""), xb.get("url", ""),
             xa.get("title", ""), xb.get("title", ""), source, verdict, _now()),
        )
        self.conn.commit()

    def distinct_pairs(self) -> set:
        """Set of pair_keys the curator marked 'distinct' (not duplicates)."""
        rows = self.conn.execute(
            "SELECT pair_key FROM dedupe_decisions WHERE verdict='distinct'").fetchall()
        return {r[0] for r in rows}

    def dedupe_decisions(self, source: str | None = None, limit: int = 40) -> list[dict]:
        """Recent decisions (newest first) for teaching the verifier."""
        if source:
            rows = self.conn.execute(
                "SELECT title_a, title_b, verdict, source FROM dedupe_decisions "
                "WHERE source=? ORDER BY created_at DESC LIMIT ?", (source, limit)).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT title_a, title_b, verdict, source FROM dedupe_decisions "
                "ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [{"title_a": r[0], "title_b": r[1], "verdict": r[2], "source": r[3]} for r in rows]

    def dedupe_decision_counts(self) -> tuple[int, int]:
        """(dup_count, distinct_count) — for a 'learned from N decisions' note."""
        d = self.conn.execute(
            "SELECT verdict, COUNT(*) FROM dedupe_decisions GROUP BY verdict").fetchall()
        m = {v: c for v, c in d}
        return m.get("dup", 0), m.get("distinct", 0)

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
            "SELECT id, username, role, active, name, email, created_at, last_login_at, ask_cap_usd "
            "FROM users ORDER BY role DESC, username"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_user(self, username: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT id, username, role, active, name, email, created_at, last_login_at, ask_cap_usd "
            "FROM users WHERE username=?", ((username or "").strip().lower(),)
        ).fetchone()
        return dict(row) if row else None

    def set_user_active(self, user_id: int, active: bool) -> None:
        self.conn.execute("UPDATE users SET active=? WHERE id=?", (int(active), user_id))
        self.conn.commit()

    def set_user_role(self, user_id: int, role: str) -> None:
        """Promote/demote an account. Role is coerced to 'user' or 'admin'."""
        role = role if role in ("user", "admin") else "user"
        self.conn.execute("UPDATE users SET role=? WHERE id=?", (role, user_id))
        self.conn.commit()

    def set_user_password(self, user_id: int, password: str) -> None:
        from .passwords import hash_password
        self.conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                          (hash_password(password), user_id))
        self.conn.commit()

    def update_user(self, user_id: int, username: str | None = None,
                    name: str | None = None, email: str | None = None) -> None:
        """Edit an account's username, display name, and/or email. Username is
        normalized (lowercased/trimmed) and unique — raises sqlite3.IntegrityError
        if taken. Pass None to leave a field unchanged."""
        sets, vals = [], []
        if username is not None:
            sets.append("username=?")
            vals.append(username.strip().lower())
        if name is not None:
            sets.append("name=?")
            vals.append(name.strip())
        if email is not None:
            sets.append("email=?")
            vals.append(email.strip())
        if not sets:
            return
        vals.append(user_id)
        self.conn.execute(f"UPDATE users SET {', '.join(sets)} WHERE id=?", vals)
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

    def count_contacts_since(self, ts: str) -> int:
        """Contacts created after `ts` (an ISO timestamp, '' = every row —
        string comparison against '' is true for any non-empty created_at)."""
        return self.conn.execute(
            "SELECT COUNT(*) FROM contacts WHERE created_at > ?", (ts,)
        ).fetchone()[0]

    # -- tools directory ---------------------------------------------------

    def add_tool(self, name: str, description: str, url: str,
                 categories: list[str], submitted_by: str = "",
                 approved: int = 0, advisor: int = 0,
                 promoted: int = 0, vendor_email: str = "",
                 warm_intro_enabled: int = 0, vendor_name: str = "") -> int:
        base = _slugify(name)
        slug = base
        suffix = 2
        while self.conn.execute("SELECT 1 FROM tools WHERE slug=?", (slug,)).fetchone():
            slug = f"{base}-{suffix}"
            suffix += 1
        now = _now()
        cur = self.conn.execute(
            """INSERT INTO tools (name, slug, description, url, categories_json,
               approved, advisor, submitted_by, created_at, updated_at, promoted, vendor_email,
               warm_intro_enabled, vendor_name)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (name.strip(), slug, description.strip(), url.strip(),
             json.dumps(categories), approved, advisor, submitted_by.strip(), now, now,
             promoted, vendor_email.strip(), warm_intro_enabled, vendor_name.strip()),
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
                    promoted: int = 0, vendor_email: str = "",
                    warm_intro_enabled: int = 0, vendor_name: str = "") -> None:
        self.conn.execute(
            """UPDATE tools SET name=?, description=?, url=?, categories_json=?,
               advisor=?, promoted=?, vendor_email=?, warm_intro_enabled=?, vendor_name=?,
               updated_at=? WHERE id=?""",
            (name.strip(), description.strip(), url.strip(),
             json.dumps(categories), advisor, promoted, vendor_email.strip(),
             warm_intro_enabled, vendor_name.strip(), _now(), tool_id),
        )
        self.conn.commit()

    def quick_update_tool(self, tool_id: int, description: str,
                          warm_intro_enabled: int, vendor_name: str,
                          vendor_email: str) -> None:
        """Partial update for the /tools inline "Quick edit" panel — touches
        only description and warm-intro fields, leaving name/url/categories/
        advisor/promoted untouched (those still require the full edit form)."""
        self.conn.execute(
            """UPDATE tools SET description=?, warm_intro_enabled=?, vendor_name=?,
               vendor_email=?, updated_at=? WHERE id=?""",
            (description.strip(), warm_intro_enabled, vendor_name.strip(),
             vendor_email.strip(), _now(), tool_id),
        )
        self.conn.commit()

    def approve_tool(self, tool_id: int) -> None:
        self.conn.execute("UPDATE tools SET approved=1 WHERE id=?", (tool_id,))
        self.conn.commit()

    def count_pending_tools(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM tools WHERE approved=0").fetchone()[0]

    def delete_tool(self, tool_id: int) -> None:
        self.conn.execute("DELETE FROM tools WHERE id=?", (tool_id,))
        self.conn.commit()

    @staticmethod
    def _tool_to_dict(r: sqlite3.Row) -> dict:
        d = dict(r)
        d["categories"] = json.loads(d.pop("categories_json", "[]") or "[]")
        return d

    # -- tool categories (the /tools filter pills) --------------------------
    # Unlike article tags, this is a curated vocabulary independent of usage —
    # a category can exist with zero tools tagged to it, ready to assign.

    def list_tool_categories(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, name, description, sort_order FROM tool_categories ORDER BY sort_order, name"
        ).fetchall()
        counts: dict[str, int] = {}
        for (cj,) in self.conn.execute("SELECT categories_json FROM tools"):
            for c in json.loads(cj) or []:
                counts[c] = counts.get(c, 0) + 1
        result = []
        for r in rows:
            d = dict(r)
            d["tool_count"] = counts.get(d["name"], 0)
            result.append(d)
        return result

    def add_tool_category(self, name: str, description: str = "") -> int:
        name, description = name.strip(), description.strip()
        if not name:
            raise ValueError("Category name is required.")
        existing = self.conn.execute(
            "SELECT 1 FROM tool_categories WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchone()
        if existing:
            raise ValueError(f'A category named "{name}" already exists.')
        next_order = self.conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM tool_categories"
        ).fetchone()[0]
        cur = self.conn.execute(
            "INSERT INTO tool_categories (name, description, sort_order) VALUES (?,?,?)",
            (name, description, next_order),
        )
        self.conn.commit()
        return cur.lastrowid

    def rename_tool_category(self, category_id: int, new_name: str, description: str = "") -> int:
        """Rename/re-describe a category, cascading the name change onto every
        tool that has it. Returns the number of tools whose categories_json
        changed. Raises ValueError if the new name collides with a different
        existing category."""
        new_name, description = new_name.strip(), description.strip()
        if not new_name:
            raise ValueError("Category name is required.")
        row = self.conn.execute(
            "SELECT name FROM tool_categories WHERE id = ?", (category_id,)
        ).fetchone()
        if not row:
            raise ValueError("Category not found.")
        old_name = row["name"]
        if new_name.lower() != old_name.lower():
            collision = self.conn.execute(
                "SELECT 1 FROM tool_categories WHERE name = ? COLLATE NOCASE AND id != ?",
                (new_name, category_id),
            ).fetchone()
            if collision:
                raise ValueError(f'A category named "{new_name}" already exists.')
        self.conn.execute(
            "UPDATE tool_categories SET name=?, description=? WHERE id=?",
            (new_name, description, category_id),
        )
        changed = 0
        if new_name != old_name:
            for t in self.conn.execute(
                "SELECT id, categories_json FROM tools WHERE categories_json LIKE ?", (f'%"{old_name}"%',)
            ).fetchall():
                cats = json.loads(t["categories_json"]) or []
                if old_name not in cats:
                    continue
                updated = sorted(set(new_name if c == old_name else c for c in cats))
                self.conn.execute(
                    "UPDATE tools SET categories_json=? WHERE id=?",
                    (json.dumps(updated), t["id"]),
                )
                changed += 1
        self.conn.commit()
        return changed

    def delete_tool_category(self, category_id: int) -> int:
        """Delete a category and strip it from every tool that has it. Tools
        left with no categories still show under "All" on /tools, just not
        under any specific filter. Returns the number of tools changed."""
        row = self.conn.execute(
            "SELECT name FROM tool_categories WHERE id = ?", (category_id,)
        ).fetchone()
        if not row:
            return 0
        name = row["name"]
        self.conn.execute("DELETE FROM tool_categories WHERE id = ?", (category_id,))
        changed = 0
        for t in self.conn.execute(
            "SELECT id, categories_json FROM tools WHERE categories_json LIKE ?", (f'%"{name}"%',)
        ).fetchall():
            cats = json.loads(t["categories_json"]) or []
            if name not in cats:
                continue
            kept = [c for c in cats if c != name]
            self.conn.execute(
                "UPDATE tools SET categories_json=? WHERE id=?",
                (json.dumps(kept), t["id"]),
            )
            changed += 1
        self.conn.commit()
        return changed

    # -- benchmarking resources (the /tools "Benchmarking Resources" section) --

    def list_benchmarks(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM benchmarks ORDER BY sort_order, name"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_benchmark(self, benchmark_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM benchmarks WHERE id = ?", (benchmark_id,)).fetchone()
        return dict(row) if row else None

    def add_benchmark(self, name: str, url: str, description: str,
                      coverage: str = "Private", pricing: str = "free") -> int:
        next_order = self.conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM benchmarks"
        ).fetchone()[0]
        cur = self.conn.execute(
            "INSERT INTO benchmarks (name, url, description, coverage, pricing, sort_order) VALUES (?,?,?,?,?,?)",
            (name.strip(), url.strip(), description.strip(), coverage, pricing, next_order),
        )
        self.conn.commit()
        return cur.lastrowid

    def update_benchmark(self, benchmark_id: int, name: str, url: str, description: str,
                         coverage: str, pricing: str) -> None:
        self.conn.execute(
            "UPDATE benchmarks SET name=?, url=?, description=?, coverage=?, pricing=? WHERE id=?",
            (name.strip(), url.strip(), description.strip(), coverage, pricing, benchmark_id),
        )
        self.conn.commit()

    def delete_benchmark(self, benchmark_id: int) -> None:
        self.conn.execute("DELETE FROM benchmarks WHERE id = ?", (benchmark_id,))
        self.conn.commit()

    # -- read later ---------------------------------------------------------
    # A personal bookmark list — every method takes user_id and scopes to it,
    # so one user's saves are never visible to or affected by another's.

    def add_read_later(self, user_id: int, url: str, title: str = "", source: str = "",
                       summary: str = "", published_at: str | None = None) -> None:
        self.conn.execute(
            """INSERT INTO read_later (user_id, url, title, source, summary, published_at, added_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(user_id, url) DO UPDATE SET
                   title=excluded.title, source=excluded.source,
                   summary=excluded.summary, published_at=excluded.published_at""",
            (user_id, url, title, source, summary, published_at, _now()),
        )
        self.conn.commit()

    def remove_read_later(self, user_id: int, url: str) -> None:
        self.conn.execute("DELETE FROM read_later WHERE user_id=? AND url=?", (user_id, url))
        self.conn.commit()

    def list_read_later(self, user_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM read_later WHERE user_id=? ORDER BY added_at DESC", (user_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def read_later_urls(self, user_id: int) -> set[str]:
        return {r[0] for r in self.conn.execute(
            "SELECT url FROM read_later WHERE user_id=?", (user_id,)
        ).fetchall()}

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

    def count_tool_leads_since(self, ts: str) -> int:
        """Leads created after `ts` (an ISO timestamp, '' = every row)."""
        return self.conn.execute(
            "SELECT COUNT(*) FROM tool_leads WHERE created_at > ?", (ts,)
        ).fetchone()[0]

    # -- password reset requests -------------------------------------------

    def create_password_reset_request(self, user_id: int, username: str,
                                       token_hash: str = "", expires_at: str = "") -> int:
        cur = self.conn.execute(
            "INSERT INTO password_reset_requests (user_id, username, created_at, token_hash, expires_at) "
            "VALUES (?,?,?,?,?)",
            (user_id, username, _now(), token_hash, expires_at),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_password_reset_by_token_hash(self, token_hash: str) -> Optional[dict]:
        """The still-pending, unexpired reset request matching this token's
        hash, or None if it doesn't exist, was already used, or expired."""
        if not token_hash:
            return None
        row = self.conn.execute(
            "SELECT * FROM password_reset_requests "
            "WHERE token_hash=? AND resolved_at='' AND expires_at > ?",
            (token_hash, _now()),
        ).fetchone()
        return dict(row) if row else None

    def list_password_reset_requests(self, pending_only: bool = True) -> list[dict]:
        if pending_only:
            rows = self.conn.execute(
                "SELECT * FROM password_reset_requests WHERE resolved_at='' ORDER BY created_at"
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM password_reset_requests ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def count_pending_password_resets(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM password_reset_requests WHERE resolved_at=''"
        ).fetchone()[0]

    def resolve_password_resets_for_user(self, user_id: int) -> None:
        """Clear pending requests for a user — called when Brian actually resets
        their password, so fixing the problem also clears the badge."""
        self.conn.execute(
            "UPDATE password_reset_requests SET resolved_at=? WHERE user_id=? AND resolved_at=''",
            (_now(), user_id),
        )
        self.conn.commit()

    def dismiss_password_reset(self, request_id: int) -> None:
        self.conn.execute(
            "UPDATE password_reset_requests SET resolved_at=? WHERE id=?",
            (_now(), request_id),
        )
        self.conn.commit()

    # -- email delivery failures ---------------------------------------------

    def log_email_failure(self, context: str, detail: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO email_failures (context, detail, created_at) VALUES (?,?,?)",
            (context, detail, _now()),
        )
        self.conn.commit()
        return cur.lastrowid

    def list_email_failures(self, pending_only: bool = True) -> list[dict]:
        if pending_only:
            rows = self.conn.execute(
                "SELECT * FROM email_failures WHERE resolved_at='' ORDER BY created_at DESC"
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM email_failures ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def count_pending_email_failures(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM email_failures WHERE resolved_at=''"
        ).fetchone()[0]

    def dismiss_email_failure(self, failure_id: int) -> None:
        self.conn.execute(
            "UPDATE email_failures SET resolved_at=? WHERE id=?",
            (_now(), failure_id),
        )
        self.conn.commit()

    # -- Ask / FP&A Buddy — shared question archive + dollar-cap tracking ------

    _DEFAULT_ASK_CAP_USD = 5.00  # coffee-money default; overridable via settings, never hardcoded elsewhere

    def record_ask_question(self, user_id: int, question: str, answer: str,
                            model: str, effort: str, use_library: bool, use_feed: bool,
                            use_web: bool, conversation_id: str = "", turn_index: int = 0,
                            input_tokens: int = 0, output_tokens: int = 0,
                            cache_creation_tokens: int = 0, cache_read_tokens: int = 0,
                            cost_usd: float = 0.0) -> int:
        """Record one Ask API call. Backs all three surfaces (admin report, a
        user's own history, and the public community view) from one row.
        `conversation_id` groups follow-up turns; pass "" on the first turn of
        a conversation and the caller fills it in with str(id) after insert."""
        now = _now()
        cur = self.conn.execute(
            """INSERT INTO ask_questions
               (conversation_id, turn_index, user_id, question, answer, model, effort,
                use_library, use_feed, use_web, input_tokens, output_tokens,
                cache_creation_tokens, cache_read_tokens, cost_usd, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (conversation_id, turn_index, user_id, question.strip(), answer,
             model, effort, int(use_library), int(use_feed), int(use_web),
             input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens,
             cost_usd, now),
        )
        row_id = cur.lastrowid
        if not conversation_id:
            self.conn.execute(
                "UPDATE ask_questions SET conversation_id=? WHERE id=?", (str(row_id), row_id)
            )
        self.conn.commit()
        return row_id

    def ask_cost_all_time(self, user_id: int) -> float:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(cost_usd),0) FROM ask_questions WHERE user_id=?", (user_id,)
        ).fetchone()
        return float(row[0])

    def ask_cost_this_month(self, user_id: int) -> float:
        month_start = datetime.now(timezone.utc).strftime("%Y-%m-01")
        row = self.conn.execute(
            "SELECT COALESCE(SUM(cost_usd),0) FROM ask_questions WHERE user_id=? AND created_at >= ?",
            (user_id, month_start),
        ).fetchone()
        return float(row[0])

    def ask_cost_total(self, user_id: int | None = None, since: str | None = None) -> float:
        """Aggregate cost across all users (or one), optionally since an ISO
        date/datetime prefix — a direct SQL SUM rather than fetching rows into
        Python, so it's correct regardless of how many rows exist (unlike
        summing a size-limited `list_ask_questions()` page) and doesn't load
        full answer text into memory just to add up a number."""
        where, params = [], []
        if user_id is not None:
            where.append("user_id=?")
            params.append(user_id)
        if since:
            where.append("created_at>=?")
            params.append(since)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        row = self.conn.execute(
            f"SELECT COALESCE(SUM(cost_usd),0) FROM ask_questions {clause}", params
        ).fetchone()
        return float(row[0])

    def get_default_ask_cap(self) -> float:
        raw = self.get_setting("ask_default_cap_usd")
        try:
            return float(raw) if raw else self._DEFAULT_ASK_CAP_USD
        except ValueError:
            return self._DEFAULT_ASK_CAP_USD

    def set_default_ask_cap(self, cap_usd: float) -> None:
        self.set_setting("ask_default_cap_usd", str(cap_usd))

    def get_effective_ask_cap(self, user_id: int) -> float:
        """The dollar cap that actually applies to this user this month —
        their per-user override if set, else the global default. Kept as data
        (settings + a per-user column) rather than logic, so a future paid
        tier is just a different row, not a code branch."""
        row = self.conn.execute("SELECT ask_cap_usd FROM users WHERE id=?", (user_id,)).fetchone()
        if row and row["ask_cap_usd"] is not None:
            return float(row["ask_cap_usd"])
        return self.get_default_ask_cap()

    def set_user_ask_cap(self, user_id: int, cap_usd: float | None) -> None:
        """Set a per-user override, or pass None to clear it (inherit the default)."""
        self.conn.execute("UPDATE users SET ask_cap_usd=? WHERE id=?", (cap_usd, user_id))
        self.conn.commit()

    # -- Ask / FP&A Buddy — the three reporting surfaces (admin, user, public) --

    def list_ask_questions(self, user_id: int | None = None,
                           limit: int = 500, offset: int = 0) -> list[dict]:
        """Newest-first Q&A rows, joined with the asker's identity. Pass
        `user_id` to scope to one user's own history; omit for the full
        admin archive."""
        where = "WHERE aq.user_id=?" if user_id is not None else ""
        params: list = [user_id] if user_id is not None else []
        rows = self.conn.execute(
            f"""SELECT aq.*, u.username AS asker_username, u.name AS asker_name
                FROM ask_questions aq LEFT JOIN users u ON u.id = aq.user_id
                {where}
                ORDER BY aq.created_at DESC LIMIT ? OFFSET ?""",
            params + [limit, offset],
        ).fetchall()
        return [dict(r) for r in rows]

    def count_ask_questions(self, user_id: int | None = None) -> int:
        if user_id is not None:
            return self.conn.execute(
                "SELECT COUNT(*) FROM ask_questions WHERE user_id=?", (user_id,)
            ).fetchone()[0]
        return self.conn.execute("SELECT COUNT(*) FROM ask_questions").fetchone()[0]

    def list_public_ask_questions(self, query: str = "", limit: int = 200) -> list[dict]:
        """Non-hidden Q&A for the community browse view, newest first, optionally
        text-filtered on question/answer. Callers render `asker_name`/
        `asker_username` unless `anonymized` is set, in which case show a
        generic label instead — anonymizing here never affects the admin or
        the asker's own history view, both of which always show the real name."""
        base = """SELECT aq.*, u.username AS asker_username, u.name AS asker_name
                  FROM ask_questions aq LEFT JOIN users u ON u.id = aq.user_id
                  WHERE aq.hidden_public=0"""
        params: list = []
        q = query.strip()
        if q:
            base += " AND (aq.question LIKE ? OR aq.answer LIKE ?)"
            like = f"%{q}%"
            params += [like, like]
        base += " ORDER BY aq.created_at DESC LIMIT ?"
        params.append(limit)
        rows = self.conn.execute(base, params).fetchall()
        return [dict(r) for r in rows]

    def set_ask_question_hidden(self, question_id: int, hidden: bool) -> None:
        """Remove (or restore) a Q&A from the public community view only —
        never deletes it from the admin archive or the asker's own history."""
        self.conn.execute(
            "UPDATE ask_questions SET hidden_public=? WHERE id=?", (int(hidden), question_id)
        )
        self.conn.commit()

    def set_ask_question_anonymized(self, question_id: int, anonymized: bool) -> None:
        """Hide the asker's name on the public community view only — the
        admin archive always shows who actually asked."""
        self.conn.execute(
            "UPDATE ask_questions SET anonymized=? WHERE id=?", (int(anonymized), question_id)
        )
        self.conn.commit()

    # -- "Sail, Don't Row" — rank/mode tuning ----------------------------------

    # rank, label, difficulty_label, collision_limit, grace_window, par_time_seconds,
    # gust_coverage_pct, obstacle_density, drift_speed (base speed @ t=0),
    # row_speed (unused), sail_speed (gust boost, additive),
    # stamina_drain_per_sec (unused), stamina_regen_per_sec (unused), sort_order,
    # speed_ramp_per_sec (u/sec base speed gains per elapsed second),
    # shark_cruise_distance (u behind the boat while cruising),
    # shark_lunge_interval_sec (seconds between lunges),
    # shark_lunge_speed (u/sec the gap closes at during a lunge),
    # shark_lunge_duration_sec (how long a lunge burst lasts before resetting)
    #
    # Round 2 tuning proposal, calibrated so an average run (accounting for
    # each rank's own gust_coverage_pct) lands close to par_time_seconds over
    # the fixed 4300-unit course: avgSpeedNeeded = COURSE_LENGTH/par_time,
    # avgBase = avgSpeedNeeded - gust_coverage_frac*gust_boost, then
    # base_start + ramp*par_time/2 = avgBase. All four ranks land within ~1.3%
    # of their par time at this math; /admin/game-settings can retune from
    # actual playtesting.
    #
    # Shark params: Deckhand is 0/inert (the shark never spawns there — gated
    # on rank in the game loop, not just tuned to be harmless). Mate/First
    # Mate/Skipper escalate on all three axes — closer cruise distance, more
    # frequent lunges, faster lunges. Chosen so lunge_speed*lunge_duration
    # comfortably exceeds (cruise_distance - catch_gap=12): an unopposed
    # lunge must actually reach catching range, or the hazard can never
    # succeed regardless of tuning knobs elsewhere. A first proposal to
    # retune from actual playtesting, same as everything else here.
    _GAME_RANK_DEFAULTS = [
        ("deckhand",   "Deckhand",   "Easy",   0, 1, 130, 45.0, 3.0, 20.0, 30.0, 8.0, 15.0, 4.0, 0, 0.15, 0.0, 0.0, 0.0, 0.0),
        ("mate",       "Mate",       "Medium", 3, 1, 150, 35.0, 5.0, 15.0, 30.0, 8.0, 15.0, 4.0, 1, 0.15, 60.0, 6.0, 50.0, 1.3),
        ("first_mate", "First Mate", "Hard",   1, 1, 165, 28.0, 7.0, 12.0, 30.0, 8.0, 15.0, 4.0, 2, 0.14, 50.0, 5.0, 58.0, 1.3),
        ("skipper",    "Skipper",    "Expert", 1, 0, 180, 20.0, 9.0, 10.0, 30.0, 8.0, 15.0, 4.0, 3, 0.14, 40.0, 4.0, 68.0, 1.3),
    ]

    def seed_game_rank_settings(self) -> None:
        """Insert the four ranks with the tuning defaults if the table is
        empty. Never overwrites existing rows — once seeded, /admin/game-settings
        owns the values."""
        if self.conn.execute("SELECT 1 FROM game_rank_settings LIMIT 1").fetchone():
            return
        now = _now()
        for row in self._GAME_RANK_DEFAULTS:
            self.conn.execute(
                """INSERT INTO game_rank_settings
                   (rank, label, difficulty_label, collision_limit, grace_window,
                    par_time_seconds, gust_coverage_pct, obstacle_density,
                    drift_speed, row_speed, sail_speed,
                    stamina_drain_per_sec, stamina_regen_per_sec, sort_order,
                    speed_ramp_per_sec, shark_cruise_distance,
                    shark_lunge_interval_sec, shark_lunge_speed,
                    shark_lunge_duration_sec, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                row + (now,),
            )
        self.conn.commit()

    def list_game_rank_settings(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM game_rank_settings ORDER BY sort_order"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_game_rank_settings(self, rank: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM game_rank_settings WHERE rank=?", (rank,)
        ).fetchone()
        return dict(row) if row else None

    def update_game_rank_settings(self, rank: str, **fields) -> None:
        """Update any subset of the tuning columns for one rank. Raises
        ValueError for an unknown rank or an unknown field name."""
        if not self.get_game_rank_settings(rank):
            raise ValueError(f'Unknown rank "{rank}".')
        allowed = {
            "label", "difficulty_label", "collision_limit", "grace_window",
            "par_time_seconds", "gust_coverage_pct", "obstacle_density",
            "drift_speed", "sail_speed", "speed_ramp_per_sec",
            "shark_cruise_distance", "shark_lunge_interval_sec",
            "shark_lunge_speed", "shark_lunge_duration_sec",
        }
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"Unknown setting(s): {', '.join(sorted(bad))}")
        if not fields:
            return
        set_clause = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(
            f"UPDATE game_rank_settings SET {set_clause}, updated_at=? WHERE rank=?",
            list(fields.values()) + [_now(), rank],
        )
        self.conn.commit()

    # -- "Sail, Don't Row" — leaderboard (one combined list) ----------------

    def record_game_run(self, user_id: int, rank: str, score: int, distance_fraction: float,
                        finished: bool, time_seconds: float, hits: int,
                        course_week: str, difficulty_index: int, difficulty_label: str) -> int:
        cur = self.conn.execute(
            """INSERT INTO game_runs
               (user_id, rank, score, distance_fraction, finished, time_seconds,
                efficiency_pct, hits, course_week, difficulty_index, difficulty_label, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (user_id, rank, score, distance_fraction, int(finished), time_seconds,
             0.0, hits, course_week, difficulty_index, difficulty_label, _now()),
        )
        self.conn.commit()
        return cur.lastrowid

    def list_game_leaderboard(self, rank: str | None = None, course_week: str | None = None,
                              limit: int = 50) -> list[dict]:
        """Top runs, best Score first, one row per player — their single best
        run, not every attempt. Combined across all ranks by default (Round 2:
        one board, each row tagged with a rank badge, not per-rank tables) —
        pass `rank` to filter to one rank. Pass course_week for the 'this
        week' view; omit for all-time."""
        where = "WHERE 1=1"
        params: list = []
        if rank:
            where += " AND gr.rank = ?"
            params.append(rank)
        if course_week:
            where += " AND gr.course_week = ?"
            params.append(course_week)
        rows = self.conn.execute(
            f"""SELECT * FROM (
                   SELECT gr.*, u.username, u.name,
                          ROW_NUMBER() OVER (
                            PARTITION BY gr.user_id
                            ORDER BY gr.score DESC, gr.created_at ASC
                          ) AS rn
                   FROM game_runs gr LEFT JOIN users u ON u.id = gr.user_id
                   {where}
                 )
                 WHERE rn = 1
                 ORDER BY score DESC, created_at ASC
                 LIMIT ?""",
            params + [limit],
        ).fetchall()
        return [dict(r) for r in rows]

    # -- archive audit log ---------------------------------------------------

    def record_archive_audit(self, admin_id: Optional[int], action: str,
                             item_id: Optional[int] = None, detail: str = "") -> int:
        """Log one admin add/edit/delete on the Archive. `item_id` is the
        affected articles.id, or None for a bulk operation spanning many rows
        (detail then carries a summary of the change)."""
        cur = self.conn.execute(
            "INSERT INTO archive_audit_log (admin_id, action, item_id, detail, created_at) "
            "VALUES (?,?,?,?,?)",
            (admin_id, action, item_id, detail, _now()),
        )
        self.conn.commit()
        return cur.lastrowid

    def list_archive_audit_log(self, limit: int = 500) -> list[dict]:
        rows = self.conn.execute(
            """SELECT a.*, u.username AS admin_username, u.name AS admin_name
               FROM archive_audit_log a LEFT JOIN users u ON u.id = a.admin_id
               ORDER BY a.created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        self.conn.close()


@contextmanager
def open_library(path: str = DEFAULT_DB_PATH) -> Iterator[Library]:
    lib = Library(path)
    try:
        yield lib
    finally:
        lib.close()
