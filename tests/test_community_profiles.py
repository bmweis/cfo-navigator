"""Public Community Profile page (/tools/communities/<slug>, Phase 3):
directory-card fields plus the deep community_profiles fields, a minimal
fallback when there's no profile, the "gap" CTA stub, and the directory
card linking into the new tab instead of straight to the external URL.
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
        founded_year=2019,
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


def test_gap_stub_redirects_to_contact_with_prefill(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    cid = lib.add_community(
        "Gap Community", "https://example.com", "", "Finance leaders",
        "Free", [], approved=1,
    )
    lib.close()

    c = _client(env)
    r = c.get(f"/tools/communities/gap?community_id={cid}", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/contact?message=")

    r2 = c.get(r.headers["location"])
    assert r2.status_code == 200
    assert "Gap Community" in r2.text


def test_gap_stub_without_community_id_still_redirects(env):
    c = _client(env)
    r = c.get("/tools/communities/gap", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/contact")
