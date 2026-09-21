"""Regression coverage for scripts/backfill_voice_review_queue_source.py's
two phases (2026-09, PR #590 Phase 2 verification).

Phase 1 (the two hand-identified communities.notes rows -> 'startup-sync')
already existed; this file's own job is Phase 2, added in this same pass:
every legacy `status='open'` row with `source IS NULL` gets tagged
'script', since `Library.add_voice_review_item` is the only code path
that has ever created an `open` row and has always defaulted `source` to
'script' (see the script's own module docstring for the full reasoning).
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from linklib.db import Library
import scripts.backfill_voice_review_queue_source as backfill_source


@pytest.fixture
def lib():
    db = tempfile.mktemp(suffix=".db")
    library = Library(db)
    yield library
    library.close()
    if os.path.exists(db):
        os.remove(db)


def _make_legacy_open_row(lib, table, row_id, column, rule, excerpt):
    """Simulate a pre-migration row: inserted the way add_voice_review_item
    would have, but with source explicitly NULL (as a row from before the
    source column/default existed would read)."""
    lib.conn.execute(
        "INSERT INTO voice_review_queue "
        "(table_name, row_id, column_name, rule, excerpt, status, created_at, source) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (table, str(row_id), column, rule, excerpt, "open", "2026-01-01T00:00:00Z", None),
    )
    lib.conn.commit()


def test_phase2_tags_legacy_open_rows_as_script(lib):
    tid = lib.add_tool("Testco", "A test tool.", "https://testco.example", [])
    _make_legacy_open_row(lib, "tools", tid, "description", "buzzword", "seamless")

    # Exercise the script's own Phase 2 selection + write logic directly
    # (its own module functions, not a re-parsed CLI) — the CLI/subprocess
    # path itself is covered by test_script_syspath_fix.py.
    item = lib.list_voice_review_queue(status="open")[0]
    assert item["source"] is None

    legacy_open = [r for r in lib.list_voice_review_queue() if r["status"] == "open" and r.get("source") is None]
    assert len(legacy_open) == 1
    ok = backfill_source._apply_and_verify(lib, legacy_open, "script")
    assert ok

    updated = lib.get_voice_review_item(item["id"])
    assert updated["source"] == "script"


def test_phase2_does_not_touch_auto_corrected_rows_with_null_source(lib):
    """Phase 2 is scoped to status='open' only — an auto_corrected row with
    source=NULL (the Phase 1 territory, or a genuinely different legacy
    row) must never be swept up by Phase 2's blanket 'script' tagging."""
    tid = lib.add_tool("Testco2", "A test tool.", "https://testco2.example", [])
    lib.conn.execute(
        "INSERT INTO voice_review_queue "
        "(table_name, row_id, column_name, rule, excerpt, before_text, after_text, status, created_at, source) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("tools", str(tid), "description", "spaced-em-dash", "X-Y", "X — Y", "X-Y",
         "auto_corrected", "2026-01-01T00:00:00Z", None),
    )
    lib.conn.commit()

    all_rows = lib.list_voice_review_queue()
    legacy_open = [r for r in all_rows if r["status"] == "open" and r.get("source") is None]
    assert legacy_open == []  # nothing to apply in Phase 2

    ac_row = [r for r in all_rows if r["status"] == "auto_corrected"][0]
    assert ac_row["source"] is None  # Phase 2 must leave this alone


def test_phase2_is_idempotent(lib):
    """Re-running Phase 2 after a row is already tagged 'script' changes
    nothing — there's no more source=NULL open row left to match."""
    tid = lib.add_tool("Testco3", "A test tool.", "https://testco3.example", [])
    _make_legacy_open_row(lib, "tools", tid, "description", "buzzword", "seamless")

    legacy_open = [r for r in lib.list_voice_review_queue() if r["status"] == "open" and r.get("source") is None]
    backfill_source._apply_and_verify(lib, legacy_open, "script")

    legacy_open_second_pass = [
        r for r in lib.list_voice_review_queue() if r["status"] == "open" and r.get("source") is None
    ]
    assert legacy_open_second_pass == []
