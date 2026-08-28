"""scripts/report_regen_blast_radius.py — community-side extension (2026-08
follow-up) plus the log-independent DB-scan default (2026-08 second
follow-up, prompted by the real em-dash cleanup's own JSONL log being lost
to a redeploy before this report could read it). Covers: community_profile
analysis (failures / cite-tag pollution / legacy-shape across all 22
prose-capable columns), the per-(entity_type, entity_id, field) dedup
across multiple --log-file inputs (the dropped-SSH-session rerun case), the
full-catalog DB-scan default when no --log-file is passed at all, and that
the script remains fully read-only in both modes.

Run as a subprocess (not an import-and-call) so stdout capture and argv
parsing are exercised exactly the way `railway ssh` will invoke it."""
import json
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


def _write_log(rows: list[dict]) -> str:
    path = tempfile.mktemp(suffix=".jsonl")
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    return path


def _run(db_path: str, *log_files: str) -> str:
    args = [sys.executable, "-m", "scripts.report_regen_blast_radius", "--db", db_path]
    for lf in log_files:
        args += ["--log-file", lf]
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"
    return result.stdout


def test_community_legacy_shape_and_pollution_detected(db_path):
    lib = Library(db_path)
    cid = lib.add_community("Acme Circle", "https://acme-circle.example", "demo", "Free", [], approved=1)
    lib.upsert_community_profile(cid, ideal_member="great fit", anti_fit="good fit")
    # Bypass the normalizer directly (simulating pre-backstop legacy content)
    # so the report has something real to find.
    lib.conn.execute(
        "UPDATE community_profiles SET anti_fit=?, notable_members=? WHERE community_id=?",
        ("too early-stage — not proven yet", 'roster includes <cite index="D-S">a firm</cite>', cid),
    )
    lib.conn.commit()
    lib.close()

    log = _write_log([
        {"entity_type": "community", "entity_id": cid, "name": "Acme Circle",
         "field": "community_profile", "status": "success"},
    ])
    out = _run(db_path, log)
    assert "[community 1] Acme Circle — community_profile (anti_fit): spaced em dash" in out
    assert "cite index=" in out
    assert "notable_members" in out
    assert "Distinct communities needing regeneration (any field): 1" in out


def test_community_failure_reported(db_path):
    lib = Library(db_path)
    lib.close()
    log = _write_log([
        {"entity_type": "community", "entity_id": 42, "name": "Ghost Circle",
         "field": "community_profile", "status": "failure", "detail": "APIError: 529"},
    ])
    out = _run(db_path, log)
    assert "[community 42] Ghost Circle — field='community_profile'" in out
    assert "APIError: 529" in out


def test_clean_community_reports_zero_hits(db_path):
    lib = Library(db_path)
    cid = lib.add_community("Clean Circle", "https://clean-circle.example", "demo", "Free", [], approved=1)
    lib.upsert_community_profile(cid, ideal_member="great fit", anti_fit="not a fit—clean")
    lib.close()
    log = _write_log([
        {"entity_type": "community", "entity_id": cid, "name": "Clean Circle",
         "field": "community_profile", "status": "success"},
    ])
    out = _run(db_path, log)
    assert "Distinct communities needing regeneration (any field): 0" in out


def test_repeated_community_across_two_log_files_is_not_double_counted(db_path):
    """The real dropped-SSH-session shape: the same community logged as a
    success in two separate run files. Must be judged once, on the LAST
    status, not counted twice."""
    lib = Library(db_path)
    cid = lib.add_community("Rerun Circle", "https://rerun-circle.example", "demo", "Free", [], approved=1)
    lib.upsert_community_profile(cid, ideal_member="great fit")
    lib.conn.execute(
        "UPDATE community_profiles SET public_criticism=? WHERE community_id=?",
        ("some say it's too clubby — hard to break in", cid),
    )
    lib.conn.commit()
    lib.close()

    log1 = _write_log([
        {"entity_type": "community", "entity_id": cid, "name": "Rerun Circle",
         "field": "community_profile", "status": "failure", "detail": "dropped SSH session"},
    ])
    log2 = _write_log([
        {"entity_type": "community", "entity_id": cid, "name": "Rerun Circle",
         "field": "community_profile", "status": "success"},
    ])
    out = _run(db_path, log1, log2)
    assert "After de-duplicating to the last status per (entity_type, entity_id, field): 1" in out
    # The failure from log1 must not appear — log2's success is the latest status.
    assert "dropped SSH session" not in out
    assert "public_criticism" in out  # the success row's own real content gets checked


def test_tool_and_community_ids_are_not_conflated(db_path):
    """A tool id and a community id can collide numerically — the two must
    be reported as fully separate entities, never merged into one set."""
    lib = Library(db_path)
    tool_id = lib.add_tool("Acme Tool", "clean desc", "https://acme-tool.example", [], approved=1)
    lib.conn.execute(
        "UPDATE tools SET agent_taxonomy_note=? WHERE id=?",
        ("flags anomalies — reviewed by a human", tool_id),
    )
    cid = lib.add_community("Acme Circle", "https://acme-circle.example", "demo", "Free", [], approved=1)
    lib.upsert_community_profile(cid, ideal_member="great fit")
    lib.conn.execute(
        "UPDATE community_profiles SET anti_fit=? WHERE community_id=?",
        ("not a fit — too early", cid),
    )
    lib.conn.commit()
    lib.close()
    assert tool_id == cid, "test assumes colliding ids to prove they aren't conflated"

    log = _write_log([
        {"entity_type": "tool", "entity_id": tool_id, "name": "Acme Tool",
         "field": "agent_taxonomy", "status": "success"},
        {"entity_type": "community", "entity_id": cid, "name": "Acme Circle",
         "field": "community_profile", "status": "success"},
    ])
    out = _run(db_path, log)
    assert "[tool 1] Acme Tool" in out
    assert "[community 1] Acme Circle" in out
    assert "Distinct tools needing regeneration (any field): 1" in out
    assert "Distinct communities needing regeneration (any field): 1" in out


def test_db_scan_mode_finds_dirty_entities_with_no_log_file(db_path):
    """The new default: omit --log-file entirely and the script scans the
    full current catalog directly."""
    lib = Library(db_path)
    tool_id = lib.add_tool("Acme Tool", "clean desc", "https://acme-tool.example", [], approved=1)
    lib.conn.execute(
        "UPDATE tools SET agent_taxonomy_note=? WHERE id=?",
        ("flags anomalies — reviewed by a human", tool_id),
    )
    cid = lib.add_community("Acme Circle", "https://acme-circle.example", "demo", "Free", [], approved=1)
    lib.upsert_community_profile(cid, ideal_member="great fit")
    lib.conn.execute(
        "UPDATE community_profiles SET anti_fit=? WHERE community_id=?",
        ("not a fit — too early", cid),
    )
    lib.conn.commit()
    lib.close()

    out = _run(db_path)  # no log files passed at all
    assert "Mode: DB-scan (no --log-file supplied)" in out
    assert "[tool 1] Acme Tool — agent_taxonomy (agent_taxonomy_note): spaced em dash" in out
    assert "[community 1] Acme Circle — community_profile (anti_fit): spaced em dash" in out
    assert "Distinct tools needing regeneration (any field): 1" in out
    assert "Distinct communities needing regeneration (any field): 1" in out


def test_db_scan_mode_excludes_clean_entities(db_path):
    """A tool/community with no violations at all must not appear anywhere
    in DB-scan mode's output — proves the scan isn't just flagging every
    row it touches."""
    lib = Library(db_path)
    lib.add_tool("Clean Tool", "nothing wrong here at all", "https://clean-tool.example", [], approved=1)
    cid = lib.add_community("Clean Circle", "https://clean-circle.example", "demo", "Free", [], approved=1)
    lib.upsert_community_profile(cid, ideal_member="a genuinely clean fit", anti_fit="also clean")
    lib.close()

    out = _run(db_path)
    assert "Distinct tools needing regeneration (any field): 0" in out
    assert "Distinct communities needing regeneration (any field): 0" in out
    assert "Clean Tool" not in out
    assert "Clean Circle" not in out


def test_db_scan_mode_covers_unapproved_tools_too(db_path):
    """A pending (unapproved) tool's drafted content matters before it's
    ever approved — DB-scan mode must not silently skip it."""
    lib = Library(db_path)
    tool_id = lib.add_tool("Pending Tool", "clean desc", "https://pending-tool.example", [], approved=0)
    lib.conn.execute(
        "UPDATE tools SET agent_taxonomy_note=? WHERE id=?",
        ("flags anomalies — reviewed by a human", tool_id),
    )
    lib.conn.commit()
    lib.close()

    out = _run(db_path)
    assert "[tool 1] Pending Tool — agent_taxonomy (agent_taxonomy_note): spaced em dash" in out


def test_db_scan_mode_failures_section_says_not_tracked(db_path):
    lib = Library(db_path)
    lib.close()
    out = _run(db_path)
    assert "not tracked in DB-scan mode" in out


def test_log_scoped_mode_still_works_when_log_file_is_passed(db_path):
    """Regression guard: passing --log-file must still produce the original
    log-scoped FAILURES section, not silently fall into DB-scan mode."""
    lib = Library(db_path)
    lib.close()
    log = _write_log([
        {"entity_type": "tool", "entity_id": 999, "name": "Ghost",
         "field": "description", "status": "failure", "detail": "boom"},
    ])
    out = _run(db_path, log)
    assert "Mode: log-scoped" in out
    assert "[tool 999] Ghost — field='description'" in out
    assert "boom" in out


def test_script_makes_no_write_calls():
    """The script's own docstring promises zero writes — grep-verify the
    claim the same way the original tool-only version's test suite did,
    rather than trusting the comment."""
    src = open(os.path.join(ROOT, "scripts", "report_regen_blast_radius.py"), encoding="utf-8").read()
    write_calls = [
        "add_tool(", "update_tool(", "delete_tool(", "upsert_community_profile(",
        "update_community(", "add_community(", ".conn.execute(\"UPDATE",
        ".conn.execute(\"INSERT", ".conn.execute(\"DELETE", ".conn.commit(",
    ]
    hits = [c for c in write_calls if c in src]
    assert not hits, f"report_regen_blast_radius.py appears to make a write call: {hits}"
