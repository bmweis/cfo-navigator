"""Surface Hidden Community Profile Fields (2026-09) — Stage focus,
Jobs program, and Individual or team join `linklib.compare.
COMMUNITY_PROFILE_GROUPS`, so they render on the Community profile page
and Compare exactly like every other group field: verified/pending/empty,
same `gates.field_state` mechanism, off the same whole-profile
`needs_review` flag. No new gating logic — this file proves that claim
holds for these three specific fields at both render sites, plus the
three-state standard (verified/pending/empty) for each.

Step 0 found real content already stored for 36 of 40 production
communities, so the populated-state tests below are the common case, not
the exception; the empty-state tests cover the minority still blank.
"""
import os
import pathlib
import sys
import tempfile

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


def _login(client):
    r = client.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    assert r.status_code in (302, 303)


# -- linklib/compare.py: placement -------------------------------------------

def test_three_fields_are_in_the_expected_groups():
    from linklib.compare import COMMUNITY_PROFILE_GROUPS
    by_group = {title: [key for _, key in fields] for title, fields in COMMUNITY_PROFILE_GROUPS}
    assert "stage_focus" in by_group["Who it's for"]
    assert "jobs_program" in by_group["What you get"]
    assert "team_or_individual" in by_group["Cost & structure"]
    # Not duplicated into any other group.
    for title, keys in by_group.items():
        if title == "Who it's for":
            assert keys.count("stage_focus") == 1
        else:
            assert "stage_focus" not in keys
        if title == "What you get":
            assert keys.count("jobs_program") == 1
        else:
            assert "jobs_program" not in keys
        if title == "Cost & structure":
            assert keys.count("team_or_individual") == 1
        else:
            assert "team_or_individual" not in keys


# -- Profile page: verified state --------------------------------------------

def test_profile_page_renders_all_three_fields_verified(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    c = lib.add_community("Peer CFOs", "https://peercfos.example", "Series B+ CFOs",
                           "Free", [], approved=1)
    slug = lib.get_community(c)["slug"]
    lib.upsert_community_profile(
        c, ideal_member="Solo CFOs at growth-stage companies.",
        verdict_summary="Great fit for scrappy operators.",
        stage_focus="Growth-stage, venture-backed Series A-D",
        jobs_program="Yes, a dedicated talent arm alongside member recruiting support",
        team_or_individual="Individual only",
        needs_review=0,
    )
    lib.close()

    r = _client(env).get(f"/tools/communities/{slug}")
    assert r.status_code == 200
    assert "Stage focus" in r.text
    assert "Growth-stage, venture-backed Series A-D" in r.text
    assert "Jobs program" in r.text
    assert "Yes, a dedicated talent arm alongside member recruiting support" in r.text
    assert "Individual or team" in r.text
    assert "Individual only" in r.text
    # Verified: no pending badge anywhere on the page.
    assert "under review" not in r.text
    assert "unverified, visible to visitors" not in r.text


# -- Profile page: pending state ---------------------------------------------

def test_profile_page_renders_all_three_fields_pending_to_public(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    c = lib.add_community("Peer CFOs", "https://peercfos.example", "Series B+ CFOs",
                           "Free", [], approved=1)
    slug = lib.get_community(c)["slug"]
    lib.upsert_community_profile(
        c, ideal_member="Solo CFOs.", verdict_summary="Great fit.",
        stage_focus="Large public and late-stage multinationals",
        jobs_program="Career center and job board; no formal placement program",
        team_or_individual="Both, individual and corporate/team memberships",
        needs_review=1,
    )
    lib.close()

    r = _client(env).get(f"/tools/communities/{slug}")
    assert r.status_code == 200
    assert "Large public and late-stage multinationals" in r.text
    assert "Career center and job board; no formal placement program" in r.text
    assert "Both, individual and corporate/team memberships" in r.text
    assert "under review" in r.text
    assert "unverified, visible to visitors" not in r.text


def test_profile_page_renders_all_three_fields_pending_to_admin(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    c = lib.add_community("Peer CFOs", "https://peercfos.example", "Series B+ CFOs",
                           "Free", [], approved=1)
    slug = lib.get_community(c)["slug"]
    lib.upsert_community_profile(
        c, ideal_member="Solo CFOs.", verdict_summary="Great fit.",
        stage_focus="No particular stage focus",
        jobs_program="None",
        team_or_individual="Individual only",
        needs_review=1,
    )
    lib.close()

    client = _client(env)
    _login(client)
    r = client.get(f"/tools/communities/{slug}")
    assert r.status_code == 200
    assert "No particular stage focus" in r.text
    assert "unverified, visible to visitors" in r.text


# -- Profile page: empty state ------------------------------------------------

def test_profile_page_empty_fields_show_placeholder_within_populated_cards(env):
    """A gap in one of the three fields (like production community_id 20/27/
    39/40) shows Tier-2 muted placeholder text inside its otherwise-populated
    group card — not a hidden card, since the group as a whole has content."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    c = lib.add_community("Peer CFOs", "https://peercfos.example", "Series B+ CFOs",
                           "Free", [], approved=1)
    slug = lib.get_community(c)["slug"]
    lib.upsert_community_profile(
        c, ideal_member="Solo CFOs.", anti_fit="Not for enterprise teams.",
        seniority_band="CFO and VP Finance",
        verdict_summary="Great fit.",
        stage_focus="",  # the gap
        needs_review=0,
    )
    lib.close()

    r = _client(env).get(f"/tools/communities/{slug}")
    assert r.status_code == 200
    assert "Stage focus" in r.text
    assert "No details available." in r.text


def test_profile_page_whole_group_empty_shows_researched_yet_placeholder(env):
    """When an entire group (e.g. no Who-it's-for fields at all, including
    Stage focus) has zero content, the whole card collapses to the existing
    'This section hasn't been researched yet.' empty-state card."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    c = lib.add_community("Peer CFOs", "https://peercfos.example", "Series B+ CFOs",
                           "Free", [], approved=1)
    slug = lib.get_community(c)["slug"]
    lib.upsert_community_profile(c, verdict_summary="Great fit.", needs_review=0)
    lib.close()

    r = _client(env).get(f"/tools/communities/{slug}")
    assert r.status_code == 200
    assert "Who it's for" in r.text
    assert "This section hasn't been researched yet." in r.text


# -- Compare: verified / pending / empty -------------------------------------

def test_compare_renders_all_three_fields_verified(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_community("Peer CFOs", "https://peercfos.example", "Series B+ CFOs",
                           "Free", [], approved=1)
    b = lib.add_community("Finance Guild", "https://financeguild.example", "Late-stage CFOs",
                           "Free", [], approved=1)
    lib.upsert_community_profile(a, verdict_summary="Great fit.",
                                  stage_focus="Growth-stage, venture-backed",
                                  jobs_program="Yes, dedicated talent arm",
                                  team_or_individual="Individual only", needs_review=0)
    lib.upsert_community_profile(b, verdict_summary="Also great.", needs_review=0)
    lib.close()

    r = _client(env).get(f"/tools/communities/compare?ids={a},{b}")
    assert r.status_code == 200
    assert "Stage focus" in r.text
    assert "Growth-stage, venture-backed" in r.text
    assert "Jobs program" in r.text
    assert "Yes, dedicated talent arm" in r.text
    assert "Individual or team" in r.text
    assert "Individual only" in r.text


def test_compare_renders_pending_state_for_the_three_fields(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_community("Peer CFOs", "https://peercfos.example", "Series B+ CFOs",
                           "Free", [], approved=1)
    b = lib.add_community("Finance Guild", "https://financeguild.example", "Late-stage CFOs",
                           "Free", [], approved=1)
    lib.upsert_community_profile(a, verdict_summary="Great fit.",
                                  stage_focus="No particular stage focus", needs_review=1)
    lib.upsert_community_profile(b, verdict_summary="Also great.", needs_review=0)
    lib.close()

    r = _client(env).get(f"/tools/communities/compare?ids={a},{b}")
    assert r.status_code == 200
    assert "No particular stage focus" in r.text
    assert "under review" in r.text


def test_compare_renders_empty_state_for_the_three_fields(env):
    """A community with a profile row but none of the three fields populated
    still gets a row for the group (since another community in the compare
    has content there), with this entity's own cell reading as empty —
    consistent with every other Compare field."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_community("Peer CFOs", "https://peercfos.example", "Series B+ CFOs",
                           "Free", [], approved=1)
    b = lib.add_community("Finance Guild", "https://financeguild.example", "Late-stage CFOs",
                           "Free", [], approved=1)
    lib.upsert_community_profile(a, verdict_summary="Great fit.",
                                  jobs_program="Job board only", needs_review=0)
    lib.upsert_community_profile(b, verdict_summary="Also great.", needs_review=0)
    lib.close()

    r = _client(env).get(f"/tools/communities/compare?ids={a},{b}")
    assert r.status_code == 200
    assert "Job board only" in r.text
    assert "Not yet documented." in r.text


# -- Compare serializer: direct unit coverage --------------------------------

def test_build_communities_compare_states_for_the_three_fields():
    from linklib import compare as compare_mod, gates

    communities = [{"id": 1, "slug": "peer-cfos", "name": "Peer CFOs", "categories": []}]
    profiles = {
        1: {
            "needs_review": 1,
            "stage_focus": "Growth-stage",
            "jobs_program": "",
            "team_or_individual": "Individual only",
        }
    }
    entities, _ = compare_mod.build_communities_compare(communities, profiles, {}, {})
    who_group = next(s for s in entities[0].sections if s.title == "Who it's for")
    what_group = next(s for s in entities[0].sections if s.title == "What you get")
    cost_group = next(s for s in entities[0].sections if s.title == "Cost & structure")

    stage_field = next(f for f in who_group.fields if f.key == "stage_focus")
    assert stage_field.state == gates.GateState.PENDING
    assert stage_field.text == "Growth-stage"

    jobs_field = next(f for f in what_group.fields if f.key == "jobs_program")
    assert jobs_field.state == gates.GateState.EMPTY
    assert jobs_field.text == ""

    team_field = next(f for f in cost_group.fields if f.key == "team_or_individual")
    assert team_field.state == gates.GateState.PENDING
    assert team_field.text == "Individual only"
