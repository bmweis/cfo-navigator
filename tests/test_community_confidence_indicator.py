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
from tests.community_edit_helpers import post_profile, get_profile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import enrich
from linklib.db import Library


@pytest.fixture(autouse=True)
def _no_citation_policy(monkeypatch):
    """This file tests parsing, not citation policy (the retry and refusal
    rules are covered in tests/test_uncited_drafts.py), so a draft is
    accepted here whether or not it carries citations."""
    from linklib import enrich as _enrich
    monkeypatch.setattr(_enrich, "_run_cited_draft", lambda attempt, *a, **k: attempt())


def _mock_anthropic(monkeypatch, payload_text):
    def _create(**kw):
        class _Block:
            type = "text"
            text = payload_text
            citations = []
        usage = types.SimpleNamespace(
            input_tokens=100, output_tokens=80,
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
        )
        return types.SimpleNamespace(content=[_Block()], usage=usage)
    fake = types.SimpleNamespace(Anthropic=lambda *a, **k: types.SimpleNamespace(
        messages=types.SimpleNamespace(create=lambda **kw: _create(**kw))))
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")


# Plain-prose labeled-block format (post citation-fix) — replaces the old
# strict-JSON payload. Values mirror the old PROFILE_JSON fixture's content
# exactly, including the two null-placeholder fields (notable_members,
# public_criticism), which the plain-prose prompt represents as a literal
# "None reported."/"None publicly reported." word rather than JSON `null`.
PROFILE_TEXT = """IDEAL_MEMBER:
Seed-stage operator CFOs.
ANTI_FIT:
Late-stage teams.
VALUE_PROP:
Peer benchmarking.
FORMAT_REALITY:
Monthly virtual.
ENGAGEMENT_LEVEL:
High.
SPONSOR_RELATIONSHIP_NOTE:
No sponsors.
BUSINESS_MODEL:
Dues-funded.
APPLICATION_FRICTION:
Light vetting.
COST_VALUE_VERDICT:
Worth it.
NOTABLE_MEMBERS:
None reported.
PUBLIC_CRITICISM:
None publicly reported.
VERDICT_SUMMARY:
Best for seed-stage CFOs.
JOBS_PROGRAM:
No
RESOURCES_INCLUDED:
Benchmarking data
CPE_ELIGIBLE:
No
CONFIDENCE:
IDEAL_MEMBER: true
ANTI_FIT: true
VALUE_PROP: false
BUSINESS_MODEL: true
FORMAT_REALITY: true
ENGAGEMENT_LEVEL: false
SPONSOR_RELATIONSHIP_NOTE: true
APPLICATION_FRICTION: true
COST_VALUE_VERDICT: false
NOTABLE_MEMBERS: true
PUBLIC_CRITICISM: true
VERDICT_SUMMARY: false
"""


# -- linklib.enrich: confidence dict parsing --------------------------------

def test_generate_community_profile_parses_confidence_dict(monkeypatch):
    from linklib import extract
    LONG_TEXT = ("Real page text describing the community in enough detail to clear the "
             "site's own extraction quality floor for grounding purposes. " * 6)
    monkeypatch.setattr(extract, "fetch_page", lambda url, **kw: types.SimpleNamespace(
        content=LONG_TEXT, raw_html=LONG_TEXT, blocked=False, fetch_error=""))
    _mock_anthropic(monkeypatch, PROFILE_TEXT)

    draft = enrich.generate_community_profile("Acme Circle", "https://acme.example", voice_core="Test voice guide.")
    assert draft is not None
    assert draft.confidence["ideal_member"] is True
    assert draft.confidence["value_prop"] is False
    assert draft.confidence["verdict_summary"] is False
    # Exactly the 12 approved keys — no more, no less.
    assert set(draft.confidence) == set(enrich.COMMUNITY_CONFIDENCE_FIELDS)


def test_generate_community_profile_defaults_missing_confidence_to_false(monkeypatch):
    from linklib import extract
    LONG_TEXT = ("Real page text describing the community in enough detail to clear the "
             "site's own extraction quality floor for grounding purposes. " * 6)
    monkeypatch.setattr(extract, "fetch_page", lambda url, **kw: types.SimpleNamespace(
        content=LONG_TEXT, raw_html=LONG_TEXT, blocked=False, fetch_error=""))
    _mock_anthropic(monkeypatch, "IDEAL_MEMBER:\nX\nVERDICT_SUMMARY:\nY\n")   # no CONFIDENCE: block at all

    draft = enrich.generate_community_profile("Acme Circle", "https://acme.example", voice_core="Test voice guide.")
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
    r = post_profile(client, cid, data={
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
    r = post_profile(client, cid, data={
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


def test_regenerate_single_field_preserves_all_others_byte_for_byte(env):
    """The exact scenario the carry-forward logic exists for: regenerate ONE
    field on an existing profile and save. (a) That field's confidence must
    come from the fresh submission, not the stale carried-forward value.
    (b) Every one of the other 11 tracked fields' confidence must be
    unchanged from before the regeneration — not just the one or two fields
    the lighter test above happens to check, all eleven, and not merely
    "still non-null" but the exact prior value, alternating 0/1 so a bug
    that clobbers everything to a single constant would be caught too."""
    from linklib.db import Library

    lib_ = Library(os.environ["LINKLIB_DB"])
    cid = lib_.add_community(name="Acme Circle", url="https://acme.example",
                             demographic="CFOs", cost_band="Free", categories=[], approved=1)
    # Seed all 12 fields with a distinct, alternating 0/1 pattern so a bug
    # that overwrites everything with one constant is also caught.
    initial = {f: i % 2 for i, f in enumerate(enrich.COMMUNITY_CONFIDENCE_FIELDS)}
    field_text = {f: f"Original {f} text." for f in enrich.COMMUNITY_CONFIDENCE_FIELDS}
    lib_.upsert_community_profile(cid, confidence=initial, **field_text)
    lib_.close()

    regenerated_field = "cost_value_verdict"
    assert initial[regenerated_field] == 0   # sanity: fresh value below actually flips it
    fresh_value = 1

    client = _client(env)
    _login(client)
    form_data = dict(field_text)
    form_data[regenerated_field] = "Freshly regenerated text."
    form_data["ai_drafted_fields"] = regenerated_field
    form_data["ai_drafted_confidence"] = f"{regenerated_field}:{fresh_value}"
    r = post_profile(client, cid, data=form_data, follow_redirects=False)
    assert r.status_code == 303

    lib_ = Library(os.environ["LINKLIB_DB"])
    p = lib_.get_community_profile(cid)
    lib_.close()

    # (a) The regenerated field reflects the fresh submitted value, not the
    # stale carried-forward one.
    assert p[f"{regenerated_field}_ai_confident"] == fresh_value

    # (b) Every other tracked field's confidence is byte-for-byte identical
    # to what it was before this save.
    for f in enrich.COMMUNITY_CONFIDENCE_FIELDS:
        if f == regenerated_field:
            continue
        assert p[f"{f}_ai_confident"] == initial[f], (
            f"{f}_ai_confident changed from {initial[f]} to {p[f'{f}_ai_confident']} "
            f"on a save that only regenerated {regenerated_field}"
        )


def test_profile_submit_ignores_stray_confidence_for_hand_edited_field(env):
    from linklib.db import Library
    lib_ = Library(os.environ["LINKLIB_DB"])
    cid = lib_.add_community(name="Acme Circle", url="https://acme.example",
                             demographic="CFOs", cost_band="Free", categories=[], approved=1)
    lib_.close()

    client = _client(env)
    _login(client)
    r = post_profile(client, cid, data={
        "ideal_member": "Hand-written.",
        "ai_drafted_fields": "",   # nothing drafted this save
        "ai_drafted_confidence": "ideal_member:1",   # stray pair
    }, follow_redirects=False)
    assert r.status_code == 303

    lib_ = Library(os.environ["LINKLIB_DB"])
    p = lib_.get_community_profile(cid)
    lib_.close()
    assert p["ideal_member_ai_confident"] is None


# -- Display: permanent (2026-08 policy revision — no longer gated on the
# shared needs_review flag; Brian's explicit call: confidence is independent
# of review status and always visible). This also drops the earlier
# all-or-nothing flattening concern entirely — each field always shows its
# own real confidence value regardless of the profile's review status.

def test_confidence_line_shown_on_profile_page_regardless_of_needs_review(env):
    from linklib.db import Library
    lib_ = Library(os.environ["LINKLIB_DB"])
    cid = lib_.add_community(name="Acme Circle", url="https://acme.example",
                             demographic="CFOs", cost_band="Free", categories=[], approved=1)
    lib_.upsert_community_profile(cid, ideal_member="Drafted.", needs_review=1,
                                  confidence={"ideal_member": 0})
    lib_.close()

    client = _client(env)
    _login(client)
    r = get_profile(client, cid)
    assert "Claude confidence: No" in r.text


def test_confidence_line_still_shown_once_reviewed(env):
    """Distinct from the shared "Mark reviewed" button/checkbox, which do
    reflect needs_review — the confidence line itself stays visible either
    way, since it's an independent fact."""
    from linklib.db import Library
    lib_ = Library(os.environ["LINKLIB_DB"])
    cid = lib_.add_community(name="Acme Circle", url="https://acme.example",
                             demographic="CFOs", cost_band="Free", categories=[], approved=1)
    lib_.upsert_community_profile(cid, ideal_member="Drafted.", needs_review=0,
                                  confidence={"ideal_member": 0})
    lib_.close()

    client = _client(env)
    _login(client)
    r = get_profile(client, cid)
    assert "Claude confidence: No" in r.text


def test_confidence_line_shows_not_yet_assessed_when_no_signal_ever_reported(env):
    """2026-08 follow-up — see the tool-side test of the same name for the
    Abacum finding that motivated this: NULL now renders a third, distinct
    state instead of being hidden."""
    from linklib.db import Library
    lib_ = Library(os.environ["LINKLIB_DB"])
    cid = lib_.add_community(name="Acme Circle", url="https://acme.example",
                             demographic="CFOs", cost_band="Free", categories=[], approved=1)
    lib_.close()

    client = _client(env)
    _login(client)
    r = get_profile(client, cid)
    assert "Claude confidence: Not yet assessed" in r.text
