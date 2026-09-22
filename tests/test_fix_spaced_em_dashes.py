"""scripts/fix_spaced_em_dashes.py — the one-off cleanup for the 2026-08
spaced-em-dash incident. Covers: preview vs. --apply parity, idempotency on
a re-run, write-then-read-back correctness across all three tables
(tools/communities/community_profiles), and — the one real correctness risk
of a narrow raw-SQL cleanup script — that it never touches any column
besides the text it's fixing (in particular `agent_taxonomy_needs_verification`
and `entity_citations`, which `Library.update_tool_agent_taxonomy` would
otherwise clear as a side effect of what it assumes is a human edit)."""
import os
import subprocess
import sys
import tempfile

import pytest

from linklib.db import Library

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def db_path():
    path = tempfile.mktemp(suffix=".db")
    yield path
    if os.path.exists(path):
        os.remove(path)


def _run(db_path: str, *, apply: bool = False) -> str:
    args = [sys.executable, "-m", "scripts.fix_spaced_em_dashes", "--db", db_path]
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
        ("Acme flags anomalies — reviewed by a human.", tool_id),
    )
    cid = lib.add_community("Acme Circle", "https://acme-circle.example", "demo", "Free", [], approved=1)
    lib.upsert_community_profile(cid, ideal_member="great fit")
    lib.conn.execute(
        "UPDATE community_profiles SET anti_fit=? WHERE community_id=?",
        ("too early-stage — not proven yet", cid),
    )
    lib.conn.commit()
    lib.close()
    return tool_id, cid


def test_preview_makes_no_changes(db_path):
    tool_id, cid = _seed_dirty_db(db_path)
    out = _run(db_path, apply=False)
    assert "Would fix: 2 field(s)" in out
    lib = Library(db_path)
    assert lib.get_tool(tool_id)["agent_taxonomy_note"] == "Acme flags anomalies — reviewed by a human."
    assert lib.get_community_profile(cid)["anti_fit"] == "too early-stage — not proven yet"
    lib.close()


def test_apply_fixes_and_read_back_matches(db_path):
    tool_id, cid = _seed_dirty_db(db_path)
    out = _run(db_path, apply=True)
    assert "Fixed: 2 field(s)" in out
    lib = Library(db_path)
    assert lib.get_tool(tool_id)["agent_taxonomy_note"] == "Acme flags anomalies—reviewed by a human."
    assert lib.get_community_profile(cid)["anti_fit"] == "too early-stage—not proven yet"
    lib.close()


def test_apply_is_idempotent(db_path):
    _seed_dirty_db(db_path)
    _run(db_path, apply=True)
    out2 = _run(db_path, apply=True)
    assert "Fixed: 0 field(s)" in out2


def test_missing_db_exits_nonzero_and_never_creates_a_file():
    """resolve_db_path(allow_missing=False) already refuses to let sqlite3
    silently create an empty file at a path that doesn't exist — confirm
    that guard actually holds for this script specifically (nonzero exit,
    clear stderr message, no file left behind), rather than trusting it by
    inference from the shared helper alone. Production is /data/library.db,
    not a relative library.db — a typo'd path here must fail loudly, never
    silently report a clean, empty database."""
    missing_path = tempfile.mktemp(suffix=".db")
    assert not os.path.exists(missing_path)
    try:
        args = [sys.executable, "-m", "scripts.fix_spaced_em_dashes", "--db", missing_path]
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


def test_apply_does_not_clear_needs_verification_or_citations(db_path):
    """The one real risk of a narrow raw-SQL fix vs. reusing
    Library.update_tool_agent_taxonomy: that higher-level method clears
    agent_taxonomy_needs_verification and entity_citations on the
    assumption a human just made a real edit. This is a pure whitespace
    substitution, not an edit — neither must change."""
    tool_id, _cid = _seed_dirty_db(db_path)
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


def test_apply_logs_every_change_to_voice_review_queue(db_path):
    """Brian's rule: nothing changes quietly. --apply must leave the same
    kind of trace a live save through Library._vf would have — an
    auto_corrected voice_review_queue row, source='script', one per
    (table, row_id, column) actually fixed. Covers both real tables and the
    settings convention (row_id=None, key as the column)."""
    tool_id, cid = _seed_dirty_db(db_path)
    lib = Library(db_path)
    # Raw insert, deliberately bypassing set_setting()'s own _vf call (which
    # would auto-fix and log this at seed time) — the point is seeding a
    # value the SCRIPT itself has to find and fix, same as _seed_dirty_db's
    # own raw UPDATEs for the tool/community rows above.
    lib.conn.execute(
        "INSERT INTO settings (key, value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        ("homepage_headline_copy", "Building things — that matter."),
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
    assert tool_row["rule"] == "spaced-em-dash"
    assert tool_row["before_text"] == "Acme flags anomalies — reviewed by a human."
    assert tool_row["after_text"] == "Acme flags anomalies—reviewed by a human."

    profile_row = by_location[("community_profiles", str(cid), "anti_fit")]
    assert profile_row["status"] == "auto_corrected"
    assert profile_row["source"] == "script"

    # Settings convention: row_id is None (not the string "None"), the
    # settings key is what's stored in column_name — matches
    # Library.set_setting's own _vf call exactly.
    settings_row = by_location[("settings", None, "homepage_headline_copy")]
    assert settings_row["status"] == "auto_corrected"
    assert settings_row["source"] == "script"
    assert settings_row["after_text"] == "Building things—that matter."
    lib.close()


def test_preview_never_writes_to_voice_review_queue(db_path):
    """The no-writes-in-preview-mode contract covers the queue log too, not
    just the content columns."""
    _seed_dirty_db(db_path)
    _run(db_path, apply=False)
    lib = Library(db_path)
    assert lib.list_voice_review_queue() == []
    lib.close()
