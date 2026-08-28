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
