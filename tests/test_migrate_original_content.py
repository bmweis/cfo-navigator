"""scripts/archive/migrate_original_content.py (Original Content Phase 1) —
the one-time migration that seeds the `original_content` table from
_TL_FEATURED_CARDS, plus the Library CRUD methods it and the eventual admin
CRUD (Phase 3) both rely on.
"""
import os
import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Library
from scripts.archive import migrate_original_content as script
from webapp.app import _TL_FEATURED_CARDS


@pytest.fixture
def db():
    path = tempfile.mktemp(suffix=".db")
    yield path
    if os.path.exists(path):
        os.remove(path)


def test_planned_rows_match_tl_featured_cards():
    planned = script.planned_rows()
    assert len(planned) == len(_TL_FEATURED_CARDS) == 3
    for i, ((href, tag, _color, title, desc, cta), row) in enumerate(zip(_TL_FEATURED_CARDS, planned)):
        assert row["slug"] == href.rsplit("/", 1)[-1]
        assert row["title"] == title
        assert row["teaser"] == desc
        assert row["tag_label"] == tag
        assert row["link_label"] == cta
        assert row["body_md"] is None
        assert row["status"] == "live"
        assert row["featured_home"] is True
        assert row["display_order"] == i


def test_dry_run_writes_nothing(db, monkeypatch, capsys):
    Library(db).close()  # create the file first — the script refuses a missing --db path
    monkeypatch.setattr(sys, "argv", ["migrate_original_content", "--db", db])
    script.main()
    out = capsys.readouterr().out
    assert "PREVIEW ONLY" in out
    lib = Library(db)
    try:
        assert lib.list_original_content() == []
    finally:
        lib.close()


def test_apply_seeds_all_three_pieces_losslessly(db, monkeypatch, capsys):
    Library(db).close()
    monkeypatch.setattr(sys, "argv", ["migrate_original_content", "--db", db, "--apply"])
    script.main()
    out = capsys.readouterr().out
    assert "Applied" in out
    assert "Verified" in out

    lib = Library(db)
    try:
        rows = lib.list_original_content()
        assert len(rows) == 3
        by_slug = {r["slug"]: r for r in rows}
        assert set(by_slug) == {"growth-engine-ratio", "ai-hackathon-playbook", "netsuite-mcp"}
        for r in rows:
            assert r["status"] == "live"
            assert r["featured_home"] == 1
            assert r["body_md"] is None
        assert by_slug["growth-engine-ratio"]["title"] == "The Growth Engine Ratio"
        assert by_slug["growth-engine-ratio"]["tag_label"] == "Framework"
        assert by_slug["growth-engine-ratio"]["link_label"] == "Read the framework"
    finally:
        lib.close()


def test_apply_is_idempotent_against_a_seeded_table(db, monkeypatch, capsys):
    Library(db).close()
    monkeypatch.setattr(sys, "argv", ["migrate_original_content", "--db", db, "--apply"])
    script.main()
    capsys.readouterr()
    script.main()
    out = capsys.readouterr().out
    assert "nothing to do" in out

    lib = Library(db)
    try:
        assert len(lib.list_original_content()) == 3
    finally:
        lib.close()


# -- Library CRUD (original_content) -----------------------------------------

def test_add_get_update_delete_original_content(db):
    lib = Library(db)
    try:
        item_id = lib.add_original_content(
            "a-new-piece", "A New Piece", teaser="Teaser text", tag_label="Guide",
            link_label="Read it", body_md="# Hello\n\nSome body.", status="draft",
            featured_home=False, date_label="Jan 2027", sort_key="2027-01",
        )
        row = lib.get_original_content(item_id)
        assert row["slug"] == "a-new-piece"
        assert row["body_md"] == "# Hello\n\nSome body."
        assert row["status"] == "draft"
        assert row["featured_home"] == 0

        by_slug = lib.get_original_content_by_slug("a-new-piece")
        assert by_slug["id"] == item_id

        lib.update_original_content(
            item_id, "a-new-piece", "A New Piece (edited)", "New teaser", "Guide",
            "Read it now", "# Hello\n\nEdited body.", "live", True, "Jan 2027", "2027-01", 0,
        )
        updated = lib.get_original_content(item_id)
        assert updated["title"] == "A New Piece (edited)"
        assert updated["status"] == "live"
        assert updated["featured_home"] == 1

        lib.delete_original_content(item_id)
        assert lib.get_original_content(item_id) is None
    finally:
        lib.close()


def test_list_original_content_for_home_excludes_non_featured_and_draft(db):
    lib = Library(db)
    try:
        lib.add_original_content("live-featured", "Live Featured", status="live",
                                 featured_home=True, display_order=0)
        lib.add_original_content("live-not-featured", "Live Not Featured", status="live",
                                 featured_home=False, display_order=1)
        lib.add_original_content("draft-featured", "Draft Featured", status="draft",
                                 featured_home=True, display_order=2)

        home = lib.list_original_content_for_home()
        assert [r["slug"] for r in home] == ["live-featured"]

        live = lib.list_original_content(status="live")
        assert {r["slug"] for r in live} == {"live-featured", "live-not-featured"}
    finally:
        lib.close()


def test_list_original_content_orders_by_display_order_then_sort_key(db):
    lib = Library(db)
    try:
        lib.add_original_content("second", "Second", status="live", display_order=1, sort_key="2026-01")
        lib.add_original_content("first", "First", status="live", display_order=0, sort_key="2026-06")
        lib.add_original_content("third-tiebreak-a", "Third A", status="live", display_order=2, sort_key="2026-06")
        lib.add_original_content("third-tiebreak-b", "Third B", status="live", display_order=2, sort_key="2026-01")

        rows = lib.list_original_content()
        assert [r["slug"] for r in rows] == ["first", "second", "third-tiebreak-a", "third-tiebreak-b"]
    finally:
        lib.close()
