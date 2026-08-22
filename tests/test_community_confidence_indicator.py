"""AI confidence indicator, Community profile draft (2026-08) — extends the
tool-side confidence indicator (see test_confidence_indicator.py) to the 12
Community profile fields judged to carry real fabrication risk
(linklib.enrich.COMMUNITY_CONFIDENCE_FIELDS). Unlike the tool-side fields,
the Community profile draft has no per-field needs_verification column — it
reuses the single whole-profile community_profiles.needs_review flag (see
CLAUDE.md's Phase G note), so the "Claude confidence" display is gated on
that shared flag instead of a per-field one.
"""
import os
import pathlib
import sys
import tempfile
import types

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import enrich
from linklib.db import Library


def _mock_anthropic(monkeypatch, payload_json):
    def _create(**kw):
        class _Block:
            type = "text"
            text = payload_json
        usage = types.SimpleNamespace(
            input_tokens=100, output_tokens=80,
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
        )
        return types.SimpleNamespace(content=[_Block()], usage=usage)
    fake = types.SimpleNamespace(Anthropic=lambda *a, **k: types.SimpleNamespace(
        messages=types.SimpleNamespace(create=lambda **kw: _create(**kw))))
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")


PROFILE_JSON = """{
  "ideal_member": "Seed-stage operator CFOs.", "anti_fit": "Late-stage teams.",
  "value_prop": "Peer benchmarking.", "format_reality": "Monthly virtual.",
  "engagement_level": "High.", "sponsor_relationship_note": "No sponsors.",
  "business_model": "Dues-funded.", "application_friction": "Light vetting.",
  "cost_value_verdict": "Worth it.", "notable_members": null,
  "founded_year": 2019, "public_criticism": null,
  "verdict_summary": "Best for seed-stage CFOs.",
  "stage_focus": "Seed", "jobs_program": "No", "team_or_individual": "Individual",
  "seniority_band": "CFO only", "primary_purpose": "Peer learning",
  "resources_included": "Benchmarking data", "platform_type": "Slack",
  "meeting_format": "Virtual", "event_style": "Small-group", "cpe_eligible": "No",
  "confidence": {
    "ideal_member": true, "anti_fit": true, "value_prop": false,
    "business_model": true, "format_reality": true, "engagement_level": false,
    "sponsor_relationship_note": true, "application_friction": true,
    "cost_value_verdict": false, "notable_members": true,
    "public_criticism": true, "verdict_summary": false
  }
}"""


# -- linklib.enrich: confidence dict parsing --------------------------------

def test_generate_community_profile_parses_confidence_dict(monkeypatch):
    from linklib import extract
    monkeypatch.setattr(extract, "fetch_page", lambda url, **kw: types.SimpleNamespace(content="Real page text."))
    _mock_anthropic(monkeypatch, PROFILE_JSON)

    draft = enrich.generate_community_profile("Acme Circle", "https://acme.example")
    assert draft is not None
    assert draft.confidence["ideal_member"] is True
    assert draft.confidence["value_prop"] is False
    assert draft.confidence["verdict_summary"] is False
    # Exactly the 12 approved keys — no more, no less.
    assert set(draft.confidence) == set(enrich.COMMUNITY_CONFIDENCE_FIELDS)


def test_generate_community_profile_defaults_missing_confidence_to_false(monkeypatch):
    from linklib import extract
    monkeypatch.setattr(extract, "fetch_page", lambda url, **kw: types.SimpleNamespace(content="Real page text."))
    _mock_anthropic(monkeypatch, '{"ideal_member": "X", "verdict_summary": "Y"}')   # no "confidence" key at all

    draft = enrich.generate_community_profile("Acme Circle", "https://acme.example")
    assert draft is not None
    assert all(v is False for v in draft.confidence.values())


def test_community_confidence_fields_excludes_categorical_and_ambiguous_fields():
    """Regression guard for the approved 12-field subset: founded_year and
    the short factual/categorical fields are out, and — per Brian's
    explicit call — so are stage_focus/jobs_program/team_or_individual,
    despite being in VOICE_REWRITE_FIELDS."""
    excluded = {
        "founded_year", "primary_purpose", "cpe_eligible", "platform_type",
        "meeting_format", "event_style", "seniority_band", "resources_included",
        "stage_focus", "jobs_program", "team_or_individual",
    }
    assert not (excluded & set(enrich.COMMUNITY_CONFIDENCE_FIELDS))
    assert len(enrich.COMMUNITY_CONFIDENCE_FIELDS) == 12


# -- Library: DB round trip --------------------------------------------------

@pytest.fixture
def lib(tmp_path):
    db = Library(str(tmp_path / "library.db"))
    yield db
    db.close()


def test_upsert_community_profile_persists_confidence(lib):
    cid = lib.add_community(name="Acme Circle", url="https://acme.example",
                            demographic="CFOs", cost_band="Free", categories=[], approved=1)
    lib.upsert_community_profile(cid, ideal_member="X",
                                 confidence={"ideal_member": 1, "value_prop": 0})
    p = lib.get_community_profile(cid)
    assert p["ideal_member_ai_confident"] == 1
    assert p["value_prop_ai_confident"] == 0
    assert p["anti_fit_ai_confident"] is None   # not passed -> NULL


def test_upsert_community_profile_no_confidence_arg_writes_all_null(lib):
    cid = lib.add_community(name="Acme Circle", url="https://acme.example",
                            demographic="CFOs", cost_band="Free", categories=[], approved=1)
    lib.upsert_community_profile(cid, ideal_member="X")
    p = lib.get_community_profile(cid)
    assert p["ideal_member_ai_confident"] is None


# -- Admin submit route: carry-forward vs. fresh confidence ------------------

@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    yield appmod
    if os.path.exists(db):
        os.remove(db)


def _client(appmod):
    from fastapi.testclient import TestClient
    return TestClient(appmod.app, raise_server_exceptions=True)


def _login(client):
    r = client.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    assert r.status_code in (302, 303)


def test_profile_submit_saves_confidence_for_freshly_drafted_field(env):
    from linklib.db import Library
    lib_ = Library(os.environ["LINKLIB_DB"])
    cid = lib_.add_community(name="Acme Circle", url="https://acme.example",
                             demographic="CFOs", cost_band="Free", categories=[], approved=1)
    lib_.close()

    client = _client(env)
    _login(client)
    r = client.post(f"/admin/tools/communities/{cid}/profile", data={
        "ideal_member": "Seed-stage CFOs.", "verdict_summary": "Best for X.",
        "ai_drafted_fields": "ideal_member,verdict_summary",
        "ai_drafted_confidence": "ideal_member:1,verdict_summary:0",
    }, follow_redirects=False)
    assert r.status_code == 303

    lib_ = Library(os.environ["LINKLIB_DB"])
    p = lib_.get_community_profile(cid)
    lib_.close()
    assert p["ideal_member_ai_confident"] == 1
    assert p["verdict_summary_ai_confident"] == 0
    assert p["needs_review"] == 1


def test_profile_submit_carries_forward_confidence_for_untouched_field(env):
    """Regenerating one field and saving must not blank out a DIFFERENT
    field's previously-recorded confidence — upsert_community_profile is a
    full replace, so the route itself must carry it forward."""
    from linklib.db import Library
    lib_ = Library(os.environ["LINKLIB_DB"])
    cid = lib_.add_community(name="Acme Circle", url="https://acme.example",
                             demographic="CFOs", cost_band="Free", categories=[], approved=1)
    lib_.upsert_community_profile(cid, ideal_member="Old draft.",
                                  confidence={"ideal_member": 1})
    lib_.close()

    client = _client(env)
    _login(client)
    # This save only (re)drafts verdict_summary — ideal_member is untouched.
    r = client.post(f"/admin/tools/communities/{cid}/profile", data={
        "ideal_member": "Old draft.", "verdict_summary": "Freshly drafted.",
        "ai_drafted_fields": "verdict_summary",
        "ai_drafted_confidence": "verdict_summary:0",
    }, follow_redirects=False)
    assert r.status_code == 303

    lib_ = Library(os.environ["LINKLIB_DB"])
    p = lib_.get_community_profile(cid)
    lib_.close()
    assert p["ideal_member_ai_confident"] == 1   # carried forward, not cleared
    assert p["verdict_summary_ai_confident"] == 0


def test_profile_submit_ignores_stray_confidence_for_hand_edited_field(env):
    from linklib.db import Library
    lib_ = Library(os.environ["LINKLIB_DB"])
    cid = lib_.add_community(name="Acme Circle", url="https://acme.example",
                             demographic="CFOs", cost_band="Free", categories=[], approved=1)
    lib_.close()

    client = _client(env)
    _login(client)
    r = client.post(f"/admin/tools/communities/{cid}/profile", data={
        "ideal_member": "Hand-written.",
        "ai_drafted_fields": "",   # nothing drafted this save
        "ai_drafted_confidence": "ideal_member:1",   # stray pair
    }, follow_redirects=False)
    assert r.status_code == 303

    lib_ = Library(os.environ["LINKLIB_DB"])
    p = lib_.get_community_profile(cid)
    lib_.close()
    assert p["ideal_member_ai_confident"] is None


# -- Display: gated on the shared needs_review flag --------------------------

def test_confidence_line_shown_on_profile_page_while_needs_review(env):
    from linklib.db import Library
    lib_ = Library(os.environ["LINKLIB_DB"])
    cid = lib_.add_community(name="Acme Circle", url="https://acme.example",
                             demographic="CFOs", cost_band="Free", categories=[], approved=1)
    lib_.upsert_community_profile(cid, ideal_member="Drafted.", needs_review=1,
                                  confidence={"ideal_member": 0})
    lib_.close()

    client = _client(env)
    _login(client)
    r = client.get(f"/admin/tools/communities/{cid}/profile")
    assert "Claude confidence: No" in r.text


def test_confidence_line_hidden_once_reviewed(env):
    from linklib.db import Library
    lib_ = Library(os.environ["LINKLIB_DB"])
    cid = lib_.add_community(name="Acme Circle", url="https://acme.example",
                             demographic="CFOs", cost_band="Free", categories=[], approved=1)
    lib_.upsert_community_profile(cid, ideal_member="Drafted.", needs_review=0,
                                  confidence={"ideal_member": 0})
    lib_.close()

    client = _client(env)
    _login(client)
    r = client.get(f"/admin/tools/communities/{cid}/profile")
    assert "Claude confidence" not in r.text
