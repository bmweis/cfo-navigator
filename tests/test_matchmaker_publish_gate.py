"""Chat Matchmaker context filter — the second known leak flagged in the
Description/Agent-taxonomy publish gate PR's own description: unlike the
directory card and profile page, linklib/matchmaker.py fed raw, unverified
description/summary/agent_taxonomy_note/competitive_differentiation and
Community-profile-draft text straight into Claude's context, regardless of
review state — a synthesized matchmaker answer could surface unverified
(possibly fabricated) text to any visitor, admin or not. Fixed by having
_build_software_context/_build_communities_context apply the same
publish-gate logic already used elsewhere: per-field for Software (each of
the three fields carries its own *_needs_verification column), whole-profile
for Communities (community_profiles.needs_review gates the entire drafted
profile at once, matching webapp.app's _profile_hidden/_display_profile
swap).
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


def test_software_context_excludes_unverified_description(db):
    db.add_tool(
        "Runway", "A fabricated-sounding drafted description.", "https://runway.com",
        ["FP&A"], approved=1, summary="A drafted short summary.",
        description_needs_verification=1,
    )
    ctx = _build_software_context(db)
    assert "fabricated-sounding" not in ctx
    assert "drafted short summary" not in ctx
    assert "Runway" in ctx  # the entry itself still appears, minus the unverified field


def test_software_context_includes_verified_description(db):
    db.add_tool(
        "Datarails", "A confirmed, human-reviewed description.", "https://datarails.com",
        ["FP&A"], approved=1, summary="A reviewed short summary.",
        description_needs_verification=0,
    )
    ctx = _build_software_context(db)
    # "What it does" prefers summary over description when both are present
    # (pre-existing _line behavior, unrelated to the publish-gate fix).
    assert "reviewed short summary" in ctx


def test_software_context_excludes_unverified_differentiation_and_taxonomy(db):
    tid = db.add_tool(
        "Abacum", "A confirmed description.", "https://abacum.io",
        ["FP&A"], approved=1, description_needs_verification=0,
    )
    db.update_tool_differentiation(
        tid, "A drafted, unverified differentiation note.", needs_verification=1,
    )
    db.set_tool_agent_taxonomy_draft(tid, "A drafted, unverified taxonomy note.", needs_verification=1)

    ctx = _build_software_context(db)
    assert "drafted, unverified differentiation note" not in ctx
    assert "drafted, unverified taxonomy note" not in ctx


def test_communities_context_excludes_unreviewed_profile_entirely(db):
    cid = db.add_community(
        "Peer CFOs", "https://peercfos.example", "Series B+ CFOs", "Free", [], approved=1,
    )
    db.upsert_community_profile(
        cid, ideal_member="A drafted ideal-member note.",
        verdict_summary="A drafted bottom-line verdict.",
        founded_year=2019, needs_review=1,
    )
    ctx = _build_communities_context(db)
    assert "A drafted ideal-member note." not in ctx
    assert "A drafted bottom-line verdict." not in ctx
    assert "2019" not in ctx
    assert "Peer CFOs" in ctx  # community-level fields (not part of the draft) still appear


def test_communities_context_includes_reviewed_profile(db):
    cid = db.add_community(
        "Peer CFOs", "https://peercfos.example", "Series B+ CFOs", "Free", [], approved=1,
    )
    db.upsert_community_profile(
        cid, ideal_member="A reviewed ideal-member note.",
        verdict_summary="A reviewed bottom-line verdict.",
        founded_year=2019, needs_review=0,
    )
    ctx = _build_communities_context(db)
    assert "A reviewed ideal-member note." in ctx
    assert "2019" in ctx
