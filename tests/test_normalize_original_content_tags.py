"""scripts/normalize_original_content_tags.py must never retag a row that
already carries a valid tag. The first version mapped chart-of-accounts to
Playbook unconditionally, so a run after Brian set it to Guide by hand would
have overwritten his editorial choice."""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Library  # noqa: E402
from scripts import normalize_original_content_tags as norm  # noqa: E402


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "t.db")
    lib = Library(path)
    try:
        # Production state on 2026-09-23: all four valid, chart-of-accounts
        # hand-set to Guide.
        for slug, tag, link in [
            ("growth-engine-ratio", "Framework", "Read the framework"),
            ("ai-hackathon-playbook", "Playbook", "Read the playbook"),
            ("netsuite-mcp", "Guide", "Read the guide"),
            ("chart-of-accounts", "Guide", "Read the guide"),
        ]:
            lib.add_original_content(slug, slug.title(), "t", tag, link, status="live")
    finally:
        lib.close()
    return path


def _tags(path):
    lib = Library(path)
    try:
        return {r["slug"]: (r["tag_label"], r["link_label"]) for r in lib.list_original_content()}
    finally:
        lib.close()


def test_hand_set_guide_survives_apply(db):
    before = _tags(db)
    assert norm.main(["--db", db, "--apply"]) == 0
    assert _tags(db) == before
    assert _tags(db)["chart-of-accounts"] == ("Guide", "Read the guide")


def test_legacy_tag_is_still_normalized(db):
    lib = Library(db)
    try:
        row = next(r for r in lib.list_original_content() if r["slug"] == "netsuite-mcp")
        lib.conn.execute("UPDATE original_content SET tag_label='Setup Guide', link_label='Read the setup guide' "
                         "WHERE id=?", (row["id"],))
        lib.conn.commit()
    finally:
        lib.close()
    assert norm.main(["--db", db, "--apply"]) == 0
    assert _tags(db)["netsuite-mcp"] == ("Guide", "Read the guide")
    assert _tags(db)["chart-of-accounts"] == ("Guide", "Read the guide")


def test_valid_tag_with_stale_link_label_only_fixes_the_link(db):
    lib = Library(db)
    try:
        lib.conn.execute("UPDATE original_content SET link_label='Read the playbook' WHERE slug='chart-of-accounts'")
        lib.conn.commit()
    finally:
        lib.close()
    assert norm.main(["--db", db, "--apply"]) == 0
    assert _tags(db)["chart-of-accounts"] == ("Guide", "Read the guide")


def test_unknown_legacy_slug_stops_without_writing(db):
    lib = Library(db)
    try:
        lib.add_original_content("new-piece", "New", "t", "Essay", "Read it", status="draft")
    finally:
        lib.close()
    before = _tags(db)
    assert norm.main(["--db", db, "--apply"]) == 1
    assert _tags(db) == before


def test_preview_writes_nothing(db):
    lib = Library(db)
    try:
        lib.conn.execute("UPDATE original_content SET tag_label='Setup Guide' WHERE slug='netsuite-mcp'")
        lib.conn.commit()
    finally:
        lib.close()
    before = _tags(db)
    assert norm.main(["--db", db]) == 0
    assert _tags(db) == before


def test_script_tag_map_matches_the_app():
    import webapp.app as appmod
    assert norm._TAG_LINK == {k: v["link_label"] for k, v in appmod._OC_TAG_INFO.items()}
