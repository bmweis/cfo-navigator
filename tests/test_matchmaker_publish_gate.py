"""Chat Matchmaker context — radical-transparency review standard.

Originally (Description/Agent-taxonomy publish gate PR) this file covered a
real leak: unlike the directory card and profile page, linklib/matchmaker.py
fed raw, unverified description/summary/agent_taxonomy_note/
competitive_differentiation and Community-profile-draft text straight into
Claude's context regardless of review state — a synthesized matchmaker
answer could surface unverified (possibly fabricated) text to any visitor.
That was fixed by EXCLUDING unverified content from the matchmaker's context
entirely, mirroring the hide-from-visitors publish gate everywhere else at
the time.

Brian's ratified radical-transparency review standard (Gate-Extraction Phase
0/PR A) supersedes that: nothing is hidden from any surface anymore,
including the matchmaker. Unverified content is now INCLUDED, with an inline
marker — "(unverified)" per field for Software's three independent flags, a
single leading "Note: ... is unverified" line per community for the one
whole-profile flag — plus a standing disclaimer appended to the system
prompt (see test_matchmaker_disclaimer.py-equivalent coverage in
test_software_matchmaker.py/test_community_matchmaker.py's own _build_system
tests, if any) whenever at least one marker is present. This file now
asserts the INCLUDE-with-marker behavior directly.
"""
import pathlib
import sys
import tempfile, os

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Library
from linklib.matchmaker import _build_software_context, _build_communities_context


@pytest.fixture
def db(monkeypatch):
    path = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", path)
    lib = Library(path)
    yield lib
    lib.close()
    if os.path.exists(path):
        os.remove(path)


def test_software_context_includes_unverified_description_with_marker(db):
    db.add_tool(
        "Runway", "A drafted description awaiting review.", "https://runway.com",
        ["FP&A"], approved=1, summary="A drafted short summary.",
        description_needs_verification=1,
    )
    ctx, has_unverified = _build_software_context(db)
    assert "A drafted short summary." in ctx
    assert "What it does (unverified): A drafted short summary." in ctx
    assert has_unverified is True


def test_software_context_includes_verified_description_with_no_marker(db):
    db.add_tool(
        "Datarails", "A confirmed, human-reviewed description.", "https://datarails.com",
        ["FP&A"], approved=1, summary="A reviewed short summary.",
        description_needs_verification=0,
    )
    ctx, has_unverified = _build_software_context(db)
    # "What it does" prefers summary over description when both are present
    # (pre-existing _line behavior, unrelated to the review standard).
    assert "What it does: A reviewed short summary." in ctx
    assert "(unverified)" not in ctx
    assert has_unverified is False


def test_software_context_marks_unverified_differentiation_and_taxonomy_independently(db):
    tid = db.add_tool(
        "Abacum", "A confirmed description.", "https://abacum.io",
        ["FP&A"], approved=1, description_needs_verification=0,
    )
    db.update_tool_differentiation(
        tid, "A drafted, unverified differentiation note.", needs_verification=1,
    )
    db.set_tool_agent_taxonomy_draft(tid, "A drafted, unverified taxonomy note.", needs_verification=1)

    ctx, has_unverified = _build_software_context(db)
    assert "How it differs from competitors (unverified): A drafted, unverified differentiation note." in ctx
    assert "Agent/automation taxonomy (unverified): A drafted, unverified taxonomy note." in ctx
    # Description is verified on this tool — only the other two fields carry
    # the marker, confirming it's per-field (three independent flags), not a
    # whole-record flag reused across fields the way Communities' is below.
    assert "What it does (unverified)" not in ctx
    assert has_unverified is True


def test_communities_context_includes_unreviewed_profile_with_one_note(db):
    cid = db.add_community(
        "Peer CFOs", "https://peercfos.example", "Series B+ CFOs", "Free", [], approved=1,
    )
    db.upsert_community_profile(
        cid, ideal_member="A drafted ideal-member note.",
        verdict_summary="A drafted bottom-line verdict.",
        needs_review=1,
    )
    ctx, has_unverified = _build_communities_context(db)
    assert "A drafted ideal-member note." in ctx
    assert "Ideal member:" in ctx
    assert "Peer CFOs" in ctx
    # One whole-profile note, not a per-line marker — the community's single
    # needs_review flag governs all nine profile lines together.
    assert "Note: this community's profile is unverified; treat the following details as provisional." in ctx
    assert has_unverified is True


def test_communities_context_includes_reviewed_profile_with_no_note(db):
    cid = db.add_community(
        "Peer CFOs", "https://peercfos.example", "Series B+ CFOs", "Free", [], approved=1,
    )
    db.upsert_community_profile(
        cid, ideal_member="A reviewed ideal-member note.",
        verdict_summary="A reviewed bottom-line verdict.",
        needs_review=0,
    )
    ctx, has_unverified = _build_communities_context(db)
    assert "A reviewed ideal-member note." in ctx
    assert "Ideal member:" in ctx
    assert "unverified" not in ctx
    assert has_unverified is False
