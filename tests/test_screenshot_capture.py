"""Homepage screenshot capture (Software search overhaul, screenshot
follow-up): linklib.screenshots.capture_homepage (mocked Playwright — no
real browser/network calls in tests), the two DB write paths (manual paste
vs. automated capture), the serving route, the live "Recapture" admin
button, and scripts/capture_tool_screenshots.py's selection/skip/dry-run
logic.
"""
import os
import pathlib
import sys
import tempfile
import types

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import screenshots
from linklib.db import Library


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


def _mock_playwright_success(monkeypatch):
    written = {}

    class _Page:
        def goto(self, url, timeout=None, wait_until=None):
            written["url"] = url

        def wait_for_timeout(self, ms):
            pass

        def screenshot(self, path):
            written["path"] = path
            with open(path, "wb") as f:
                f.write(b"fake-png-bytes")

    class _Browser:
        def new_page(self, viewport=None):
            return _Page()

        def close(self):
            pass

    class _Chromium:
        def launch(self):
            return _Browser()

    class _PW:
        def __enter__(self):
            return types.SimpleNamespace(chromium=_Chromium())

        def __exit__(self, *a):
            return False

    fake = types.SimpleNamespace(sync_playwright=lambda: _PW())
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake)
    return written


def _mock_playwright_failure(monkeypatch):
    class _PW:
        def __enter__(self):
            raise RuntimeError("net::ERR_TUNNEL_CONNECTION_FAILED")

        def __exit__(self, *a):
            return False

    fake = types.SimpleNamespace(sync_playwright=lambda: _PW())
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake)


# -- linklib.screenshots -------------------------------------------------------

def test_capture_homepage_success(monkeypatch, tmp_path):
    written = _mock_playwright_success(monkeypatch)
    dest = str(tmp_path / "shot.png")
    ok = screenshots.capture_homepage("https://runway.com", dest)
    assert ok is True
    assert os.path.isfile(dest)
    assert written["url"] == "https://runway.com"


def test_capture_homepage_failure_returns_false(monkeypatch, tmp_path):
    _mock_playwright_failure(monkeypatch)
    dest = str(tmp_path / "shot.png")
    ok = screenshots.capture_homepage("https://blocked.example", dest)
    assert ok is False
    assert not os.path.isfile(dest)


def test_capture_homepage_creates_parent_dirs(monkeypatch, tmp_path):
    _mock_playwright_success(monkeypatch)
    dest = str(tmp_path / "nested" / "dir" / "shot.png")
    ok = screenshots.capture_homepage("https://runway.com", dest)
    assert ok is True
    assert os.path.isfile(dest)


# -- linklib.db ----------------------------------------------------------------

def test_set_tool_screenshot_capture_forces_homepage_only(tmp_path):
    lib = Library(str(tmp_path / "t.db"))
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.update_tool_screenshot(a, "https://example.com/manual.png", 1)  # marked as product
    lib.set_tool_screenshot_capture(a, "/tools/software/screenshot/runway.png")
    tool = lib.get_tool(a)
    assert tool["screenshot_url"] == "/tools/software/screenshot/runway.png"
    assert tool["screenshot_is_product"] == 0   # auto-capture always resets this
    assert tool["screenshot_captured_at"]        # timestamp set
    lib.close()


def test_manual_update_clears_captured_at(tmp_path):
    lib = Library(str(tmp_path / "t.db"))
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.set_tool_screenshot_capture(a, "/tools/software/screenshot/runway.png")
    assert lib.get_tool(a)["screenshot_captured_at"]
    lib.update_tool_screenshot(a, "https://example.com/replaced.png", 0)
    tool = lib.get_tool(a)
    assert tool["screenshot_url"] == "https://example.com/replaced.png"
    assert tool["screenshot_captured_at"] == ""
    lib.close()


# -- serving route --------------------------------------------------------------

def test_screenshot_serving_route(env, tmp_path, monkeypatch):
    screenshot_dir = os.path.join(os.path.dirname(os.path.abspath(os.environ["LINKLIB_DB"])), "tool_screenshots")
    os.makedirs(screenshot_dir, exist_ok=True)
    with open(os.path.join(screenshot_dir, "runway.png"), "wb") as f:
        f.write(b"fake-png")

    r = _client(env).get("/tools/software/screenshot/runway.png")
    assert r.status_code == 200
    assert r.content == b"fake-png"


def test_screenshot_serving_route_404_for_missing_file(env):
    r = _client(env).get("/tools/software/screenshot/does-not-exist.png")
    assert r.status_code == 404


def test_screenshot_serving_route_blocks_traversal(env, tmp_path):
    screenshot_dir = os.path.join(os.path.dirname(os.path.abspath(os.environ["LINKLIB_DB"])), "tool_screenshots")
    os.makedirs(screenshot_dir, exist_ok=True)
    r = _client(env).get("/tools/software/screenshot/..%2F..%2Fetc%2Fpasswd")
    assert r.status_code in (404, 400)


# -- live recapture admin button ------------------------------------------------

def test_admin_recapture_success(env, monkeypatch):
    written = _mock_playwright_success(monkeypatch)
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post(f"/admin/tools/{a}/screenshot/recapture", follow_redirects=False)
    assert r.status_code == 303
    assert "screenshot_captured=1" in r.headers["location"]

    lib = Library(os.environ["LINKLIB_DB"])
    tool = lib.get_tool(a)
    assert tool["screenshot_url"]
    assert tool["screenshot_captured_at"]
    assert tool["screenshot_is_product"] == 0
    lib.close()

    r = client.get(f"/admin/tools/{a}/edit?screenshot_captured=1")
    assert "Screenshot captured." in r.text


def test_admin_recapture_failure_leaves_existing_screenshot(env, monkeypatch):
    _mock_playwright_failure(monkeypatch)
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.update_tool_screenshot(a, "https://example.com/existing.png", 0)
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post(f"/admin/tools/{a}/screenshot/recapture", follow_redirects=False)
    assert r.status_code == 303
    assert "screenshot_captured=0" in r.headers["location"]

    lib = Library(os.environ["LINKLIB_DB"])
    assert lib.get_tool(a)["screenshot_url"] == "https://example.com/existing.png"   # untouched
    lib.close()

    r = client.get(f"/admin/tools/{a}/edit?screenshot_captured=0")
    assert "Couldn" in r.text  # the failure banner


def test_admin_recapture_requires_auth(env):
    r = _client(env).post("/admin/tools/1/screenshot/recapture")
    assert r.status_code == 401


# -- scripts/capture_tool_screenshots.py ----------------------------------------

def test_script_dry_run_captures_nothing(monkeypatch, tmp_path):
    import scripts.capture_tool_screenshots as script_mod
    db_path = str(tmp_path / "t.db")
    lib = Library(db_path)
    lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.close()

    monkeypatch.setattr(sys, "argv", ["prog", "--db", db_path, "--tools", "Runway", "--dry-run"])
    rc = script_mod.main()
    assert rc == 0

    lib = Library(db_path)
    tool = lib.list_tools(approved_only=True)[0]
    assert tool["screenshot_url"] == ""
    lib.close()


def test_script_captures_and_skips_existing(monkeypatch, tmp_path):
    import scripts.capture_tool_screenshots as script_mod
    _mock_playwright_success(monkeypatch)
    db_path = str(tmp_path / "t.db")
    lib = Library(db_path)
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.close()

    monkeypatch.setattr(sys, "argv", ["prog", "--db", db_path, "--tools", "Runway"])
    rc = script_mod.main()
    assert rc == 0

    lib = Library(db_path)
    tool = lib.get_tool(a)
    assert tool["screenshot_url"]
    assert tool["screenshot_captured_at"]
    lib.close()

    # Re-run without --force: should skip (already has a screenshot)
    monkeypatch.setattr(sys, "argv", ["prog", "--db", db_path, "--tools", "Runway"])
    rc = script_mod.main()
    assert rc == 0


def test_script_requires_scope_flag(tmp_path, monkeypatch):
    import scripts.capture_tool_screenshots as script_mod
    db_path = str(tmp_path / "t.db")
    Library(db_path).close()
    monkeypatch.setattr(sys, "argv", ["prog", "--db", db_path])
    rc = script_mod.main()
    assert rc == 2
