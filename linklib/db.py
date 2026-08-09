"""SQLite + FTS5 storage layer for the link library.

This is the durable spine. Everything else (Feedly import, going-forward
capture, web UI, Claude access) reads and writes through here.

The same schema works whether the DB is a local file or a hosted
libSQL/Turso/D1 database later — only the connection changes.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterator, Optional


def resolve_db_path(cli_db: Optional[str], *, allow_missing: bool = False) -> str:
    """Resolve the database path for a one-off script, and refuse to guess.

    Precedence: an explicit --db value, then the LINKLIB_DB env var. With
    neither, exit loudly rather than silently falling back to a relative
    "library.db" that resolves against whatever the current directory
    happens to be — that silent fallback is exactly the bug that let an
    earlier admin fix (Corpay's category, July 2026) write to nowhere real
    without a single error, while production stayed untouched.

    Also refuses to let sqlite3 silently create an empty database file at
    the resolved path: unless allow_missing=True (only for scripts that are
    deliberately initializing a database for the first time, e.g.
    import_archive.py), a missing file is treated as a resolved-to-the-
    wrong-place error, not a fresh start.

    Prints the resolved absolute path so it's visible in the script's
    output, not just implied.
    """
    path = cli_db or os.environ.get("LINKLIB_DB")
    if not path:
        sys.exit(
            "No database path given — refusing to guess.\n"
            "Pass --db /path/to/library.db, or set LINKLIB_DB, e.g.:\n"
            "  LINKLIB_DB=/data/library.db python -m scripts.<name> ...\n"
            "(A silent relative-path fallback here is what let a past fix "
            "land nowhere without an error — see CLAUDE.md.)"
        )
    abs_path = os.path.abspath(path)
    if not allow_missing and not os.path.exists(abs_path):
        sys.exit(
            f"Database file not found at {abs_path}\n"
            "Refusing to let sqlite3 silently create an empty database "
            "here — double-check --db / LINKLIB_DB point at the real file."
        )
    print(f"Using database: {abs_path}")
    return abs_path

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
-- purely derived from usage. sort_order is insertion order only; display order
-- is always alphabetical, applied at render time in list_tool_categories().
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

-- Deletion audit trail for Software entries (the seed-reappearance
-- investigation). Same shape as archive_audit_log/contact_audit_log below
-- (admin_id/action/item_id/detail/created_at), kept as its own table for the
-- same reason contact_audit_log is separate from archive_audit_log: item_id
-- would be ambiguous about which table it references if these were merged.
-- action is always 'delete' for now but distinguishes context in practice —
-- the single-row admin Delete button, a bulk delete, a pending-submission
-- Reject, and a name-duplicate merge's "delete the loser" step all route
-- through Library.delete_tool, so the audit write lives inside that method
-- (not at each call site, unlike archive/contact audit) to guarantee no
-- caller can add a new delete path and forget to log it. Since this is a
-- hard delete (no deleted_at column on tools — see delete_tool), the row
-- itself is gone after this fires, so detail carries a name/url/categories
-- snapshot taken immediately before the DELETE — the only record of what
-- was removed.
CREATE TABLE IF NOT EXISTS tool_audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id   INTEGER,
    action     TEXT NOT NULL,             -- 'delete' | 'reject' | 'merge'
    item_id    INTEGER,                    -- the deleted tools.id; row no longer exists
    detail     TEXT NOT NULL DEFAULT '',   -- name/url/categories snapshot at deletion time
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tool_audit_admin ON tool_audit_log(admin_id);
CREATE INDEX IF NOT EXISTS idx_tool_audit_created ON tool_audit_log(created_at);

-- Manually curated competitor cross-links between Software entries (search
-- overhaul Phase 3). One undirected edge per pair, normalized so tool_id is
-- always the smaller id (see Library.add_tool_competitor) — that's what
-- UNIQUE(tool_id, competitor_id) dedupes against, and it means curating the
-- relationship from either tool's admin edit page is enough for it to show
-- up on both profiles; callers looking up "competitors of X" query
-- `WHERE tool_id=X OR competitor_id=X`. Source of truth is this table, not
-- a live tag-overlap computation — see suggest_tool_competitors for the
-- tag-overlap helper, which only powers an admin-UI suggestion list to
-- speed up curation.
CREATE TABLE IF NOT EXISTS tool_competitors (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_id       INTEGER NOT NULL,
    competitor_id INTEGER NOT NULL,
    created_at    TEXT NOT NULL,
    UNIQUE(tool_id, competitor_id)
);

CREATE INDEX IF NOT EXISTS idx_tool_competitors_tool ON tool_competitors(tool_id);
CREATE INDEX IF NOT EXISTS idx_tool_competitors_competitor ON tool_competitors(competitor_id);

-- Per-feature standalone-vs-bundled availability for a Software entry
-- (search overhaul Phase 4a) — the data the Phase 5 comparison matrix reads.
-- standalone_available/bundled_only are independent booleans, not mutually
-- exclusive: some vendors sell a feature both a la carte and folded into a
-- higher tier, so a row can legitimately be 1/1. needs_verification reuses
-- the exact confidence-flag shape from linklib.enrich's community-listing
-- autofill (NEEDS_VERIFICATION) rather than a new mechanism — here it's a
-- per-row bool (not per-field) since one row is already one semantic unit
-- (a feature name + its availability). Manually admin-entered rows default
-- needs_verification=0 (a human typed it); rows from the Phase 4b LLM
-- enrichment pass default it to 1 and get reviewed before Phase 5 treats
-- them as reliable. No DB-level uniqueness on (tool_id, feature_name) —
-- LLM-drafted names won't always match casing/phrasing exactly on a re-run,
-- so de-duplication is the batch script's job, not a constraint here.
-- Enrichment cost is recorded through the existing generic enrichment_cost
-- ledger (article_id=NULL), the same pattern generate_tool_description and
-- generate_community_profile already use — no new cost table needed.
CREATE TABLE IF NOT EXISTS tool_features (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_id              INTEGER NOT NULL,
    feature_name         TEXT NOT NULL,
    standalone_available INTEGER NOT NULL DEFAULT 0,
    bundled_only         INTEGER NOT NULL DEFAULT 0,
    notes                TEXT NOT NULL DEFAULT '',
    source_url           TEXT NOT NULL DEFAULT '',
    needs_verification   INTEGER NOT NULL DEFAULT 0,
    source               TEXT NOT NULL DEFAULT 'manual',
    model                TEXT NOT NULL DEFAULT '',
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_tool_features_tool ON tool_features(tool_id);

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
-- One row per turn in a conversation, grouped by conversation_id. A follow-up
-- turn can involve TWO API calls — the retrieval query-rewrite (Haiku) and the
-- answer itself — folded into the same row: cost_usd is the turn TOTAL, so
-- every SUM(cost_usd) (monthly cap, reports) needs no special handling, and
-- the rewrite_* columns break out the rewrite's share. The plain token columns
-- cover the answer call only. Real cost is computed from actual token usage
-- at call time (see linklib.pricing) — never an estimate.
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
    cost_usd              REAL NOT NULL DEFAULT 0,     -- turn TOTAL: answer call + rewrite call
    rewrite_input_tokens  INTEGER NOT NULL DEFAULT 0,  -- follow-up query-rewrite call; 0 on turn one
    rewrite_output_tokens INTEGER NOT NULL DEFAULT 0,
    rewrite_cost_usd      REAL NOT NULL DEFAULT 0,     -- rewrite's share, already inside cost_usd
    embed_input_tokens    INTEGER NOT NULL DEFAULT 0,  -- query-time embedding call for hybrid retrieval (#93)
    embed_cost_usd        REAL NOT NULL DEFAULT 0,     -- embed's share, already inside cost_usd (user-cap cost,
                                                        -- unlike embed-on-save which is overhead — see article_embeddings)
    hidden_public         INTEGER NOT NULL DEFAULT 0,  -- admin removed from the community view only
    anonymized            INTEGER NOT NULL DEFAULT 0,  -- asker name hidden on the community view only
    citations_json        TEXT NOT NULL DEFAULT '[]',  -- the turn's API-verified cited sources (see record_ask_question)
    created_at            TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ask_questions_user ON ask_questions(user_id);
CREATE INDEX IF NOT EXISTS idx_ask_questions_created ON ask_questions(created_at);
CREATE INDEX IF NOT EXISTS idx_ask_questions_conversation ON ask_questions(conversation_id);
-- NOTE: no index on citations_json (nothing queries it by content). Any future
-- index on it must go in _POST_MIGRATION_INDEXES, never here — on pre-existing
-- DBs the column only arrives via the ALTER TABLE migration loop (see the note
-- above password_reset_requests for the incident this rule comes from).

-- Member feedback on FP&A Buddy answers: one row per rated turn per user,
-- upserted on (question_id, user_id) so a changed rating updates in place
-- rather than stacking rows. question_id -> ask_questions.id by convention
-- (no declared FK, like everywhere else in this schema). Captured for three
-- downstream uses: admin triage (/admin/ask-feedback), a retrieval eval set
-- for the planned semantic-search build (flagged questions plus the
-- citations_json snapshot on the rated ask_questions row), and prompt
-- refinement. Feedback never mutates prompts or retrieval automatically —
-- capture + triage only.
CREATE TABLE IF NOT EXISTS ask_feedback (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id INTEGER NOT NULL,             -- ask_questions.id (the rated turn)
    user_id     INTEGER NOT NULL,             -- who rated (always the turn's asker today)
    rating      TEXT NOT NULL,                -- 'helpful' | 'inaccurate' | 'not_helpful'
    comment     TEXT NOT NULL DEFAULT '',     -- optional "what was off?" free text
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT '',     -- '' until the rating is first changed
    UNIQUE(question_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_ask_feedback_rating  ON ask_feedback(rating);
CREATE INDEX IF NOT EXISTS idx_ask_feedback_created ON ask_feedback(created_at);

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

-- Same audit-trail shape as archive_audit_log above, kept as its own table
-- rather than folded into that one: archive_audit_log's item_id is
-- documented as an articles.id, and mixing contacts.id rows into the same
-- column would make every existing row ambiguous about which table it
-- refers to. action is always 'delete' for now (soft-delete is the only
-- admin action on contacts today) but the column stays for parity/future use.
CREATE TABLE IF NOT EXISTS contact_audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id   INTEGER,
    action     TEXT NOT NULL,             -- 'delete'
    item_id    INTEGER,                    -- contacts.id; NULL for a bulk operation
    detail     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_contact_audit_admin ON contact_audit_log(admin_id);
CREATE INDEX IF NOT EXISTS idx_contact_audit_created ON contact_audit_log(created_at);

-- Overhead cost ledger for embed-on-save + the one-off backfill (#93) — one
-- row per embedded article, upserted by article_id. content_hash is the
-- SHA-256 of the exact text that was embedded (linklib.embeddings.
-- document_text + content_hash); a later edit changes the hash, so the
-- backfill script can detect staleness and re-embed only what actually
-- changed rather than the whole corpus. cost_usd here is Brian's overhead
-- spend — it is never summed into ask_questions and never counts toward a
-- user's monthly Ask cap (see ask_questions.embed_cost_usd for the
-- user-cap-side embedding cost, i.e. embedding the QUESTION at retrieval
-- time, which is a different call). A general ledger covering enrichment
-- spend too now lives in enrichment_cost below (#105).
CREATE TABLE IF NOT EXISTS article_embeddings (
    article_id   INTEGER PRIMARY KEY,
    content_hash TEXT NOT NULL DEFAULT '',
    model        TEXT NOT NULL DEFAULT '',
    input_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd     REAL NOT NULL DEFAULT 0,
    embedded_at  TEXT NOT NULL DEFAULT ''
);

-- Overhead cost ledger for linklib.enrich.enrich() calls (#105 — scoped down
-- from a general ledger once article_embeddings above turned out to be the
-- only real precedent: enrichment needs output_tokens, which embeddings
-- never has, so the two ledgers stay separate rather than sharing a schema).
-- Append-only: unlike article_embeddings, this is NOT upserted by article_id
-- — an article can be enriched more than once (backfill force-reruns, a
-- rules-version bump), and each real call's cost should stay in history
-- rather than overwrite the previous call's row. article_id is NULL for
-- enrichment that happens before a candidate is saved (linklib.queue's
-- pre-save enrichment path) — the API call still cost real money even if
-- the candidate is later dismissed rather than promoted into articles.
-- cost_usd here is Brian's overhead spend, same rule as article_embeddings:
-- never summed into ask_questions, never counts toward a user's Ask cap.
CREATE TABLE IF NOT EXISTS enrichment_cost (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id    INTEGER,
    model         TEXT NOT NULL DEFAULT '',
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd      REAL NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_enrichment_cost_article ON enrichment_cost(article_id);

-- Manual vendor-spend ledger for /admin/overhead-spend "Vendor totals" —
-- one row per real charge (Railway, Cloudflare, Google Workspace, domain
-- registration, Anthropic, OpenAI, Exa, anything else). amount is the actual
-- dollars paid, tax-inclusive, exactly as it hit the card — this table is
-- the sole source for "total cost of the site" precisely because it's typed
-- in from receipts rather than derived, so it always sums correctly and
-- never needs to reconcile against enrichment_cost/article_embeddings/
-- ask_questions (those are a separate, explicitly-not-summed-together
-- "Toolbox usage" view of internal cost attribution — see
-- overhead_cost_breakdown). category is a display/filter tag only (e.g.
-- "Infrastructure" / "AI & API" / "Other") — never used to compute a
-- subtotal, just to filter the list view.
CREATE TABLE IF NOT EXISTS manual_overhead (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    vendor     TEXT NOT NULL,
    date       TEXT NOT NULL,               -- ISO date (YYYY-MM-DD), user-entered from the receipt
    amount     REAL NOT NULL,
    category   TEXT NOT NULL DEFAULT '',
    note       TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_manual_overhead_date ON manual_overhead(date);

-- CFO Toolbox "Communities" directory (/tools/communities), a sibling of
-- tools/tool_categories above: peer groups, associations, and Slack
-- communities rather than software vendors. cost_band is one of five fixed
-- values ('Free', 'Undisclosed dues', '<$1k/yr', '<$2,500/yr', '$2,500+/yr')
-- bucketed by the individual/base rate — exact dues go stale (AFP alone moved
-- $495->$545 in Jan 2026), so a band is the durable fact and cost_note is
-- free text for anything more specific (e.g. a multi-seat corporate rate).
-- sponsorship_type is 'Independent'|'Vendor-sponsored'|'Investor-sponsored'.
-- The legacy `region` free-text column (superseded by reach/metros_json,
-- added by migration below) was dropped entirely once confirmed unused for
-- any public filtering/display — see the DROP COLUMN migration below.
CREATE TABLE IF NOT EXISTS communities (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT NOT NULL DEFAULT '',
    slug             TEXT NOT NULL UNIQUE DEFAULT '',
    url              TEXT NOT NULL DEFAULT '',
    demographic      TEXT NOT NULL DEFAULT '',
    cost_band        TEXT NOT NULL DEFAULT 'Undisclosed dues',
    cost_note        TEXT NOT NULL DEFAULT '',
    sponsorship_type TEXT NOT NULL DEFAULT 'Independent',
    sponsor_name     TEXT NOT NULL DEFAULT '',
    access           TEXT NOT NULL DEFAULT '',
    format           TEXT NOT NULL DEFAULT '',
    notes            TEXT NOT NULL DEFAULT '',
    categories_json  TEXT NOT NULL DEFAULT '[]',
    approved         INTEGER NOT NULL DEFAULT 0,
    submitted_by     TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_communities_approved ON communities(approved);

-- Deletion audit trail for Communities entries — exact structural mirror of
-- tool_audit_log above (same reasoning: hard delete with no deleted_at
-- column, audit write lives inside Library.delete_community so every call
-- site — single-row delete, bulk delete, pending-submission reject — is
-- covered without relying on each route to remember to log it).
CREATE TABLE IF NOT EXISTS community_audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id   INTEGER,
    action     TEXT NOT NULL,             -- 'delete' | 'reject'
    item_id    INTEGER,                    -- the deleted communities.id; row no longer exists
    detail     TEXT NOT NULL DEFAULT '',   -- name/url/categories snapshot at deletion time
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_community_audit_admin ON community_audit_log(admin_id);
CREATE INDEX IF NOT EXISTS idx_community_audit_created ON community_audit_log(created_at);

-- The controlled vocabulary of category pills shown on /tools/communities,
-- same shape and same reasoning as tool_categories above: independent of
-- which communities currently use them.
CREATE TABLE IF NOT EXISTS community_categories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    sort_order  INTEGER NOT NULL DEFAULT 0
);

-- Manually curated "similar communities" cross-links, mirroring
-- tool_competitors exactly (same normalized-pair-with-smaller-id-first
-- shape, same UNIQUE constraint, same OR-both-sides lookup pattern) — see
-- the tool_competitors CREATE TABLE comment above for the full reasoning.
-- A separate table rather than a shared/polymorphic one, matching this
-- codebase's convention of keeping Software and Communities schema/routes
-- independent throughout.
CREATE TABLE IF NOT EXISTS community_competitors (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    community_id  INTEGER NOT NULL,
    competitor_id INTEGER NOT NULL,
    created_at    TEXT NOT NULL,
    UNIQUE(community_id, competitor_id)
);

CREATE INDEX IF NOT EXISTS idx_community_competitors_community ON community_competitors(community_id);
CREATE INDEX IF NOT EXISTS idx_community_competitors_competitor ON community_competitors(competitor_id);

-- Deep, opinionated read on a community (Community Profiles, Phase 2): the
-- qualitative judgment calls a directory row's cost/access/region fields can't
-- carry. 1:1 with communities via community_id as the PRIMARY KEY, mirroring
-- article_embeddings.article_id rather than a SQL-level FK — this codebase
-- doesn't declare REFERENCES anywhere, so cleanup on delete is manual (see
-- delete_community) same as delete_article does for article_embeddings.
-- sponsor_relationship_note is deliberately separate from communities'
-- sponsor_name/sponsorship_type — those are factual, this is a qualitative
-- read on whether the sponsor presence feels value-add or a sales funnel.
-- Drafted via POST /admin/tools/communities/generate-profile
-- (linklib/enrich.py::generate_community_profile), mirroring
-- generate_tool_description's contract exactly: never auto-saved, and
-- low_confidence flags a draft made without a successful page fetch so
-- whoever reviews it knows to double-check facts. Rows can be empty/thin
-- until Research content is available and backfilled, or an admin generates
-- one per-community via that button.
CREATE TABLE IF NOT EXISTS community_profiles (
    community_id             INTEGER PRIMARY KEY,
    ideal_member              TEXT NOT NULL DEFAULT '',
    anti_fit                  TEXT NOT NULL DEFAULT '',
    value_prop                TEXT NOT NULL DEFAULT '',
    format_reality            TEXT NOT NULL DEFAULT '',
    engagement_level          TEXT NOT NULL DEFAULT '',
    sponsor_relationship_note TEXT NOT NULL DEFAULT '',
    application_friction      TEXT NOT NULL DEFAULT '',
    cost_value_verdict        TEXT NOT NULL DEFAULT '',
    notable_members           TEXT NOT NULL DEFAULT '',
    founded_year              INTEGER,
    public_criticism          TEXT NOT NULL DEFAULT '',
    verdict_summary           TEXT NOT NULL DEFAULT '',
    low_confidence            INTEGER NOT NULL DEFAULT 0,
    updated_at                TEXT NOT NULL DEFAULT ''
);

-- Review-status audit trail for every AI-drafted field on Software/Communities
-- profiles (standing principle: AI drafts a first pass into the edit form,
-- nothing publishes without Brian reviewing and saving it — this table is
-- what makes that a real, auditable gate rather than an assumption). One
-- generic table rather than a {field}_reviewed_at/_by column pair per field
-- — with 15+ generatable fields across two record types and more likely to
-- come later (e.g. Phase 8 features), a fixed-shape table scales without a
-- migration every time a new generatable field is added.
-- reviewed_by is stored even though there's only ever one admin (Brian)
-- today, so the schema doesn't need revisiting if that ever changes.
-- Written by Library.record_field_review, called from an edit-submit route
-- whenever the submitted form explicitly flags a field as AI-drafted this
-- editing session (see the ai_drafted_fields hidden input convention in the
-- generate-button JS) — never inferred from content alone, since there's no
-- reliable way to tell "hand-typed" from "AI draft the admin approved as-is"
-- after the fact.
CREATE TABLE IF NOT EXISTS field_reviews (
    entity_type TEXT NOT NULL,
    entity_id   INTEGER NOT NULL,
    field_name  TEXT NOT NULL,
    reviewed_at TEXT NOT NULL,
    reviewed_by TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (entity_type, entity_id, field_name)
);

-- Gap-collection (Phase 5): the native replacement for the old /community
-- waitlist page's Google Form, folded into the live directory instead of a
-- separate parked page. current_communities/gaps/looking_for are the
-- visitor's own words (no controlled vocabulary — this is qualitative
-- signal for Brian, not a filter). search_context_json is a best-effort
-- snapshot of the directory search/filter state at submission time, built
-- client-side and carried through as a hidden form field (the filters
-- themselves are pure client-side JS state on /tools/communities, never
-- posted to the server otherwise) — '' when the visitor arrived via a
-- profile page's mini-CTA rather than the directory's bottom-of-page CTA.
-- viewed_community_ids_json is populated server-side at submission time
-- from community_profile_views (see below), not trusted from the client.
-- closest_community_id has no SQL REFERENCES, same as community_profiles
-- above — nullable, so "none in particular" is representable.
-- submission_type (added by migration, see Library.__init__) distinguishes
-- this row shape from a Recommender quiz completion (Phase 7) or a
-- profile-page correction report: 'gap' is every field above, used as
-- documented; 'recommender' reuses search_context_json for the quiz
-- answers + result count instead (leaving current_communities/gaps/
-- looking_for '' since the quiz collects no free text), and 'correction'
-- reuses just gaps (the free-text report) and closest_community_id (the
-- community being corrected), leaving current_communities/looking_for/
-- search_context_json empty since a correction isn't about a directory
-- search.
CREATE TABLE IF NOT EXISTS community_gap_submissions (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    current_communities       TEXT NOT NULL DEFAULT '',
    gaps                      TEXT NOT NULL DEFAULT '',
    looking_for               TEXT NOT NULL DEFAULT '',
    search_context_json       TEXT NOT NULL DEFAULT '',
    viewed_community_ids_json TEXT NOT NULL DEFAULT '[]',
    closest_community_id      INTEGER,
    email                     TEXT NOT NULL DEFAULT '',
    reviewed                  INTEGER NOT NULL DEFAULT 0,
    created_at                TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_community_gap_submissions_reviewed
    ON community_gap_submissions(reviewed);

-- Session-scoped view tracking, no login required: which community profile
-- pages a visitor opened before (maybe) submitting the gap form. Keyed by an
-- anonymous cookie (cfo_visitor in webapp/app.py), not a users.id — this is
-- the first anonymous-session primitive in the codebase (everything else,
-- e.g. read_later, requires a logged-in user_id). PRIMARY KEY on
-- (session_id, community_id) dedups repeat views of the same profile;
-- INSERT OR REPLACE refreshes viewed_at on a re-view rather than growing
-- unbounded. No cleanup job yet for stale sessions — rows are small
-- (two ints + a timestamp) and carry no PII, so this is deferred rather
-- than solved here.
CREATE TABLE IF NOT EXISTS community_profile_views (
    session_id   TEXT NOT NULL,
    community_id INTEGER NOT NULL,
    viewed_at    TEXT NOT NULL,
    PRIMARY KEY (session_id, community_id)
);

-- Chat Matchmaker (linklib/matchmaker.py): one row per turn in a
-- conversational matchmaker session, at /tools/communities/find (and, in a
-- later phase, a Software equivalent) — a free-type chat that narrows to 2-3
-- best-fit suggestions, replacing the old quiz-based Recommender. Deliberately
-- its own table rather than folded into ask_questions: FP&A Buddy and the
-- matchmaker(s) should track spend against independent monthly dollar caps
-- (see users.matchmaker_cap_usd / settings['matchmaker_default_cap_usd']),
-- since a matching conversation can run more back-and-forth turns than a
-- typical FP&A Buddy question even though each turn is individually cheaper
-- (no retrieval, no web search, no citations — just the full structured
-- profile dataset as context). `kind` lets Communities and a future Software
-- matchmaker share this one table/cap rather than forking the mechanism per
-- kind. No feedback table — thumbs up/down here is UI-only, session-scoped,
-- never persisted (see CLAUDE.md's matchmaker feedback decision).
--
-- Unlike ask_questions, `user_id` is nullable: /tools/communities/find (like
-- the quiz it replaces) is a PUBLIC page, no login required, so most rows
-- have no signed-in user. `session_id` (the same anonymous cfo_visitor
-- cookie already used by community_profile_views) is the rate-limit and
-- conversation-continuity key for those anonymous rows; a signed-in row
-- carries both (session_id is always set — the cookie predates login state
-- — but user_id + get_effective_matchmaker_cap is what actually governs a
-- logged-in user's cap, mirroring ask_questions/Ask exactly).
CREATE TABLE IF NOT EXISTS matchmaker_questions (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    kind                  TEXT NOT NULL DEFAULT 'community',  -- 'community' | 'software'
    conversation_id       TEXT NOT NULL DEFAULT '',   -- groups turns; = str(id) of the first turn
    turn_index            INTEGER NOT NULL DEFAULT 0,
    user_id               INTEGER,                    -- NULL for anonymous (public page, no login)
    session_id            TEXT NOT NULL DEFAULT '',    -- cfo_visitor cookie value
    question              TEXT NOT NULL DEFAULT '',
    answer                TEXT NOT NULL DEFAULT '',
    model                 TEXT NOT NULL DEFAULT '',
    input_tokens          INTEGER NOT NULL DEFAULT 0,
    output_tokens         INTEGER NOT NULL DEFAULT 0,
    cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens     INTEGER NOT NULL DEFAULT 0,
    cost_usd              REAL NOT NULL DEFAULT 0,
    created_at            TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_matchmaker_questions_user ON matchmaker_questions(user_id);
CREATE INDEX IF NOT EXISTS idx_matchmaker_questions_session ON matchmaker_questions(session_id);
CREATE INDEX IF NOT EXISTS idx_matchmaker_questions_created ON matchmaker_questions(created_at);
CREATE INDEX IF NOT EXISTS idx_matchmaker_questions_conversation ON matchmaker_questions(conversation_id);

-- Admin verdicts on name-based duplicate candidates in the Software directory
-- (normalize_tool_name() exact-match, not the URL-based dedup above — see
-- normalize_tool_name's docstring). Mirrors dedupe_decisions' pair-verdict
-- shape, with one deliberate change: pair_key is built from the two tools'
-- *ids* (sorted numerically), not their names, so a later name edit (e.g.
-- dropping a "(WiseLayer)" parenthetical) can never re-open a pair a human
-- already resolved. Only resolved pairs are ever written here — candidates
-- are computed live from the current tools table on every view, and this
-- table just says which of those candidate pairs to stop surfacing. Both
-- verdicts suppress future resurfacing; 'duplicate' doesn't itself merge or
-- delete anything, it only flags the pair as "an admin already saw this and
-- is on it" so /admin/tools/name-duplicates stops nagging about it. Deleting
-- one of the two tools (the actual resolution of a 'duplicate' verdict) would
-- otherwise leave its row here permanently orphaned, so delete_tool cascades
-- a cleanup here too — same pattern as its existing tool_competitors delete.
CREATE TABLE IF NOT EXISTS tool_name_dedupe_decisions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    pair_key   TEXT NOT NULL UNIQUE,        -- "min(id_a,id_b) newline max(id_a,id_b)"
    tool_id_a  INTEGER NOT NULL,
    tool_id_b  INTEGER NOT NULL,
    verdict    TEXT NOT NULL DEFAULT 'dismissed',  -- 'duplicate' | 'dismissed'
    created_at TEXT NOT NULL
);
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
    # Root-domain special case: urlsplit gives path == "/" when the URL had a
    # trailing slash and "" when it didn't (e.g. "https://x.io/" vs
    # "https://x.io"), so a plain `len(path) > 1` guard here never strips a
    # lone "/" and the two never converge — a real bug that let root-domain
    # trailing-slash variants (e.g. https://vendor.com vs https://vendor.com/)
    # slip past the PR #197 duplicate-URL check. Collapse "/" to "" before the
    # general strip, which already handles any deeper path correctly.
    path = parts.path
    if path == "/":
        path = ""
    elif path.endswith("/"):
        path = path.rstrip("/")
    return urlunsplit(("https", host, path, urlencode(kept), ""))


_ENTITY_SUFFIXES = {
    "inc", "incorporated", "llc", "l.l.c", "ltd", "limited", "corp",
    "corporation", "co", "company", "plc", "gmbh", "pbc",
}


def normalize_tool_name(name: str) -> str:
    """Canonicalize a Software directory name for exact-match duplicate
    detection: lowercase, drop anything in parentheses (e.g. "(acquired by
    X)"), strip a trailing common entity suffix (Inc/LLC/Corp/Ltd/Co/...),
    and collapse whitespace/punctuation. Deliberately exact-match only — no
    fuzzy/similarity scoring, so spelling variants, spacing, or hyphenation
    differences won't collide here (see the ARCHITECTURE.md note on this
    feature for why that's in scope for a later pass, not this one)."""
    import re
    name = (name or "").strip().lower()
    if not name:
        return ""
    name = re.sub(r"\([^)]*\)", " ", name)
    name = re.sub(r"[^\w\s-]", " ", name)
    words = name.split()
    if words and words[-1].rstrip(".") in _ENTITY_SUFFIXES:
        words = words[:-1]
    return " ".join(words)


# Reserved category name (Phase J3): the CFO Toolbox directories' "Uncategorized"
# filter pill is a client-side sentinel (see webapp/app.py's __uncategorized__
# handling in filtered()/applySortFilter) that OR-matches an empty categories
# list, not a real category row. A real category literally named "Uncategorized"
# would be indistinguishable from that sentinel in the filter UI, so category
# creation/rename rejects the name (case-insensitive) for both tools and
# communities.
RESERVED_CATEGORY_NAME = "uncategorized"


class DuplicateURLError(Exception):
    """Raised by add_tool/update_tool/add_community/update_community when the
    given URL normalize_url()-matches an existing row (a different row, on
    update). Carries the conflicting row so callers can point the admin at it
    instead of just saying "duplicate"."""
    def __init__(self, entry_type: str, entry_id: int, name: str, slug: str = ""):
        self.entry_type = entry_type
        self.entry_id = entry_id
        self.name = name
        self.slug = slug
        super().__init__(f'A {entry_type} with this URL already exists: "{name}" (id={entry_id})')


def _slugify(name: str) -> str:
    import re
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    return slug[:80]


def _slug_host(url: str) -> str:
    """Lowercased hostname with a leading 'www.' stripped, for slug derivation.
    Best-effort — returns '' if the URL can't be parsed or has no host."""
    from urllib.parse import urlsplit
    url = (url or "").strip()
    if not url:
        return ""
    if "://" not in url:
        url = f"//{url}"
    try:
        host = urlsplit(url).hostname or ""
    except Exception:
        return ""
    host = host.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _domain_slug_base(url: str) -> str:
    """Short domain-derived slug: the first label of the bare domain root,
    e.g. https://www.abacum.io -> 'abacum'. Falls back to '' when the URL
    can't be parsed — callers should treat that as "no domain slug available"."""
    import re
    host = _slug_host(url)
    if not host:
        return ""
    label = host.split(".")[0]
    label = re.sub(r"[^a-z0-9]+", "-", label).strip("-")
    return label


def _domain_slug_full(url: str) -> str:
    """Full bare domain with dots replaced by hyphens, e.g. 'abacum.io' ->
    'abacum-io'. Used as the collision fallback when two entries of the same
    type reduce to the same short domain-slug base (see Phase 2 algorithm)."""
    import re
    host = _slug_host(url)
    if not host:
        return ""
    slug = host.replace(".", "-")
    slug = re.sub(r"[^a-z0-9-]+", "-", slug).strip("-")
    return slug


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
    def __init__(self, path: str):
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
            # Soft delete for spam/junk contact submissions — '' means not
            # deleted, same empty-string-sentinel idiom as resolved_at above,
            # rather than a real NULL.
            "ALTER TABLE contacts ADD COLUMN deleted_at TEXT NOT NULL DEFAULT ''",
            # History-aware retrieval: the follow-up query-rewrite call's own
            # usage, folded into the same turn's row. cost_usd remains the
            # single authoritative column to SUM (it's the turn total,
            # rewrite included); these break out the rewrite's share. No
            # index — nothing queries these columns directly.
            "ALTER TABLE ask_questions ADD COLUMN rewrite_input_tokens INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE ask_questions ADD COLUMN rewrite_output_tokens INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE ask_questions ADD COLUMN rewrite_cost_usd REAL NOT NULL DEFAULT 0",
            # Per-turn citation snapshot — the sources an answer ACTUALLY cited,
            # persisted so a flagged answer stays inspectable later (feedback
            # triage, retrieval eval set). Pre-existing turns get '[]' (their
            # citations were only ever sent to the client, never stored).
            "ALTER TABLE ask_questions ADD COLUMN citations_json TEXT NOT NULL DEFAULT '[]'",
            # Hybrid retrieval (#93): embedding the retrieval question is a
            # user-cap cost (unlike embed-on-save, which is overhead — see
            # article_embeddings), so it's folded into cost_usd the same way
            # rewrite_cost_usd is, with these columns breaking out its share.
            "ALTER TABLE ask_questions ADD COLUMN embed_input_tokens INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE ask_questions ADD COLUMN embed_cost_usd REAL NOT NULL DEFAULT 0",
            # Communities geography model: replaces the free-text `region`
            # column with a controlled `reach` value plus a `metros` tag list,
            # so the region filter can match a metro against national/global
            # communities with a local chapter there, not just purely-regional
            # ones. scripts/backfill_community_geo.py is the one-off pass that
            # populated these for the existing corpus.
            "ALTER TABLE communities ADD COLUMN reach TEXT NOT NULL DEFAULT 'National'",
            "ALTER TABLE communities ADD COLUMN metros_json TEXT NOT NULL DEFAULT '[]'",
            # Metros checkbox grid -> free text (Local markets): the fixed
            # 18-city vocabulary was too narrow (couldn't express a chapter in
            # a city not on the list) and blocked entirely on Brian curating
            # new options. `local_markets` replaces it as the admin-owned
            # field going forward; `metros_json` is frozen in place rather
            # than dropped (same no-destructive-migration precedent as the
            # retired *_tags columns below) and backfilled into
            # `local_markets` once by _migrate_community_local_markets, since
            # turning its JSON array into readable text needs Python, not a
            # bare ALTER TABLE.
            "ALTER TABLE communities ADD COLUMN local_markets TEXT NOT NULL DEFAULT ''",
            # Featured pinning, mirroring tools.promoted exactly (coral badge +
            # pin-to-top; independent of the advisor flag below).
            "ALTER TABLE communities ADD COLUMN featured INTEGER NOT NULL DEFAULT 0",
            # Advisor disclosure, mirroring tools.advisor exactly: syncs from
            # scripts/seed_communities.py's COMMUNITIES on every startup (see
            # _seed_toolbox), same as name/notes — not purely admin-owned like
            # featured/reach/local_markets.
            "ALTER TABLE communities ADD COLUMN advisor INTEGER NOT NULL DEFAULT 0",
            # Recommender (Phase 7): distinguishes a quiz submission from a
            # hand-written gap-form submission in the same table rather than a
            # separate one. 'gap' preserves the meaning of every pre-existing
            # row. A recommender row reuses search_context_json for the quiz
            # answers + result count instead of current_communities/gaps/
            # looking_for, which stay '' for that submission_type.
            "ALTER TABLE community_gap_submissions ADD COLUMN submission_type TEXT NOT NULL DEFAULT 'gap'",
            # business_model is deliberately separate from
            # sponsor_relationship_note above: that field judges whether a
            # sponsor's presence feels value-add or a sales funnel, this one
            # describes the structural way the community sustains itself —
            # e.g. a gated, subscription-funded peer group insulated from a
            # sales pitch by design, vs. a wide-funnel free-to-join community
            # monetized via paid tiers, events, or sponsorships. A community
            # can be non-salesy on one axis and a wide-funnel business on the
            # other, so the two judgments are captured independently.
            "ALTER TABLE community_profiles ADD COLUMN business_model TEXT NOT NULL DEFAULT ''",
            # The 8-field backfill (bulk community-profile import): factual/
            # categorical data researched alongside the 13 narrative fields
            # above but arriving later, once these columns existed. Deliberately
            # TEXT rather than a strict boolean/enum — the source research
            # carries qualifiers ("Yes (NASBA-approved sponsor)", "Unclear"),
            # which a bare 0/1 or fixed enum would lose. Unlike the narrative
            # fields, these are short factual/categorical data, not prose, so
            # they're excluded from the bulk import's voice-rewrite pass (see
            # scripts/import_community_profiles.py).
            "ALTER TABLE community_profiles ADD COLUMN primary_purpose TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE community_profiles ADD COLUMN cpe_eligible TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE community_profiles ADD COLUMN platform_type TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE community_profiles ADD COLUMN meeting_format TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE community_profiles ADD COLUMN event_style TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE community_profiles ADD COLUMN seniority_band TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE community_profiles ADD COLUMN resources_included TEXT NOT NULL DEFAULT ''",
            # Marks a profile row as imported/edited but not yet personally
            # reviewed by Brian (bulk community-profile import) — distinct from
            # `communities.approved` (directory visibility): a `needs_review`
            # profile is already live on its public profile page, this flag is
            # purely an admin-side triage signal, same spirit as `approved` on
            # tools/communities submissions but for content review rather than
            # publication.
            "ALTER TABLE community_profiles ADD COLUMN needs_review INTEGER NOT NULL DEFAULT 0",
            # Recommender best-fit weighting (Communities Recommender ranking):
            # a short controlled-vocabulary tag list per dimension, alongside
            # (not replacing) the free-text research columns above — the raw
            # research prose is too inconsistent for reliable keyword/substring
            # matching (see the Phase 0 investigation: e.g. a platform_type of
            # "not a Slack/forum" would false-match a naive "Slack" keyword
            # check). scripts/backfill_community_weight_tags.py is the one-off
            # pass that hand-classifies the existing 38 rows into these tags;
            # new communities get theirs set via the admin profile edit form's
            # checkbox groups (_community_profile_form_fields), same as any
            # other profile field. JSON list of values from the fixed
            # vocabulary in webapp/app.py's _WEIGHT_DIMENSIONS (e.g.
            # seniority_band_tags: ["senior","mixed"]) — a list, not a single
            # value, because a community can genuinely span more than one
            # bucket (e.g. AFP serves "senior" execs and "controller"-level
            # staff and is explicitly "mixed"). cpe_eligible_tags and
            # resources_included_tags are effectively booleans (["yes"] or
            # []) since their quiz checkbox is a single "Yes" option per
            # Phase 0's proposed vocabulary.
            "ALTER TABLE community_profiles ADD COLUMN seniority_band_tags TEXT NOT NULL DEFAULT '[]'",
            "ALTER TABLE community_profiles ADD COLUMN cpe_eligible_tags TEXT NOT NULL DEFAULT '[]'",
            "ALTER TABLE community_profiles ADD COLUMN primary_purpose_tags TEXT NOT NULL DEFAULT '[]'",
            "ALTER TABLE community_profiles ADD COLUMN platform_type_tags TEXT NOT NULL DEFAULT '[]'",
            "ALTER TABLE community_profiles ADD COLUMN meeting_format_tags TEXT NOT NULL DEFAULT '[]'",
            "ALTER TABLE community_profiles ADD COLUMN event_style_tags TEXT NOT NULL DEFAULT '[]'",
            "ALTER TABLE community_profiles ADD COLUMN resources_included_tags TEXT NOT NULL DEFAULT '[]'",
            # Function (Recommender weighting redesign, post-#159 Phase 1): a
            # new dimension splitting the old seniority_band's controller/
            # accounting-focused bucket out into its own axis (Overall finance
            # org / FP&A / Accounting / Treasury), alongside seniority_band
            # narrowing to a pure CFO/Senior-Exec/Open-to-all seniority read.
            # No sibling free-text `function` column — unlike the original 7
            # profile dimensions, this one has no researched narrative field
            # of its own; it's classified straight from ideal_member/
            # value_prop/categories (see scripts/recategorize_level_function.py).
            "ALTER TABLE community_profiles ADD COLUMN function_tags TEXT NOT NULL DEFAULT '[]'",
            # Purpose+Resources merge (Recommender weighting redesign, PR 2):
            # primary_purpose_tags and resources_included_tags retire as
            # weighting dimensions in favor of one merged multi-select,
            # "What you're looking for" (looking_for_tags) — genuine
            # re-derivation, not a relabel: the old primary_purpose vocabulary
            # bundled "peer networking" as one option, this one splits it
            # into Peer discussions vs. Networking, folds in the old
            # Resources included as one of five checkboxes, and adds Vendor
            # connections as a wholly new concept the old vocabulary never
            # captured. The two retired *_tags columns and their free-text
            # siblings stay in the schema (no destructive migration) —
            # they're dropped from _WEIGHT_DIMENSIONS/_WEIGHT_TAG_COLUMNS and
            # from upsert_community_profile's own column list entirely, so
            # whatever backfill_community_weight_tags.py last wrote there
            # stays frozen rather than getting silently zeroed out the next
            # time an admin saves an unrelated profile field.
            "ALTER TABLE community_profiles ADD COLUMN looking_for_tags TEXT NOT NULL DEFAULT '[]'",
            # Programming+Event style merge (Recommender weighting redesign,
            # PR 4): meeting_format_tags and event_style_tags retire in favor
            # of one merged multi-select reusing the "Programming" admin
            # label that used to belong to meeting_format alone — a
            # deliberate repurposing of that label to a new vocabulary, not
            # a naming collision to preserve. New vocabulary: Meals /
            # Conferences / Retreats / Virtual Panels — derived from
            # format_reality (the narrative field describing actual
            # programming), not from meeting_format/event_style, since
            # that's where this level of detail actually lives. "Demo Days"
            # (part of the originally proposed vocabulary) has no supporting
            # text anywhere in the current research corpus and is
            # deliberately left out rather than force-fit — same treatment
            # as the stage_focus placeholder below, a candidate for a future
            # research round. Same retirement pattern as PR 2's
            # looking_for_tags: the two retired *_tags columns and their
            # free-text siblings stay in the schema, but are dropped from
            # _WEIGHT_DIMENSIONS/_WEIGHT_TAG_COLUMNS and from
            # upsert_community_profile's own column list.
            "ALTER TABLE community_profiles ADD COLUMN programming_tags TEXT NOT NULL DEFAULT '[]'",
            # Dues dual-tagging (Recommender weighting redesign, PR 5): the
            # "Dues" dimension (key paid_free, admin_label "Dues") moves from
            # source: "derived" (computed on the fly as a single value —
            # ["free"] if communities.cost_band == 'Free' else ["paid"]) to
            # source: "profile", backed by this new dedicated paid_free_tags
            # column, so a freemium community with both a free tier and a
            # paid tier (e.g. Finance Alliance, GaapSavvy, Startup CFO, CFO
            # Connect) can carry both tags at once — something a single
            # communities.cost_band column can never represent. This is a
            # deliberate divergence from cost_band, not a bug: cost_band
            # stays the single-value directory-listing fact (still shown/
            # edited on the community's own edit form), while
            # paid_free_tags is the weighting-specific, independently
            # editable value that can be dual-tagged. scripts/
            # recategorize_dues.py seeds every community's paid_free_tags
            # from its current cost_band (preserving today's behavior)
            # before applying the known freemium overrides.
            "ALTER TABLE community_profiles ADD COLUMN paid_free_tags TEXT NOT NULL DEFAULT '[]'",
            # Industry (Recommender weighting redesign, PR 6): a new
            # dimension, no free-text sibling column — Life sciences /
            # Healthcare / Private equity/funds / Industry-neutral, drawn
            # from the natural categories that emerged from the
            # "Industry-specific"-tagged communities' research text (see the
            # Phase 0 investigation). Deliberately narrower than that
            # category's own framing (which also mentions nonprofit and
            # tech) — no community in the current 38 is nonprofit-focused
            # (the one candidate was removed via corrections-and-
            # overrides.md's REMOVALS before this Recommender build even
            # started), and "tech" overlaps the stage_focus placeholder's
            # in-flight Research Round 4 work, so both are left out rather
            # than added as options with zero or contested matches.
            "ALTER TABLE community_profiles ADD COLUMN industry_tags TEXT NOT NULL DEFAULT '[]'",
            # Three placeholder factual/categorical columns, same pattern as
            # business_model when it was first added: nullable/empty-default,
            # visible in the admin edit form and the generate-profile-draft
            # prompt, but empty until a future research round backfills them.
            # Not part of the Recommender's quiz weighting yet — there's no
            # data to weight on until Research fills these in.
            "ALTER TABLE community_profiles ADD COLUMN stage_focus TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE community_profiles ADD COLUMN jobs_program TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE community_profiles ADD COLUMN team_or_individual TEXT NOT NULL DEFAULT ''",
            # `region` (legacy free-text note, superseded by reach/metros_json
            # above) confirmed unused for any public filtering/display —
            # dropped entirely rather than left as dead weight. A no-op
            # OperationalError (caught below) on every boot after the first.
            "ALTER TABLE communities DROP COLUMN region",
            # Competitor cross-links (Phase 3): the free-text "how this
            # differs from the competition" slot on a Software profile page.
            # Placeholder UI only — no generator drafts this, it's hand-
            # written by Brian, same as the rest of a tool's description.
            "ALTER TABLE tools ADD COLUMN differentiation_note TEXT NOT NULL DEFAULT ''",
            # Agent taxonomy (Phase 0 decision, never actually shipped in
            # Phases 1-4 — added here in Phase 5 since the comparison matrix
            # is the first thing that needs it rendered): descriptive copy on
            # where a tool sits on the standalone-feature vs. agent-assisted
            # vs. fully-independent-agent spectrum. Deliberately free text,
            # not a structured/enum field — see the Phase 0 notes. Included
            # in the public directory's client-side search string
            # (ALL_TOOLS in tools_directory) so it's searchable, not just
            # decorative, same requirement as everything else on that page.
            "ALTER TABLE tools ADD COLUMN agent_taxonomy_note TEXT NOT NULL DEFAULT ''",
            # Profile-page screenshot (Phase 5 follow-up). Two provenances
            # write to the same fields: a manually pasted external URL
            # (update_tool_screenshot — clears screenshot_captured_at, since
            # we don't know when a hand-pasted image was taken), or an
            # automated homepage capture (set_tool_screenshot_capture —
            # scripts/capture_tool_screenshots.py's bulk backfill, or the
            # live "Recapture" admin button; both go through
            # linklib.screenshots.capture_homepage so every captured shot
            # uses the same fixed viewport). screenshot_is_product
            # distinguishes an actual product UI shot from a homepage-only
            # capture, so the profile page can caption honestly rather than
            # imply a homepage grab is the product; automated captures are
            # always homepage-only by design, so they never set this flag.
            "ALTER TABLE tools ADD COLUMN screenshot_url TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE tools ADD COLUMN screenshot_is_product INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE tools ADD COLUMN screenshot_captured_at TEXT NOT NULL DEFAULT ''",
            # Automated agent-taxonomy research follow-up: agent_taxonomy_note
            # can now be LLM-drafted (generate_tool_features, extended to
            # return an agent_taxonomy summary alongside feature rows) as well
            # as hand-typed, so it needs the same needs_verification tracking
            # tool_features rows already have. Defaults to 0 (verified) so
            # existing hand-typed notes aren't retroactively flagged.
            "ALTER TABLE tools ADD COLUMN agent_taxonomy_needs_verification INTEGER NOT NULL DEFAULT 0",
            # Description-length follow-up: `description` grows to a full
            # ~8-12 sentence profile-page write-up; `summary` is the short
            # 2-3 sentence version for the directory card and client-side
            # search on /tools/software — a proper condensed rewrite, not
            # truncated description text. Backfilled below from the existing
            # (already-short) description for every pre-existing row, so
            # cards keep showing something sensible until re-enriched.
            "ALTER TABLE tools ADD COLUMN summary TEXT NOT NULL DEFAULT ''",
            # Chat Matchmaker (replaces the quiz-based Recommender at
            # /tools/communities/find): a separate dollar-cap column from
            # ask_cap_usd, same NULL-inherits-the-default shape, so FP&A
            # Buddy and the matchmaker(s) track spend independently rather
            # than competing for one budget — see matchmaker_questions below.
            "ALTER TABLE users ADD COLUMN matchmaker_cap_usd REAL",
            # Recommender weighting *_tags columns dropped entirely: the quiz
            # they existed for is gone (replaced by the Chat Matchmaker, which
            # reads the free-text columns of the same base name directly, no
            # controlled vocabulary needed) and nothing else ever read them —
            # confirmed unused, then removed by Brian's explicit call rather
            # than left as dead weight (same DROP COLUMN precedent as
            # `communities.region` above). Takes the four already-retired
            # columns (primary_purpose_tags, resources_included_tags,
            # meeting_format_tags, event_style_tags — dead even before this)
            # along with the eight that were still live until now.
            "ALTER TABLE community_profiles DROP COLUMN seniority_band_tags",
            "ALTER TABLE community_profiles DROP COLUMN cpe_eligible_tags",
            "ALTER TABLE community_profiles DROP COLUMN platform_type_tags",
            "ALTER TABLE community_profiles DROP COLUMN function_tags",
            "ALTER TABLE community_profiles DROP COLUMN looking_for_tags",
            "ALTER TABLE community_profiles DROP COLUMN programming_tags",
            "ALTER TABLE community_profiles DROP COLUMN paid_free_tags",
            "ALTER TABLE community_profiles DROP COLUMN industry_tags",
            "ALTER TABLE community_profiles DROP COLUMN primary_purpose_tags",
            "ALTER TABLE community_profiles DROP COLUMN resources_included_tags",
            "ALTER TABLE community_profiles DROP COLUMN meeting_format_tags",
            "ALTER TABLE community_profiles DROP COLUMN event_style_tags",
            # Profile-page screenshot (Phase 3b) — built from scratch for
            # Communities, mirroring tools.screenshot_url/_is_product/
            # _captured_at exactly (see the tools ALTER TABLE block above for
            # the full reasoning: two write paths, manual paste vs. automated
            # homepage capture via linklib.screenshots.capture_homepage,
            # sharing these three columns).
            "ALTER TABLE communities ADD COLUMN screenshot_url TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE communities ADD COLUMN screenshot_is_product INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE communities ADD COLUMN screenshot_captured_at TEXT NOT NULL DEFAULT ''",
            # Company logo backfill (Phase D). Stores a relative path, e.g.
            # "logos/tools/abacum.svg", to a locally-downloaded logo asset —
            # never a hotlinked external URL, since scripts/backfill_logos.py
            # downloads and stores the asset itself rather than pointing at
            # Brandfetch's CDN long-term (see that script's docstring for why:
            # Brandfetch's free CDN Logo API is browser-embed-only and
            # disallows programmatic access; the Brand API used here is a
            # real, storable JSON+asset response). The path is relative to a
            # "logos/" directory next to library.db (same Railway volume as
            # tool_screenshots/community_screenshots — see backfill_logos.py's
            # docstring for why NOT webapp/static/, despite the original Phase
            # D investigation assuming that), not to webapp/static/ itself —
            # Phase F picks the actual serving route later, same as the
            # screenshot precedent's dedicated GET route. ''  = no logo yet.
            # set_tool_logo/set_community_logo are the only writers, and the
            # backfill script's selection query only ever targets rows where
            # this is still empty — a manually uploaded logo (future admin UI,
            # Phase F) is never silently overwritten by a re-run.
            "ALTER TABLE tools ADD COLUMN logo_path TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE communities ADD COLUMN logo_path TEXT NOT NULL DEFAULT ''",
            # Dual screenshot capture (Phase E): a second, independent
            # screenshot slot for the actual product/app UI, alongside the
            # existing homepage slot above. screenshot_is_product is retired
            # by this phase (see migrate_app_screenshot_from_product_flag
            # below — a manual-trigger data migration, not an automatic
            # boot one; see scripts/migrate_app_screenshot_from_product_flag.py)
            # but deliberately NOT dropped — same non-destructive
            # precedent as the retired community_profiles *_tags columns:
            # the column stays in place, frozen, as a historical marker of
            # which pre-Phase-E rows were manually flagged as a product
            # shot, in case that's ever needed to reconstruct/audit the
            # one-time migration. Naming deliberately does NOT mirror
            # screenshot_url/screenshot_captured_at 1:1: the homepage slot
            # has no separate "source URL" column because it always reuses
            # the tool's/community's own `url` field as the capture target.
            # The app slot has no such built-in source (there's no single
            # "the app's URL" the way there's a homepage URL), so it needs
            # its own field to hold whatever login/demo/product-tour URL
            # Brian supplies. app_screenshot_url mirrors screenshot_url
            # exactly (the served path, populated by either auto-capture or
            # a manual crop-and-upload — same field either way, no
            # provenance tracking, mirroring how screenshot_url doesn't
            # distinguish its own two write paths). app_screenshot_captured_at
            # mirrors screenshot_captured_at exactly.
            "ALTER TABLE tools ADD COLUMN app_screenshot_source_url TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE tools ADD COLUMN app_screenshot_url TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE tools ADD COLUMN app_screenshot_captured_at TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE communities ADD COLUMN app_screenshot_source_url TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE communities ADD COLUMN app_screenshot_url TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE communities ADD COLUMN app_screenshot_captured_at TEXT NOT NULL DEFAULT ''",
        ]:
            try:
                self.conn.execute(_col_sql)
                self.conn.commit()
            except sqlite3.OperationalError:
                pass
        # Idempotent: only touches rows where summary is still empty, so a
        # row that later gets a real generated (or hand-written) summary is
        # never overwritten by a re-run of this backfill on a later boot.
        self.conn.execute("UPDATE tools SET summary=description WHERE summary='' AND description!=''")
        self.conn.commit()
        # read_later predates per-user scoping (no user_id column, UNIQUE(url)
        # inline constraint) — a plain ALTER TABLE ADD COLUMN can't fix the
        # uniqueness half of that, so it gets its own table-recreation
        # migration rather than a line in the loop above. Must run before
        # _POST_MIGRATION_INDEXES, which assumes user_id already exists.
        self._migrate_read_later_user_scope()
        self._migrate_community_local_markets()
        # migrate_app_screenshot_from_product_flag is deliberately NOT called
        # here, unlike the two migrations above — it's a one-time DATA
        # migration touching pre-existing production rows (moving a legacy
        # screenshot_is_product=1 row's screenshot into the new app slot),
        # not a schema/column backfill. The standing rule (CLAUDE.md, "One-
        # off admin fixes against the database") requires Brian's explicit
        # review of a production data write before it happens, not after —
        # an automatic boot hook would fire the moment this deploys, before
        # anyone has seen the affected-row count. Run it by hand instead via
        # scripts/migrate_app_screenshot_from_product_flag.py (preview by
        # default, --apply to actually write, same convention as
        # scripts/backfill_logos.py). See that script's docstring.
        # Indexes on any column added by the ALTER TABLE loop above must be
        # created here, never inside _SCHEMA — see the NOTE above the
        # password_reset_requests table in _SCHEMA for why (a real incident:
        # an index on token_hash inside _SCHEMA broke every boot against a
        # pre-existing DB, since _SCHEMA's CREATE TABLE IF NOT EXISTS is a
        # no-op there and the column doesn't land until this loop runs).
        for _idx_sql in _POST_MIGRATION_INDEXES:
            self.conn.execute(_idx_sql)
        self.conn.commit()
        # Vector search (#93) — best-effort, never blocks boot. See
        # _init_vector_search's docstring for why this can't live in _SCHEMA.
        self._vec_available = self._init_vector_search()

    def _init_vector_search(self) -> bool:
        """Load the sqlite-vec extension on this connection and create the
        articles_vec virtual table. Returns True when vector search is usable
        here, False otherwise (extension not installed, or this SQLite build
        has extension loading disabled) — every caller must check this rather
        than assume it worked, and degrade to FTS5-only rather than fail.

        Deliberately NOT part of _SCHEMA: creating a vec0 virtual table
        requires the extension to be loaded on THIS connection first, and any
        connection that can't load it must still be able to do ordinary
        articles reads/writes — a hard failure here would mean the whole app
        can't boot just because semantic search is unavailable.

        articles_vec uses rowid = articles.id (mirroring articles_fts's
        content_rowid='id' convention) rather than a stored article_id column
        — the same external-content-by-rowid idiom already used for FTS5,
        just without the trigger sync FTS5 gets (a network call can't run
        inside a SQL trigger, so embeddings are written from Python instead;
        see upsert_article_embedding). No explicit distance metric is
        configured: OpenAI's text-embedding-3 vectors are unit-length
        normalized, so vec0's default L2 distance is a monotonic transform of
        cosine similarity (L2^2 = 2 - 2*cos_sim) — ranking by ascending L2
        already ranks by descending cosine similarity.
        """
        try:
            import sqlite_vec
            from .embeddings import EMBED_DIM
            self.conn.enable_load_extension(True)
            sqlite_vec.load(self.conn)
            self.conn.enable_load_extension(False)
            self.conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS articles_vec USING "
                f"vec0(embedding float[{EMBED_DIM}])"
            )
            self.conn.commit()
            return True
        except Exception:
            return False

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

    def _migrate_community_local_markets(self) -> None:
        """One-time backfill of `local_markets` from the retired `metros_json`
        column, for every existing community that has metros but no
        local_markets yet (idempotent — a no-op on every boot after the
        first, and on any row an admin has already edited post-migration).
        Turning a JSON array into readable comma-separated text needs Python,
        so this can't be a plain ALTER TABLE line in the loop above."""
        rows = self.conn.execute(
            "SELECT id, metros_json FROM communities WHERE local_markets = '' AND metros_json != '[]'"
        ).fetchall()
        for row in rows:
            try:
                metros = json.loads(row["metros_json"]) or []
            except (ValueError, TypeError):
                continue
            if not metros:
                continue
            self.conn.execute(
                "UPDATE communities SET local_markets=? WHERE id=?",
                (", ".join(metros), row["id"]),
            )
        if rows:
            self.conn.commit()

    def find_legacy_product_screenshot_rows(self) -> list[dict]:
        """Preview query for migrate_app_screenshot_from_product_flag below —
        every tools/communities row a Phase E migration run would touch,
        with enough context (name/slug/url) to review before anything is
        written. Read-only; safe to call any time, as often as you like."""
        out: list[dict] = []
        for table in ("tools", "communities"):
            rows = self.conn.execute(
                f"SELECT id, name, slug, screenshot_url, screenshot_captured_at FROM {table} "
                f"WHERE screenshot_is_product = 1 AND app_screenshot_url = '' AND screenshot_url != ''"
            ).fetchall()
            for row in rows:
                out.append({
                    "table": table,
                    "id": row["id"],
                    "name": row["name"],
                    "slug": row["slug"],
                    "screenshot_url": row["screenshot_url"],
                    "screenshot_captured_at": row["screenshot_captured_at"],
                })
        return out

    def migrate_app_screenshot_from_product_flag(self) -> list[dict]:
        """One-time data migration (Phase E): before this phase, a manually-
        pasted screenshot flagged screenshot_is_product=1 was the ONLY way to
        show an actual product/app shot — it lived in the single
        screenshot_url slot and replaced whatever homepage capture was
        there. Now that app_screenshot_url is its own slot, every
        pre-existing row like that is semantically an app screenshot, not a
        homepage one, so this moves screenshot_url/screenshot_captured_at
        over to app_screenshot_url/app_screenshot_captured_at and clears the
        homepage slot (a record that had a product shot standing in for its
        homepage shot goes back to having no homepage shot at all, until one
        is captured for real — see CLAUDE.md/ARCHITECTURE.md for why this
        isn't a "no screenshot" regression, it's the correct read of what
        that row actually had).

        Deliberately NOT called from Library.__init__ (contrast with the two
        migrations above it) — this is a production DATA write, not a
        schema/column backfill, and the standing rule (CLAUDE.md, "One-off
        admin fixes against the database") requires Brian's review of the
        affected rows BEFORE a production write, not an after-the-fact
        deploy-log line. The only caller is
        scripts/migrate_app_screenshot_from_product_flag.py, run by hand
        (preview by default, --apply to actually write — same convention as
        scripts/backfill_logos.py). Idempotent regardless: guarded by
        app_screenshot_url = '', so a re-run only ever touches a row once.
        Non-destructive: screenshot_is_product itself is never cleared or
        dropped (see the ALTER TABLE comment above), so which rows this
        touched stays visible/reconstructable afterward purely by
        re-querying for screenshot_is_product=1.

        Returns the same row-dict shape as find_legacy_product_screenshot_rows
        — i.e. exactly what was (or, called with no candidates, would have
        been) touched — so a caller can print/log/assert against it."""
        touched: list[dict] = []
        for table in ("tools", "communities"):
            rows = self.conn.execute(
                f"SELECT id, name, slug, screenshot_url, screenshot_captured_at FROM {table} "
                f"WHERE screenshot_is_product = 1 AND app_screenshot_url = '' AND screenshot_url != ''"
            ).fetchall()
            for row in rows:
                self.conn.execute(
                    f"UPDATE {table} SET app_screenshot_url=?, app_screenshot_captured_at=?, "
                    f"screenshot_url='', screenshot_captured_at='' WHERE id=?",
                    (row["screenshot_url"], row["screenshot_captured_at"], row["id"]),
                )
                touched.append({
                    "table": table,
                    "id": row["id"],
                    "name": row["name"],
                    "slug": row["slug"],
                    "screenshot_url": row["screenshot_url"],
                    "screenshot_captured_at": row["screenshot_captured_at"],
                })
            if rows:
                self.conn.commit()
        return touched

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
        """Permanently remove an article. FTS is updated by the articles_ad
        trigger; the vector index and embedding ledger row are cleaned up
        here instead — a trigger can't load the sqlite-vec extension or make
        a network call, so embeddings never get trigger-based sync (see
        _init_vector_search)."""
        self.conn.execute("DELETE FROM articles WHERE id=?", (article_id,))
        self.conn.execute("DELETE FROM article_embeddings WHERE article_id=?", (article_id,))
        if self._vec_available:
            self.conn.execute("DELETE FROM articles_vec WHERE rowid=?", (article_id,))
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

    # -- embeddings / semantic retrieval (#93) ---------------------------------

    def vector_search_available(self) -> bool:
        """Whether this connection can do vector search (sqlite-vec loaded).
        Callers (agent.retrieve) use this to skip embedding a query entirely
        when the answer would be an empty result anyway."""
        return self._vec_available

    def upsert_article_embedding(self, article_id: int, vector: list[float],
                                 content_hash: str, model: str,
                                 input_tokens: int = 0, cost_usd: float = 0.0) -> bool:
        """Persist one article's embedding vector (articles_vec) plus its
        overhead-cost ledger row (article_embeddings).

        Returns False and writes nothing when vector search isn't available
        on this connection — a ledger row implying "this article is
        searchable" would be misleading with no vector behind it.

        Upserts by deleting any prior vector for this article_id first: vec0
        virtual tables reject a duplicate rowid even with `INSERT OR REPLACE`
        (verified — raises the same UNIQUE-constraint error as a plain
        duplicate insert), so this mirrors the delete-then-insert idiom the
        articles_fts triggers already use for the same reason.
        """
        if not self._vec_available:
            return False
        import sqlite_vec
        self.conn.execute("DELETE FROM articles_vec WHERE rowid=?", (article_id,))
        self.conn.execute(
            "INSERT INTO articles_vec(rowid, embedding) VALUES (?,?)",
            (article_id, sqlite_vec.serialize_float32(vector)),
        )
        self.conn.execute(
            """INSERT INTO article_embeddings
               (article_id, content_hash, model, input_tokens, cost_usd, embedded_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(article_id) DO UPDATE SET
                   content_hash=excluded.content_hash, model=excluded.model,
                   input_tokens=excluded.input_tokens, cost_usd=excluded.cost_usd,
                   embedded_at=excluded.embedded_at""",
            (article_id, content_hash, model, input_tokens, cost_usd, _now()),
        )
        self.conn.commit()
        return True

    def record_enrichment_cost(self, article_id: Optional[int], model: str,
                               input_tokens: int = 0, output_tokens: int = 0,
                               cost_usd: float = 0.0) -> None:
        """Append one enrichment API call's real cost to the overhead ledger
        (#105). A plain INSERT, not an upsert like upsert_article_embedding —
        enrichment_cost keeps every call's history rather than the latest
        call only. `article_id=None` records enrichment that happened before
        a candidate was saved (linklib.queue's pre-save path) — the spend
        still counts even if the candidate is later dismissed."""
        self.conn.execute(
            """INSERT INTO enrichment_cost
               (article_id, model, input_tokens, output_tokens, cost_usd, created_at)
               VALUES (?,?,?,?,?,?)""",
            (article_id, model, input_tokens, output_tokens, cost_usd, _now()),
        )
        self.conn.commit()

    # -- AI-drafted field review tracking (standing principle: AI drafts a
    # first pass into the edit form, nothing publishes without an explicit
    # human review-and-save) ------------------------------------------------

    def record_field_review(self, entity_type: str, entity_id: int, field_name: str,
                             reviewed_by: str = "") -> None:
        """Stamp one field as reviewed (saved after being AI-drafted this
        editing session) — an upsert, since only the most recent review of a
        field matters. Called from an edit-submit route once per field named
        in the submitted ai_drafted_fields list, never inferred from content."""
        self.conn.execute(
            """INSERT INTO field_reviews (entity_type, entity_id, field_name, reviewed_at, reviewed_by)
               VALUES (?,?,?,?,?)
               ON CONFLICT(entity_type, entity_id, field_name)
               DO UPDATE SET reviewed_at=excluded.reviewed_at, reviewed_by=excluded.reviewed_by""",
            (entity_type, entity_id, field_name, _now(), reviewed_by.strip()),
        )
        self.conn.commit()

    def list_field_reviews(self, entity_type: str, entity_id: int) -> dict[str, dict]:
        """{field_name: {"reviewed_at":..., "reviewed_by":...}} for one
        entity — the admin edit page's per-field "last reviewed" display."""
        rows = self.conn.execute(
            "SELECT field_name, reviewed_at, reviewed_by FROM field_reviews WHERE entity_type=? AND entity_id=?",
            (entity_type, entity_id),
        ).fetchall()
        return {r["field_name"]: {"reviewed_at": r["reviewed_at"], "reviewed_by": r["reviewed_by"]} for r in rows}

    def enrichment_cost_total(self, since: Optional[str] = None) -> float:
        """Total enrichment overhead spend, optionally since an ISO date/datetime
        prefix. Mirrors ask_cost_total's shape for the user-cap ledger."""
        clause = "WHERE created_at>=?" if since else ""
        params = [since] if since else []
        row = self.conn.execute(
            f"SELECT COALESCE(SUM(cost_usd),0) FROM enrichment_cost {clause}", params
        ).fetchone()
        return float(row[0])

    def overhead_cost_breakdown(self) -> list[dict]:
        """One row per internal cost-attribution source (embeddings,
        enrichment, FP&A Buddy queries) for the admin overhead-spend page's
        "Toolbox usage" section: count of ledger rows, total cost all-time,
        and total cost this calendar month. This is token-cost math, not
        billed dollars, and is never summed into manual_overhead's vendor
        totals — see manual_overhead's schema comment for why those two
        numbers are deliberately kept apart rather than reconciled."""
        month_start = datetime.now(timezone.utc).strftime("%Y-%m-01")
        sources = [
            ("Embeddings", "article_embeddings", "embedded_at"),
            ("Enrichment", "enrichment_cost", "created_at"),
            ("FP&A Buddy queries", "ask_questions", "created_at"),
        ]
        out = []
        for label, table, ts_col in sources:
            row = self.conn.execute(
                f"SELECT COUNT(*), COALESCE(SUM(cost_usd),0) FROM {table}"
            ).fetchone()
            month_row = self.conn.execute(
                f"SELECT COALESCE(SUM(cost_usd),0) FROM {table} WHERE {ts_col}>=?",
                (month_start,),
            ).fetchone()
            out.append({
                "source": label, "count": row[0], "total_cost": float(row[1]),
                "this_month_cost": float(month_row[0]),
            })
        return out

    def overhead_cost_by_month(self, months: int = 12) -> list[dict]:
        """Combined embeddings + enrichment + FP&A Buddy internal cost
        attribution grouped by calendar month, most recent first, for the
        admin overhead-spend page's "Toolbox usage" by-month table. See
        overhead_cost_breakdown for why this is kept separate from
        manual_overhead's vendor totals."""
        totals: dict[str, float] = {}
        for table, ts_col in (("article_embeddings", "embedded_at"),
                              ("enrichment_cost", "created_at"),
                              ("ask_questions", "created_at")):
            rows = self.conn.execute(
                f"""SELECT strftime('%Y-%m', {ts_col}) AS ym, SUM(cost_usd)
                    FROM {table} WHERE {ts_col} != '' GROUP BY ym"""
            ).fetchall()
            for ym, cost in rows:
                if ym:
                    totals[ym] = totals.get(ym, 0.0) + float(cost)
        return [{"month": ym, "cost_usd": totals[ym]}
                for ym in sorted(totals, reverse=True)[:months]]

    # -- manual_overhead: vendor totals (Section 1) ---------------------------
    # The one and only source for "total cost of the site." Every row is a
    # real receipt amount typed in by hand — see the table's schema comment
    # for why this is deliberately never reconciled against the token-cost
    # ledgers in overhead_cost_breakdown/overhead_cost_by_month above.

    def list_manual_overhead(self, category: str | None = None) -> list[dict]:
        """All vendor-spend rows, most recent charge first. Pass category to
        filter the list view — purely a display filter, doesn't affect any
        total."""
        if category:
            rows = self.conn.execute(
                "SELECT * FROM manual_overhead WHERE category=? ORDER BY date DESC, id DESC",
                (category,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM manual_overhead ORDER BY date DESC, id DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def manual_overhead_categories(self) -> list[str]:
        """Distinct category tags in use, alphabetical — populates the
        filter dropdown and the add-form's suggestions."""
        rows = self.conn.execute(
            "SELECT DISTINCT category FROM manual_overhead WHERE category != '' ORDER BY category"
        ).fetchall()
        return [r[0] for r in rows]

    def manual_overhead_total(self, category: str | None = None) -> float:
        """Sum of every vendor-spend row — THE total cost of the site.
        A plain SQL SUM, always correct because it's literally what got
        paid; no derivation, no reconciliation possible or needed."""
        if category:
            row = self.conn.execute(
                "SELECT COALESCE(SUM(amount),0) FROM manual_overhead WHERE category=?",
                (category,),
            ).fetchone()
        else:
            row = self.conn.execute("SELECT COALESCE(SUM(amount),0) FROM manual_overhead").fetchone()
        return float(row[0])

    def manual_overhead_monthly_by_category(self, months: int = 12) -> dict:
        """Vendor-spend rows bucketed by calendar month and category, for the
        last `months` months ending with the current month — feeds the
        stacked-bar chart on /admin/overhead-spend (and its wider 36-month
        variant on the details page). Zero-filled: every month in the range
        appears even if nothing was spent. Blank category is grouped as
        "Uncategorized" rather than dropped, so a hand-entered row without a
        category tag doesn't silently vanish from the chart. Categories are
        ordered by total spend (largest first) so the stack order and the
        legend order match.

        Returns {"months": [...], "categories": [...], "series": {cat: [...]}}
        — months oldest-first, each series list parallel to months."""
        today = datetime.now(timezone.utc).date()
        start_year, start_month = today.year, today.month - (months - 1)
        while start_month <= 0:
            start_month += 12
            start_year -= 1
        start_date = f"{start_year:04d}-{start_month:02d}-01"

        month_keys = []
        y, m = start_year, start_month
        for _ in range(months):
            month_keys.append(f"{y:04d}-{m:02d}")
            m += 1
            if m > 12:
                m = 1
                y += 1

        rows = self.conn.execute(
            """SELECT strftime('%Y-%m', date) AS month,
                      COALESCE(NULLIF(category, ''), 'Uncategorized') AS cat,
                      SUM(amount) AS amt
               FROM manual_overhead
               WHERE date >= ?
               GROUP BY month, cat""",
            (start_date,),
        ).fetchall()

        month_set = set(month_keys)
        totals_by_cat: dict[str, float] = {}
        grid: dict[str, dict[str, float]] = {}
        for r in rows:
            month, cat, amt = r["month"], r["cat"], float(r["amt"])
            if month not in month_set:
                continue  # malformed date string that didn't strftime cleanly
            grid.setdefault(cat, {})[month] = amt
            totals_by_cat[cat] = totals_by_cat.get(cat, 0.0) + amt

        categories = sorted(totals_by_cat, key=lambda c: -totals_by_cat[c])
        series = {
            cat: [grid.get(cat, {}).get(mk, 0.0) for mk in month_keys]
            for cat in categories
        }
        return {"months": month_keys, "categories": categories, "series": series}

    def add_manual_overhead(self, vendor: str, date: str, amount: float,
                             category: str = "", note: str = "") -> int:
        vendor, date, category, note = vendor.strip(), date.strip(), category.strip(), note.strip()
        if not vendor:
            raise ValueError("Vendor is required.")
        if not date:
            raise ValueError("Date is required.")
        cur = self.conn.execute(
            """INSERT INTO manual_overhead (vendor, date, amount, category, note, created_at)
               VALUES (?,?,?,?,?,?)""",
            (vendor, date, amount, category, note, datetime.now(timezone.utc).isoformat()),
        )
        self.conn.commit()
        return cur.lastrowid

    def update_manual_overhead(self, entry_id: int, vendor: str, date: str, amount: float,
                                category: str = "", note: str = "") -> None:
        vendor, date, category, note = vendor.strip(), date.strip(), category.strip(), note.strip()
        if not vendor:
            raise ValueError("Vendor is required.")
        if not date:
            raise ValueError("Date is required.")
        row = self.conn.execute("SELECT 1 FROM manual_overhead WHERE id=?", (entry_id,)).fetchone()
        if not row:
            raise ValueError("Entry not found.")
        self.conn.execute(
            "UPDATE manual_overhead SET vendor=?, date=?, amount=?, category=?, note=? WHERE id=?",
            (vendor, date, amount, category, note, entry_id),
        )
        self.conn.commit()

    def delete_manual_overhead(self, entry_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM manual_overhead WHERE id=?", (entry_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def get_article(self, article_id: int) -> Optional[dict]:
        """One article by id, or None. A plain indexed lookup — used by the
        embed-on-save hook, which needs the freshly-upserted row (content may
        have been merged/kept from an existing row, not the just-saved
        Article object) rather than a linear scan of search results."""
        row = self.conn.execute("SELECT * FROM articles WHERE id=?", (article_id,)).fetchone()
        return self._row_to_dict(row) if row else None

    def embedding_content_hash(self, article_id: int) -> Optional[str]:
        """Stored content_hash for one article's embedding, or None if it's
        never been embedded. A single-row lookup for the embed-on-save hook
        (called once per save) — the bulk embedding_hashes() map below is for
        the backfill script, which needs every row's hash at once."""
        row = self.conn.execute(
            "SELECT content_hash FROM article_embeddings WHERE article_id=?", (article_id,)
        ).fetchone()
        return row[0] if row else None

    def embedding_hashes(self) -> dict[int, str]:
        """Map of article_id -> stored content_hash for every embedded
        article, in one query. Used by the backfill script to skip rows
        whose current document_text hash already matches, without a
        per-row lookup."""
        rows = self.conn.execute(
            "SELECT article_id, content_hash FROM article_embeddings"
        ).fetchall()
        return {r[0]: r[1] for r in rows}

    def vector_search(self, vector: list[float], limit: int = 50) -> list[dict]:
        """K-nearest-neighbor search over embedded articles, nearest first.
        Returns [] when vector search isn't available on this connection or
        `vector` is empty — callers fall back to FTS5-only, same contract as
        every other best-effort call in this codebase."""
        if not self._vec_available or not vector:
            return []
        import sqlite_vec
        rows = self.conn.execute(
            "SELECT rowid FROM articles_vec WHERE embedding MATCH ? AND k = ? ORDER BY distance",
            (sqlite_vec.serialize_float32(vector), limit),
        ).fetchall()
        if not rows:
            return []
        ids = [r[0] for r in rows]
        placeholders = ",".join("?" * len(ids))
        by_id = {a["id"]: a for a in
                 [self._row_to_dict(r) for r in
                  self.conn.execute(f"SELECT * FROM articles WHERE id IN ({placeholders})", ids).fetchall()]}
        # Preserve KNN rank order; silently skip a rowid whose article no
        # longer exists (delete_article cleans up articles_vec too, so this
        # is just a guard against drift, not an expected case).
        return [by_id[rid] for (rid,) in rows if rid in by_id]

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
            "SELECT id, username, role, active, name, email, created_at, last_login_at, "
            "ask_cap_usd, matchmaker_cap_usd "
            "FROM users ORDER BY role DESC, username"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_user(self, username: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT id, username, role, active, name, email, created_at, last_login_at, "
            "ask_cap_usd, matchmaker_cap_usd "
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

    def list_contacts(self, include_deleted: bool = False) -> list[dict]:
        where = "" if include_deleted else "WHERE deleted_at = ''"
        rows = self.conn.execute(
            f"SELECT * FROM contacts {where} ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def count_contacts_since(self, ts: str) -> int:
        """Non-deleted contacts created after `ts` (an ISO timestamp, '' =
        every row — string comparison against '' is true for any non-empty
        created_at)."""
        return self.conn.execute(
            "SELECT COUNT(*) FROM contacts WHERE created_at > ? AND deleted_at = ''", (ts,)
        ).fetchone()[0]

    def soft_delete_contacts(self, ids: list[int]) -> list[dict]:
        """Soft-delete the given contacts.id values (already-deleted ids are
        left alone). Returns the rows that were actually deleted, as they
        looked just before deletion, for the caller to build an audit-log
        detail string from."""
        ids = [i for i in ids if isinstance(i, int)]
        if not ids:
            return []
        placeholders = ",".join("?" * len(ids))
        rows = self.conn.execute(
            f"SELECT * FROM contacts WHERE id IN ({placeholders}) AND deleted_at = ''", ids
        ).fetchall()
        deleted = [dict(r) for r in rows]
        if deleted:
            self.conn.execute(
                f"UPDATE contacts SET deleted_at=? WHERE id IN ({placeholders})",
                [_now()] + ids,
            )
            self.conn.commit()
        return deleted

    # -- contact audit log ---------------------------------------------------

    def record_contact_audit(self, admin_id: Optional[int], action: str,
                              item_id: Optional[int] = None, detail: str = "") -> int:
        """Log one admin delete of a contact submission (or a bulk delete,
        item_id=None, detail carrying a summary). Same shape as
        record_archive_audit."""
        cur = self.conn.execute(
            "INSERT INTO contact_audit_log (admin_id, action, item_id, detail, created_at) "
            "VALUES (?,?,?,?,?)",
            (admin_id, action, item_id, detail, _now()),
        )
        self.conn.commit()
        return cur.lastrowid

    def list_contact_audit_log(self, limit: int = 500) -> list[dict]:
        rows = self.conn.execute(
            """SELECT a.*, u.username AS admin_username, u.name AS admin_name
               FROM contact_audit_log a LEFT JOIN users u ON u.id = a.admin_id
               ORDER BY a.created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    # -- tools directory ---------------------------------------------------

    def _find_tool_by_normalized_url(self, url: str, exclude_id: int | None = None) -> sqlite3.Row | None:
        target = normalize_url(url)
        if not target:
            return None
        rows = self.conn.execute("SELECT id, name, url, slug FROM tools").fetchall()
        for r in rows:
            if r["id"] == exclude_id:
                continue
            if normalize_url(r["url"]) == target:
                return r
        return None

    def find_tool_name_duplicate(self, name: str, exclude_id: int | None = None) -> dict | None:
        """Save-time warn check (not a block, unlike the URL check above): the
        first existing tool whose normalize_tool_name() matches. Used by the
        /admin/tools/new and edit-save routes to show a non-blocking warning
        banner — callers decide the UX; this is a plain read."""
        target = normalize_tool_name(name)
        if not target:
            return None
        rows = self.conn.execute("SELECT id, name, slug FROM tools").fetchall()
        for r in rows:
            if r["id"] == exclude_id:
                continue
            if normalize_tool_name(r["name"]) == target:
                return dict(r)
        return None

    @staticmethod
    def _tool_name_pair_key(tool_id_a: int, tool_id_b: int) -> str:
        """Order-independent key for a pair of tool ids."""
        lo, hi = sorted([int(tool_id_a), int(tool_id_b)])
        return f"{lo}\n{hi}"

    def record_tool_name_dedupe_decision(self, tool_id_a: int, tool_id_b: int, verdict: str) -> None:
        """Remember the admin's call on a name-duplicate candidate pair:
        'duplicate' or 'dismissed'. Upserts on the id pair, so re-deciding
        overwrites the prior verdict. Stored with tool_id_a/b as the smaller
        id first, for a stable, human-readable row regardless of click order."""
        lo, hi = sorted([int(tool_id_a), int(tool_id_b)])
        self.conn.execute(
            "INSERT INTO tool_name_dedupe_decisions (pair_key, tool_id_a, tool_id_b, verdict, created_at) "
            "VALUES (?,?,?,?,?) "
            "ON CONFLICT(pair_key) DO UPDATE SET verdict=excluded.verdict, created_at=excluded.created_at",
            (self._tool_name_pair_key(lo, hi), lo, hi, verdict, _now()),
        )
        self.conn.commit()

    def resolved_tool_name_pairs(self) -> set:
        """pair_keys the admin has already resolved (either verdict) — both
        stop the pair from resurfacing in find_tool_name_duplicate_candidates."""
        rows = self.conn.execute("SELECT pair_key FROM tool_name_dedupe_decisions").fetchall()
        return {r[0] for r in rows}

    def tool_name_dedupe_decisions(self, limit: int = 100) -> list[dict]:
        """Resolved candidate pairs, newest first, with each tool's current
        name/slug so a stale row (renamed since) still reads sensibly.
        delete_tool cascades a cleanup here, so a '(deleted)' side shouldn't
        normally occur — this is just a defensive fallback (e.g. direct DB
        edits) rather than erroring on a lookup miss. Note 'duplicate' rows
        found here are legacy, from before the confirm-a-duplicate action
        started deleting immediately (see pending_tool_name_merges) — the
        admin view only ever writes 'dismissed' through this path now."""
        rows = self.conn.execute(
            "SELECT tool_id_a, tool_id_b, verdict, created_at FROM tool_name_dedupe_decisions "
            "ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            ta = self.get_tool(r["tool_id_a"])
            tb = self.get_tool(r["tool_id_b"])
            out.append({
                "tool_id_a": r["tool_id_a"], "tool_id_b": r["tool_id_b"],
                "name_a": ta["name"] if ta else "(deleted)",
                "name_b": tb["name"] if tb else "(deleted)",
                "verdict": r["verdict"], "created_at": r["created_at"],
            })
        return out

    def pending_tool_name_merges(self) -> list[dict]:
        """Legacy 'duplicate'-verdict rows where both tools are still live —
        i.e. an admin confirmed a pair as duplicates before the merge-on-confirm
        UI existed, so nothing was actually deleted. Surfaced in
        /admin/tools/name-duplicates as an actionable "pick which to keep" row,
        same shape as a fresh candidate. Drains to empty naturally: once one
        side is deleted, delete_tool's cascade removes the row here too."""
        rows = self.conn.execute(
            "SELECT tool_id_a, tool_id_b FROM tool_name_dedupe_decisions WHERE verdict='duplicate'"
        ).fetchall()
        out = []
        for r in rows:
            ta = self.get_tool(r["tool_id_a"])
            tb = self.get_tool(r["tool_id_b"])
            if not ta or not tb:
                continue
            out.append({
                "normalized_name": normalize_tool_name(ta["name"]),
                "tool_a": ta, "tool_b": tb,
            })
        return out

    def find_tool_name_duplicate_candidates(self) -> list[dict]:
        """Scan every current tool (approved or not) for normalize_tool_name()
        exact matches, grouped, then every pairwise combination within a group
        minus any pair the admin already resolved (see resolved_tool_name_pairs).
        Powers the /admin/tools/name-duplicates review view. O(n log n) grouping
        over ~190 rows — fine to run live on every page load, no caching needed."""
        from itertools import combinations
        rows = self.conn.execute("SELECT id, name, slug, url, approved FROM tools").fetchall()
        groups: dict[str, list[sqlite3.Row]] = {}
        for r in rows:
            key = normalize_tool_name(r["name"])
            if not key:
                continue
            groups.setdefault(key, []).append(r)
        resolved = self.resolved_tool_name_pairs()
        candidates = []
        for key, members in groups.items():
            if len(members) < 2:
                continue
            for a, b in combinations(members, 2):
                pair_key = self._tool_name_pair_key(a["id"], b["id"])
                if pair_key in resolved:
                    continue
                candidates.append({
                    "normalized_name": key,
                    "tool_a": dict(a),
                    "tool_b": dict(b),
                    "pair_key": pair_key,
                })
        candidates.sort(key=lambda c: c["normalized_name"])
        return candidates

    def add_tool(self, name: str, description: str, url: str,
                 categories: list[str], submitted_by: str = "",
                 approved: int = 0, advisor: int = 0,
                 promoted: int = 0, vendor_email: str = "",
                 warm_intro_enabled: int = 0, vendor_name: str = "",
                 summary: str = "") -> int:
        dup = self._find_tool_by_normalized_url(url)
        if dup:
            raise DuplicateURLError("software entry", dup["id"], dup["name"], dup["slug"])
        # Domain-derived slug (Phase 2): short domain root first (e.g.
        # "abacum"), falling back to the full hyphenated domain only when
        # the short form collides with an existing row, then a numeric
        # suffix as a last resort. Falls back to a name-based slug when the
        # URL has no parseable host.
        base = _domain_slug_base(url) or _slugify(name)
        if self.conn.execute("SELECT 1 FROM tools WHERE slug=?", (base,)).fetchone():
            base = _domain_slug_full(url) or base
        slug = base
        suffix = 2
        while self.conn.execute("SELECT 1 FROM tools WHERE slug=?", (slug,)).fetchone():
            slug = f"{base}-{suffix}"
            suffix += 1
        now = _now()
        cur = self.conn.execute(
            """INSERT INTO tools (name, slug, description, url, categories_json,
               approved, advisor, submitted_by, created_at, updated_at, promoted, vendor_email,
               warm_intro_enabled, vendor_name, summary)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (name.strip(), slug, description.strip(), url.strip(),
             json.dumps(categories), approved, advisor, submitted_by.strip(), now, now,
             promoted, vendor_email.strip(), warm_intro_enabled, vendor_name.strip(),
             summary.strip()),
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

    def get_tool_by_slug(self, slug: str) -> dict | None:
        """Used by the public profile page (/tools/software/<slug>). Only
        returns approved rows — an unapproved/pending tool has no live
        directory listing, so its profile shouldn't be reachable either."""
        row = self.conn.execute(
            "SELECT * FROM tools WHERE slug=? AND approved=1", (slug,)
        ).fetchone()
        return self._tool_to_dict(row) if row else None

    def update_tool(self, tool_id: int, name: str, description: str,
                    url: str, categories: list[str], advisor: int = 0,
                    promoted: int = 0, vendor_email: str = "",
                    warm_intro_enabled: int = 0, vendor_name: str = "",
                    summary: str = "") -> None:
        # Only check when the URL is actually changing — callers that resave a
        # row unchanged (e.g. the bulk-edit routes, which always pass the
        # row's own current url back) must never trip on a pre-existing
        # duplicate elsewhere in the table that has nothing to do with this edit.
        current = self.get_tool(tool_id)
        if current and normalize_url(url) != normalize_url(current["url"]):
            dup = self._find_tool_by_normalized_url(url, exclude_id=tool_id)
            if dup:
                raise DuplicateURLError("software entry", dup["id"], dup["name"], dup["slug"])
        self.conn.execute(
            """UPDATE tools SET name=?, description=?, url=?, categories_json=?,
               advisor=?, promoted=?, vendor_email=?, warm_intro_enabled=?, vendor_name=?,
               summary=?, updated_at=? WHERE id=?""",
            (name.strip(), description.strip(), url.strip(),
             json.dumps(categories), advisor, promoted, vendor_email.strip(),
             warm_intro_enabled, vendor_name.strip(), summary.strip(), _now(), tool_id),
        )
        self.conn.commit()

    def update_tool_content(self, tool_id: int, name: str, description: str) -> None:
        """Narrow update for scripts/seed_tools.py's re-sync pass (#113): touches only
        name and description, leaving categories/advisor/promoted/vendor/warm-intro
        fields untouched so a content refresh can never clobber admin edits made
        directly on the live site after seeding."""
        self.conn.execute(
            "UPDATE tools SET name=?, description=?, updated_at=? WHERE id=?",
            (name.strip(), description.strip(), _now(), tool_id),
        )
        self.conn.commit()

    def quick_update_tool(self, tool_id: int, description: str,
                          warm_intro_enabled: int, vendor_name: str,
                          vendor_email: str, summary: str = "") -> None:
        """Partial update for the /tools inline "Quick edit" panel — touches
        only description/summary and warm-intro fields, leaving name/url/
        categories/advisor/promoted untouched (those still require the full
        edit form)."""
        self.conn.execute(
            """UPDATE tools SET description=?, warm_intro_enabled=?, vendor_name=?,
               vendor_email=?, summary=?, updated_at=? WHERE id=?""",
            (description.strip(), warm_intro_enabled, vendor_name.strip(),
             vendor_email.strip(), summary.strip(), _now(), tool_id),
        )
        self.conn.commit()

    def approve_tool(self, tool_id: int) -> None:
        self.conn.execute("UPDATE tools SET approved=1 WHERE id=?", (tool_id,))
        self.conn.commit()

    def count_pending_tools(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM tools WHERE approved=0").fetchone()[0]

    def delete_tool(self, tool_id: int, admin_id: Optional[int] = None,
                     action: str = "delete") -> None:
        """Hard-deletes a tools row (no soft-delete column exists — see the
        tools CREATE TABLE). Snapshots name/url/categories into
        tool_audit_log *before* the DELETE, since that's the only record of
        what was removed once this returns. Logged here rather than at each
        call site (unlike archive/contact audit) because every delete path —
        the single-row admin Delete button, bulk delete, a pending
        submission's Reject, and a name-duplicate merge's "delete the loser"
        step — routes through this one method; logging inside it means a
        future new call site can't forget to record the deletion. `action`
        lets a caller note which of those paths this was ('delete' default,
        'reject', or 'merge')."""
        row = self.get_tool(tool_id)
        self.conn.execute("DELETE FROM tools WHERE id=?", (tool_id,))
        self.conn.execute("DELETE FROM field_reviews WHERE entity_type='tool' AND entity_id=?", (tool_id,))
        self.conn.execute(
            "DELETE FROM tool_competitors WHERE tool_id=? OR competitor_id=?",
            (tool_id, tool_id),
        )
        # Same cascade as tool_competitors above: a resolved name-dedupe
        # verdict referencing this id (either side) has nothing left to
        # point at once the tool's gone, so drop it rather than leave it as
        # permanent orphaned data.
        self.conn.execute(
            "DELETE FROM tool_name_dedupe_decisions WHERE tool_id_a=? OR tool_id_b=?",
            (tool_id, tool_id),
        )
        if row:
            detail = f"{row['name']} | {row['url']} | categories: {', '.join(row['categories'])}"
            self.conn.execute(
                "INSERT INTO tool_audit_log (admin_id, action, item_id, detail, created_at) "
                "VALUES (?,?,?,?,?)",
                (admin_id, action, tool_id, detail, _now()),
            )
        self.conn.commit()

    def list_tool_audit_log(self, limit: int = 500) -> list[dict]:
        rows = self.conn.execute(
            """SELECT a.*, u.username AS admin_username, u.name AS admin_name
               FROM tool_audit_log a LEFT JOIN users u ON u.id = a.admin_id
               ORDER BY a.created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def update_tool_differentiation(self, tool_id: int, differentiation_note: str) -> None:
        """Narrow update for the admin full-edit form's "How this differs from
        the competition" field (Phase 3) — same reasoning as
        quick_update_tool: kept separate from update_tool so the Software
        bulk-edit panel, which re-saves every other field on every call, can
        never silently blank this one out just because it doesn't know about it."""
        self.conn.execute(
            "UPDATE tools SET differentiation_note=?, updated_at=? WHERE id=?",
            (differentiation_note.strip(), _now(), tool_id),
        )
        self.conn.commit()

    def update_tool_agent_taxonomy(self, tool_id: int, agent_taxonomy_note: str) -> None:
        """Narrow update for the admin full-edit form's agent-taxonomy field
        (Phase 5) — same bulk-edit-safety reasoning as update_tool_differentiation.
        A human editing/saving this field is itself a confirmation, so this
        always clears agent_taxonomy_needs_verification — same convention as
        editing a tool_features row implying review."""
        self.conn.execute(
            "UPDATE tools SET agent_taxonomy_note=?, agent_taxonomy_needs_verification=0, "
            "updated_at=? WHERE id=?",
            (agent_taxonomy_note.strip(), _now(), tool_id),
        )
        self.conn.commit()

    def set_tool_agent_taxonomy_draft(self, tool_id: int, agent_taxonomy_note: str,
                                      needs_verification: int = 1) -> None:
        """Records an LLM-drafted agent-taxonomy summary (automated research —
        either the auto-run-on-add background task or the on-demand refresh)
        as unconfirmed by default. Only writes when the tool doesn't already
        have a note, unless the caller explicitly wants to overwrite (the
        on-demand "Refresh" action passes needs_verification the same way but
        the caller decides whether to call this at all — see the refresh
        route, which always overwrites; the auto-on-add path only calls this
        for a brand-new tool that has nothing yet)."""
        self.conn.execute(
            "UPDATE tools SET agent_taxonomy_note=?, agent_taxonomy_needs_verification=?, "
            "updated_at=? WHERE id=?",
            (agent_taxonomy_note.strip(), needs_verification, _now(), tool_id),
        )
        self.conn.commit()

    def mark_tool_agent_taxonomy_verified(self, tool_id: int) -> None:
        """One-click "Mark verified" action, same as the equivalent
        tool_features action — clears the flag without touching the text."""
        self.conn.execute(
            "UPDATE tools SET agent_taxonomy_needs_verification=0, updated_at=? WHERE id=?",
            (_now(), tool_id),
        )
        self.conn.commit()

    def update_tool_screenshot(self, tool_id: int, screenshot_url: str, screenshot_is_product: int) -> None:
        """Legacy narrow update, kept for pre-Phase-E callers/tests only —
        DO NOT call this from the admin edit-form submit path. Writes
        screenshot_is_product unconditionally, which is exactly the bug a
        2026-08 incident traced back to this method: the Phase E submit
        route called it with a hardcoded screenshot_is_product=0 on EVERY
        full-form save (any field, not just the screenshot ones), silently
        clearing the retired flag on rows the one-time migration script
        (scripts/migrate_app_screenshot_from_product_flag.py) hadn't been
        run against yet — so a row could vanish from that script's preview
        between two runs seconds apart, with no --apply in between, just
        because someone resaved the tool's edit page for an unrelated
        reason. update_tool_screenshot_url below is what the live app
        actually calls now; this one stays only so
        tests/test_screenshot_capture.py's and
        tests/test_software_screenshot.py's coverage of the pre-Phase-E
        write path (and any future one-off script that genuinely needs to
        set screenshot_is_product) still has a method to call."""
        self.conn.execute(
            "UPDATE tools SET screenshot_url=?, screenshot_is_product=?, screenshot_captured_at='', "
            "updated_at=? WHERE id=?",
            (screenshot_url.strip(), screenshot_is_product, _now(), tool_id),
        )
        self.conn.commit()

    def update_tool_screenshot_url(self, tool_id: int, screenshot_url: str) -> None:
        """Narrow update for the admin full-edit form's homepage Screenshot
        URL field (Phase E fix) — same bulk-edit-safety reasoning as
        update_tool_differentiation, and deliberately does NOT touch
        screenshot_is_product at all (contrast with update_tool_screenshot
        above, which does and is why this method exists — see its
        docstring). Clears screenshot_captured_at: a hand-pasted URL has no
        known capture time, and leaving a stale timestamp on it would
        misrepresent it as a fresh automated capture."""
        self.conn.execute(
            "UPDATE tools SET screenshot_url=?, screenshot_captured_at='', updated_at=? WHERE id=?",
            (screenshot_url.strip(), _now(), tool_id),
        )
        self.conn.commit()

    def set_tool_screenshot_capture(self, tool_id: int, screenshot_url: str) -> None:
        """Records an automated homepage capture — used by both
        scripts/capture_tool_screenshots.py and the live "Recapture" admin
        button, so a bulk backfill and a one-off manual recapture leave the
        row in an identical state. Always homepage-only (screenshot_is_product=0)
        by design; timestamp is set here, not passed in, so every capture path
        stamps the actual write time."""
        self.conn.execute(
            "UPDATE tools SET screenshot_url=?, screenshot_is_product=0, screenshot_captured_at=?, "
            "updated_at=? WHERE id=?",
            (screenshot_url.strip(), _now(), _now(), tool_id),
        )
        self.conn.commit()

    def update_tool_app_screenshot_source(self, tool_id: int, app_screenshot_source_url: str) -> None:
        """Narrow update for the admin edit form's app-screenshot source URL
        field (Phase E) — the login/demo/product-tour page Brian wants
        auto-capture to run against. Deliberately doesn't touch
        app_screenshot_url/app_screenshot_captured_at: editing the source URL
        doesn't invalidate whatever's currently captured/uploaded until a
        recapture actually runs, same reasoning as update_tool_differentiation
        (a narrow single-field update, safe to call from the bulk-edit form)."""
        self.conn.execute(
            "UPDATE tools SET app_screenshot_source_url=?, updated_at=? WHERE id=?",
            (app_screenshot_source_url.strip(), _now(), tool_id),
        )
        self.conn.commit()

    def set_tool_app_screenshot(self, tool_id: int, app_screenshot_url: str) -> None:
        """Records an app screenshot — either an automated capture against
        app_screenshot_source_url, or a manually cropped-and-uploaded file
        (Phase E). Both write paths call this same setter: unlike the
        homepage slot's screenshot_is_product distinction, there's no need to
        track which path populated the app slot (a hand-uploaded shot and an
        auto-captured one are equally "the app screenshot" once saved)."""
        self.conn.execute(
            "UPDATE tools SET app_screenshot_url=?, app_screenshot_captured_at=?, "
            "updated_at=? WHERE id=?",
            (app_screenshot_url.strip(), _now(), _now(), tool_id),
        )
        self.conn.commit()

    def set_tool_logo(self, tool_id: int, logo_path: str) -> None:
        """Records a downloaded-and-stored logo asset (Phase D backfill —
        scripts/backfill_logos.py is the only caller today). `logo_path` is a
        relative path under webapp/static/ (e.g. "logos/tools/abacum.svg"),
        never an external URL — see the logo_path ALTER TABLE comment in
        __init__ for the full reasoning."""
        self.conn.execute(
            "UPDATE tools SET logo_path=?, updated_at=? WHERE id=?",
            (logo_path.strip(), _now(), tool_id),
        )
        self.conn.commit()

    # -- competitor cross-links (Phase 3) ------------------------------------
    # See the tool_competitors CREATE TABLE comment for the normalized-pair
    # storage shape. This is the source of truth rendered on a Software
    # profile page; suggest_tool_competitors below is a separate, read-only
    # tag-overlap helper that only powers an admin-UI suggestion list.

    def add_tool_competitor(self, tool_id: int, competitor_id: int) -> None:
        if tool_id == competitor_id:
            raise ValueError("A tool can't be its own competitor.")
        a, b = sorted((tool_id, competitor_id))
        self.conn.execute(
            "INSERT OR IGNORE INTO tool_competitors (tool_id, competitor_id, created_at) VALUES (?,?,?)",
            (a, b, _now()),
        )
        self.conn.commit()

    def remove_tool_competitor(self, tool_id: int, competitor_id: int) -> None:
        a, b = sorted((tool_id, competitor_id))
        self.conn.execute(
            "DELETE FROM tool_competitors WHERE tool_id=? AND competitor_id=?", (a, b)
        )
        self.conn.commit()

    def list_tool_competitors(self, tool_id: int) -> list[dict]:
        """Every tool curated as a competitor of tool_id, from either side of
        the normalized pair. Only returns approved rows — an unapproved
        competitor has no live profile page to link to."""
        rows = self.conn.execute(
            """SELECT t.* FROM tools t
               JOIN tool_competitors c
                 ON (c.tool_id = ? AND c.competitor_id = t.id)
                 OR (c.competitor_id = ? AND c.tool_id = t.id)
               WHERE t.approved = 1
               ORDER BY t.name""",
            (tool_id, tool_id),
        ).fetchall()
        return [self._tool_to_dict(r) for r in rows]

    def suggest_tool_competitors(self, tool_id: int, limit: int = 8) -> list[dict]:
        """Candidate competitors for the admin edit page's suggestion list,
        ranked by shared-category count (most overlap first, then name).
        Excludes the tool itself and anything already curated as a
        competitor — this is purely a curation speed-up, never the data
        actually rendered on a profile page (see Phase 0: pure tag overlap
        is too noisy to trust unreviewed, given how broad the 15-tag
        taxonomy is)."""
        tool = self.get_tool(tool_id)
        if not tool or not tool["categories"]:
            return []
        existing_ids = {c["id"] for c in self.list_tool_competitors(tool_id)}
        existing_ids.add(tool_id)
        candidates = []
        for row in self.conn.execute("SELECT * FROM tools WHERE approved=1"):
            d = self._tool_to_dict(row)
            if d["id"] in existing_ids:
                continue
            overlap = len(set(d["categories"]) & set(tool["categories"]))
            if overlap:
                d["_overlap"] = overlap
                candidates.append(d)
        candidates.sort(key=lambda d: (-d["_overlap"], d["name"]))
        return candidates[:limit]

    # -- feature comparison data (Phase 4a) ----------------------------------
    # See the tool_features CREATE TABLE comment for the confidence-flag and
    # dedup reasoning. Rendered on the Phase 5 comparison matrix.

    def add_tool_feature(self, tool_id: int, feature_name: str,
                         standalone_available: int = 0, bundled_only: int = 0,
                         notes: str = "", source_url: str = "",
                         needs_verification: int = 0, source: str = "manual",
                         model: str = "") -> int:
        feature_name = feature_name.strip()
        if not feature_name:
            raise ValueError("Feature name is required.")
        now = _now()
        cur = self.conn.execute(
            """INSERT INTO tool_features (tool_id, feature_name, standalone_available,
               bundled_only, notes, source_url, needs_verification, source, model,
               created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (tool_id, feature_name, standalone_available, bundled_only,
             notes.strip(), source_url.strip(), needs_verification, source.strip(),
             model.strip(), now, now),
        )
        self.conn.commit()
        return cur.lastrowid

    def list_tool_features(self, tool_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM tool_features WHERE tool_id=? ORDER BY feature_name", (tool_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_tool_feature(self, feature_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM tool_features WHERE id=?", (feature_id,)).fetchone()
        return dict(row) if row else None

    def update_tool_feature(self, feature_id: int, feature_name: str,
                            standalone_available: int, bundled_only: int,
                            notes: str, source_url: str, needs_verification: int) -> None:
        """Full edit — used by the admin form, including the "mark verified"
        checkbox that clears needs_verification once a human has confirmed
        an LLM-drafted row (or edited it, which implies confirmation)."""
        feature_name = feature_name.strip()
        if not feature_name:
            raise ValueError("Feature name is required.")
        self.conn.execute(
            """UPDATE tool_features SET feature_name=?, standalone_available=?, bundled_only=?,
               notes=?, source_url=?, needs_verification=?, updated_at=? WHERE id=?""",
            (feature_name, standalone_available, bundled_only, notes.strip(),
             source_url.strip(), needs_verification, _now(), feature_id),
        )
        self.conn.commit()

    def delete_tool_feature(self, feature_id: int) -> None:
        self.conn.execute("DELETE FROM tool_features WHERE id=?", (feature_id,))
        self.conn.commit()

    @staticmethod
    def _tool_to_dict(r: sqlite3.Row) -> dict:
        d = dict(r)
        # Sorted at read time, not just at write time — every caller that
        # reads a tool's categories (card grid, profile page, compare matrix,
        # admin table) goes through here, so this is the one place that
        # guarantees alphabetical order regardless of what order a given
        # write path (add/update/quick-edit/bulk-edit) happened to save them in.
        d["categories"] = sorted(json.loads(d.pop("categories_json", "[]") or "[]"))
        return d

    # -- tool categories (the /tools filter pills) --------------------------
    # Unlike article tags, this is a curated vocabulary independent of usage —
    # a category can exist with zero tools tagged to it, ready to assign.

    def list_tool_categories(self) -> list[dict]:
        # Always alphabetical by name, not sort_order (insertion order)—so the
        # filter pills on /tools/software and the rows on /admin/tools/categories
        # self-correct on any future add/rename/delete without a persisted
        # display-order field to keep in sync.
        rows = self.conn.execute(
            "SELECT id, name, description, sort_order FROM tool_categories ORDER BY name COLLATE NOCASE"
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
        if name.lower() == RESERVED_CATEGORY_NAME:
            raise ValueError('"Uncategorized" is reserved for the directory\'s built-in filter and can\'t be used as a category name.')
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
        if new_name.lower() == RESERVED_CATEGORY_NAME:
            raise ValueError('"Uncategorized" is reserved for the directory\'s built-in filter and can\'t be used as a category name.')
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

    def update_benchmark_content(self, benchmark_id: int, name: str, description: str) -> None:
        """Narrow update for the startup seed-sync pass: touches only name and
        description, leaving coverage/pricing untouched so an admin edit made
        directly on /admin/tools/benchmarks survives a re-sync."""
        self.conn.execute(
            "UPDATE benchmarks SET name=?, description=? WHERE id=?",
            (name.strip(), description.strip(), benchmark_id),
        )
        self.conn.commit()

    def delete_benchmark(self, benchmark_id: int) -> None:
        self.conn.execute("DELETE FROM benchmarks WHERE id = ?", (benchmark_id,))
        self.conn.commit()

    # -- communities (the /tools/communities directory) ---------------------
    # Mirrors the tools/tool_categories shape above: categories_json holds the
    # many-to-many relationship inline (no join table), community_categories
    # is just the controlled vocabulary of pills.

    def _find_community_by_normalized_url(self, url: str, exclude_id: int | None = None) -> sqlite3.Row | None:
        target = normalize_url(url)
        if not target:
            return None
        rows = self.conn.execute("SELECT id, name, url, slug FROM communities").fetchall()
        for r in rows:
            if r["id"] == exclude_id:
                continue
            if normalize_url(r["url"]) == target:
                return r
        return None

    def add_community(self, name: str, url: str, demographic: str,
                      cost_band: str, categories: list[str], cost_note: str = "",
                      sponsorship_type: str = "Independent", sponsor_name: str = "",
                      access: str = "", format: str = "", notes: str = "",
                      submitted_by: str = "", approved: int = 0,
                      reach: str = "National", local_markets: str = "",
                      featured: int = 0, advisor: int = 0) -> int:
        dup = self._find_community_by_normalized_url(url)
        if dup:
            raise DuplicateURLError("community", dup["id"], dup["name"], dup["slug"])
        # Domain-derived slug (Phase 2) — see add_tool for the algorithm.
        # Software and Communities each enforce slug uniqueness only within
        # their own table, so a domain shared across a vendor's software
        # listing and its own branded community (e.g. datarails.com) is not
        # a collision — separate namespaces, separate URL prefixes.
        base = _domain_slug_base(url) or _slugify(name)
        if self.conn.execute("SELECT 1 FROM communities WHERE slug=?", (base,)).fetchone():
            base = _domain_slug_full(url) or base
        slug = base
        suffix = 2
        while self.conn.execute("SELECT 1 FROM communities WHERE slug=?", (slug,)).fetchone():
            slug = f"{base}-{suffix}"
            suffix += 1
        now = _now()
        cur = self.conn.execute(
            """INSERT INTO communities (name, slug, url, demographic, cost_band,
               cost_note, sponsorship_type, sponsor_name, access, format, notes,
               categories_json, approved, submitted_by, created_at, updated_at,
               reach, local_markets, featured, advisor)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (name.strip(), slug, url.strip(), demographic.strip(),
             cost_band, cost_note.strip(), sponsorship_type, sponsor_name.strip(),
             access.strip(), format.strip(), notes.strip(), json.dumps(categories),
             approved, submitted_by.strip(), now, now,
             reach, local_markets.strip(), featured, advisor),
        )
        self.conn.commit()
        return cur.lastrowid

    def list_communities(self, approved_only: bool = True) -> list[dict]:
        if approved_only:
            rows = self.conn.execute(
                "SELECT * FROM communities WHERE approved=1 ORDER BY name"
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM communities ORDER BY approved, created_at DESC"
            ).fetchall()
        return [self._community_to_dict(r) for r in rows]

    def get_community(self, community_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM communities WHERE id=?", (community_id,)).fetchone()
        return self._community_to_dict(row) if row else None

    def get_community_by_slug(self, slug: str) -> dict | None:
        """Used by the public profile page (/tools/communities/<slug>). Only
        returns approved rows — an unapproved/pending community has no live
        directory listing, so its profile shouldn't be reachable either."""
        row = self.conn.execute(
            "SELECT * FROM communities WHERE slug=? AND approved=1", (slug,)
        ).fetchone()
        return self._community_to_dict(row) if row else None

    def update_community(self, community_id: int, name: str, url: str,
                         demographic: str, cost_band: str, categories: list[str],
                         cost_note: str = "", sponsorship_type: str = "Independent",
                         sponsor_name: str = "", access: str = "", format: str = "",
                         notes: str = "", reach: str = "National",
                         local_markets: str = "", featured: int = 0,
                         advisor: int = 0) -> None:
        # Only check when the URL is actually changing — see update_tool for why.
        current = self.get_community(community_id)
        if current and normalize_url(url) != normalize_url(current["url"]):
            dup = self._find_community_by_normalized_url(url, exclude_id=community_id)
            if dup:
                raise DuplicateURLError("community", dup["id"], dup["name"], dup["slug"])
        self.conn.execute(
            """UPDATE communities SET name=?, url=?, demographic=?, cost_band=?,
               cost_note=?, sponsorship_type=?, sponsor_name=?, access=?, format=?,
               notes=?, categories_json=?, updated_at=?, reach=?, local_markets=?,
               featured=?, advisor=? WHERE id=?""",
            (name.strip(), url.strip(), demographic.strip(), cost_band,
             cost_note.strip(), sponsorship_type, sponsor_name.strip(), access.strip(),
             format.strip(), notes.strip(), json.dumps(categories), _now(),
             reach, local_markets.strip(), featured, advisor, community_id),
        )
        self.conn.commit()

    def update_community_content(self, community_id: int, name: str, notes: str = "") -> None:
        """Narrow update for scripts/seed_communities.py's re-sync pass (and the startup
        seeder): touches only name and notes — the two fields sourced straight from the
        underlying research, same role as name/description for tools and benchmarks.
        `advisor` re-syncs from the same COMMUNITIES source list too, mirroring
        tools.advisor exactly, but via a separate direct UPDATE in the caller
        (webapp/app.py's _seed_toolbox, scripts/seed_communities.py's main) —
        not through this method. demographic/cost_band/cost_note/
        sponsorship_type/sponsor_name/access/format/categories_json/approved/
        reach/local_markets/featured are admin-owned, edited at
        /admin/tools/communities, and never touched here — otherwise an admin's edit
        would get silently reverted on the next deploy's re-sync."""
        self.conn.execute(
            "UPDATE communities SET name=?, notes=?, updated_at=? WHERE id=?",
            (name.strip(), notes.strip(), _now(), community_id),
        )
        self.conn.commit()

    def update_community_screenshot(self, community_id: int, screenshot_url: str, screenshot_is_product: int) -> None:
        """Legacy narrow update, kept for pre-Phase-E callers/tests only —
        mirrors update_tool_screenshot exactly, including the "DO NOT call
        from the admin edit-form submit path" warning in its docstring; see
        there for the 2026-08 incident this caused. update_community_screenshot_url
        below is what the live app actually calls now."""
        self.conn.execute(
            "UPDATE communities SET screenshot_url=?, screenshot_is_product=?, screenshot_captured_at='', "
            "updated_at=? WHERE id=?",
            (screenshot_url.strip(), screenshot_is_product, _now(), community_id),
        )
        self.conn.commit()

    def update_community_screenshot_url(self, community_id: int, screenshot_url: str) -> None:
        """Narrow update for the admin edit form's homepage Screenshot URL
        field (Phase E fix) — mirrors update_tool_screenshot_url exactly,
        deliberately not touching screenshot_is_product."""
        self.conn.execute(
            "UPDATE communities SET screenshot_url=?, screenshot_captured_at='', updated_at=? WHERE id=?",
            (screenshot_url.strip(), _now(), community_id),
        )
        self.conn.commit()

    def set_community_screenshot_capture(self, community_id: int, screenshot_url: str) -> None:
        """Records an automated homepage capture — mirrors
        set_tool_screenshot_capture exactly, including always-homepage-only
        (screenshot_is_product=0) by design."""
        self.conn.execute(
            "UPDATE communities SET screenshot_url=?, screenshot_is_product=0, screenshot_captured_at=?, "
            "updated_at=? WHERE id=?",
            (screenshot_url.strip(), _now(), _now(), community_id),
        )
        self.conn.commit()

    def update_community_app_screenshot_source(self, community_id: int, app_screenshot_source_url: str) -> None:
        """Narrow update for the app-screenshot source URL field (Phase E) —
        mirrors update_tool_app_screenshot_source exactly."""
        self.conn.execute(
            "UPDATE communities SET app_screenshot_source_url=?, updated_at=? WHERE id=?",
            (app_screenshot_source_url.strip(), _now(), community_id),
        )
        self.conn.commit()

    def set_community_app_screenshot(self, community_id: int, app_screenshot_url: str) -> None:
        """Records an app screenshot (auto-captured or uploaded) — mirrors
        set_tool_app_screenshot exactly."""
        self.conn.execute(
            "UPDATE communities SET app_screenshot_url=?, app_screenshot_captured_at=?, "
            "updated_at=? WHERE id=?",
            (app_screenshot_url.strip(), _now(), _now(), community_id),
        )
        self.conn.commit()

    def set_community_logo(self, community_id: int, logo_path: str) -> None:
        """Records a downloaded-and-stored logo asset — mirrors set_tool_logo
        exactly (Phase D backfill, scripts/backfill_logos.py)."""
        self.conn.execute(
            "UPDATE communities SET logo_path=?, updated_at=? WHERE id=?",
            (logo_path.strip(), _now(), community_id),
        )
        self.conn.commit()

    def approve_community(self, community_id: int) -> None:
        self.conn.execute("UPDATE communities SET approved=1 WHERE id=?", (community_id,))
        self.conn.commit()

    def count_pending_communities(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM communities WHERE approved=0").fetchone()[0]

    def delete_community(self, community_id: int, admin_id: Optional[int] = None,
                          action: str = "delete") -> None:
        """Hard-deletes a communities row (no soft-delete column exists — see
        the communities CREATE TABLE). Same snapshot-before-delete audit
        contract as delete_tool above, and the same reasoning for logging
        inside this method rather than at each call site: the single-row
        admin Delete button, bulk delete, and a pending submission's Reject
        all route through here."""
        row = self.get_community(community_id)
        self.conn.execute("DELETE FROM communities WHERE id=?", (community_id,))
        self.conn.execute("DELETE FROM community_profiles WHERE community_id=?", (community_id,))
        self.conn.execute("DELETE FROM field_reviews WHERE entity_type='community' AND entity_id=?", (community_id,))
        self.conn.execute(
            "DELETE FROM community_competitors WHERE community_id=? OR competitor_id=?",
            (community_id, community_id),
        )
        if row:
            detail = f"{row['name']} | {row['url']} | categories: {', '.join(row['categories'])}"
            self.conn.execute(
                "INSERT INTO community_audit_log (admin_id, action, item_id, detail, created_at) "
                "VALUES (?,?,?,?,?)",
                (admin_id, action, community_id, detail, _now()),
            )
        self.conn.commit()

    def list_community_audit_log(self, limit: int = 500) -> list[dict]:
        rows = self.conn.execute(
            """SELECT a.*, u.username AS admin_username, u.name AS admin_name
               FROM community_audit_log a LEFT JOIN users u ON u.id = a.admin_id
               ORDER BY a.created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def _community_to_dict(r: sqlite3.Row) -> dict:
        d = dict(r)
        # Same read-time sort as Library._tool_to_dict — guarantees alphabetical
        # order for every caller regardless of write-time order.
        d["categories"] = sorted(json.loads(d.pop("categories_json", "[]") or "[]"))
        return d

    # -- competitor cross-links ("similar communities") ----------------------
    # Exact mirror of add_tool_competitor/remove_tool_competitor/
    # list_tool_competitors/suggest_tool_competitors above — see those
    # docstrings and the community_competitors CREATE TABLE comment for the
    # shared reasoning; not deduplicated into a shared helper because this
    # codebase keeps Software and Communities schema/routes independent.

    def add_community_competitor(self, community_id: int, competitor_id: int) -> None:
        if community_id == competitor_id:
            raise ValueError("A community can't be its own competitor.")
        a, b = sorted((community_id, competitor_id))
        self.conn.execute(
            "INSERT OR IGNORE INTO community_competitors (community_id, competitor_id, created_at) VALUES (?,?,?)",
            (a, b, _now()),
        )
        self.conn.commit()

    def remove_community_competitor(self, community_id: int, competitor_id: int) -> None:
        a, b = sorted((community_id, competitor_id))
        self.conn.execute(
            "DELETE FROM community_competitors WHERE community_id=? AND competitor_id=?", (a, b)
        )
        self.conn.commit()

    def list_community_competitors(self, community_id: int) -> list[dict]:
        """Every community curated as similar to community_id, from either
        side of the normalized pair. Only returns approved rows — an
        unapproved community has no live profile page to link to."""
        rows = self.conn.execute(
            """SELECT c.* FROM communities c
               JOIN community_competitors x
                 ON (x.community_id = ? AND x.competitor_id = c.id)
                 OR (x.competitor_id = ? AND x.community_id = c.id)
               WHERE c.approved = 1
               ORDER BY c.name""",
            (community_id, community_id),
        ).fetchall()
        return [self._community_to_dict(r) for r in rows]

    def suggest_community_competitors(self, community_id: int, limit: int = 8) -> list[dict]:
        """Candidate similar communities for the admin edit page's suggestion
        list, ranked by shared-category count. Same pure-tag-overlap
        curation speed-up as suggest_tool_competitors — never the data
        actually rendered on a profile page."""
        community = self.get_community(community_id)
        if not community or not community["categories"]:
            return []
        existing_ids = {c["id"] for c in self.list_community_competitors(community_id)}
        existing_ids.add(community_id)
        candidates = []
        for row in self.conn.execute("SELECT * FROM communities WHERE approved=1"):
            d = self._community_to_dict(row)
            if d["id"] in existing_ids:
                continue
            overlap = len(set(d["categories"]) & set(community["categories"]))
            if overlap:
                d["_overlap"] = overlap
                candidates.append(d)
        candidates.sort(key=lambda d: (-d["_overlap"], d["name"]))
        return candidates[:limit]

    # -- community profiles (deep qualitative read per community) -----------
    # 1:1 with communities via community_id; see the CREATE TABLE comment in
    # _SCHEMA for why this stays an upsert-by-PK rather than a SQL FK.

    def get_community_profile(self, community_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM community_profiles WHERE community_id=?", (community_id,)
        ).fetchone()
        return dict(row) if row else None

    def upsert_community_profile(self, community_id: int, ideal_member: str = "",
                                 anti_fit: str = "", value_prop: str = "",
                                 format_reality: str = "", engagement_level: str = "",
                                 sponsor_relationship_note: str = "",
                                 application_friction: str = "", cost_value_verdict: str = "",
                                 notable_members: str = "", founded_year: Optional[int] = None,
                                 public_criticism: str = "", verdict_summary: str = "",
                                 low_confidence: int = 0, business_model: str = "",
                                 primary_purpose: str = "", cpe_eligible: str = "",
                                 platform_type: str = "", meeting_format: str = "",
                                 event_style: str = "", seniority_band: str = "",
                                 resources_included: str = "", needs_review: int = 0,
                                 stage_focus: str = "", jobs_program: str = "",
                                 team_or_individual: str = "") -> None:
        """Insert or fully replace a community's profile row. There's no partial
        update here (unlike update_community_content's narrow sync) — the admin
        edit form always submits every field, generated or hand-written.

        The Recommender's controlled-vocabulary `*_tags` columns (seniority_band_
        tags, cpe_eligible_tags, platform_type_tags, function_tags, looking_for_
        tags, programming_tags, paid_free_tags, industry_tags) were dropped
        entirely once the quiz they existed for was replaced by the Chat
        Matchmaker (which reads these free-text columns directly, needing no
        controlled vocabulary) — removed by Brian's explicit call rather than
        left as dead weight, alongside the admin panel that edited them and
        scripts/backfill_community_weight_tags.py. The four *_tags columns
        retired even earlier than that (primary_purpose_tags,
        resources_included_tags, meeting_format_tags, event_style_tags) went
        the same way in the same migration."""
        self.conn.execute(
            """INSERT INTO community_profiles
               (community_id, ideal_member, anti_fit, value_prop, format_reality,
                engagement_level, sponsor_relationship_note, application_friction,
                cost_value_verdict, notable_members, founded_year, public_criticism,
                verdict_summary, low_confidence, updated_at, business_model,
                primary_purpose, cpe_eligible, platform_type, meeting_format,
                event_style, seniority_band, resources_included, needs_review,
                stage_focus, jobs_program, team_or_individual)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(community_id) DO UPDATE SET
                 ideal_member=excluded.ideal_member, anti_fit=excluded.anti_fit,
                 value_prop=excluded.value_prop, format_reality=excluded.format_reality,
                 engagement_level=excluded.engagement_level,
                 sponsor_relationship_note=excluded.sponsor_relationship_note,
                 application_friction=excluded.application_friction,
                 cost_value_verdict=excluded.cost_value_verdict,
                 notable_members=excluded.notable_members,
                 founded_year=excluded.founded_year,
                 public_criticism=excluded.public_criticism,
                 verdict_summary=excluded.verdict_summary,
                 low_confidence=excluded.low_confidence,
                 updated_at=excluded.updated_at,
                 business_model=excluded.business_model,
                 primary_purpose=excluded.primary_purpose,
                 cpe_eligible=excluded.cpe_eligible,
                 platform_type=excluded.platform_type,
                 meeting_format=excluded.meeting_format,
                 event_style=excluded.event_style,
                 seniority_band=excluded.seniority_band,
                 resources_included=excluded.resources_included,
                 needs_review=excluded.needs_review,
                 stage_focus=excluded.stage_focus, jobs_program=excluded.jobs_program,
                 team_or_individual=excluded.team_or_individual""",
            (community_id, ideal_member.strip(), anti_fit.strip(), value_prop.strip(),
             format_reality.strip(), engagement_level.strip(), sponsor_relationship_note.strip(),
             application_friction.strip(), cost_value_verdict.strip(), notable_members.strip(),
             founded_year, public_criticism.strip(), verdict_summary.strip(),
             low_confidence, _now(), business_model.strip(),
             primary_purpose.strip(), cpe_eligible.strip(), platform_type.strip(),
             meeting_format.strip(), event_style.strip(), seniority_band.strip(),
             resources_included.strip(), needs_review,
             stage_focus.strip(), jobs_program.strip(), team_or_individual.strip()),
        )
        self.conn.commit()

    def update_community_profile_research_fields(
        self, community_id: int, *, founded_year: Optional[int] = None,
        notable_members: Optional[str] = None, low_confidence: Optional[int] = None,
        anti_fit: Optional[str] = None, sponsor_relationship_note: Optional[str] = None,
        public_criticism: Optional[str] = None, needs_review: Optional[int] = None,
        stage_focus: Optional[str] = None, jobs_program: Optional[str] = None,
        team_or_individual: Optional[str] = None,
    ) -> None:
        """Narrow, partial update for a deepened-research pass on a subset of
        fields (e.g. a later research round that only re-covers a few fields
        rather than the whole profile) — unlike upsert_community_profile,
        which always fully replaces every column, this only SETs the columns
        whose keyword argument was actually passed (not None), leaving every
        other field on the row untouched. A no-op on a field is "omit the
        argument," not "pass None" — every parameter here is a column that
        can legitimately hold NULL/empty (`founded_year` in particular), so
        there's no way to distinguish "leave alone" from "set to null" other
        than by omission. Caller is responsible for not passing None for a
        field it actually wants nulled out; today's only caller
        (scripts/patch_round3_community_profiles.py) never needs to."""
        fields = {
            "founded_year": founded_year, "notable_members": notable_members,
            "low_confidence": low_confidence, "anti_fit": anti_fit,
            "sponsor_relationship_note": sponsor_relationship_note,
            "public_criticism": public_criticism, "needs_review": needs_review,
            "stage_focus": stage_focus, "jobs_program": jobs_program,
            "team_or_individual": team_or_individual,
        }
        fields = {k: v for k, v in fields.items() if v is not None}
        if not fields:
            return
        set_clause = ", ".join(f"{col}=?" for col in fields)
        values = [v.strip() if isinstance(v, str) else v for v in fields.values()]
        self.conn.execute(
            f"UPDATE community_profiles SET {set_clause}, updated_at=? WHERE community_id=?",
            (*values, _now(), community_id),
        )
        self.conn.commit()

    def mark_community_profile_reviewed(self, community_id: int) -> None:
        """Clear `needs_review` once Brian has personally read/approved a
        profile — the admin list's "Mark reviewed" action. A no-op (not an
        error) if the profile row doesn't exist yet."""
        self.conn.execute(
            "UPDATE community_profiles SET needs_review=0, updated_at=? WHERE community_id=?",
            (_now(), community_id),
        )
        self.conn.commit()

    def count_communities_needing_review(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM community_profiles WHERE needs_review=1"
        ).fetchone()[0]

    def community_profile_needs_review_ids(self) -> set[int]:
        """Which community_ids currently have needs_review=1 — used by the
        admin communities list to badge/filter rows without joining the full
        community_profiles row per community."""
        return {r[0] for r in self.conn.execute(
            "SELECT community_id FROM community_profiles WHERE needs_review=1"
        ).fetchall()}

    # -- community gap submissions (Phase 5: native gap-collection) ---------

    def record_community_view(self, session_id: str, community_id: int) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO community_profile_views (session_id, community_id, viewed_at)
               VALUES (?,?,?)""",
            (session_id, community_id, _now()),
        )
        self.conn.commit()

    def get_viewed_community_ids(self, session_id: str, limit: int = 20) -> list[int]:
        if not session_id:
            return []
        rows = self.conn.execute(
            """SELECT community_id FROM community_profile_views
               WHERE session_id=? ORDER BY viewed_at DESC LIMIT ?""",
            (session_id, limit),
        ).fetchall()
        return [r[0] for r in rows]

    def add_community_gap_submission(self, current_communities: str = "", gaps: str = "",
                                     looking_for: str = "", search_context_json: str = "",
                                     viewed_community_ids_json: str = "[]",
                                     closest_community_id: Optional[int] = None,
                                     email: str = "", submission_type: str = "gap") -> int:
        cur = self.conn.execute(
            """INSERT INTO community_gap_submissions
               (current_communities, gaps, looking_for, search_context_json,
                viewed_community_ids_json, closest_community_id, email, reviewed,
                created_at, submission_type)
               VALUES (?,?,?,?,?,?,?,0,?,?)""",
            (current_communities.strip(), gaps.strip(), looking_for.strip(),
             search_context_json, viewed_community_ids_json, closest_community_id,
             email.strip(), _now(), submission_type),
        )
        self.conn.commit()
        return cur.lastrowid

    def list_community_gap_submissions(self, reviewed: bool | None = None,
                                       limit: int = 200) -> list[dict]:
        where, params = "", []
        if reviewed is not None:
            where = "WHERE g.reviewed=?"
            params.append(1 if reviewed else 0)
        rows = self.conn.execute(
            f"""SELECT g.*, c.name AS closest_community_name
                FROM community_gap_submissions g
                LEFT JOIN communities c ON c.id = g.closest_community_id
                {where}
                ORDER BY g.created_at DESC LIMIT ?""",
            params + [limit],
        ).fetchall()
        return [dict(r) for r in rows]

    def community_gap_counts(self, since: str = "") -> dict[str, int]:
        """Total and unreviewed counts, each scoped to `since` if given —
        same shape/reasoning as ask_feedback_counts, but only two buckets
        (there's no per-rating breakdown here)."""
        where, params = "", []
        if since:
            where = "WHERE created_at >= ?"
            params = [since]
        total = self.conn.execute(
            f"SELECT COUNT(*) FROM community_gap_submissions {where}", params
        ).fetchone()[0]
        unreviewed_where = f"{where} AND reviewed=0" if where else "WHERE reviewed=0"
        unreviewed = self.conn.execute(
            f"SELECT COUNT(*) FROM community_gap_submissions {unreviewed_where}", params
        ).fetchone()[0]
        return {"total": total, "unreviewed": unreviewed}

    def toggle_community_gap_reviewed(self, submission_id: int) -> None:
        self.conn.execute(
            "UPDATE community_gap_submissions SET reviewed = 1 - reviewed WHERE id=?",
            (submission_id,),
        )
        self.conn.commit()

    # -- community categories (the /tools/communities filter pills) ---------

    def list_community_categories(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, name, description, sort_order FROM community_categories ORDER BY sort_order, name"
        ).fetchall()
        counts: dict[str, int] = {}
        for (cj,) in self.conn.execute("SELECT categories_json FROM communities"):
            for c in json.loads(cj) or []:
                counts[c] = counts.get(c, 0) + 1
        result = []
        for r in rows:
            d = dict(r)
            d["community_count"] = counts.get(d["name"], 0)
            result.append(d)
        return result

    def add_community_category(self, name: str, description: str = "") -> int:
        name, description = name.strip(), description.strip()
        if not name:
            raise ValueError("Category name is required.")
        if name.lower() == RESERVED_CATEGORY_NAME:
            raise ValueError('"Uncategorized" is reserved for the directory\'s built-in filter and can\'t be used as a category name.')
        existing = self.conn.execute(
            "SELECT 1 FROM community_categories WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchone()
        if existing:
            raise ValueError(f'A category named "{name}" already exists.')
        next_order = self.conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM community_categories"
        ).fetchone()[0]
        cur = self.conn.execute(
            "INSERT INTO community_categories (name, description, sort_order) VALUES (?,?,?)",
            (name, description, next_order),
        )
        self.conn.commit()
        return cur.lastrowid

    def rename_community_category(self, category_id: int, new_name: str, description: str = "") -> int:
        """Rename/re-describe a category, cascading the name change onto every
        community that has it. Returns the number of communities whose
        categories_json changed. Raises ValueError on a name collision."""
        new_name, description = new_name.strip(), description.strip()
        if not new_name:
            raise ValueError("Category name is required.")
        if new_name.lower() == RESERVED_CATEGORY_NAME:
            raise ValueError('"Uncategorized" is reserved for the directory\'s built-in filter and can\'t be used as a category name.')
        row = self.conn.execute(
            "SELECT name FROM community_categories WHERE id = ?", (category_id,)
        ).fetchone()
        if not row:
            raise ValueError("Category not found.")
        old_name = row["name"]
        if new_name.lower() != old_name.lower():
            collision = self.conn.execute(
                "SELECT 1 FROM community_categories WHERE name = ? COLLATE NOCASE AND id != ?",
                (new_name, category_id),
            ).fetchone()
            if collision:
                raise ValueError(f'A category named "{new_name}" already exists.')
        self.conn.execute(
            "UPDATE community_categories SET name=?, description=? WHERE id=?",
            (new_name, description, category_id),
        )
        changed = 0
        if new_name != old_name:
            for c in self.conn.execute(
                "SELECT id, categories_json FROM communities WHERE categories_json LIKE ?", (f'%"{old_name}"%',)
            ).fetchall():
                cats = json.loads(c["categories_json"]) or []
                if old_name not in cats:
                    continue
                updated = sorted(set(new_name if x == old_name else x for x in cats))
                self.conn.execute(
                    "UPDATE communities SET categories_json=? WHERE id=?",
                    (json.dumps(updated), c["id"]),
                )
                changed += 1
        self.conn.commit()
        return changed

    def delete_community_category(self, category_id: int) -> int:
        """Delete a category and strip it from every community that has it.
        Returns the number of communities changed."""
        row = self.conn.execute(
            "SELECT name FROM community_categories WHERE id = ?", (category_id,)
        ).fetchone()
        if not row:
            return 0
        name = row["name"]
        self.conn.execute("DELETE FROM community_categories WHERE id = ?", (category_id,))
        changed = 0
        for c in self.conn.execute(
            "SELECT id, categories_json FROM communities WHERE categories_json LIKE ?", (f'%"{name}"%',)
        ).fetchall():
            cats = json.loads(c["categories_json"]) or []
            if name not in cats:
                continue
            kept = [x for x in cats if x != name]
            self.conn.execute(
                "UPDATE communities SET categories_json=? WHERE id=?",
                (json.dumps(kept), c["id"]),
            )
            changed += 1
        self.conn.commit()
        return changed

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
                            cost_usd: float = 0.0, rewrite_input_tokens: int = 0,
                            rewrite_output_tokens: int = 0,
                            rewrite_cost_usd: float = 0.0,
                            embed_input_tokens: int = 0, embed_cost_usd: float = 0.0,
                            citations: Optional[list[dict]] = None) -> int:
        """Record one Ask turn. Backs all three surfaces (admin report, a
        user's own history, and the public community view) from one row.
        `conversation_id` groups follow-up turns; pass "" on the first turn of
        a conversation and the caller fills it in with str(id) after insert.
        `cost_usd` is the turn TOTAL (answer + any query-rewrite call + any
        query-time embedding call for hybrid retrieval); the rewrite_* and
        embed_* args break out each call's share of it. (embed_* here is the
        user-cap cost of embedding the QUESTION — a different thing from
        article_embeddings.cost_usd, which is Brian's embed-on-save overhead
        and never touches this table.)
        `citations` is the turn's API-verified cited-source list
        ([{n, title, url, type, article_id?}] — article_id only on
        library-type entries), stored as a snapshot: feed and web sources are
        transient, so the persisted title/url IS the record and is never
        re-resolved later."""
        now = _now()
        cur = self.conn.execute(
            """INSERT INTO ask_questions
               (conversation_id, turn_index, user_id, question, answer, model, effort,
                use_library, use_feed, use_web, input_tokens, output_tokens,
                cache_creation_tokens, cache_read_tokens, cost_usd,
                rewrite_input_tokens, rewrite_output_tokens, rewrite_cost_usd,
                embed_input_tokens, embed_cost_usd,
                citations_json, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (conversation_id, turn_index, user_id, question.strip(), answer,
             model, effort, int(use_library), int(use_feed), int(use_web),
             input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens,
             cost_usd, rewrite_input_tokens, rewrite_output_tokens, rewrite_cost_usd,
             embed_input_tokens, embed_cost_usd,
             json.dumps(citations or []), now),
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

    def get_exa_enabled(self) -> bool:
        """Whether Exa should handle FP&A Buddy's web tier (Phase 7 kill
        switch). Defaults to True — Exa stays the live behavior unless
        explicitly toggled off. This is independent of whether EXA_API_KEY is
        actually set; agent.py combines both conditions into one fallback to
        the native web_search_20250305 tool."""
        return self.get_setting("exa_enabled", "1") != "0"

    def set_exa_enabled(self, enabled: bool) -> None:
        self.set_setting("exa_enabled", "1" if enabled else "0")

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

    # -- Chat Matchmaker (Communities/Software) — a dollar cap independent of
    # FP&A Buddy's above, same override-else-default shape. See
    # matchmaker_questions in _SCHEMA for why this is a separate budget line. --

    _DEFAULT_MATCHMAKER_CAP_USD = 2.00  # lower than Ask's $5: no retrieval/web search per turn

    def record_matchmaker_question(self, session_id: str, kind: str, question: str, answer: str,
                                   model: str, user_id: int | None = None,
                                   conversation_id: str = "", turn_index: int = 0,
                                   input_tokens: int = 0, output_tokens: int = 0,
                                   cache_creation_tokens: int = 0, cache_read_tokens: int = 0,
                                   cost_usd: float = 0.0) -> int:
        """Record one matchmaker turn. `conversation_id` groups follow-up turns;
        pass "" on the first turn and the caller fills it in with str(id) after
        insert, mirroring record_ask_question. `user_id` is None for the (most
        common) anonymous case — the public page requires no login — in which
        case `session_id` (the cfo_visitor cookie) is what rate-limiting and
        conversation ownership key off of."""
        now = _now()
        cur = self.conn.execute(
            """INSERT INTO matchmaker_questions
               (kind, conversation_id, turn_index, user_id, session_id, question, answer, model,
                input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens,
                cost_usd, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (kind, conversation_id, turn_index, user_id, session_id, question.strip(), answer, model,
             input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens,
             cost_usd, now),
        )
        row_id = cur.lastrowid
        if not conversation_id:
            self.conn.execute(
                "UPDATE matchmaker_questions SET conversation_id=? WHERE id=?", (str(row_id), row_id)
            )
        self.conn.commit()
        return row_id

    def list_matchmaker_conversation_turns(self, conversation_id: str) -> list[dict]:
        """All turns of one matchmaker conversation in conversation order —
        the server-side source of truth POST /tools/communities/find/chat
        rebuilds follow-up history from, mirroring list_conversation_turns."""
        if not conversation_id:
            return []
        rows = self.conn.execute(
            "SELECT * FROM matchmaker_questions WHERE conversation_id=? ORDER BY turn_index, id",
            (conversation_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def matchmaker_cost_this_month(self, user_id: int) -> float:
        month_start = datetime.now(timezone.utc).strftime("%Y-%m-01")
        row = self.conn.execute(
            "SELECT COALESCE(SUM(cost_usd),0) FROM matchmaker_questions WHERE user_id=? AND created_at >= ?",
            (user_id, month_start),
        ).fetchone()
        return float(row[0])

    def matchmaker_cost_this_month_session(self, session_id: str) -> float:
        """Same as matchmaker_cost_this_month but keyed by the anonymous
        cfo_visitor session cookie rather than a logged-in user_id — the cap
        that actually applies on the public, no-login /tools/communities/find
        page for the common case of an unauthenticated visitor."""
        month_start = datetime.now(timezone.utc).strftime("%Y-%m-01")
        row = self.conn.execute(
            "SELECT COALESCE(SUM(cost_usd),0) FROM matchmaker_questions "
            "WHERE session_id=? AND user_id IS NULL AND created_at >= ?",
            (session_id, month_start),
        ).fetchone()
        return float(row[0])

    def get_default_matchmaker_cap(self) -> float:
        raw = self.get_setting("matchmaker_default_cap_usd")
        try:
            return float(raw) if raw else self._DEFAULT_MATCHMAKER_CAP_USD
        except ValueError:
            return self._DEFAULT_MATCHMAKER_CAP_USD

    def set_default_matchmaker_cap(self, cap_usd: float) -> None:
        self.set_setting("matchmaker_default_cap_usd", str(cap_usd))

    def get_effective_matchmaker_cap(self, user_id: int) -> float:
        """The dollar cap that actually applies to this user this month for
        the matchmaker — their per-user override if set, else the global
        default, mirroring get_effective_ask_cap exactly."""
        row = self.conn.execute("SELECT matchmaker_cap_usd FROM users WHERE id=?", (user_id,)).fetchone()
        if row and row["matchmaker_cap_usd"] is not None:
            return float(row["matchmaker_cap_usd"])
        return self.get_default_matchmaker_cap()

    def set_user_matchmaker_cap(self, user_id: int, cap_usd: float | None) -> None:
        """Set a per-user override, or pass None to clear it (inherit the default)."""
        self.conn.execute("UPDATE users SET matchmaker_cap_usd=? WHERE id=?", (cap_usd, user_id))
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

    def list_conversation_turns(self, conversation_id: str,
                                feedback_user_id: int | None = None) -> list[dict]:
        """All turns of one conversation in conversation order (turn_index,
        then id — the same ordering the history views use). This is the
        server-side source of truth POST /ask rebuilds follow-up history from;
        the follow-up cap counts these rows, never client-supplied turns.
        Pass `feedback_user_id` to also carry that user's existing rating of
        each turn as fb_rating/fb_comment (NULL when unrated), so the resume
        transcript can show feedback state without a second query."""
        if not conversation_id:
            # Legacy pre-conversation_id rows store '' — they're solo turns,
            # not a conversation, and '' must never match them all at once.
            return []
        if feedback_user_id is None:
            rows = self.conn.execute(
                "SELECT * FROM ask_questions WHERE conversation_id=?"
                " ORDER BY turn_index, id",
                (conversation_id,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """SELECT aq.*, f.rating AS fb_rating, f.comment AS fb_comment
                   FROM ask_questions aq
                   LEFT JOIN ask_feedback f
                     ON f.question_id = aq.id AND f.user_id = ?
                   WHERE aq.conversation_id=?
                   ORDER BY aq.turn_index, aq.id""",
                (feedback_user_id, conversation_id),
            ).fetchall()
        return [dict(r) for r in rows]

    def list_recent_conversations(self, user_id: int, limit: int = 5) -> list[dict]:
        """The user's most recent conversations, newest activity first — the
        /ask resume list. One row per conversation_id: the first question
        (the label), turn count, and last-activity timestamp. Legacy turns
        that predate conversation_id (stored as '') are excluded — with no id
        there is nothing to resume; /ask/history still shows them."""
        rows = self.conn.execute(
            """SELECT aq.conversation_id,
                      COUNT(*) AS turns,
                      MAX(aq.created_at) AS last_at,
                      (SELECT q2.question FROM ask_questions q2
                       WHERE q2.conversation_id = aq.conversation_id
                       ORDER BY q2.turn_index, q2.id LIMIT 1) AS first_question
               FROM ask_questions aq
               WHERE aq.user_id=? AND aq.conversation_id != ''
               GROUP BY aq.conversation_id
               ORDER BY last_at DESC LIMIT ?""",
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

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

    # -- Ask / FP&A Buddy — answer feedback ------------------------------------

    ASK_FEEDBACK_RATINGS = ("helpful", "inaccurate", "not_helpful")

    def get_ask_question(self, question_id: int) -> Optional[dict]:
        """One recorded turn by id — used by the feedback endpoint to verify
        the turn exists and belongs to the rater before recording anything."""
        row = self.conn.execute(
            "SELECT * FROM ask_questions WHERE id=?", (question_id,)
        ).fetchone()
        return dict(row) if row else None

    def record_ask_feedback(self, question_id: int, user_id: int, rating: str,
                            comment: str = "") -> int:
        """Record (or change) one user's rating of one Ask turn. Upserts on
        (question_id, user_id), so re-rating updates the existing row — one
        feedback row per turn per user, never a pile of superseded rows. The
        comment is always overwritten too, so re-rating to 'helpful' clears a
        stale "what was off?" note. Raises ValueError on an unknown rating."""
        if rating not in self.ASK_FEEDBACK_RATINGS:
            raise ValueError(f'Unknown rating "{rating}".')
        cur = self.conn.execute(
            """INSERT INTO ask_feedback (question_id, user_id, rating, comment, created_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(question_id, user_id) DO UPDATE SET
                   rating=excluded.rating, comment=excluded.comment,
                   updated_at=excluded.created_at""",
            (question_id, user_id, rating, (comment or "").strip(), _now()),
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT id FROM ask_feedback WHERE question_id=? AND user_id=?",
            (question_id, user_id),
        ).fetchone()
        return row[0] if row else cur.lastrowid

    def list_ask_feedback(self, rating: str | None = None, limit: int = 200) -> list[dict]:
        """Feedback rows newest first, joined with the rated turn (question,
        answer, model, cost, citations snapshot) and the rater's identity —
        everything the admin triage view shows. Pass `rating` to filter."""
        where, params = "", []
        if rating:
            where = "WHERE f.rating=?"
            params.append(rating)
        rows = self.conn.execute(
            f"""SELECT f.*, u.username AS rater_username, u.name AS rater_name,
                       aq.question, aq.answer, aq.model, aq.effort, aq.cost_usd,
                       aq.conversation_id, aq.turn_index, aq.citations_json,
                       aq.user_id AS asker_user_id
                FROM ask_feedback f
                LEFT JOIN users u ON u.id = f.user_id
                LEFT JOIN ask_questions aq ON aq.id = f.question_id
                {where}
                ORDER BY f.created_at DESC LIMIT ?""",
            params + [limit],
        ).fetchall()
        return [dict(r) for r in rows]

    def ask_feedback_counts(self, since: str = "") -> dict[str, int]:
        """Per-rating counts (every rating key present, 0 when none), optionally
        since an ISO date/datetime prefix — the admin view's stat cards."""
        where, params = "", []
        if since:
            where = "WHERE created_at >= ?"
            params.append(since)
        counts = {r: 0 for r in self.ASK_FEEDBACK_RATINGS}
        for rating, n in self.conn.execute(
            f"SELECT rating, COUNT(*) FROM ask_feedback {where} GROUP BY rating", params
        ).fetchall():
            counts[rating] = n
        return counts

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
def open_library(path: str) -> Iterator[Library]:
    lib = Library(path)
    try:
        yield lib
    finally:
        lib.close()
