"""scripts/fix_invisible_characters.py — the retroactive backfill for
content written before `normalize_voice_mechanics`'s invisible-character
strip existed (2026-09, PR #590 Phase 2 verification — closes the E2 gap
flagged in Phase 1's report: the write-time backstop prevents recurrence
but never touched already-stored data, so the production incident this
whole feature was built around — category_features id 104's trailing
zero-width space — was never actually fixed by the original PR).

Mirrors tests/test_fix_spaced_em_dashes.py's own shape: preview vs.
--apply parity, idempotency, write-then-read-back correctness, and that
this narrow raw-SQL script never touches a side-effect column
(`agent_taxonomy_needs_verification`, `entity_citations`) the way the
higher-level `Library.update_tool_agent_taxonomy` would."""
import os
import subprocess
import sys
import tempfile

import pytest

from linklib.db import Library

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ZWS = "​"      # zero-width space
BOM = "﻿"      # zero-width no-break space / BOM
WJ = "⁠"       # word joiner
ZWJ = "‍"       # zero-width joiner (flag-only, never auto-stripped)


@pytest.fixture
def db_path():
    path = tempfile.mktemp(suffix=".db")
    yield path
    if os.path.exists(path):
        os.remove(path)


def _run(db_path: str, *, apply: bool = False) -> str:
    args = [sys.executable, "-m", "scripts.fix_invisible_characters", "--db", db_path]
    if apply:
        args.append("--apply")
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"
    return result.stdout


def _seed_dirty_db(db_path: str) -> tuple[int, int]:
    lib = Library(db_path)
    tool_id = lib.add_tool("Acme", "clean desc", "https://acme.example", [], approved=1)
    lib.conn.execute(
        "UPDATE tools SET agent_taxonomy_note=?, agent_taxonomy_needs_verification=1 WHERE id=?",
        (f"Acme flags anomalies{ZWS} reviewed by a human.", tool_id),
    )
    cat_id = lib.add_tool_category("Finance")
    feature_id = lib.add_category_feature(cat_id, "Anomaly detection", "placeholder")
    # The real production incident's exact shape: a trailing zero-width
    # space nothing before the write-time backstop could ever have caught.
    lib.conn.execute(
        "UPDATE category_features SET definition=? WHERE id=?",
        (f"Identifies unusual or erroneous items and patterns{ZWS}", feature_id),
    )
    lib.conn.commit()
    lib.close()
    return tool_id, feature_id


def test_preview_makes_no_changes(db_path):
    tool_id, feature_id = _seed_dirty_db(db_path)
    out = _run(db_path, apply=False)
    assert "Would fix: 2 field(s)" in out
    lib = Library(db_path)
    assert lib.get_tool(tool_id)["agent_taxonomy_note"] == f"Acme flags anomalies{ZWS} reviewed by a human."
    row = lib.conn.execute("SELECT definition FROM category_features WHERE id=?", (feature_id,)).fetchone()
    assert row["definition"].endswith(ZWS)
    lib.close()


def test_apply_fixes_and_read_back_matches(db_path):
    tool_id, feature_id = _seed_dirty_db(db_path)
    out = _run(db_path, apply=True)
    assert "Fixed: 2 field(s)" in out
    lib = Library(db_path)
    assert lib.get_tool(tool_id)["agent_taxonomy_note"] == "Acme flags anomalies reviewed by a human."
    row = lib.conn.execute("SELECT definition FROM category_features WHERE id=?", (feature_id,)).fetchone()
    assert ZWS not in row["definition"]
    assert row["definition"] == "Identifies unusual or erroneous items and patterns"
    lib.close()


def test_apply_is_idempotent(db_path):
    _seed_dirty_db(db_path)
    _run(db_path, apply=True)
    out2 = _run(db_path, apply=True)
    assert "Fixed: 0 field(s)" in out2


def test_apply_does_not_clear_needs_verification_or_citations(db_path):
    tool_id, _feature_id = _seed_dirty_db(db_path)
    lib = Library(db_path)
    lib.set_entity_citations("tool", tool_id, "agent_taxonomy",
                              [{"n": 1, "url": "https://acme.example", "title": "Acme"}])
    lib.close()

    _run(db_path, apply=True)

    lib = Library(db_path)
    tool = lib.get_tool(tool_id)
    assert tool["agent_taxonomy_needs_verification"] == 1, "needs_verification must survive a text-only fix"
    citations = lib.get_entity_citations("tool", tool_id, "agent_taxonomy")
    assert citations, "entity_citations must survive a text-only fix"
    lib.close()


def test_flag_only_characters_are_never_touched(db_path):
    """The zero-width joiner (load-bearing in real emoji) and other
    flag-only characters must never be silently stripped by this script —
    only the three characters normalize_voice_mechanics already treats as
    auto-strip-safe (ZWS, BOM, word joiner)."""
    lib = Library(db_path)
    tool_id = lib.add_tool("Acme2", "clean desc", "https://acme2.example", [], approved=1)
    lib.conn.execute(
        "UPDATE tools SET description=? WHERE id=?",
        (f"Team spirit{ZWJ} finance folks.", tool_id),
    )
    lib.conn.commit()
    lib.close()

    out = _run(db_path, apply=True)
    assert "Fixed: 0 field(s)" in out

    lib = Library(db_path)
    assert lib.get_tool(tool_id)["description"] == f"Team spirit{ZWJ} finance folks."
    lib.close()


def test_apply_logs_every_change_to_voice_review_queue(db_path):
    """Brian's rule: nothing changes quietly. --apply must leave the same
    kind of trace a live save through Library._vf would have — an
    auto_corrected voice_review_queue row, source='script', one per
    (table, row_id, column) actually fixed. Covers both real tables and the
    settings convention (row_id=None, key as the column)."""
    tool_id, feature_id = _seed_dirty_db(db_path)
    lib = Library(db_path)
    # Raw insert, deliberately bypassing set_setting()'s own _vf call (which
    # would auto-fix and log this at seed time) — the point is seeding a
    # value the SCRIPT itself has to find and fix, same as _seed_dirty_db's
    # own raw UPDATEs for the tool/feature rows above.
    lib.conn.execute(
        "INSERT INTO settings (key, value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        ("homepage_headline_copy", f"Building things{ZWS} that matter."),
    )
    lib.conn.commit()
    lib.close()

    _run(db_path, apply=True)

    lib = Library(db_path)
    rows = lib.list_voice_review_queue()
    assert len(rows) == 3, f"expected 3 logged corrections, got {len(rows)}: {rows}"

    by_location = {(r["table_name"], r["row_id"], r["column_name"]): r for r in rows}

    tool_row = by_location[("tools", str(tool_id), "agent_taxonomy_note")]
    assert tool_row["status"] == "auto_corrected"
    assert tool_row["source"] == "script"
    assert tool_row["rule"] == "invisible-character"
    assert tool_row["before_text"] == f"Acme flags anomalies{ZWS} reviewed by a human."
    assert tool_row["after_text"] == "Acme flags anomalies reviewed by a human."

    feature_row = by_location[("category_features", str(feature_id), "definition")]
    assert feature_row["status"] == "auto_corrected"
    assert feature_row["source"] == "script"

    # Settings convention: row_id is None (not the string "None"), the
    # settings key is what's stored in column_name — matches
    # Library.set_setting's own _vf call exactly.
    settings_row = by_location[("settings", None, "homepage_headline_copy")]
    assert settings_row["status"] == "auto_corrected"
    assert settings_row["source"] == "script"
    assert settings_row["after_text"] == "Building things that matter."
    lib.close()


def test_preview_never_writes_to_voice_review_queue(db_path):
    """The no-writes-in-preview-mode contract covers the queue log too, not
    just the content columns."""
    _seed_dirty_db(db_path)
    _run(db_path, apply=False)
    lib = Library(db_path)
    assert lib.list_voice_review_queue() == []
    lib.close()


def test_missing_db_exits_nonzero_and_never_creates_a_file():
    """resolve_db_path(allow_missing=False) already refuses to let sqlite3
    silently create an empty file at a path that doesn't exist — confirm
    that guard actually holds for this script specifically (nonzero exit,
    clear stderr message, no file left behind), rather than trusting it by
    inference from the shared helper alone."""
    missing_path = tempfile.mktemp(suffix=".db")
    assert not os.path.exists(missing_path)
    try:
        args = [sys.executable, "-m", "scripts.fix_invisible_characters", "--db", missing_path]
        result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=30)
        assert result.returncode != 0, (
            f"expected a nonzero exit for a missing DB file, got 0\nstdout: {result.stdout}"
        )
        assert "not found" in result.stderr.lower() or "not found" in result.stdout.lower()
        assert not os.path.exists(missing_path), (
            "script must never let sqlite3 silently create an empty DB file at a missing path"
        )
    finally:
        if os.path.exists(missing_path):
            os.remove(missing_path)


def test_this_fix_closes_the_real_production_incident_shape(db_path):
    """The exact shape this whole item exists for: a category_features
    definition ending in a trailing zero-width space, closed via a real
    subprocess run of this script, not just the underlying library call."""
    lib = Library(db_path)
    cat_id = lib.add_tool_category("Finance")
    feature_id = lib.add_category_feature(cat_id, "Anomaly detection", "placeholder")
    lib.conn.execute(
        "UPDATE category_features SET definition=? WHERE id=?",
        (f"Identifies unusual or erroneous items and patterns that don't look right{ZWS}", feature_id),
    )
    lib.conn.commit()
    lib.close()

    _run(db_path, apply=True)

    lib = Library(db_path)
    row = lib.conn.execute("SELECT definition FROM category_features WHERE id=?", (feature_id,)).fetchone()
    assert row["definition"] == "Identifies unusual or erroneous items and patterns that don't look right"
    lib.close()
