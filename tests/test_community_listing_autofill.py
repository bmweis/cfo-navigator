"""Community directory-listing auto-populate ("Auto-fill from URL" on the
Add/Edit Community form) — the community-side equivalent of the CFO Toolbox
tool form's "Generate summary" description button.

Covers: linklib.enrich.generate_community_listing (unit, mocked Claude call),
the /admin/tools/communities/generate-listing route, and the NEEDS_VERIFICATION
sentinel rendering visibly (not hidden) on public-facing pages, flagged with the
"comm-verify" styling rather than looking like a confirmed value
(webapp/app.py::_verify_html — the old _public_community() choke point that used
to blank this sentinel was confirmed a true no-op and retired outright in
Gate-Extraction PR B; see that PR's own tests for the retirement itself).
"""
import pathlib
import sys
import tempfile
import types
import os

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import enrich


def _mock_anthropic(monkeypatch, payload_json, input_tokens=100, output_tokens=40):
    def _create(**kw):
        class _Block:
            type = "text"
            text = payload_json
        usage = types.SimpleNamespace(
            input_tokens=input_tokens, output_tokens=output_tokens,
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
        )
        return types.SimpleNamespace(content=[_Block()], usage=usage)
    fake = types.SimpleNamespace(Anthropic=lambda *a, **k: types.SimpleNamespace(
        messages=types.SimpleNamespace(create=lambda **kw: _create(**kw))))
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")


LONG_PAGE_CONTENT = (
    "This community brings together finance leaders at growth-stage companies for peer "
    "learning, tactical playbooks, and a private space to compare notes on the same "
    "problems everyone in the role eventually runs into. Members meet in small groups, "
    "attend regular virtual sessions, and get access to a shared library of templates and "
    "benchmarking data contributed by the group itself rather than a vendor. It's positioned "
    "as a working peer group for people already doing the job, not a general networking event."
)


def _mock_fetch_page(monkeypatch, content=LONG_PAGE_CONTENT):
    """2026-09 JS-render grounding fix: needs raw_html/blocked/fetch_error
    too, not just content — see test_description_citations.py's identical
    helper. `content` must be >=60 words for a "successful direct fetch"
    test — the default (LONG_PAGE_CONTENT) already clears it."""
    from linklib import extract
    page = types.SimpleNamespace(content=content, raw_html=content, blocked=False, fetch_error="")
    monkeypatch.setattr(extract, "fetch_page", lambda url, **kw: page)


def _mock_exa_fallback(monkeypatch, text="", cost=0.0):
    from linklib import medium_platform
    monkeypatch.setattr(medium_platform, "fetch_content_by_url", lambda lib, url: (text, cost))


REACH = ["Regional", "National", "Global"]
COST_BANDS = ["Free", "Undisclosed dues", "<$1k/yr", "<$2,500/yr", "$2,500+/yr"]
SPONSORSHIP = ["Independent", "Vendor-sponsored", "Investor-sponsored"]
ACCESS = ["Open", "Application", "Invite-only", "Qualification-based"]
FORMAT = ["Hybrid", "In-person", "Slack", "Online", "LinkedIn group"]
CATEGORIES = ["FP&A", "Treasury"]


def test_generate_listing_fills_confident_fields(monkeypatch):
    _mock_fetch_page(monkeypatch)
    _mock_anthropic(monkeypatch, """{
        "demographic": "CFOs at Series B+ SaaS companies",
        "reach": "National",
        "local_markets": "Boston",
        "cost_band": "Free",
        "cost_note": "",
        "sponsorship_type": "Independent",
        "sponsor_name": "",
        "access": "Invite-only",
        "format": "Slack",
        "categories": ["FP&A"]
    }""")
    draft = enrich.generate_community_listing(
        "Test Community", "https://example.com",
        reach_options=REACH, cost_band_options=COST_BANDS,
        sponsorship_options=SPONSORSHIP, access_options=ACCESS, format_options=FORMAT,
        category_options=CATEGORIES, voice_core="Test voice guide.",
    )
    assert draft is not None
    assert draft.reach == "National"
    assert draft.local_markets == "Boston"
    assert draft.cost_band == "Free"
    assert draft.access == "Invite-only"
    assert draft.format == "Slack"
    assert draft.categories == ["FP&A"]
    assert draft.low_confidence is False
    assert draft.cost_usd > 0


def test_generate_listing_uses_needs_verification_sentinel_for_unclear_fields(monkeypatch):
    _mock_fetch_page(monkeypatch)
    _mock_anthropic(monkeypatch, """{
        "demographic": "Needs verification",
        "reach": "Needs verification",
        "local_markets": "",
        "cost_band": "Needs verification",
        "cost_note": "",
        "sponsorship_type": "Needs verification",
        "sponsor_name": "",
        "access": "Needs verification",
        "format": "Needs verification",
        "categories": []
    }""")
    draft = enrich.generate_community_listing(
        "Obscure Community", "https://example.com",
        reach_options=REACH, cost_band_options=COST_BANDS,
        sponsorship_options=SPONSORSHIP, access_options=ACCESS, format_options=FORMAT,
        category_options=CATEGORIES, voice_core="Test voice guide.",
    )
    assert draft is not None
    assert draft.reach == enrich.NEEDS_VERIFICATION
    assert draft.cost_band == enrich.NEEDS_VERIFICATION
    assert draft.sponsorship_type == enrich.NEEDS_VERIFICATION
    assert draft.access == enrich.NEEDS_VERIFICATION
    assert draft.format == enrich.NEEDS_VERIFICATION
    assert draft.local_markets == ""
    assert draft.categories == []


def test_generate_listing_rejects_hallucinated_enum_values(monkeypatch):
    # A model that ignores the controlled vocabulary must not poison the
    # form with a value that doesn't correspond to any real <option> or
    # checkbox — validated back out to "" rather than trusted verbatim.
    # local_markets has no controlled vocabulary (free text), so it isn't
    # part of this check.
    _mock_fetch_page(monkeypatch)
    _mock_anthropic(monkeypatch, """{
        "demographic": "CFOs",
        "reach": "Worldwide",
        "local_markets": "",
        "cost_band": "Very expensive",
        "cost_note": "",
        "sponsorship_type": "Government-sponsored",
        "sponsor_name": "",
        "access": "Open to all",
        "format": "",
        "categories": ["Not A Real Category"]
    }""")
    draft = enrich.generate_community_listing(
        "Test Community", "https://example.com",
        reach_options=REACH, cost_band_options=COST_BANDS,
        sponsorship_options=SPONSORSHIP, access_options=ACCESS, format_options=FORMAT,
        category_options=CATEGORIES, voice_core="Test voice guide.",
    )
    assert draft is not None
    assert draft.reach == ""
    assert draft.cost_band == ""
    assert draft.sponsorship_type == ""
    assert draft.access == ""
    assert draft.categories == []


def test_generate_listing_low_confidence_when_fetched_via_exa(monkeypatch):
    """2026-09 JS-render grounding fix: a too-thin direct fetch falls back
    to Exa; low_confidence=True when Exa is what actually grounded the
    draft (a second-choice route), not "no grounding at all" — see
    test_generate_listing_raises_when_fetch_totally_fails for the case
    where nothing at all could be recovered."""
    _mock_fetch_page(monkeypatch, content="")
    _mock_exa_fallback(monkeypatch, LONG_PAGE_CONTENT, cost=0.007)
    _mock_anthropic(monkeypatch, """{
        "demographic": "Needs verification", "reach": "Needs verification", "local_markets": "",
        "cost_band": "Needs verification", "cost_note": "", "sponsorship_type": "Needs verification",
        "sponsor_name": "", "access": "Needs verification", "format": "Needs verification", "categories": []
    }""")
    draft = enrich.generate_community_listing(
        "Test Community", "https://example.com",
        reach_options=REACH, cost_band_options=COST_BANDS,
        sponsorship_options=SPONSORSHIP, access_options=ACCESS, format_options=FORMAT,
        category_options=CATEGORIES, voice_core="Test voice guide.",
    )
    assert draft is not None
    assert draft.low_confidence is True
    assert draft.exa_cost_usd == 0.007


def test_generate_listing_raises_when_fetch_totally_fails(monkeypatch):
    """The non-negotiable refusal (2026-09 JS-render grounding fix): when
    neither the direct fetch nor Exa can produce anything usable,
    generate_community_listing raises GroundingUnavailable rather than
    drafting a hedge from the model's own knowledge."""
    _mock_fetch_page(monkeypatch, content="")
    _mock_exa_fallback(monkeypatch, "", cost=0.0)
    _mock_anthropic(monkeypatch, """{
        "demographic": "Needs verification", "reach": "Needs verification", "local_markets": "",
        "cost_band": "Needs verification", "cost_note": "", "sponsorship_type": "Needs verification",
        "sponsor_name": "", "access": "Needs verification", "format": "Needs verification", "categories": []
    }""")
    with pytest.raises(enrich.GroundingUnavailable):
        enrich.generate_community_listing(
            "Test Community", "https://example.com",
            reach_options=REACH, cost_band_options=COST_BANDS,
            sponsorship_options=SPONSORSHIP, access_options=ACCESS, format_options=FORMAT,
            category_options=CATEGORIES, voice_core="Test voice guide.",
        )


def test_generate_listing_without_api_key_returns_none(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    draft = enrich.generate_community_listing(
        "Test Community", "https://example.com",
        reach_options=REACH, cost_band_options=COST_BANDS,
        sponsorship_options=SPONSORSHIP, access_options=ACCESS, format_options=FORMAT,
        category_options=CATEGORIES, voice_core="Test voice guide.",
    )
    assert draft is None


def test_generate_listing_aborts_when_voice_core_empty(monkeypatch):
    # Same "(a) injected, hard-fail if empty" contract as every other
    # generate_* helper — a direct caller that skips require_voice_setting
    # (bypassing the normal webapp/app.py resolution) must not silently
    # draft with no voice guidance, and must not pay for a page fetch it's
    # about to discard.
    _mock_fetch_page(monkeypatch)
    _mock_anthropic(monkeypatch, "{}")
    draft = enrich.generate_community_listing(
        "Test Community", "https://example.com",
        reach_options=REACH, cost_band_options=COST_BANDS,
        sponsorship_options=SPONSORSHIP, access_options=ACCESS, format_options=FORMAT,
        category_options=CATEGORIES,
    )
    assert draft is None


# --- webapp route + public-render scrub -------------------------------------

@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    # The generate-listing route now refuses (require_voice_setting) unless
    # voice_core is seeded — same convention as
    # tests/test_community_profile_citations.py's app_module fixture.
    from linklib.db import Library
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


def test_generate_listing_route_requires_auth(env):
    c = _client(env)
    r = c.post("/admin/tools/communities/generate-listing",
               json={"name": "Test Community", "url": "https://example.com"})
    assert r.status_code == 401


def test_generate_listing_route_returns_draft(env, monkeypatch):
    _mock_fetch_page(monkeypatch)
    _mock_anthropic(monkeypatch, """{
        "demographic": "CFOs at Series B+ SaaS companies", "reach": "National", "local_markets": "",
        "cost_band": "Free", "cost_note": "", "sponsorship_type": "Independent", "sponsor_name": "",
        "access": "Invite-only", "format": "Slack", "categories": []
    }""")
    c = _client(env)
    c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    r = c.post("/admin/tools/communities/generate-listing",
               json={"name": "Test Community", "url": "https://example.com"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["cost_band"] == "Free"
    assert body["low_confidence"] is False


def test_generate_listing_route_503_when_unavailable(env, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    c = _client(env)
    c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    r = c.post("/admin/tools/communities/generate-listing",
               json={"name": "Test Community", "url": "https://example.com"})
    assert r.status_code == 503
    assert r.json()["ok"] is False


def test_needs_verification_sentinel_renders_flagged_on_public_directory(env):
    """Per Brian's call: unresearched fields render visibly with a "Needs
    verification" flag on public pages now, instead of being hidden — a
    visitor should see the flag, but styled distinctly (comm-verify) from a
    real confirmed value (comm-cost), never looking like a guessed fact."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    lib.add_community(
        "Gap Community", "https://example.com", enrich.NEEDS_VERIFICATION,
        enrich.NEEDS_VERIFICATION, [], access=enrich.NEEDS_VERIFICATION,
        format=enrich.NEEDS_VERIFICATION, reach=enrich.NEEDS_VERIFICATION,
        sponsorship_type=enrich.NEEDS_VERIFICATION, approved=1,
    )
    lib.close()

    c = _client(env)
    r = c.get("/tools/communities")
    assert r.status_code == 200
    # The directory card is JS-templated client-side from an embedded JSON
    # blob, so a plain (non-browser) request can't see the rendered DOM —
    # what it can confirm is that the sentinel isn't blanked out of that
    # JSON before it reaches the page.
    assert enrich.NEEDS_VERIFICATION in r.text
    assert 'function commVerify' in r.text  # the JS helper that renders the flag from it


def test_needs_verification_sentinel_visible_on_admin_table(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    lib.add_community(
        "Gap Community", "https://example.com", enrich.NEEDS_VERIFICATION,
        enrich.NEEDS_VERIFICATION, [], access=enrich.NEEDS_VERIFICATION,
        format=enrich.NEEDS_VERIFICATION, reach=enrich.NEEDS_VERIFICATION,
        sponsorship_type=enrich.NEEDS_VERIFICATION, approved=1,
    )
    lib.close()

    c = _client(env)
    c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    r = c.get("/admin/tools/communities")
    assert r.status_code == 200
    assert "need" in r.text and "verification" in r.text


def test_needs_verification_sentinel_flagged_on_profile_page(env):
    """The server-rendered profile page (unlike the JS-templated directory
    card) lets us assert the actual output: the sentinel renders inside the
    tp-verify-inline flag (Phase 3b's Details card), not styled as a
    confirmed value."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    community_id = lib.add_community(
        "Gap Community", "https://example.com", enrich.NEEDS_VERIFICATION,
        enrich.NEEDS_VERIFICATION, [], access=enrich.NEEDS_VERIFICATION,
        format=enrich.NEEDS_VERIFICATION, reach=enrich.NEEDS_VERIFICATION,
        sponsorship_type=enrich.NEEDS_VERIFICATION, approved=1,
    )
    slug = lib.get_community(community_id)["slug"]
    lib.close()

    c = _client(env)
    r = c.get(f"/tools/communities/{slug}")
    assert r.status_code == 200
    assert '<span class="tp-verify-inline">Needs verification</span>' in r.text


def test_needs_verification_sentinel_flagged_on_compare_page(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    gap_id = lib.add_community(
        "Gap Community", "https://example.com", enrich.NEEDS_VERIFICATION,
        enrich.NEEDS_VERIFICATION, [], access=enrich.NEEDS_VERIFICATION,
        format=enrich.NEEDS_VERIFICATION, reach=enrich.NEEDS_VERIFICATION,
        sponsorship_type=enrich.NEEDS_VERIFICATION, approved=1,
    )
    confirmed_id = lib.add_community(
        "Confirmed Community", "https://example.com/two", "CFOs at Series B+",
        "Free", [], access="Open", approved=1,
    )
    lib.close()

    c = _client(env)
    r = c.get(f"/tools/communities/compare?ids={gap_id},{confirmed_id}")
    assert r.status_code == 200
    assert '<span class="comm-verify"' in r.text
    assert 'class="comm-verify">Needs verification</span>' in r.text
    assert "Open" in r.text  # the confirmed community's real access value still renders normally


def test_community_geo_line_does_not_guess_when_reach_unverified(env):
    """Regression guard: _community_geo_line's fallback branch used to treat
    a truthy-but-unresearched reach as if it were empty, silently rendering
    a guessed "National · online" instead of flagging the gap."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    community_id = lib.add_community(
        "Gap Community", "https://example.com", "CFOs",
        "Free", [], reach=enrich.NEEDS_VERIFICATION, approved=1,
    )
    slug = lib.get_community(community_id)["slug"]
    lib.close()

    c = _client(env)
    r = c.get(f"/tools/communities/{slug}")
    assert r.status_code == 200
    assert "National &middot; online" not in r.text
    assert "Needs verification" in r.text
