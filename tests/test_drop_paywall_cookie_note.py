"""Dropping the retired `paywall_cookie_note` column.

The column shipped dormant in #341 so `seed_paywall_cookie_flags` had a source
to convert from. This removes it once that conversion is provably done.

The ordering hazard the drop has to survive: a database that has NOT yet run
the conversion still needs the column. Rather than rely on someone checking
production first, the drop is gated on the `paywall_cookie_flags_seeded`
settings flag, which is set only after a successful conversion pass. These
tests pin both directions of that gate, because getting it wrong destroys data
on exactly the databases nobody looked at.
"""
import pathlib
import sqlite3
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Library

REPO_OPML = str(pathlib.Path(__file__).resolve().parents[1] / "preferred_sites.opml")
LEGACY_NOTE = "Cookie auth via LINKLIB_AUTH_COOKIES"


def _columns(conn):
    return [r[1] for r in conn.execute("PRAGMA table_info(feeds)").fetchall()]


def _legacy_db(path, *, seeded_flag, notes=True):
    """A database as it looked BEFORE this migration: the retired column
    present, optionally carrying real notes, and the conversion flag in
    whichever state we want to test.

    Built by opening a Library (which no longer creates the column), then
    re-adding it by hand — the same shape an existing production database has.
    """
    lib = Library(str(path))
    lib.seed_feeds_from_opml(REPO_OPML)
    try:
        lib.conn.execute("ALTER TABLE feeds ADD COLUMN paywall_cookie_note TEXT NOT NULL DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    if notes:
        for dom in ("mostlymetrics.com", "stratechery.com", "blog.publiccomps.com"):
            lib.conn.execute(
                "UPDATE feeds SET paywall_cookie_note=? WHERE xml_url LIKE ?",
                (LEGACY_NOTE, f"%{dom}%"))
    lib.conn.commit()
    if seeded_flag:
        lib.set_setting("paywall_cookie_flags_seeded", "1")
    lib.close()


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

def test_column_is_dropped_once_the_conversion_has_run(tmp_path):
    db = tmp_path / "converted.db"
    _legacy_db(db, seeded_flag=True)

    lib = Library(str(db))
    try:
        assert "paywall_cookie_note" not in _columns(lib.conn)
    finally:
        lib.close()


def test_column_survives_when_the_conversion_has_not_run(tmp_path):
    """The case that would destroy data if the gate were missing: a database
    that never converted still needs its source column."""
    db = tmp_path / "unconverted.db"
    _legacy_db(db, seeded_flag=False)

    lib = Library(str(db))
    try:
        assert "paywall_cookie_note" in _columns(lib.conn)
        notes = [r[0] for r in lib.conn.execute(
            "SELECT paywall_cookie_note FROM feeds WHERE paywall_cookie_note!=''")]
        assert len(notes) == 3, "the notes must still be readable"
    finally:
        lib.close()


def test_an_unconverted_db_converts_then_drops_on_the_next_boot(tmp_path):
    """Self-healing: no manual sequencing needed, even for a database restored
    from a backup taken before the boolean existed."""
    db = tmp_path / "selfheal.db"
    _legacy_db(db, seeded_flag=False)

    # Boot 1: column kept, conversion runs.
    lib = Library(str(db))
    try:
        assert "paywall_cookie_note" in _columns(lib.conn)
        result = lib.seed_paywall_cookie_flags()
        assert result["seeded"] is True
        assert result["feeds"] == 3
    finally:
        lib.close()

    # Boot 2: conversion is done, so the column goes.
    lib = Library(str(db))
    try:
        assert "paywall_cookie_note" not in _columns(lib.conn)
        flagged = [f for f in lib.list_feeds() if f["has_paywall_cookie"]]
        assert len(flagged) == 3, "the converted values must survive the drop"
    finally:
        lib.close()


# ---------------------------------------------------------------------------
# Data integrity across the drop
# ---------------------------------------------------------------------------

def test_the_drop_preserves_every_row_and_column_value(tmp_path):
    """Against a database seeded from the real OPML, not a toy fixture."""
    db = tmp_path / "real.db"
    _legacy_db(db, seeded_flag=False)

    lib = Library(str(db))
    try:
        lib.seed_paywall_cookie_flags()
        before = {f["xml_url"]: dict(f) for f in lib.list_feeds()}
    finally:
        lib.close()

    lib = Library(str(db))          # boot that performs the drop
    try:
        after = {f["xml_url"]: dict(f) for f in lib.list_feeds()}
    finally:
        lib.close()

    assert len(after) == len(before) == 22
    assert set(after) == set(before)
    for url, row in after.items():
        for field in ("id", "section_id", "name", "xml_url", "html_url",
                      "exclude_from_queue", "display_order", "created_at",
                      "has_active_subscription", "has_paywall_cookie"):
            assert row[field] == before[url][field], f"{field} changed for {url}"


def test_tokenized_urls_survive_the_drop_verbatim(tmp_path):
    """The verbatim-URL guarantee has to hold through a schema migration too."""
    db = tmp_path / "token.db"
    tokenized = "https://paid.example/feed?token=AbC-123_xyz&u=9"
    _legacy_db(db, seeded_flag=False)

    lib = Library(str(db))
    try:
        sid = lib.list_feed_sections()[0]["id"]
        lib.add_feed(sid, "Paid", tokenized, "https://paid.example/")
        lib.seed_paywall_cookie_flags()
    finally:
        lib.close()

    lib = Library(str(db))
    try:
        assert "paywall_cookie_note" not in _columns(lib.conn)
        assert lib.find_feed_by_url(tokenized)["xml_url"] == tokenized
    finally:
        lib.close()


def test_writes_still_work_after_the_drop(tmp_path):
    """A rebuilt or altered table must still accept the normal write paths."""
    db = tmp_path / "writes.db"
    _legacy_db(db, seeded_flag=True)

    lib = Library(str(db))
    try:
        sid = lib.list_feed_sections()[0]["id"]
        fid = lib.add_feed(sid, "New", "https://new.example/feed",
                           has_paywall_cookie=True)
        assert lib.get_feed(fid)["has_paywall_cookie"] == 1
        lib.set_feed_paywall_cookie(fid, False)
        assert lib.get_feed(fid)["has_paywall_cookie"] == 0
        lib.update_feed(fid, sid, "Renamed", "https://new.example/feed", "",
                        has_paywall_cookie=True)
        assert lib.get_feed(fid)["name"] == "Renamed"
        lib.delete_feed(fid)
        assert lib.find_feed_by_url("https://new.example/feed") is None
    finally:
        lib.close()


def test_opml_still_regenerates_after_the_drop(tmp_path):
    db = tmp_path / "opml.db"
    _legacy_db(db, seeded_flag=True)

    lib = Library(str(db))
    try:
        xml = lib.opml_xml()
        assert "paywall_cookie_note" not in xml
        out = tmp_path / "out.opml"
        lib.write_opml(str(out))
        from linklib.feed import parse_opml
        assert len(parse_opml(str(out))) == 22
    finally:
        lib.close()


# ---------------------------------------------------------------------------
# Idempotence and the pre-3.35 fallback
# ---------------------------------------------------------------------------

def test_repeated_boots_do_not_re_add_the_column(tmp_path):
    """The ADD line was removed from the migration list for exactly this
    reason: with it still there, every boot would re-add what the drop just
    removed."""
    db = tmp_path / "idempotent.db"
    _legacy_db(db, seeded_flag=True)
    for _ in range(3):
        lib = Library(str(db))
        try:
            assert "paywall_cookie_note" not in _columns(lib.conn)
        finally:
            lib.close()


def test_a_fresh_database_never_has_the_column(tmp_path):
    lib = Library(str(tmp_path / "fresh.db"))
    try:
        assert "paywall_cookie_note" not in _columns(lib.conn)
        assert "has_paywall_cookie" in _columns(lib.conn)
    finally:
        lib.close()


def test_the_rebuild_fallback_produces_the_same_result(tmp_path):
    """Exercises the pre-3.35 path directly, since this SQLite supports
    DROP COLUMN and would never reach it otherwise."""
    db = tmp_path / "rebuild.db"
    _legacy_db(db, seeded_flag=False)

    lib = Library(str(db))
    try:
        lib.seed_paywall_cookie_flags()
        before = {f["xml_url"]: dict(f) for f in lib.list_feeds()}
        # Put the column back so there is something for the fallback to remove.
        if "paywall_cookie_note" not in _columns(lib.conn):
            lib.conn.execute(
                "ALTER TABLE feeds ADD COLUMN paywall_cookie_note TEXT NOT NULL DEFAULT ''")
            lib.conn.commit()

        lib._rebuild_feeds_without_cookie_note()

        assert "paywall_cookie_note" not in _columns(lib.conn)
        after = {f["xml_url"]: dict(f) for f in lib.list_feeds()}
        assert set(after) == set(before)
        for url, row in after.items():
            for field in ("id", "name", "xml_url", "html_url", "section_id",
                          "exclude_from_queue", "has_paywall_cookie",
                          "has_active_subscription", "display_order"):
                assert row[field] == before[url][field], f"{field} changed for {url}"
    finally:
        lib.close()


def test_the_rebuild_fallback_keeps_the_unique_constraint(tmp_path):
    """A rebuild that lost UNIQUE(xml_url) would let the same feed be
    subscribed twice — the natural key this whole table rests on."""
    db = tmp_path / "unique.db"
    _legacy_db(db, seeded_flag=False)

    lib = Library(str(db))
    try:
        lib.seed_paywall_cookie_flags()
        lib._rebuild_feeds_without_cookie_note()
        existing = lib.list_feeds()[0]
        with pytest.raises(sqlite3.IntegrityError):
            lib.conn.execute(
                "INSERT INTO feeds (section_id, name, xml_url) VALUES (?,?,?)",
                (existing["section_id"], "Dupe", existing["xml_url"]))
    finally:
        lib.close()
