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
    client) is simply ignored, since the submit route hardcodes 0 now."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    a_slug = lib.get_tool(a)["slug"]
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post(f"/tools/software/{a_slug}/edit", data={
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


def test_profile_page_shows_homepage_not_yet_captured_caption(env):
    """A manually-pasted homepage URL with no capture timestamp — the old
    caption here was 'Homepage screenshot (no product screenshot available
    yet)', which anticipated this exact phase and is now obsolete wording."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    a_slug = lib.get_tool(a)["slug"]
    lib.update_tool_screenshot(a, "https://example.com/homepage.png", 0)
    lib.close()

    r = _client(env).get(f"/tools/software/{a_slug}")
    assert "https://example.com/homepage.png" in r.text
    assert "Homepage screenshot (not yet captured)" in r.text
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
