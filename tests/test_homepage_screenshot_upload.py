"""Item A4 (edit-page fixes) — manual "Upload homepage screenshot" for the
Software edit page: the homepage slot gets the same crop-and-upload
alternative to auto-capture the App screenshot slot already has
(_APP_SCREENSHOT_CROP_JS, generalized to a `slot` param — see that
constant's own docstring), reusing that exact crop flow and validation
rather than building a second one. Mirrors tests/test_app_screenshot.py's
upload-route coverage for the app slot.
"""
import io
import os
import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

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


# A minimal valid PNG (1x1) — same fixture shape test_app_screenshot.py uses.
_FAKE_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def test_admin_homepage_screenshot_upload_success(env):
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post(
        f"/admin/tools/software/{a}/screenshot/upload",
        files={"file": ("homepage-screenshot.png", io.BytesIO(_FAKE_PNG), "image/png")},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "screenshot_captured=1" in r.headers["location"]

    lib = Library(os.environ["LINKLIB_DB"])
    tool = lib.get_tool(a)
    assert tool["screenshot_url"]
    # Item A4's own requirement: an upload reads "Manually set—no capture
    # date," same as a hand-pasted URL — never a stamped capture time the
    # way Recapture's own set_tool_screenshot_capture always claims.
    assert tool["screenshot_captured_at"] == ""
    # App slot is untouched by a homepage-screenshot upload.
    assert tool["app_screenshot_url"] == ""
    lib.close()

    # Served through the existing homepage-screenshot serving route/
    # directory — the same {slug}.png filename Generate/Recapture already
    # write to, no new serving route needed.
    served_path = "/tools/software/screenshot/" + tool["slug"] + ".png"
    r2 = client.get(served_path)
    assert r2.status_code == 200
    assert r2.content == _FAKE_PNG


def test_admin_homepage_screenshot_upload_overwrites_existing_screenshot(env, monkeypatch):
    """An upload replaces whatever's currently saved, generated or pasted
    alike — the exact reported case (Payhawk's Generate-captured homepage
    screenshot caught a cookie banner and needed replacing)."""
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Payhawk", "Spend", "https://payhawk.com", ["Spend"], approved=1)
    lib.update_tool_screenshot_url(a, "https://cdn.example/old-with-cookie-banner.png")
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post(
        f"/admin/tools/software/{a}/screenshot/upload",
        files={"file": ("clean-homepage.png", io.BytesIO(_FAKE_PNG), "image/png")},
        follow_redirects=False,
    )
    assert "screenshot_captured=1" in r.headers["location"]

    lib = Library(os.environ["LINKLIB_DB"])
    tool = lib.get_tool(a)
    assert "old-with-cookie-banner" not in tool["screenshot_url"]
    assert tool["slug"] in tool["screenshot_url"]
    lib.close()


def test_admin_homepage_screenshot_upload_rejects_bad_mime(env):
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post(
        f"/admin/tools/software/{a}/screenshot/upload",
        files={"file": ("not-an-image.txt", io.BytesIO(b"hello world"), "text/plain")},
        follow_redirects=False,
    )
    assert "screenshot_captured=0" in r.headers["location"]

    lib = Library(os.environ["LINKLIB_DB"])
    assert lib.get_tool(a)["screenshot_url"] == ""
    lib.close()


def test_admin_homepage_screenshot_upload_rejects_oversized_file(env):
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.close()

    import webapp.app as appmod
    oversized = b"\x89PNG\r\n\x1a\n" + b"\x00" * (appmod._APP_SCREENSHOT_MAX_BYTES + 1)

    client = _client(env)
    _login(client)
    r = client.post(
        f"/admin/tools/software/{a}/screenshot/upload",
        files={"file": ("big.png", io.BytesIO(oversized), "image/png")},
        follow_redirects=False,
    )
    assert "screenshot_captured=0" in r.headers["location"]


def test_admin_homepage_screenshot_upload_requires_auth(env):
    r = _client(env).post(
        "/admin/tools/software/1/screenshot/upload",
        files={"file": ("homepage-screenshot.png", io.BytesIO(_FAKE_PNG), "image/png")},
    )
    assert r.status_code == 401


def test_admin_homepage_screenshot_upload_missing_tool_404s(env):
    client = _client(env)
    _login(client)
    r = client.post(
        "/admin/tools/software/999/screenshot/upload",
        files={"file": ("homepage-screenshot.png", io.BytesIO(_FAKE_PNG), "image/png")},
    )
    assert r.status_code == 404


def test_edit_page_shows_upload_homepage_screenshot_button(env):
    """The button/file-input/crop-modal markup this test proves exists is
    what item A1's two-row logo redesign and item A2's app-screenshot fix
    also live on — a smoke test that the new control actually rendered,
    not just that its route works standalone."""
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    slug = lib.get_tool(a)["slug"]
    lib.close()

    client = _client(env)
    _login(client)
    r = client.get(f"/tools/software/{slug}/edit")
    assert r.status_code == 200
    body = r.text
    assert "Upload homepage screenshot" in body
    assert f'id="home-screenshot-file-tools-{a}"' in body
    assert f'action="/admin/tools/software/{a}/screenshot/upload"' in body
    assert f"handleShotFile(this, 'tools-{a}', 'home')" in body
    # A1: the logo section's rename and its two-row split.
    assert "Pull from Logo.dev" in body
    assert "Revert and re-fetch from Logo.dev" not in body
    # A2: the recapture button now validates client-side instead of being
    # server-side disabled with a tooltip.
    assert f"submitAppScreenshotRecapture('tools-{a}')" in body
    assert "Enter a source URL above, then Save changes, first." not in body
