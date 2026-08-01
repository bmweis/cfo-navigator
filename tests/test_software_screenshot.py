"""Software profile page screenshot box (Phase 5 follow-up): a hotlinked
external image URL + a "this is a product screenshot vs. homepage fallback"
flag, rendered in a bordered sidebar box only when a URL is actually set.
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
    assert tool["screenshot_is_product"] == 1
    lib.close()


def test_profile_page_shows_product_screenshot_caption(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    a_slug = lib.get_tool(a)["slug"]
    lib.update_tool_screenshot(a, "https://example.com/product-shot.png", 1)
    lib.close()

    r = _client(env).get(f"/tools/software/{a_slug}")
    assert r.status_code == 200
    assert "https://example.com/product-shot.png" in r.text
    assert "Product screenshot" in r.text
    assert "no product screenshot available" not in r.text.lower()


def test_profile_page_shows_homepage_fallback_caption(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    a_slug = lib.get_tool(a)["slug"]
    lib.update_tool_screenshot(a, "https://example.com/homepage.png", 0)
    lib.close()

    r = _client(env).get(f"/tools/software/{a_slug}")
    assert "https://example.com/homepage.png" in r.text
    assert "no product screenshot available yet" in r.text.lower()


def test_profile_page_hides_screenshot_box_when_unset(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Solo Co", "A tool with nothing set for the image field.", "https://solo.example", [], approved=1)
    a_slug = lib.get_tool(a)["slug"]
    lib.close()

    r = _client(env).get(f"/tools/software/{a_slug}")
    assert "tool-profile-side" not in r.text
    assert "screenshot" not in r.text.lower()
