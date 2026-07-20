"""Public Community Profile page (/tools/communities/<slug>, Phase 3):
directory-card fields plus the deep community_profiles fields, a minimal
fallback when there's no profile, the native gap-collection form (Phase 5),
and the directory card linking into the new tab instead of straight to the
external URL.
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


def test_profile_page_renders_full_profile(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    cid = lib.add_community(
        "Test CFO Guild", "https://example.com", "", "Seed-stage finance leaders",
        "Free", ["Peer group"], access="Invite-only", approved=1,
    )
    lib.upsert_community_profile(
        cid, ideal_member="Solo CFOs at Series A/B", anti_fit="Enterprise CFOs",
        verdict_summary="Best for scrappy operators, not late-stage teams.",
        founded_year=2019, business_model="Gated subscription, insulated by design.",
    )
    community = lib.get_community(cid)
    lib.close()

    c = _client(env)
    r = c.get(f"/tools/communities/{community['slug']}")
    assert r.status_code == 200
    assert "Test CFO Guild" in r.text
    assert "Solo CFOs at Series A/B" in r.text
    assert "Bottom line" in r.text
    assert "Best for scrappy operators" in r.text
    assert "2019" in r.text
    assert "Tell us why" in r.text
    assert f"/tools/communities/gap?community_id={cid}" in r.text
    assert "Gated subscription, insulated by design." in r.text
    assert "Business model" in r.text


def test_admin_profile_save_persists_all_weight_tag_dimensions(env):
    """Regression test: the admin profile edit POST handler hardcodes one
    form.getlist(...) line per Recommender weighting *_tags dimension (see
    webapp/app.py's admin_communities_profile_save-equivalent handler) —
    unlike the checkbox groups above it, which render generically off
    _WEIGHT_DIMENSIONS. A new dimension (e.g. function_tags, added in the
    Level/Function PR) is silently dropped on every save until its own
    getlist line is added, even though its checkbox renders and its column
    exists. Catches that class of bug for every current *_tags dimension."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    cid = lib.add_community(
        "Test CFO Guild", "https://example.com", "", "Seed-stage finance leaders",
        "Free", ["Peer group"], access="Invite-only", approved=1,
    )
    lib.upsert_community_profile(cid, ideal_member="Solo CFOs at Series A/B")
    lib.close()

    c = _client(env)
    c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    r = c.post(f"/admin/tools/communities/{cid}/profile", data={
        "ideal_member": "Solo CFOs at Series A/B",
        "seniority_band_tags": ["cfo", "senior_exec"],
        "function_tags": ["fpa"],
        "cpe_eligible_tags": ["yes"],
        "platform_type_tags": ["slack"],
        "looking_for_tags": ["peer_discussions", "vendor_connections"],
        "programming_tags": ["meals", "conferences"],
        "paid_free_tags": ["free", "paid"],
    }, follow_redirects=False)
    assert r.status_code == 303

    lib = Library(os.environ["LINKLIB_DB"])
    profile = lib.get_community_profile(cid)
    lib.close()
    assert profile["seniority_band_tags"] == ["cfo", "senior_exec"]
    assert profile["function_tags"] == ["fpa"]
    assert profile["cpe_eligible_tags"] == ["yes"]
    assert profile["platform_type_tags"] == ["slack"]
    assert profile["looking_for_tags"] == ["peer_discussions", "vendor_connections"]
    assert profile["programming_tags"] == ["meals", "conferences"]
    assert profile["paid_free_tags"] == ["free", "paid"]


def test_profile_page_falls_back_to_minimal_when_no_profile(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    cid = lib.add_community(
        "Bare Community", "https://example.com", "", "Finance leaders",
        "Free", [], approved=1,
    )
    community = lib.get_community(cid)
    lib.close()

    c = _client(env)
    r = c.get(f"/tools/communities/{community['slug']}")
    assert r.status_code == 200
    assert "Bare Community" in r.text
    assert "Bottom line" not in r.text


def test_profile_page_falls_back_when_profile_row_all_blank(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    cid = lib.add_community(
        "Empty Profile Row", "https://example.com", "", "Finance leaders",
        "Free", [], approved=1,
    )
    lib.upsert_community_profile(cid)  # all fields default to empty
    community = lib.get_community(cid)
    lib.close()

    c = _client(env)
    r = c.get(f"/tools/communities/{community['slug']}")
    assert r.status_code == 200
    assert "Bottom line" not in r.text


def test_profile_page_404_for_unknown_slug(env):
    c = _client(env)
    assert c.get("/tools/communities/does-not-exist").status_code == 404


def test_profile_page_404_for_unapproved_community(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    cid = lib.add_community(
        "Pending Community", "https://example.com", "", "Finance leaders",
        "Free", [], approved=0,
    )
    community = lib.get_community(cid)
    lib.close()

    c = _client(env)
    assert c.get(f"/tools/communities/{community['slug']}").status_code == 404


def test_directory_card_links_to_profile_page_in_new_tab(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    lib.add_community(
        "Linked Community", "https://example.com", "", "Finance leaders",
        "Free", [], approved=1,
    )
    lib.close()

    c = _client(env)
    r = c.get("/tools/communities")
    assert r.status_code == 200
    assert '/tools/communities/' in r.text
    assert "target=\\'_blank\\'" in r.text or "target='_blank'" in r.text or 'target="_blank"' in r.text


def test_gap_form_prefills_closest_match_from_community_id(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    cid = lib.add_community(
        "Gap Community", "https://example.com", "", "Finance leaders",
        "Free", [], approved=1,
    )
    lib.close()

    c = _client(env)
    r = c.get(f"/tools/communities/gap?community_id={cid}")
    assert r.status_code == 200
    assert "Gap Community" in r.text
    assert f'value="{cid}" selected' in r.text


def test_gap_form_with_no_session_state_shows_no_transparency_note(env):
    c = _client(env)
    r = c.get("/tools/communities/gap")
    assert r.status_code == 200
    assert "We noticed" not in r.text
    assert "came up empty" not in r.text


def test_gap_form_search_context_shows_transparency_note(env):
    c = _client(env)
    r = c.get("/tools/communities/gap?q=tax&region=Boston")
    assert r.status_code == 200
    assert "We noticed" in r.text
    assert "Boston" in r.text


def test_gap_form_zero_result_shows_empty_search_framing(env):
    c = _client(env)
    r = c.get("/tools/communities/gap?q=tax&zero=1")
    assert r.status_code == 200
    assert "came up empty" in r.text


def test_gap_form_mentions_viewed_profile_after_visiting_one(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    cid = lib.add_community(
        "Viewed Community", "https://example.com", "", "Finance leaders",
        "Free", [], approved=1,
    )
    lib.close()

    c = _client(env)
    profile_resp = c.get(f"/tools/communities/{_slug_for(env, cid)}")
    assert profile_resp.status_code == 200
    cookie = profile_resp.cookies.get("cfo_visitor")
    assert cookie

    c.cookies.set("cfo_visitor", cookie)
    r = c.get("/tools/communities/gap")
    assert r.status_code == 200
    assert "Viewed Community" in r.text
    assert "We noticed" in r.text


def _slug_for(env, community_id):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    c = lib.get_community(community_id)
    lib.close()
    return c["slug"]


def test_gap_form_closest_match_and_transparency_note_render_together(env):
    """closest_community_id (the per-profile 'Re: X' line) and the session
    transparency note are independent — neither suppresses the other when
    both a closest match and search/viewed context are present in the same
    session."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    closest_id = lib.add_community(
        "Closest Match Community", "https://example.com", "", "Finance leaders",
        "Free", [], approved=1,
    )
    other_id = lib.add_community(
        "Other Viewed Community", "https://example.com", "", "Finance leaders",
        "Free", [], approved=1,
    )
    lib.close()

    c = _client(env)
    # Visit a different community's profile first, so it's tracked as
    # "viewed" independently of the eventual closest-match pick.
    profile_resp = c.get(f"/tools/communities/{_slug_for(env, other_id)}")
    cookie = profile_resp.cookies.get("cfo_visitor")
    c.cookies.set("cfo_visitor", cookie)

    r = c.get(f"/tools/communities/gap?community_id={closest_id}&q=tax&region=Boston")
    assert r.status_code == 200

    # The per-profile "Re: X" line renders.
    assert "Re: <strong>Closest Match Community</strong>" in r.text
    assert "wasn&rsquo;t quite the right fit" in r.text

    # The session transparency note renders too, unsuppressed, mentioning
    # both the search context and the other viewed profile.
    assert "We noticed" in r.text
    assert "Boston" in r.text
    assert "Other Viewed Community" in r.text


def test_gap_form_submission_persists_and_records_server_side_viewed_ids(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    cid = lib.add_community(
        "Submit Target", "https://example.com", "", "Finance leaders",
        "Free", [], approved=1,
    )
    lib.close()

    c = _client(env)
    profile_resp = c.get(f"/tools/communities/{_slug_for(env, cid)}")
    cookie = profile_resp.cookies.get("cfo_visitor")
    c.cookies.set("cfo_visitor", cookie)

    r = c.post("/tools/communities/gap", data={
        "current_communities": "FEI",
        "gaps": "no regional presence",
        "looking_for": "mentorship",
        "closest_community_id": str(cid),
        "search_context_json": '{"q": "tax"}',
        "email": "visitor@example.com",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/tools/communities/gap?submitted=1"

    lib = Library(os.environ["LINKLIB_DB"])
    rows = lib.list_community_gap_submissions()
    lib.close()
    assert len(rows) == 1
    assert rows[0]["gaps"] == "no regional presence"
    assert rows[0]["closest_community_id"] == cid
    assert rows[0]["search_context_json"] == '{"q": "tax"}'
    assert rows[0]["viewed_community_ids_json"] == f"[{cid}]"
    assert rows[0]["reviewed"] == 0


def test_admin_community_gaps_requires_auth(env):
    c = _client(env)
    r = c.get("/admin/community-gaps", follow_redirects=False)
    assert r.status_code == 303
    assert "/login" in r.headers["location"]


def test_admin_community_gaps_triage_and_toggle(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    sub_id = lib.add_community_gap_submission(
        current_communities="FEI", gaps="no regional presence",
        looking_for="mentorship", email="visitor@example.com",
    )
    lib.close()

    c = _client(env)
    c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)

    r = c.get("/admin/community-gaps")
    assert r.status_code == 200
    assert "no regional presence" in r.text
    assert "1" in r.text  # unreviewed stat

    r2 = c.get("/admin/community-gaps?reviewed=no")
    assert "no regional presence" in r2.text
    r3 = c.get("/admin/community-gaps?reviewed=yes")
    assert "no regional presence" not in r3.text

    toggle = c.post(f"/admin/community-gaps/{sub_id}/toggle-reviewed", follow_redirects=False)
    assert toggle.status_code == 303

    lib = Library(os.environ["LINKLIB_DB"])
    row = lib.list_community_gap_submissions()[0]
    lib.close()
    assert row["reviewed"] == 1


def test_unreviewed_community_gap_feeds_admin_badge(env):
    from linklib.db import Library
    from webapp import tasks as _tasks
    lib = Library(os.environ["LINKLIB_DB"])
    lib.add_community_gap_submission(gaps="something missing")
    counts = _tasks.open_task_counts(lib)
    lib.close()
    assert counts.get("/admin/community-gaps") == 1
