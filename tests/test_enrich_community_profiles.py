"""scripts/enrich_community_profiles.py — the Communities equivalent of
scripts/enrich_agent_taxonomy.py: bulk-drafts the Community Profile fields
instead of clicking "Auto-fill from URL" once per community. Covers
selection (--communities/--limit), dry-run vs. write, the skip-unless-force
already-researched guard, and the critical echo-back safety property:
upsert_community_profile fully replaces every column it owns, so this
script must read the existing row first and pass every field the draft
doesn't produce straight through — never blank a retired field just
because a bulk refresh ran.
"""
import os
import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import enrich
from linklib.db import Library
from scripts import enrich_community_profiles as script


@pytest.fixture
def db(monkeypatch):
    path = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    # 2026-08 visibility follow-up: the script now refuses (require_voice_setting)
    # unless voice_core is seeded.
    _seed_lib = Library(path)
    _seed_lib.seed_voice_prompts()
    _seed_lib.close()
    yield path
    if os.path.exists(path):
        os.remove(path)


def _add_community(lib: Library, name: str, url: str) -> int:
    return lib.add_community(name, url, "CFOs at growth-stage companies", "Free", ["FP&A"], approved=1)


def _mock_generate_community_profile(monkeypatch, **overrides):
    calls = []

    def _fake(name, url, existing=None, model="", voice_core=""):
        calls.append((name, url, existing, model))
        defaults = dict(
            ideal_member="CFOs at Series B+ SaaS companies.",
            anti_fit="Not a fit for solo founders.",
            value_prop="Peer benchmarking and vendor intros.",
            format_reality="Mostly async Slack, quarterly in-person.",
            engagement_level="High for the first month, tapers off.",
            sponsor_relationship_note="",
            business_model="Membership dues.",
            application_friction="Light vetting, approval in days.",
            cost_value_verdict="Worth it for the network alone.",
            notable_members="",
            public_criticism="",
            verdict_summary="A solid pick for finance leaders at scale.",
            jobs_program="", cpe_eligible="Yes", resources_included="Templates",
            low_confidence=False, model="claude-opus-5",
            input_tokens=800, output_tokens=600, cost_usd=0.03,
        )
        defaults.update(overrides)
        return enrich.CommunityProfileDraft(**defaults)

    monkeypatch.setattr(enrich, "generate_community_profile", _fake)
    return calls


# -- selection ------------------------------------------------------------------

def test_select_communities_by_name(db):
    lib = Library(db)
    a = _add_community(lib, "Chief", "https://chief.com")
    _add_community(lib, "Rho Community", "https://rho.co/community")
    lib.close()

    lib = Library(db)
    selected = script._select_communities(lib, "Chief", 0)
    lib.close()
    assert [c["id"] for c in selected] == [a]


def test_select_communities_by_limit(db):
    lib = Library(db)
    for i in range(5):
        _add_community(lib, f"Community {i}", f"https://c{i}.example")
    lib.close()

    lib = Library(db)
    selected = script._select_communities(lib, "", 2)
    lib.close()
    assert len(selected) == 2


# -- main(): dry-run vs. write ----------------------------------------------------

def test_dry_run_writes_nothing(db, monkeypatch):
    lib = Library(db)
    _add_community(lib, "Chief", "https://chief.com")
    lib.close()

    calls = _mock_generate_community_profile(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["enrich_community_profiles", "--db", db, "--communities", "Chief", "--dry-run"])
    rc = script.main()
    assert rc == 0
    assert len(calls) == 1

    lib = Library(db)
    communities = lib.list_communities(approved_only=True)
    profile = lib.get_community_profile(communities[0]["id"])
    lib.close()
    assert profile is None   # dry run must not touch the DB


def test_writes_profile_with_needs_review_flag(db, monkeypatch):
    lib = Library(db)
    cid = _add_community(lib, "Chief", "https://chief.com")
    lib.close()

    _mock_generate_community_profile(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["enrich_community_profiles", "--db", db, "--communities", "Chief"])
    rc = script.main()
    assert rc == 0

    lib = Library(db)
    profile = lib.get_community_profile(cid)
    lib.close()
    assert profile["ideal_member"] == "CFOs at Series B+ SaaS companies."
    assert profile["verdict_summary"] == "A solid pick for finance leaders at scale."
    assert profile["needs_review"] == 1   # never auto-confirmed


def test_records_enrichment_cost(db, monkeypatch):
    lib = Library(db)
    _add_community(lib, "Chief", "https://chief.com")
    lib.close()

    _mock_generate_community_profile(monkeypatch, cost_usd=0.05)
    monkeypatch.setattr(sys, "argv", ["enrich_community_profiles", "--db", db, "--communities", "Chief"])
    script.main()

    lib = Library(db)
    total = lib.conn.execute("SELECT SUM(cost_usd) FROM enrichment_cost").fetchone()[0]
    lib.close()
    assert total == pytest.approx(0.05)


# -- skip / --force ---------------------------------------------------------------

def test_already_researched_skipped_without_force(db, monkeypatch):
    lib = Library(db)
    cid = _add_community(lib, "Chief", "https://chief.com")
    lib.upsert_community_profile(cid, ideal_member="Already drafted.", needs_review=0)
    lib.close()

    calls = _mock_generate_community_profile(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["enrich_community_profiles", "--db", db, "--communities", "Chief"])
    script.main()
    assert len(calls) == 0

    lib = Library(db)
    profile = lib.get_community_profile(cid)
    lib.close()
    assert profile["ideal_member"] == "Already drafted."   # untouched


def test_force_redrafts_existing(db, monkeypatch):
    lib = Library(db)
    cid = _add_community(lib, "Chief", "https://chief.com")
    lib.upsert_community_profile(cid, ideal_member="Old draft.", needs_review=0)
    lib.close()

    _mock_generate_community_profile(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["enrich_community_profiles", "--db", db, "--communities", "Chief", "--force"])
    script.main()

    lib = Library(db)
    profile = lib.get_community_profile(cid)
    lib.close()
    assert profile["ideal_member"] == "CFOs at Series B+ SaaS companies."
    assert profile["needs_review"] == 1


# -- the echo-back safety property --------------------------------------------------

def test_refresh_does_not_blank_untouched_profile_fields(db, monkeypatch):
    """upsert_community_profile fully replaces every column — a bulk refresh
    must read the existing row and pass through fields generate_community_profile
    doesn't produce, or a --force re-run silently wipes out the retired
    free-text fields."""
    lib = Library(db)
    cid = _add_community(lib, "Chief", "https://chief.com")
    lib.upsert_community_profile(
        cid, ideal_member="Old draft.", needs_review=0,
        primary_purpose="Networking", cpe_eligible="Yes",
        platform_type="Slack + in-person", meeting_format="Hybrid",
        event_style="Roundtable", seniority_band="VP+", founded_year=1999,
        stage_focus="Series B+", team_or_individual="Individual",
        resources_included="Vendor directory",
    )
    lib.close()

    _mock_generate_community_profile(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["enrich_community_profiles", "--db", db, "--communities", "Chief", "--force"])
    script.main()

    lib = Library(db)
    profile = lib.get_community_profile(cid)
    lib.close()
    assert profile["ideal_member"] == "CFOs at Series B+ SaaS companies."   # refreshed
    assert profile["primary_purpose"] == "Networking"                       # preserved
    assert profile["founded_year"] == 1999
    assert profile["stage_focus"] == "Series B+"
    assert profile["team_or_individual"] == "Individual"
    assert profile["platform_type"] == "Slack + in-person"
    assert profile["meeting_format"] == "Hybrid"
    assert profile["event_style"] == "Roundtable"
    assert profile["seniority_band"] == "VP+"
    assert profile["resources_included"] == "Templates"                     # live field, regenerated


# -- CLI guardrails ---------------------------------------------------------------

def test_requires_api_key(db, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    lib = Library(db)
    _add_community(lib, "Chief", "https://chief.com")
    lib.close()
    monkeypatch.setattr(sys, "argv", ["enrich_community_profiles", "--db", db, "--communities", "Chief"])
    assert script.main() == 2


def test_requires_communities_or_limit(db):
    lib = Library(db)
    _add_community(lib, "Chief", "https://chief.com")
    lib.close()
    sys.argv = ["enrich_community_profiles", "--db", db]
    assert script.main() == 2


# -- over-limit drafts (2a.1) ---------------------------------------------------

def test_over_limit_draft_is_skipped_and_the_run_continues(db, monkeypatch, capsys):
    """A draft over a field's hard limit is refused whole by the library. The
    run must skip that community, say why and carry on, not abort."""
    lib = Library(db)
    a = _add_community(lib, "Alpha", "https://alpha.example")
    b = _add_community(lib, "Beta", "https://beta.example")
    lib.close()

    def _fake(name, url, existing=None, model="", voice_core=""):
        too_long = "x" * 900 if name == "Alpha" else "Fine."
        return enrich.CommunityProfileDraft(
            ideal_member=too_long, anti_fit="", value_prop="", format_reality="", engagement_level="",
            sponsor_relationship_note="", business_model="", application_friction="",
            cost_value_verdict="", notable_members="", public_criticism="",
            verdict_summary="Best for X.", jobs_program="", cpe_eligible="", resources_included="",
            low_confidence=False, model="claude-opus-5", input_tokens=1, output_tokens=1, cost_usd=0.01)

    monkeypatch.setattr(enrich, "generate_community_profile", _fake)
    monkeypatch.setattr(sys, "argv", ["x", "--db", db, "--communities", "Alpha,Beta"])
    monkeypatch.setattr(script.time, "sleep", lambda s: None)
    rc = script.main()
    out = capsys.readouterr().out
    assert rc == 0
    lib = Library(db)
    assert not (lib.get_community_profile(a) or {}).get("ideal_member")   # refused whole
    assert (lib.get_community_profile(b) or {}).get("verdict_summary") == "Best for X."
    lib.close()
    assert "skipped, over limit" in out and "Ideal member" in out and "900" in out
    assert "Skipped, over limit: 1" in out
