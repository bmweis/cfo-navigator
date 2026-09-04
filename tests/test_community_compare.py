"""Communities Compare view (/tools/communities/compare):
side-by-side read of the directory-card fields plus the deep
community_profiles fields for 2-3 selected communities, graceful
degradation when a selected community has no profile row, and the
directory card's compare checkbox affordance.

Compare Redesign Phase 1 (2026-09) rebuilt this page on the shared
linklib/compare.py serializer — the flat 11-field list is gone, replaced
by the same 4 themed profile-page groups (compare.COMMUNITY_PROFILE_GROUPS)
plus a Bottom line section (verdict_summary) and a Key facts band
(Region/Access/Sponsor/Cost/Founded + shared/unique tags). See
linklib/compare.py's module docstring and ARCHITECTURE.md's Compare
section for the full write-up.
"""
import pathlib
import sys
import tempfile, os

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


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


def test_compare_renders_two_communities_side_by_side(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    c1 = lib.add_community("Community One", "https://example.com/one", "Finance leaders",
                            "Free", [], access="Invite-only", approved=1)
    c2 = lib.add_community("Community Two", "https://example.com/two", "Finance leaders",
                            "<$1k/yr", [], access="Open", approved=1)
    lib.upsert_community_profile(c1, ideal_member="Solo CFOs at Series A/B",
                                  verdict_summary="Great for scrappy operators.",
                                  business_model="Free-to-join funnel monetized via paid tiers.")
    lib.close()

    c = _client(env)
    r = c.get(f"/tools/communities/compare?ids={c1},{c2}")
    assert r.status_code == 200
    assert "Community One" in r.text
    assert "Community Two" in r.text
    assert "Solo CFOs at Series A/B" in r.text
    assert "Free-to-join funnel monetized via paid tiers." in r.text
    assert "Business model" in r.text


def test_compare_works_with_three_communities(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    ids = [
        lib.add_community(f"Community {n}", f"https://example.com/{n.lower()}", "Finance leaders",
                           "Free", [], approved=1)
        for n in ("A", "B", "C")
    ]
    lib.close()

    c = _client(env)
    r = c.get("/tools/communities/compare?ids=" + ",".join(str(i) for i in ids))
    assert r.status_code == 200
    for n in ("A", "B", "C"):
        assert f"Community {n}" in r.text


def test_compare_caps_at_three_even_if_more_ids_passed(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    ids = [
        lib.add_community(f"Community {n}", f"https://example.com/{n.lower()}", "Finance leaders",
                           "Free", [], approved=1)
        for n in ("A", "B", "C", "D")
    ]
    lib.close()

    c = _client(env)
    r = c.get("/tools/communities/compare?ids=" + ",".join(str(i) for i in ids))
    assert r.status_code == 200
    assert "Community A" in r.text
    assert "Community D" not in r.text


def test_compare_requires_at_least_two_ids(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    cid = lib.add_community("Solo Community", "https://example.com", "Finance leaders",
                             "Free", [], approved=1)
    lib.close()

    c = _client(env)
    r = c.get(f"/tools/communities/compare?ids={cid}")
    assert r.status_code == 200
    assert "Pick at least two" in r.text
    assert "Solo Community" not in r.text


def test_compare_ignores_unapproved_and_unknown_ids(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    approved = lib.add_community("Approved Community", "https://example.com/approved", "Finance leaders",
                                  "Free", [], approved=1)
    pending = lib.add_community("Pending Community", "https://example.com/pending", "Finance leaders",
                                 "Free", [], approved=0)
    lib.close()

    c = _client(env)
    r = c.get(f"/tools/communities/compare?ids={approved},{pending},99999")
    assert r.status_code == 200
    assert "Pick at least two" in r.text  # only one valid community survives


def test_directory_card_has_compare_checkbox(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    lib.add_community("Checkbox Community", "https://example.com", "Finance leaders",
                       "Free", [], approved=1)
    lib.close()

    c = _client(env)
    r = c.get("/tools/communities")
    assert r.status_code == 200
    assert "comm-compare-cb" in r.text
    assert "toggleCompareSelect" in r.text
    assert "/tools/communities/compare" in r.text


# -- Compare Redesign Phase 1: grouped sections, Key facts band, citations --

def test_compare_groups_fields_into_the_four_profile_page_themes(env):
    """The old flat 11-row list is gone — Compare now groups the same
    profile fields into the identical 4 themed sections the profile page's
    own COMMUNITY_PROFILE_GROUPS uses, plus a Bottom line section."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    c1 = lib.add_community("Community One", "https://example.com/one", "Finance leaders", "Free", [], approved=1)
    c2 = lib.add_community("Community Two", "https://example.com/two", "Finance leaders", "Free", [], approved=1)
    lib.close()

    r = _client(env).get(f"/tools/communities/compare?ids={c1},{c2}")
    for title in ("Key facts", "Bottom line", "Who it's for", "What you get", "How it works",
                  "Cost &amp; structure", "Similar communities"):
        assert f'cc-section" colspan="3">{title}</td>' in r.text, title


def test_compare_whole_group_empty_shows_group_placeholder(env):
    """A themed group with NO populated fields for either community shows
    the profile page's own "hasn't been researched yet" placeholder, not
    11 individually-empty rows."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    c1 = lib.add_community("Community One", "https://example.com/one", "Finance leaders", "Free", [], approved=1)
    c2 = lib.add_community("Community Two", "https://example.com/two", "Finance leaders", "Free", [], approved=1)
    lib.close()

    r = _client(env).get(f"/tools/communities/compare?ids={c1},{c2}")
    assert "Not yet documented." in r.text  # COMPARE_EMPTY_LABELS["community_profile_group"]


def test_compare_populated_group_shows_tier2_for_missing_subfield(env):
    """One field populated inside an otherwise-empty "Who it's for" group ->
    the populated field renders, and the still-empty sibling fields in that
    SAME group get the profile page's Tier-2 "No details available." inline
    (no admin suffix) rather than the whole-group placeholder."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    c1 = lib.add_community("Community One", "https://example.com/one", "Finance leaders", "Free", [], approved=1)
    c2 = lib.add_community("Community Two", "https://example.com/two", "Finance leaders", "Free", [], approved=1)
    lib.upsert_community_profile(c1, ideal_member="Solo CFOs at Series A/B.")
    lib.close()

    r = _client(env).get(f"/tools/communities/compare?ids={c1},{c2}")
    assert "Solo CFOs at Series A/B." in r.text
    assert "No details available." in r.text


def test_compare_key_facts_band_shows_region_access_sponsor_cost_and_tags(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    c1 = lib.add_community("Community One", "https://example.com/one", "Finance leaders", "<$1k/yr",
                            ["FP&A", "CFO"], access="Invite-only", approved=1)
    c2 = lib.add_community("Community Two", "https://example.com/two", "Finance leaders", "Free",
                            ["FP&A"], access="Open", approved=1)
    lib.close()

    r = _client(env).get(f"/tools/communities/compare?ids={c1},{c2}")
    assert "Invite-only" in r.text
    assert "&lt;$1k/yr" in r.text
    assert '<span class="cmp-tag cmp-tag-shared">FP&amp;A</span>' in r.text
    assert '<span class="cmp-tag cmp-tag-unique">CFO</span>' in r.text


def test_compare_key_fact_needs_verification_sentinel_shows_badge(env):
    from linklib.db import Library
    from linklib.enrich import NEEDS_VERIFICATION
    lib = Library(os.environ["LINKLIB_DB"])
    c1 = lib.add_community("Community One", "https://example.com/one", "Finance leaders",
                            NEEDS_VERIFICATION, [], approved=1)
    c2 = lib.add_community("Community Two", "https://example.com/two", "Finance leaders", "Free", [], approved=1)
    lib.close()

    r = _client(env).get(f"/tools/communities/compare?ids={c1},{c2}")
    assert '<span class="comm-verify">Needs verification</span>' in r.text


def test_compare_shows_working_citation_chips(env):
    """Dead citation markers fix: Communities' Bottom line field carries
    the whole profile draft's single shared citation set, same as the
    profile page's one Sources list."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    c1 = lib.add_community("Community One", "https://example.com/one", "Finance leaders", "Free", [], approved=1)
    c2 = lib.add_community("Community Two", "https://example.com/two", "Finance leaders", "Free", [], approved=1)
    lib.upsert_community_profile(c1, verdict_summary="Great for scrappy operators[1].")
    lib.set_entity_citations("community", c1, "community_profile",
                              [{"n": 1, "title": "Community homepage", "url": "https://example.com/one/about"}])
    lib.close()

    r = _client(env).get(f"/tools/communities/compare?ids={c1},{c2}")
    assert "Great for scrappy operators[1]." in r.text
    assert "Sources" in r.text
    assert 'href="https://example.com/one/about"' in r.text


def test_compare_narrative_field_preserves_line_breaks(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    c1 = lib.add_community("Community One", "https://example.com/one", "Finance leaders", "Free", [], approved=1)
    c2 = lib.add_community("Community Two", "https://example.com/two", "Finance leaders", "Free", [], approved=1)
    bullets = "- Monthly roundtable.\n- Quarterly in-person summit."
    lib.upsert_community_profile(c1, format_reality=bullets)
    lib.close()

    r = _client(env).get(f"/tools/communities/compare?ids={c1},{c2}")
    assert '<div class="cmp-clamp-inner">' in r.text
    assert "- Monthly roundtable.\n- Quarterly in-person summit." in r.text


def test_compare_shows_similar_communities_chip_list(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    c1 = lib.add_community("Community One", "https://example.com/one", "Finance leaders", "Free", [], approved=1)
    c2 = lib.add_community("Community Two", "https://example.com/two", "Finance leaders", "Free", [], approved=1)
    lib.add_community_competitor(c1, c2)
    c2_slug = lib.get_community(c2)["slug"]
    lib.close()

    r = _client(env).get(f"/tools/communities/compare?ids={c1},{c2}")
    assert "Similar communities" in r.text
    assert f'<a href="/tools/communities/{c2_slug}" class="cmp-chip"' in r.text


def test_compare_full_profile_link_per_entity(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    c1 = lib.add_community("Community One", "https://example.com/one", "Finance leaders", "Free", [], approved=1)
    c2 = lib.add_community("Community Two", "https://example.com/two", "Finance leaders", "Free", [], approved=1)
    c1_slug = lib.get_community(c1)["slug"]
    c2_slug = lib.get_community(c2)["slug"]
    lib.close()

    r = _client(env).get(f"/tools/communities/compare?ids={c1},{c2}")
    assert 'class="cmp-full-link">Full profile' in r.text
    assert f'/tools/communities/{c1_slug}' in r.text
    assert f'/tools/communities/{c2_slug}' in r.text


def test_compare_admin_sees_unverified_badge_on_pending_bottom_line(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    c1 = lib.add_community("Community One", "https://example.com/one", "Finance leaders", "Free", [], approved=1)
    c2 = lib.add_community("Community Two", "https://example.com/two", "Finance leaders", "Free", [], approved=1)
    lib.upsert_community_profile(c1, verdict_summary="A drafted verdict.", needs_review=1)
    lib.close()

    client = _client(env)
    r = client.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    assert r.status_code in (302, 303)
    r = client.get(f"/tools/communities/compare?ids={c1},{c2}")
    assert "A drafted verdict." in r.text
    assert "unverified, visible to visitors" in r.text
