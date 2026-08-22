"""Software profile page (/tools/software/<slug>, Phase 2 of the search
overhaul): the first dedicated detail page for a Software entry — previously
only a card/modal existed. Covers get_tool_by_slug, the public route, the
404 for unapproved/unknown slugs, and the Warm Intro gating that mirrors the
directory card's rules.
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


def test_get_tool_by_slug_returns_approved_only():
    from linklib.db import Library
    db = tempfile.mktemp(suffix=".db")
    lib = Library(db)
    try:
        approved_id = lib.add_tool("Ramp", "Spend management", "https://ramp.com",
                                    ["Procurement/Spend"], approved=1)
        pending_id = lib.add_tool("Pending Co", "Not approved yet", "https://pending.example",
                                   [], approved=0)
        approved = lib.get_tool(approved_id)
        pending = lib.get_tool(pending_id)
        assert lib.get_tool_by_slug(approved["slug"])["name"] == "Ramp"
        assert lib.get_tool_by_slug(pending["slug"]) is None
        assert lib.get_tool_by_slug("nonexistent-slug") is None
    finally:
        lib.close()
        if os.path.exists(db):
            os.remove(db)


def test_profile_page_renders_full_description_and_categories(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    lib.add_tool("Ramp", "Corporate cards and expense management, in full detail.",
                 "https://ramp.com", ["Procurement/Spend", "Accounting"], approved=1)
    lib.close()

    r = _client(env).get("/tools/software/ramp")
    assert r.status_code == 200
    assert "Ramp" in r.text
    assert "Corporate cards and expense management, in full detail." in r.text
    assert "Procurement/Spend" in r.text
    assert "Accounting" in r.text


def test_profile_page_404s_for_unknown_slug(env):
    r = _client(env).get("/tools/software/does-not-exist")
    assert r.status_code == 404


def test_profile_page_404s_for_unapproved_tool(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    lib.add_tool("Pending Co", "Awaiting approval", "https://pending.example", [], approved=0)
    lib.close()

    r = _client(env).get("/tools/software/pending-co")
    assert r.status_code == 404


def test_profile_page_shows_disabled_intro_button_when_not_signed_in(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Vendor Co", "Has a vendor contact.", "https://vendor.example", [],
                      approved=1, warm_intro_enabled=1, vendor_email="sales@vendor.example")
    a_slug = lib.get_tool(a)["slug"]
    lib.close()

    r = _client(env).get(f"/tools/software/{a_slug}")
    assert r.status_code == 200
    assert "Warm intro" in r.text
    assert "disabled" in r.text
    assert "Sign in to request a warm intro" in r.text


def test_profile_page_hides_intro_button_without_warm_intro(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("No Intro Co", "No vendor contact set up.", "https://nointro.example", [],
                      approved=1)
    a_slug = lib.get_tool(a)["slug"]
    lib.close()

    r = _client(env).get(f"/tools/software/{a_slug}")
    assert r.status_code == 200
    assert "Warm intro" not in r.text


def test_directory_card_includes_slug_and_full_profile_link(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    lib.add_tool("Ramp", "Spend management", "https://ramp.com", [], approved=1)
    lib.close()

    r = _client(env).get("/tools/software")
    assert r.status_code == 200
    assert '"slug": "ramp"' in r.text or '"slug":"ramp"' in r.text
    assert "/tools/software/' + esc(t.slug)" in r.text
    assert "Full profile" in r.text
