"""Regression coverage for the outage where opening the Library against an
existing (pre-migration) database crashed on every boot.

Root cause: _SCHEMA's CREATE TABLE IF NOT EXISTS for password_reset_requests
already included token_hash (for a brand-new DB), and a CREATE INDEX on
token_hash ran immediately after it, inside the same executescript() call.
On any DB where the table already existed (i.e. every production DB, which
predated token_hash), CREATE TABLE IF NOT EXISTS is a no-op, so the index
creation ran against a table that didn't have that column yet — the ALTER
TABLE that adds it doesn't run until after the whole _SCHEMA script finishes.
Every unit test used a fresh tmp_path DB, so none of them hit this; only a
DB carrying real migration history did.

These tests (a) pin the general rule — _SCHEMA must never index a column
that's only ever added by the ALTER TABLE migration loop — and (b) exercise
the exact scenario that broke: opening a database whose password_reset_requests
table predates token_hash/expires_at.
"""
import inspect
import pathlib
import re
import sqlite3
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import db as db_mod
from linklib.db import Library


def _migrated_columns() -> set[tuple[str, str]]:
    """(table, column) pairs added via the ALTER TABLE migration loop in
    Library.__init__ — i.e. columns that do NOT exist on a DB whose table
    predates that migration."""
    src = inspect.getsource(db_mod.Library.__init__)
    return set(re.findall(r"ALTER TABLE (\w+) ADD COLUMN (\w+)", src))


def _schema_indexed_columns() -> set[tuple[str, str]]:
    """(table, column) pairs any CREATE INDEX inside _SCHEMA references."""
    pairs = set()
    for m in re.finditer(r"CREATE INDEX IF NOT EXISTS \w+ ON (\w+)\(([^)]+)\)", db_mod._SCHEMA):
        table = m.group(1)
        for col in m.group(2).split(","):
            pairs.add((table, col.strip().split()[0]))   # strip trailing DESC etc
    return pairs


def test_schema_never_indexes_a_migration_only_column():
    """The general rule that prevents this class of bug: _SCHEMA (which runs
    in full on a fresh DB, but is a set of no-ops on an existing one) must
    never CREATE INDEX on a column that's only added by the ALTER TABLE
    migration loop below it — that loop runs after the whole schema script,
    so on an existing DB the column isn't there yet when the index tries to
    create. Any such index belongs in _POST_MIGRATION_INDEXES instead."""
    overlap = _migrated_columns() & _schema_indexed_columns()
    assert not overlap, f"_SCHEMA indexes column(s) only added by migration: {overlap}"


def test_post_migration_indexes_reference_migrated_columns():
    """Sanity check the other direction: _POST_MIGRATION_INDEXES exists to
    hold exactly these — if it's empty while migrated columns exist that
    need indexing, that's not itself a bug, but confirms the list is wired
    up and non-trivial for the case we know needs it (token_hash)."""
    assert any("token_hash" in stmt for stmt in db_mod._POST_MIGRATION_INDEXES)


def test_open_existing_db_predating_token_hash(tmp_path):
    """Exact repro of the outage: a password_reset_requests table created
    before token_hash/expires_at existed (i.e. any real production DB at the
    time PR #60 shipped). Opening it must not raise, must backfill the new
    columns, and must end up with the token_hash index in place."""
    path = str(tmp_path / "pre_existing.db")
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE password_reset_requests (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            username    TEXT NOT NULL DEFAULT '',
            created_at  TEXT NOT NULL,
            resolved_at TEXT NOT NULL DEFAULT ''
        )
    """)
    conn.execute("CREATE INDEX idx_pwreset_resolved ON password_reset_requests(resolved_at)")
    conn.commit()
    conn.close()

    lib = Library(path)   # must not raise
    try:
        cols = {r[1] for r in lib.conn.execute("PRAGMA table_info(password_reset_requests)").fetchall()}
        assert {"token_hash", "expires_at"} <= cols
        indexes = {r[1] for r in lib.conn.execute("PRAGMA index_list(password_reset_requests)").fetchall()}
        assert "idx_pwreset_token" in indexes
        # And the migrated columns actually work end to end.
        uid = lib.create_user("regressiontest", "supersecret", email="r@example.com")
        lib.create_password_reset_request(uid, "regressiontest", token_hash="abc123", expires_at="2099-01-01")
        assert lib.get_password_reset_by_token_hash("abc123") is not None
    finally:
        lib.close()

    # Re-opening (second boot) against the now-migrated DB must also be a no-op, not a crash.
    lib2 = Library(path)
    lib2.close()


def test_open_existing_db_predating_rewrite_columns(tmp_path):
    """ask_questions tables created before the history-aware-retrieval
    rewrite_* columns must migrate cleanly on open: no crash, columns
    backfilled with zeros, and the new record/cap paths work end to end."""
    path = str(tmp_path / "pre_rewrite.db")
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE ask_questions (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id       TEXT NOT NULL DEFAULT '',
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
            hidden_public         INTEGER NOT NULL DEFAULT 0,
            anonymized            INTEGER NOT NULL DEFAULT 0,
            created_at            TEXT NOT NULL
        )
    """)
    conn.execute("INSERT INTO ask_questions (user_id, question, created_at) "
                 "VALUES (1, 'old row', '2026-01-01T00:00:00Z')")
    conn.commit()
    conn.close()

    lib = Library(path)   # must not raise
    try:
        cols = {r[1] for r in lib.conn.execute("PRAGMA table_info(ask_questions)").fetchall()}
        assert {"rewrite_input_tokens", "rewrite_output_tokens", "rewrite_cost_usd"} <= cols
        # Pre-existing rows get zeros (the declared defaults).
        old = lib.conn.execute("SELECT rewrite_cost_usd FROM ask_questions WHERE question='old row'").fetchone()
        assert old[0] == 0
        # And the new turn-total accounting works end to end: cost_usd is the
        # total (answer + rewrite), so the monthly-cap SUM includes the
        # rewrite spend with no query changes.
        lib.record_ask_question(1, "q", "a", "claude-sonnet-4-6", "standard",
                                True, False, True, cost_usd=0.0103,
                                rewrite_input_tokens=200, rewrite_output_tokens=15,
                                rewrite_cost_usd=0.0003)
        assert lib.ask_cost_this_month(1) == pytest.approx(0.0103)
    finally:
        lib.close()

    # Second boot against the now-migrated DB is a no-op, not a crash.
    lib2 = Library(path)
    lib2.close()


def test_open_existing_db_predating_citations_and_feedback(tmp_path):
    """ask_questions tables created before citations_json (and before the
    ask_feedback table existed at all) must migrate cleanly on open: no
    crash, old turns backfilled with '[]', the feedback table + its indexes
    created, and the new snapshot/feedback paths working end to end."""
    path = str(tmp_path / "pre_citations.db")
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE ask_questions (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id       TEXT NOT NULL DEFAULT '',
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
            rewrite_input_tokens  INTEGER NOT NULL DEFAULT 0,
            rewrite_output_tokens INTEGER NOT NULL DEFAULT 0,
            rewrite_cost_usd      REAL NOT NULL DEFAULT 0,
            hidden_public         INTEGER NOT NULL DEFAULT 0,
            anonymized            INTEGER NOT NULL DEFAULT 0,
            created_at            TEXT NOT NULL
        )
    """)
    conn.execute("INSERT INTO ask_questions (user_id, question, created_at) "
                 "VALUES (1, 'old row', '2026-01-01T00:00:00Z')")
    conn.commit()
    conn.close()

    lib = Library(path)   # must not raise
    try:
        cols = {r[1] for r in lib.conn.execute("PRAGMA table_info(ask_questions)").fetchall()}
        assert "citations_json" in cols
        # Pre-existing rows get the declared '[]' default — their citations
        # were only ever sent to the client, never stored.
        old = lib.conn.execute(
            "SELECT citations_json FROM ask_questions WHERE question='old row'").fetchone()
        assert old[0] == "[]"
        # The brand-new table and its indexes exist.
        assert lib.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ask_feedback'").fetchone()
        indexes = {r[1] for r in lib.conn.execute("PRAGMA index_list(ask_feedback)").fetchall()}
        assert {"idx_ask_feedback_rating", "idx_ask_feedback_created"} <= indexes
        # And the new paths work end to end against the migrated DB.
        qid = lib.record_ask_question(
            1, "q", "a", "m", "standard", True, False, True,
            citations=[{"n": 1, "title": "T", "url": "https://ex.com", "type": "web"}])
        assert '"https://ex.com"' in lib.get_ask_question(qid)["citations_json"]
        lib.record_ask_feedback(qid, 1, "helpful")
        assert lib.ask_feedback_counts()["helpful"] == 1
    finally:
        lib.close()

    # Second boot against the now-migrated DB is a no-op, not a crash.
    lib2 = Library(path)
    lib2.close()


def test_fresh_db_has_citations_and_feedback(tmp_path):
    """Fresh DBs get citations_json straight from _SCHEMA's CREATE TABLE and
    the ask_feedback table with its inline UNIQUE(question_id, user_id) —
    pinned so the fresh and migrated paths can't drift apart."""
    lib = Library(str(tmp_path / "fresh_feedback.db"))
    try:
        cols = {r[1] for r in lib.conn.execute("PRAGMA table_info(ask_questions)").fetchall()}
        assert "citations_json" in cols
        fb_cols = {r[1] for r in lib.conn.execute("PRAGMA table_info(ask_feedback)").fetchall()}
        assert {"question_id", "user_id", "rating", "comment", "created_at", "updated_at"} <= fb_cols
        # The upsert target: (question_id, user_id) is UNIQUE.
        lib.record_ask_feedback(1, 1, "helpful")
        lib.record_ask_feedback(1, 1, "not_helpful")
        assert lib.conn.execute("SELECT COUNT(*) FROM ask_feedback").fetchone()[0] == 1
    finally:
        lib.close()


def test_fresh_db_has_rewrite_columns(tmp_path):
    """Fresh DBs get the rewrite_* columns straight from _SCHEMA's CREATE
    TABLE (the ALTER in the migration loop no-ops) — pinned so the two paths
    can't drift apart."""
    lib = Library(str(tmp_path / "fresh_rewrite.db"))
    try:
        cols = {r[1] for r in lib.conn.execute("PRAGMA table_info(ask_questions)").fetchall()}
        assert {"rewrite_input_tokens", "rewrite_output_tokens", "rewrite_cost_usd"} <= cols
    finally:
        lib.close()


def test_enrichment_cost_table_on_existing_and_fresh_db(tmp_path):
    """enrichment_cost (#105) is a brand-new table, not a migrated column —
    CREATE TABLE IF NOT EXISTS handles both a fresh DB and one that predates
    this change with no ALTER TABLE entry needed. Pinned so a future change
    doesn't assume it needs migration-loop handling like a new column would."""
    path = str(tmp_path / "pre_enrichment_cost.db")
    # Build a DB on the full current schema, then drop enrichment_cost to
    # simulate "a real DB from before this table existed" without having to
    # hand-maintain a second copy of the whole legacy schema.
    setup = Library(path)
    setup.conn.execute("DROP TABLE enrichment_cost")
    setup.conn.commit()
    setup.close()

    lib = Library(path)   # must not raise
    try:
        assert lib.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='enrichment_cost'"
        ).fetchone()
        indexes = {r[1] for r in lib.conn.execute("PRAGMA index_list(enrichment_cost)").fetchall()}
        assert "idx_enrichment_cost_article" in indexes
        lib.record_enrichment_cost(None, "claude-haiku-4-5-20251001", cost_usd=0.001)
        assert lib.enrichment_cost_total() == pytest.approx(0.001)
    finally:
        lib.close()

    lib2 = Library(path)  # second boot is a no-op, not a crash
    lib2.close()


def test_open_fresh_db_also_fine(tmp_path):
    """The fresh-DB path never broke (CREATE TABLE runs in full, including
    token_hash, before the index in the same script) — pinned here so a
    future change can't silently break it while fixing the existing-DB case."""
    path = str(tmp_path / "fresh.db")
    lib = Library(path)
    try:
        cols = {r[1] for r in lib.conn.execute("PRAGMA table_info(password_reset_requests)").fetchall()}
        assert {"token_hash", "expires_at"} <= cols
        indexes = {r[1] for r in lib.conn.execute("PRAGMA index_list(password_reset_requests)").fetchall()}
        assert "idx_pwreset_token" in indexes
    finally:
        lib.close()
