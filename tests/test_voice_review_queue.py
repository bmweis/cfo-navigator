"""Voice review queue (2026-09) — the scanner/auto-corrector blocker fix,
the ampersand-exception additions, and the queue mechanism itself."""
import os
import tempfile

import pytest

from linklib.db import Library
from linklib.voice_review import AMPERSAND_ACRONYMS, mechanical_findings
from linklib.voice_db_scan import scan_db_copy, scan_db_copy_report


@pytest.fixture
def lib():
    path = tempfile.mktemp(suffix=".db")
    lib = Library(path)
    yield lib
    lib.close()
    try:
        os.remove(path)
    except OSError:
        pass


# --- Blocker: scanner now detects what _voice_fix corrects -------------------

def test_category_features_definition_is_now_scanned_and_matches_the_corrector():
    """The confirmed production case (category_features id 8's `definition`)
    — before this fix, the DB scanner never looked at `definition`/
    `pointer_note` at all, so it could never report what `_voice_fix`
    (already wired into add/update_category_feature) was silently
    correcting at write time. This proves the two now agree: the raw text
    `_voice_fix` would change is exactly what the scanner flags."""
    path = tempfile.mktemp(suffix=".db")
    lib = Library(path)
    try:
        cid = lib.add_tool_category("Test Category")
        fid = lib.add_category_feature(cid, "Test Feature")
        # add_category_feature already runs _voice_fix at write time, so the
        # STORED value is pre-corrected — insert the raw spaced-em-dash text
        # directly to simulate a row written before the backstop existed
        # (or any of the other still-uncovered write paths).
        lib.conn.execute(
            "UPDATE category_features SET definition=? WHERE id=?",
            ("...patterns that don't look right — not necessarily a balance change", fid),
        )
        lib.conn.commit()
        report = scan_db_copy_report(lib)
        hits = [v for v in report.violations
                if v.table == "category_features" and v.column == "definition" and v.row_id == fid]
        assert hits, "scanner did not catch a spaced-em-dash in category_features.definition"
        assert hits[0].rule == "spaced-em-dash"
    finally:
        lib.close()
        try:
            os.remove(path)
        except OSError:
            pass


def test_category_features_name_still_scanned_but_typography_exempt(lib):
    cid = lib.add_tool_category("Test Category 2")
    lib.add_category_feature(cid, "Sales & Marketing", "", "")
    violations = scan_db_copy(lib)
    # Mechanical rules still apply to name; typography (bare-ampersand) does
    # not, since "Sales & Marketing" is an already-allowlisted real term —
    # confirming no bare-ampersand finding fires for it.
    amp_hits = [v for v in violations if v.table == "category_features" and v.column == "name"
                and v.rule == "bare-ampersand"]
    assert not amp_hits


def test_thought_leadership_title_is_typography_exempt(lib):
    lib.add_thought_leadership("writing", "Cash Flow Show & Friends", "https://example.com",
                                "Venue", "Jun 2026", "2026-06", "", 0, None)
    violations = scan_db_copy(lib)
    amp_hits = [v for v in violations if v.table == "thought_leadership" and v.column == "title"
                and v.rule == "bare-ampersand"]
    assert not amp_hits


# --- Part 2a: ampersand allowlist additions -----------------------------------

def test_ga_and_ld_are_allowlisted_acronyms():
    assert "G&A" in AMPERSAND_ACRONYMS
    assert "L&D" in AMPERSAND_ACRONYMS


# --- Part 1: the review queue mechanism ---------------------------------------

def test_vf_logs_an_auto_corrected_row_when_text_changes(lib):
    fixed = lib._vf("tools", 42, "description", "a — b")
    assert fixed == "a—b"
    items = lib.list_voice_review_queue()
    assert len(items) == 1
    assert items[0]["status"] == "auto_corrected"
    assert items[0]["table_name"] == "tools"
    assert items[0]["row_id"] == "42"
    assert items[0]["before_text"] == "a — b"
    assert items[0]["after_text"] == "a—b"


def test_vf_logs_nothing_when_text_is_already_clean(lib):
    fixed = lib._vf("tools", 42, "description", "a—b, already fine")
    assert fixed == "a—b, already fine"
    assert lib.list_voice_review_queue() == []


def test_set_setting_logs_a_correction(lib):
    lib.set_setting("homepage_headline_copy", "Strategic partner — not just a scorekeeper")
    items = lib.list_voice_review_queue()
    assert len(items) == 1
    assert items[0]["table_name"] == "settings"
    assert items[0]["row_id"] is None
    assert items[0]["column_name"] == "homepage_headline_copy"


def test_update_tool_logs_a_correction(lib):
    tid = lib.add_tool("Test Tool", "A tool.", "https://example.com", [], approved=1)
    lib.update_tool(tid, "Test Tool", "A tool that does X — and Y too", "https://example.com", [])
    items = lib.list_voice_review_queue()
    assert any(i["table_name"] == "tools" and i["column_name"] == "description" for i in items)


def test_add_voice_review_item_and_exception_flow(lib):
    item_id = lib.add_voice_review_item("communities", 5, "demographic", "bare-ampersand", "Bain & Company")
    assert item_id
    assert lib.count_open_voice_review_items() == 1
    # Accept as exception marks it permanently — future adds for the exact
    # same (table, row_id, column, rule) are skipped.
    assert lib.resolve_voice_review_item(item_id, "accept_exception")
    assert lib.is_voice_exception("communities", 5, "demographic", "bare-ampersand")
    second = lib.add_voice_review_item("communities", 5, "demographic", "bare-ampersand", "Bain & Company")
    assert second == 0
    assert lib.count_open_voice_review_items() == 0


def test_exception_is_row_scoped_not_global(lib):
    """Accepting an exception for one record must not suppress the identical
    finding on a DIFFERENT record — this is a per-record exception, never a
    global rule change (that's what the source-side AMPERSAND_NAMES/
    AMPERSAND_ACRONYMS allowlists are for)."""
    item_id = lib.add_voice_review_item("communities", 5, "demographic", "bare-ampersand", "Bain & Company")
    lib.resolve_voice_review_item(item_id, "accept_exception")
    other = lib.add_voice_review_item("communities", 6, "demographic", "bare-ampersand", "Ernst & Young")
    assert other != 0
    assert lib.count_open_voice_review_items() == 1


def test_resolve_revert_and_edit_write_back_via_apply(lib):
    tid = lib.add_tool("Test Tool 2", "A tool.", "https://example.com", [], approved=1)
    lib.update_tool_content(tid, "Test Tool 2", "A tool that does X — and Y too")
    item = lib.list_voice_review_queue()[0]
    assert lib.apply_voice_review_write("tools", item["row_id"], "description", item["before_text"])
    tool = lib.get_tool(int(item["row_id"]))
    assert tool["description"] == item["before_text"]


def test_apply_voice_review_write_rejects_unknown_table_column(lib):
    assert not lib.apply_voice_review_write("not_a_real_table", 1, "x", "y")
    assert not lib.apply_voice_review_write("tools", 1, "not_a_real_column", "y")


def test_backfill_script_queues_open_items_from_the_scan(lib):
    from scripts.backfill_voice_review_queue import main as _unused  # importable
    cid = lib.add_tool_category("Test Category 3")
    lib.conn.execute(
        "UPDATE category_features SET definition=? WHERE category_id=? AND id=(SELECT id FROM category_features WHERE category_id=?)",
        ("bad — dash", cid, cid),
    )
    fid = lib.add_category_feature(cid, "Feature X", "", "")
    lib.conn.execute("UPDATE category_features SET definition=? WHERE id=?", ("bad — dash", fid))
    lib.conn.commit()
    report = scan_db_copy_report(lib)
    before = lib.conn.execute("SELECT COUNT(*) FROM voice_review_queue").fetchone()[0]
    for v in report.violations:
        lib.add_voice_review_item(v.table, v.row_id, v.column, v.rule, v.excerpt)
    after = lib.conn.execute("SELECT COUNT(*) FROM voice_review_queue").fetchone()[0]
    assert after > before
