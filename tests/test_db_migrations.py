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
