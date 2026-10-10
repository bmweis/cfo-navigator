"""Software profile page screenshot box (Phase 5 follow-up; captions
redesigned in Phase E for the dual homepage+app screenshot feature — see
tests/test_app_screenshot.py for the app-slot-specific coverage). A hotlinked
external image URL, rendered in a bordered sidebar box only when a URL is
actually set. screenshot_is_product is retired as of Phase E (its DB column
and update_tool_screenshot's parameter are left in place, non-destructively,
for legacy rows — see linklib/db.py — but the admin UI no longer exposes it
and the profile page no longer reads it for captioning)."""
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


def test_update_tool_screenshot(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.update_tool_screenshot(a, "https://example.com/shot.png", 1)
    tool = lib.get_tool(a)
    assert tool["screenshot_url"] == "https://example.com/shot.png"
    assert tool["screenshot_is_product"] == 1
    lib.close()


def test_admin_edit_saves_screenshot_fields(env):
    """Phase E: the admin edit form no longer submits screenshot_is_product
    (the checkbox was removed) — a POST that still includes it (e.g. a stale
    client) is simply ignored, since the submit route no longer reads it at
    all (see the regression test below for why "ignored" specifically means
    "never touches the column")."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    a_slug = lib.get_tool(a)["slug"]
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post(f"/tools/software/{a_slug}/edit", data={"primary_category": "FP&A", 
        "name": "Runway", "url": "https://runway.com", "description": "FP&A", "summary": "FP&A",
        "screenshot_url": "https://example.com/shot.png",
        "screenshot_is_product": "1",
    }, follow_redirects=False)
    assert r.status_code == 303

    lib = Library(os.environ["LINKLIB_DB"])
    tool = lib.get_tool(a)
    assert tool["screenshot_url"] == "https://example.com/shot.png"
    assert tool["screenshot_is_product"] == 0
    lib.close()


def test_admin_edit_save_does_not_clobber_legacy_product_flag(env):
    """Regression test (2026-08 incident): an unrelated full-form "Save
    changes" on a tool that still carries a legacy screenshot_is_product=1
    must NOT silently reset it to 0. The Phase E submit route originally
    called update_tool_screenshot with a hardcoded screenshot_is_product=0
    on every save regardless of what was actually edited — which meant a
    row could vanish from scripts/archive/migrate_app_screenshot_from_product_flag.py's
    preview between two runs, with no --apply in between, purely because an
    admin resaved the page for an unrelated reason. Fixed by having the
    submit route call the new update_tool_screenshot_url (which never
    touches screenshot_is_product) instead of update_tool_screenshot."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    a_slug = lib.get_tool(a)["slug"]
    lib.update_tool_screenshot(a, "https://example.com/product-shot.png", 1)  # legacy flag set
    lib.close()

    client = _client(env)
    _login(client)
    # A save that doesn't touch the screenshot section at all — just a
    # routine edit to an unrelated field.
    r = client.post(f"/tools/software/{a_slug}/edit", data={"primary_category": "FP&A", 
        "name": "Runway", "url": "https://runway.com", "description": "Updated description",
        "summary": "FP&A",
        "screenshot_url": "https://example.com/product-shot.png",
    }, follow_redirects=False)
    assert r.status_code == 303

    lib = Library(os.environ["LINKLIB_DB"])
    tool = lib.get_tool(a)
    assert tool["description"] == "Updated description"
    assert tool["screenshot_url"] == "https://example.com/product-shot.png"
    assert tool["screenshot_is_product"] == 1  # survives the unrelated save
    lib.close()


def test_profile_page_shows_homepage_captured_caption(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    a_slug = lib.get_tool(a)["slug"]
    lib.set_tool_screenshot_capture(a, "https://example.com/homepage.png")
    lib.close()

    r = _client(env).get(f"/tools/software/{a_slug}")
    assert r.status_code == 200
    assert "https://example.com/homepage.png" in r.text
    assert "Homepage screenshot, captured" in r.text
    assert "App screenshot" not in r.text
    assert "no product screenshot available" not in r.text.lower()


def test_profile_page_shows_homepage_manually_set_caption(env):
    """A manually-pasted homepage URL with no capture timestamp — the old
    caption here was 'Homepage screenshot (no product screenshot available
    yet)', which anticipated this exact phase and is now obsolete wording.

    Edit-page-fixes item 3 (2026-09): the caption briefly read "Homepage
    screenshot (not yet captured)" for this exact state, which is wrong — a
    real image is rendering right above it; the URL is present, just
    hand-pasted rather than machine-captured (Library.update_tool_screenshot
    always clears screenshot_captured_at, same as update_tool_screenshot_url).
    Now matches the edit page's own wording for this state exactly."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    a_slug = lib.get_tool(a)["slug"]
    lib.update_tool_screenshot(a, "https://example.com/homepage.png", 0)
    lib.close()

    r = _client(env).get(f"/tools/software/{a_slug}")
    assert "https://example.com/homepage.png" in r.text
    assert "Homepage screenshot, manually set—no capture date" in r.text
    assert "not yet captured" not in r.text
    assert "no product screenshot available" not in r.text.lower()


def test_profile_page_shows_placeholder_when_screenshot_unset(env):
    """Phase 3: the screenshot card always renders (layout completeness, per
    the mockup) — a tool with no screenshot gets a "No screenshot yet"
    placeholder rather than the card disappearing entirely."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Solo Co", "A tool with nothing set for the image field.", "https://solo.example", [], approved=1)
    a_slug = lib.get_tool(a)["slug"]
    lib.close()

    r = _client(env).get(f"/tools/software/{a_slug}")
    assert "No screenshot yet" in r.text
    assert "<img" not in r.text
