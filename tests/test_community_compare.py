"""Communities Compare view (/tools/communities/compare, Phase 6):
side-by-side read of the directory-card fields plus the deep
community_profiles fields for 2-3 selected communities, graceful
degradation when a selected community has no profile row, and the
directory card's compare checkbox affordance.
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
    c1 = lib.add_community("Community One", "https://example.com", "", "Finance leaders",
                            "Free", [], access="Invite-only", approved=1)
    c2 = lib.add_community("Community Two", "https://example.com", "", "Finance leaders",
                            "<$1k/yr", [], access="Open", approved=1)
    lib.upsert_community_profile(c1, ideal_member="Solo CFOs at Series A/B",
                                  verdict_summary="Great for scrappy operators.")
    lib.close()

    c = _client(env)
    r = c.get(f"/tools/communities/compare?ids={c1},{c2}")
    assert r.status_code == 200
    assert "Community One" in r.text
    assert "Community Two" in r.text
    assert "Solo CFOs at Series A/B" in r.text
    assert "Not available yet" in r.text  # Community Two has no profile


def test_compare_works_with_three_communities(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    ids = [
        lib.add_community(f"Community {n}", "https://example.com", "", "Finance leaders",
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
        lib.add_community(f"Community {n}", "https://example.com", "", "Finance leaders",
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
    cid = lib.add_community("Solo Community", "https://example.com", "", "Finance leaders",
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
    approved = lib.add_community("Approved Community", "https://example.com", "", "Finance leaders",
                                  "Free", [], approved=1)
    pending = lib.add_community("Pending Community", "https://example.com", "", "Finance leaders",
                                 "Free", [], approved=0)
    lib.close()

    c = _client(env)
    r = c.get(f"/tools/communities/compare?ids={approved},{pending},99999")
    assert r.status_code == 200
    assert "Pick at least two" in r.text  # only one valid community survives


def test_directory_card_has_compare_checkbox(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    lib.add_community("Checkbox Community", "https://example.com", "", "Finance leaders",
                       "Free", [], approved=1)
    lib.close()

    c = _client(env)
    r = c.get("/tools/communities")
    assert r.status_code == 200
    assert "comm-compare-cb" in r.text
    assert "toggleCompareSelect" in r.text
    assert "/tools/communities/compare" in r.text
