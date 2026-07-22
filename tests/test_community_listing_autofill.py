"""Community directory-listing auto-populate ("Auto-fill from URL" on the
Add/Edit Community form) — the community-side equivalent of the CFO Toolbox
tool form's "Generate" description button.

Covers: linklib.enrich.generate_community_listing (unit, mocked Claude call),
the /admin/tools/communities/generate-listing route, and the NEEDS_VERIFICATION
sentinel never leaking to a public-facing page (webapp/app.py::_public_community).
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


def _mock_fetch_page(monkeypatch, content="Some community page content."):
    from linklib import extract
    page = types.SimpleNamespace(content=content)
    monkeypatch.setattr(extract, "fetch_page", lambda url, **kw: page)


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
        category_options=CATEGORIES,
    )
    assert draft is not None
    assert draft.demographic == "CFOs at Series B+ SaaS companies"
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
        category_options=CATEGORIES,
    )
    assert draft is not None
    assert draft.reach == enrich.NEEDS_VERIFICATION
    assert draft.cost_band == enrich.NEEDS_VERIFICATION
    assert draft.sponsorship_type == enrich.NEEDS_VERIFICATION
    assert draft.demographic == enrich.NEEDS_VERIFICATION
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
        category_options=CATEGORIES,
    )
    assert draft is not None
    assert draft.reach == ""
    assert draft.cost_band == ""
    assert draft.sponsorship_type == ""
    assert draft.access == ""
    assert draft.categories == []


def test_generate_listing_low_confidence_when_fetch_fails(monkeypatch):
    _mock_fetch_page(monkeypatch, content="")
    _mock_anthropic(monkeypatch, """{
        "demographic": "Needs verification", "reach": "Needs verification", "local_markets": "",
        "cost_band": "Needs verification", "cost_note": "", "sponsorship_type": "Needs verification",
        "sponsor_name": "", "access": "Needs verification", "format": "Needs verification", "categories": []
    }""")
    draft = enrich.generate_community_listing(
        "Test Community", "https://example.com",
        reach_options=REACH, cost_band_options=COST_BANDS,
        sponsorship_options=SPONSORSHIP, access_options=ACCESS, format_options=FORMAT,
        category_options=CATEGORIES,
    )
    assert draft is not None
    assert draft.low_confidence is True


def test_generate_listing_without_api_key_returns_none(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
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
    assert body["demographic"] == "CFOs at Series B+ SaaS companies"
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


def test_needs_verification_sentinel_never_reaches_public_directory(env):
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
    assert enrich.NEEDS_VERIFICATION not in r.text


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
