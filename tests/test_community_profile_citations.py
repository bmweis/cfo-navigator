"""Citations-API grounding fix, Phase 3 — Community profile field. Same
pattern as Phase 2's Description (tests/test_description_citations.py):
covers linklib.enrich.generate_community_profile's real document-block
grounding (unit, mocked Claude call + mocked page fetch), the server-side
_validate_citations_payload guard on the browser-carried citations payload,
and the profile submit route that persists/clears entity_citations for
field_name='community_profile'.

Community profile is deliberately different from Description in one way
(decision 5, Phase 0): ONE shared citation set covers all 23 drafted
fields, not one row per field — so the tests here also cover that a
hand-edit to ANY of the 23 fields clears the set, and that the set persists
as exactly one row regardless of how many fields were actually (re)drafted.
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


def _mock_fetch_page(monkeypatch, content=""):
    """2026-09 JS-render grounding fix: needs raw_html/blocked/fetch_error
    too — see test_description_citations.py's identical helper. `content`
    must be >=60 words for a "successful direct fetch" test."""
    from linklib import extract
    monkeypatch.setattr(extract, "fetch_page", lambda url, **kw: types.SimpleNamespace(
        content=content, raw_html=content, blocked=False, fetch_error=""))


def _mock_exa_fallback(monkeypatch, text="", cost=0.0):
    from linklib import medium_platform
    monkeypatch.setattr(medium_platform, "fetch_content_by_url", lambda lib, url: (text, cost))


LONG_PAGE_CONTENT = (
    "Chief is a private membership network for senior executive women built to give them a "
    "confidential peer group of others operating at a similar level. Members join small "
    "curated groups called Cores that meet regularly to work through real leadership "
    "challenges together, alongside a broader calendar of programming, mentorship, and "
    "in-person events across several major cities. The network is positioned as a career "
    "accelerant for women already in senior roles, not an entry-level networking group."
)


def _mock_anthropic_citing(monkeypatch, blocks, input_tokens=200, output_tokens=150):
    """blocks: list of (text, cited_document_indexes) — one SDK text block
    per entry, each with its own `citations` list. Mirrors how the real
    Citations API actually splits a response into separate blocks at
    citation boundaries (a cited claim and uncited surrounding structure
    are different blocks, never one block carrying both). Also captures
    the actual messages.create kwargs so a test can assert on the document
    block actually sent."""
    captured = {}

    def _create(**kw):
        captured.update(kw)

        class _Citation:
            def __init__(self, idx):
                self.document_index = idx

        class _Block:
            def __init__(self, text, cited_indexes):
                self.type = "text"
                self.text = text
                self.citations = [_Citation(i) for i in cited_indexes]

        content = [_Block(text, cited) for text, cited in blocks]
        usage = types.SimpleNamespace(
            input_tokens=input_tokens, output_tokens=output_tokens,
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
        )
        return types.SimpleNamespace(content=content, usage=usage)
    fake = types.SimpleNamespace(Anthropic=lambda *a, **k: types.SimpleNamespace(
        messages=types.SimpleNamespace(create=lambda **kw: _create(**kw))))
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    return captured


# Plain prose + labeled blocks — see CLAUDE.md's citation-tag-investigation
# follow-up; this stopped being a JSON payload. PROFILE_BODY is the cited
# first field, PROFILE_TAIL the rest (uncited) — split the same way the
# merged Description/Agent taxonomy fix's fixtures are, matching real
# citation-boundary block splitting.
PROFILE_BODY = "IDEAL_MEMBER:\nSeed-stage operator CFOs."
PROFILE_TAIL = """

ANTI_FIT:
Public-company controllers.

VALUE_PROP:
Peer benchmarking and tactical playbooks.

FORMAT_REALITY:
Monthly virtual roundtables.

ENGAGEMENT_LEVEL:
Moderate, active in Slack, quieter at events.

SPONSOR_RELATIONSHIP_NOTE:
Sponsors present but not intrusive.

BUSINESS_MODEL:
Dues-funded, gated peer group.

APPLICATION_FRICTION:
Light vetting, most qualified applicants get in.

COST_VALUE_VERDICT:
Worth it for the network alone.

NOTABLE_MEMBERS:
None publicly reported.

FOUNDED_YEAR:
2019

PUBLIC_CRITICISM:
None reported.

VERDICT_SUMMARY:
Best for seed-stage operator CFOs, not for public-company controllers.

STAGE_FOCUS:
Growth-stage

JOBS_PROGRAM:
No

TEAM_OR_INDIVIDUAL:
Individual

SENIORITY_BAND:
CFO and VP Finance only

PRIMARY_PURPOSE:
Peer learning

RESOURCES_INCLUDED:
Templates, benchmarking data

PLATFORM_TYPE:
Slack

MEETING_FORMAT:
Virtual

EVENT_STYLE:
Intimate small-group

CPE_ELIGIBLE:
No

CONFIDENCE:
IDEAL_MEMBER: true
ANTI_FIT: true
VALUE_PROP: true
BUSINESS_MODEL: true
FORMAT_REALITY: true
ENGAGEMENT_LEVEL: false
SPONSOR_RELATIONSHIP_NOTE: true
APPLICATION_FRICTION: true
COST_VALUE_VERDICT: true
NOTABLE_MEMBERS: false
PUBLIC_CRITICISM: false
VERDICT_SUMMARY: true"""


# -- generate_community_profile: real document-block grounding ---------------

def test_generate_community_profile_sends_real_document_block(monkeypatch):
    _mock_fetch_page(monkeypatch, LONG_PAGE_CONTENT)
    captured = _mock_anthropic_citing(monkeypatch, [(PROFILE_BODY + PROFILE_TAIL, [])])

    enrich.generate_community_profile("Chief", "https://chief.com", voice_core="Test voice guide.")

    content = captured["messages"][0]["content"]
    assert isinstance(content, list)   # doc block + a trailing text block, not a bare string
    doc_blocks = [b for b in content if b.get("type") == "document"]
    assert len(doc_blocks) == 1
    assert doc_blocks[0]["citations"] == {"enabled": True}
    assert doc_blocks[0]["source"]["data"] == LONG_PAGE_CONTENT
    assert content[-1]["type"] == "text"   # drafting instructions ride last


def test_generate_community_profile_returns_verified_citations_tagged_community_page(monkeypatch):
    _mock_fetch_page(monkeypatch, LONG_PAGE_CONTENT)
    _mock_anthropic_citing(monkeypatch, [(PROFILE_BODY, [0]), (PROFILE_TAIL, [])])

    draft = enrich.generate_community_profile("Chief", "https://chief.com", voice_core="Test voice guide.")
    assert draft is not None
    assert len(draft.citations) == 1
    assert draft.citations[0]["url"] == "https://chief.com"
    assert draft.citations[0]["type"] == "community_page"
    # The cited field gets a real [n] marker spliced in by extract_citations
    # itself (inject_markers=True) — the new intended footnote rendering.
    assert "[1]" in draft.ideal_member
    assert "Seed-stage operator CFOs." in draft.ideal_member
    assert draft.low_confidence is False   # a real direct fetch, no Exa fallback needed


def test_generate_community_profile_confidence_still_parses_when_citations_present(monkeypatch):
    """The real risk this fix targets: citations enabled alongside the
    trailing CONFIDENCE: block. Confirms the sentinel block still parses
    correctly even with a real citation attached earlier in the response."""
    _mock_fetch_page(monkeypatch, LONG_PAGE_CONTENT)
    _mock_anthropic_citing(monkeypatch, [(PROFILE_BODY, [0]), (PROFILE_TAIL, [])])

    draft = enrich.generate_community_profile("Chief", "https://chief.com", voice_core="Test voice guide.")
    assert draft is not None
    assert draft.verdict_summary == "Best for seed-stage operator CFOs, not for public-company controllers."
    assert draft.confidence["ideal_member"] is True
    assert draft.confidence["notable_members"] is False


def test_generate_community_profile_low_confidence_and_still_cited_when_fetched_via_exa(monkeypatch):
    """2026-09 JS-render grounding fix: a too-thin direct fetch falls back
    to Exa; a successful recovery still rides as a real Citations-API
    document block — low_confidence=True (a second-choice route) but not
    ungrounded. See test_generate_community_profile_raises_when_direct_and_
    exa_both_unusable for the case where nothing at all could be found."""
    _mock_fetch_page(monkeypatch, "")
    _mock_exa_fallback(monkeypatch, LONG_PAGE_CONTENT, cost=0.007)
    _mock_anthropic_citing(monkeypatch, [(PROFILE_BODY, [0]), (PROFILE_TAIL, [])])

    draft = enrich.generate_community_profile("Chief", "https://chief.com", voice_core="Test voice guide.")
    assert draft is not None
    assert draft.low_confidence is True
    assert draft.exa_cost_usd == 0.007
    assert len(draft.citations) == 1


def test_generate_community_profile_raises_when_direct_and_exa_both_unusable(monkeypatch):
    """The non-negotiable refusal (2026-09 JS-render grounding fix): when
    neither the direct fetch nor Exa can produce anything usable,
    generate_community_profile raises GroundingUnavailable rather than
    drafting a hedge from the model's own knowledge — same reasoning as
    generate_tool_description's matching refusal."""
    _mock_fetch_page(monkeypatch, "")
    _mock_exa_fallback(monkeypatch, "", cost=0.0)
    _mock_anthropic_citing(monkeypatch, [(PROFILE_BODY, [0]), (PROFILE_TAIL, [])])

    with pytest.raises(enrich.GroundingUnavailable):
        enrich.generate_community_profile("Obscure Community", "https://obscure.example", voice_core="Test voice guide.")


# -- app fixture + client helpers ---------------------------------------------

@pytest.fixture
def app_module(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    # 2026-08 visibility follow-up: the generate-* routes now refuse
    # (require_voice_setting) unless voice_core is seeded.
    _seed_lib = Library(db)
    _seed_lib.seed_voice_prompts()
    _seed_lib.close()
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


def _add_community(lib):
    return lib.add_community(name="Chief", url="https://chief.com", demographic="Senior executive women",
                             cost_band="Paid", categories=[], approved=1)


# -- generate-profile AJAX route: returns citations + model ------------------

def test_generate_profile_route_returns_citations_and_model(app_module, monkeypatch):
    _mock_fetch_page(monkeypatch, LONG_PAGE_CONTENT)
    _mock_anthropic_citing(monkeypatch, [(PROFILE_BODY, [0]), (PROFILE_TAIL, [])])

    client = _client(app_module)
    _login(client)
    r = client.post("/admin/tools/communities/generate-profile",
                     json={"name": "Chief", "url": "https://chief.com"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert len(body["citations"]) == 1
    assert body["citations"][0]["url"] == "https://chief.com"
    assert body["model"]


# -- profile submit route: persist / clear, one row for the whole draft ------

def test_profile_submit_persists_one_citations_row_for_fresh_draft(app_module):
    lib = Library(os.environ["LINKLIB_DB"])
    community_id = _add_community(lib)
    lib.close()

    client = _client(app_module)
    _login(client)
    citations_json = '[{"n": 1, "title": "Chief", "url": "https://chief.com", "type": "community_page"}]'
    r = client.post(f"/admin/tools/communities/{community_id}/profile", data={
        "ideal_member": "Freshly generated ideal member.",
        "verdict_summary": "Freshly generated verdict.",
        # Only 2 of the 23 fields were actually (re)drafted this session —
        # decision 5 still expects exactly one shared citations row.
        "ai_drafted_fields": "ideal_member,verdict_summary",
        "ai_drafted_confidence": "ideal_member:1,verdict_summary:1",
        "ai_drafted_citations": citations_json,
        "ai_drafted_citations_model": "claude-opus-5",
    }, follow_redirects=False)
    assert r.status_code == 303

    lib = Library(os.environ["LINKLIB_DB"])
    citations = lib.get_entity_citations("community", community_id, "community_profile")
    assert len(citations) == 1
    assert citations[0]["url"] == "https://chief.com"
    profile = lib.get_community_profile(community_id)
    assert profile["needs_review"] == 1
    lib.close()


def test_profile_submit_hand_written_draft_records_no_citations(app_module):
    """A profile saved without ever clicking Generate (no ai_drafted_fields
    at all) — no citations, matching Description's own unaffected
    hand-written-content behavior."""
    lib = Library(os.environ["LINKLIB_DB"])
    community_id = _add_community(lib)
    lib.close()

    client = _client(app_module)
    _login(client)
    r = client.post(f"/admin/tools/communities/{community_id}/profile", data={
        "ideal_member": "Hand-written ideal member.",
        "verdict_summary": "Hand-written verdict.",
    }, follow_redirects=False)
    assert r.status_code == 303

    lib = Library(os.environ["LINKLIB_DB"])
    assert lib.get_entity_citations("community", community_id, "community_profile") == []
    lib.close()


def test_profile_submit_malformed_citations_payload_persists_nothing(app_module):
    lib = Library(os.environ["LINKLIB_DB"])
    community_id = _add_community(lib)
    lib.close()

    client = _client(app_module)
    _login(client)
    r = client.post(f"/admin/tools/communities/{community_id}/profile", data={
        "ideal_member": "Freshly generated ideal member.",
        "ai_drafted_fields": "ideal_member",
        "ai_drafted_citations": "not json",
    }, follow_redirects=False)
    assert r.status_code == 303

    lib = Library(os.environ["LINKLIB_DB"])
    assert lib.get_entity_citations("community", community_id, "community_profile") == []
    lib.close()


def test_profile_submit_clears_citations_on_hand_edit(app_module):
    """A save that doesn't follow a fresh Generate click on any of the 23
    fields — even one that changes a field's text by hand — clears any
    prior AI citations for the whole draft, same convention
    update_tool_agent_taxonomy/Description's submit routes already apply."""
    lib = Library(os.environ["LINKLIB_DB"])
    community_id = _add_community(lib)
    lib.set_entity_citations("community", community_id, "community_profile",
                             [{"n": 1, "title": "Old source", "url": "https://old.example", "type": "community_page"}],
                             model="claude-opus-5")
    lib.close()

    client = _client(app_module)
    _login(client)
    r = client.post(f"/admin/tools/communities/{community_id}/profile", data={
        "ideal_member": "Hand-edited ideal member, no Generate this session.",
        "verdict_summary": "Hand-edited verdict.",
    }, follow_redirects=False)
    assert r.status_code == 303

    lib = Library(os.environ["LINKLIB_DB"])
    assert lib.get_entity_citations("community", community_id, "community_profile") == []
    lib.close()


def test_profile_submit_clears_citations_when_unrelated_field_edited(app_module):
    """A hand-edit to just ONE of the 23 fields still clears the whole
    shared citation set — decision 5's "clear on hand-edit of any field in
    the set", not just the specific field that changed."""
    lib = Library(os.environ["LINKLIB_DB"])
    community_id = _add_community(lib)
    lib.upsert_community_profile(community_id, ideal_member="AI ideal member.",
                                 verdict_summary="AI verdict.", needs_review=1)
    lib.set_entity_citations("community", community_id, "community_profile",
                             [{"n": 1, "title": "Source", "url": "https://chief.com", "type": "community_page"}],
                             model="claude-opus-5")
    lib.close()

    client = _client(app_module)
    _login(client)
    # Only resources_included changes by hand; ideal_member/verdict_summary
    # are resubmitted unchanged, and no ai_drafted_fields this time.
    r = client.post(f"/admin/tools/communities/{community_id}/profile", data={
        "ideal_member": "AI ideal member.",
        "verdict_summary": "AI verdict.",
        "resources_included": "Hand-added resource list.",
    }, follow_redirects=False)
    assert r.status_code == 303

    lib = Library(os.environ["LINKLIB_DB"])
    assert lib.get_entity_citations("community", community_id, "community_profile") == []
    lib.close()


# -- rendering: public cap, admin uncapped, single shared list ---------------

def test_public_profile_shows_capped_sources_list_once(app_module):
    lib = Library(os.environ["LINKLIB_DB"])
    community_id = _add_community(lib)
    lib.upsert_community_profile(community_id, ideal_member="Ideal member.",
                                 verdict_summary="Best for X, not for Y.")
    citations = [{"n": i + 1, "title": f"Source {i}", "url": f"https://source{i}.example", "type": "community_page"}
                 for i in range(7)]
    lib.set_entity_citations("community", community_id, "community_profile", citations, model="claude-opus-5")
    slug = lib.get_community(community_id)["slug"]
    lib.close()

    client = _client(app_module)
    r = client.get(f"/tools/communities/{slug}")
    assert r.status_code == 200
    html = r.text
    assert html.count("source0.example") == 1
    assert "source6.example" not in html   # capped at 5 — the 7th source is dropped
    # One shared list, not one per card — the 5 shown sources appear exactly
    # once each, not repeated across the profile's multiple cards.
    assert html.count("source1.example") == 1


def test_admin_edit_page_shows_uncapped_sources_list(app_module):
    lib = Library(os.environ["LINKLIB_DB"])
    community_id = _add_community(lib)
    citations = [{"n": i + 1, "title": f"Source {i}", "url": f"https://source{i}.example", "type": "community_page"}
                 for i in range(7)]
    lib.set_entity_citations("community", community_id, "community_profile", citations, model="claude-opus-5")
    lib.close()

    client = _client(app_module)
    _login(client)
    r = client.get(f"/admin/tools/communities/{community_id}/profile")
    assert r.status_code == 200
    assert "source6.example" in r.text   # uncapped for the admin reviewer


def test_admin_edit_page_shows_empty_note_when_no_citations(app_module):
    lib = Library(os.environ["LINKLIB_DB"])
    community_id = _add_community(lib)
    lib.close()

    client = _client(app_module)
    _login(client)
    r = client.get(f"/admin/tools/communities/{community_id}/profile")
    assert r.status_code == 200
    assert "No citations recorded for this draft" in r.text


def test_community_profile_shown_under_review_to_public_when_unreviewed(app_module):
    """Superseded again by Brian's radical-transparency review standard
    (Gate-Extraction Phase 0/PR A, see CLAUDE.md): the Community profile's
    whole-profile hide-from-visitors gate (added in the earlier
    Description/Community profile publish-gate follow-up this docstring
    used to describe) is itself now superseded — an unreviewed
    (needs_review=1) draft always renders for every viewer, labeled "under
    review" for a visitor instead of hidden. See
    tests/test_review_state_publish_gates.py for the full coverage."""
    lib = Library(os.environ["LINKLIB_DB"])
    community_id = _add_community(lib)
    lib.upsert_community_profile(community_id, ideal_member="An unreviewed AI-drafted ideal member.",
                                 verdict_summary="Unreviewed verdict.", needs_review=1)
    slug = lib.get_community(community_id)["slug"]
    lib.close()

    client = _client(app_module)   # no login — a signed-out public visitor
    r = client.get(f"/tools/communities/{slug}")
    assert r.status_code == 200
    assert "An unreviewed AI-drafted ideal member." in r.text
    assert "under review" in r.text
    assert "unverified, visible to visitors" not in r.text
