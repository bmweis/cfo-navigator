"""Proves the spaced-em-dash backstop actually runs on every real `Library`
write path a save can take, not just the pure-function unit tests in
test_voice_mechanics.py. Each test writes text containing a spaced em dash
through the exact method a real caller uses (the bulk regen script, an
admin Generate-then-save AJAX route, or a hand-edit save — see the
docstring on each method in linklib/db.py) and reads the row back to
confirm the stored value is already fixed. Per Brian's explicit ask
(2026-08): this is the actual coverage list, not just a claim of coverage.

Tool fields covered: description, summary (add_tool, update_tool,
update_tool_content, quick_update_tool), agent_taxonomy_note
(update_tool_agent_taxonomy, set_tool_agent_taxonomy_draft),
competitive_differentiation (update_tool_differentiation), suite_note
(set_tool_suite_note).

Community fields covered: demographic, cost_note, notes, local_markets
(add_community, update_community, update_community_content), and every
prose column in community_profiles (upsert_community_profile,
update_community_profile_research_fields).
"""
import os
import tempfile

import pytest

from linklib.db import Library

DASH = "flags the anomaly — drafts the note"
FIXED = "flags the anomaly—drafts the note"


@pytest.fixture
def lib():
    db = tempfile.mktemp(suffix=".db")
    library = Library(db)
    yield library
    library.close()
    if os.path.exists(db):
        os.remove(db)


# --- tools --------------------------------------------------------------

def test_add_tool_fixes_description_and_summary(lib):
    tool_id = lib.add_tool("Acme", DASH, "https://acme.example", [], summary=DASH)
    row = lib.get_tool(tool_id)
    assert row["description"] == FIXED
    assert row["summary"] == FIXED


def test_update_tool_fixes_description_and_summary(lib):
    tool_id = lib.add_tool("Acme", "orig", "https://acme.example", [])
    lib.update_tool(tool_id, "Acme", DASH, "https://acme.example", [], summary=DASH)
    row = lib.get_tool(tool_id)
    assert row["description"] == FIXED
    assert row["summary"] == FIXED


def test_update_tool_content_fixes_description(lib):
    tool_id = lib.add_tool("Acme", "orig", "https://acme.example", [])
    lib.update_tool_content(tool_id, "Acme", DASH)
    assert lib.get_tool(tool_id)["description"] == FIXED


def test_quick_update_tool_fixes_description_and_summary(lib):
    tool_id = lib.add_tool("Acme", "orig", "https://acme.example", [])
    lib.quick_update_tool(tool_id, DASH, 0, "", "", summary=DASH)
    row = lib.get_tool(tool_id)
    assert row["description"] == FIXED
    assert row["summary"] == FIXED


def test_update_tool_agent_taxonomy_fixes_note(lib):
    tool_id = lib.add_tool("Acme", "orig", "https://acme.example", [])
    lib.update_tool_agent_taxonomy(tool_id, DASH)
    assert lib.get_tool(tool_id)["agent_taxonomy_note"] == FIXED


def test_set_tool_agent_taxonomy_draft_fixes_note(lib):
    tool_id = lib.add_tool("Acme", "orig", "https://acme.example", [])
    lib.set_tool_agent_taxonomy_draft(tool_id, DASH)
    assert lib.get_tool(tool_id)["agent_taxonomy_note"] == FIXED


def test_update_tool_differentiation_fixes_field(lib):
    tool_id = lib.add_tool("Acme", "orig", "https://acme.example", [])
    lib.update_tool_differentiation(tool_id, DASH)
    assert lib.get_tool(tool_id)["competitive_differentiation"] == FIXED


def test_set_tool_suite_note_fixes_field(lib):
    tool_id = lib.add_tool("Acme", "orig", "https://acme.example", [])
    lib.set_tool_suite_note(tool_id, DASH)
    assert lib.get_tool(tool_id)["suite_note"] == FIXED


# --- communities ----------------------------------------------------------

def test_add_community_fixes_prose_fields(lib):
    cid = lib.add_community(
        "Acme Circle", "https://acme.example", DASH, "Free", [],
        cost_note=DASH, notes=DASH, local_markets=DASH,
    )
    row = lib.get_community(cid)
    assert row["demographic"] == FIXED
    assert row["cost_note"] == FIXED
    assert row["notes"] == FIXED
    assert row["local_markets"] == FIXED


def test_update_community_fixes_prose_fields(lib):
    cid = lib.add_community("Acme Circle", "https://acme.example", "orig", "Free", [])
    lib.update_community(
        cid, "Acme Circle", "https://acme.example", DASH, "Free", [],
        cost_note=DASH, notes=DASH, local_markets=DASH,
    )
    row = lib.get_community(cid)
    assert row["demographic"] == FIXED
    assert row["cost_note"] == FIXED
    assert row["notes"] == FIXED
    assert row["local_markets"] == FIXED


def test_update_community_content_fixes_notes(lib):
    cid = lib.add_community("Acme Circle", "https://acme.example", "orig", "Free", [])
    lib.update_community_content(cid, "Acme Circle", notes=DASH)
    assert lib.get_community(cid)["notes"] == FIXED


# --- community_profiles ----------------------------------------------------

def test_upsert_community_profile_fixes_every_prose_field(lib):
    cid = lib.add_community("Acme Circle", "https://acme.example", "orig", "Free", [])
    lib.upsert_community_profile(
        cid,
        ideal_member=DASH, anti_fit=DASH, value_prop=DASH,
        format_reality=DASH, engagement_level=DASH,
        sponsor_relationship_note=DASH, application_friction=DASH,
        cost_value_verdict=DASH, notable_members=DASH,
        public_criticism=DASH, verdict_summary=DASH,
        business_model=DASH, primary_purpose=DASH, cpe_eligible=DASH,
        platform_type=DASH, meeting_format=DASH, event_style=DASH,
        seniority_band=DASH, resources_included=DASH,
        stage_focus=DASH, jobs_program=DASH, team_or_individual=DASH,
    )
    row = lib.get_community_profile(cid)
    prose_fields = [
        "ideal_member", "anti_fit", "value_prop", "format_reality",
        "engagement_level", "sponsor_relationship_note", "application_friction",
        "cost_value_verdict", "notable_members", "public_criticism",
        "verdict_summary", "business_model", "primary_purpose", "cpe_eligible",
        "platform_type", "meeting_format", "event_style", "seniority_band",
        "resources_included", "stage_focus", "jobs_program", "team_or_individual",
    ]
    for field in prose_fields:
        assert row[field] == FIXED, f"{field} was not fixed: {row[field]!r}"


def test_update_community_profile_research_fields_fixes_passed_fields(lib):
    cid = lib.add_community("Acme Circle", "https://acme.example", "orig", "Free", [])
    lib.upsert_community_profile(cid)  # seed a row so the narrow UPDATE has something to hit
    lib.update_community_profile_research_fields(
        cid, notable_members=DASH, anti_fit=DASH, sponsor_relationship_note=DASH,
        public_criticism=DASH, stage_focus=DASH, jobs_program=DASH,
        team_or_individual=DASH,
    )
    row = lib.get_community_profile(cid)
    for field in (
        "notable_members", "anti_fit", "sponsor_relationship_note",
        "public_criticism", "stage_focus", "jobs_program", "team_or_individual",
    ):
        assert row[field] == FIXED, f"{field} was not fixed: {row[field]!r}"
