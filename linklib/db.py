"""SQLite + FTS5 storage layer for the link library.

This is the durable spine. Everything else (Feedly import, going-forward
capture, web UI, Claude access) reads and writes through here.

The same schema works whether the DB is a local file or a hosted
libSQL/Turso/D1 database later — only the connection changes.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterator, Optional

from .voice_mechanics import normalize_voice_mechanics as _voice_fix
from .voice_mechanics import correction_rule_for as _voice_fix_rule
from .voice_mechanics import norm_for_compare
from .community_profile import PROFILE_LIMITS, coerce_cpe_eligible as _coerce_cpe


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
    in_scope    INTEGER NOT NULL DEFAULT 1,     -- FROZEN (PR 4, "Remove content"
                                                 -- retirement, 2026-09): the enricher no
                                                 -- longer judges audience fit at all — the
                                                 -- archive is hand-curated one article at a
                                                 -- time now, so this scaffolding from the
                                                 -- initial bulk import has nothing left to
                                                 -- do. Production had 0 rows with
                                                 -- in_scope=0 at the time this was retired.
                                                 -- Column kept, not dropped (non-destructive
                                                 -- retirement) — always 1 going forward.
                                                 -- Was: 0 = flagged off-audience for review,
                                                 -- surfaced on the now-removed
                                                 -- /admin/library/review-removals page.
    scope_reason TEXT NOT NULL DEFAULT '',      -- FROZEN alongside in_scope above — was:
                                                 -- why it was flagged in/out of scope.
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
    last_login_at TEXT NOT NULL DEFAULT '',
    -- Set whenever the current password was chosen by someone other than the
    -- account holder (account creation, an admin reset) — cleared the moment
    -- the holder sets their own password. Drives a dismissible nudge banner
    -- only, never a login block. See the migration list's own comment.
    password_change_recommended INTEGER NOT NULL DEFAULT 0
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

-- Resources section on /tools (table name kept as `benchmarks`; the public/admin
-- URLs and page copy were renamed from "Benchmarking" to "Resources" in the
-- admin URL convention PR — URL/copy rename only, no schema change).
-- coverage: 'Private'|'Public'|'Both'.
-- pricing: 'free'|'paid'|'freemium' (only 'paid'/'freemium' render a $ badge).
CREATE TABLE IF NOT EXISTS benchmarks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL DEFAULT '',
    url         TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    coverage    TEXT NOT NULL DEFAULT 'Private',
    pricing     TEXT NOT NULL DEFAULT 'free',
    sort_order  INTEGER NOT NULL DEFAULT 0,
    section     TEXT NOT NULL DEFAULT 'benchmarking'
);

-- The /thought-leadership page's four editorial lists (Writing, Speaking &
-- Events, Podcasts, Press), admin-managed (Phase 1 — see CLAUDE.md). type:
-- 'writing'|'speaking'|'podcast'|'press'. Role/capacity (Host, Co-Chair,
-- Guest, ...) is deliberately not a separate column — it stays free-text
-- inside title, matching how every existing entry already writes it (e.g.
-- "Cash Cycle Demo Day—Co-Chair"). date_label is the display string an
-- admin types (e.g. "Jun 2026"); sort_key is "YYYY-MM" and drives
-- newest-first ordering on the public page — "" floats an item to the top
-- of its section (a standing link with no single date). Since a follow-up
-- fix (see CLAUDE.md), sort_key is no longer a separate hand-typed admin
-- form field — it's derived automatically from date_label on every save
-- (webapp/app.py's _sort_key_from_date_label, "Mon YYYY"/"Month YYYY" ->
-- "YYYY-MM"; unparseable or blank input yields "", the same
-- floats-to-top behavior). It still lives as its own column here because
-- render-order queries need a plain sortable string, not a date_label to
-- reparse on every read. display_order is a stable tiebreaker for items
-- that share a sort_key (or are both undated), preserving whatever order
-- they were added/migrated in rather than leaving ties to SQLite's
-- unspecified row order. needs_synopsis flags a description that's
-- deliberately blank pending research, not skipped by accident. Superseded
-- webapp/thought_leadership_data.py (kept in the repo, unused, as a
-- rollback reference) — see that module's docstring and CLAUDE.md's Phase 1
-- entry for the migration this table replaced it with. One legacy entry
-- (Abacum AI Summit, which has photos — a field this table doesn't carry;
-- see CLAUDE.md) stays hardcoded in webapp/app.py instead of migrating here.
CREATE TABLE IF NOT EXISTS thought_leadership (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    type           TEXT NOT NULL,
    title          TEXT NOT NULL DEFAULT '',
    url            TEXT NOT NULL DEFAULT '',
    venue          TEXT NOT NULL DEFAULT '',
    date_label     TEXT NOT NULL DEFAULT '',
    sort_key       TEXT NOT NULL DEFAULT '',
    description    TEXT NOT NULL DEFAULT '',
    needs_synopsis INTEGER NOT NULL DEFAULT 0,
    display_order  INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT,
    updated_at     TEXT
);

-- Original Content (Original Content Phase 1, 2026-08) — card metadata for
-- the "flagship pieces" row shown on the homepage and /thought-leadership,
-- replacing the hardcoded _TL_FEATURED_CARDS tuple in webapp/app.py, plus
-- the model for any brand-new piece authored going forward with no code
-- change per article. slug is the URL segment under /thought-leadership/
-- (lowercase-hyphen, unique) — collision with the three literal bespoke
-- routes (growth-engine-ratio, ai-hackathon-playbook, netsuite-mcp) is
-- checked in the admin form (Phase 3), not enforced here. body_md is
-- nullable and that nullability is load-bearing: NULL means "card metadata
-- only" — one of the three hand-built bespoke pages renders the actual
-- piece, and since those three rows' slugs are set to match their existing
-- route path segments exactly, the literal routes always win over the
-- generic GET /thought-leadership/{slug} catch-all by registration order,
-- with no separate custom-route column needed. A real markdown string means
-- the shared article template at that catch-all route renders it instead
-- (Phase 2). status is 'draft'|'live' — a draft never appears on the
-- homepage, on /thought-leadership, or at its own canonical URL for a
-- signed-out visitor; a signed-in admin can still preview it there.
-- featured_home selects which live pieces appear in the homepage's flagship
-- row (all live pieces show on /thought-leadership regardless). date_label/
-- sort_key/display_order follow the same convention as thought_leadership
-- above — date_label is the admin-typed display string, sort_key ("YYYY-MM")
-- is derived from it on every save via webapp/app.py's
-- _sort_key_from_date_label (reused verbatim, not reimplemented), blank or
-- unparseable input yields "". Unlike thought_leadership, ordering here is
-- display_order first (a curated card order, not a strict chronological
-- feed) with sort_key only as a tiebreak — see Library.list_original_content.
-- _TL_FEATURED_CARDS itself stays in the repo, unimported, as a rollback
-- reference (same precedent as webapp/thought_leadership_data.py) — see
-- scripts/archive/migrate_original_content.py for the one-time migration that seeds
-- this table from it, and CLAUDE.md's Original Content Phase 1 entry.
CREATE TABLE IF NOT EXISTS original_content (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    slug          TEXT NOT NULL UNIQUE,
    title         TEXT NOT NULL DEFAULT '',
    teaser        TEXT NOT NULL DEFAULT '',
    tag_label     TEXT NOT NULL DEFAULT '',
    link_label    TEXT NOT NULL DEFAULT '',
    body_md       TEXT,
    status        TEXT NOT NULL DEFAULT 'draft',
    featured_home INTEGER NOT NULL DEFAULT 0,
    date_label    TEXT NOT NULL DEFAULT '',
    sort_key      TEXT NOT NULL DEFAULT '',
    display_order INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT,
    updated_at    TEXT
);

-- The AI-surface cards on /how-this-is-built (Original Content, explainers-
-- collection PR) — previously a hardcoded Python tuple, _AI_SURFACES. Same
-- draft/live + display_order shape as original_content above, minus the
-- flagship-card-specific columns (tag_label/link_label/featured_home) this
-- page never needed. body_md is nullable and rendered through the same
-- trusted _render_original_content_markdown() original_content uses (this
-- is admin-authored content, never public input) — a row with a real
-- body_md is reachable at /how-this-is-built/<slug>, same catch-all
-- convention as GET /thought-leadership/{slug}. external_href is the case
-- named in the build brief: an explainer whose own page lives elsewhere
-- (FP&A Buddy's, at /tools/fpa-buddy/how-it-works) — when set, the card
-- links straight there instead of to this table's own /how-this-is-built/
-- <slug> route, and body_md is typically left NULL for that row since
-- there's nothing here to render. A row with neither body_md nor
-- external_href, or with status='draft', renders its card unlinked
-- ("Explainer coming soon.") on the public /how-this-is-built page.
CREATE TABLE IF NOT EXISTS ai_surfaces (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    slug          TEXT NOT NULL UNIQUE,
    title         TEXT NOT NULL DEFAULT '',
    teaser        TEXT NOT NULL DEFAULT '',
    body_md       TEXT,
    external_href TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'draft',
    display_order INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT,
    updated_at    TEXT
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
    added_at    TEXT NOT NULL,
    -- Cached content from a save-time fetch (2026-08 Reader cleanliness
    -- pass) — see the ALTER TABLE migration comment below for why this
    -- replaced the original always-live-fetch design. Both '' until a save
    -- or a manual refresh populates them.
    content       TEXT NOT NULL DEFAULT '',
    content_html  TEXT NOT NULL DEFAULT ''
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

-- The legacy tool_features table (search overhaul Phase 4a — per-feature
-- standalone-vs-bundled availability for a Software entry) lived here until
-- the Feature Taxonomy Phase 1b PR 2 retired it outright: every route,
-- admin section, and public rendering path that read or wrote it was
-- removed in that PR (see CLAUDE.md's "no dead data" note), so this CREATE
-- TABLE is gone too — a fresh DB never creates it. Brian's production DB
-- (which had real rows) drops the table for real via the human-run
-- scripts/drop_legacy_tool_features.py once that PR is deployed and
-- verified; this schema change just stops a brand-new database from ever
-- creating dead weight it would have no code path to fill or read.
-- category_features/tool_feature_links below are the governed replacement.

-- Feature Taxonomy (docs/FEATURE_TAXONOMY.md is canon): governed replacement for
-- tool_features' flat free text, category by category as each is curated — see
-- CLAUDE.md's Feature Taxonomy note for the legacy-coexistence read-time branch.
-- category_id FKs to tool_categories (the existing /tools/software filter-pill
-- vocabulary), NOT a separate feature-only taxonomy — a 2026-08 investigation found
-- the pilot's three category names ("ERP & Accounting", "FP&A Planning", "Close
-- Management") didn't match any live pill, and Brian's resolution was to reuse the
-- real pills (ERP, FP&A) for the first two and add "Close Management" as a genuine
-- new pill for the third, rather than invent a parallel vocabulary. retired_at
-- (nullable) means features are retired, never deleted — a retirement drops a
-- feature from the curated list and its comparison rendering without destroying the
-- historical tool_feature_links rows pointing at it. Name is unique per category
-- (not globally) since the same capability name deliberately recurs across
-- categories by design (§2 of the rules doc) — e.g. "Anomaly Detection" is a
-- legitimate separate row in both ERP and Close Management.
CREATE TABLE IF NOT EXISTS category_features (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id  INTEGER NOT NULL,
    name         TEXT NOT NULL,
    definition   TEXT NOT NULL DEFAULT '',
    pointer_note TEXT NOT NULL DEFAULT '',
    sort_order   INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    retired_at   TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_category_features_category ON category_features(category_id);

-- One row per (tool, feature) — the vendor-specific designations the rules doc
-- (§6) is explicit must live on the LINK, never on the feature itself, since the
-- same feature is native/rules-based at one vendor and an add-on/AI-driven at
-- another. availability is a CHECK constraint, not free text, matching §6's
-- "exactly one of native | add_on" rule (absence of a row IS the third state —
-- "not available" — never a stored value). verified_as_of is required per link
-- (§6: "claims decay fast"); source_url is nullable free text for now, not a
-- source-tier enum — §8's sourcing hierarchy is a scan-tool/reviewer
-- judgment call, not yet schema.
-- note is the internal curation log (quoted vendor copy mixed with reviewer
-- caveats) and is never shown publicly or sent over MCP. public_note is the
-- separate, visitor-facing vendor-specific text Brian curates by hand, often
-- lifting the publishable part out of note; it starts empty for every row and
-- nothing seeds or copies into it automatically.
CREATE TABLE IF NOT EXISTS tool_feature_links (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_id        INTEGER NOT NULL,
    feature_id     INTEGER NOT NULL,
    availability   TEXT NOT NULL DEFAULT 'native' CHECK (availability IN ('native', 'add_on')),
    ai_enabled     INTEGER NOT NULL DEFAULT 0,
    verified_as_of TEXT NOT NULL DEFAULT '',
    note           TEXT NOT NULL DEFAULT '',
    source_url     TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL DEFAULT '',
    public_note    TEXT NOT NULL DEFAULT '',
    UNIQUE(tool_id, feature_id)
);

CREATE INDEX IF NOT EXISTS idx_tool_feature_links_tool ON tool_feature_links(tool_id);
CREATE INDEX IF NOT EXISTS idx_tool_feature_links_feature ON tool_feature_links(feature_id);

-- The review gate (rules doc §9): no proposed change reaches category_features or
-- tool_feature_links without a human approving it here first, regardless of who or
-- what proposed it — an admin's own edit, the (later, Phase 3) recurring AI scan, or
-- a (later, Phase 4) public visitor suggestion. source distinguishes the three;
-- category_id/tool_id are nullable because a brand-new-category origination
-- proposal (Phase 3's "origination mode") has no existing category to point at yet.
-- payload is the proposed change as JSON (shape varies by proposal_type — new
-- feature, new link, designation change, flag/question) since the three sources
-- produce structurally different proposals and a fixed column set can't cover all
-- of them without a wall of nullable columns. articulation is the rules-doc-required
-- "why this belongs" writeup (§9) — required at the UI layer for a public addition
-- proposal, optional for a flag/question, and nullable here since the schema can't
-- enforce a per-proposal-type requirement on its own. submitter_name/submitter_email
-- are nullable now (no public channel exists yet — Phase 4) but present so that
-- phase doesn't need a migration to add them. Approving a queue entry applies its
-- payload through the exact same Library methods a manual admin edit would use
-- (§9: "AI and the public draft; Brian decides"), never a direct table write from
-- the queue-approval code path itself.
CREATE TABLE IF NOT EXISTS feature_review_queue (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    source           TEXT NOT NULL CHECK (source IN ('admin', 'scan', 'public')),
    status           TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'edited', 'denied')),
    category_id      INTEGER,
    tool_id          INTEGER,
    proposal_type    TEXT NOT NULL DEFAULT '',
    payload          TEXT NOT NULL DEFAULT '{}',
    articulation     TEXT NOT NULL DEFAULT '',
    submitter_name   TEXT NOT NULL DEFAULT '',
    submitter_email  TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL,
    resolved_at      TEXT NOT NULL DEFAULT '',
    resolution_note  TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_feature_review_queue_status ON feature_review_queue(status);

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

-- API tokens for the MCP server (/mcp, Phase 1). Each token resolves to a
-- real users.id + its current role — the whole point being that an MCP
-- caller is never anonymous or unmetered the way the legacy flat
-- LINKLIB_SAVE_TOKEN is (see CLAUDE.md's MCP auth bullet). Only token_hash
-- (sha256 hex digest of the plaintext) is stored, never the plaintext
-- itself — same reasoning as password_reset_requests.token_hash above: a
-- leaked DB backup must not hand out usable tokens. Minted/revoked by
-- scripts/mint_api_token.py (human-run via `railway ssh`); no admin UI yet.
CREATE TABLE IF NOT EXISTS api_tokens (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash    TEXT NOT NULL UNIQUE,
    user_id       INTEGER NOT NULL,        -- FK convention -> users.id
    label         TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL DEFAULT '',
    revoked_at    TEXT NOT NULL DEFAULT '', -- '' = active, matching password_reset_requests' convention
    last_used_at  TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_api_tokens_user ON api_tokens(user_id);

-- Durable record of failed outbound-email attempts (contact form, tool
-- submissions, welcome emails, password resets, warm intros). Every send
-- path is best-effort (a broken mailer must never block the underlying DB
-- write), but "best-effort" must not mean "silent" — this table plus the
-- /admin/inbox/email-failures badge is how a broken send actually surfaces instead
-- of only ever appearing in a Railway log line nobody's watching.
CREATE TABLE IF NOT EXISTS email_failures (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    context     TEXT NOT NULL DEFAULT '',   -- e.g. 'contact', 'tool_submission', 'welcome', 'password_reset'
    detail      TEXT NOT NULL DEFAULT '',   -- str(exception)
    created_at  TEXT NOT NULL,
    resolved_at TEXT NOT NULL DEFAULT ''    -- '' = still pending
);

CREATE INDEX IF NOT EXISTS idx_email_failures_resolved ON email_failures(resolved_at);

-- RETIRED, frozen not dropped (2026-09, PR 3) — the "Library Queue" staging
-- mechanism (linklib/queue.py: an ongoing feed scan + a one-time historical
-- sitemap sweep, both landing candidates here for review at
-- /admin/library/queue before promotion into `articles`) was removed
-- outright. A 2026-09-09 production query found 5,508 rows here, 100%
-- dismissed, 0 pending, 0 member submissions ever, dormant since 2026-06-28
-- — the archive now grows by 1-2 articles every few days via the
-- bookmarklet, which doesn't justify an AI-enriched proposal/review
-- pipeline. This is a deliberate retirement of working code, not a bug fix.
-- Every read/write path (Library.add_to_queue/list_queue/queue_count/
-- dismiss_queue_item/remove_from_queue/update_queue_published/
-- promote_queue_item, and linklib/queue.py + linklib/suggest.py in full)
-- is gone from the codebase; this table stays, unread and unwritten, as the
-- historical record of 5,508 real (mostly automated) dismissal decisions —
-- not reconstructible from anywhere else. `/library/submit`'s "suggest an
-- addition" form no longer writes here either — it now sends Brian a plain
-- email notification instead (see webapp/app.py's library_submit route);
-- he reads it and saves the article himself with the bookmarklet.
--
-- Original comment, preserved for context: candidates — from the live feed
-- or a one-time historical sweep — landed here enriched but unsaved, so
-- they could be reviewed before entering the library (and the Ask corpus).
-- URL was the natural key, matching `articles`. `content` (third-party full
-- text) was an internal enrichment/search input only; the resale-safe
-- surface was `summary` + tags. Promoting a row moved it into `articles`,
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
    exa_result_count      INTEGER NOT NULL DEFAULT 0,  -- Exa cost tracking (2026-09): Answer.exa_result_count,
                                                        -- added via migration below (see that migration's comment)
    exa_cost_usd          REAL NOT NULL DEFAULT 0,     -- Exa's share, already inside cost_usd — see
                                                        -- Answer.exa_cost_usd/linklib.agent.retrieve_exa
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
-- downstream uses: admin triage (/admin/fpa-buddy/feedback), a retrieval eval set
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

-- Off-site backup audit trail (Phase O). One row per linklib.backup.
-- backup_now() attempt, success or failure — the actual audit trail for
-- "did the Google Drive backup work." Written from linklib/backup.py
-- itself (not from webapp/app.py call sites) so every trigger path is
-- covered by one code path: the daily Railway Cron Service hitting
-- POST /admin/backup-now, an admin clicking the same route by hand, and
-- the ~18 debounced maybe_backup() call sites in webapp/app.py that fire
-- it as a side effect of a Library/Archive write. Before this table
-- existed, the only record of an attempt was a print() to stdout inside
-- maybe_backup()'s exception handler — invisible to anyone not tailing
-- Railway's runtime logs. Those print() calls stay as a secondary signal;
-- this table is the one the admin UI (/admin/library-backup) reads from.
-- drive_file_id/bytes/row_count are populated on success only; error is
-- populated on failure only. row_count is SELECT COUNT(*) FROM articles
-- against the snapshot at backup time — the sanity check the Phase O
-- investigation recommended, reusing the same check the restore path
-- (/admin/library-backup/upload-db) already runs on upload.
CREATE TABLE IF NOT EXISTS backup_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    status        TEXT NOT NULL,              -- 'success' | 'failure'
    filename      TEXT NOT NULL DEFAULT '',    -- library-YYYYMMDD-HHMMSS.db
    drive_file_id TEXT NOT NULL DEFAULT '',    -- Drive file id (success only)
    bytes         INTEGER NOT NULL DEFAULT 0,
    row_count     INTEGER NOT NULL DEFAULT 0,  -- articles count at backup time
    error         TEXT NOT NULL DEFAULT '',    -- failure only
    created_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_backup_log_created ON backup_log(created_at);

-- Durability audit item 2 (2026-08, elevated): nothing in this app ever ran
-- PRAGMA integrity_check against the live database — corruption would only
-- ever surface at restore time, by which point it had already been
-- propagated into every retained daily/weekly snapshot. linklib.backup.
-- check_integrity() now runs PRAGMA integrity_check plus the FTS5
-- self-check (`INSERT INTO articles_fts(articles_fts) VALUES
-- ('integrity-check')` — the same command RUNBOOK.md §4's restore rehearsal
-- already runs by hand) against the live DB, on the same cadence as the
-- backup itself, immediately before every snapshot — see backup_now()'s
-- docstring for why a failure blocks that night's upload rather than
-- uploading anyway. Shape mirrors backup_log exactly (one row per attempt,
-- 'ok'|'failure', append-only) — same convention, not a new one.
CREATE TABLE IF NOT EXISTS integrity_check_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    status        TEXT NOT NULL,              -- 'ok' | 'failure'
    detail        TEXT NOT NULL DEFAULT '',    -- integrity_check's own output, or the FTS self-check's error
    created_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_integrity_check_log_created ON integrity_check_log(created_at);

-- Durability audit item 3 (2026-08): _JOB_STATE (webapp/app.py) is an
-- in-process dict — the only record of whether the re-enrich job,
-- Historical sweep, or the Reader content backfill last succeeded, failed,
-- or ever ran at all, and a Railway redeploy (or crash) wipes it silently.
-- This table is the durable record, written by all three _JOB_STATE-backed
-- jobs at start (Library.start_job_run) and finish
-- (Library.finish_job_run) — shape mirrors backup_log/integrity_check_log
-- (one row per run, append-only), not a new convention. _JOB_STATE itself
-- is UNCHANGED and still owns live in-request progress (poll-friendly,
-- no DB round trip per tick) — this table is only ever written twice per
-- run (start, finish), never polled during a run.
CREATE TABLE IF NOT EXISTS job_run_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    job_name      TEXT NOT NULL,              -- 'enrich' | 'backfill' | 'content_backfill'
    status        TEXT NOT NULL,              -- 'running' | 'success' | 'failure' | 'stopped'
    summary       TEXT NOT NULL DEFAULT '',    -- short human-readable counts, e.g. '42/50 enriched'
    error         TEXT NOT NULL DEFAULT '',    -- failure only
    started_at    TEXT NOT NULL,
    finished_at   TEXT NOT NULL DEFAULT ''     -- '' while status='running' (a crash mid-run leaves this
                                                -- empty forever, which is itself informative — see
                                                -- Library.latest_job_run)
);

CREATE INDEX IF NOT EXISTS idx_job_run_log_job_started ON job_run_log(job_name, started_at);

-- Per-article attempt log for the Reader content-structure backfill (Phase
-- 5b): PR #322's extract_reader_html() only ever ran against a live fetch
-- (an unsaved Feed item, or a saved article whose cached content was too
-- thin to use as-is) — the ~4,500 already-saved articles are stored as
-- flattened plain text from the original ingest, since no raw HTML was ever
-- kept for them. This table is the durable, resumable, observable record of
-- re-fetching each one: one row per attempt (a re-run after a stop/crash
-- adds new rows rather than overwriting old ones, so the full history of a
-- flaky source is visible, not just its latest state) — same shape and
-- reasoning as backup_log above. reason is populated on failure only, and is
-- one of extract.assess_extraction_quality's own reason strings ('paywall',
-- 'bot-challenge', 'too-thin'), 'fetch-error' for a request that failed
-- outright (timeout, DNS, non-2xx), or 'defunct-service' for a URL whose
-- host is a known-permanently-discontinued service (linklib.pipeline.
-- _DEFUNCT_SERVICE_DOMAINS — no fetch or Wayback attempt is even made for
-- these, and articles_needing_content_backfill()'s default scope excludes
-- them from future runs entirely) — never a generic message, so failures
-- are groupable/countable by cause on the admin page. A failed attempt never
-- touches articles.content or articles.content_html — see
-- linklib.pipeline.backfill_article_content. `source` ('direct' | 'wayback',
-- added via migration below — see that migration's comment; 'migration' added
-- in the Phase 5b follow-up #2 domain-migration tier — see
-- linklib.pipeline._DOMAIN_MIGRATIONS) distinguishes a Wayback-archived-
-- snapshot or known-domain-migration success from a normal live-fetch
-- success. Attempt COUNT per article (not just latest-attempt state) backs
-- the "needs-manual-review" capped-retry tier (Phase 5b follow-up #2, see
-- Library._manual_review_article_ids) — a URL correction via
-- url_correction_log resets what counts as "since the last correction", so a
-- corrected article's attempt count starts fresh rather than inheriting a
-- pre-correction failure streak forever. `exa_cost_usd` (added via migration
-- below, same as `source` — see that migration's comment) is the real Exa
-- spend this attempt incurred, from the domain-migration and/or
-- Medium-platform tiers (linklib/domain_migration.py, linklib/
-- medium_platform.py) — 0 for an attempt that never reached Exa.
CREATE TABLE IF NOT EXISTS content_refetch_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id    INTEGER NOT NULL,
    status        TEXT NOT NULL,              -- 'success' | 'failure'
    reason        TEXT NOT NULL DEFAULT '',    -- failure only: paywall|bot-challenge|too-thin|fetch-error|defunct-service
    detail        TEXT NOT NULL DEFAULT '',    -- optional extra context (e.g. exception text)
    attempted_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_content_refetch_log_article ON content_refetch_log(article_id);

-- Phase 5b follow-up #2 (retry backoff + manual URL correction): a durable,
-- distinct trace every time an article's stored URL is corrected via the
-- manual-review CSV import (webapp /admin/reader/backfill-content's export/
-- import round trip) — see CLAUDE.md's "every production data change leaves
-- a trace" rule. Deliberately its own table, not folded into
-- content_refetch_log (which records FETCH attempts, not URL edits) or
-- tool_audit_log/community_audit_log (a different entity type). old_url is
-- captured immediately before the UPDATE, same reasoning as those audit
-- logs snapshotting state right before a destructive write. admin_id stays
-- nullable — this app has no per-admin user accounts (a single shared
-- secret, see CLAUDE.md's Authentication & security section), so it's
-- forward-looking only and always NULL today.
CREATE TABLE IF NOT EXISTS url_correction_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id  INTEGER NOT NULL,
    old_url     TEXT NOT NULL DEFAULT '',
    new_url     TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL DEFAULT 'csv-import',
    admin_id    INTEGER,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_url_correction_log_article ON url_correction_log(article_id);

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
-- enrichment that has no `articles.id` yet to attach to — every batch/regen
-- generation script (scripts/regen_ai_drafted_fields.py and friends) is the
-- live example today; the Archive Queue's own pre-save enrichment path used
-- to be another, retired along with the queue itself (2026-09, PR 3) — the
-- API call still cost real money even when a NULL-article_id row's source
-- was never saved anywhere.
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

-- Compare Redesign Phase 2 — the AI overlap/contrast summary shown above the
-- Software/Communities Compare tables. Cached permanently (no TTL), keyed by
-- the exact set of compared entities plus a content hash of what actually
-- went into the prompt (see Library.compare_summary_content_hash) — an edit
-- to any compared entity's underlying fields changes the hash and naturally
-- misses the cache on the next view, with no separate invalidation mechanism
-- needed. `has_unverified` is deliberately NOT stored here: it's derived live
-- from the CURRENT entities' gate state at render time (see
-- webapp.app._cmp_summary_block_html), decoupled from the content-hash key,
-- so a verify-only action (no text edit — the hash is unchanged) doesn't
-- force a wasteful regen but the footnote's unverified-content disclosure
-- still reflects today's real review state, not the state at generation time.
CREATE TABLE IF NOT EXISTS compare_summary_cache (
    entity_type   TEXT NOT NULL,             -- 'tool' | 'community'
    entity_ids    TEXT NOT NULL,             -- sorted, comma-joined entity ids, e.g. "12,47"
    content_hash  TEXT NOT NULL,             -- sha256 of the concatenated prompt input text
    summary       TEXT NOT NULL DEFAULT '',
    model         TEXT NOT NULL DEFAULT '',
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd      REAL NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (entity_type, entity_ids, content_hash)
);

-- Manual, free-text feedback on one cached compare summary — "what was
-- flagged, on which comparison, optional free text" per the Phase 2 spec.
-- No automated action on a submission; Brian reviews the list by hand at
-- /admin/compare-summary-feedback and marks each one reviewed once handled.
-- `summary_text` snapshots the flagged summary verbatim so the review list
-- still shows exactly what was flagged even if that cache row is later
-- regenerated (a content edit changes the hash, which would otherwise orphan
-- the feedback row's own context).
CREATE TABLE IF NOT EXISTS compare_summary_feedback (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type   TEXT NOT NULL DEFAULT '',
    entity_ids    TEXT NOT NULL DEFAULT '',
    content_hash  TEXT NOT NULL DEFAULT '',
    summary_text  TEXT NOT NULL DEFAULT '',
    note          TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL DEFAULT '',
    reviewed_at   TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_compare_summary_feedback_reviewed ON compare_summary_feedback(reviewed_at);

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

-- Citations-API grounding fix, Phase 1b (2026-08) — API-verified citations
-- for an AI-drafted narrative field, shared across entity types/fields
-- rather than a *_citations column per field (a per-field column would
-- have needed migrating off when Description/Community profile joined in
-- Phase 2/3 — see CLAUDE.md's grounding-fix plan). Mirrors field_reviews'
-- shape immediately above: a composite natural key, upsert-on-write (a
-- fresh draft REPLACES the row wholesale — this is current state, not an
-- append-only log the way narrative_review_log is, since there's only
-- ever one live citation set per field at a time). citations_json holds
-- the FULL deduped-by-url list, uncapped — a 5-source display cap is
-- applied only at public-render time (Library.get_entity_citations always
-- returns everything; the caller slices). model + generated_at are the
-- generation run reference (no separate run/log table — Brian's explicit
-- call, since nothing else in this codebase has a run-id concept to
-- reference instead). entity_type is 'tool' today (Phase 1b: Agent
-- taxonomy, field_name='agent_taxonomy'); 'community' joins in Phase 3
-- (one row per community holds the whole shared profile-draft citation
-- set — field_name='community_profile' — not one row per profile field,
-- per the "one shared citation set per profile draft" decision).
CREATE TABLE IF NOT EXISTS entity_citations (
    entity_type    TEXT NOT NULL,
    entity_id      INTEGER NOT NULL,
    field_name     TEXT NOT NULL,
    citations_json TEXT NOT NULL DEFAULT '[]',
    model          TEXT NOT NULL DEFAULT '',
    generated_at   TEXT NOT NULL,
    PRIMARY KEY (entity_type, entity_id, field_name)
);

-- Phase G: explicit "Mark verified" audit trail for the *public-facing*
-- unverified badge on AI-drafted narrative fields (Agent taxonomy note here;
-- Description, Differentiation, and the Community profile draft join it in
-- the Phase G follow-up). Deliberately NOT the same thing as field_reviews
-- above, even though both are "AI-drafted content, review it" tables —
-- field_reviews is a passive by-product of saving the edit form after a
-- Generate click (the standing "the save that follows is what makes
-- reviewed real" principle), written automatically and never surfaced
-- anywhere in the UI. This table backs a *requested* gate instead: a
-- {field}_needs_verification boolean column stays true (and drives a public
-- "unverified" badge on the profile/compare/directory pages) until an admin
-- explicitly clicks "Mark verified" — a distinct action from Save, which a
-- draft the admin never actually scrutinized can otherwise sail through.
-- One shared table with field_type/entity_type discriminators, mirroring
-- tool_audit_log/community_audit_log/backup_log's shape (id/admin_id
-- nullable-FK-to-users/detail/created_at) rather than a
-- {field}_review_log table per field — same reasoning those tables gave for
-- being one generic shape apiece: four near-identical tables for what's
-- structurally one concern isn't worth it. Append-only: re-verifying after a
-- fresh AI draft writes a new row rather than updating one in place, so
-- "who verified this and when" has real history, not just a last-write-wins
-- snapshot — Library.get_latest_narrative_review reads the newest row per
-- (entity_type, field_type, item_id) for display.
CREATE TABLE IF NOT EXISTS narrative_review_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id    INTEGER,
    entity_type TEXT NOT NULL,             -- 'tool' | 'community'
    field_type  TEXT NOT NULL,             -- 'agent_taxonomy' | 'description' | 'differentiation' | 'community_profile'
    item_id     INTEGER NOT NULL,          -- tools.id or communities.id — no REFERENCES, same as tool_audit_log/community_audit_log
    detail      TEXT NOT NULL DEFAULT '',  -- the reviewed text snapshot at verification time
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_narrative_review_log_lookup
    ON narrative_review_log(entity_type, field_type, item_id, created_at);

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
-- is on it" so /admin/tools/software/name-duplicates stops nagging about it. Deleting
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

-- RSS subscription list — the source of truth behind preferred_sites.opml.
--
-- The OPML file is NOT the source of truth anymore; it's a derived cache,
-- regenerated from these two tables by Library.write_opml() on every mutation
-- and again on every app boot. That inversion exists because the file lives
-- inside the Docker image (/app/preferred_sites.opml), which Railway rebuilds
-- on every deploy — anything written there by the running app is destroyed on
-- the next deploy. Regenerating at boot makes that ephemerality irrelevant.
--
-- Three downstream consumers still read the FILE, unmodified (a fourth,
-- queue.scan_feed_into_queue -> the ongoing feed scan into the now-retired
-- Archive Queue, was removed along with the queue itself — 2026-09, PR 3):
--   feed.parse_opml        -> the Reader's Feed view + its Sources tree
--   sources.preferred_domains -> FP&A Buddy's web-search domain allowlist
--   authcheck              -> picks a probe URL per paywalled domain
--
-- Sections are pure grouping: a name and an order, nothing else. The
-- retired Read-only flag (exclude_from_queue) lived on `feeds`, not here —
-- see that table's comment.
CREATE TABLE IF NOT EXISTS feed_sections (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL UNIQUE,
    display_order INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT ''
);

-- One row per RSS/Atom subscription. xml_url is the natural key (the same
-- feed can't be subscribed twice); html_url is the publication's own site,
-- used by preferred_domains, which prefers it over xml_url when building
-- the allowlist.
--
-- exclude_from_queue is RETIRED, frozen not dropped (2026-09, PR 3) —
-- along with the Archive Queue itself (see the library_queue table's own
-- comment above for the full reasoning), the admin "Read only" checkbox,
-- its POST route, and Library.set_feed_excluded/excluded_feed_urls/
-- has_feeds are all gone from the codebase. The column stays, frozen at
-- whatever value each row last had, on the same non-destructive-retirement
-- precedent as has_paywall_cookie elsewhere in this table — nothing reads
-- it any more.
--
-- Original comment, preserved for context: exclude_from_queue replaced the
-- old QUEUE_EXCLUDE_CATEGORIES string-match set (a name-matched env var).
-- It was per-FEED rather than per-section: a section is a display grouping,
-- and "should this source be proposed into the archive queue" was a
-- judgment about the source itself, so one feed in a section could be
-- read-only without dragging its neighbours along. Because it was stored
-- rather than matched on a name, renaming a section (or moving a feed
-- between sections) couldn't silently change which sources reached the
-- queue. Seeded 1 for the two feeds that were in the "News" section,
-- preserving the exact pre-migration behavior.
--
-- NOTE: xml_url is stored and regenerated verbatim. Some feeds carry a
-- subscriber token in the URL; nothing in the add/edit path may normalize,
-- trim, or rewrite it. See the round-trip test in tests/test_feed_management.py.
CREATE TABLE IF NOT EXISTS feeds (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    section_id         INTEGER NOT NULL REFERENCES feed_sections(id),
    name               TEXT NOT NULL,
    xml_url            TEXT NOT NULL UNIQUE,
    html_url           TEXT NOT NULL DEFAULT '',
    exclude_from_queue INTEGER NOT NULL DEFAULT 0,  -- RETIRED (2026-09, PR 3) — frozen, unread
    display_order      INTEGER NOT NULL DEFAULT 0,
    created_at         TEXT NOT NULL DEFAULT ''
);

-- Voice review queue (2026-09) -- see linklib/voice_review_queue.py's module
-- docstring for the full design. Every voice-rule finding, whether an
-- `_voice_fix()` correction already applied at save time (status
-- 'auto_corrected') or a mechanical/typography/holistic scanner finding
-- that couldn't be auto-fixed (status 'open'), lands here for after-the-
-- fact human review rather than being silently applied or silently
-- reported as an aggregate count with no per-item action. row_id is
-- nullable TEXT (a settings-key row has none, same convention
-- voice_db_scan.DbCopyViolation already uses); rule is one of
-- 'buzzword'|'filler'|'performative'|'bare-ampersand'|'spaced-em-dash'|
-- 'invisible-character'|'holistic'. status: 'auto_corrected' (logged, not
-- yet reviewed) -> 'resolved' (Accept/Revert/Edit) or 'open' (a scanner
-- finding with nothing to auto-fix) -> 'resolved' or 'exception' (Accept
-- as exception -- permanent, keyed to this exact table+row_id+column+rule,
-- never a global rule change).
CREATE TABLE IF NOT EXISTS voice_review_queue (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name  TEXT NOT NULL,
    row_id      TEXT,
    column_name TEXT NOT NULL,
    rule        TEXT NOT NULL,
    excerpt     TEXT NOT NULL DEFAULT '',
    before_text TEXT,
    after_text  TEXT,
    status      TEXT NOT NULL DEFAULT 'open',
    created_at  TEXT NOT NULL,
    reviewed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_voice_review_queue_status ON voice_review_queue(status);
CREATE INDEX IF NOT EXISTS idx_voice_review_queue_lookup
    ON voice_review_queue(table_name, column_name, rule, row_id);

-- Globally-approved voice terms (2026-09) — "Always allow" (renamed from
-- "Approve term"/"Allow everywhere") on the review
-- queue. Scoped to `rule='bare-ampersand'` only, on purpose: an ampersand
-- violation is usually a real defined term/name ("Bain & Company"), which
-- is exactly what a global, permanent approval is for; a banned word
-- (`rule='buzzword'`) never gets a global approval from this table at
-- all — Brian's own explicit call ("seamless" stays banned everywhere;
-- allowing it in one specific spot is the row-scoped "Allow once"
-- (renamed from "Allow here") exception on voice_review_queue,
-- `status='exception'`, not a change to
-- what's banned). DB-backed, not source-code, since CI has no route to a
-- live database anyway (same boundary voice_db_scan.py's own module
-- docstring already states) — so this table is read only by the LIVE
-- DB scan (`voice_db_scan.py`), never by the CI-only source-code scan in
-- linklib/voice_review.py's `typography_findings`.
CREATE TABLE IF NOT EXISTS voice_approved_terms (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    term       TEXT NOT NULL,
    rule       TEXT NOT NULL DEFAULT 'bare-ampersand',
    created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_voice_approved_terms_unique
    ON voice_approved_terms(rule, term COLLATE NOCASE);

-- Thin-fetch audit dismissals (2026-09, JS-render grounding fix, Phase 2b) --
-- a per-record, per-field "reviewed and confirmed fine, stop flagging"
-- marker for scripts/audit_thin_fetch_grounding.py, the diagnostic that
-- surfaces Software/Community rows whose stored low_confidence/ai_confident/
-- entity_citations signals still look like the pre-fix JS-render defect
-- (fetch reported "succeeded" on a near-empty page, the model wasn't
-- confident, and no real citations were ever recorded). Verified against
-- real production data (Lumera, since fixed by hand, vs. Paylocity, a
-- confirmed still-open defect) that NEITHER candidate the investigation
-- started with actually works: needs_verification is unreliable (Paylocity
-- already reads needs_verification=0 despite being genuinely broken -- a
-- human can clear that flag without truly re-checking the content), and
-- low_confidence/ai_confident are usually already 0/0 either way (a hand-
-- edit that fixes the text doesn't change either column -- ai_confident is
-- deliberately permanent, see CLAUDE.md's Confidence indicator bullets, and
-- low_confidence was already 0 under the OLD buggy "any non-empty text
-- counts as succeeded" check). There is no existing signal that
-- distinguishes "already fixed" from "still broken" without a bulk
-- regeneration pass (explicitly out of scope -- only 7 records are
-- affected and Brian is fixing them by hand), so this is a genuinely new,
-- small, explicit per-record dismissal -- the same shape as
-- voice_review_queue's own row-scoped "Allow once" exception above, not an
-- attempt to auto-derive "fixed" from data that can't actually tell the
-- two cases apart. field_name is 'description'|'agent_taxonomy' for a tool
-- row or 'community_profile' for the one whole-profile community row (see
-- the audit script's own COMMUNITY_CONFIDENCE_FIELDS comment).
CREATE TABLE IF NOT EXISTS thin_fetch_audit_dismissals (
    entity_type  TEXT NOT NULL,
    entity_id    INTEGER NOT NULL,
    field_name   TEXT NOT NULL,
    dismissed_at TEXT NOT NULL,
    note         TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (entity_type, entity_id, field_name)
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
    # ask_feedback.reviewed is migration-added (see the ALTER TABLE above),
    # not present in the original CREATE TABLE, so — same reasoning as
    # read_later's own index above — it has to live here, not inline in the
    # schema string, or a fresh-DB boot would try to index a column that
    # doesn't exist yet on that first pass.
    "CREATE INDEX IF NOT EXISTS idx_ask_feedback_reviewed ON ask_feedback(reviewed)",
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


def _url_domain_changed(old_url: str, new_url: str) -> bool:
    """True when two URLs resolve to a genuinely different bare domain (via
    _slug_host — lowercased, www-stripped), used to flag a manual logo
    override as stale on a real site change (see update_tool/update_community)
    without tripping on a path/query/scheme-only edit to the same site."""
    old_host, new_host = _slug_host(old_url), _slug_host(new_url)
    return bool(old_host) and bool(new_host) and old_host != new_host


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
            # Audience-scope review (FROZEN, PR 4 "Remove content" retirement,
            # 2026-09 — see the articles.in_scope column comment above). Kept
            # only so an older DB still gets these two columns on upgrade.
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
            # ones. scripts/archive/backfill_community_geo.py is the one-off pass that
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
            # scripts/archive/import_community_profiles.py).
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
            # can now be LLM-drafted (originally generate_tool_features,
            # which also returned feature rows alongside the agent_taxonomy
            # summary at the time this migration was added — that function
            # is now generate_tool_agent_taxonomy, agent-taxonomy-only, since
            # the Feature Taxonomy Phase 1b PR 2 retired the feature-row half
            # along with the tool_features table it wrote to) as well as
            # hand-typed, so it needs its own needs_verification tracking.
            # Defaults to 0 (verified) so existing hand-typed notes aren't
            # retroactively flagged.
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
            # boot one; see scripts/archive/migrate_app_screenshot_from_product_flag.py)
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
            # Phase G PR 2: Description and Differentiation join Agent taxonomy
            # (agent_taxonomy_needs_verification above) with their own
            # needs_verification flags, gated by the same narrative_review_log
            # "Mark verified" pattern PR 1 established. Both default to 0 so
            # existing content isn't retroactively flagged — same reasoning as
            # agent_taxonomy_needs_verification's own migration, and consistent
            # with the decision not to backfill field_reviews history into
            # narrative_review_log (see CLAUDE.md's Phase G note). The
            # Community profile draft does NOT get an equivalent
            # `community_profiles` column here — it reuses the pre-existing
            # `community_profiles.needs_review` flag instead (see
            # set_tool_agent_taxonomy_draft's sibling logic in the edit-submit
            # routes below for how these get set to 1).
            "ALTER TABLE tools ADD COLUMN description_needs_verification INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE tools ADD COLUMN differentiation_needs_verification INTEGER NOT NULL DEFAULT 0",
            # Phase P (edit-page layout reorg): the Software edit page's
            # "How this differs from the competition" field is relabeled
            # "Competitive differentiation" to match the new "Competition"
            # section heading, so the backing column follows the same name
            # rather than leaving code and UI copy permanently mismatched.
            # SQLite's RENAME COLUMN raises OperationalError (no such column)
            # on a DB that already ran this rename on a prior boot — caught
            # by the same try/except every ADD COLUMN line above relies on
            # for idempotency, so this fits the existing convention without
            # a special case. A fresh DB runs the two ADD COLUMN lines above
            # under their original names first, then these rename them in
            # the same pass — order matters, so this must stay after them.
            "ALTER TABLE tools RENAME COLUMN differentiation_note TO competitive_differentiation",
            "ALTER TABLE tools RENAME COLUMN differentiation_needs_verification TO competitive_differentiation_needs_verification",
            # Originally added for the Phase 3 addendum's pin-then-recency-
            # backfill homepage teaser panel; the Homepage Restructure phase
            # replaced that panel with 3 hardcoded flagship pieces + a
            # 4-column type breakdown (get_thought_leadership_representative,
            # since deleted) and repurposed this same column rather than
            # adding a new one. A later pass (the Recent-highlights-as-a-
            # curated-set build) deleted that per-type mechanism outright in
            # favor of a hand-curated 4-slot featured set spanning any mix
            # of types — see list_thought_leadership_featured_home below for
            # the selection logic this column now drives. Still defaults to
            # 0 for every existing row (no retroactive selection), same
            # precedent as agent_taxonomy_needs_verification's own migration.
            "ALTER TABLE thought_leadership ADD COLUMN featured_home INTEGER NOT NULL DEFAULT 0",
            # Phase 5b (Reader content backfill): structured HTML for the
            # Reader pane, persisted per-article so a re-fetch's work
            # actually survives past the request that did it — without this,
            # _resolve_reader_content's "cached content > 200 chars, use it"
            # branch would keep serving the old plain-text `content` on every
            # later read and the backfill's output would be silently
            # discarded. Deliberately a NEW column, not a reuse of `content`:
            # `content` is a load-bearing plain-text contract (FTS5 indexing,
            # the enrichment prompt, looks_paywalled's length check) that
            # must not start holding HTML. Empty string ("" default) doubles
            # as "not yet backfilled" for articles_needing_content_backfill's
            # default (non-force) scope — same not-yet-done-signal pattern
            # `unenriched()` uses for `enriched=0`, just via an empty column
            # instead of a boolean.
            "ALTER TABLE articles ADD COLUMN content_html TEXT NOT NULL DEFAULT ''",
            # Phase 5b follow-up (fetch reliability): distinguishes a Wayback-
            # sourced success from a direct-fetch success in content_refetch_log
            # — without this, a Wayback-archived (possibly stale, possibly
            # different from what the live page shows today) version looks
            # identical to a normal live fetch, which matters for spotting a
            # drifted/stale re-fetch later. 'direct' for every existing row
            # (all prior attempts were direct fetches, no Wayback fallback
            # existed yet) and every non-Wayback attempt going forward,
            # including failures — a failure's `source` isn't really
            # meaningful (nothing was fetched from anywhere), but defaulting
            # it to 'direct' rather than adding a third empty-string state
            # keeps every row's column populated and queryable.
            "ALTER TABLE content_refetch_log ADD COLUMN source TEXT NOT NULL DEFAULT 'direct'",
            # paywall_cookie_note's ADD line used to sit here. It is gone rather
            # than commented out: with _drop_retired_paywall_cookie_note below
            # running on the same boot, leaving it would re-add the column
            # immediately after every drop, churning the schema forever. A
            # database that still HAS the column keeps it (this list only ever
            # added it, never populated it), so the one-time conversion in
            # seed_paywall_cookie_flags still finds its source; one that never
            # had it converts from feed.PAYWALLED_DOMAINS instead, which
            # seed_paywall_cookie_flags already handles via .get().
            # Whether Brian currently pays for this source. PURELY
            # INFORMATIONAL — nothing reads it. It does not gate fetching, does
            # not reach the Reader, and is independent of both
            # paywall_cookie_note (a label pointing at where a cookie lives)
            # and feed.PAYWALLED_DOMAINS (which drives the paywall badge and
            # authcheck's probe list). Linking it to cookie-apply behaviour was
            # discussed and dropped; that's separate, later work. If a future
            # change wants to make it functional, that's a deliberate decision
            # to make then, not an assumption to inherit from this column
            # existing.
            "ALTER TABLE feeds ADD COLUMN has_active_subscription INTEGER NOT NULL DEFAULT 0",
            # Replaces paywall_cookie_note's free text with a boolean. There is
            # exactly one cookie mechanism in the app (LINKLIB_AUTH_COOKIES), so
            # per-row text pointing at it was redundant and drifted between rows
            # (typos, inconsistent phrasing). The "where is it configured"
            # explanation now lives once in the page footnote instead.
            #
            # paywall_cookie_note shipped retired (present, unused) and is now
            # dropped for real by _drop_retired_paywall_cookie_note below, once
            # seed_paywall_cookie_flags has provably converted it. Dormant
            # columns are clutter; retirement was the safe intermediate step,
            # not the destination.
            "ALTER TABLE feeds ADD COLUMN has_paywall_cookie INTEGER NOT NULL DEFAULT 0",
            # Durability audit item 1 (2026-08): ingest_url() used to store
            # whatever fetch_page() returned with no quality check at all — a
            # bad save (paywall preview, bot-challenge interstitial, a stub
            # under _MIN_CONTENT_WORDS) was indistinguishable from a good one
            # until someone happened to read it, which is exactly how 54% of
            # Medium-platform articles sat empty and unnoticed. These two
            # columns are the save-time signal: set together, right after
            # ingest_url runs extract.assess_extraction_quality() on the fresh
            # fetch, by Library.set_content_check_flag. needs_content_check
            # never blocks or rejects a save — see ingest_url's docstring —
            # it only flags the row so /admin/reader/backfill-content can
            # surface it distinctly from "just hasn't been through a backfill
            # run yet" (which is the entire existing corpus, by definition,
            # since content_html starts empty for every row). Cleared back to
            # 0/'' by set_article_content_html the moment a later backfill
            # (or a resave through ingest_url) actually succeeds for that
            # article — see that method's docstring.
            "ALTER TABLE articles ADD COLUMN needs_content_check INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE articles ADD COLUMN content_check_reason TEXT NOT NULL DEFAULT ''",
            # Feature Taxonomy Phase 1: free-text suite-membership notation (rules
            # doc §5's "beyond the office of the CFO" case — CRM, HRIS, and similar
            # adjacent-but-out-of-Toolbox-scope capabilities a vendor bundles in).
            # Deliberately generic, reused verbatim across any tool that's part of a
            # broader operational suite (NetSuite first; Workday or a future vendor
            # later), not a structured field — same free-text precedent as
            # competitive_differentiation/agent_taxonomy_note. Nullable/empty means
            # "no suite to note," not "not yet researched."
            "ALTER TABLE tools ADD COLUMN suite_note TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE tool_feature_links ADD COLUMN public_note TEXT NOT NULL DEFAULT ''",
            # /tools/resources splits into two sections (Benchmarking, Book
            # recommendations) — 'benchmarking' | 'books'. Every existing row
            # defaults to 'benchmarking' with no backfill needed; the ten
            # book rows are inserted by a one-off migration script, not by
            # this column-add.
            "ALTER TABLE benchmarks ADD COLUMN section TEXT NOT NULL DEFAULT 'benchmarking'",
            # AI confidence indicator, tool Description/Short summary and
            # Competitive differentiation (2026-08) — a genuine model self-
            # report, matching the pattern agent_taxonomy_needs_verification
            # already established (generate_tool_agent_taxonomy's own
            # "confident" JSON key). Deliberately a SEPARATE fact from
            # description_needs_verification/competitive_differentiation_needs_verification:
            # verification is about human review status, confidence is the
            # model's own self-assessed certainty at generation time — the
            # two combine (an unverified AND low-confidence field is the
            # highest-risk state a reader can see) rather than one standing
            # in for the other. NULL means "no signal" (hand-written,
            # pre-existing row, or a hand-edited save that isn't a fresh AI
            # draft) — 0/1 only ever gets written alongside a fresh
            # generation, the same "hand-edited counts as a fresh state"
            # convention needs_verification already uses. summary shares
            # description_ai_confident exactly like it shares
            # description_needs_verification, since both are drafted by the
            # same generateDescription() call.
            "ALTER TABLE tools ADD COLUMN description_ai_confident INTEGER",
            "ALTER TABLE tools ADD COLUMN competitive_differentiation_ai_confident INTEGER",
            # Confidence indicator, Community profile draft (2026-08) — same
            # signal as the two tools columns above, extended to the 12
            # Community profile fields judged to carry real fabrication risk
            # (linklib.enrich.COMMUNITY_CONFIDENCE_FIELDS; see that constant's
            # comment for what's excluded and why). One column per field
            # rather than a JSON blob, matching every other per-field column
            # on this table. NULL means no signal yet, same convention as the
            # tools columns.
            "ALTER TABLE community_profiles ADD COLUMN ideal_member_ai_confident INTEGER",
            "ALTER TABLE community_profiles ADD COLUMN anti_fit_ai_confident INTEGER",
            "ALTER TABLE community_profiles ADD COLUMN value_prop_ai_confident INTEGER",
            "ALTER TABLE community_profiles ADD COLUMN business_model_ai_confident INTEGER",
            "ALTER TABLE community_profiles ADD COLUMN format_reality_ai_confident INTEGER",
            "ALTER TABLE community_profiles ADD COLUMN engagement_level_ai_confident INTEGER",
            "ALTER TABLE community_profiles ADD COLUMN sponsor_relationship_note_ai_confident INTEGER",
            "ALTER TABLE community_profiles ADD COLUMN application_friction_ai_confident INTEGER",
            "ALTER TABLE community_profiles ADD COLUMN cost_value_verdict_ai_confident INTEGER",
            "ALTER TABLE community_profiles ADD COLUMN notable_members_ai_confident INTEGER",
            "ALTER TABLE community_profiles ADD COLUMN public_criticism_ai_confident INTEGER",
            "ALTER TABLE community_profiles ADD COLUMN verdict_summary_ai_confident INTEGER",
            # Confidence indicator, Agent taxonomy (2026-08 follow-up) — the
            # same genuine self-reported signal as the two tools columns
            # above, extended to Agent taxonomy, the field this whole effort
            # started from (the Abacum fabrication finding). Until now
            # generate_tool_agent_taxonomy's own "confident" JSON key only
            # ever drove agent_taxonomy_needs_verification
            # (needs_verification = not confident) — a real signal, but never
            # stored on its own, so it couldn't get the same permanent
            # "Claude confidence: Yes/No" display line Description/
            # Differentiation have. NULL means no signal yet, same convention
            # as every other *_ai_confident column. Deliberately NOT wired
            # into the public publish gate (agent_taxonomy_needs_verification
            # still gates visitor visibility — see CLAUDE.md's "Agent
            # taxonomy publish gate" bullet, unchanged by this column) or the
            # "Mark verified" badge/button (_narrative_verify_widget, also
            # unchanged) — this is a second, independent admin-facing fact,
            # not a replacement for either existing mechanism.
            "ALTER TABLE tools ADD COLUMN agent_taxonomy_ai_confident INTEGER",
            # Quality-indicator visibility (item 6, Aug 2026 UI pass) —
            # persists the OTHER signal every generate_tool_* draft already
            # returns (draft.low_confidence: "the page fetch failed / no
            # page content, drafted from name+URL alone") right alongside
            # each field's existing *_ai_confident column. Previously this
            # only ever flashed in the Generate-status toast text
            # ("Could not fetch the page...") and was lost on reload —
            # never persisted for tools at all, unlike community_profiles'
            # single low_confidence column (which predates this and stays
            # untouched). A DISTINCT fact from confident: low_confidence is
            # a mechanical pre-generation signal (did the fetch succeed),
            # confident is the model's own post-generation self-report —
            # the two can and do disagree (a successful fetch of a thin
            # page can still yield a confident=false draft). NULL means no
            # signal yet, same convention as every *_ai_confident column;
            # written alongside a fresh generation only, same COALESCE
            # write-path convention.
            "ALTER TABLE tools ADD COLUMN description_low_confidence INTEGER",
            "ALTER TABLE tools ADD COLUMN competitive_differentiation_low_confidence INTEGER",
            "ALTER TABLE tools ADD COLUMN agent_taxonomy_low_confidence INTEGER",
            # Read Later content caching (2026-08 Reader cleanliness pass) —
            # /save-later used to be a bare metadata insert with no fetch at
            # all; every open of an unread Read Later item re-fetched live via
            # _resolve_reader_content, with nothing ever cached. These two
            # columns let /save-later cache a fetch's result the same way
            # Archive does, and let a manual per-item "Refresh" action replace
            # it later. Both default to '' (no cache yet) — a row saved before
            # this migration just falls through to the existing live-fetch
            # path unchanged, same as today, until it's next refreshed.
            "ALTER TABLE read_later ADD COLUMN content TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE read_later ADD COLUMN content_html TEXT NOT NULL DEFAULT ''",
            # Stale "Verified by X on Y" stamp fix (2026-08) — narrative_review_log
            # stays append-only (nothing is ever deleted, per its own schema
            # comment), but get_latest_narrative_review must stop surfacing a
            # row once the field it verified has been regenerated. NULL means
            # "still the live stamp"; a non-NULL timestamp means "superseded by
            # a later regeneration" — see Library._supersede_narrative_review.
            "ALTER TABLE narrative_review_log ADD COLUMN superseded_at TEXT",
            # Manual logo override (2026-08) — Phase 0 investigation confirmed
            # scripts/backfill_logos.py is the ONLY writer of logo_path, and it
            # already skips any row where logo_path is non-empty — but that's an
            # accident of its own WHERE clause, not a real guarantee, and a
            # future "refresh logos" feature could easily not know to preserve
            # it. logo_manual_override is the explicit, defense-in-depth signal:
            # set_tool_logo/set_community_logo (the only writers Brandfetch-side
            # automation ever calls) now refuse to overwrite a row with this
            # flag set, regardless of what selection query got them there — same
            # "one choke point" precedent as the em-dash mechanical backstop.
            # logo_override_stale flags (never silently clears) a manual
            # override when the tool/community's URL domain changes after the
            # override was set — see update_tool/update_community — so a
            # correction made for one company's site doesn't quietly keep
            # rendering once the URL points somewhere else entirely.
            "ALTER TABLE tools ADD COLUMN logo_manual_override INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE tools ADD COLUMN logo_override_stale INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE communities ADD COLUMN logo_manual_override INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE communities ADD COLUMN logo_override_stale INTEGER NOT NULL DEFAULT 0",
            # Whole-record tool profile signoff (2026-08) — mirrors
            # community_profiles.needs_review as closely as sensible for
            # tools' shape (see CLAUDE.md's "Tools whole-record profile
            # signoff" bullet). Deliberately named identically to Communities'
            # own column, not *_needs_verification-prefixed, so it reads as
            # the same higher-level concept across both entity types while
            # staying unambiguous next to the three field-scoped
            # description_needs_verification/agent_taxonomy_needs_verification/
            # competitive_differentiation_needs_verification columns (those are
            # always field-prefixed; this one never is).
            #
            # Amended (2026-08, same phase, before this shipped) — two
            # reversals, both by explicit direction:
            #
            # (1) Auto-linked to per-field regeneration after all, mirroring
            # Communities' `needs_review = checkbox OR profile_ai_drafted`
            # pattern as closely as tools' three-independent-fields shape
            # allows: a fresh Generate/Refresh draft that sets ANY of the
            # three per-field flags to 1 also forces this one to 1, regardless
            # of the checkbox's prior state — see admin_tools_edit_submit
            # (Description/Differentiation, computed in the same request as
            # the checkbox, so the OR is literal) and _run_tool_research
            # (Agent taxonomy, whose fresh-draft trigger fires from a
            # different request entirely — the background task on tool
            # creation or the "Refresh AI research" button — so it force-sets
            # to 1 directly rather than ORing). The checkbox/"Mark reviewed"
            # button can still set this to 0 at any time, and that manual 0
            # persists across any later save that doesn't itself draft one of
            # the three fields — only a FRESH draft landing needs_verification
            # on 1 overrides it. This also means scripts/regen_ai_drafted_
            # fields.py's own deliberate bypass (it always passes
            # needs_verification=0 for all three fields) correctly never
            # triggers the auto-link, since the trigger is keyed on the flag
            # actually landing on 1, not on "a field was drafted."
            #
            # (2) SQL column default stays 0 — the safe, non-retroactive-
            # flagging value for both migration backfill (an existing row on
            # first deploy of this column must not suddenly read as
            # unreviewed) and any raw INSERT that doesn't pass a value. The
            # "brand-new tool defaults to needs review" behavior instead
            # lives as add_tool()'s own Python-level default parameter
            # (needs_review: int = 1) — same "SQL default is the floor,
            # Python default is the real behavior for the one call site that
            # matters" split description_needs_verification already uses.
            #
            # Admin-only bookkeeping is unchanged by either reversal — still
            # gates nothing on the public profile/compare pages, unlike
            # Communities' needs_review (which hides the whole profile
            # draft): tools already have the three per-field gates doing
            # that job.
            "ALTER TABLE tools ADD COLUMN needs_review INTEGER NOT NULL DEFAULT 0",
            # FP&A Buddy published-content ingestion (2026-09) — provenance
            # flag, not a ranking signal (see linklib/agent.py's
            # _build_source_documents/_rrf_merge — nothing in retrieval reads
            # this column; it's read only post-retrieval, for citation
            # labeling). Set unconditionally on the 3 original_content
            # mirror rows by sync_original_content_article(), and set (never
            # cleared) by pipeline.ingest_url() whenever a saved URL matches
            # a thought_leadership.url — see
            # linklib/original_content_sync.py and Library.
            # is_thought_leadership_url/set_article_own_content.
            "ALTER TABLE articles ADD COLUMN is_own_content INTEGER NOT NULL DEFAULT 0",
            # The mirrored articles.id for this original_content row, or NULL
            # before the first sync. Tracked explicitly (rather than
            # re-deriving it via a URL lookup on every sync) so a later slug
            # rename — which legitimately changes the canonical URL, see the
            # admin form's own warning copy — doesn't strand the mirror or
            # require re-matching by URL.
            "ALTER TABLE original_content ADD COLUMN mirrored_article_id INTEGER",
            # Encourage-password-change (2026-09) — set whenever an account's
            # password was chosen by someone other than the account holder
            # (admin-created, or admin-reset), cleared the moment the holder
            # sets their own new password (self-service /reset-password, or
            # the in-session /change-password form) — see create_user's
            # password_change_recommended default, set_user_password's
            # sibling set_password_change_recommended, and
            # webapp.app._password_change_nudge_html. A dismissible nudge
            # only (Brian's explicit call) — never blocks any route. SQL
            # default 0 so an existing row on first deploy of this column
            # doesn't suddenly nag; the "new/reset account defaults to 1"
            # behavior lives at the Python call sites that actually create
            # or reset a password, same "SQL default is the floor" split
            # tools.needs_review's own migration comment already documents.
            "ALTER TABLE users ADD COLUMN password_change_recommended INTEGER NOT NULL DEFAULT 0",
            # FP&A Buddy feedback triage (2026-09) — a manual "mark
            # reviewed" toggle for ask_feedback, matching Community gaps'
            # own `community_gap_submissions.reviewed` column name/type/
            # default exactly (Phase 0 investigation confirmed that's a
            # plain boolean, not a timestamp, so this mirrors it rather than
            # inventing a viewed_at convention). Deliberately NOT auto-clear-
            # on-view, for the same reason Community gaps isn't: merely
            # opening the admin list shouldn't silently dismiss every row on
            # it. See toggle_ask_feedback_reviewed/count_unreviewed_ask_
            # feedback and the admin_ask_feedback route.
            "ALTER TABLE ask_feedback ADD COLUMN reviewed INTEGER NOT NULL DEFAULT 0",
            # Exa cost tracking, pre-dashboard foundation (2026-09) — the
            # Reader content backfill's domain-migration and Medium-platform
            # fallback tiers (linklib/domain_migration.py,
            # linklib/medium_platform.py) call Exa with zero cost tracking
            # anywhere — unlike FP&A Buddy's retrieve_exa, whose cost lands
            # on ask_questions.exa_cost_usd. content_refetch_log already
            # records each backfill attempt's outcome, so this is the
            # natural home for it rather than a new table: one row per
            # backfill_article_content() call already exists, this just
            # adds what that attempt's Exa usage (if any) cost, real or $0.
            # 0 for every pre-existing row (no Exa spend was ever tracked
            # for them) and for any attempt that never called Exa at all
            # (a direct-fetch success, a defunct-service skip, or a
            # Wayback-only attempt with no migration/Medium tier reached).
            # See Library.log_content_refetch_attempt's exa_cost_usd param.
            "ALTER TABLE content_refetch_log ADD COLUMN exa_cost_usd REAL NOT NULL DEFAULT 0",
            # Exa cost tracking, pre-dashboard foundation (2026-09), part 2:
            # Answer.exa_cost_usd/exa_result_count (linklib/agent.py) were
            # always computed on every Buddy turn but never persisted here —
            # unlike embed_cost_usd/rewrite_cost_usd, which each got their
            # own column the moment their call became a real per-turn cost.
            # exa_cost_usd is already folded into cost_usd (same "share,
            # already inside the total" convention as the other two
            # breakout columns); this just makes that share visible on its
            # own, the same gap this whole PR closes for the Reader
            # backfill's Exa calls too. 0 for every pre-existing row (no
            # per-turn Exa cost was ever tracked for them).
            "ALTER TABLE ask_questions ADD COLUMN exa_result_count INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE ask_questions ADD COLUMN exa_cost_usd REAL NOT NULL DEFAULT 0",
            # Current Feed (/current-feed, 2026-09) — replaces the original
            # section-name-matching design (Blogs=Side A, Substacks=Side B,
            # News/Market Insights excluded) with two explicit, per-feed
            # columns, admin-editable on /admin/reader/feeds. The
            # section-based design worked but left one real gap: a feed
            # sitting in a section that's neither a known side nor a known
            # exclusion (production already has an empty "Tools" section)
            # had no clean home — it could only be silently included,
            # silently excluded, or flagged as an unmapped edge case. A
            # per-feed "show" flag has no such edge case: a feed in any
            # section, however new, simply isn't shown until someone
            # deliberately marks it. New feeds default to NOT shown
            # (show_on_current_feed=0) — Brian's own call: he'd rather set
            # it deliberately than have something appear unreviewed.
            # current_feed_side is free text ('old_school'/'new_school'/''),
            # not a CHECK constraint, so a future third side needs no
            # migration — only current_feed()'s own rendering code needs to
            # learn a new value. Existing rows are seeded from their CURRENT
            # section by Library.seed_current_feed_sides() (settings-flagged,
            # not emptiness-checked, same precedent as
            # seed_paywall_cookie_flags — see that method's own docstring).
            "ALTER TABLE feeds ADD COLUMN show_on_current_feed INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE feeds ADD COLUMN current_feed_side TEXT NOT NULL DEFAULT ''",
            # Running order within a side (2026-09 follow-up) — a mixtape's
            # track order is part of the point, so it's not left to whatever
            # list_feeds()'s own section/name ordering happens to produce.
            # Lower sorts first; only meaningful for a shown feed (see
            # current_feed()'s own sort, which reads this column only after
            # filtering to show_on_current_feed=1) — a hidden feed's value is
            # inert, not cleared, so a position set before a feed is turned
            # back on isn't lost. Ties fall back to feeds.id, the one value
            # that's both permanent and already unique, so two equal-order
            # feeds render in the same order on every request rather than
            # shuffling with SQLite's own unspecified tie order. Existing
            # rows are seeded once, from the render order they already had,
            # by Library.seed_current_feed_order() — see its own docstring.
            "ALTER TABLE feeds ADD COLUMN current_feed_order INTEGER NOT NULL DEFAULT 0",
            # Voice review queue trigger taxonomy (2026-09, seed-sync infinite-
            # loop investigation; extended 2026-09 for issue #592 item 3) —
            # records WHICH mechanism produced a given voice_review_queue row.
            # Five values: 'admin-edit' (a human editing a record through an
            # /admin/* submit route), 'startup-sync' (_seed_toolbox's per-boot
            # re-sync of a tools/communities/benchmarks row, or its advisor
            # boolean, against its static seed-source value), 'script' (a
            # one-off, HUMAN-RUN backfill/fix script, e.g.
            # scripts/backfill_voice_review_queue.py,
            # scripts/fix_spaced_em_dashes.py, scripts/regen_ai_drafted_fields.py),
            # 'submission' (a public, member-gated submission route —
            # POST /tools/submit, POST /tools/communities/submit — where the
            # content's origin is known but it's neither an admin edit nor a
            # script/sync run), or 'scan' (a periodic BACKGROUND pass —
            # `Library.reconcile_voice_review_queue()`, run on a schedule by
            # webapp.tasks' checks refresher, re-reading the live DB — not a
            # human-run script, even though it shares add_voice_review_item's
            # same insertion path as scripts/backfill_voice_review_queue.py;
            # the two were indistinguishable under 'script' before this fix).
            #
            # A sixth string worth knowing about here, even though it's a
            # DIFFERENT column: `rule='seed-disagreement'` (set by
            # `add_seed_disagreement_item`) is the RULE that matched, not a
            # `source` value — a seed-disagreement row's own `source` is
            # always 'startup-sync', the mechanism that found the divergence.
            #
            # NULL for every pre-existing row (deliberately not backfilled —
            # see Library._vf's own call sites for which value each caller now
            # passes) and for any future caller that doesn't yet pass one;
            # rendered as "unknown" on /admin/voice/review-queue rather than a
            # blank cell. Built specifically because a silent infinite loop was
            # found: _seed_toolbox re-syncs a community's `notes` field from
            # scripts/seed_communities.py's raw (spaced-em-dash) text on EVERY
            # boot, since the live DB value is already voice-fixed (unspaced)
            # by a prior save — the write-then-normalize-then-log cycle repeats
            # forever, invisibly, because a DB-only scan always finds the
            # already-corrected text. Tagging the source lets a reviewer tell
            # "an admin typed this" from "the seed sync did this again" at a
            # glance, without having to reverse-engineer it from the excerpt.
            "ALTER TABLE voice_review_queue ADD COLUMN source TEXT",
            # 2026-09 bidirectional-sync + approve-term follow-up: a plain
            # note explaining an automatic resolution not driven by one of
            # the queue's own per-row actions — set only by
            # `reconcile_voice_review_queue()` ("resolved outside the
            # queue — no longer found on the last scan") and
            # `approve_voice_term()` ('Always allowed as "<term>".'). NULL
            # for every row resolved through an ordinary per-row action.
            "ALTER TABLE voice_review_queue ADD COLUMN resolution_note TEXT",
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
        self._drop_retired_paywall_cookie_note()
        # migrate_app_screenshot_from_product_flag is deliberately NOT called
        # here, unlike the two migrations above — it's a one-time DATA
        # migration touching pre-existing production rows (moving a legacy
        # screenshot_is_product=1 row's screenshot into the new app slot),
        # not a schema/column backfill. The standing rule (CLAUDE.md, "One-
        # off admin fixes against the database") requires Brian's explicit
        # review of a production data write before it happens, not after —
        # an automatic boot hook would fire the moment this deploys, before
        # anyone has seen the affected-row count. Run it by hand instead via
        # scripts/archive/migrate_app_screenshot_from_product_flag.py (preview by
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

    def _feeds_has_column(self, name: str) -> bool:
        return any(r[1] == name for r in
                   self.conn.execute("PRAGMA table_info(feeds)").fetchall())

    def _drop_retired_paywall_cookie_note(self) -> None:
        """Remove the retired `paywall_cookie_note` column for real.

        It shipped dormant when `has_paywall_cookie` replaced it, on the
        non-destructive-retirement precedent. Dormant columns are clutter, so
        this drops it once its one job — being the source
        `seed_paywall_cookie_flags` migrates from — is provably done.

        **Gated on `paywall_cookie_flags_seeded`, not on a human checking
        production first.** That flag is set only after a successful
        conversion pass, so:

          * flag set   -> the column has already been read; dropping it can
                          lose nothing.
          * flag unset -> this database has never run the conversion. The drop
                          is SKIPPED, the column survives, and the next boot
                          converts from it as designed.

        That makes the ordering hazard structurally impossible on any database,
        including ones nobody inspected first, and it self-heals: a DB restored
        from an old backup still converts before it drops. Idempotent either
        way, since it no-ops once the column is gone.
        """
        if not self._feeds_has_column("paywall_cookie_note"):
            return          # already dropped, or a fresh DB that never had it
        if self.get_setting("paywall_cookie_flags_seeded") != "1":
            return          # not yet converted — leave the source in place
        try:
            # SQLite >= 3.35. Atomic, and with no data copy there is no way to
            # land a value in the wrong column.
            self.conn.execute("ALTER TABLE feeds DROP COLUMN paywall_cookie_note")
            self.conn.commit()
        except sqlite3.OperationalError:
            # Older SQLite has no DROP COLUMN: fall back to the rebuild.
            self._rebuild_feeds_without_cookie_note()

    # Every column of `feeds` except the one being dropped, in CREATE order.
    _FEEDS_COLUMNS_AFTER_DROP = (
        "id", "section_id", "name", "xml_url", "html_url", "exclude_from_queue",
        "display_order", "created_at", "has_active_subscription",
        "has_paywall_cookie",
    )

    def _rebuild_feeds_without_cookie_note(self) -> None:
        """Pre-3.35 fallback for the drop above: copy into a fresh table.

        Columns are named explicitly on both sides of the INSERT rather than
        relying on positional order, so a future column added to `feeds` can't
        silently shift values into the wrong slot.
        """
        cols = ", ".join(self._FEEDS_COLUMNS_AFTER_DROP)
        self.conn.executescript(f"""
            PRAGMA foreign_keys=off;
            BEGIN;
            CREATE TABLE feeds_migrated (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                section_id              INTEGER NOT NULL REFERENCES feed_sections(id),
                name                    TEXT NOT NULL,
                xml_url                 TEXT NOT NULL UNIQUE,
                html_url                TEXT NOT NULL DEFAULT '',
                exclude_from_queue      INTEGER NOT NULL DEFAULT 0,
                display_order           INTEGER NOT NULL DEFAULT 0,
                created_at              TEXT NOT NULL DEFAULT '',
                has_active_subscription INTEGER NOT NULL DEFAULT 0,
                has_paywall_cookie      INTEGER NOT NULL DEFAULT 0
            );
            INSERT INTO feeds_migrated ({cols}) SELECT {cols} FROM feeds;
            DROP TABLE feeds;
            ALTER TABLE feeds_migrated RENAME TO feeds;
            COMMIT;
            PRAGMA foreign_keys=on;
        """)
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
        scripts/archive/migrate_app_screenshot_from_product_flag.py, run by hand
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
        _init_vector_search).

        Also removes content_refetch_log and url_correction_log rows for
        this article (article-purge-flow follow-up, 2026-08) — a re-fetch
        attempt history or URL-correction trace has no meaning once the
        article itself is gone, and leaving them orphaned would let a
        future article reusing the same id (SQLite recycles AUTOINCREMENT
        ids once a table is VACUUMed, though not otherwise) inherit a
        stranger's history. This is a general fix to every existing caller
        of delete_article (dedupe removal, the member Reader's own delete —
        review-removals was a caller too until its PR 4, 2026-09 retirement),
        not something new only the purge flow needed.

        Deliberately NOT deleted: enrichment_cost (a real-money spend
        ledger — the API call cost actual dollars regardless of whether the
        article survives; see that table's CREATE TABLE comment) and
        archive_audit_log (the historical 'what happened' record — every
        caller of delete_article already writes a 'delete' row there via
        _log_archive_audit BEFORE calling this, and that row's whole job is
        to outlive the article, same as tool_audit_log/community_audit_log
        surviving a deleted tool/community elsewhere in this codebase)."""
        self.conn.execute("DELETE FROM articles WHERE id=?", (article_id,))
        self.conn.execute("DELETE FROM article_embeddings WHERE article_id=?", (article_id,))
        self.conn.execute("DELETE FROM content_refetch_log WHERE article_id=?", (article_id,))
        self.conn.execute("DELETE FROM url_correction_log WHERE article_id=?", (article_id,))
        if self._vec_available:
            self.conn.execute("DELETE FROM articles_vec WHERE rowid=?", (article_id,))
        self.conn.commit()

    # -- article purge (durability follow-up, 2026-08) -----------------------

    def articles_eligible_for_purge(self, limit: int = 5000) -> list[dict]:
        """Candidates for the article-purge flow: articles with essentially
        nothing saved — the plain-text `content` field under
        extract._MIN_CONTENT_WORDS (the same floor assess_extraction_quality()
        uses for its 'too-thin' verdict) AND no structured content_html ever
        backfilled either. Each row carries `word_count` (computed here,
        not stored) and `reason` (content_check_reason — durability audit
        item 1 — when set, else '' for an article saved before that flag
        existed) — the same reason vocabulary already shown elsewhere on
        /admin/reader/backfill-content, not a new one invented for this
        list.

        Deliberately NOT the same set as articles_needing_content_backfill()'s
        Remaining tile: that's every article without content_html yet,
        the vast majority of which have perfectly good plain-text content
        and are just waiting for a structure-backfill pass. This is the
        much narrower 'genuinely nothing useful was ever saved for this
        URL' set — real purge candidates, not ordinary backfill backlog."""
        from .extract import _MIN_CONTENT_WORDS
        rows = self.conn.execute(
            "SELECT * FROM articles WHERE url!='' AND content_html='' ORDER BY id"
        ).fetchall()
        out: list[dict] = []
        for r in rows:
            content = r["content"] or ""
            word_count = len(content.split())
            if word_count >= _MIN_CONTENT_WORDS:
                continue
            d = self._row_to_dict(r)
            d["word_count"] = word_count
            out.append(d)
            if len(out) >= limit:
                break
        return out

    def count_purge_candidates(self) -> int:
        return len(self.articles_eligible_for_purge(limit=100000))

    def purge_article(self, article_id: int) -> Optional[dict]:
        """Hard-deletes one article via delete_article (which now also
        cleans up content_refetch_log/url_correction_log — see its
        docstring), then reads back to verify the delete actually took —
        write-then-read-back, per CLAUDE.md's one-off-admin-fix discipline,
        applied here as a standing check rather than a one-time script
        since every call through this method is genuinely destructive and
        irreversible outside a DB restore.

        Returns a snapshot dict (id/title/url/word_count) captured
        IMMEDIATELY BEFORE the delete — for the caller to log to
        archive_audit_log and show in a confirmation summary — or None if
        the article doesn't exist (a no-op, not an error; matches
        apply_article_url_correction's convention for an unknown id).

        Raises RuntimeError if the article is somehow still present after
        the delete — this should never happen for a plain DELETE, and
        silently continuing past that would be exactly the kind of
        unverified destructive write CLAUDE.md's admin-fix discipline
        exists to catch."""
        row = self.get_article(article_id)
        if row is None:
            return None
        snapshot = {"id": row["id"], "title": row.get("title") or "",
                    "url": row.get("url") or "",
                    "word_count": len((row.get("content") or "").split())}
        self.delete_article(article_id)
        if self.get_article(article_id) is not None:
            raise RuntimeError(
                f"purge_article: article {article_id} is still present after delete_article() ran")
        return snapshot

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
        """`in_scope`/`scope_reason` are frozen (PR 4, "Remove content"
        retirement, 2026-09) — nothing computes a non-default value for
        either any more (see the `articles.in_scope` column comment), so
        every real caller now leaves both at their True/"" defaults. Kept as
        parameters, not dropped, purely so the column write stays explicit
        and no caller signature needed to change."""
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
        call only. `article_id=None` records enrichment that has no
        `articles.id` yet to attach to (every batch/regen generation script
        is the live example) — the spend still counts even when that
        generation's source is never saved anywhere."""
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

    # -- Citations-API grounding fix, Phase 1b — entity_citations ----------------
    # See that table's schema comment for the full shape/reasoning.

    def set_entity_citations(self, entity_type: str, entity_id: int, field_name: str,
                              citations: list[dict], model: str = "") -> None:
        """Upsert the FULL, deduped-by-url citation list for one AI-drafted
        field — a fresh draft replaces whatever was here before (current
        state, not an append-only log). `citations` is already deduped/
        ordered by the caller (linklib.citations.extract_citations does
        this); a 5-source display cap is applied only at public-render
        time, never here — this table always holds everything so the admin
        view can show the full list. Pass an empty list to clear (e.g. a
        human hand-edited the field and its prior AI citations no longer
        apply — see update_tool_agent_taxonomy)."""
        self._write_entity_citations(entity_type, entity_id, field_name, citations, model)
        self.conn.commit()

    def _write_entity_citations(self, entity_type: str, entity_id: int, field_name: str,
                                citations: list[dict], model: str) -> None:
        """The upsert behind set_entity_citations, without the commit — for
        a caller that needs it inside its own transaction."""
        self.conn.execute(
            """INSERT INTO entity_citations (entity_type, entity_id, field_name, citations_json, model, generated_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(entity_type, entity_id, field_name)
               DO UPDATE SET citations_json=excluded.citations_json, model=excluded.model,
                             generated_at=excluded.generated_at""",
            (entity_type, entity_id, field_name, json.dumps(citations or []), model, _now()),
        )

    def clear_entity_citations(self, entity_type: str, entity_id: int, field_name: str) -> None:
        """Shorthand for set_entity_citations(..., [], model='') — used
        wherever a human edit invalidates a field's prior AI citations."""
        self.set_entity_citations(entity_type, entity_id, field_name, [], model="")

    def get_entity_citations(self, entity_type: str, entity_id: int, field_name: str) -> list[dict]:
        """The full, uncapped citation list for one field, or [] if it was
        never grounded (hand-written, or drafted with nothing to cite)."""
        row = self.conn.execute(
            "SELECT citations_json FROM entity_citations WHERE entity_type=? AND entity_id=? AND field_name=?",
            (entity_type, entity_id, field_name),
        ).fetchone()
        return json.loads(row["citations_json"]) if row else []

    # -- Narrative-field "Mark verified" audit trail (Phase G) — distinct from
    # field_reviews above; see the narrative_review_log schema comment for why
    # these two AI-drafted-content tables coexist. -----------------------------

    def record_narrative_review(self, admin_id: Optional[int], entity_type: str,
                                 field_type: str, item_id: int, detail: str = "") -> None:
        """Log one explicit "Mark verified" click. Append-only — a field
        verified once, re-drafted by a later AI refresh, and verified again
        gets two rows, so get_latest_narrative_review always reflects the
        most recent human confirmation rather than the first one ever made."""
        self.conn.execute(
            """INSERT INTO narrative_review_log
               (admin_id, entity_type, field_type, item_id, detail, created_at)
               VALUES (?,?,?,?,?,?)""",
            (admin_id, entity_type, field_type, item_id, detail.strip(), _now()),
        )
        self.conn.commit()

    def get_latest_narrative_review(self, entity_type: str, field_type: str,
                                     item_id: int) -> Optional[dict]:
        """Most recent, still-live "Mark verified" row for one field on one
        entity, with the verifying admin's username joined in — the
        "Verified by X on Y" line on the edit page. None if it's never been
        explicitly verified, OR if it was verified but the field has since
        been regenerated (superseded_at IS NOT NULL — see
        _supersede_narrative_review) — either way, "never verified" is the
        correct rendering for the CURRENT content."""
        row = self.conn.execute(
            """SELECT a.*, u.username AS admin_username, u.name AS admin_name
               FROM narrative_review_log a LEFT JOIN users u ON u.id = a.admin_id
               WHERE a.entity_type=? AND a.field_type=? AND a.item_id=? AND a.superseded_at IS NULL
               ORDER BY a.created_at DESC, a.id DESC LIMIT 1""",
            (entity_type, field_type, item_id),
        ).fetchone()
        return dict(row) if row else None

    def _supersede_narrative_review(self, entity_type: str, field_type: str, item_id: int) -> None:
        """Marks every currently-live narrative_review_log row for this field
        as superseded, so get_latest_narrative_review stops returning it and
        the edit page renders "never verified" instead of a stale "Verified
        by X on Y" stamp. Called whenever a field is regenerated (a fresh,
        not-yet-human-reviewed AI draft is written) — see the call sites in
        set_tool_agent_taxonomy_draft/update_tool/update_tool_differentiation/
        upsert_community_profile. Rows are marked, never deleted:
        narrative_review_log's append-only history (list_narrative_review_log)
        is preserved unchanged; superseded_at IS NULL means "is this the live
        stamp," not "does this row exist." A no-op if there's no live row to
        supersede (a field that's never been verified)."""
        self.conn.execute(
            """UPDATE narrative_review_log SET superseded_at=?
               WHERE entity_type=? AND field_type=? AND item_id=? AND superseded_at IS NULL""",
            (_now(), entity_type, field_type, item_id),
        )
        self.conn.commit()

    def list_narrative_review_log(self, limit: int = 500) -> list[dict]:
        """Full log, newest first — mirrors list_tool_audit_log's shape.
        No dedicated history view yet (Phase G scope), but this is here for
        the same reason list_tool_audit_log/list_community_audit_log are:
        so one exists when a history view is eventually wanted."""
        rows = self.conn.execute(
            """SELECT a.*, u.username AS admin_username, u.name AS admin_name
               FROM narrative_review_log a LEFT JOIN users u ON u.id = a.admin_id
               ORDER BY a.created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def enrichment_cost_total(self, since: Optional[str] = None) -> float:
        """Total enrichment overhead spend, optionally since an ISO date/datetime
        prefix. Mirrors ask_cost_total's shape for the user-cap ledger."""
        clause = "WHERE created_at>=?" if since else ""
        params = [since] if since else []
        row = self.conn.execute(
            f"SELECT COALESCE(SUM(cost_usd),0) FROM enrichment_cost {clause}", params
        ).fetchone()
        return float(row[0])

    # -- Compare Redesign Phase 2 — AI comparison summary: cache, daily cap,
    # and manual feedback triage. See compare_summary_cache's schema comment
    # for the cache-key/invalidation design. --

    _DEFAULT_COMPARE_SUMMARY_CAP_USD = 2.00  # small, shared daily budget —
    # this is one cached artifact per unique entity-set, not a per-visitor
    # cost like Ask/matchmaker, so it doesn't need their larger per-user caps.

    @staticmethod
    def compare_summary_content_hash(text: str) -> str:
        """sha256 of the exact text handed to the generator — the cache
        key's content component. A pure function (no DB access) so the
        caller can compute it before deciding whether a lookup is even
        needed."""
        import hashlib
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def get_compare_summary(self, entity_type: str, entity_ids: str, content_hash: str) -> Optional[dict]:
        row = self.conn.execute(
            """SELECT * FROM compare_summary_cache
               WHERE entity_type=? AND entity_ids=? AND content_hash=?""",
            (entity_type, entity_ids, content_hash),
        ).fetchone()
        return dict(row) if row else None

    def set_compare_summary(self, entity_type: str, entity_ids: str, content_hash: str,
                             summary: str, model: str, input_tokens: int = 0,
                             output_tokens: int = 0, cost_usd: float = 0.0) -> None:
        """Upsert one cached summary. `summary` is run through the same
        mechanical voice backstop (linklib.voice_mechanics) every other
        prose-capable Library write applies before persisting — a freshly
        generated summary is exactly the kind of AI-drafted text that
        backstop exists for."""
        from .voice_mechanics import normalize_voice_mechanics
        self.conn.execute(
            """INSERT INTO compare_summary_cache
               (entity_type, entity_ids, content_hash, summary, model,
                input_tokens, output_tokens, cost_usd, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(entity_type, entity_ids, content_hash) DO UPDATE SET
                 summary=excluded.summary, model=excluded.model,
                 input_tokens=excluded.input_tokens, output_tokens=excluded.output_tokens,
                 cost_usd=excluded.cost_usd, created_at=excluded.created_at""",
            (entity_type, entity_ids, content_hash, normalize_voice_mechanics(summary), model,
             input_tokens, output_tokens, cost_usd, _now()),
        )
        self.conn.commit()

    def get_default_compare_summary_cap(self) -> float:
        raw = self.get_setting("compare_summary_default_cap_usd")
        try:
            return float(raw) if raw else self._DEFAULT_COMPARE_SUMMARY_CAP_USD
        except ValueError:
            return self._DEFAULT_COMPARE_SUMMARY_CAP_USD

    def set_default_compare_summary_cap(self, cap_usd: float) -> None:
        self.set_setting("compare_summary_default_cap_usd", str(cap_usd))

    def compare_summary_cost_today(self) -> float:
        """Total compare-summary generation spend so far today (UTC calendar
        day) — a shared, global figure, not per-user, since this is one
        cached resource everyone reads. Mirrors matchmaker_cost_this_month's
        shape at a daily grain instead of monthly."""
        day_start = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        row = self.conn.execute(
            "SELECT COALESCE(SUM(cost_usd),0) FROM compare_summary_cache WHERE created_at >= ?",
            (day_start,),
        ).fetchone()
        return float(row[0])

    def add_compare_summary_feedback(self, entity_type: str, entity_ids: str, content_hash: str,
                                      summary_text: str, note: str = "") -> int:
        cur = self.conn.execute(
            """INSERT INTO compare_summary_feedback
               (entity_type, entity_ids, content_hash, summary_text, note, created_at)
               VALUES (?,?,?,?,?,?)""",
            (entity_type, entity_ids, content_hash, summary_text, note.strip(), _now()),
        )
        self.conn.commit()
        return cur.lastrowid

    def list_compare_summary_feedback(self, include_reviewed: bool = True) -> list[dict]:
        where = "" if include_reviewed else "WHERE reviewed_at=''"
        rows = self.conn.execute(
            f"SELECT * FROM compare_summary_feedback {where} ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def count_compare_summary_feedback(self, reviewed: Optional[bool] = None) -> int:
        if reviewed is None:
            clause, params = "", ()
        elif reviewed:
            clause, params = "WHERE reviewed_at!=''", ()
        else:
            clause, params = "WHERE reviewed_at=''", ()
        return self.conn.execute(
            f"SELECT COUNT(*) FROM compare_summary_feedback {clause}", params
        ).fetchone()[0]

    def mark_compare_summary_feedback_reviewed(self, feedback_id: int) -> None:
        self.conn.execute(
            "UPDATE compare_summary_feedback SET reviewed_at=? WHERE id=?", (_now(), feedback_id)
        )
        self.conn.commit()

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

    def get_article_by_url(self, url: str) -> Optional[dict]:
        """One article by its exact stored URL, or None — the lookup key
        the bulk-delete CSV tool (webapp/app.py's
        /admin/reader/bulk-delete/*) uses to resolve a pasted URL to a
        current article_id before deleting, mirroring the inline SQL
        _resolve_reader_content already ran for a Feed item that turns out
        to already be saved (Phase 5c). Kept as a real Library method
        (rather than inline SQL a second time) since there's now a second
        call site."""
        row = self.conn.execute("SELECT * FROM articles WHERE url=?", (url,)).fetchone()
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
        """Full-text search ranked by relevance (bm25).

        The raw query used to be passed straight into `articles_fts MATCH
        ?`, unescaped — but FTS5 has its own query mini-language, and plain
        English collides with it constantly: a hyphen ("self-serve"), an
        apostrophe ("brian's"), an unmatched quote, or a bareword that
        happens to be an FTS5 operator keyword (AND/OR/NOT) all threw
        sqlite3.OperationalError, which nothing caught — an ordinary search
        500'd instead of returning "no results." Found live during the
        Reader QA pass via the Saved search box and confirmed the same way
        against a bare FTS5 table with no app code involved.

        Fixed with two layers: try the query exactly as given first, then
        fall back to the whole thing wrapped as a single FTS5 phrase
        (quoted, with any literal double quotes doubled per FTS5's own
        escaping rule) if that raises — a quoted phrase never throws,
        whatever's inside it. Trying the raw query first matters: this
        method already has one caller that hands it deliberately
        well-formed FTS5 syntax, not free text —
        linklib.agent.retrieve()'s `_safe_fts_query()` pre-tokenizes and
        OR-joins a question into something like `"self" OR "serve"` for a
        real multi-term match. Wrapping THAT in an outer phrase quote
        (discovered while building this fix, before it shipped) turns the
        whole thing into one literal string search for `"self" OR "serve"`
        — everything doubly quoted, verbatim — which matches nothing;
        FP&A Buddy's library retrieval would have silently gone dark on
        every question. Trying the raw form first preserves that caller's
        semantics exactly (it's already valid syntax, so the raw attempt
        just succeeds); the raw-user-text case (invalid syntax) fails fast
        on the first attempt and only then gets the safe quoted fallback.
        Either an unrecoverable raw query or a failure on the quoted
        fallback degrades to zero results, never a 500.
        """
        if not query.strip():
            rows = self.conn.execute(
                "SELECT * FROM articles ORDER BY saved_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [self._row_to_dict(r) for r in rows]

        def _run(fts_query: str):
            return self.conn.execute(
                """SELECT a.*, bm25(articles_fts) AS rank
                   FROM articles_fts
                   JOIN articles a ON a.id = articles_fts.rowid
                   WHERE articles_fts MATCH ?
                   ORDER BY rank
                   LIMIT ?""",
                (fts_query, limit),
            ).fetchall()

        try:
            rows = _run(query)
        except sqlite3.OperationalError:
            quoted = '"' + query.replace('"', '""') + '"'
            try:
                rows = _run(quoted)
            except sqlite3.OperationalError:
                return []
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

    # Failed attempts (excluding defunct-service, which is already permanent)
    # after which an article is pulled out of default-scope auto-retry and
    # into the "needs manual review" tier — see _manual_review_article_ids.
    # 3, per Brian's own call in the Phase 5b follow-up #2 brief (his
    # assumption, flagged rather than silently picked): enough attempts to
    # rule out a one-off fluke (a timeout, a transient block) without
    # burning indefinite future batches' time and Wayback's scarce rate-limit
    # budget on a domain/link that's been failing every single time.
    _MANUAL_REVIEW_ATTEMPT_THRESHOLD = 3

    def _manual_review_article_ids(self, threshold: int | None = None) -> set[int]:
        """Article ids whose most recent content_refetch_log attempt is a
        failure (not defunct-service — that's a separate, permanent
        exclusion, never merged with this one per the Phase 5b follow-up #2
        brief) AND have failed at least `threshold` times IN A ROW SINCE
        THEIR LAST URL CORRECTION (or ever, if never corrected).

        "Since their last correction" is the key design point: a URL
        correction via apply_article_url_correction doesn't delete or mutate
        any content_refetch_log history (full audit trail stays intact —
        same non-destructive precedent as everywhere else in this codebase),
        so this only counts attempts with attempted_at strictly after the
        article's most recent url_correction_log row. That's what makes a
        corrected article naturally re-qualify for default-scope retry
        (attempt count resets to zero against the new URL) without a
        separate "clear attempt count" write.

        Deliberately query-time-derived, not written as its own
        content_refetch_log reason the way defunct-service is: unlike
        defunct-service (a fact knowable from a single attempt — this host
        is discontinued), "3rd failure in a row" is a judgment about
        accumulated history that can only be computed by looking at several
        rows at once."""
        threshold = threshold if threshold is not None else self._MANUAL_REVIEW_ATTEMPT_THRESHOLD
        rows = self.conn.execute(
            """WITH cutoffs AS (
                 SELECT article_id, MAX(created_at) AS cutoff
                 FROM url_correction_log GROUP BY article_id
               ),
               attempts AS (
                 SELECT l.article_id, l.status, l.reason, l.attempted_at,
                        ROW_NUMBER() OVER (PARTITION BY l.article_id ORDER BY l.attempted_at DESC) AS rn
                 FROM content_refetch_log l
                 LEFT JOIN cutoffs c ON c.article_id = l.article_id
                 WHERE c.cutoff IS NULL OR l.attempted_at > c.cutoff
               ),
               counts AS (
                 SELECT article_id, COUNT(*) AS attempt_count FROM attempts GROUP BY article_id
               )
               SELECT a.article_id FROM attempts a
               JOIN counts c ON c.article_id = a.article_id
               WHERE a.rn=1 AND a.status='failure' AND a.reason!='defunct-service'
                 AND c.attempt_count>=?""",
            (threshold,),
        ).fetchall()
        return {r[0] for r in rows}

    def count_articles_needing_manual_review(self) -> int:
        return len(self._manual_review_article_ids())

    def list_articles_needing_manual_review(self, limit: int = 500) -> list[dict]:
        """One row per needs-manual-review article: title, current stored
        URL, the real last failure reason/detail (not a synthetic
        "needs-manual-review" tag — see _manual_review_article_ids'
        docstring for why this is derived, not stored), the post-correction
        attempt count, and the last attempt's timestamp. Backs both the
        admin page's list section and the CSV export (webapp/app.py)."""
        rows = self.conn.execute(
            """WITH cutoffs AS (
                 SELECT article_id, MAX(created_at) AS cutoff
                 FROM url_correction_log GROUP BY article_id
               ),
               attempts AS (
                 SELECT l.article_id, l.status, l.reason, l.detail, l.attempted_at,
                        ROW_NUMBER() OVER (PARTITION BY l.article_id ORDER BY l.attempted_at DESC) AS rn
                 FROM content_refetch_log l
                 LEFT JOIN cutoffs c ON c.article_id = l.article_id
                 WHERE c.cutoff IS NULL OR l.attempted_at > c.cutoff
               ),
               counts AS (
                 SELECT article_id, COUNT(*) AS attempt_count FROM attempts GROUP BY article_id
               )
               SELECT a.article_id AS article_id, art.title AS title, art.url AS current_url,
                      a.reason AS reason, a.detail AS detail, a.attempted_at AS last_attempted_at,
                      c.attempt_count AS attempt_count
               FROM attempts a
               JOIN counts c ON c.article_id = a.article_id
               JOIN articles art ON art.id = a.article_id
               WHERE a.rn=1 AND a.status='failure' AND a.reason!='defunct-service'
                 AND c.attempt_count>=?
               ORDER BY a.attempted_at DESC LIMIT ?""",
            (self._MANUAL_REVIEW_ATTEMPT_THRESHOLD, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def apply_article_url_correction(self, article_id: int, new_url: str,
                                      source: str = "csv-import",
                                      admin_id: int | None = None) -> bool:
        """Corrects one article's stored URL (e.g. from the manual-review CSV
        import) and leaves a durable, distinct trace in url_correction_log —
        per CLAUDE.md's "every production data change leaves a trace" rule,
        and distinguishable later from a fetch attempt or any other kind of
        one-off fix. Does NOT touch content_refetch_log or content_html
        directly — resuming default-scope retry against the new URL, and
        the attempt-count reset that makes that possible, both fall out of
        _manual_review_article_ids' own cutoff logic once this row exists;
        nothing here needs to special-case that. Returns False (no write) if
        the article_id doesn't exist; True on a verified single-row update —
        same 'assert the write actually happened' discipline as CLAUDE.md's
        one-off-admin-fix guidance, just built into the library method since
        this is a repeatable path, not a one-off script."""
        row = self.conn.execute("SELECT url FROM articles WHERE id=?", (article_id,)).fetchone()
        if row is None:
            return False
        old_url = row[0] or ""
        try:
            cur = self.conn.execute("UPDATE articles SET url=? WHERE id=?", (new_url, article_id))
        except sqlite3.IntegrityError:
            # new_url already belongs to a different article (articles.url is
            # UNIQUE) — defensively roll back any implicit transaction this
            # statement opened, then let the caller decide how to report it
            # (webapp/app.py's CSV-import commit route reports it per-row and
            # keeps processing the rest of the batch, rather than the whole
            # request dying with a bare 500).
            self.conn.rollback()
            raise
        if cur.rowcount != 1:
            self.conn.rollback()
            return False
        self.conn.execute(
            "INSERT INTO url_correction_log (article_id, old_url, new_url, source, admin_id, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (article_id, old_url, new_url, source, admin_id, _now()),
        )
        self.conn.commit()
        return True

    def list_url_correction_log(self, limit: int = 200) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM url_correction_log ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def articles_needing_content_backfill(self, limit: int = 100000, force: bool = False,
                                           host_suffixes: list[str] | None = None) -> list[dict]:
        """Scope for the Reader content-structure backfill (Phase 5b) —
        mirrors unenriched()/all_articles()'s own resumability idiom exactly.
        Default (force=False): only rows with content_html still empty, so a
        stopped/crashed run and a deliberate re-run both just pick up where
        they left off, and articles a prior run already succeeded on are
        never re-fetched (rate-limit-friendly, and safe to press "start"
        again after a stop). ALSO excludes, in the default scope only:
        - any article whose most recent content_refetch_log attempt was
          reason='defunct-service' (linklib.pipeline._DEFUNCT_SERVICE_DOMAINS)
          — a confirmed-permanently-dead host (e.g. Google's discontinued
          FeedBurner proxy) can never succeed no matter how many times it's
          retried, so retrying it on every future batch would only burn
          fetch attempts and Wayback's own scarce rate-limit budget for a
          known outcome.
        - (Phase 5b follow-up #2) any article in the "needs manual review"
          tier (_manual_review_article_ids) — failed repeatedly enough
          (_MANUAL_REVIEW_ATTEMPT_THRESHOLD) that further automatic retries
          are unlikely to help without a human correcting the URL first.
          Distinct from defunct-service: NOT considered permanently dead
          (a Cloudflare block can lift, a 404 can be relinked), so it's
          reachable again the moment a correction resets its attempt count,
          not just via force=True.
        - (durability audit item 4) any article accept_article_content() has
          marked accepted-as-final (_accepted_content_ids) — the admin's
          explicit "this is fine as-is" call has to actually stick, not just
          hide the article from one list while the next backfill run
          silently re-fails and re-flags it.
        `force=True` re-runs every row with a saved URL, defunct-service and
        needs-manual-review both included — for standardizing the whole
        library after an extraction-logic change, or re-checking a domain
        that's since recovered, same escape hatch as the re-enrich job's own
        force option.

        `host_suffixes` (Medium-platform tier follow-through, 2026-08):
        when given, scopes to articles whose URL host matches one of the
        given suffixes (exact host match or a `.`-boundary subdomain match
        — same convention as linklib.medium_platform.is_medium_platform_host
        and _defunct_service_domain below) and, for those matching hosts
        ONLY, bypasses the needs-manual-review exclusion — the whole reason
        to scope a run to a specific host is usually to re-attempt exactly
        the articles that got stuck in manual review because the new fetch
        tier (e.g. Medium search) didn't exist yet when they were last
        tried. The defunct-service exclusion still applies even when
        host-scoped: a confirmed-dead host can't be un-dead by narrowing the
        run to it. `force` and `host_suffixes` may be combined (force wins
        on the manual-review/defunct-service exclusions; host_suffixes still
        narrows which rows are returned)."""
        if host_suffixes:
            suffixes = [s.strip().lower() for s in host_suffixes if s.strip()]

            def _host_matches(url: str) -> bool:
                from urllib.parse import urlsplit
                host = (urlsplit(url or "").netloc or "").lower().split(":")[0]
                if host.startswith("www."):
                    host = host[4:]
                return any(host == s or host.endswith("." + s) for s in suffixes)

            if force:
                rows = self.conn.execute(
                    "SELECT * FROM articles WHERE url!='' ORDER BY id"
                ).fetchall()
            else:
                rows = self.conn.execute(
                    """SELECT a.* FROM articles a
                       WHERE a.url!='' AND a.content_html=''
                         AND a.id NOT IN (
                           SELECT article_id FROM (
                             SELECT article_id, reason,
                                    ROW_NUMBER() OVER (PARTITION BY article_id ORDER BY attempted_at DESC) AS rn
                             FROM content_refetch_log
                           ) WHERE rn=1 AND reason='defunct-service'
                         )
                       ORDER BY a.id"""
                ).fetchall()
                accepted_ids = self._accepted_content_ids()
                rows = [r for r in rows if r["id"] not in accepted_ids]
            matched = [r for r in rows if _host_matches(r["url"])][:limit]
            return [self._row_to_dict(r) for r in matched]

        if force:
            rows = self.conn.execute(
                "SELECT * FROM articles WHERE url!='' ORDER BY id LIMIT ?", (limit,)
            ).fetchall()
            return [self._row_to_dict(r) for r in rows]

        manual_review_ids = self._manual_review_article_ids()
        rows = self.conn.execute(
            """SELECT a.* FROM articles a
               WHERE a.url!='' AND a.content_html=''
                 AND a.id NOT IN (
                   SELECT article_id FROM (
                     SELECT article_id, reason,
                            ROW_NUMBER() OVER (PARTITION BY article_id ORDER BY attempted_at DESC) AS rn
                     FROM content_refetch_log
                   ) WHERE rn=1 AND reason='defunct-service'
                 )
               ORDER BY a.id"""
        ).fetchall()
        accepted_ids = self._accepted_content_ids()
        rows = [r for r in rows if r["id"] not in manual_review_ids
                and r["id"] not in accepted_ids][:limit]
        return [self._row_to_dict(r) for r in rows]

    def count_structured_content(self) -> int:
        """Articles that actually have content_html populated — the real
        "Structured" count. Deliberately its own query rather than derived
        as `total - remaining`: once count_content_backfill_remaining()
        started excluding defunct-service articles too, that subtraction
        would silently misattribute an excluded-but-never-structured
        article as "done"."""
        return self.conn.execute(
            "SELECT COUNT(*) FROM articles WHERE url!='' AND content_html!=''"
        ).fetchone()[0]

    def _content_backfill_remaining_ids(self) -> set[int]:
        """The id set behind count_content_backfill_remaining() / the
        "Remaining" tile — factored out so the count and the
        never-attempted-vs-attempted-and-failed breakdown below can't drift
        apart the way `done_count = total - remaining` once did (see
        count_structured_content's docstring)."""
        base_ids = {
            r[0] for r in self.conn.execute(
                """SELECT a.id FROM articles a
                   WHERE a.url!='' AND a.content_html=''
                     AND a.id NOT IN (
                       SELECT article_id FROM (
                         SELECT article_id, reason,
                                ROW_NUMBER() OVER (PARTITION BY article_id ORDER BY attempted_at DESC) AS rn
                         FROM content_refetch_log
                       ) WHERE rn=1 AND reason='defunct-service'
                     )"""
            ).fetchall()
        }
        return base_ids - self._manual_review_article_ids() - self._accepted_content_ids()

    def count_content_backfill_remaining(self) -> int:
        """Matches articles_needing_content_backfill()'s default (non-force)
        scope exactly, including the defunct-service, needs-manual-review
        (Phase 5b follow-up #2), and accepted-as-final (durability audit
        item 4) exclusions — so this stat reads as "how many articles the
        next default-scope run will actually attempt," not an inflated
        count that includes articles already known permanently
        unrecoverable, parked for a human to correct, or explicitly
        accepted as-is."""
        return len(self._content_backfill_remaining_ids())

    def remaining_content_backfill_breakdown(self) -> dict:
        """Splits the Remaining tile's own id set (never-attempted vs.
        attempted-and-failed, grouped by the failed attempt's latest
        reason) — built for the dashboard-clarity pass so "Remaining" isn't
        just one opaque number. Reuses
        _content_backfill_remaining_ids() rather than re-deriving the scope,
        so this can never disagree with the tile's own count.

        Every id in this set either has zero content_refetch_log rows
        (never attempted — a fresh save, or one still waiting for its first
        backfill pass) or has a latest-attempt status of 'failure' with a
        reason other than 'defunct-service' (excluded from this set
        entirely) and an attempt count under
        _MANUAL_REVIEW_ATTEMPT_THRESHOLD (at or over that, it's in "Needs
        review" instead, not here) — 'success'/'accepted' latest attempts
        can't appear here either, since those already imply content_html is
        populated (Structured) or the article is durably excluded
        (Accepted as final). So "reason" here is always a real failure
        reason, never a placeholder.

        Returns {"never_attempted": int, "attempted_failed": int,
        "by_reason": {reason: count}} — by_reason counts only the
        attempted-failed subset, one entry per article (latest attempt
        only, same de-dupe as content_refetch_failure_counts)."""
        remaining_ids = self._content_backfill_remaining_ids()
        if not remaining_ids:
            return {"never_attempted": 0, "attempted_failed": 0, "by_reason": {}}
        placeholders = ",".join("?" * len(remaining_ids))
        rows = self.conn.execute(
            f"""SELECT article_id, reason FROM (
                  SELECT article_id, reason,
                         ROW_NUMBER() OVER (PARTITION BY article_id ORDER BY attempted_at DESC) AS rn
                  FROM content_refetch_log
                  WHERE article_id IN ({placeholders})
                ) WHERE rn=1""",
            tuple(remaining_ids),
        ).fetchall()
        attempted_ids = {r[0] for r in rows}
        by_reason: dict[str, int] = {}
        for _article_id, reason in rows:
            key = reason or "unknown"
            by_reason[key] = by_reason.get(key, 0) + 1
        return {
            "never_attempted": len(remaining_ids) - len(attempted_ids),
            "attempted_failed": len(attempted_ids),
            "by_reason": by_reason,
        }

    def count_permanently_excluded_content(self) -> int:
        """How many articles have been marked defunct-service on their most
        recent attempt — permanently excluded from the default backfill
        scope (see articles_needing_content_backfill). Surfaced on the admin
        page so Brian can see at a glance how many articles are being
        deliberately skipped, not just watch the remaining count quietly
        exclude them with no explanation."""
        return self.conn.execute(
            """SELECT COUNT(*) FROM (
                 SELECT article_id, reason,
                        ROW_NUMBER() OVER (PARTITION BY article_id ORDER BY attempted_at DESC) AS rn
                 FROM content_refetch_log
               ) WHERE rn=1 AND reason='defunct-service'"""
        ).fetchone()[0]

    def set_article_content_html(self, article_id: int, content_html: str) -> None:
        """Store the backfill's structured HTML for one article. Never
        touches `content` (the plain-text contract) or `updated_at` — this
        is enrichment-adjacent bookkeeping, not an edit to the article
        itself, same reasoning as embeddings living in their own table
        rather than bumping articles.updated_at on every embed.

        Also clears needs_content_check/content_check_reason (durability
        audit item 1): a successful structured re-fetch is direct proof the
        article's content is real, so any save-time quality flag on it is
        stale the moment this runs — see set_content_check_flag."""
        self.conn.execute(
            "UPDATE articles SET content_html=?, needs_content_check=0, "
            "content_check_reason='' WHERE id=?", (content_html, article_id)
        )
        self.conn.commit()

    def set_content_check_flag(self, article_id: int, needs_check: bool, reason: str = "") -> None:
        """Set or clear the save-time content-quality flag (durability audit
        item 1) — called by ingest_url right after extract.
        assess_extraction_quality() judges a fresh fetch, and again (cleared)
        by set_article_content_html whenever a later backfill succeeds.
        Never blocks or rejects the save itself; this only marks the row."""
        self.conn.execute(
            "UPDATE articles SET needs_content_check=?, content_check_reason=? WHERE id=?",
            (int(needs_check), reason if needs_check else "", article_id),
        )
        self.conn.commit()

    def count_needs_content_check(self) -> int:
        """How many articles are currently flagged at save time — surfaced
        as its own tile on /admin/reader/backfill-content, distinct from
        Remaining (which is every article with no content_html yet,
        structured-or-not, and says nothing about whether the save itself
        looked suspect)."""
        return self.conn.execute(
            "SELECT COUNT(*) FROM articles WHERE needs_content_check=1"
        ).fetchone()[0]

    def set_article_own_content(self, article_id: int, is_own: bool = True) -> None:
        """Mark (or, in principle, unmark) an article as Brian's own published
        writing — a provenance flag only, read solely by
        linklib.agent._build_source_documents/citations.extract_citations for
        citation labeling and never by retrieve()/_rrf_merge (no ranking
        effect — see the articles.is_own_content migration comment).

        Every real caller only ever sets True: sync_original_content_article
        (the 3 mirrored original_content rows, unconditionally, by
        construction) and pipeline.ingest_url (a future bookmarklet save
        whose URL matches a thought_leadership.url — see
        is_thought_leadership_url). Like needs_content_check's own clearing
        rule, this is a durable fact once learned; nothing clears it
        automatically."""
        self.conn.execute(
            "UPDATE articles SET is_own_content=? WHERE id=?",
            (int(bool(is_own)), article_id),
        )
        self.conn.commit()

    def is_thought_leadership_url(self, url: str) -> bool:
        """True when `url` (after the same normalize_url() canonicalization
        upsert() applies) matches a thought_leadership.url — the generic
        provenance-setting logic pipeline.ingest_url() uses to flag a future
        bookmarklet save of one of the externally-hosted pieces. A plain
        Python scan over thought_leadership (a few dozen rows) rather than a
        SQL comparison, since URLs need normalize_url() applied to both sides
        before comparing and thought_leadership.url is stored verbatim
        (whatever was typed into the admin form)."""
        target = normalize_url(url)
        if not target:
            return False
        rows = self.conn.execute(
            "SELECT url FROM thought_leadership WHERE url != ''"
        ).fetchall()
        return any(normalize_url(r["url"]) == target for r in rows)

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

    def set_setting(self, key: str, value: str, source: str | None = None) -> None:
        # Fixed 2026-09 (voice-enforcement PR, item 3b audit) — `set_setting`
        # is the one choke point every settings-backed copy field
        # (homepage_headline_copy, about_page_copy, htib_before_copy/
        # htib_after_copy, ...) is written through, from four separate
        # /admin/copy/* route handlers that call it directly with no
        # _voice_fix() of their own — a real, confirmed gap, the same shape
        # as the category_features one this PR was built around, just at
        # the route layer instead of a dedicated Library method. Fixing it
        # HERE, once, closes it for those four fields and for any future
        # settings-backed copy field with no new call site to remember.
        # Safe for every non-prose settings key too (caps, flags, model ids,
        # JSON blobs, tokens): normalize_voice_mechanics is a no-op on text
        # with no spaced em dash, which is every one of those.
        fixed = self._vf("settings", None, key, value, source=source) if isinstance(value, str) else value
        self.conn.execute(
            "INSERT INTO settings (key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, fixed),
        )
        self.conn.commit()

    # --- Voice review queue -------------------------------------------------
    # See linklib/voice_review_queue.py's module docstring for the full
    # design (why this is asynchronous/log-not-block). `_vf` is the
    # instrumented replacement for a bare `_voice_fix(...)` call: it
    # normalizes exactly the same way, and additionally logs an
    # `auto_corrected` row when the text actually changed. row_id is
    # nullable (a settings key has none, matching voice_db_scan's own
    # DbCopyViolation.row_id=None convention for settings). Deliberately
    # instrumented at a SUBSET of write paths, not every `_voice_fix(...)`
    # call site in this file (see this PR's own report for the exact list
    # and the reasoning) — `set_setting` (every /admin/copy/* field),
    # every tool/community/community_profile/category_features write path,
    # and `original_content` (added in this PR's own follow-up round,
    # since it holds Brian's own published thought leadership — the
    # single highest-value table for this whole feature) are covered.
    # `communities`/`community_profiles`/`benchmarks`/`thought_leadership`/
    # `ai_surfaces`' write methods still call bare `_voice_fix()` with no
    # queue logging — named explicitly in `tests/
    # test_voice_fix_coverage_ci_guard.py`'s allowlist as a disclosed
    # follow-up, not silently left uncovered.
    def _vf(self, table: str, row_id, column: str, value, source: str | None = None) -> str:
        if not isinstance(value, str) or not value:
            return value
        fixed = _voice_fix(value)
        if fixed != value:
            # Which rule actually fired (spaced-em-dash vs. invisible-
            # character) is derived from the PRE-fix text, since
            # normalize_voice_mechanics can apply more than one correction
            # in a single call — see voice_mechanics.correction_rule_for's
            # own docstring for why invisible-character is checked first.
            self.log_voice_correction(table, row_id, column, value, fixed,
                                       source=source, rule=_voice_fix_rule(value))
        return fixed

    def log_voice_correction(self, table: str, row_id, column: str,
                              before: str, after: str, source: str | None = None,
                              rule: str = "spaced-em-dash") -> None:
        """Record one `_voice_fix` correction as an `auto_corrected` review-
        queue row. No-op when before==after (nothing actually changed) —
        callers should still be free to call this unconditionally; see `_vf`
        above for the guarded wrapper most `Library` methods should use.

        `source` (2026-09, seed-sync infinite-loop investigation, extended
        2026-09 for issue #592 item 3) is one of 'admin-edit' (an /admin/*
        submit route saving a human's edit), 'startup-sync' (_seed_toolbox's
        per-boot re-sync against a static seed source), 'script' (a one-off
        backfill/fix script), 'submission' (a public, member-gated
        submission route — the content's origin is known but it's neither
        an admin edit nor a script/sync run), or 'scan' (a periodic
        background pass re-reading the live DB — `reconcile_voice_review_
        queue()`, not a human-run script) — or None when the caller doesn't
        yet pass one (every write path predating this parameter). Note: a
        `voice_review_queue` row can also carry `rule='seed-disagreement'`
        (via `add_seed_disagreement_item`) — that's a distinct dimension,
        the RULE that matched, not a `source` value; a seed-disagreement
        row's own `source` is always 'startup-sync', the mechanism that
        found the divergence. Purely descriptive: it changes nothing about
        whether/how the correction is logged, only what's recorded about
        who/what triggered it, so a reviewer at /admin/voice/review-queue
        can tell "an admin typed this" from "the seed sync did this again"
        without reverse-engineering it from the excerpt.

        `rule` (2026-09, invisible-character auto-strip) defaults to
        'spaced-em-dash' for every pre-existing caller that never passed it
        — `_vf` above passes the real rule explicitly, derived from the
        pre-fix text via `voice_mechanics.correction_rule_for`, since
        `normalize_voice_mechanics` can now apply more than one kind of
        correction in a single call."""
        if before == after:
            return
        self.conn.execute(
            "INSERT INTO voice_review_queue "
            "(table_name, row_id, column_name, rule, excerpt, before_text, after_text, status, created_at, source) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (table, str(row_id) if row_id is not None else None, column,
             rule, after[:200], before, after, "auto_corrected", _now(), source),
        )
        self.conn.commit()

    def is_voice_exception(self, table: str, row_id, column: str, rule: str) -> bool:
        """True when a specific (table, row_id, column, rule) combination has
        been marked 'Accept as exception' — this specific record/column/rule,
        never a global allowlist (see linklib.voice_review.AMPERSAND_NAMES/
        AMPERSAND_ACRONYMS for the global, source-side exceptions)."""
        row = self.conn.execute(
            "SELECT 1 FROM voice_review_queue WHERE table_name=? AND column_name=? AND rule=? "
            "AND status='exception' AND row_id IS ? LIMIT 1",
            (table, column, rule, str(row_id) if row_id is not None else None),
        ).fetchone()
        return row is not None

    def has_open_voice_review_item(self, table: str, row_id, column: str, rule: str) -> bool:
        """True when an `open` review-queue row already exists for this exact
        (table, row_id, column, rule) location — regardless of the excerpt
        text, since the same underlying violation at the same location only
        needs reviewing once. A distinct check from `is_voice_exception`:
        this is an unresolved PENDING review, not a permanent accepted
        exception, and the two are tracked separately (see 2026-09's
        double-insert incident — `scripts/backfill_voice_review_queue.py`
        used to dedupe against accepted exceptions only, so a re-scan after
        an unrelated rule change re-proposed every already-queued finding as
        if it were new). Matching drops the excerpt deliberately: a finding
        that's still open at this location shouldn't be re-queued a second
        time just because a scan re-run produced a slightly different
        excerpt for it (e.g. from an unrelated nearby edit) — it's the same
        review item either way. Once resolved (any status other than
        'open'), the same finding CAN legitimately be re-queued if it
        reappears — this only ever suppresses a duplicate of a still-open
        row."""
        row = self.conn.execute(
            "SELECT 1 FROM voice_review_queue WHERE table_name=? AND column_name=? AND rule=? "
            "AND status='open' AND row_id IS ? LIMIT 1",
            (table, column, rule, str(row_id) if row_id is not None else None),
        ).fetchone()
        return row is not None

    def add_voice_review_item(self, table: str, row_id, column: str, rule: str,
                               excerpt: str, source: str | None = "script") -> int:
        """Insert an `open` review-queue row (a scanner finding that can't be
        auto-corrected) — skipped when a matching exception already exists
        OR an open row for this exact location already exists (see
        `has_open_voice_review_item`'s own docstring for why both checks
        matter). `source` defaults to 'script' since the original caller is
        scripts/backfill_voice_review_queue.py, a one-off human-run backfill
        — but `reconcile_voice_review_queue()` (a periodic BACKGROUND pass,
        not a one-off script) is a second caller, and passes 'scan'
        explicitly rather than accepting this default, since the two are a
        genuinely different mechanism (see `log_voice_correction`'s own
        docstring for the full taxonomy)."""
        if self.is_voice_exception(table, row_id, column, rule):
            return 0
        if self.has_open_voice_review_item(table, row_id, column, rule):
            return 0
        cur = self.conn.execute(
            "INSERT INTO voice_review_queue "
            "(table_name, row_id, column_name, rule, excerpt, status, created_at, source) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (table, str(row_id) if row_id is not None else None, column, rule,
             excerpt, "open", _now(), source),
        )
        self.conn.commit()
        return cur.lastrowid

    def list_voice_review_queue(self, status: str | None = None) -> list[dict]:
        sql = "SELECT * FROM voice_review_queue"
        params: tuple = ()
        if status:
            sql += " WHERE status=?"
            params = (status,)
        sql += " ORDER BY rule, table_name, column_name, id"
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def count_open_voice_review_items(self) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM voice_review_queue WHERE status IN ('open','auto_corrected')"
        ).fetchone()
        return row[0] if row else 0

    def count_open_voice_review_seed_disagreements(self) -> int:
        """Subset of count_open_voice_review_items() whose source is
        'startup-sync' — _seed_toolbox's per-boot re-sync of a tools/
        communities/benchmarks row against its static seed-source value
        finding a live disagreement, not a genuine scanner/write-time voice
        violation. An ordinary, expected state, not an edge case — broken
        out so a caller (the /admin/checks summary) can label the review
        queue's own count apart from the database-copy scan's, which never
        counts these at all (see webapp.checks.voice_review_queue_status's
        own docstring for why the two totals legitimately differ)."""
        row = self.conn.execute(
            "SELECT COUNT(*) FROM voice_review_queue WHERE status IN ('open','auto_corrected') "
            "AND source='startup-sync'"
        ).fetchone()
        return row[0] if row else 0

    def get_voice_review_item(self, item_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM voice_review_queue WHERE id=?", (item_id,)
        ).fetchone()
        return dict(row) if row else None

    _VOICE_RESOLUTION_NOTES = {
        "accept": "Auto-fix accepted.",
        "revert": "Auto-fix reverted to the original text.",
        "edit": "Edited.",
        "accept_exception": "Allowed once.",
        "use_seed": "Replaced with the seed version.",
        "keep_mine": "Kept the stored value; seed version declined.",
    }

    def resolve_voice_review_item(self, item_id: int, action: str,
                                   edited_text: str | None = None) -> bool:
        """`action` is one of: 'accept' (confirm an auto-corrected change),
        'revert' (write `before_text` back to the live row), 'edit' (write
        `edited_text` back to the live row), 'accept_exception' (mark this
        specific record+column+rule as a deliberate, permanent exception —
        never resurfaces as `open` again, tracked separately from the
        global source-side ampersand allowlists), 'use_seed' (a
        'seed-disagreement' row only — write the seed's own text, i.e.
        `after_text`, back to the live row and resolve), or 'keep_mine' (a
        'seed-disagreement' row only — keep the currently-stored value
        untouched and mark this exact location a permanent exception, so
        the same divergence never reopens on a future sync). Returns False
        for an unknown id or action; the caller (the admin route) is
        responsible for actually writing `before_text`/`edited_text`/
        `after_text` back to the live table/column — this method only
        updates the queue row's own state, since it has no generic way to
        UPDATE an arbitrary table/column safely without a real column
        allowlist per table.

        Durability fix (2026-09, closing the "reopen loop" incident): also
        rejects an action that is meaningless for the item's own current
        `status` -- the exact gap that let a group's own "Accept selected"
        bulk button be run against a still-`open` row it was never valid
        for (`accept`/`revert` require `status='auto_corrected'`;
        `accept_exception`/`edit` require `status='open'`; `use_seed`/
        `keep_mine` require `rule='seed-disagreement'`, which is always
        `status='open'`). Calling `resolve_voice_review_item('accept', ...)`
        against an `open` row used to write nothing back and record no
        exception -- it read as resolved for exactly one 120s reconciler
        pass before `reconcile_voice_review_queue()` found the same live
        violation still there and reopened it as a brand-new row. Every
        remaining action on an `open` finding now provably ends it one of
        three ways: the text changes (`edit`), an exception is recorded
        (`accept_exception`), or (via the separate "Always allow" route) a
        term is always allowed -- never a status flip with nothing
        durable behind it."""
        item = self.get_voice_review_item(item_id)
        valid_actions = ("accept", "revert", "edit", "accept_exception", "use_seed", "keep_mine")
        if not item or action not in valid_actions:
            return False
        status = item["status"]
        if action in ("accept", "revert") and status != "auto_corrected":
            return False
        if action in ("accept_exception", "edit") and status != "open":
            return False
        if action in ("use_seed", "keep_mine") and item["rule"] != "seed-disagreement":
            return False
        new_status = {"accept": "resolved", "revert": "resolved",
                      "edit": "resolved", "accept_exception": "exception",
                      "use_seed": "resolved", "keep_mine": "exception"}[action]
        # Every action records how the row ended (checks-page follow-ups,
        # 2026-09), so the queue's "Resolved and exceptions" history reads
        # as what happened rather than a bare status. "Allow once" matches
        # "Always allowed as <term>." (approve_voice_term) in tense/shape.
        # A caller with a more specific note (the bulk ampersand replace)
        # overwrites this one right after.
        note = self._VOICE_RESOLUTION_NOTES[action]
        self.conn.execute(
            "UPDATE voice_review_queue SET status=?, reviewed_at=?, resolution_note=? WHERE id=?",
            (new_status, _now(), note, item_id),
        )
        self.conn.commit()
        return True

    # A small, separate allowlist from `linklib.voice_db_scan._SCAN_TABLES`
    # (which is prose-column-only, feeding the voice scanner) — these are
    # boolean columns that can also land a 'seed-disagreement' item (via
    # `add_seed_disagreement_item`) when `_seed_toolbox()` finds the stored
    # value disagrees with the seed list, same mechanism as the five text
    # fields, just for a boolean instead of free text. Never scanned for
    # voice violations (they hold no prose), so they deliberately do NOT
    # live in `_SCAN_TABLES` — only here, for `apply_voice_review_write`/
    # `get_voice_review_current_value` to recognize.
    _SEED_BOOLEAN_COLUMNS: tuple[tuple[str, str, str], ...] = (
        ("tools", "id", "advisor"),
        ("communities", "id", "advisor"),
    )

    def apply_voice_review_write(self, table: str, row_id, column: str, text: str) -> bool:
        """Writes `text` back to the live `(table, row_id, column)` cell —
        the generic counterpart `resolve_voice_review_item`'s docstring
        says it deliberately doesn't do itself. Validated against
        `linklib.voice_db_scan._SCAN_TABLES` (the same enumeration the
        scanner reads from) plus the settings special case and
        `_SEED_BOOLEAN_COLUMNS` (boolean seed-disagreement columns, e.g.
        tools.advisor/communities.advisor — not voice-scanned, but still a
        real seed-disagreement write target), so this can never be pointed
        at an arbitrary table/column from outside those known lists —
        there is no free-text table/column parameter reaching this from an
        admin form, only whatever the queue row itself already recorded.
        For a boolean column, `text` is the literal string "True"/"False"
        (as stored by `add_seed_disagreement_item`'s callers) and is
        written as 1/0. Returns False for an unrecognized table/column or
        a settings row with no key (row_id None but table != 'settings')."""
        from .voice_db_scan import _SCAN_TABLES
        if table == "settings":
            self.conn.execute(
                "UPDATE settings SET value=? WHERE key=?", (text, column)
            )
            self.conn.commit()
            return True
        for tname, id_col, columns, _exempt in _SCAN_TABLES:
            if tname == table and column in columns and row_id is not None:
                self.conn.execute(
                    f"UPDATE {table} SET {column}=? WHERE {id_col}=?", (text, row_id)
                )
                self.conn.commit()
                return True
        for tname, id_col, col in self._SEED_BOOLEAN_COLUMNS:
            if tname == table and column == col and row_id is not None:
                self.conn.execute(
                    f"UPDATE {table} SET {column}=? WHERE {id_col}=?",
                    (1 if text == "True" else 0, row_id),
                )
                self.conn.commit()
                return True
        return False

    def get_voice_review_current_value(self, table: str, row_id, column: str) -> str | None:
        """The FULL, CURRENT live value of `(table, row_id, column)` — the
        read-side counterpart of `apply_voice_review_write`, validated the
        identical way (against `linklib.voice_db_scan._SCAN_TABLES`, the
        settings special case, and `_SEED_BOOLEAN_COLUMNS`). Exists
        specifically so the review-queue UI's "Edit" action can pre-fill
        with the real current column value instead of the row's own
        `excerpt` (a mid-text snippet on a real scanner finding, or a
        display label like "U+200B (zero-width space)" on an
        invisible-character finding — neither is the full column value,
        and saving either back verbatim would truncate or replace real
        published copy with a fragment or a label; see CLAUDE.md's Part 4
        writeup for the incident this closes). A boolean column reads back
        as the literal string "True"/"False", matching what
        `add_seed_disagreement_item`'s boolean callers store. Returns None
        when the table/column isn't recognized or the row no longer exists
        — the caller falls back to the queue row's own stored text in that
        case, since there's nothing live left to read."""
        from .voice_db_scan import _SCAN_TABLES
        if table == "settings":
            row = self.conn.execute("SELECT value FROM settings WHERE key=?", (column,)).fetchone()
            return row[0] if row else None
        for tname, id_col, columns, _exempt in _SCAN_TABLES:
            if tname == table and column in columns and row_id is not None:
                row = self.conn.execute(
                    f"SELECT {column} FROM {table} WHERE {id_col}=?", (row_id,)
                ).fetchone()
                return row[0] if row else None
        for tname, id_col, col in self._SEED_BOOLEAN_COLUMNS:
            if tname == table and column == col and row_id is not None:
                row = self.conn.execute(
                    f"SELECT {column} FROM {table} WHERE {id_col}=?", (row_id,)
                ).fetchone()
                return None if not row else ("True" if row[0] else "False")
        return None

    def preview_ampersand_replacement(self, item_id: int) -> dict | None:
        """Issue #592 item 4 — computes what a bulk "Replace & with and"
        action would do to one open review-queue row, WITHOUT writing
        anything. Reads the FULL, CURRENT live value (same
        `get_voice_review_current_value` every other write-back path here
        uses — never the queue row's own excerpt/before/after, which can
        be a stale mid-text snippet), then runs it through
        `linklib.voice_review.replace_spaced_ampersands` against the
        currently-approved bare-ampersand terms.

        Returns None for an unknown item or one whose table/column isn't
        recognized by `get_voice_review_current_value` (nothing to preview
        against). Otherwise a dict with `item_id`/`table`/`row_id`/
        `column`/`before`/`after`/`changed` — `changed` is False when
        `text` had no eligible (spaced, unprotected) ampersand to replace
        at all, e.g. it's only "S&M"-style unspaced text, or every spaced
        ampersand present is inside an approved term — the caller should
        treat that row as untouched, left open for a manual decision, not
        as an error."""
        from .voice_review import replace_spaced_ampersands
        item = self.get_voice_review_item(item_id)
        if not item:
            return None
        current = self.get_voice_review_current_value(item["table_name"], item["row_id"], item["column_name"])
        if current is None:
            return None
        approved_terms = [t["term"] for t in self.list_approved_voice_terms("bare-ampersand")]
        new_text, changed = replace_spaced_ampersands(current, approved_terms)
        return {
            "item_id": item_id, "table": item["table_name"], "row_id": item["row_id"],
            "column": item["column_name"], "before": current, "after": new_text, "changed": changed,
        }

    def apply_ampersand_replacement(self, item_id: int) -> bool:
        """Writes the replacement `preview_ampersand_replacement` computes
        and resolves the row — but ONLY when there's a real change to make.
        Re-derives the replacement fresh against the CURRENT live value
        (not whatever a caller's earlier preview call showed), the same
        TOCTOU discipline every other preview-then-confirm flow in this
        codebase uses (bulk-delete, the manual-review/purge CSV round
        trips) — a value edited between preview and apply is replaced
        against its own current text, never a stale preview snapshot.

        Returns False (writes nothing, resolves nothing) when the item/row
        no longer exists, or when there's nothing eligible to replace
        (an unspaced-only or fully-approved-term field) — that row stays
        open, exactly as `preview_ampersand_replacement`'s own `changed`
        flag promises, so a genuinely no-op selection can never silently
        resolve a row nobody actually looked at. Returns True once the
        write succeeds and the row is resolved with a `resolution_note`
        explaining the bulk action, not a plain per-row "edit"."""
        preview = self.preview_ampersand_replacement(item_id)
        if preview is None or not preview["changed"]:
            return False
        ok = self.apply_voice_review_write(preview["table"], preview["row_id"], preview["column"], preview["after"])
        if not ok:
            return False
        self.resolve_voice_review_item(item_id, "edit", preview["after"])
        self.conn.execute(
            "UPDATE voice_review_queue SET resolution_note=? WHERE id=?",
            ('Replaced: spaced ampersand(s) changed to "and" in bulk; any '
             "unspaced or approved-term ampersand in the same field was left untouched.",
             item_id),
        )
        self.conn.commit()
        return True

    def add_seed_disagreement_item(self, table: str, row_id, column: str,
                                    stored_value: str, seed_value: str,
                                    source: str | None = "startup-sync") -> int:
        """Record a divergence between a live stored value and what a static
        seed source (scripts/seed_tools.py, scripts/seed_communities.py,
        webapp/app.py's _DEFAULT_BENCHMARKS) says it should be — the
        replacement for `_seed_toolbox()`'s old behavior of silently
        overwriting the stored value on every boot (see CLAUDE.md's Part 1
        writeup, the seed-sync-overwrite fix). Rule is always
        'seed-disagreement', a distinct rule from every mechanical/
        typography finding, with its own pair of resolution actions
        ('use_seed'/'keep_mine' — see `resolve_voice_review_item`).
        `before_text` is the CURRENTLY STORED value, `after_text` is the
        SEED's proposed value — "use seed version" applies `after_text`;
        "keep mine" leaves the stored value untouched and marks this exact
        (table, row_id, column) a permanent exception.

        Also used for a boolean column (see `_SEED_BOOLEAN_COLUMNS`, e.g.
        tools.advisor/communities.advisor) — the same shape, just with
        `stored_value`/`seed_value` as the literal strings "True"/"False"
        rather than free text; `apply_voice_review_write`/
        `get_voice_review_current_value` both recognize that convention.

        Deduplicated exactly like `add_voice_review_item`: skipped when this
        exact (table, row_id, column) is already a permanent exception
        (an admin already said "keep mine" — never reopens), or when an
        `open` row already exists for it (repeated boots must not queue the
        same divergence twice — the seed-sync infinite-loop bug this whole
        mechanism replaces). Returns 0 when skipped for either reason, else
        the new row's id."""
        rule = "seed-disagreement"
        if self.is_voice_exception(table, row_id, column, rule):
            return 0
        if self.has_open_voice_review_item(table, row_id, column, rule):
            return 0
        cur = self.conn.execute(
            "INSERT INTO voice_review_queue "
            "(table_name, row_id, column_name, rule, excerpt, before_text, after_text, status, created_at, source) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (table, str(row_id) if row_id is not None else None, column, rule,
             seed_value[:200], stored_value, seed_value, "open", _now(), source),
        )
        self.conn.commit()
        return cur.lastrowid

    # --- Voice review queue: globally-approved terms ("Always allow") ------

    def list_approved_voice_terms(self, rule: str | None = None) -> list[dict]:
        sql = "SELECT * FROM voice_approved_terms"
        params: tuple = ()
        if rule:
            sql += " WHERE rule=?"
            params = (rule,)
        sql += " ORDER BY term COLLATE NOCASE"
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def is_approved_voice_term(self, rule: str, text: str) -> bool:
        """True when `text` contains (case-insensitively) any term already
        approved for `rule`. A standalone, ad-hoc "has this term already
        been approved" check — NOT the mechanism the live DB scanner
        actually uses to mask approved terms out of a bare-ampersand scan
        (that's `voice_review.mask_approved_ampersand_terms`, called from
        `voice_db_scan.scan_db_copy_report` with the full approved-terms
        list read once per scan via `list_approved_voice_terms`, not this
        per-value method). Confirmed by inspection: this method currently
        has no live caller anywhere in the app — kept as a small, tested,
        directly-usable primitive for a future call site (e.g. an admin UI
        that wants to show "already approved" before offering the Approve
        action), not because anything reads it today."""
        if not text:
            return False
        low = text.lower()
        for row in self.list_approved_voice_terms(rule):
            if row["term"].lower() in low:
                return True
        return False

    def approve_voice_term(self, term: str, rule: str = "bare-ampersand") -> int:
        """Globally, permanently approve `term` for `rule` — "this specific
        matched term is always fine, everywhere." Resolves every currently
        OPEN queue row of the same `rule` whose stored excerpt/before/after
        text contains `term` (case-insensitive), all at once, with a
        `resolution_note` saying so. Idempotent on the term itself (a
        second approval of the identical term is a no-op insert, still
        re-sweeps open rows in case one was queued since). Returns the new
        (or existing) row's id."""
        term = (term or "").strip()
        if not term:
            raise ValueError("A term to approve is required.")
        existing = self.conn.execute(
            "SELECT id FROM voice_approved_terms WHERE rule=? AND term=? COLLATE NOCASE",
            (rule, term),
        ).fetchone()
        if existing:
            term_id = existing[0]
        else:
            cur = self.conn.execute(
                "INSERT INTO voice_approved_terms (term, rule, created_at) VALUES (?,?,?)",
                (term, rule, _now()),
            )
            self.conn.commit()
            term_id = cur.lastrowid
        low_term = term.lower()
        note = f'Always allowed as "{term}".'
        for item in self.list_voice_review_queue(status="open"):
            if item["rule"] != rule:
                continue
            haystack = " ".join(filter(None, [item.get("excerpt"), item.get("before_text"), item.get("after_text")])).lower()
            if low_term in haystack:
                self.conn.execute(
                    "UPDATE voice_review_queue SET status='resolved', reviewed_at=?, resolution_note=? WHERE id=?",
                    (_now(), note, item["id"]),
                )
        self.conn.commit()
        return term_id

    def remove_approved_voice_term(self, term_id: int) -> None:
        """Removing an approved term makes it flaggable again on the NEXT
        scan/reconciliation pass — it does not retroactively reopen queue
        rows already resolved by the earlier approval; that history stays
        (see `voice_review_queue.resolution_note`)."""
        self.conn.execute("DELETE FROM voice_approved_terms WHERE id=?", (term_id,))
        self.conn.commit()

    def reconcile_voice_review_queue(self) -> dict:
        """Bidirectional sync between the live DB scan
        (`voice_db_scan.scan_db_copy`) and `voice_review_queue` — closes a
        real 2026-09 gap in both directions, found when /admin/checks'
        scan count and the review queue's open count disagreed:

        1. Write-time logging (`Library._vf`/`log_voice_correction`) only
           ever captures AUTO-CORRECTIONS (things `normalize_voice_mechanics`
           can fix mechanically, like a spaced em dash). It never captures a
           finding that needs human judgment (a bare ampersand, a banned
           word) — nothing writes those to the queue except a scan. A new
           violation of that kind entering the DB via an ordinary admin save
           sat invisible in the live scan but never reached the queue until
           someone manually re-ran the backfill script.
        2. The reverse was also broken: fixing an ampersand/banned-word
           violation directly on a record's own admin edit page (bypassing
           the queue's own resolution actions) makes it disappear from the
           scan, but its OPEN queue row stayed open forever, since nothing
           closed it.

        Run periodically from the background checks refresher
        (`webapp.tasks`), not on every request — a full DB scan is real
        work, the same reasoning `run_all()`'s own TTL cache already uses.
        Never touches 'seed-disagreement' rows (those come from
        `_seed_toolbox()`'s own sync pass, not this scanner, and have their
        own separate lifecycle) or already-resolved/exception rows.
        Returns {"added": n, "closed": n} for the caller to log/report."""
        from . import voice_db_scan

        live = voice_db_scan.scan_db_copy(self)
        live_keys = {
            (v.table, str(v.row_id) if v.row_id is not None else None, v.column, v.rule)
            for v in live
        }

        added = 0
        for v in live:
            # add_voice_review_item already dedupes internally against both
            # an existing exception and an existing open row (see its own
            # docstring) — it returns 0, never inserting, in either case, so
            # a truthy return here always means a genuinely new row.
            # 2026-09 follow-up (issue #592 item 3) — 'scan', not 'script':
            # this is a periodic background pass re-reading the live DB,
            # not a one-off human-run script (scripts/
            # backfill_voice_review_queue.py, which shares this exact
            # insertion path via add_voice_review_item's own 'script'
            # default). The two were previously indistinguishable at this
            # call site, which is exactly the ambiguity a reviewer at
            # /admin/voice/review-queue shouldn't have to guess past.
            new_id = self.add_voice_review_item(v.table, v.row_id, v.column, v.rule, v.excerpt, source="scan")
            if new_id:
                added += 1

        closed = 0
        # Every rule the live DB scan can actually produce (mechanical_findings'
        # four rules plus typography_findings_plain's two) — deliberately
        # excludes 'seed-disagreement' (a different mechanism, _seed_toolbox's
        # own sync pass, not this scanner) and 'holistic' (Claude-judged, never
        # scanned automatically), so this reconciliation only ever closes a
        # row this exact scan could have produced or reproduced.
        scanned_rules = {"buzzword", "filler", "performative", "invisible-character",
                         "bare-ampersand", "spaced-em-dash"}
        for item in self.list_voice_review_queue(status="open"):
            if item["rule"] not in scanned_rules:
                continue
            key = (item["table_name"], item["row_id"], item["column_name"], item["rule"])
            if key not in live_keys:
                self.conn.execute(
                    "UPDATE voice_review_queue SET status='resolved', reviewed_at=?, resolution_note=? WHERE id=?",
                    (_now(), "Resolved outside the queue — no longer found on the last scan.", item["id"]),
                )
                closed += 1
        self.conn.commit()
        return {"added": added, "closed": closed}

    # --- thin-fetch audit dismissals (2026-09, JS-render grounding fix) ---
    # See thin_fetch_audit_dismissals's own schema comment for why this
    # exists as a genuinely new, explicit per-record marker rather than an
    # attempt to derive "already fixed" from needs_verification/
    # low_confidence/ai_confident — verified against real production data
    # (Lumera vs. Paylocity) that none of those three actually distinguish
    # the two cases.

    def dismiss_thin_fetch_audit_finding(self, entity_type: str, entity_id: int,
                                         field_name: str, note: str = "") -> None:
        """Record that a human has reviewed this exact (entity, field)
        finding from scripts/audit_thin_fetch_grounding.py and confirmed the
        current content is fine — an upsert, since re-dismissing (e.g. with
        a fresher note) after a later regeneration is a normal, expected
        action, not an error."""
        self.conn.execute(
            """INSERT INTO thin_fetch_audit_dismissals
                   (entity_type, entity_id, field_name, dismissed_at, note)
               VALUES (?,?,?,?,?)
               ON CONFLICT(entity_type, entity_id, field_name)
               DO UPDATE SET dismissed_at=excluded.dismissed_at, note=excluded.note""",
            (entity_type, entity_id, field_name, _now(), (note or "").strip()),
        )
        self.conn.commit()

    def undismiss_thin_fetch_audit_finding(self, entity_type: str, entity_id: int,
                                           field_name: str) -> None:
        """Removing a dismissal makes the finding reappear on the audit's
        next run (if it still matches) — never automatic; only a human
        deciding a prior dismissal was wrong should call this."""
        self.conn.execute(
            "DELETE FROM thin_fetch_audit_dismissals WHERE entity_type=? AND entity_id=? AND field_name=?",
            (entity_type, entity_id, field_name),
        )
        self.conn.commit()

    def list_thin_fetch_audit_dismissals(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM thin_fetch_audit_dismissals ORDER BY dismissed_at DESC"
        ).fetchall()]

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
                    name: str = "", email: str = "",
                    password_change_recommended: bool = True) -> int:
        """Create an account. Raises sqlite3.IntegrityError if the username exists.

        password_change_recommended defaults True — every account created here
        was given a password by an admin, not chosen by the account holder, so
        it starts flagged for the dismissible change-password nudge (see the
        password_change_recommended migration comment). Pass False only for a
        caller that's genuinely not in that shape (none exist today; kept as a
        real parameter rather than hardcoded so a future self-registration
        flow, if one is ever built, isn't forced to flag itself)."""
        from .passwords import hash_password
        username = (username or "").strip().lower()
        role = role if role in ("user", "admin") else "user"
        cur = self.conn.execute(
            "INSERT INTO users (username, password_hash, role, active, name, email, created_at, "
            "password_change_recommended) VALUES (?,?,?,1,?,?,?,?)",
            (username, hash_password(password), role, name.strip(), email.strip(), _now(),
             int(password_change_recommended)),
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
            "ask_cap_usd, matchmaker_cap_usd, password_change_recommended "
            "FROM users ORDER BY role DESC, username"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_user(self, username: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT id, username, role, active, name, email, created_at, last_login_at, "
            "ask_cap_usd, matchmaker_cap_usd, password_change_recommended "
            "FROM users WHERE username=?", ((username or "").strip().lower(),)
        ).fetchone()
        return dict(row) if row else None

    def get_user_by_id(self, user_id: int) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT id, username, role, active, name, email, created_at, last_login_at, "
            "ask_cap_usd, matchmaker_cap_usd, password_change_recommended "
            "FROM users WHERE id=?", (user_id,)
        ).fetchone()
        return dict(row) if row else None

    # -- MCP server API tokens (/mcp, Phase 1) --
    #
    # A token resolves to a real users.id + that user's CURRENT role (read
    # live off `users` on every verify, not snapshotted at mint time) so a
    # role change or deactivation takes effect on the very next call, not
    # after the token is re-minted.

    def create_api_token(self, user_id: int, label: str = "") -> tuple[int, str]:
        """Mint a new token for user_id. Returns (token row id, plaintext) —
        the plaintext is returned exactly once and never stored; only its
        sha256 hash lands in the DB. Caller (scripts/mint_api_token.py) is
        responsible for printing it and never logging/persisting it again."""
        token = secrets.token_urlsafe(32)  # 256 bits of entropy
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        now = datetime.now(timezone.utc).isoformat()
        cur = self.conn.execute(
            "INSERT INTO api_tokens (token_hash, user_id, label, created_at) VALUES (?, ?, ?, ?)",
            (token_hash, user_id, (label or "").strip(), now),
        )
        self.conn.commit()
        return cur.lastrowid, token

    def verify_api_token(self, token: str) -> Optional[dict]:
        """Resolve a presented plaintext token to {id, user_id, username,
        role} — or None if it's missing, unknown, revoked, or its owning
        user is inactive. Never raises on a bad token; the caller (the /mcp
        auth gate and each tool's own re-check) treats None as "refuse."

        Updates last_used_at on a successful verify (best-effort — a failed
        UPDATE here must never turn a valid token into a rejected one, so
        it's wrapped defensively rather than left to propagate)."""
        if not token:
            return None
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        row = self.conn.execute(
            "SELECT t.id AS token_id, t.user_id AS user_id, u.username AS username, "
            "u.role AS role, u.active AS active "
            "FROM api_tokens t JOIN users u ON u.id = t.user_id "
            "WHERE t.token_hash=? AND t.revoked_at=''",
            (token_hash,),
        ).fetchone()
        if not row or not row["active"]:
            return None
        try:
            self.conn.execute(
                "UPDATE api_tokens SET last_used_at=? WHERE id=?",
                (datetime.now(timezone.utc).isoformat(), row["token_id"]),
            )
            self.conn.commit()
        except Exception:
            pass
        return {"user_id": row["user_id"], "username": row["username"], "role": row["role"]}

    def revoke_api_token(self, token_id: int) -> bool:
        """Marks a token revoked. Returns True iff a row was actually
        updated (an unknown/already-revoked id is a no-op, not an error)."""
        cur = self.conn.execute(
            "UPDATE api_tokens SET revoked_at=? WHERE id=? AND revoked_at=''",
            (datetime.now(timezone.utc).isoformat(), token_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def list_api_tokens(self, user_id: Optional[int] = None) -> list[dict]:
        """Never returns token_hash — this is for a human/script to see
        labels, owners, and usage, not to recover or compare tokens."""
        sql = ("SELECT t.id, t.user_id, u.username, t.label, t.created_at, "
               "t.revoked_at, t.last_used_at FROM api_tokens t "
               "JOIN users u ON u.id = t.user_id")
        args: tuple = ()
        if user_id is not None:
            sql += " WHERE t.user_id=?"
            args = (user_id,)
        sql += " ORDER BY t.created_at DESC"
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def default_admin_user_id(self) -> Optional[int]:
        """The earliest-created admin account's id, or None if there isn't
        one (local dev / break-glass-only setups with no real `users` row).

        Same query and same "attribute it to the earliest admin" reasoning
        as _migrate_read_later_user_scope's one-time backfill — this is the
        live-request counterpart, used to resolve a user_id for a
        token-authenticated write that has no session to read one from
        (e.g. the Read Later bookmarklet/Shortcut, which posts with a save
        token and no login cookie, so _current_user_id has nothing to work
        with)."""
        row = self.conn.execute(
            "SELECT id FROM users WHERE role='admin' ORDER BY id LIMIT 1"
        ).fetchone()
        return row[0] if row else None

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

    def set_password_change_recommended(self, user_id: int, recommended: bool) -> None:
        """Set or clear the dismissible change-password nudge flag.

        Deliberately separate from set_user_password rather than folded into
        it — the two call sites that change a password want opposite
        outcomes: an admin resetting someone else's password (/admin/users)
        sets this True (they didn't choose it), while the account holder
        setting their own new password (self-service /reset-password, or
        /change-password) clears it. Folding this into set_user_password
        would force one of those two call sites to immediately undo it."""
        self.conn.execute(
            "UPDATE users SET password_change_recommended=? WHERE id=?",
            (int(recommended), user_id),
        )
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

    # -- off-site backups (Phase O) ------------------------------------------

    def record_backup_attempt(self, status: str, filename: str = "", drive_file_id: str = "",
                               size_bytes: int = 0, row_count: int = 0, error: str = "") -> int:
        """Log one linklib.backup.backup_now() attempt, success or failure.
        See the backup_log CREATE TABLE comment for why this exists and who
        calls it."""
        cur = self.conn.execute(
            "INSERT INTO backup_log (status, filename, drive_file_id, bytes, row_count, error, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (status, filename, drive_file_id, size_bytes, row_count, error, _now()),
        )
        self.conn.commit()
        return cur.lastrowid

    def list_backup_log(self, limit: int = 100) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM backup_log ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def most_recent_successful_backup_at(self) -> str:
        """created_at of the most recent status='success' backup_log row, or
        '' if there has never been one. One indexed query (idx_backup_log_
        created), cheap enough for the admin nav badge's own open_task_counts
        pass — see webapp.tasks.open_task_counts' "stale backup" entry, which
        is what this exists for: the daily Railway Cron Service hits
        POST /admin/backup-now roughly once every 24h, so no successful row
        in ~26h means either that cron stopped firing or every recent
        attempt has been failing — either way, worth a badge, not silence."""
        row = self.conn.execute(
            "SELECT created_at FROM backup_log WHERE status='success' "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        return row["created_at"] if row else ""

    # -- integrity checks (durability audit item 2) --------------------------

    def record_integrity_check(self, status: str, detail: str = "") -> int:
        """Log one linklib.backup.check_integrity() run, ok or failure. See
        the integrity_check_log CREATE TABLE comment for why this exists and
        who calls it."""
        cur = self.conn.execute(
            "INSERT INTO integrity_check_log (status, detail, created_at) VALUES (?,?,?)",
            (status, detail, _now()),
        )
        self.conn.commit()
        return cur.lastrowid

    def list_integrity_check_log(self, limit: int = 100) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM integrity_check_log ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    # -- background-job run history (durability audit item 3) ----------------

    def start_job_run(self, job_name: str) -> int:
        """Log the start of one background-job run (re-enrich, Historical
        sweep, or the Reader content backfill) — see job_run_log's CREATE
        TABLE comment. Returns the row id, passed back to finish_job_run
        when the job exits (success, failure, or a deliberate stop)."""
        cur = self.conn.execute(
            "INSERT INTO job_run_log (job_name, status, started_at) VALUES (?,?,?)",
            (job_name, "running", _now()),
        )
        self.conn.commit()
        return cur.lastrowid

    def finish_job_run(self, run_id: int, status: str, summary: str = "", error: str = "") -> None:
        """Close out a job_run_log row started by start_job_run. `status` is
        'success' | 'failure' | 'stopped' — never 'running' again (a run
        that never reaches this call, e.g. a crash, is exactly what leaves
        finished_at='' forever, which latest_job_run surfaces as-is rather
        than guessing)."""
        self.conn.execute(
            "UPDATE job_run_log SET status=?, summary=?, error=?, finished_at=? WHERE id=?",
            (status, summary, error, _now(), run_id),
        )
        self.conn.commit()

    def latest_job_run(self, job_name: str) -> Optional[dict]:
        """Most recent job_run_log row for one job, or None if it's never
        run. Backs each job's admin-page "last run: outcome, N ago" line —
        the durable fallback for exactly the case _JOB_STATE can't cover: a
        redeploy or crash since the last run, when in-process state is
        gone."""
        row = self.conn.execute(
            "SELECT * FROM job_run_log WHERE job_name=? ORDER BY started_at DESC LIMIT 1",
            (job_name,),
        ).fetchone()
        return dict(row) if row else None

    def list_job_run_log(self, job_name: str, limit: int = 20) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM job_run_log WHERE job_name=? ORDER BY started_at DESC LIMIT ?",
            (job_name, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # -- Reader content-structure backfill (Phase 5b) ------------------------

    def log_content_refetch_attempt(self, article_id: int, status: str,
                                     reason: str = "", detail: str = "",
                                     source: str = "direct",
                                     exa_cost_usd: float = 0.0) -> int:
        """One row per re-fetch attempt — see content_refetch_log's CREATE
        TABLE comment for why every attempt is logged, not just failures.
        `source` ('direct' | 'wayback') distinguishes a Wayback-archived
        success from a normal live-fetch success — see linklib.wayback and
        linklib.pipeline.backfill_article_content. `exa_cost_usd` (2026-09,
        Exa cost-tracking foundation) is the real compute_exa_cost() total
        for every Exa call this attempt made — the domain-migration and/or
        Medium-platform tiers, whichever actually ran — win or miss; 0.0 for
        an attempt that never reached Exa at all (a direct-fetch success, a
        defunct-service skip, or a Wayback-only path with no migration/Medium
        tier applicable)."""
        cur = self.conn.execute(
            "INSERT INTO content_refetch_log (article_id, status, reason, detail, source, exa_cost_usd, attempted_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (article_id, status, reason, detail, source, exa_cost_usd, _now()),
        )
        self.conn.commit()
        return cur.lastrowid

    def list_content_refetch_log(self, limit: int = 200) -> list[dict]:
        rows = self.conn.execute(
            """SELECT l.*, a.title AS article_title, a.url AS article_url
               FROM content_refetch_log l LEFT JOIN articles a ON a.id = l.article_id
               ORDER BY l.attempted_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    # -- manual-review "accept as final" override (durability audit item 4) -

    def accept_article_content(self, article_id: int) -> bool:
        """Escape hatch for a false positive in the manual-review tier: an
        article with real-but-short content (under
        extract._MIN_CONTENT_WORDS) fails assess_extraction_quality()
        identically forever, with no way out short of a DB edit — a URL
        correction can't help, since the URL is already correct.

        Composes with _manual_review_article_ids() by writing a new
        content_refetch_log row with a THIRD status value, 'accepted' —
        distinct from 'success'/'failure' — rather than a new column or a
        special-cased reason string. That query already only looks at each
        article's MOST RECENT attempt and requires status='failure', so an
        'accepted' row as the latest attempt removes the article from the
        manual-review list for free, with no change needed there. The same
        latest-row idiom is reused in articles_needing_content_backfill()'s
        default scope and count_content_backfill_remaining() to also pull an
        accepted article OUT of automatic retry — durable, not just hidden
        from one list — so a future backfill run can't silently overwrite an
        admin's "this is fine as-is" call with a fresh failure row.

        The prior failure reason (if any) is copied onto the accepted row
        itself, purely for display (see list_accepted_content) and so
        unaccept_article_content can restore it without a second query.
        Per-article only — no bulk/select-all variant exists on purpose,
        this is a deliberate one-at-a-time override, not a backfill
        mechanism. Returns False if the article doesn't exist."""
        row = self.conn.execute("SELECT id FROM articles WHERE id=?", (article_id,)).fetchone()
        if row is None:
            return False
        last = self.conn.execute(
            "SELECT reason FROM content_refetch_log WHERE article_id=? "
            "ORDER BY attempted_at DESC LIMIT 1", (article_id,)
        ).fetchone()
        prior_reason = (last["reason"] if last else "") or ""
        self.conn.execute(
            "INSERT INTO content_refetch_log (article_id, status, reason, detail, source, attempted_at) "
            "VALUES (?,'accepted',?,?,?,?)",
            (article_id, prior_reason, "accepted as final by admin override", "accept", _now()),
        )
        self.conn.commit()
        return True

    def unaccept_article_content(self, article_id: int) -> bool:
        """Undo accept_article_content — restores the article to whatever
        state it would be in had it never been accepted, by writing a new
        'failure' row carrying the same reason the accepted row had
        recorded. Deliberately additive (content_refetch_log's full history
        is never mutated or deleted, same non-destructive precedent as
        everywhere else in this codebase) rather than deleting the
        'accepted' row — the fact that it WAS accepted, and later reversed,
        stays visible in the log. Returns False if the article was never
        accepted (or the accepted state has already been superseded by a
        later log row of any kind)."""
        latest = self.conn.execute(
            "SELECT status, reason FROM content_refetch_log WHERE article_id=? "
            "ORDER BY attempted_at DESC LIMIT 1", (article_id,)
        ).fetchone()
        if latest is None or latest["status"] != "accepted":
            return False
        self.conn.execute(
            "INSERT INTO content_refetch_log (article_id, status, reason, detail, source, attempted_at) "
            "VALUES (?,'failure',?,?,?,?)",
            (article_id, latest["reason"] or "too-thin",
             "un-accepted by admin — resumes normal retry/manual-review scoring", "accept", _now()),
        )
        self.conn.commit()
        return True

    def list_accepted_content(self, limit: int = 500) -> list[dict]:
        """Every article currently accepted-as-final (latest content_refetch_log
        row has status='accepted') — backs the admin page's "Accepted as
        final" section, the mirror of list_articles_needing_manual_review
        for the escape hatch this composes with."""
        rows = self.conn.execute(
            """WITH latest AS (
                 SELECT l.article_id, l.status, l.reason, l.attempted_at,
                        ROW_NUMBER() OVER (PARTITION BY l.article_id ORDER BY l.attempted_at DESC) AS rn
                 FROM content_refetch_log l
               )
               SELECT lt.article_id AS article_id, art.title AS title, art.url AS current_url,
                      lt.reason AS reason, lt.attempted_at AS accepted_at
               FROM latest lt
               JOIN articles art ON art.id = lt.article_id
               WHERE lt.rn=1 AND lt.status='accepted'
               ORDER BY lt.attempted_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def count_content_accepted(self) -> int:
        return self.conn.execute(
            """SELECT COUNT(*) FROM (
                 SELECT article_id, status,
                        ROW_NUMBER() OVER (PARTITION BY article_id ORDER BY attempted_at DESC) AS rn
                 FROM content_refetch_log
               ) WHERE rn=1 AND status='accepted'"""
        ).fetchone()[0]

    def _accepted_content_ids(self) -> set[int]:
        """Article ids whose most recent content_refetch_log attempt is
        status='accepted' — the exclusion set articles_needing_content_backfill()
        and count_content_backfill_remaining() both subtract out, so an
        accepted article is durably out of automatic retry, not just hidden
        from the manual-review list."""
        return {
            r[0] for r in self.conn.execute(
                """SELECT article_id FROM (
                     SELECT article_id, status,
                            ROW_NUMBER() OVER (PARTITION BY article_id ORDER BY attempted_at DESC) AS rn
                     FROM content_refetch_log
                   ) WHERE rn=1 AND status='accepted'"""
            ).fetchall()
        }

    def content_refetch_failure_counts(self) -> dict[str, int]:
        """Failure count by reason, most-recent-attempt-per-article only —
        so an article that failed once and later succeeded on a re-run
        doesn't keep inflating the failure tally shown on the admin page."""
        rows = self.conn.execute(
            """SELECT reason, COUNT(*) FROM (
                 SELECT article_id, status, reason,
                        ROW_NUMBER() OVER (PARTITION BY article_id ORDER BY attempted_at DESC) AS rn
                 FROM content_refetch_log
               ) WHERE rn=1 AND status='failure' GROUP BY reason"""
        ).fetchall()
        return {r[0] or "unknown": r[1] for r in rows}

    def content_refetch_failure_domains(self, limit: int = 15) -> list[dict]:
        """Failure count by source domain, most-recent-attempt-per-article
        only (same de-dupe as content_refetch_failure_counts) — lets an
        admin tell "several independent dead links" from "one host
        systematically blocking/throttling this tool" before a full run
        repeats whatever's wrong across every article from that source.
        Domain is derived from the article's URL in Python (no clean
        host-extraction in SQL), so this is O(failed articles), not indexed
        — fine at this table's realistic size (thousands of rows at most)."""
        from urllib.parse import urlsplit
        rows = self.conn.execute(
            """SELECT a.url FROM (
                 SELECT article_id, status,
                        ROW_NUMBER() OVER (PARTITION BY article_id ORDER BY attempted_at DESC) AS rn
                 FROM content_refetch_log
               ) l JOIN articles a ON a.id = l.article_id
               WHERE l.rn=1 AND l.status='failure'"""
        ).fetchall()
        counts: dict[str, int] = {}
        for (url,) in rows:
            host = (urlsplit(url or "").netloc or "unknown").lower()
            if host.startswith("www."):
                host = host[4:]
            counts[host] = counts.get(host, 0) + 1
        return sorted(
            [{"domain": d, "count": c} for d, c in counts.items()],
            key=lambda x: -x["count"],
        )[:limit]

    def count_wayback_content(self) -> int:
        """How many articles currently have content_html sourced from a
        Wayback Machine snapshot rather than a direct fetch — the same
        latest-attempt-per-article de-dupe as content_refetch_failure_counts,
        restricted to successes. Surfaced on the admin page so it's obvious
        at a glance how much of the archive is running on a possibly-stale
        archived copy, not just visible per-row in the attempts log."""
        return self.conn.execute(
            """SELECT COUNT(*) FROM (
                 SELECT article_id, status, source,
                        ROW_NUMBER() OVER (PARTITION BY article_id ORDER BY attempted_at DESC) AS rn
                 FROM content_refetch_log
               ) WHERE rn=1 AND status='success' AND source='wayback'"""
        ).fetchone()[0]

    def count_migration_content(self) -> int:
        """How many articles currently have content_html sourced from the
        known-domain-migration tier (linklib.pipeline._DOMAIN_MIGRATIONS,
        Phase 5b follow-up #2) rather than a direct fetch or Wayback —
        same shape/reasoning as count_wayback_content()."""
        return self.conn.execute(
            """SELECT COUNT(*) FROM (
                 SELECT article_id, status, source,
                        ROW_NUMBER() OVER (PARTITION BY article_id ORDER BY attempted_at DESC) AS rn
                 FROM content_refetch_log
               ) WHERE rn=1 AND status='success' AND source='migration'"""
        ).fetchone()[0]

    def count_medium_search_content(self) -> int:
        """How many articles currently have content_html sourced from the
        Medium-platform Exa search-by-title tier (linklib/medium_platform.py)
        rather than a direct fetch, Wayback, the domain-migration tier, or
        the fetch-by-URL variant of this same tier — same shape/reasoning as
        count_wayback_content()/count_migration_content(). See
        count_medium_fetch_content() for the fetch-by-URL sibling
        (source='medium-fetch', 2026-08 wrap-up sprint)."""
        return self.conn.execute(
            """SELECT COUNT(*) FROM (
                 SELECT article_id, status, source,
                        ROW_NUMBER() OVER (PARTITION BY article_id ORDER BY attempted_at DESC) AS rn
                 FROM content_refetch_log
               ) WHERE rn=1 AND status='success' AND source='medium-search'"""
        ).fetchone()[0]

    def count_medium_fetch_content(self) -> int:
        """How many articles currently have content_html sourced from a
        direct Exa fetch-by-URL of the article's own current URL
        (linklib.medium_platform.fetch_content_by_url, tried before
        search-by-title in the Medium-platform tier — 2026-08 wrap-up
        sprint item 1) — same shape/reasoning as
        count_medium_search_content()."""
        return self.conn.execute(
            """SELECT COUNT(*) FROM (
                 SELECT article_id, status, source,
                        ROW_NUMBER() OVER (PARTITION BY article_id ORDER BY attempted_at DESC) AS rn
                 FROM content_refetch_log
               ) WHERE rn=1 AND status='success' AND source='medium-fetch'"""
        ).fetchone()[0]

    def _wayback_429_retry_ids(self) -> set[int]:
        """Article ids whose most recent content_refetch_log attempt failed
        specifically because the Wayback fallback itself was rate-limited
        (HTTP 429) — see linklib.wayback's module docstring on archive.org's
        unpredictable/broad 429s. `_finish_backfill_via_wayback` logs these
        as an ordinary failure row (status='failure', reason=the original
        direct-fetch reason, source='direct') with the Wayback outcome
        folded into `detail` as `"...(wayback: HTTP 429)"` or
        `"wayback: HTTP 429"` — there's no dedicated status/reason value for
        this, so the only way to find them is this substring match on the
        latest attempt's detail.

        Deliberately keyed on the latest attempt's FAILURE REASON, not an
        attempt count — distinct from _manual_review_article_ids(), whose
        3-in-a-row threshold an article can cross well before or well after
        landing here. An article rate-limited on Wayback may still be well
        under that threshold and already eligible for the ordinary default-
        scope sweep's next pass — but archive.org's 429s were observed
        broadly and unpredictably (not a one-off), so nothing guarantees a
        later ordinary pass ever revisits it before it accumulates enough
        failures to fall into manual review anyway, where the only paths
        back out (a URL correction, "Accept as final") don't fit a
        transient rate-limit at all. This is the targeted fix: retry
        exactly this set, once, on demand.

        `a.content_html=''` guards against a stale/impossible state (the
        latest logged attempt says failure but content_html is somehow
        already populated) rather than assuming the log and the article
        row can never disagree."""
        rows = self.conn.execute(
            """WITH latest AS (
                 SELECT article_id, status, detail,
                        ROW_NUMBER() OVER (PARTITION BY article_id ORDER BY attempted_at DESC) AS rn
                 FROM content_refetch_log
               )
               SELECT a.id FROM articles a
               JOIN latest l ON l.article_id = a.id AND l.rn = 1
               WHERE l.status='failure' AND l.detail LIKE '%wayback: HTTP 429%'
                 AND a.url!='' AND a.content_html=''"""
        ).fetchall()
        return {r[0] for r in rows}

    def count_wayback_429_retry_candidates(self) -> int:
        return len(self._wayback_429_retry_ids())

    def list_wayback_429_retry_candidates(self, limit: int = 100000) -> list[dict]:
        """Full article rows for _wayback_429_retry_ids(), ordered by id —
        backs both the admin preview list and the retry sweep's own fetch
        loop (linklib.pipeline.backfill_article_content expects a full
        article dict, same shape articles_needing_content_backfill()'s rows
        already provide)."""
        ids = sorted(self._wayback_429_retry_ids())[:limit]
        if not ids:
            return []
        placeholders = ",".join("?" * len(ids))
        rows = self.conn.execute(
            f"SELECT * FROM articles WHERE id IN ({placeholders}) ORDER BY id", ids
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

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
        /admin/tools/software/name-duplicates as an actionable "pick which to keep" row,
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
        Powers the /admin/tools/software/name-duplicates review view. O(n log n) grouping
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

    # Character budgets — a soft TARGET (the live counter, webapp.app.
    # _char_budget, turns amber past it, but the save still works) and a hard
    # MAX (the save is refused, nothing is written) — for every admin text
    # field known to be AI-drafted and prone to drift, per a production
    # length read (2026-09-23):
    #   tools.agent_taxonomy_note          longest 3,540 (133 rows over the old 1,200 cap)
    #   tools.description                  longest 2,339 (0 over the old 2,500 cap)
    #   tools.competitive_differentiation  longest   546 (0 over the old 600 cap)
    #   tools.summary                      longest   453 (35 rows over the old 400 cap —
    #                                                      previously guarded by a conditional
    #                                                      maxlength patch, see admin_tools_edit)
    #   community_profiles.stage_focus     longest   440 (1 row over the old 300 cap)
    # Every MAX below clears its own field's longest stored value with real
    # headroom, so nothing already saved becomes unsavable — same "the cap
    # sits below the longest value" discipline CATEGORY_FEATURE_TEXT_MAX
    # established. Nothing here ever shortens a value; a save over MAX is
    # refused outright with an error naming both numbers (see
    # _check_text_field_length below), same as CATEGORY_FEATURE_TEXT_MAX/
    # FEATURE_LINK_PUBLIC_NOTE_MAX's own _check_* methods.
    TOOL_DESCRIPTION_TARGET = 2_500
    TOOL_DESCRIPTION_MAX = 3_500
    TOOL_SUMMARY_TARGET = 400
    TOOL_SUMMARY_MAX = 800
    TOOL_AGENT_TAXONOMY_TARGET = 2_500
    TOOL_AGENT_TAXONOMY_MAX = 4_000
    TOOL_DIFFERENTIATION_TARGET = 600
    TOOL_DIFFERENTIATION_MAX = 1_200
    @classmethod
    def _check_text_field_length(cls, label: str, value: str, limit: int) -> None:
        """Shared hard-limit refusal for a character-budgeted field, generalizing
        _check_category_feature_text/_check_feature_link_public_note's identical
        pattern to every field added since. A save over `limit` raises and writes
        nothing; the caller's own soft TARGET (shown by the live counter, never
        enforced here) never blocks a save."""
        n = cls.text_budget_length(value)
        if n > limit:
            raise ValueError(
                f"{label} is {n:,} characters; the limit is {limit:,}. "
                f"Nothing was saved. Shorten it and try again."
            )

    def add_tool(self, name: str, description: str, url: str,
                 categories: list[str], submitted_by: str = "",
                 approved: int = 0, advisor: int = 0,
                 promoted: int = 0, vendor_email: str = "",
                 warm_intro_enabled: int = 0, vendor_name: str = "",
                 summary: str = "",
                 description_needs_verification: int = 0,
                 description_ai_confident: Optional[int] = None,
                 description_low_confidence: Optional[int] = None,
                 needs_review: int = 1, source: str | None = None) -> int:
        # needs_review defaults to 1 (not the column's own SQL default of 0)
        # — a brand-new tool's profile should read as "needs review" until
        # someone actually signs off on it, not "already reviewed" by
        # default. See the needs_review migration comment for the SQL-
        # default-vs-Python-default split this relies on. Applies to every
        # caller (admin add-form, public /tools/submit, seed scripts) unless
        # one explicitly passes 0 — none does today.
        # description_needs_verification/description_ai_confident (Citations-API
        # grounding fix, Phase 2): a brand-new tool created straight from a
        # Generate-description draft used to have no way to record either —
        # add_tool() simply didn't accept them, so a fresh AI draft on the
        # "Add software" form silently landed as needs_verification=0/
        # ai_confident=NULL regardless of what the model actually reported.
        # Default 0/None (not drafted) so every OTHER existing caller
        # (public /tools/submit, seed scripts, tests) is unaffected — only
        # the admin new-tool submit route passes an explicit value, mirroring
        # update_tool's own explicit-int convention at its one real caller.
        dup = self._find_tool_by_normalized_url(url)
        if dup:
            raise DuplicateURLError("software entry", dup["id"], dup["name"], dup["slug"])
        self._check_text_field_length("Description", description.strip(), self.TOOL_DESCRIPTION_MAX)
        self._check_text_field_length("Short summary", summary.strip(), self.TOOL_SUMMARY_MAX)
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
        description_before, summary_before = description.strip(), summary.strip()
        description_fixed, summary_fixed = _voice_fix(description_before), _voice_fix(summary_before)
        cur = self.conn.execute(
            """INSERT INTO tools (name, slug, description, url, categories_json,
               approved, advisor, submitted_by, created_at, updated_at, promoted, vendor_email,
               warm_intro_enabled, vendor_name, summary,
               description_needs_verification, description_ai_confident, description_low_confidence,
               needs_review)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (name.strip(), slug, description_fixed, url.strip(),
             json.dumps(categories), approved, advisor, submitted_by.strip(), now, now,
             promoted, vendor_email.strip(), warm_intro_enabled, vendor_name.strip(),
             summary_fixed, description_needs_verification, description_ai_confident,
             description_low_confidence, 1 if needs_review else 0),
        )
        self.conn.commit()
        new_id = cur.lastrowid
        # Logged AFTER insert, using the real row id — no id exists until
        # the INSERT itself returns one (same reason add_original_content/
        # add_category_feature do the same). A pre-existing gap from the
        # original voice-review-queue PR — add_tool called bare
        # _voice_fix() with no queue logging, never caught because that
        # PR's own CI guard accepted a bare _voice_fix() call as
        # sufficient; found and fixed here by the same tightened guard
        # that closed the tool_categories/community_categories gap.
        self.log_voice_correction("tools", new_id, "description", description_before, description_fixed, source=source, rule=_voice_fix_rule(description_before))
        self.log_voice_correction("tools", new_id, "summary", summary_before, summary_fixed, source=source, rule=_voice_fix_rule(summary_before))
        return new_id

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

    def get_tool_by_name(self, name: str) -> dict | None:
        """Exact (case-insensitive) name lookup — used by the Feature Taxonomy
        seed script (scripts/seed_feature_taxonomy.py), which must resolve a
        CSV's tool name against a real row or abort with a clear report,
        never guess. Unlike find_tool_name_duplicate this is not a fuzzy
        collision check; it returns None on anything but an exact match."""
        row = self.conn.execute(
            "SELECT * FROM tools WHERE name = ? COLLATE NOCASE", (name.strip(),)
        ).fetchone()
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
                    summary: str = "",
                    description_needs_verification: Optional[int] = None,
                    description_ai_confident: Optional[int] = None,
                    description_low_confidence: Optional[int] = None,
                    clear_description_verification_stamp: bool = False, source: str | None = None) -> None:
        # description_needs_verification defaults to None ("leave the column
        # alone") rather than 0/1, because update_tool is also the bulk-edit
        # panel's write path (every row resaved at once) and
        # scripts/archive/fix_corpay_category.py's one-off fix path — neither of
        # those callers knows or should guess whether this particular save
        # followed a fresh Generate click, so they simply don't pass it and
        # the flag stays whatever it already was. Only the admin edit-submit
        # route (which reads the ai_drafted_fields signal) passes an explicit
        # 0 or 1. COALESCE keeps that "unless told otherwise" behavior a
        # plain UPDATE can't express on its own.
        #
        # clear_description_verification_stamp (2026-08, stale-stamp fix) is a
        # DIFFERENT, explicit signal from description_needs_verification=1 —
        # the edit-submit route passes both together (same underlying "field
        # ai-drafted this save" boolean), but scripts/regen_ai_drafted_fields.py
        # forces description_needs_verification=0 (so no review badge shows)
        # while still passing this =True, since it's still a fresh,
        # not-yet-human-reviewed draft that must not go on showing an old
        # "Verified by X on Y" line. Never inferred from the needs_verification
        # value itself for exactly that reason. Defaults False so every other
        # caller (bulk-edit, one-off scripts, tests) is unaffected.
        #
        # Only check when the URL is actually changing — callers that resave a
        # row unchanged (e.g. the bulk-edit routes, which always pass the
        # row's own current url back) must never trip on a pre-existing
        # duplicate elsewhere in the table that has nothing to do with this edit.
        current = self.get_tool(tool_id)
        if current and normalize_url(url) != normalize_url(current["url"]):
            dup = self._find_tool_by_normalized_url(url, exclude_id=tool_id)
            if dup:
                raise DuplicateURLError("software entry", dup["id"], dup["name"], dup["slug"])
        self._check_text_field_length("Description", description.strip(), self.TOOL_DESCRIPTION_MAX)
        self._check_text_field_length("Short summary", summary.strip(), self.TOOL_SUMMARY_MAX)
        self.conn.execute(
            """UPDATE tools SET name=?, description=?, url=?, categories_json=?,
               advisor=?, promoted=?, vendor_email=?, warm_intro_enabled=?, vendor_name=?,
               summary=?,
               description_needs_verification=COALESCE(?, description_needs_verification),
               description_ai_confident=COALESCE(?, description_ai_confident),
               description_low_confidence=COALESCE(?, description_low_confidence),
               updated_at=? WHERE id=?""",
            (name.strip(), self._vf("tools", tool_id, "description", description.strip(), source=source), url.strip(),
             json.dumps(categories), advisor, promoted, vendor_email.strip(),
             warm_intro_enabled, vendor_name.strip(), self._vf("tools", tool_id, "summary", summary.strip(), source=source),
             description_needs_verification, description_ai_confident,
             description_low_confidence, _now(), tool_id),
        )
        self.conn.commit()
        if clear_description_verification_stamp:
            self._supersede_narrative_review("tool", "description", tool_id)
        # Manual logo override staleness (2026-08): a URL edit that changes
        # the domain never touches logo_path/logo_manual_override itself —
        # silently dropping the override would lose a real correction, and
        # silently keeping it could paper the new site with the old one's
        # logo with no signal anything changed. Flag only; surfaced as a
        # banner on the edit page (see _logo_admin_section).
        if current and current.get("logo_manual_override") and _url_domain_changed(current["url"], url):
            self.conn.execute(
                "UPDATE tools SET logo_override_stale=1 WHERE id=?", (tool_id,)
            )
            self.conn.commit()

    def update_tool_content(self, tool_id: int, name: str, description: str, source: str | None = None) -> None:
        """Narrow update for scripts/seed_tools.py's re-sync pass (#113): touches only
        name and description, leaving categories/advisor/promoted/vendor/warm-intro
        fields untouched so a content refresh can never clobber admin edits made
        directly on the live site after seeding."""
        self.conn.execute(
            "UPDATE tools SET name=?, description=?, updated_at=? WHERE id=?",
            (name.strip(), self._vf("tools", tool_id, "description", description.strip(), source=source), _now(), tool_id),
        )
        self.conn.commit()

    def quick_update_tool(self, tool_id: int, description: str,
                          warm_intro_enabled: int, vendor_name: str,
                          vendor_email: str, summary: str = "", source: str | None = None) -> None:
        """Partial update for the /tools inline "Quick edit" panel — touches
        only description/summary and warm-intro fields, leaving name/url/
        categories/advisor/promoted untouched (those still require the full
        edit form)."""
        self._check_text_field_length("Description", description.strip(), self.TOOL_DESCRIPTION_MAX)
        self._check_text_field_length("Short summary", summary.strip(), self.TOOL_SUMMARY_MAX)
        self.conn.execute(
            """UPDATE tools SET description=?, warm_intro_enabled=?, vendor_name=?,
               vendor_email=?, summary=?, updated_at=? WHERE id=?""",
            (self._vf("tools", tool_id, "description", description.strip(), source=source), warm_intro_enabled, vendor_name.strip(),
             vendor_email.strip(), self._vf("tools", tool_id, "summary", summary.strip(), source=source), _now(), tool_id),
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
        'reject', or 'merge').

        Cascade fix (2026-08, the Pave/Culpepper/Radford removal
        investigation): this used to leave tool_feature_links and
        entity_citations rows orphaned — nothing reads them once the tool
        is gone, so per CLAUDE.md's "no dead data" rule they get deleted
        here too, and any pending feature_review_queue proposal naming this
        tool is denied (never silently left pointing at a deleted tool) —
        same deny-with-a-reason precedent scripts/remap_queue_to_framework.py
        already uses for a consolidated-away or out-of-scope proposal.
        tool_leads, tool_competitors, tool_name_dedupe_decisions, and
        narrative_review_log are deliberately NOT touched by this cascade —
        tool_competitors/tool_name_dedupe_decisions are cleaned below (their
        own pre-existing cascade), and tool_leads/narrative_review_log
        survive on purpose, same precedent as tool_audit_log surviving a
        deleted tool: they're historical record (an intro-request log, an
        append-only verification trail), not current state a deleted tool
        needs to keep correct."""
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
        self.conn.execute("DELETE FROM tool_feature_links WHERE tool_id=?", (tool_id,))
        self.conn.execute(
            "DELETE FROM entity_citations WHERE entity_type='tool' AND entity_id=?", (tool_id,)
        )
        self.conn.execute(
            """UPDATE feature_review_queue SET status='denied', resolved_at=?,
               resolution_note=? WHERE tool_id=? AND status='pending'""",
            (_now(), "tool deleted", tool_id),
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

    def update_tool_differentiation(self, tool_id: int, competitive_differentiation: str,
                                     needs_verification: int = 0,
                                     ai_confident: Optional[int] = None,
                                     low_confidence: Optional[int] = None,
                                     clear_verification_stamp: bool = False, source: str | None = None) -> None:
        """Narrow update for the admin full-edit form's "How this differs from
        the competition" field (Phase 3) — same reasoning as
        quick_update_tool: kept separate from update_tool so the Software
        bulk-edit panel, which re-saves every other field on every call, can
        never silently blank this one out just because it doesn't know about it.
        needs_verification defaults to 0 (verified) so every existing caller
        that predates Phase G PR 2 — tests, scripts — keeps its old
        behavior unchanged; the edit-submit route is the only caller that
        passes an explicit 1, when this save's `ai_drafted_fields` names
        `competitive_differentiation` (a fresh AI draft this session, not yet
        confirmed) — same "unconfirmed until an explicit Mark verified click"
        contract agent_taxonomy_needs_verification already established,
        just collapsed into this one write path since, unlike Agent
        taxonomy, Differentiation has no separate Refresh route — Generate
        is AJAX-only and this Save is the only place a draft ever gets
        persisted. ai_confident (2026-08, confidence indicator) mirrors
        needs_verification's own write contract — a real value only from
        the edit-submit route on a fresh draft this session, None everywhere
        else — but is a DIFFERENT, independent fact from needs_verification:
        the UI displays it permanently regardless of needs_verification's
        value (2026-08 policy revision — see CLAUDE.md's "Confidence
        indicator" bullet), so there's no need to clear a stale value on
        every unrelated resave the way needs_verification itself must be.

        clear_verification_stamp (2026-08, stale-stamp fix) is a separate,
        explicit signal from needs_verification=1 — the edit-submit route
        passes both together (same "field ai-drafted this save" boolean),
        but scripts/regen_ai_drafted_fields.py forces needs_verification=0
        while still passing this =True, since it's still a fresh,
        not-yet-human-reviewed draft. See update_tool's matching parameter
        for the full reasoning; never inferred from needs_verification's
        value itself."""
        self._check_text_field_length("Bottom line", competitive_differentiation.strip(), self.TOOL_DIFFERENTIATION_MAX)
        self.conn.execute(
            "UPDATE tools SET competitive_differentiation=?, competitive_differentiation_needs_verification=?, "
            "competitive_differentiation_ai_confident=COALESCE(?, competitive_differentiation_ai_confident), "
            "competitive_differentiation_low_confidence=COALESCE(?, competitive_differentiation_low_confidence), "
            "updated_at=? WHERE id=?",
            (self._vf("tools", tool_id, "competitive_differentiation", competitive_differentiation.strip(), source=source),
             needs_verification, ai_confident,
             low_confidence, _now(), tool_id),
        )
        self.conn.commit()
        if clear_verification_stamp:
            self._supersede_narrative_review("tool", "differentiation", tool_id)

    def set_tool_suite_note(self, tool_id: int, suite_note: str, source: str | None = None) -> None:
        """Narrow update for tools.suite_note (Feature Taxonomy rules doc §5's
        "beyond the office of the CFO" case — a standard, reusable notation
        that a vendor offers a broader suite of operational solutions, e.g.
        NetSuite's CRM/HRIS). Same narrow-single-column-update pattern as
        update_tool_agent_taxonomy/update_tool_screenshot_url — deliberately
        NOT part of the general update_tool path, so the Software bulk-edit
        panel (which resaves every field it knows about on every call) can
        never blank it out on an unrelated save."""
        self.conn.execute(
            "UPDATE tools SET suite_note=?, updated_at=? WHERE id=?",
            (self._vf("tools", tool_id, "suite_note", suite_note.strip(), source=source), _now(), tool_id),
        )
        self.conn.commit()

    def update_tool_agent_taxonomy(self, tool_id: int, agent_taxonomy_note: str, source: str | None = None) -> None:
        """Narrow update for the admin full-edit form's agent-taxonomy field
        (Phase 5) — same bulk-edit-safety reasoning as update_tool_differentiation.
        A human editing/saving this field is itself a confirmation, so this
        always clears agent_taxonomy_needs_verification — same convention the
        retired tool_features rows used to follow (editing a row implied
        review).

        entity_citations (Citations-API grounding fix, Phase 1b): cleared
        only when the note's text actually changed (see
        voice_mechanics.norm_for_compare — whitespace-only edits, CRLF vs LF
        and NULL vs "" all count as unchanged). A hand-typed note has no
        citation trace to keep, and leaving a prior AI draft's citations
        attached to text a human just overwrote would misattribute the
        human's own words as machine-grounded. But an UNCHANGED note (the
        admin form re-posts the whole note on every Save, including right
        after "Generate summary" persisted a fresh draft and its sources in
        a separate request) is still exactly the text those citations
        describe, so its rows are left alone. The stored-value read, the
        UPDATE and the clear happen in one transaction so a concurrent
        Generate can't land between the compare and the clear."""
        self._check_text_field_length("Agent taxonomy", agent_taxonomy_note.strip(), self.TOOL_AGENT_TAXONOMY_MAX)
        # _vf may commit its own queue-log row, so run it before the
        # transaction below opens.
        new_note = self._vf("tools", tool_id, "agent_taxonomy_note", agent_taxonomy_note.strip(), source=source)
        if not self.conn.in_transaction:
            self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute(
                "SELECT agent_taxonomy_note FROM tools WHERE id=?", (tool_id,)
            ).fetchone()
            changed = norm_for_compare(row["agent_taxonomy_note"] if row else None) != norm_for_compare(new_note)
            self.conn.execute(
                "UPDATE tools SET agent_taxonomy_note=?, agent_taxonomy_needs_verification=0, "
                "updated_at=? WHERE id=?",
                (new_note, _now(), tool_id),
            )
            if changed:
                self._write_entity_citations("tool", tool_id, "agent_taxonomy", [], "")
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def set_tool_agent_taxonomy_draft(self, tool_id: int, agent_taxonomy_note: str,
                                      needs_verification: int = 1,
                                      ai_confident: Optional[int] = None,
                                      low_confidence: Optional[int] = None, source: str | None = None) -> None:
        """Records an LLM-drafted agent-taxonomy summary (automated research —
        either the auto-run-on-add background task or the on-demand refresh)
        as unconfirmed by default. Only writes when the tool doesn't already
        have a note, unless the caller explicitly wants to overwrite (the
        on-demand "Refresh" action passes needs_verification the same way but
        the caller decides whether to call this at all — see the refresh
        route, which always overwrites; the auto-on-add path only calls this
        for a brand-new tool that has nothing yet).

        ai_confident (2026-08 follow-up, confidence indicator) is the raw
        "confident" self-report from the same generation call —
        COALESCE-written so a caller that somehow omits it (there isn't one
        today; both trigger points always have a real AgentTaxonomyResult)
        can't accidentally blank out a previously-recorded signal.

        Unconditionally supersedes any live narrative_review_log stamp
        (2026-08, stale-stamp fix) — unlike Description/Differentiation/
        Community profile, this method has no hand-edit-confirms sibling
        call path (that's update_tool_agent_taxonomy); every call here,
        whether from the live "Refresh" route or scripts/
        regen_ai_drafted_fields.py, is by construction a fresh, not-yet-
        human-reviewed AI draft, so no extra parameter is needed to gate it
        the way update_tool/update_tool_differentiation/
        upsert_community_profile need one."""
        self.conn.execute(
            "UPDATE tools SET agent_taxonomy_note=?, agent_taxonomy_needs_verification=?, "
            "agent_taxonomy_ai_confident=COALESCE(?, agent_taxonomy_ai_confident), "
            "agent_taxonomy_low_confidence=COALESCE(?, agent_taxonomy_low_confidence), "
            "updated_at=? WHERE id=?",
            (self._vf("tools", tool_id, "agent_taxonomy_note", agent_taxonomy_note.strip(), source=source),
             needs_verification, ai_confident,
             low_confidence, _now(), tool_id),
        )
        self.conn.commit()
        self._supersede_narrative_review("tool", "agent_taxonomy", tool_id)

    def mark_tool_agent_taxonomy_verified(self, tool_id: int) -> None:
        """One-click "Mark verified" action — clears the flag without
        touching the text. (The retired tool_features table had the
        equivalent per-row action; this is the tools-table-level version.)"""
        self.conn.execute(
            "UPDATE tools SET agent_taxonomy_needs_verification=0, updated_at=? WHERE id=?",
            (_now(), tool_id),
        )
        self.conn.commit()

    def mark_tool_description_verified(self, tool_id: int) -> None:
        """One-click "Mark verified" for the Description field (Phase G PR 2)
        — same shape as mark_tool_agent_taxonomy_verified."""
        self.conn.execute(
            "UPDATE tools SET description_needs_verification=0, updated_at=? WHERE id=?",
            (_now(), tool_id),
        )
        self.conn.commit()

    def mark_tool_differentiation_verified(self, tool_id: int) -> None:
        """One-click "Mark verified" for the Differentiation/Bottom-line
        field (Phase G PR 2) — same shape as mark_tool_agent_taxonomy_verified."""
        self.conn.execute(
            "UPDATE tools SET competitive_differentiation_needs_verification=0, updated_at=? WHERE id=?",
            (_now(), tool_id),
        )
        self.conn.commit()

    def set_tool_needs_review(self, tool_id: int, needs_review: int) -> None:
        """Whole-record profile signoff (2026-08) — the manual checkbox on
        the tool edit form writes here directly, same as
        community_profiles.needs_review's own checkbox. Deliberately its own
        narrow single-column update, not folded into update_tool's larger
        COALESCE-based write, so it's obviously independent of the three
        per-field flags update_tool/update_tool_differentiation/
        update_tool_agent_taxonomy touch — see the tools.needs_review
        migration comment for the full reasoning."""
        self.conn.execute(
            "UPDATE tools SET needs_review=?, updated_at=? WHERE id=?",
            (1 if needs_review else 0, _now(), tool_id),
        )
        self.conn.commit()

    def mark_tool_reviewed(self, tool_id: int) -> None:
        """One-click whole-record "Mark reviewed" action — clears
        tools.needs_review without touching anything else. Mirrors
        mark_community_profile_reviewed(); a no-op (not an error) if the
        tool doesn't exist (matched by the UPDATE's WHERE clause finding no
        row, same as every other mark_*_verified method here)."""
        self.conn.execute(
            "UPDATE tools SET needs_review=0, updated_at=? WHERE id=?",
            (_now(), tool_id),
        )
        self.conn.commit()

    def count_tools_needing_review(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM tools WHERE needs_review=1"
        ).fetchone()[0]

    def count_tools_needing_attention(self) -> int:
        """Distinct-tool count for the admin badge (2026-09) — the single
        dedup-safe number behind the Software card's badge, combining
        EVERY condition that used to be summed as separate counts:
        pending approval (`approved=0`), whole-record `needs_review`, and
        any of the three per-field `*_needs_verification` flags
        (Description/Agent taxonomy/Competitive differentiation).

        This was built once (needs_review + the three field flags only,
        no approval condition), deliberately reverted in favor of a plain
        `count_pending_tools() + count_tools_needing_review()` sum per an
        earlier instruction to defer the field flags, then resurrected
        with the field flags folded back in (still no approval condition —
        that sum was `count_pending_tools() + count_tools_needing_
        attention()`, approval kept as a separate additive term). This
        revision folds `approved=0` in too, replacing that sum with a
        single query, because the sum was a REAL, LIVE double-count, not
        a latent one: `add_tool()` defaults `needs_review=1` for every
        brand-new tool regardless of caller (admin add-form, public
        `/tools/submit`, seed scripts — see that method's own docstring),
        and `approve_tool()` only ever flips `approved`, never touches
        `needs_review` or the three field flags — so EVERY tool sitting in
        the approval queue today already also has `needs_review=1`, and
        was being counted twice by `count_pending_tools() + count_tools_
        needing_attention()`. Confirmed by direct code read of `add_tool`/
        `approve_tool`, not assumed.

        Every condition here is a column on the same `tools` row (no join
        across a separate table), so `COUNT(*)` is already a per-tool
        count — no `DISTINCT` needed. `count_pending_tools()` and
        `count_tools_needing_review()` are both untouched and keep their
        own other callers (the admin list's "Needs review" filter count,
        the review-status pill's "(n/3)" breakdown, the pending-submissions
        table) — only the Software card's badge wiring in `webapp.tasks.
        open_task_counts()` reads this method now, as the sole count for
        that badge (no longer summed with anything else)."""
        return self.conn.execute(
            """SELECT COUNT(*) FROM tools
               WHERE approved=0
                  OR needs_review=1
                  OR description_needs_verification=1
                  OR agent_taxonomy_needs_verification=1
                  OR competitive_differentiation_needs_verification=1"""
        ).fetchone()[0]

    def update_tool_screenshot(self, tool_id: int, screenshot_url: str, screenshot_is_product: int) -> None:
        """Legacy narrow update, kept for pre-Phase-E callers/tests only —
        DO NOT call this from the admin edit-form submit path. Writes
        screenshot_is_product unconditionally, which is exactly the bug a
        2026-08 incident traced back to this method: the Phase E submit
        route called it with a hardcoded screenshot_is_product=0 on EVERY
        full-form save (any field, not just the screenshot ones), silently
        clearing the retired flag on rows the one-time migration script
        (scripts/archive/migrate_app_screenshot_from_product_flag.py) hadn't been
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

    def set_tool_logo(self, tool_id: int, logo_path: str, force: bool = False) -> bool:
        """Records a downloaded-and-stored logo asset — scripts/backfill_logos.py
        (automated Brandfetch fetch) is the only caller today. `logo_path` is a
        relative path under webapp/static/ (e.g. "logos/tools/abacum.svg"),
        never an external URL — see the logo_path ALTER TABLE comment in
        __init__ for the full reasoning.

        Manual-override fix (2026-08): refuses to overwrite a row with
        logo_manual_override=1 unless force=True — this is the one choke
        point every automated write goes through, so it protects a manual
        correction regardless of what selection query got the caller here
        (backfill_logos.py's own WHERE clause already skips these rows too,
        but that's a selection-query nicety, not the guarantee; this is).
        Returns True if the row was actually written, False if skipped
        because of an active override — callers that care (the backfill
        script's own reporting) can tell a no-op from a real write."""
        if not force:
            row = self.conn.execute(
                "SELECT logo_manual_override FROM tools WHERE id=?", (tool_id,)
            ).fetchone()
            if row and row["logo_manual_override"]:
                return False
        self.conn.execute(
            "UPDATE tools SET logo_path=?, updated_at=? WHERE id=?",
            (logo_path.strip(), _now(), tool_id),
        )
        self.conn.commit()
        return True

    def set_tool_logo_manual(self, tool_id: int, logo_path: str) -> None:
        """Admin-set logo override (manual URL fetch or upload) — the only
        writer that sets logo_manual_override=1. Also clears
        logo_override_stale, since setting/re-setting the override is itself
        the admin confirming it's correct for the tool's current URL."""
        self.conn.execute(
            "UPDATE tools SET logo_path=?, logo_manual_override=1, "
            "logo_override_stale=0, updated_at=? WHERE id=?",
            (logo_path.strip(), _now(), tool_id),
        )
        self.conn.commit()

    def clear_tool_logo_override(self, tool_id: int) -> None:
        """"Revert to automatic": drops the override flag and blanks
        logo_path, so the next scripts/backfill_logos.py run (its selection
        query targets empty logo_path) picks the tool back up and re-fetches
        from Brandfetch. Does not itself fetch anything — that stays a
        separate, deliberate script run, same as every other logo fetch."""
        self.conn.execute(
            "UPDATE tools SET logo_path='', logo_manual_override=0, "
            "logo_override_stale=0, updated_at=? WHERE id=?",
            (_now(), tool_id),
        )
        self.conn.commit()

    def dismiss_tool_logo_stale(self, tool_id: int) -> None:
        """Admin confirms an existing manual override is still correct after
        a URL change, without re-uploading anything — clears the stale flag
        only, leaves logo_path/logo_manual_override untouched."""
        self.conn.execute(
            "UPDATE tools SET logo_override_stale=0, updated_at=? WHERE id=?",
            (_now(), tool_id),
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

    def tool_competitor_counts(self) -> dict[int, int]:
        """{tool_id: curated-competitor count}, for every tool with at
        least one — used by the admin Software list's completeness filter
        so it doesn't call list_tool_competitors per row (same bulk-query
        precedent as community_profile_quality_flags: one grouped query
        for the whole page, not an N+1 per-row fetch). Counts both sides
        of the normalized (tool_id, competitor_id) pair against every tool
        that appears in either column."""
        out: dict[int, int] = {}
        rows = self.conn.execute(
            """SELECT id AS tool_id, COUNT(*) AS n FROM (
                   SELECT tool_id AS id FROM tool_competitors
                   UNION ALL
                   SELECT competitor_id AS id FROM tool_competitors
               ) GROUP BY id"""
        ).fetchall()
        for r in rows:
            out[r["tool_id"]] = r["n"]
        return out

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

    # -- Feature Taxonomy: category_features / tool_feature_links -----------
    # The legacy per-tool free-text Feature comparison data (Phase 4a) that
    # used to live here — add_tool_feature/list_tool_features/get_tool_feature/
    # update_tool_feature/delete_tool_feature, reading/writing the tool_features
    # table — was retired outright in the Feature Taxonomy Phase 1b PR 2 (see
    # CLAUDE.md's "no dead data" note): every route, admin section, and public
    # rendering path that touched it is gone, and the table itself is dropped
    # by the human-run scripts/drop_legacy_tool_features.py once this PR is
    # deployed and verified. list_tool_feature_links_with_details below is
    # the governed model's equivalent read path.
    # Governed replacement for tool_features above, built category by category
    # (docs/FEATURE_TAXONOMY.md is canon). See the category_features/
    # tool_feature_links CREATE TABLE comments for the model. Everything here
    # is the shared code path both a manual admin edit AND an approved
    # feature_review_queue entry go through (rules doc §9) — the queue's
    # approve_feature_review_queue_item below calls straight into these, never
    # writing to category_features/tool_feature_links directly.

    def list_category_features(self, category_id: int, include_retired: bool = False) -> list[dict]:
        sql = "SELECT * FROM category_features WHERE category_id=?"
        if not include_retired:
            sql += " AND retired_at=''"
        sql += " ORDER BY sort_order, name COLLATE NOCASE"
        return [dict(r) for r in self.conn.execute(sql, (category_id,)).fetchall()]

    def get_category_feature(self, feature_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM category_features WHERE id=?", (feature_id,)).fetchone()
        return dict(row) if row else None

    # Upper bound on category_features.definition/pointer_note, shared by the
    # admin inputs' live character counter and this server-side check so the
    # two can never disagree. This check is the only enforcement: the inputs
    # carry no HTML maxlength, since a browser silently cuts a paste to it. Derived, not arbitrary: the longest stored definition was 1,470
    # characters (production, 2026-09-23), and the old 500 cap sat below it.
    # 10,000 is ~7x that — room for real reference text, while still refusing an
    # accidental whole-document paste. A save over the limit is REFUSED with a
    # visible error naming both numbers; nothing here ever shortens a value.
    CATEGORY_FEATURE_TEXT_MAX = 10_000

    @staticmethod
    def text_budget_length(value: str | None) -> int:
        """Length as a character budget counts it: a browser submits a
        textarea's line breaks as CRLF, but shows (and counts) them as one
        character, so CRLF counts once here too. The live counter
        (webapp.app._char_budget) uses the same rule, so the number on
        screen and the number the server checks never disagree."""
        return len((value or "").replace("\r\n", "\n"))

    @classmethod
    def _check_category_feature_text(cls, definition: str, pointer_note: str) -> None:
        for label, value in (("Definition", definition), ("Pointer note", pointer_note)):
            n = cls.text_budget_length(value)
            if n > cls.CATEGORY_FEATURE_TEXT_MAX:
                raise ValueError(
                    f"{label} is {n:,} characters; the limit is {cls.CATEGORY_FEATURE_TEXT_MAX:,}. "
                    f"Nothing was saved. Shorten it and try again."
                )

    def add_category_feature(self, category_id: int, name: str, definition: str = "",
                              pointer_note: str = "", sort_order: int | None = None, source: str | None = None) -> int:
        name = name.strip()
        if not name:
            raise ValueError("Feature name is required.")
        self._check_category_feature_text(definition.strip(), pointer_note.strip())
        existing = self.conn.execute(
            "SELECT 1 FROM category_features WHERE category_id=? AND name=? COLLATE NOCASE AND retired_at=''",
            (category_id, name),
        ).fetchone()
        if existing:
            raise ValueError(f'"{name}" already exists in this category.')
        if sort_order is None:
            sort_order = self.conn.execute(
                "SELECT COALESCE(MAX(sort_order), 0) + 10 FROM category_features WHERE category_id=?",
                (category_id,),
            ).fetchone()[0]
        definition_before, pointer_note_before = definition.strip(), pointer_note.strip()
        definition_fixed, pointer_note_fixed = _voice_fix(definition_before), _voice_fix(pointer_note_before)
        cur = self.conn.execute(
            """INSERT INTO category_features (category_id, name, definition, pointer_note,
               sort_order, created_at) VALUES (?,?,?,?,?,?)""",
            (category_id, name, definition_fixed, pointer_note_fixed, sort_order, _now()),
        )
        self.conn.commit()
        new_id = cur.lastrowid
        # Logged AFTER insert, using the real row id — the one write path in
        # this file where `_vf` can't be used directly (no id exists until
        # the INSERT itself returns one).
        self.log_voice_correction("category_features", new_id, "definition", definition_before, definition_fixed, source=source, rule=_voice_fix_rule(definition_before))
        self.log_voice_correction("category_features", new_id, "pointer_note", pointer_note_before, pointer_note_fixed, source=source, rule=_voice_fix_rule(pointer_note_before))
        return new_id

    def update_category_feature(self, feature_id: int, name: str, definition: str,
                                 pointer_note: str, sort_order: int, source: str | None = None) -> None:
        name = name.strip()
        if not name:
            raise ValueError("Feature name is required.")
        row = self.conn.execute("SELECT category_id FROM category_features WHERE id=?", (feature_id,)).fetchone()
        if row is None:
            raise ValueError("Feature not found.")
        self._check_category_feature_text(definition.strip(), pointer_note.strip())
        dup = self.conn.execute(
            "SELECT 1 FROM category_features WHERE category_id=? AND name=? COLLATE NOCASE AND id!=? AND retired_at=''",
            (row[0], name, feature_id),
        ).fetchone()
        if dup:
            raise ValueError(f'"{name}" already exists in this category.')
        self.conn.execute(
            "UPDATE category_features SET name=?, definition=?, pointer_note=?, sort_order=? WHERE id=?",
            (name, self._vf("category_features", feature_id, "definition", definition.strip(), source=source),
             self._vf("category_features", feature_id, "pointer_note", pointer_note.strip(), source=source),
             sort_order, feature_id),
        )
        self.conn.commit()

    def retire_category_feature(self, feature_id: int) -> None:
        """Soft-retire only — rules doc: 'features are retired, never
        deleted.' Existing tool_feature_links rows survive untouched (a
        retired feature just stops rendering/being offered going forward);
        no cascade delete."""
        self.conn.execute(
            "UPDATE category_features SET retired_at=? WHERE id=? AND retired_at=''",
            (_now(), feature_id),
        )
        self.conn.commit()

    def list_tool_feature_links(self, tool_id: int) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM tool_feature_links WHERE tool_id=?", (tool_id,)
        ).fetchall()]

    def list_tool_feature_links_with_details(self, tool_id: int) -> list[dict]:
        """Phase 2 (public rendering) read path — joins tool_feature_links to
        category_features (name, sort_order) and tool_categories (category
        name), for every link whose feature is still live (soft-retired
        features drop out here, same as the admin checklist's own
        list_category_features(include_retired=False) default — a retired
        feature just stops rendering going forward, no cascade needed since
        the link row itself is untouched). Ordered by category name then the
        feature's own sort_order, so a multi-category tool's card groups
        predictably. Powers the public "Key features" card on
        /tools/software/{slug} (which, since the feature-definitions PR,
        also renders each feature's category-level definition/pointer_note
        and the link's publishable `public_note`; the internal `note` is
        never rendered), get_software's MCP payload, and the Software
        Matchmaker's context."""
        rows = self.conn.execute(
            """SELECT l.*, cf.name AS feature_name, cf.sort_order AS feature_sort_order,
                      cf.definition AS feature_definition, cf.pointer_note AS feature_pointer_note,
                      cf.category_id AS category_id, tc.name AS category_name
               FROM tool_feature_links l
               JOIN category_features cf ON cf.id = l.feature_id
               JOIN tool_categories tc ON tc.id = cf.category_id
               WHERE l.tool_id=? AND cf.retired_at=''
               ORDER BY tc.name COLLATE NOCASE, cf.sort_order, cf.name COLLATE NOCASE""",
            (tool_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_tool_feature_link(self, tool_id: int, feature_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM tool_feature_links WHERE tool_id=? AND feature_id=?", (tool_id, feature_id)
        ).fetchone()
        return dict(row) if row else None

    # Upper bound on tool_feature_links.public_note, shared by the admin
    # textarea's live counter and the server-side check below so the two can
    # never disagree (same pattern as CATEGORY_FEATURE_TEXT_MAX). Derived: the
    # longest internal note, the text this is usually lifted from, was 312
    # characters (production, 2026-09-23), and it renders under the category
    # definition in a sidebar card, so ~3x that is room enough. A save over
    # the limit is REFUSED with a visible error naming both numbers; nothing
    # here ever shortens a value. TARGET (character-budget-target PR) is the
    # softer, editorial ceiling the live counter turns amber past — roughly
    # half the hard cap, since the field is meant to be a short caption next
    # to a definition, not a second paragraph of text.
    FEATURE_LINK_PUBLIC_NOTE_MAX = 1_000
    FEATURE_LINK_PUBLIC_NOTE_TARGET = 500

    @classmethod
    def _check_feature_link_public_note(cls, public_note: str) -> None:
        # text_budget_length (not a bare len()), so this agrees with the live
        # counter's own CRLF-counts-once rule (webapp.app._char_budget) — a
        # plain len() would count a pasted CRLF line break twice, disagreeing
        # with what the on-screen counter shows for the exact same text.
        n = cls.text_budget_length(public_note)
        if n > cls.FEATURE_LINK_PUBLIC_NOTE_MAX:
            raise ValueError(
                f"Public text is {n:,} characters; the limit is {cls.FEATURE_LINK_PUBLIC_NOTE_MAX:,}. "
                f"Nothing was saved. Shorten it and try again."
            )

    def upsert_tool_feature_link(self, tool_id: int, feature_id: int, availability: str,
                                  ai_enabled: int, verified_as_of: str, note: str = "",
                                  source_url: str = "", public_note: str | None = None,
                                  source: str | None = None) -> int:
        """Insert or update the one link row for this (tool, feature) pair —
        the admin checklist toggles a feature on by calling this, and re-calls
        it on every designation edit. availability must be 'native'|'add_on'
        (the CHECK constraint backs this up at the DB layer too).

        public_note=None (the default) leaves any stored public_note alone,
        so review-queue approval and the seed script, which never carry it,
        can't wipe text Brian curated. Pass a string (including "") to set
        it. Over FEATURE_LINK_PUBLIC_NOTE_MAX raises ValueError and writes
        nothing."""
        if availability not in ("native", "add_on"):
            raise ValueError('availability must be "native" or "add_on".')
        if public_note is not None:
            public_note = public_note.strip()
            self._check_feature_link_public_note(public_note)
        now = _now()
        existing = self.get_tool_feature_link(tool_id, feature_id)
        if existing:
            if public_note is None:
                public_note = existing.get("public_note") or ""
            else:
                public_note = self._vf("tool_feature_links", existing["id"], "public_note",
                                       public_note, source=source)
            self.conn.execute(
                """UPDATE tool_feature_links SET availability=?, ai_enabled=?, verified_as_of=?,
                   note=?, source_url=?, public_note=?, updated_at=? WHERE id=?""",
                (availability, int(ai_enabled), verified_as_of.strip(), note.strip(),
                 source_url.strip(), public_note, now, existing["id"]),
            )
            self.conn.commit()
            return existing["id"]
        raw_public = public_note or ""
        fixed_public = _voice_fix(raw_public) if raw_public else raw_public
        cur = self.conn.execute(
            """INSERT INTO tool_feature_links (tool_id, feature_id, availability, ai_enabled,
               verified_as_of, note, source_url, public_note, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (tool_id, feature_id, availability, int(ai_enabled), verified_as_of.strip(),
             note.strip(), source_url.strip(), fixed_public, now, now),
        )
        self.conn.commit()
        if fixed_public != raw_public:
            self.log_voice_correction("tool_feature_links", cur.lastrowid, "public_note",
                                      raw_public, fixed_public, source=source,
                                      rule=_voice_fix_rule(raw_public))
        return cur.lastrowid

    def delete_tool_feature_link(self, tool_id: int, feature_id: int) -> None:
        """Un-toggling a feature in the admin checklist — removes the link
        row entirely (not a soft delete; the designations themselves aren't
        historical record the way a retired feature's past existence is)."""
        self.conn.execute(
            "DELETE FROM tool_feature_links WHERE tool_id=? AND feature_id=?", (tool_id, feature_id)
        )
        self.conn.commit()

    def category_has_features(self, category_id: int) -> bool:
        """Filters which categories get a checklist section on the admin
        edit page's "Manage Tool Features" — a tool's category with nothing
        curated yet shouldn't render an empty section. (Originally also
        drove the public profile page's legacy-vs-governed branch, before
        the legacy tool_features Features card was retired outright in the
        Feature Taxonomy Phase 1b PR 2 — the public page now always renders
        the governed "Key features" card regardless of this flag.)"""
        row = self.conn.execute(
            "SELECT 1 FROM category_features WHERE category_id=? AND retired_at='' LIMIT 1",
            (category_id,),
        ).fetchone()
        return row is not None

    # -- Feature Taxonomy review queue (rules doc §9) ------------------------
    # No proposed change reaches category_features/tool_feature_links without
    # landing here first and being approved by a human — an admin's own edit
    # may fast-path through add_feature_review_queue_item + an immediate
    # approve, but it's still logged through the queue, never a bypass.

    def add_feature_review_queue_item(self, source: str, proposal_type: str, payload: dict,
                                       category_id: int | None = None, tool_id: int | None = None,
                                       articulation: str = "", submitter_name: str = "",
                                       submitter_email: str = "") -> int:
        if source not in ("admin", "scan", "public"):
            raise ValueError('source must be "admin", "scan", or "public".')
        cur = self.conn.execute(
            """INSERT INTO feature_review_queue (source, status, category_id, tool_id,
               proposal_type, payload, articulation, submitter_name, submitter_email,
               created_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (source, "pending", category_id, tool_id, proposal_type.strip(),
             json.dumps(payload), articulation.strip(), submitter_name.strip(),
             submitter_email.strip(), _now()),
        )
        self.conn.commit()
        return cur.lastrowid

    def update_feature_review_queue_payload(self, item_id: int, payload: dict,
                                             proposal_type: str | None = None,
                                             articulation: str | None = None) -> None:
        """Rewrites a PENDING item's payload/proposal_type/articulation in
        place, keeping it pending — distinct from approve/deny, which both
        resolve the item. Built for scripts/remap_queue_to_framework.py's
        consolidation step: several source='scan' proposals that map to the
        same human-defined framework bucket collapse into ONE updated queue
        row (canonical name/definition, unioned tool links) rather than a
        fresh insert, so the queue's created_at/id history for that row
        still traces back to its original scan proposal. Raises if the item
        isn't pending — a resolved item's payload is a historical record,
        not something a later script should silently rewrite."""
        item = self.get_feature_review_queue_item(item_id)
        if item is None:
            raise ValueError("Review queue item not found.")
        if item["status"] != "pending":
            raise ValueError(f'This item is already {item["status"]}, not pending — refusing to '
                              f'rewrite a resolved item\'s payload.')
        proposal_type = item["proposal_type"] if proposal_type is None else proposal_type.strip()
        articulation = item["articulation"] if articulation is None else articulation.strip()
        self.conn.execute(
            "UPDATE feature_review_queue SET payload=?, proposal_type=?, articulation=? WHERE id=?",
            (json.dumps(payload), proposal_type, articulation, item_id),
        )
        self.conn.commit()

    def count_feature_review_queue(self, status: str | None = "pending") -> int:
        """Cheap indexed COUNT (idx_feature_review_queue_status) — powers the
        /admin hub's pending badge, mirroring list_feature_review_queue's own
        status handling."""
        if status:
            row = self.conn.execute(
                "SELECT COUNT(*) FROM feature_review_queue WHERE status=?", (status,)
            ).fetchone()
        else:
            row = self.conn.execute("SELECT COUNT(*) FROM feature_review_queue").fetchone()
        return row[0]

    def find_category_feature_by_name(self, category_id: int, name: str,
                                       include_retired: bool = True) -> dict | None:
        """Case-insensitive exact-name lookup within one category — the same
        merge check approve_feature_review_queue_item uses internally, pulled
        out so the review-queue approve route can pre-check for a name
        collision and show a confirmation step before merging (Phase 1c),
        rather than merging silently."""
        name = name.strip().lower()
        return next(
            (f for f in self.list_category_features(category_id, include_retired=include_retired)
             if f["name"].strip().lower() == name),
            None,
        )

    def list_tools_linked_to_feature(self, feature_id: int) -> list[dict]:
        """Tool names/ids currently linked to a feature — used to show "this
        feature already exists and is linked to [vendor list]" on the
        review-queue merge confirmation step."""
        rows = self.conn.execute(
            """SELECT t.id, t.name FROM tool_feature_links l
               JOIN tools t ON t.id = l.tool_id
               WHERE l.feature_id=? ORDER BY t.name COLLATE NOCASE""",
            (feature_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def list_feature_review_queue(self, status: str | None = "pending") -> list[dict]:
        if status:
            rows = self.conn.execute(
                "SELECT * FROM feature_review_queue WHERE status=? ORDER BY created_at", (status,)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM feature_review_queue ORDER BY created_at DESC"
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["payload"] = json.loads(d["payload"] or "{}")
            result.append(d)
        return result

    def get_feature_review_queue_item(self, item_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM feature_review_queue WHERE id=?", (item_id,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["payload"] = json.loads(d["payload"] or "{}")
        return d

    def approve_feature_review_queue_item(self, item_id: int, override_payload: dict | None = None,
                                           resolution_note: str = "") -> dict:
        """Applies a pending queue entry's payload through the exact same
        methods a manual admin edit would call — never a direct table write.
        Payload shape: {"category_id": int, "feature_id": int (existing) OR
        "feature": {"name","definition","pointer_note"} (new),
        "links": [{"tool_id","availability","ai_enabled","verified_as_of",
        "note","source_url"}, ...]}. override_payload lets the admin edit the
        proposal before approving (rules doc: "edit-then-approve") — when
        given, status lands as 'edited' rather than 'approved' so the queue
        history shows a proposal wasn't taken verbatim. Returns the resolved
        feature_id/link ids so the caller can build a confirmation message."""
        item = self.get_feature_review_queue_item(item_id)
        if item is None:
            raise ValueError("Review queue item not found.")
        if item["status"] != "pending":
            raise ValueError(f'This item is already {item["status"]}, not pending.')
        payload = override_payload if override_payload is not None else item["payload"]
        category_id = payload.get("category_id") or item["category_id"]
        if not category_id:
            raise ValueError("payload is missing category_id.")

        feature_id = payload.get("feature_id")
        if not feature_id:
            draft = payload.get("feature") or {}
            feature_name = (draft.get("name") or "").strip()
            if not feature_name:
                raise ValueError("payload has neither an existing feature_id nor a new feature name.")
            existing = self.find_category_feature_by_name(category_id, feature_name)
            if existing:
                feature_id = existing["id"]
            else:
                feature_id = self.add_category_feature(
                    category_id, feature_name, draft.get("definition", ""), draft.get("pointer_note", ""),
                )

        link_ids = []
        for link in payload.get("links", []):
            link_ids.append(self.upsert_tool_feature_link(
                link["tool_id"], feature_id, link.get("availability", "native"),
                int(link.get("ai_enabled", 0)), link.get("verified_as_of", ""),
                link.get("note", ""), link.get("source_url", ""),
            ))

        status = "edited" if override_payload is not None else "approved"
        self.conn.execute(
            "UPDATE feature_review_queue SET status=?, resolved_at=?, resolution_note=? WHERE id=?",
            (status, _now(), resolution_note.strip(), item_id),
        )
        self.conn.commit()
        return {"feature_id": feature_id, "link_ids": link_ids}

    def deny_feature_review_queue_item(self, item_id: int, resolution_note: str = "") -> None:
        item = self.get_feature_review_queue_item(item_id)
        if item is None:
            raise ValueError("Review queue item not found.")
        if item["status"] != "pending":
            raise ValueError(f'This item is already {item["status"]}, not pending.')
        self.conn.execute(
            "UPDATE feature_review_queue SET status='denied', resolved_at=?, resolution_note=? WHERE id=?",
            (_now(), resolution_note.strip(), item_id),
        )
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

    def get_tool_category_id(self, name: str) -> int | None:
        """Exact (case-insensitive) name -> id lookup. Used by the Feature
        Taxonomy seed script to resolve category_features.category_id
        against a real tool_categories row — never guessed, never fuzzy."""
        row = self.conn.execute(
            "SELECT id FROM tool_categories WHERE name = ? COLLATE NOCASE", (name.strip(),)
        ).fetchone()
        return row[0] if row else None

    def add_category_to_tool(self, tool_id: int, category_name: str) -> bool:
        """Additive-only: adds category_name to a tool's existing tags if not
        already present, never removes any. Built for the Feature Taxonomy
        seed script's Close Management tagging (Brian's explicit call: FloQast/
        Numeric/Ledge keep every existing tag, Close Management is added
        alongside them, not swapped in). Returns True if the tag was actually
        added, False if the tool already had it (so the seed script's printed
        report can distinguish "already tagged" from "just tagged")."""
        row = self.conn.execute("SELECT categories_json FROM tools WHERE id=?", (tool_id,)).fetchone()
        if row is None:
            raise ValueError(f"No tool with id={tool_id}.")
        cats = json.loads(row[0] or "[]")
        if category_name in cats:
            return False
        cats.append(category_name)
        self.conn.execute(
            "UPDATE tools SET categories_json=?, updated_at=? WHERE id=?",
            (json.dumps(sorted(cats)), _now(), tool_id),
        )
        self.conn.commit()
        return True

    def list_tool_categories(self) -> list[dict]:
        # Always alphabetical by name, not sort_order (insertion order)—so the
        # filter pills on /tools/software and the rows on /admin/tools/software/categories
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

    def add_tool_category(self, name: str, description: str = "", source: str | None = None) -> int:
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
        description_fixed = _voice_fix(description)
        cur = self.conn.execute(
            "INSERT INTO tool_categories (name, description, sort_order) VALUES (?,?,?)",
            (name, description_fixed, next_order),
        )
        self.conn.commit()
        new_id = cur.lastrowid
        # Logged AFTER insert, using the real row id — no id exists until
        # the INSERT itself returns one (same reason add_original_content/
        # add_category_feature do the same).
        self.log_voice_correction("tool_categories", new_id, "description", description, description_fixed, source=source, rule=_voice_fix_rule(description))
        return new_id

    def rename_tool_category(self, category_id: int, new_name: str, description: str = "", source: str | None = None) -> int:
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
            (new_name, self._vf("tool_categories", category_id, "description", description, source=source), category_id),
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

    # -- benchmarking resources (the /tools "Resources" section; table/methods --
    # keep their original "benchmark" naming, only the URLs and page copy renamed) --

    def list_benchmarks(self, section: str | None = None) -> list[dict]:
        """All rows, or just one section ('benchmarking' | 'books') when
        `section` is given — the public/admin Resources pages both render
        the two sections separately."""
        if section is not None:
            rows = self.conn.execute(
                "SELECT * FROM benchmarks WHERE section=? ORDER BY sort_order, name", (section,)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM benchmarks ORDER BY section, sort_order, name"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_benchmark(self, benchmark_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM benchmarks WHERE id = ?", (benchmark_id,)).fetchone()
        return dict(row) if row else None

    def add_benchmark(self, name: str, url: str, description: str,
                      coverage: str = "Private", pricing: str = "free",
                      section: str = "benchmarking", source: str | None = None) -> int:
        next_order = self.conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM benchmarks WHERE section=?", (section,)
        ).fetchone()[0]
        description_before = description.strip()
        description_fixed = _voice_fix(description_before)
        cur = self.conn.execute(
            "INSERT INTO benchmarks (name, url, description, coverage, pricing, sort_order, section) VALUES (?,?,?,?,?,?,?)",
            (name.strip(), url.strip(), description_fixed, coverage, pricing, next_order, section),
        )
        self.conn.commit()
        new_id = cur.lastrowid
        # Logged AFTER insert, using the real row id — no id exists until
        # the INSERT itself returns one (same reason add_original_content/
        # add_category_feature do the same).
        self.log_voice_correction("benchmarks", new_id, "description", description_before, description_fixed, source=source, rule=_voice_fix_rule(description_before))
        return new_id

    def update_benchmark(self, benchmark_id: int, name: str, url: str, description: str,
                         coverage: str, pricing: str, section: str = "benchmarking", source: str | None = None) -> None:
        self.conn.execute(
            "UPDATE benchmarks SET name=?, url=?, description=?, coverage=?, pricing=?, section=? WHERE id=?",
            (name.strip(), url.strip(), self._vf("benchmarks", benchmark_id, "description", description.strip(), source=source),
             coverage, pricing, section, benchmark_id),
        )
        self.conn.commit()

    def update_benchmark_content(self, benchmark_id: int, name: str, description: str, source: str | None = None) -> None:
        """Narrow update for the startup seed-sync pass: touches only name and
        description, leaving coverage/pricing untouched so an admin edit made
        directly on /admin/tools/resources survives a re-sync."""
        self.conn.execute(
            "UPDATE benchmarks SET name=?, description=? WHERE id=?",
            (name.strip(), self._vf("benchmarks", benchmark_id, "description", description.strip(), source=source), benchmark_id),
        )
        self.conn.commit()

    def delete_benchmark(self, benchmark_id: int) -> None:
        self.conn.execute("DELETE FROM benchmarks WHERE id = ?", (benchmark_id,))
        self.conn.commit()

    # -- thought leadership (the /thought-leadership page's four columns) ---
    # Ordering mirrors the pre-DB behavior in webapp/thought_leadership_data.py:
    # undated items (sort_key == '') float to the top, everything else sorts
    # newest-first by sort_key; display_order breaks ties within each group so
    # migrated/added items don't reshuffle on every read.
    _TL_ORDER_SQL = "(CASE WHEN sort_key = '' THEN 0 ELSE 1 END), sort_key DESC, display_order ASC"

    def list_thought_leadership(self, type: str | None = None) -> list[dict]:
        if type:
            rows = self.conn.execute(
                f"SELECT * FROM thought_leadership WHERE type = ? ORDER BY {self._TL_ORDER_SQL}",
                (type,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                f"SELECT * FROM thought_leadership ORDER BY type, {self._TL_ORDER_SQL}"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_thought_leadership(self, item_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM thought_leadership WHERE id = ?", (item_id,)).fetchone()
        return dict(row) if row else None

    # Ordering for the homepage's curated "Recent highlights" set —
    # deliberately NOT _TL_ORDER_SQL above. _TL_ORDER_SQL's first clause
    # floats an undated entry (sort_key == '') to the top, which is the
    # right call for a chronological feed (an undated standing link, like
    # a full episode feed, belongs at the top of its section) but wrong for
    # a hand-curated set of four: it means an admin picks four pieces and an
    # undated one silently jumps to slot one with no lever to move it — the
    # exact defect (id 34's checkbox going inert under a newer dated entry)
    # that motivated replacing get_thought_leadership_representative with
    # this featured-set query in the first place. Newest first, undated
    # last, display_order as tiebreaker.
    _TL_FEATURED_ORDER_SQL = "sort_key DESC, display_order ASC"

    def count_featured_home(self, exclude_id: int | None = None) -> int:
        """How many thought_leadership rows currently have featured_home=1.
        `exclude_id` (load-bearing for an edit-form save) leaves one row's
        own current state out of the count, so re-saving an already-featured
        row while all 4 slots are full doesn't trip the cap against itself."""
        if exclude_id is not None:
            row = self.conn.execute(
                "SELECT COUNT(*) FROM thought_leadership WHERE featured_home = 1 AND id != ?",
                (exclude_id,),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT COUNT(*) FROM thought_leadership WHERE featured_home = 1"
            ).fetchone()
        return row[0]

    def list_thought_leadership_featured_home(self) -> list[dict]:
        """The homepage's "Recent highlights" set: up to 4 curated pieces,
        any mix of types, in _TL_FEATURED_ORDER_SQL order. `LIMIT 4` here —
        not an assert — is what makes a theoretical 5th featured row (a
        direct DB write, a race between two admin tabs; the cap is enforced
        at the write routes, not here) harmless: the public homepage still
        renders exactly 4 and nothing breaks, rather than taking the page
        down over an admin data condition. See the write routes' own
        comments for why no lock guards the check-then-act race — it isn't
        worth one for a single-admin tool, and this LIMIT is the actual
        backstop."""
        rows = self.conn.execute(
            f"SELECT * FROM thought_leadership WHERE featured_home = 1 "
            f"ORDER BY {self._TL_FEATURED_ORDER_SQL} LIMIT 4"
        ).fetchall()
        return [dict(r) for r in rows]

    def add_thought_leadership(self, type: str, title: str, url: str = "", venue: str = "",
                               date_label: str = "", sort_key: str = "", description: str = "",
                               needs_synopsis: bool = False, display_order: int | None = None,
                               featured_home: bool = False, source: str | None = None) -> int:
        if display_order is None:
            display_order = self.conn.execute(
                "SELECT COALESCE(MAX(display_order), -1) + 1 FROM thought_leadership WHERE type = ?",
                (type,),
            ).fetchone()[0]
        now = _now()
        title_before, venue_before, description_before = title.strip(), venue.strip(), description.strip()
        title_fixed, venue_fixed, description_fixed = (
            _voice_fix(title_before), _voice_fix(venue_before), _voice_fix(description_before),
        )
        cur = self.conn.execute(
            "INSERT INTO thought_leadership "
            "(type, title, url, venue, date_label, sort_key, description, needs_synopsis, display_order, "
            "featured_home, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (type, title_fixed, url.strip(), venue_fixed, date_label.strip(),
             sort_key.strip(), description_fixed, int(bool(needs_synopsis)), display_order,
             int(bool(featured_home)), now, now),
        )
        self.conn.commit()
        new_id = cur.lastrowid
        # Logged AFTER insert, using the real row id — no id exists until
        # the INSERT itself returns one (same reason add_original_content/
        # add_category_feature do the same).
        self.log_voice_correction("thought_leadership", new_id, "title", title_before, title_fixed, source=source, rule=_voice_fix_rule(title_before))
        self.log_voice_correction("thought_leadership", new_id, "venue", venue_before, venue_fixed, source=source, rule=_voice_fix_rule(venue_before))
        self.log_voice_correction("thought_leadership", new_id, "description", description_before, description_fixed, source=source, rule=_voice_fix_rule(description_before))
        return new_id

    def update_thought_leadership(self, item_id: int, type: str, title: str, url: str, venue: str,
                                  date_label: str, sort_key: str, description: str,
                                  needs_synopsis: bool, display_order: int,
                                  featured_home: bool = False, source: str | None = None) -> None:
        self.conn.execute(
            "UPDATE thought_leadership SET type=?, title=?, url=?, venue=?, date_label=?, sort_key=?, "
            "description=?, needs_synopsis=?, display_order=?, featured_home=?, updated_at=? WHERE id=?",
            (type, self._vf("thought_leadership", item_id, "title", title.strip(), source=source), url.strip(),
             self._vf("thought_leadership", item_id, "venue", venue.strip(), source=source), date_label.strip(),
             sort_key.strip(), self._vf("thought_leadership", item_id, "description", description.strip(), source=source),
             int(bool(needs_synopsis)), display_order, int(bool(featured_home)), _now(), item_id),
        )
        self.conn.commit()

    def delete_thought_leadership(self, item_id: int) -> None:
        self.conn.execute("DELETE FROM thought_leadership WHERE id = ?", (item_id,))
        self.conn.commit()

    # -- original content (flagship-piece cards + admin-authored articles) --
    # Ordering is display_order first (a curated card order — the admin picks
    # what leads), sort_key only as a tiebreak for entries that share a
    # display_order — the opposite priority from _TL_ORDER_SQL above, since
    # this list is a handful of hand-curated flagship pieces, not a
    # chronological feed.
    _OC_ORDER_SQL = "display_order ASC, sort_key DESC"

    def list_original_content(self, status: str | None = None) -> list[dict]:
        if status:
            rows = self.conn.execute(
                f"SELECT * FROM original_content WHERE status = ? ORDER BY {self._OC_ORDER_SQL}",
                (status,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                f"SELECT * FROM original_content ORDER BY {self._OC_ORDER_SQL}"
            ).fetchall()
        return [dict(r) for r in rows]

    def list_original_content_for_home(self) -> list[dict]:
        """The homepage's flagship row: live pieces flagged featured_home=1
        only. (/thought-leadership itself shows every live piece regardless
        of this flag — see list_original_content(status='live').)"""
        rows = self.conn.execute(
            f"SELECT * FROM original_content WHERE status = 'live' AND featured_home = 1 "
            f"ORDER BY {self._OC_ORDER_SQL}"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_original_content(self, item_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM original_content WHERE id = ?", (item_id,)).fetchone()
        return dict(row) if row else None

    def get_original_content_by_slug(self, slug: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM original_content WHERE slug = ?", (slug,)).fetchone()
        return dict(row) if row else None

    def add_original_content(self, slug: str, title: str, teaser: str = "", tag_label: str = "",
                             link_label: str = "", body_md: str | None = None, status: str = "draft",
                             featured_home: bool = False, date_label: str = "", sort_key: str = "",
                             display_order: int | None = None, source: str | None = None) -> int:
        if display_order is None:
            display_order = self.conn.execute(
                "SELECT COALESCE(MAX(display_order), -1) + 1 FROM original_content"
            ).fetchone()[0]
        now = _now()
        title_before, teaser_before = title.strip(), teaser.strip()
        title_fixed, teaser_fixed = _voice_fix(title_before), _voice_fix(teaser_before)
        body_fixed = _voice_fix(body_md) if body_md else body_md
        cur = self.conn.execute(
            "INSERT INTO original_content "
            "(slug, title, teaser, tag_label, link_label, body_md, status, featured_home, "
            "date_label, sort_key, display_order, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (slug.strip(), title_fixed, teaser_fixed, tag_label.strip(),
             link_label.strip(), body_fixed, status,
             int(bool(featured_home)), date_label.strip(), sort_key.strip(), display_order, now, now),
        )
        self.conn.commit()
        new_id = cur.lastrowid
        # Logged AFTER insert, using the real row id — same reason
        # add_category_feature does the same: no id exists until the INSERT
        # itself returns one, so _vf's inline "value already known, wrap it"
        # shape can't be used here.
        self.log_voice_correction("original_content", new_id, "title", title_before, title_fixed, source=source, rule=_voice_fix_rule(title_before))
        self.log_voice_correction("original_content", new_id, "teaser", teaser_before, teaser_fixed, source=source, rule=_voice_fix_rule(teaser_before))
        if body_md:
            self.log_voice_correction("original_content", new_id, "body_md", body_md, body_fixed, source=source, rule=_voice_fix_rule(body_md))
        return new_id

    def update_original_content(self, item_id: int, slug: str, title: str, teaser: str, tag_label: str,
                                link_label: str, body_md: str | None, status: str, featured_home: bool,
                                date_label: str, sort_key: str, display_order: int, source: str | None = None) -> None:
        self.conn.execute(
            "UPDATE original_content SET slug=?, title=?, teaser=?, tag_label=?, link_label=?, "
            "body_md=?, status=?, featured_home=?, date_label=?, sort_key=?, display_order=?, "
            "updated_at=? WHERE id=?",
            (slug.strip(), self._vf("original_content", item_id, "title", title.strip(), source=source),
             self._vf("original_content", item_id, "teaser", teaser.strip(), source=source), tag_label.strip(),
             link_label.strip(),
             self._vf("original_content", item_id, "body_md", body_md, source=source) if body_md else body_md, status,
             int(bool(featured_home)), date_label.strip(), sort_key.strip(), display_order, _now(), item_id),
        )
        self.conn.commit()

    def delete_original_content(self, item_id: int) -> None:
        self.conn.execute("DELETE FROM original_content WHERE id = ?", (item_id,))
        self.conn.commit()

    # -- AI surfaces (the /how-this-is-built cards) --
    # Ordering is display_order first, same "curated card order" convention
    # as original_content above — there's no sort_key/date to tiebreak on
    # here, so ties just fall back to id.
    _AI_SURFACE_ORDER_SQL = "display_order ASC, id ASC"

    def list_ai_surfaces(self, status: str | None = None) -> list[dict]:
        if status:
            rows = self.conn.execute(
                f"SELECT * FROM ai_surfaces WHERE status = ? ORDER BY {self._AI_SURFACE_ORDER_SQL}",
                (status,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                f"SELECT * FROM ai_surfaces ORDER BY {self._AI_SURFACE_ORDER_SQL}"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_ai_surface(self, item_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM ai_surfaces WHERE id = ?", (item_id,)).fetchone()
        return dict(row) if row else None

    def get_ai_surface_by_slug(self, slug: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM ai_surfaces WHERE slug = ?", (slug,)).fetchone()
        return dict(row) if row else None

    def add_ai_surface(self, slug: str, title: str, teaser: str = "", body_md: str | None = None,
                        external_href: str = "", status: str = "draft",
                        display_order: int | None = None, source: str | None = None) -> int:
        if display_order is None:
            display_order = self.conn.execute(
                "SELECT COALESCE(MAX(display_order), -1) + 1 FROM ai_surfaces"
            ).fetchone()[0]
        now = _now()
        title_before, teaser_before = title.strip(), teaser.strip()
        title_fixed, teaser_fixed = _voice_fix(title_before), _voice_fix(teaser_before)
        body_fixed = _voice_fix(body_md) if body_md else body_md
        cur = self.conn.execute(
            "INSERT INTO ai_surfaces (slug, title, teaser, body_md, external_href, status, "
            "display_order, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (slug.strip(), title_fixed, teaser_fixed, body_fixed, external_href.strip(),
             status, display_order, now, now),
        )
        self.conn.commit()
        new_id = cur.lastrowid
        # Logged AFTER insert, using the real row id — same reason
        # add_original_content/add_category_feature do the same: no id
        # exists until the INSERT itself returns one.
        self.log_voice_correction("ai_surfaces", new_id, "title", title_before, title_fixed, source=source, rule=_voice_fix_rule(title_before))
        self.log_voice_correction("ai_surfaces", new_id, "teaser", teaser_before, teaser_fixed, source=source, rule=_voice_fix_rule(teaser_before))
        if body_md:
            self.log_voice_correction("ai_surfaces", new_id, "body_md", body_md, body_fixed, source=source, rule=_voice_fix_rule(body_md))
        return new_id

    def update_ai_surface(self, item_id: int, slug: str, title: str, teaser: str,
                           body_md: str | None, external_href: str, status: str,
                           display_order: int, source: str | None = None) -> None:
        self.conn.execute(
            "UPDATE ai_surfaces SET slug=?, title=?, teaser=?, body_md=?, external_href=?, "
            "status=?, display_order=?, updated_at=? WHERE id=?",
            (slug.strip(), self._vf("ai_surfaces", item_id, "title", title.strip(), source=source),
             self._vf("ai_surfaces", item_id, "teaser", teaser.strip(), source=source),
             self._vf("ai_surfaces", item_id, "body_md", body_md, source=source) if body_md else body_md,
             external_href.strip(), status, display_order, _now(), item_id),
        )
        self.conn.commit()

    def delete_ai_surface(self, item_id: int) -> None:
        self.conn.execute("DELETE FROM ai_surfaces WHERE id = ?", (item_id,))
        self.conn.commit()

    def set_original_content_mirrored_article_id(self, item_id: int, article_id: int | None) -> None:
        """Narrow single-column setter tracking which articles.id (if any)
        currently mirrors this piece — see linklib/original_content_sync.py.
        Deliberately not folded into add/update_original_content's own
        signature: those two are driven by the admin form, which knows
        nothing about the mirrored article's id."""
        self.conn.execute(
            "UPDATE original_content SET mirrored_article_id=? WHERE id=?",
            (article_id, item_id),
        )
        self.conn.commit()

    def list_unmirrored_original_content(self) -> list[dict]:
        """Every original_content row that SHOULD have a working articles
        mirror (per linklib.original_content_sync.sync_original_content_
        article's own rule: any row with non-empty body_md, regardless of
        status) but doesn't — mirrored_article_id is NULL, or it points at
        an articles row that no longer exists.

        Backs the "Original content mirrored for retrieval" /admin/checks
        invariant (2026-09). Root cause this exists for: the sync only ever
        fires from the two admin save routes (POST /admin/thought-leadership/
        original/new and .../{id}/edit) — a write via any other path (the
        three scripts/migrate_*_content.py one-time migrations included) can
        silently leave a row unmirrored, with nothing surfacing it until
        FP&A Buddy quietly fails to retrieve content that actually exists.
        Two rows sat exactly like this for three weeks before anyone
        noticed. This check exists so that gap is visible on the very next
        /admin/checks load instead of found by chance — the fix for a
        flagged row is always the same and needs no code: open it in
        /admin/thought-leadership/original and click Save, which re-runs
        the sync unconditionally."""
        rows = self.conn.execute(
            "SELECT oc.id, oc.slug, oc.title, oc.mirrored_article_id "
            "FROM original_content oc LEFT JOIN articles a ON a.id = oc.mirrored_article_id "
            "WHERE TRIM(COALESCE(oc.body_md, '')) != '' "
            "AND (oc.mirrored_article_id IS NULL OR a.id IS NULL) "
            "ORDER BY oc.id"
        ).fetchall()
        return [dict(r) for r in rows]

    def list_drifted_original_content_mirrors(self) -> list[dict]:
        """Every original_content row WITH a working mirror
        (mirrored_article_id points at a real articles row) whose mirrored
        content is stale relative to the current body_md — i.e. the mirror
        exists but sync_original_content_article() hasn't actually run
        since the last edit. Complements list_unmirrored_original_content()
        above, which only catches "no mirror at all"; this catches "mirror
        exists but drifted," the gap a future non-route write (a script
        that calls update_original_content() directly, bypassing the
        admin-save-route sync trigger) can still produce.

        This is a SAFETY NET, not the primary fix for any write path this
        codebase already knows about — the two /admin/thought-leadership/
        original save routes and, as of the voice-queue-durability fix
        (2026-09), every voice review queue action that can write
        original_content.<column> (Edit, Revert, and the bulk "Replace
        ampersands with and" apply route — see webapp.app._resolve_voice_
        item_action and admin_voice_review_bulk_replace_ampersand_apply)
        all fire sync_original_content_article() synchronously, at the
        point of write, so none of THOSE paths should ever actually
        produce a row this method flags. What's left for this method to
        catch is a genuinely future write path — a new script, a new admin
        route — that forgets to call the sync, not the queue's own already-
        wired actions.

        Deliberately compares CONTENT, not updated_at timestamps: scripts/
        normalize_original_content_tags.py calls update_original_content()
        passing body_md unchanged (only tag_label/link_label actually
        change), but Library.update_original_content() still bumps
        updated_at unconditionally on every call — a timestamp comparison
        would false-positive on that exact case. Re-deriving the expected
        plain-text via plain_text_from_body_md() (the same function
        sync_original_content_article() itself uses) and comparing it
        directly against the stored articles.content is immune to that:
        no drift is reported unless the actual indexed text differs."""
        from .original_content_sync import plain_text_from_body_md

        rows = self.conn.execute(
            "SELECT oc.id, oc.slug, oc.title, oc.mirrored_article_id, "
            "oc.body_md, a.content AS mirrored_content "
            "FROM original_content oc JOIN articles a ON a.id = oc.mirrored_article_id "
            "WHERE TRIM(COALESCE(oc.body_md, '')) != '' "
            "ORDER BY oc.id"
        ).fetchall()
        drifted = []
        for r in rows:
            expected = plain_text_from_body_md(r["body_md"])
            if expected != (r["mirrored_content"] or ""):
                drifted.append({
                    "id": r["id"],
                    "slug": r["slug"],
                    "title": r["title"],
                    "mirrored_article_id": r["mirrored_article_id"],
                })
        return drifted

    def insert_mirrored_article(self, url: str, title: str, content: str) -> int:
        """Create the articles row backing a mirrored original_content piece.
        Deliberately NOT Library.upsert() — upsert's merge-into-existing-row
        path keeps whatever the existing row already has (existing["content"]
        or art.content), which is correct for an external re-fetch but wrong
        here: a brand-new mirror has nothing to merge with, so a plain INSERT
        is simplest and clearest about that. is_own_content is set separately
        by the caller (set_article_own_content), same as every other flag on
        a freshly-inserted article."""
        url = normalize_url(url)
        now = _now()
        cur = self.conn.execute(
            "INSERT INTO articles (url, title, content, saved_at, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?)",
            (url, title, content, now, now, now),
        )
        self.conn.commit()
        return cur.lastrowid

    def update_mirrored_article(self, article_id: int, title: str, url: str, content: str) -> None:
        """Overwrite (never merge) an existing mirrored article's title/url/
        content — the re-sync-on-edit path. Deliberately NOT
        Library.upsert()/update_content(): upsert()'s merge keeps existing
        non-empty content, and update_content() only ever touches `content`.
        A re-sync must always win, the same way an admin's own edit to a
        draft always should — the mirrored row is a reflection of body_md,
        not independently-editable content of its own."""
        self.conn.execute(
            "UPDATE articles SET title=?, url=?, content=?, updated_at=? WHERE id=?",
            (title, normalize_url(url), content, _now(), article_id),
        )
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
                      featured: int = 0, advisor: int = 0, source: str | None = None) -> int:
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
        demographic_before, cost_note_before = demographic.strip(), cost_note.strip()
        notes_before, local_markets_before = notes.strip(), local_markets.strip()
        demographic_fixed, cost_note_fixed = _voice_fix(demographic_before), _voice_fix(cost_note_before)
        notes_fixed, local_markets_fixed = _voice_fix(notes_before), _voice_fix(local_markets_before)
        cur = self.conn.execute(
            """INSERT INTO communities (name, slug, url, demographic, cost_band,
               cost_note, sponsorship_type, sponsor_name, access, format, notes,
               categories_json, approved, submitted_by, created_at, updated_at,
               reach, local_markets, featured, advisor)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (name.strip(), slug, url.strip(), demographic_fixed,
             cost_band, cost_note_fixed, sponsorship_type, sponsor_name.strip(),
             access.strip(), format.strip(), notes_fixed, json.dumps(categories),
             approved, submitted_by.strip(), now, now,
             reach, local_markets_fixed, featured, advisor),
        )
        self.conn.commit()
        new_id = cur.lastrowid
        # Logged AFTER insert, using the real row id — no id exists until
        # the INSERT itself returns one (same reason add_original_content/
        # add_category_feature do the same).
        self.log_voice_correction("communities", new_id, "demographic", demographic_before, demographic_fixed, source=source, rule=_voice_fix_rule(demographic_before))
        self.log_voice_correction("communities", new_id, "cost_note", cost_note_before, cost_note_fixed, source=source, rule=_voice_fix_rule(cost_note_before))
        self.log_voice_correction("communities", new_id, "notes", notes_before, notes_fixed, source=source, rule=_voice_fix_rule(notes_before))
        self.log_voice_correction("communities", new_id, "local_markets", local_markets_before, local_markets_fixed, source=source, rule=_voice_fix_rule(local_markets_before))
        return new_id

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

    def list_communities_for_directory(self) -> list[dict]:
        """Approved communities for the public directory, with each one's profile
        Bottom line joined in by a single query (PR 2a): `bottom_line` (the
        profile's verdict_summary), `bottom_line_pending` (the whole-profile
        needs_review flag, for the "under review" tag) and `ideal_member`
        (search text only). A community with no profile row gets None/0 for
        these, never an error."""
        rows = self.conn.execute(
            """SELECT c.*, p.verdict_summary AS bottom_line, p.ideal_member AS ideal_member,
                      COALESCE(p.needs_review, 0) AS bottom_line_pending
               FROM communities c LEFT JOIN community_profiles p ON p.community_id = c.id
               WHERE c.approved=1 ORDER BY c.name"""
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
                         demographic: Optional[str], cost_band: str, categories: list[str],
                         cost_note: Optional[str] = None, sponsorship_type: str = "Independent",
                         sponsor_name: str = "", access: str = "", format: str = "",
                         notes: Optional[str] = None, reach: str = "National",
                         local_markets: str = "", featured: int = 0,
                         advisor: int = 0, source: str | None = None) -> None:
        """Update a community's listing fields.

        `demographic`, `cost_note` and `notes` are RETIRED (PR 2a, 2026-09; see
        `linklib.community_profile.RETIRED_COMMUNITY_FIELDS`) and frozen in the
        schema: passing `None` — what the merged edit page does — leaves the
        stored value untouched, so a save can never blank them. A caller that
        passes a string still writes it (tests, old scripts); the running app
        does not."""
        # Only check when the URL is actually changing — see update_tool for why.
        current = self.get_community(community_id)
        if current and normalize_url(url) != normalize_url(current["url"]):
            dup = self._find_community_by_normalized_url(url, exclude_id=community_id)
            if dup:
                raise DuplicateURLError("community", dup["id"], dup["name"], dup["slug"])
        sets = ["name=?", "url=?", "cost_band=?", "sponsorship_type=?", "sponsor_name=?",
                "access=?", "format=?", "categories_json=?", "updated_at=?", "reach=?",
                "local_markets=?", "featured=?", "advisor=?"]
        params: list = [name.strip(), url.strip(), cost_band, sponsorship_type, sponsor_name.strip(),
                        access.strip(), format.strip(), json.dumps(categories), _now(), reach,
                        self._vf("communities", community_id, "local_markets", local_markets.strip(), source=source),
                        featured, advisor]
        for col, val in (("demographic", demographic), ("cost_note", cost_note), ("notes", notes)):
            if val is not None:
                sets.append(f"{col}=?")
                params.append(self._vf("communities", community_id, col, val.strip(), source=source))
        params.append(community_id)
        self.conn.execute(f"UPDATE communities SET {', '.join(sets)} WHERE id=?", params)
        self.conn.commit()
        # Manual logo override staleness — mirrors update_tool exactly.
        if current and current.get("logo_manual_override") and _url_domain_changed(current["url"], url):
            self.conn.execute(
                "UPDATE communities SET logo_override_stale=1 WHERE id=?", (community_id,)
            )
            self.conn.commit()

    def update_community_content(self, community_id: int, name: str, source: str | None = None) -> None:
        """Narrow update for scripts/seed_communities.py's re-sync pass: touches
        only `name`. `notes` (the retired "Short description") used to sync here
        too; PR 2a froze it, so the seed no longer touches it. `advisor`
        re-syncs from the same COMMUNITIES source list via a separate direct
        UPDATE in the caller. demographic/cost_band/cost_note/sponsorship_type/
        sponsor_name/access/format/categories_json/approved/reach/local_markets/
        featured are admin-owned, edited on the community edit page, and never
        touched here — otherwise an admin's edit would get silently reverted on
        the next deploy's re-sync."""
        self.conn.execute(
            "UPDATE communities SET name=?, updated_at=? WHERE id=?",
            (name.strip(), _now(), community_id),
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

    def set_community_logo(self, community_id: int, logo_path: str, force: bool = False) -> bool:
        """Records a downloaded-and-stored logo asset — mirrors set_tool_logo
        exactly, including the manual-override guard (see that method's
        docstring for the full reasoning)."""
        if not force:
            row = self.conn.execute(
                "SELECT logo_manual_override FROM communities WHERE id=?", (community_id,)
            ).fetchone()
            if row and row["logo_manual_override"]:
                return False
        self.conn.execute(
            "UPDATE communities SET logo_path=?, updated_at=? WHERE id=?",
            (logo_path.strip(), _now(), community_id),
        )
        self.conn.commit()
        return True

    def set_community_logo_manual(self, community_id: int, logo_path: str) -> None:
        """Admin-set logo override — mirrors set_tool_logo_manual exactly."""
        self.conn.execute(
            "UPDATE communities SET logo_path=?, logo_manual_override=1, "
            "logo_override_stale=0, updated_at=? WHERE id=?",
            (logo_path.strip(), _now(), community_id),
        )
        self.conn.commit()

    def clear_community_logo_override(self, community_id: int) -> None:
        """"Revert to automatic" — mirrors clear_tool_logo_override exactly."""
        self.conn.execute(
            "UPDATE communities SET logo_path='', logo_manual_override=0, "
            "logo_override_stale=0, updated_at=? WHERE id=?",
            (_now(), community_id),
        )
        self.conn.commit()

    def dismiss_community_logo_stale(self, community_id: int) -> None:
        """Mirrors dismiss_tool_logo_stale exactly."""
        self.conn.execute(
            "UPDATE communities SET logo_override_stale=0, updated_at=? WHERE id=?",
            (_now(), community_id),
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

    def community_competitor_counts(self) -> dict[int, int]:
        """{community_id: curated-similar-community count} — the
        Communities-side mirror of tool_competitor_counts, same bulk-query
        reasoning (one grouped query for the admin list's completeness
        filter, not a per-row list_community_competitors call)."""
        out: dict[int, int] = {}
        rows = self.conn.execute(
            """SELECT id AS community_id, COUNT(*) AS n FROM (
                   SELECT community_id AS id FROM community_competitors
                   UNION ALL
                   SELECT competitor_id AS id FROM community_competitors
               ) GROUP BY id"""
        ).fetchall()
        for r in rows:
            out[r["community_id"]] = r["n"]
        return out

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

    # The live prose columns upsert_community_profile writes, in the order the
    # INSERT lists them, plus the twelve `*_ai_confident` columns after them.
    # Retired columns (linklib.community_profile.RETIRED_PROFILE_FIELDS) are
    # deliberately NOT here: they are written only when a caller passes one.
    _PROFILE_TEXT_COLUMNS = (
        "ideal_member", "anti_fit", "value_prop", "format_reality", "engagement_level",
        "sponsor_relationship_note", "application_friction", "cost_value_verdict",
        "notable_members", "public_criticism", "verdict_summary", "business_model",
        "cpe_eligible", "resources_included", "jobs_program",
    )

    def upsert_community_profile(self, community_id: int, ideal_member: str = "",
                                 anti_fit: str = "", value_prop: str = "",
                                 format_reality: str = "", engagement_level: str = "",
                                 sponsor_relationship_note: str = "",
                                 application_friction: str = "", cost_value_verdict: str = "",
                                 notable_members: str = "",
                                 public_criticism: str = "", verdict_summary: str = "",
                                 low_confidence: int = 0, business_model: str = "",
                                 cpe_eligible: str = "",
                                 resources_included: str = "", needs_review: int = 0,
                                 jobs_program: str = "",
                                 confidence: Optional[dict] = None,
                                 clear_verification_stamp: bool = False, source: str | None = None,
                                 *, founded_year: Optional[int] = None,
                                 primary_purpose: Optional[str] = None,
                                 platform_type: Optional[str] = None,
                                 meeting_format: Optional[str] = None,
                                 event_style: Optional[str] = None,
                                 seniority_band: Optional[str] = None,
                                 stage_focus: Optional[str] = None,
                                 team_or_individual: Optional[str] = None) -> None:
        """Insert or fully replace a community's LIVE profile fields. There's no
        partial update here (unlike update_community_content's narrow sync) — the
        admin edit form always submits every live field, generated or hand-written.

        RETIRED columns (PR 2a, 2026-09 — the keyword-only arguments at the end,
        listed in `linklib.community_profile.RETIRED_PROFILE_FIELDS`) are frozen:
        the default `None` means "leave whatever is stored untouched", so a save
        from the merged edit page, which no longer carries them, can never blank
        a retired column. A caller that passes one explicitly (a test, an old
        seed) still writes it; nothing in the running app does.

        Every live prose field has a hard maximum in
        `linklib.community_profile.PROFILE_LIMITS`; a value over it raises and
        nothing is saved. `cpe_eligible` is coerced to the Yes/No/Unclear
        vocabulary (see `coerce_cpe_eligible`).

        clear_verification_stamp (2026-08, stale-stamp fix) must be an
        EXPLICIT, separate signal from needs_review — needs_review can be 1
        either because this save followed a fresh Generate click
        (profile_ai_drafted) OR because the admin manually ticked the
        "needs review" checkbox with no fresh draft at all, and only the
        former should clear the "Reviewed by X on Y" stamp. The submit route
        passes profile_ai_drafted here; scripts/regen_ai_drafted_fields.py
        forces needs_review=0 (so no review flag is set) while still passing
        this =True, since it's still a fresh, not-yet-human-reviewed draft.

        `confidence` (2026-08 confidence indicator): an optional
        {field_name: 0|1|None} dict covering
        `linklib.enrich.COMMUNITY_CONFIDENCE_FIELDS` — since this whole method
        is a full replace of the live columns on every save (unlike the tools
        table's COALESCE-based narrow updates), the caller is responsible for
        deciding each field's value on every call, not this method: pass the
        fresh model-reported value for a field that was just (re)drafted this
        save, or the field's own previous value (read back from
        `get_community_profile` first) to carry it forward unchanged, or `None`
        to write NULL (no signal). A missing key defaults to `None`/NULL.

        The Recommender's controlled-vocabulary `*_tags` columns were dropped
        entirely once the quiz they existed for was replaced by the Chat
        Matchmaker, alongside the admin panel that edited them."""
        values = {
            "ideal_member": ideal_member, "anti_fit": anti_fit, "value_prop": value_prop,
            "format_reality": format_reality, "engagement_level": engagement_level,
            "sponsor_relationship_note": sponsor_relationship_note,
            "application_friction": application_friction,
            "cost_value_verdict": cost_value_verdict, "notable_members": notable_members,
            "public_criticism": public_criticism, "verdict_summary": verdict_summary,
            "business_model": business_model, "cpe_eligible": cpe_eligible,
            "resources_included": resources_included, "jobs_program": jobs_program,
        }
        labels = {
            "ideal_member": "Ideal member", "anti_fit": "Who should skip it",
            "value_prop": "Value proposition", "format_reality": "Programming",
            "engagement_level": "Engagement level", "application_friction": "Application friction",
            "business_model": "Business model", "sponsor_relationship_note": "Sponsor relationship",
            "cost_value_verdict": "Cost vs. value", "notable_members": "Notable members",
            "public_criticism": "Trade-offs to weigh", "verdict_summary": "Bottom line",
            "resources_included": "Resources included", "jobs_program": "Jobs program",
        }
        for col, (_target, limit) in PROFILE_LIMITS.items():
            self._check_text_field_length(labels[col], (values.get(col) or "").strip(), limit)
        values["cpe_eligible"] = _coerce_cpe(values["cpe_eligible"])
        confidence = confidence or {}
        conf_cols = [f"{f}_ai_confident" for f in self._COMMUNITY_CONFIDENCE_FIELDS]
        conf_vals = [confidence.get(f) for f in self._COMMUNITY_CONFIDENCE_FIELDS]

        cols = ["community_id"]
        params: list = [community_id]
        for col in self._PROFILE_TEXT_COLUMNS:
            cols.append(col)
            params.append(self._vf("community_profiles", community_id, col,
                                    (values.get(col) or "").strip(), source=source))
        cols += ["low_confidence", "updated_at", "needs_review"]
        params += [low_confidence, _now(), needs_review]
        cols += conf_cols
        params += conf_vals
        # Retired columns: written only when a caller passed one explicitly.
        retired = {
            "founded_year": founded_year, "primary_purpose": primary_purpose,
            "platform_type": platform_type, "meeting_format": meeting_format,
            "event_style": event_style, "seniority_band": seniority_band,
            "stage_focus": stage_focus, "team_or_individual": team_or_individual,
        }
        for col, val in retired.items():
            if val is None:
                continue
            cols.append(col)
            params.append(self._vf("community_profiles", community_id, col, val.strip(), source=source)
                          if isinstance(val, str) else val)
        placeholders = ",".join("?" for _ in cols)
        updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "community_id")
        self.conn.execute(
            f"INSERT INTO community_profiles ({', '.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT(community_id) DO UPDATE SET {updates}",
            params,
        )
        self.conn.commit()
        if clear_verification_stamp:
            self._supersede_narrative_review("community", "community_profile", community_id)

    def update_community_profile_research_fields(
        self, community_id: int, *,
        notable_members: Optional[str] = None, low_confidence: Optional[int] = None,
        anti_fit: Optional[str] = None, sponsor_relationship_note: Optional[str] = None,
        public_criticism: Optional[str] = None, needs_review: Optional[int] = None,
        jobs_program: Optional[str] = None,
        source: str | None = None,
    ) -> None:
        """Narrow, partial update for a deepened-research pass on a subset of
        fields (e.g. a later research round that only re-covers a few fields
        rather than the whole profile) — unlike upsert_community_profile,
        which always fully replaces every column, this only SETs the columns
        whose keyword argument was actually passed (not None), leaving every
        other field on the row untouched. A no-op on a field is "omit the
        argument," not "pass None" — every parameter here is a column that
        can legitimately hold NULL/empty, so
        there's no way to distinguish "leave alone" from "set to null" other
        than by omission. Caller is responsible for not passing None for a
        field it actually wants nulled out; today's only caller
        (scripts/archive/patch_round3_community_profiles.py) never needs to."""
        fields = {
            "notable_members": notable_members,
            "low_confidence": low_confidence, "anti_fit": anti_fit,
            "sponsor_relationship_note": sponsor_relationship_note,
            "public_criticism": public_criticism, "needs_review": needs_review,
            "jobs_program": jobs_program,
        }
        fields = {k: v for k, v in fields.items() if v is not None}
        if not fields:
            return
        set_clause = ", ".join(f"{col}=?" for col in fields)
        values = [
            self._vf("community_profiles", community_id, k, v.strip(), source=source) if isinstance(v, str) else v
            for k, v in fields.items()
        ]
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

    def flag_community_profile_needs_review(self, community_id: int) -> None:
        """One-click "Flag for review" (2026-08 quick-toggle follow-up) — the
        mirror action of mark_community_profile_reviewed: sets `needs_review`
        to 1 from the admin list alone, without opening the profile edit
        form. Same narrow single-column UPDATE shape, and the same no-op
        (not an error) precedent for a community with no profile row yet —
        deliberately does NOT create one; a community with nothing drafted
        yet has no profile to flag."""
        self.conn.execute(
            "UPDATE community_profiles SET needs_review=1, updated_at=? WHERE community_id=?",
            (_now(), community_id),
        )
        self.conn.commit()

    def count_communities_needing_review(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM community_profiles WHERE needs_review=1"
        ).fetchone()[0]

    def count_communities_needing_attention(self) -> int:
        """Distinct-community count for the admin badge (2026-09) — the
        dedup-safe replacement for `count_pending_communities() +
        count_communities_needing_review()` summed as two counts.

        Unlike tools (see `count_tools_needing_attention()`'s docstring,
        where pending-approval and needs-review overlap on EVERY unapproved
        tool by construction), overlap here is possible but not automatic:
        `add_community()` has no `needs_review` concept at all — that flag
        lives on `community_profiles`, a separate table with no row at all
        until a profile is actually drafted (`community_profiles.
        community_id` is its own primary key, one row per community, only
        ever created by a profile save). The public submission route
        (`/tools/communities/submit`) calls `add_community()` alone, with
        no profile generation — so a freshly submitted pending community
        has no `community_profiles` row and can't be needs-review=1 yet.
        `approve_community()` only ever flips `approved`, same as tools'
        `approve_tool()`.

        The overlap CAN still happen, though, and nothing in the code
        prevents it: `GET/POST /admin/tools/communities/{id}/profile` (the
        profile-edit page and its save route) never checks the community's
        `approved` status — confirmed by direct code read, not assumed —
        so an admin drafting/saving a profile for a still-pending
        submission (reachable by direct URL; there's no link to it from
        the Pending submissions table's own row, which offers only
        Approve/Reject) would leave that community `approved=0` with a
        `community_profiles` row at `needs_review=1`, exactly the
        double-count the sum pattern doesn't protect against. Whether any
        currently-pending community is actually in that state is a live-
        data question this session can't check (no production DB access) —
        so this is reported as a real possibility, not a confirmed live
        bug the way tools' case is.

        A `LEFT JOIN` (not an `INNER JOIN`) is required here, unlike
        tools' single-table query: most communities — pending ones
        especially — have no `community_profiles` row at all, and an
        INNER JOIN would silently drop every one of them from the count.
        `community_profiles.community_id` is a 1:1 primary key (no fan-out
        possible), so `COUNT(DISTINCT c.id)` is defensive/explicit rather
        than strictly required — but kept for the same reason
        `count_tools_needing_attention()`'s query comment calls out its
        own dedup mechanism explicitly. `count_pending_communities()` and
        `count_communities_needing_review()` are both untouched and keep
        their own other callers (the admin list's "needing review" filter,
        the review-status pill's "(n/12)" breakdown) — only the
        Communities card's badge wiring in `webapp.tasks.open_task_counts()`
        reads this method now, as the sole count for that badge."""
        return self.conn.execute(
            """SELECT COUNT(DISTINCT c.id) FROM communities c
               LEFT JOIN community_profiles p ON p.community_id = c.id
               WHERE c.approved=0
                  OR p.needs_review=1"""
        ).fetchone()[0]

    def community_profile_needs_review_ids(self) -> set[int]:
        """Which community_ids currently have needs_review=1 — used by the
        admin communities list to badge/filter rows without joining the full
        community_profiles row per community."""
        return {r[0] for r in self.conn.execute(
            "SELECT community_id FROM community_profiles WHERE needs_review=1"
        ).fetchall()}

    # The 12 Community profile fields tracked for confidence — mirrors
    # linklib.enrich.COMMUNITY_CONFIDENCE_FIELDS verbatim (not imported, to
    # keep linklib.db free of an enrich.py dependency, same reasoning as
    # every other hand-duplicated small constant in this file).
    _COMMUNITY_CONFIDENCE_FIELDS = (
        "ideal_member", "anti_fit", "value_prop", "business_model", "format_reality",
        "engagement_level", "sponsor_relationship_note", "application_friction",
        "cost_value_verdict", "notable_members", "public_criticism", "verdict_summary",
    )

    # The full set of Community-profile narrative fields the admin
    # completeness filter (2026-09) checks for emptiness — mirrors
    # linklib.compare.COMMUNITY_PROFILE_GROUPS' 14 grouped fields plus
    # Bottom line (verdict_summary), hand-duplicated here rather than
    # imported, same "keep linklib.db free of a sibling-module dependency"
    # convention as _COMMUNITY_CONFIDENCE_FIELDS just above.
    _COMMUNITY_NARRATIVE_FIELDS = (
        "ideal_member", "anti_fit", "value_prop",
        "format_reality", "engagement_level", "application_friction",
        "business_model", "sponsor_relationship_note", "cost_value_verdict",
        "notable_members", "public_criticism", "verdict_summary",
        "resources_included", "jobs_program", "cpe_eligible",
    )

    def community_profile_quality_flags(self) -> dict[int, dict]:
        """Item 6 (Aug 2026 UI pass) — read-only surfacing of quality
        signals the admin communities LIST page never showed, even though
        both already exist and are fully persisted on community_profiles
        (low_confidence: a real checkbox on the per-profile edit view,
        auto-set from Generate; the 12 *_ai_confident columns: inline
        badges on that same edit view). No new columns — this is purely a
        bulk read, one query for every community, same reasoning as
        community_profile_needs_review_ids just above (avoid a full-row
        join per community on a list page).

        Returns {community_id: {"low_confidence": bool,
        "unconfident_count": int}} for every community with a profile row;
        a community with no profile row at all has no entry (nothing to
        flag)."""
        cols = ", ".join(f"{f}_ai_confident" for f in self._COMMUNITY_CONFIDENCE_FIELDS)
        rows = self.conn.execute(
            f"SELECT community_id, low_confidence, {cols} FROM community_profiles"
        ).fetchall()
        out: dict[int, dict] = {}
        for r in rows:
            row = dict(r)
            unconfident = sum(
                1 for f in self._COMMUNITY_CONFIDENCE_FIELDS
                if row.get(f"{f}_ai_confident") is not None and not bool(row[f"{f}_ai_confident"])
            )
            out[row["community_id"]] = {
                "low_confidence": bool(row["low_confidence"]),
                "unconfident_count": unconfident,
            }
        return out

    def community_profile_has_empty_narrative_field(self) -> dict[int, bool]:
        """{community_id: True} for every community whose community_profiles
        row has at least one blank field among _COMMUNITY_NARRATIVE_FIELDS
        (the same field set linklib.compare.COMMUNITY_PROFILE_GROUPS + Bottom
        line track) — the Communities-side signal behind the admin list's
        "Missing something" completeness filter (2026-09). One bulk query
        for the whole page, same reasoning as community_profile_quality_flags
        just above. A community with NO community_profiles row at all has
        no entry here — the caller treats "no entry" as incomplete too,
        since it has none of the tracked fields at all."""
        cols = ", ".join(self._COMMUNITY_NARRATIVE_FIELDS)
        rows = self.conn.execute(
            f"SELECT community_id, {cols} FROM community_profiles"
        ).fetchall()
        out: dict[int, bool] = {}
        for r in rows:
            row = dict(r)
            out[row["community_id"]] = any(
                not (row.get(f) or "").strip() for f in self._COMMUNITY_NARRATIVE_FIELDS
            )
        return out

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

    def add_community_category(self, name: str, description: str = "", source: str | None = None) -> int:
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
        description_fixed = _voice_fix(description)
        cur = self.conn.execute(
            "INSERT INTO community_categories (name, description, sort_order) VALUES (?,?,?)",
            (name, description_fixed, next_order),
        )
        self.conn.commit()
        new_id = cur.lastrowid
        # Logged AFTER insert, using the real row id — no id exists until
        # the INSERT itself returns one (same reason add_original_content/
        # add_category_feature do the same).
        self.log_voice_correction("community_categories", new_id, "description", description, description_fixed, source=source, rule=_voice_fix_rule(description))
        return new_id

    def rename_community_category(self, category_id: int, new_name: str, description: str = "", source: str | None = None) -> int:
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
            (new_name, self._vf("community_categories", category_id, "description", description, source=source), category_id),
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
                       summary: str = "", published_at: str | None = None,
                       content: str = "", content_html: str = "") -> None:
        """Save (or re-save) a Read Later item. `content`/`content_html` are
        the result of a save-time fetch (2026-08 Reader cleanliness pass —
        see the read_later ALTER TABLE migration comment) — optional, since a
        caller that can't fetch (or chooses not to) should still be able to
        queue the URL, same as before this pair of columns existed.

        On a re-save of an already-queued URL, content/content_html are only
        ever REPLACED by a non-empty new value, never blanked by an empty one
        — the same write-once-on-empty guard `Library.upsert()` uses for
        articles.content, so a re-save whose own fetch happened to fail can't
        destroy a previously-cached good copy."""
        self.conn.execute(
            """INSERT INTO read_later (user_id, url, title, source, summary, published_at, added_at, content, content_html)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(user_id, url) DO UPDATE SET
                   title=excluded.title, source=excluded.source,
                   summary=excluded.summary, published_at=excluded.published_at,
                   content=CASE WHEN excluded.content != '' THEN excluded.content ELSE read_later.content END,
                   content_html=CASE WHEN excluded.content_html != '' THEN excluded.content_html ELSE read_later.content_html END""",
            (user_id, url, title, source, summary, published_at, _now(), content, content_html),
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

    def get_read_later_by_url(self, user_id: int, url: str) -> dict | None:
        """A single Read Later row for this user by URL, or None — the Reader's
        read-time cache lookup (mirrors the existing articles-by-url match in
        webapp._resolve_reader_content) and the source-of-truth for the
        per-item "Refresh" action's before-you-refresh existence check."""
        row = self.conn.execute(
            "SELECT * FROM read_later WHERE user_id=? AND url=?", (user_id, url)
        ).fetchone()
        return dict(row) if row else None

    def update_read_later_content(self, user_id: int, url: str, content: str, content_html: str) -> bool:
        """Replace a Read Later row's cached content — the manual per-item
        "Refresh" action's write path (deliberately its own narrow method,
        not a call through add_read_later, since add_read_later's other
        fields — title/source/summary — have no fresh values to offer here
        and must never be touched by a content-only refresh).

        Never blanks existing content: an empty content AND content_html (a
        failed refresh fetch) is a no-op that leaves the previously-cached
        copy alone, same non-destructive guard add_read_later's own ON
        CONFLICT uses. Returns False on that no-op case or when the URL
        isn't actually in this user's Read Later list; True on a real write."""
        if not content and not content_html:
            return False
        cur = self.conn.execute(
            "UPDATE read_later SET content=?, content_html=? WHERE user_id=? AND url=?",
            (content, content_html, user_id, url),
        )
        self.conn.commit()
        return cur.rowcount > 0

    # The "library queue" section that used to live here (add_to_queue,
    # list_queue, queue_count, dismiss_queue_item, remove_from_queue,
    # update_queue_published, promote_queue_item, article_urls, queue_urls,
    # last_saved_at, _queue_to_dict) was retired in full, 2026-09 (PR 3) —
    # see the library_queue table's own comment in _SCHEMA above for why.

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
                            exa_result_count: int = 0, exa_cost_usd: float = 0.0,
                            citations: Optional[list[dict]] = None) -> int:
        """Record one Ask turn. Backs all three surfaces (admin report, a
        user's own history, and the public community view) from one row.
        `conversation_id` groups follow-up turns; pass "" on the first turn of
        a conversation and the caller fills it in with str(id) after insert.
        `cost_usd` is the turn TOTAL (answer + any query-rewrite call + any
        query-time embedding call for hybrid retrieval + any Exa web-search
        call); the rewrite_*/embed_*/exa_* args break out each call's share
        of it. (embed_* here is the user-cap cost of embedding the QUESTION —
        a different thing from article_embeddings.cost_usd, which is Brian's
        embed-on-save overhead and never touches this table. exa_cost_usd —
        2026-09, Exa cost-tracking foundation — mirrors Answer.exa_cost_usd/
        exa_result_count from linklib.agent.retrieve_exa exactly the same
        way embed_cost_usd already mirrors the embedding call's share.)
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
                exa_result_count, exa_cost_usd,
                citations_json, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (conversation_id, turn_index, user_id, question.strip(), answer,
             model, effort, int(use_library), int(use_feed), int(use_web),
             input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens,
             cost_usd, rewrite_input_tokens, rewrite_output_tokens, rewrite_cost_usd,
             embed_input_tokens, embed_cost_usd,
             exa_result_count, exa_cost_usd,
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

    # Deepest/highest-quality curated model, matching linklib.models._REGISTRY's
    # own "Best quality" entry — the default for the AI model selection toggle
    # below, quality over cost, same reasoning LINKLIB_ENRICH_MODEL's own
    # fallback uses (see linklib/enrich.py's DEFAULT_MODEL).
    _DEFAULT_ENRICH_MODEL = "claude-opus-5"

    def get_enrich_model(self) -> str:
        """The live, DB-stored model id enrichment (Description, Agent
        taxonomy, Competitive differentiation, Community profile fields, and
        every other linklib.enrich generation call) actually uses — settable
        from /admin/system/ai without a redeploy, same reasoning
        get_exa_enabled's Phase 7 kill switch already established: env vars
        need a deploy to change, a DB-backed setting doesn't. Falls back to
        LINKLIB_ENRICH_MODEL (or its own hardcoded default) when no selection
        has ever been saved, so an unconfigured install keeps working exactly
        as it did before this setting existed."""
        raw = (self.get_setting("enrich_model") or "").strip()
        if raw:
            return raw
        from .enrich import DEFAULT_MODEL
        return DEFAULT_MODEL

    def set_enrich_model(self, model_id: str) -> None:
        self.set_setting("enrich_model", model_id.strip())

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

    def list_public_ask_questions(self, query: str = "", limit: int = 200,
                                   helpful_only: bool = False) -> list[dict]:
        """Non-hidden Q&A for the community browse view, newest first, optionally
        text-filtered on question/answer. Callers render `asker_name`/
        `asker_username` unless `anonymized` is set, in which case show a
        generic label instead — anonymizing here never affects the admin or
        the asker's own history view, both of which always show the real name.
        `helpful_only` (Phase 2, the FP&A Buddy "search past questions"
        feature on /tools/fpa-buddy) additionally restricts to questions with
        at least one ask_feedback.rating='helpful' row — an EXISTS check, not
        a join, so a question with several raters (some maybe 'inaccurate')
        still appears exactly once as long as any one of them rated it
        helpful. ask_feedback carries no declared FK to ask_questions, so
        this is matched by question_id convention, same as everywhere else
        that joins the two tables."""
        base = """SELECT aq.*, u.username AS asker_username, u.name AS asker_name
                  FROM ask_questions aq LEFT JOIN users u ON u.id = aq.user_id
                  WHERE aq.hidden_public=0"""
        params: list = []
        if helpful_only:
            base += """ AND EXISTS (
                SELECT 1 FROM ask_feedback f
                WHERE f.question_id = aq.id AND f.rating = 'helpful'
            )"""
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

    def list_ask_feedback(self, rating: str | None = None, reviewed: bool | None = None,
                          limit: int = 200) -> list[dict]:
        """Feedback rows newest first, joined with the rated turn (question,
        answer, model, cost, citations snapshot) and the rater's identity —
        everything the admin triage view shows. Pass `rating` and/or
        `reviewed` to filter (combined with AND when both are given — same
        convention as list_community_gap_submissions' own `reviewed`
        param)."""
        clauses, params = [], []
        if rating:
            clauses.append("f.rating=?")
            params.append(rating)
        if reviewed is not None:
            clauses.append("f.reviewed=?")
            params.append(1 if reviewed else 0)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
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

    def toggle_ask_feedback_reviewed(self, feedback_id: int) -> None:
        """Manual "mark reviewed"/"mark unreviewed" toggle — mirrors
        toggle_community_gap_reviewed exactly (same flip-in-place shape),
        per the explicit decision to give ask_feedback the same manual
        pattern Community gaps already has, not an auto-clear-on-view one."""
        self.conn.execute(
            "UPDATE ask_feedback SET reviewed = 1 - reviewed WHERE id=?",
            (feedback_id,),
        )
        self.conn.commit()

    def count_unreviewed_ask_feedback(self, since: str = "") -> int:
        """Badge count for the FP&A Buddy feedback card — same shape as
        community_gap_counts()'s own `unreviewed` bucket."""
        where, params = "", []
        if since:
            where = "WHERE created_at >= ?"
            params = [since]
        unreviewed_where = f"{where} AND reviewed=0" if where else "WHERE reviewed=0"
        return self.conn.execute(
            f"SELECT COUNT(*) FROM ask_feedback {unreviewed_where}", params
        ).fetchone()[0]

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
    # of their par time at this math; /admin/thought-leadership/game-settings can retune from
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
        empty. Never overwrites existing rows — once seeded, /admin/thought-leadership/game-settings
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

    # -- RSS subscription list (feed_sections / feeds) -----------------------
    #
    # These two tables are the source of truth; preferred_sites.opml is a
    # derived cache. See the feed_sections table comment in _SCHEMA for why
    # the file can't be authoritative on Railway, and which three consumers
    # still read it unmodified (a fourth, the Archive Queue's own feed scan,
    # was retired along with the queue itself — 2026-09, PR 3).

    def list_feed_sections(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM feed_sections ORDER BY display_order ASC, name ASC"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_feed_section(self, section_id: int) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM feed_sections WHERE id = ?", (section_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_feeds(self, section_id: Optional[int] = None) -> list[dict]:
        """Every feed, in section order, with its section name attached.

        Ordered to match the OPML's own document order so write_opml() and the
        admin list render the same sequence.
        """
        sql = ("SELECT f.*, s.name AS section_name, "
               "       s.display_order AS section_display_order "
               "FROM feeds f JOIN feed_sections s ON s.id = f.section_id ")
        params: list = []
        if section_id is not None:
            sql += "WHERE f.section_id = ? "
            params.append(section_id)
        sql += ("ORDER BY s.display_order ASC, s.name ASC, "
                "f.display_order ASC, f.name ASC")
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def get_feed(self, feed_id: int) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT f.*, s.name AS section_name FROM feeds f "
            "JOIN feed_sections s ON s.id = f.section_id WHERE f.id = ?",
            (feed_id,),
        ).fetchone()
        return dict(row) if row else None

    def find_feed_by_url(self, xml_url: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM feeds WHERE xml_url = ?", (xml_url.strip(),)
        ).fetchone()
        return dict(row) if row else None

    def count_feeds_in_section(self, section_id: int) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM feeds WHERE section_id = ?", (section_id,)
        ).fetchone()[0]

    # excluded_feed_urls()/has_feeds() (the Archive Queue's own "which feeds
    # are read-only" lookup and its unseeded-DB fallback check) were retired
    # along with the queue itself — 2026-09, PR 3.

    def add_feed_section(self, name: str) -> int:
        next_order = self.conn.execute(
            "SELECT COALESCE(MAX(display_order), -1) + 1 FROM feed_sections"
        ).fetchone()[0]
        cur = self.conn.execute(
            "INSERT INTO feed_sections (name, display_order, created_at) VALUES (?,?,?)",
            (name.strip(), next_order, _now()),
        )
        self.conn.commit()
        return cur.lastrowid

    def rename_feed_section(self, section_id: int, name: str) -> None:
        self.conn.execute("UPDATE feed_sections SET name=? WHERE id=?",
                          (name.strip(), section_id))
        self.conn.commit()

    def delete_feed_section(self, section_id: int) -> None:
        """Delete an EMPTY section. Callers must check count_feeds_in_section
        first and surface the count; this raises rather than cascading, so a
        section's feeds can never be silently destroyed along with it."""
        remaining = self.count_feeds_in_section(section_id)
        if remaining:
            raise ValueError(f"section still has {remaining} feed(s)")
        self.conn.execute("DELETE FROM feed_sections WHERE id = ?", (section_id,))
        self.conn.commit()

    def add_feed(self, section_id: int, name: str, xml_url: str,
                 html_url: str = "", exclude_from_queue: bool = False,
                 has_paywall_cookie: bool = False,
                 has_active_subscription: bool = False,
                 show_on_current_feed: bool = False,
                 current_feed_side: str = "",
                 current_feed_order: int = 0) -> int:
        """Store a feed. `xml_url` is written verbatim apart from surrounding
        whitespace — no normalization, no query-string handling. A feed URL can
        carry a subscriber token, and rewriting one silently breaks the feed.

        `has_paywall_cookie` is frozen historical data as of 2026-08 — the
        admin checkbox that used to write it was replaced with a computed,
        read-only indicator (`extract.has_configured_cookie`), so this
        parameter only matters for a caller migrating old rows; the web app's
        own add/edit routes no longer pass anything but the default. It never
        changed fetch behaviour anywhere, before or after.

        `show_on_current_feed` defaults to False for every new feed — a
        deliberate product decision (2026-09), not just a schema default: a
        new subscription should never appear on the public /current-feed
        page unreviewed. `current_feed_order` defaults to 0 (ties with
        every other unset row, broken by feed id — see current_feed()'s sort).
        """
        next_order = self.conn.execute(
            "SELECT COALESCE(MAX(display_order), -1) + 1 FROM feeds WHERE section_id = ?",
            (section_id,),
        ).fetchone()[0]
        cur = self.conn.execute(
            "INSERT INTO feeds (section_id, name, xml_url, html_url, exclude_from_queue, "
            "has_paywall_cookie, has_active_subscription, show_on_current_feed, "
            "current_feed_side, current_feed_order, display_order, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (section_id, name.strip(), xml_url.strip(), html_url.strip(),
             int(bool(exclude_from_queue)), int(bool(has_paywall_cookie)),
             int(bool(has_active_subscription)), int(bool(show_on_current_feed)),
             (current_feed_side or "").strip(), int(current_feed_order), next_order, _now()),
        )
        self.conn.commit()
        return cur.lastrowid

    def update_feed(self, feed_id: int, section_id: int, name: str,
                    xml_url: str, html_url: str,
                    exclude_from_queue: bool = False,
                    has_paywall_cookie: bool = False,
                    has_active_subscription: bool = False,
                    show_on_current_feed: bool = False,
                    current_feed_side: str = "",
                    current_feed_order: int = 0) -> None:
        """Same verbatim-URL guarantee as add_feed — see its docstring.

        `has_paywall_cookie`, `has_active_subscription`,
        `show_on_current_feed`, `current_feed_side`, and `current_feed_order`
        are all written on every call, so the edit form has to round-trip the
        current values or a save would clear them. That's the same contract
        every other field on this method already has.
        """
        self.conn.execute(
            "UPDATE feeds SET section_id=?, name=?, xml_url=?, html_url=?, "
            "exclude_from_queue=?, has_paywall_cookie=?, has_active_subscription=?, "
            "show_on_current_feed=?, current_feed_side=?, current_feed_order=? "
            "WHERE id=?",
            (section_id, name.strip(), xml_url.strip(), html_url.strip(),
             int(bool(exclude_from_queue)), int(bool(has_paywall_cookie)),
             int(bool(has_active_subscription)), int(bool(show_on_current_feed)),
             (current_feed_side or "").strip(), int(current_feed_order), feed_id),
        )
        self.conn.commit()

    def set_feed_paywall_cookie(self, feed_id: int, needs_cookie: bool) -> None:
        """Toggle the cookie flag from the feed table's own row control. Narrow
        single-column update, same shape as move_feed_to_section (and the
        now-retired set_feed_excluded): it can't rewrite a URL in passing."""
        self.conn.execute("UPDATE feeds SET has_paywall_cookie=? WHERE id=?",
                          (int(bool(needs_cookie)), feed_id))
        self.conn.commit()

    def set_feed_active_subscription(self, feed_id: int, active: bool) -> None:
        """Toggle the informational subscription flag from the feed table's own
        row control. Narrow single-column update, same as the two above."""
        self.conn.execute("UPDATE feeds SET has_active_subscription=? WHERE id=?",
                          (int(bool(active)), feed_id))
        self.conn.commit()

    def move_feed_to_section(self, feed_id: int, section_id: int) -> None:
        """Regroup a feed. Touches section_id only — never the URL, so a feed
        carrying a subscriber token can be moved with no risk of a rewrite."""
        self.conn.execute("UPDATE feeds SET section_id=? WHERE id=?",
                          (section_id, feed_id))
        self.conn.commit()

    def set_feed_current_feed_display(self, feed_id: int, show: bool, side: str,
                                       order: int = 0) -> None:
        """The /current-feed row control — narrow, same shape as the three
        toggles above. `side` is free text ('old_school'/'new_school'/''),
        not CHECK-constrained, so a future third side is a rendering-code
        change, not a migration. All three columns are written together (not
        separate setters) since the admin form always submits them as one
        group — a feed marked "show" with no side selected would render
        nowhere on the page, which is worth preventing at the write site
        rather than discovering it as a silent gap later. `order` defaults to
        0 for a caller (like seed_current_feed_sides) that only cares about
        show/side — current_feed_order is seeded separately, and 0 is
        already the column's own default for an unset row."""
        self.conn.execute(
            "UPDATE feeds SET show_on_current_feed=?, current_feed_side=?, "
            "current_feed_order=? WHERE id=?",
            (int(bool(show)), (side or "").strip(), int(order), feed_id))
        self.conn.commit()

    # set_feed_excluded (the "Read only" row-control toggle) was retired
    # along with the Archive Queue itself — 2026-09, PR 3.

    def delete_feed(self, feed_id: int) -> None:
        self.conn.execute("DELETE FROM feeds WHERE id = ?", (feed_id,))
        self.conn.commit()

    # -- OPML generation ----------------------------------------------------

    def opml_xml(self, title: str = "Brian subscriptions") -> str:
        """Render the current subscription list as OPML text.

        Two attribute rules matter and are not cosmetic:
        1. Section outlines carry NO htmlUrl. sources.preferred_domains walks
           every outline at any depth and reads `htmlUrl or xmlUrl`, so an
           htmlUrl on a section would leak a bogus domain into FP&A Buddy's
           web-search allowlist.
        2. A feed's htmlUrl is omitted entirely when empty, rather than written
           as htmlUrl="". preferred_domains prefers htmlUrl over xmlUrl, and an
           empty string is falsy there, so both shapes behave the same today —
           omitting it keeps the file honest and matches the hand-written
           original.
        """
        def esc(s: str) -> str:
            return (str(s).replace("&", "&amp;").replace("<", "&lt;")
                    .replace(">", "&gt;").replace('"', "&quot;"))

        by_section: dict[int, list[dict]] = {}
        for f in self.list_feeds():
            by_section.setdefault(f["section_id"], []).append(f)

        lines = ['<?xml version="1.0" encoding="UTF-8"?>',
                 f'<opml version="1.0"><head><title>{esc(title)}</title></head><body>']
        for section in self.list_feed_sections():
            sname = esc(section["name"])
            lines.append(f'  <outline text="{sname}" title="{sname}">')
            for f in by_section.get(section["id"], []):
                fname = esc(f["name"])
                attrs = (f'type="rss" text="{fname}" title="{fname}" '
                         f'xmlUrl="{esc(f["xml_url"])}"')
                if f["html_url"]:
                    attrs += f' htmlUrl="{esc(f["html_url"])}"'
                lines.append(f'    <outline {attrs}/>')
            lines.append('  </outline>')
        lines.append('</body></opml>')
        return "\n".join(lines) + "\n"

    def write_opml(self, path: str) -> bool:
        """Regenerate `path` from the feeds tables. Returns True if written.

        Call this after EVERY mutation of feed_sections/feeds, and once at app
        startup. It also clears sources.preferred_domains' lru_cache, which is
        deliberately folded in here rather than left to each caller: that cache
        is read once per process and never re-read, so a regenerated file with
        a stale cache would leave FP&A Buddy's allowlist wrong until the next
        deploy, silently. Making the two inseparable means no write path can
        forget one.

        Writes atomically (temp file + os.replace) because three separate
        consumers read this file at request time; a half-written file would be
        a parse error for all of them at once.

        No-ops when there are no feeds. An empty table means the seed hasn't
        run yet (or the DB is a fresh fixture), and regenerating from it would
        overwrite the curated repo copy with an empty subscription list.

        Also no-ops when the file on disk already matches what we'd write.
        That keeps the common boot (nothing changed since last deploy) from
        rewriting the file at all, and keeps any test that boots the app from
        touching the repo's working copy as a side effect. Skipping the cache
        clear in that branch is deliberate, not an oversight: identical
        content means the cached allowlist is already correct.
        """
        if not self.conn.execute("SELECT 1 FROM feeds LIMIT 1").fetchone():
            return False
        xml = self.opml_xml()
        try:
            with open(path, encoding="utf-8") as fh:
                if fh.read() == xml:
                    return False
        except OSError:
            pass
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(xml)
        os.replace(tmp, path)
        try:
            from .sources import preferred_domains
            preferred_domains.cache_clear()
        except Exception:
            pass
        return True

    def seed_feeds_from_opml(self, path: str) -> dict:
        """One-time import of an existing OPML file into the feeds tables.

        Guarded by a settings flag, NOT by "is the table empty". Those look
        identical on a fresh DB but diverge the moment an admin deletes every
        feed in a section, or the last feed outright: an emptiness check would
        re-import the whole file on the next restart and resurrect deliberately
        deleted feeds. This is the same bug _seed_toolbox shipped and had to
        fix; the flag means seeding happens exactly once per database, ever.

        Returns {"seeded": bool, "sections": n, "feeds": n}.
        """
        if self.get_setting("feeds_seeded_from_opml") == "1":
            return {"seeded": False, "sections": 0, "feeds": 0}

        from .feed import parse_opml
        try:
            metas = parse_opml(path)
        except Exception:
            return {"seeded": False, "sections": 0, "feeds": 0}
        if not metas:
            # Nothing to import — don't burn the flag, so a later boot with a
            # readable file still gets its chance to seed.
            return {"seeded": False, "sections": 0, "feeds": 0}

        section_ids: dict[str, int] = {s["name"]: s["id"] for s in self.list_feed_sections()}
        new_sections = new_feeds = 0
        for meta in metas:
            cat = meta.category or "Other"
            if cat not in section_ids:
                section_ids[cat] = self.add_feed_section(cat)
                new_sections += 1
            if self.find_feed_by_url(meta.xml_url):
                continue
            # exclude_from_queue is retired (frozen, unread — see the feeds
            # table's own schema comment); every newly seeded feed just
            # takes the column default. meta.xml_url goes in untouched.
            self.add_feed(section_ids[cat], meta.name or meta.xml_url,
                          meta.xml_url, meta.html_url or "")
            new_feeds += 1

        self.set_setting("feeds_seeded_from_opml", "1")
        return {"seeded": True, "sections": new_sections, "feeds": new_feeds}

    def seed_paywall_cookie_flags(self) -> dict:
        """Set the cookie flag for feeds that need one, once per database.

        Two sources, because this both MIGRATES and SEEDS:
          - any feed carrying a non-empty legacy `paywall_cookie_note` (an
            existing database that ran the free-text version), and
          - any feed on a `feed.PAYWALLED_DOMAINS` domain (a fresh database
            that never had notes to migrate).
        On Brian's database both routes select the same three feeds, so the
        set is identical either way; the domain arm exists so a brand-new
        deploy isn't left with every box unchecked.

        Guarded by a settings flag, NOT by "is the box unchecked" — same
        reasoning as seed_feeds_from_opml above, and the same bug _seed_toolbox
        shipped. An emptiness check would look correct on a fresh DB and then
        re-check a box the admin deliberately unchecked on the very next
        restart, because "never set" and "set and then cleared" are
        indistinguishable from a 0. The flag means this runs exactly once.

        Returns {"seeded": bool, "feeds": n}.
        """
        if self.get_setting("paywall_cookie_flags_seeded") == "1":
            return {"seeded": False, "feeds": 0}

        feeds = self.list_feeds()
        if not feeds:
            # Nothing to flag yet — don't burn the flag, so a later boot that
            # successfully seeds the feeds table still gets its chance.
            # Without this, one boot with an unreadable OPML would permanently
            # skip every feed seeded afterwards.
            return {"seeded": False, "feeds": 0}

        from .feed import PAYWALLED_DOMAINS
        updated = 0
        for feed in feeds:
            if feed.get("has_paywall_cookie"):
                continue
            haystack = f'{feed["xml_url"]} {feed.get("html_url") or ""}'
            legacy_note = (feed.get("paywall_cookie_note") or "").strip()
            if legacy_note or any(dom in haystack for dom in PAYWALLED_DOMAINS):
                self.set_feed_paywall_cookie(feed["id"], True)
                updated += 1

        # Burn the flag even when nothing matched. The table is non-empty by
        # this point, so "no paywalled feeds" is a real answer rather than a
        # not-ready-yet signal, and re-checking on every future boot would only
        # risk re-setting a cleared box.
        self.set_setting("paywall_cookie_flags_seeded", "1")
        return {"seeded": True, "feeds": updated}

    # Sections whose feeds seed straight onto a Current Feed side. Anything
    # else (News, Market Insights, Tools, or a future section) seeds as
    # not-shown — the column default already matches, so those rows are
    # simply left alone rather than written to their own default value.
    _CURRENT_FEED_SEED_SIDES = {"Blogs": "old_school", "Substacks": "new_school"}

    def seed_current_feed_sides(self) -> dict:
        """One-time seed of show_on_current_feed/current_feed_side from each
        feed's CURRENT section, so /current-feed works immediately on an
        existing database without Brian having to hand-tag every feed first.
        Blogs -> shown, old_school; Substacks -> shown, new_school; every
        other section (News, Market Insights, Tools, ...) is left at the
        column default (not shown, no side) — Brian adjusts from there via
        /admin/reader/feeds.

        Guarded by a settings flag, NOT by "is the column still at its
        default" — same reasoning as seed_paywall_cookie_flags/
        seed_feeds_from_opml above: an emptiness check can't tell "never
        seeded" from "deliberately set back to not-shown," so it would
        silently re-show a feed Brian turned off. The flag means this runs
        exactly once, ever.

        Returns {"seeded": bool, "feeds": n}.
        """
        if self.get_setting("current_feed_sides_seeded") == "1":
            return {"seeded": False, "feeds": 0}

        feeds = self.list_feeds()
        if not feeds:
            # Nothing to seed yet — don't burn the flag, same reasoning as
            # seed_paywall_cookie_flags's own empty-table guard.
            return {"seeded": False, "feeds": 0}

        updated = 0
        for feed in feeds:
            side = self._CURRENT_FEED_SEED_SIDES.get(feed["section_name"])
            if side:
                self.set_feed_current_feed_display(feed["id"], True, side)
                updated += 1

        self.set_setting("current_feed_sides_seeded", "1")
        return {"seeded": True, "feeds": updated}

    def seed_current_feed_order(self) -> dict:
        """One-time seed of current_feed_order (2026-09 follow-up) from the
        render order shown feeds already had before this column existed, so
        shipping it doesn't visually reorder anything on /current-feed —
        Brian reorders from here by hand.

        "Already had" means list_feeds()'s own ordering (section display
        order, then feed display order/name — the same ordering current_feed()
        grouped by side before this column existed), numbered 0, 1, 2, ...
        independently within each side. A hidden feed is left at the column
        default; its order is inert until it's shown, and there's nothing to
        seed for it anyway.

        Guarded by a settings flag, not an emptiness/default check — same
        reasoning as every other one-time feed seed in this file: 0 is also
        a real, deliberately-set "goes first" value, so "still at 0" can't
        tell never-seeded apart from already-first. A separate flag from
        seed_current_feed_sides() since this column, and this seed, shipped
        later — the two run independently and neither re-runs the other.

        Returns {"seeded": bool, "feeds": n}.
        """
        if self.get_setting("current_feed_order_seeded") == "1":
            return {"seeded": False, "feeds": 0}

        shown = [f for f in self.list_feeds() if f["show_on_current_feed"]]
        if not shown:
            # Nothing shown yet — don't burn the flag, same reasoning as
            # seed_current_feed_sides's own empty-table guard.
            return {"seeded": False, "feeds": 0}

        by_side: dict[str, list[dict]] = {}
        for f in shown:
            by_side.setdefault(f["current_feed_side"], []).append(f)

        updated = 0
        for side_feeds in by_side.values():
            for i, f in enumerate(side_feeds):
                self.set_feed_current_feed_display(f["id"], True, f["current_feed_side"], i)
                updated += 1

        self.set_setting("current_feed_order_seeded", "1")
        return {"seeded": True, "feeds": updated}

    def seed_voice_prompts(self) -> dict:
        """Populate voice_core/voice_fpa_buddy/voice_matchmaker from their
        current code-default constants, once per database (2026-08
        visibility follow-up — see linklib/voice_settings.py's module
        docstring for the full incident/rationale). Only ever writes a
        setting that's CURRENTLY EMPTY — an admin who already customized
        one before this shipped keeps their own text untouched.

        Guarded by a settings flag, NOT by "is it currently empty" at call
        time — same reasoning as seed_paywall_cookie_flags/seed_feeds_from_opml
        above, and the same bug _seed_toolbox originally shipped: an
        emptiness check can't tell "never seeded" from "seeded, then
        deliberately cleared at /admin/voice" apart, and would silently
        resurrect a deliberate clear-out on the next restart. The flag
        means every field is populated exactly once; after that, an empty
        field stays empty until an admin fills it in themselves.

        Returns {"seeded": bool, "populated": [key, ...]}."""
        if self.get_setting("voice_prompts_seeded") == "1":
            return {"seeded": False, "populated": []}

        # Lazy import: linklib.agent and linklib.matchmaker both import
        # Library from this module, so importing them at db.py's own
        # module level would be circular. Deferred to call time instead,
        # same pattern webapp/app.py already uses for these same constants.
        from .agent import VOICE_CORE_DEFAULT, VOICE_FPA_BUDDY_DEFAULT
        from .matchmaker import VOICE_MATCHMAKER_DEFAULT

        defaults = {
            "voice_core": VOICE_CORE_DEFAULT,
            "voice_fpa_buddy": VOICE_FPA_BUDDY_DEFAULT,
            "voice_matchmaker": VOICE_MATCHMAKER_DEFAULT,
        }
        populated = []
        for key, default_text in defaults.items():
            if not (self.get_setting(key) or "").strip():
                self.set_setting(key, default_text)
                populated.append(key)

        self.set_setting("voice_prompts_seeded", "1")
        return {"seeded": True, "populated": populated}

    # Sources Brian currently pays for, as of this column's introduction. Only
    # domains listed here are switched on; every other feed keeps the column's
    # 0 default. Stratechery and Public Comps are deliberately absent — they're
    # paywalled (they carry a cookie note) but not currently subscribed, which
    # is exactly the distinction this flag exists to record.
    _ACTIVE_SUBSCRIPTION_SEED_DOMAINS = ("mostlymetrics.com",)

    def seed_active_subscriptions(self) -> dict:
        """Set the informational subscription flag for known-paid sources.

        Flag-guarded rather than emptiness-checked, same reasoning as
        seed_paywall_cookie_flags: an unchecked box and a deliberately
        unchecked one are indistinguishable, so a re-run on every boot would
        silently re-check a feed Brian had just unchecked. Runs once per DB.

        Returns {"seeded": bool, "feeds": n}.
        """
        if self.get_setting("active_subscriptions_seeded") == "1":
            return {"seeded": False, "feeds": 0}

        feeds = self.list_feeds()
        if not feeds:
            # Same not-ready-yet guard as seed_paywall_cookie_flags — don't
            # burn the flag before there's anything to annotate.
            return {"seeded": False, "feeds": 0}

        updated = 0
        for feed in feeds:
            if feed.get("has_active_subscription"):
                continue
            haystack = f'{feed["xml_url"]} {feed.get("html_url") or ""}'
            if any(dom in haystack for dom in self._ACTIVE_SUBSCRIPTION_SEED_DOMAINS):
                self.set_feed_active_subscription(feed["id"], True)
                updated += 1

        self.set_setting("active_subscriptions_seeded", "1")
        return {"seeded": True, "feeds": updated}

    def close(self) -> None:
        self.conn.close()


@contextmanager
def open_library(path: str) -> Iterator[Library]:
    lib = Library(path)
    try:
        yield lib
    finally:
        lib.close()
