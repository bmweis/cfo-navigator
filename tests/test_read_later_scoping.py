"""Save-for-later must be a private, per-user bookmark list — never a single
shared list. read_later predates per-user scoping (it shipped as
UNIQUE(url), no owner column, back when only one admin used it); these tests
pin (a) the Library-level contract that every read/write is scoped to
user_id, and (b) the table-recreation migration that backfills a
pre-existing DB's legacy rows without losing data.
"""
import pathlib
import sqlite3
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Library


@pytest.fixture
def lib(tmp_path):
    db = Library(str(tmp_path / "test.db"))
    try:
        yield db
    finally:
        db.close()


def test_read_later_is_scoped_per_user(lib):
    u1 = lib.create_user("alice", "supersecret", role="user")
    u2 = lib.create_user("bob", "supersecret", role="user")

    lib.add_read_later(u1, "https://ex.com/a", title="A")
    lib.add_read_later(u2, "https://ex.com/b", title="B")

    assert [r["url"] for r in lib.list_read_later(u1)] == ["https://ex.com/a"]
    assert [r["url"] for r in lib.list_read_later(u2)] == ["https://ex.com/b"]
    assert lib.read_later_urls(u1) == {"https://ex.com/a"}
    assert lib.read_later_urls(u2) == {"https://ex.com/b"}


def test_two_users_can_independently_save_the_same_url(lib):
    """The old UNIQUE(url) constraint made this impossible — a second user
    saving a URL already saved by someone else would silently overwrite (or
    conflict with) the first user's row. Uniqueness is now per (user_id, url)."""
    u1 = lib.create_user("alice", "supersecret", role="user")
    u2 = lib.create_user("bob", "supersecret", role="user")

    lib.add_read_later(u1, "https://ex.com/shared")
    lib.add_read_later(u2, "https://ex.com/shared")

    assert lib.read_later_urls(u1) == {"https://ex.com/shared"}
    assert lib.read_later_urls(u2) == {"https://ex.com/shared"}


def test_removing_one_users_save_does_not_affect_the_other(lib):
    u1 = lib.create_user("alice", "supersecret", role="user")
    u2 = lib.create_user("bob", "supersecret", role="user")
    lib.add_read_later(u1, "https://ex.com/shared")
    lib.add_read_later(u2, "https://ex.com/shared")

    lib.remove_read_later(u1, "https://ex.com/shared")

    assert lib.read_later_urls(u1) == set()
    assert lib.read_later_urls(u2) == {"https://ex.com/shared"}


def test_re_adding_updates_only_that_users_row(lib):
    u1 = lib.create_user("alice", "supersecret", role="user")
    lib.add_read_later(u1, "https://ex.com/a", title="Old title")
    lib.add_read_later(u1, "https://ex.com/a", title="New title")
    rows = lib.list_read_later(u1)
    assert len(rows) == 1
    assert rows[0]["title"] == "New title"


def test_migration_backfills_legacy_rows_to_the_admin_account(tmp_path):
    """Simulate a pre-existing DB with the old shared-list schema (no
    user_id, UNIQUE(url)) and confirm opening it with Library() migrates the
    table and attributes existing rows to the (sole, pre-existing) admin
    account rather than losing them."""
    path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE read_later (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            url         TEXT NOT NULL UNIQUE,
            title       TEXT NOT NULL DEFAULT '',
            source      TEXT NOT NULL DEFAULT '',
            summary     TEXT NOT NULL DEFAULT '',
            published_at TEXT,
            added_at    TEXT NOT NULL
        )
    """)
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, role TEXT)")
    conn.execute("INSERT INTO users (id, role) VALUES (1, 'admin')")
    conn.execute(
        "INSERT INTO read_later (url, title, added_at) VALUES ('https://ex.com/legacy', 'Legacy', '2026-01-01T00:00:00+00:00')"
    )
    conn.commit()
    conn.close()

    lib = Library(path)   # must not raise
    try:
        cols = {r[1] for r in lib.conn.execute("PRAGMA table_info(read_later)").fetchall()}
        assert "user_id" in cols
        rows = lib.conn.execute("SELECT user_id, url FROM read_later").fetchall()
        assert [tuple(r) for r in rows] == [(1, "https://ex.com/legacy")]
        assert lib.read_later_urls(1) == {"https://ex.com/legacy"}
        indexes = {r[1] for r in lib.conn.execute("PRAGMA index_list(read_later)").fetchall()}
        assert "idx_read_later_user_url" in indexes
    finally:
        lib.close()

    # Re-opening (second boot) against the now-migrated DB must be a no-op, not a crash.
    lib2 = Library(path)
    lib2.close()


def test_migration_is_noop_on_fresh_db(tmp_path):
    path = str(tmp_path / "fresh.db")
    lib = Library(path)
    try:
        cols = {r[1] for r in lib.conn.execute("PRAGMA table_info(read_later)").fetchall()}
        assert "user_id" in cols
        indexes = {r[1] for r in lib.conn.execute("PRAGMA index_list(read_later)").fetchall()}
        assert "idx_read_later_user_url" in indexes
    finally:
        lib.close()
