"""Dual screenshot capture (Phase E): the second, independent app/product
screenshot slot alongside the existing homepage slot — the two DB write
paths (auto-capture against app_screenshot_source_url, manual crop-and-
upload), the admin recapture/upload routes, the retirement of
screenshot_is_product (data migration + non-destructive column), and the
profile page's dual-screenshot card (stacked desktop rendering, mobile
toggle button, per-slot captions). Mirrors tests/test_screenshot_capture.py's
structure for the homepage slot.
"""
import io
import os
import pathlib
import sys
import tempfile
import types

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


# A minimal valid PNG (1x1, from the standard magic-byte fixture used
# elsewhere in this test suite for _sniff_image_mime coverage).
_FAKE_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


# -- linklib.db: migration -------------------------------------------------

def test_migration_not_run_automatically_on_boot(tmp_path):
    """Reopening the DB (which re-runs every migration in Library.__init__)
    must NOT touch a legacy screenshot_is_product=1 row — this migration is
    manual-trigger only (scripts/migrate_app_screenshot_from_product_flag.py),
    per the standing "human review before a production data write" rule."""
    lib = Library(str(tmp_path / "t.db"))
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.update_tool_screenshot(a, "https://example.com/product-shot.png", 1)  # legacy is_product=1
    lib.close()

    lib = Library(str(tmp_path / "t.db"))  # a boot / reopen
    tool = lib.get_tool(a)
    assert tool["screenshot_url"] == "https://example.com/product-shot.png"  # untouched
    assert tool["app_screenshot_url"] == ""  # untouched
    lib.close()


def test_find_legacy_product_screenshot_rows_is_read_only(tmp_path):
    lib = Library(str(tmp_path / "t.db"))
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.update_tool_screenshot(a, "https://example.com/product-shot.png", 1)
    candidates = lib.find_legacy_product_screenshot_rows()
    assert len(candidates) == 1
    assert candidates[0]["table"] == "tools"
    assert candidates[0]["id"] == a
    assert candidates[0]["screenshot_url"] == "https://example.com/product-shot.png"
    # Read-only — no write happened.
    tool = lib.get_tool(a)
    assert tool["screenshot_url"] == "https://example.com/product-shot.png"
    assert tool["app_screenshot_url"] == ""
    lib.close()


def test_migrate_app_screenshot_from_product_flag_moves_row(tmp_path):
    lib = Library(str(tmp_path / "t.db"))
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.update_tool_screenshot(a, "https://example.com/product-shot.png", 1)  # legacy is_product=1

    touched = lib.migrate_app_screenshot_from_product_flag()
    assert len(touched) == 1
    assert touched[0]["id"] == a

    tool = lib.get_tool(a)
    assert tool["screenshot_url"] == ""  # homepage slot cleared
    assert tool["app_screenshot_url"] == "https://example.com/product-shot.png"
    assert tool["screenshot_is_product"] == 1  # frozen, not dropped — historical marker
    lib.close()


def test_migrate_app_screenshot_from_product_flag_is_idempotent(tmp_path):
    lib = Library(str(tmp_path / "t.db"))
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.update_tool_screenshot(a, "https://example.com/product-shot.png", 1)
    lib.migrate_app_screenshot_from_product_flag()
    # A second run must not re-touch or duplicate anything.
    touched_again = lib.migrate_app_screenshot_from_product_flag()
    assert touched_again == []
    tool = lib.get_tool(a)
    assert tool["app_screenshot_url"] == "https://example.com/product-shot.png"
    lib.close()


def test_migrate_app_screenshot_from_product_flag_leaves_non_flagged_rows_alone(tmp_path):
    lib = Library(str(tmp_path / "t.db"))
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.set_tool_screenshot_capture(a, "https://example.com/homepage.png")  # ordinary auto-capture
    touched = lib.migrate_app_screenshot_from_product_flag()
    assert touched == []
    tool = lib.get_tool(a)
    assert tool["screenshot_url"] == "https://example.com/homepage.png"
    assert tool["app_screenshot_url"] == ""
    lib.close()


# -- scripts/migrate_app_screenshot_from_product_flag.py ---------------------

def test_script_preview_makes_no_writes(monkeypatch, tmp_path):
    import scripts.migrate_app_screenshot_from_product_flag as script_mod
    db_path = str(tmp_path / "t.db")
    lib = Library(db_path)
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.update_tool_screenshot(a, "https://example.com/product-shot.png", 1)
    lib.close()

    monkeypatch.setattr(sys, "argv", ["prog", "--db", db_path])
    rc = script_mod.main()
    assert rc == 0

    lib = Library(db_path)
    tool = lib.get_tool(a)
    assert tool["screenshot_url"] == "https://example.com/product-shot.png"  # untouched
    assert tool["app_screenshot_url"] == ""
    lib.close()


def test_script_apply_writes_and_verifies(monkeypatch, tmp_path):
    import scripts.migrate_app_screenshot_from_product_flag as script_mod
    db_path = str(tmp_path / "t.db")
    lib = Library(db_path)
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.update_tool_screenshot(a, "https://example.com/product-shot.png", 1)
    lib.close()

    monkeypatch.setattr(sys, "argv", ["prog", "--db", db_path, "--apply"])
    rc = script_mod.main()
    assert rc == 0

    lib = Library(db_path)
    tool = lib.get_tool(a)
    assert tool["screenshot_url"] == ""
    assert tool["app_screenshot_url"] == "https://example.com/product-shot.png"
    lib.close()


def test_script_no_candidates_exits_clean(monkeypatch, tmp_path):
    import scripts.migrate_app_screenshot_from_product_flag as script_mod
    db_path = str(tmp_path / "t.db")
    Library(db_path).close()
    monkeypatch.setattr(sys, "argv", ["prog", "--db", db_path, "--apply"])
    rc = script_mod.main()
    assert rc == 0


# -- linklib.db: writers -----------------------------------------------------

def test_set_tool_app_screenshot(tmp_path):
    lib = Library(str(tmp_path / "t.db"))
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.set_tool_app_screenshot(a, "/tools/software/screenshot/runway-app.png")
    tool = lib.get_tool(a)
    assert tool["app_screenshot_url"] == "/tools/software/screenshot/runway-app.png"
    assert tool["app_screenshot_captured_at"]
    lib.close()


def test_update_tool_app_screenshot_source_does_not_touch_app_url(tmp_path):
    lib = Library(str(tmp_path / "t.db"))
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.set_tool_app_screenshot(a, "/tools/software/screenshot/runway-app.png")
    lib.update_tool_app_screenshot_source(a, "https://runway.com/demo")
    tool = lib.get_tool(a)
    assert tool["app_screenshot_source_url"] == "https://runway.com/demo"
    assert tool["app_screenshot_url"] == "/tools/software/screenshot/runway-app.png"  # untouched
    lib.close()


def test_set_community_app_screenshot(tmp_path):
    lib = Library(str(tmp_path / "t.db"))
    c = lib.add_community("FP&A Club", "https://fpaclub.example", "CFOs", "Free", ["FP&A"], approved=1)
    lib.set_community_app_screenshot(c, "/tools/communities/screenshot/fpa-club-app.png")
    community = lib.get_community(c)
    assert community["app_screenshot_url"] == "/tools/communities/screenshot/fpa-club-app.png"
    assert community["app_screenshot_captured_at"]
    lib.close()


# -- admin recapture route (auto-capture) ------------------------------------

def test_admin_app_screenshot_recapture_success(env, monkeypatch):
    _mock_playwright_success(monkeypatch)
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.update_tool_app_screenshot_source(a, "https://runway.com/demo")
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post(f"/admin/tools/{a}/app-screenshot/recapture", follow_redirects=False)
    assert r.status_code == 303
    assert "app_screenshot_captured=1" in r.headers["location"]

    lib = Library(os.environ["LINKLIB_DB"])
    tool = lib.get_tool(a)
    assert tool["app_screenshot_url"]
    assert tool["app_screenshot_captured_at"]
    # Homepage slot is untouched by an app-screenshot capture.
    assert tool["screenshot_url"] == ""
    lib.close()


def test_admin_app_screenshot_recapture_without_source_url_noops(env, monkeypatch):
    """No app_screenshot_source_url saved yet — recapture has nothing to
    capture against and must not call Playwright at all."""
    written = _mock_playwright_success(monkeypatch)
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post(f"/admin/tools/{a}/app-screenshot/recapture", follow_redirects=False)
    assert r.status_code == 303
    assert "app_screenshot_captured=0" in r.headers["location"]
    assert "url" not in written


def test_admin_app_screenshot_recapture_failure(env, monkeypatch):
    _mock_playwright_failure(monkeypatch)
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.update_tool_app_screenshot_source(a, "https://runway.com/demo")
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post(f"/admin/tools/{a}/app-screenshot/recapture", follow_redirects=False)
    assert "app_screenshot_captured=0" in r.headers["location"]


def test_admin_app_screenshot_recapture_requires_auth(env):
    r = _client(env).post("/admin/tools/1/app-screenshot/recapture")
    assert r.status_code == 401


def test_admin_communities_app_screenshot_recapture_success(env, monkeypatch):
    _mock_playwright_success(monkeypatch)
    lib = Library(os.environ["LINKLIB_DB"])
    c = lib.add_community("FP&A Club", "https://fpaclub.example", "CFOs", "Free", ["FP&A"], approved=1)
    lib.update_community_app_screenshot_source(c, "https://fpaclub.example/join")
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post(f"/admin/tools/communities/{c}/app-screenshot/recapture", follow_redirects=False)
    assert r.status_code == 303
    assert "app_screenshot_captured=1" in r.headers["location"]


# -- admin upload route (manual crop-and-upload) -----------------------------

def test_admin_app_screenshot_upload_success(env):
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post(
        f"/admin/tools/{a}/app-screenshot/upload",
        files={"file": ("app-screenshot.png", io.BytesIO(_FAKE_PNG), "image/png")},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "app_screenshot_captured=1" in r.headers["location"]

    lib = Library(os.environ["LINKLIB_DB"])
    tool = lib.get_tool(a)
    assert tool["app_screenshot_url"]
    assert tool["app_screenshot_captured_at"]
    lib.close()

    # Served through the existing homepage-screenshot route/directory — no
    # new serving route needed, just a "-app" filename suffix.
    served_path = "/tools/software/screenshot/" + tool["slug"] + "-app.png"
    r2 = client.get(served_path)
    assert r2.status_code == 200
    assert r2.content == _FAKE_PNG


def test_admin_app_screenshot_upload_rejects_bad_mime(env):
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post(
        f"/admin/tools/{a}/app-screenshot/upload",
        files={"file": ("not-an-image.txt", io.BytesIO(b"hello world"), "text/plain")},
        follow_redirects=False,
    )
    assert "app_screenshot_captured=0" in r.headers["location"]

    lib = Library(os.environ["LINKLIB_DB"])
    assert lib.get_tool(a)["app_screenshot_url"] == ""
    lib.close()


def test_admin_app_screenshot_upload_rejects_oversized_file(env):
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    lib.close()

    import webapp.app as appmod
    oversized = b"\x89PNG\r\n\x1a\n" + b"\x00" * (appmod._APP_SCREENSHOT_MAX_BYTES + 1)

    client = _client(env)
    _login(client)
    r = client.post(
        f"/admin/tools/{a}/app-screenshot/upload",
        files={"file": ("big.png", io.BytesIO(oversized), "image/png")},
        follow_redirects=False,
    )
    assert "app_screenshot_captured=0" in r.headers["location"]


def test_admin_app_screenshot_upload_requires_auth(env):
    r = _client(env).post(
        "/admin/tools/1/app-screenshot/upload",
        files={"file": ("app-screenshot.png", io.BytesIO(_FAKE_PNG), "image/png")},
    )
    assert r.status_code == 401


def test_admin_communities_app_screenshot_upload_success(env):
    lib = Library(os.environ["LINKLIB_DB"])
    c = lib.add_community("FP&A Club", "https://fpaclub.example", "CFOs", "Free", ["FP&A"], approved=1)
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post(
        f"/admin/tools/communities/{c}/app-screenshot/upload",
        files={"file": ("app-screenshot.png", io.BytesIO(_FAKE_PNG), "image/png")},
        follow_redirects=False,
    )
    assert "app_screenshot_captured=1" in r.headers["location"]


# -- admin edit form: saves the source URL field -----------------------------

def test_admin_edit_saves_app_screenshot_source_url(env):
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    a_slug = lib.get_tool(a)["slug"]
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post(f"/tools/software/{a_slug}/edit", data={
        "name": "Runway", "url": "https://runway.com", "description": "FP&A", "summary": "FP&A",
        "app_screenshot_source_url": "https://runway.com/demo",
    }, follow_redirects=False)
    assert r.status_code == 303

    lib = Library(os.environ["LINKLIB_DB"])
    assert lib.get_tool(a)["app_screenshot_source_url"] == "https://runway.com/demo"
    lib.close()


def test_admin_communities_edit_save_does_not_clobber_legacy_product_flag(env):
    """Communities equivalent of the tools regression test in
    tests/test_software_screenshot.py — same 2026-08 incident, same fix
    (update_community_screenshot_url instead of update_community_screenshot)."""
    lib = Library(os.environ["LINKLIB_DB"])
    c = lib.add_community("FP&A Club", "https://fpaclub.example", "CFOs", "Free", ["FP&A"], approved=1)
    c_slug = lib.get_community(c)["slug"]
    lib.update_community_screenshot(c, "https://example.com/product-shot.png", 1)  # legacy flag set
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post(f"/tools/communities/{c_slug}/edit", data={
        "name": "FP&A Club Renamed", "demographic": "CFOs",
        "screenshot_url": "https://example.com/product-shot.png",
    }, follow_redirects=False)
    assert r.status_code == 303

    lib = Library(os.environ["LINKLIB_DB"])
    community = lib.get_community(c)
    assert community["name"] == "FP&A Club Renamed"
    assert community["screenshot_url"] == "https://example.com/product-shot.png"
    assert community["screenshot_is_product"] == 1  # survives the unrelated save
    lib.close()


# -- profile page: dual-screenshot card --------------------------------------

def test_profile_page_single_screenshot_unchanged_when_no_app_shot(env):
    """The common case at launch: only a homepage screenshot. Must render
    with no 'has-app' class and no toggle button — strictly identical to
    pre-Phase-E behavior."""
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    a_slug = lib.get_tool(a)["slug"]
    lib.set_tool_screenshot_capture(a, "https://example.com/homepage.png")
    lib.close()

    r = _client(env).get(f"/tools/software/{a_slug}")
    # "has-app" legitimately appears in the page's static <style> block
    # (the CSS rule exists on every profile page) — what must NOT appear is
    # the class actually applied to this record's card, or the toggle
    # button itself.
    assert 'class="tp-card tp-shot-card has-app"' not in r.text
    assert '<button type="button" class="tp-shot-toggle"' not in r.text
    assert "Show app screenshot</button>" not in r.text


def test_profile_page_shows_both_screenshots_stacked(env):
    lib = Library(os.environ["LINKLIB_DB"])
    a = lib.add_tool("Runway", "FP&A", "https://runway.com", ["FP&A"], approved=1)
    a_slug = lib.get_tool(a)["slug"]
    lib.set_tool_screenshot_capture(a, "https://example.com/homepage.png")
    lib.set_tool_app_screenshot(a, "https://example.com/app.png")
    lib.close()

    r = _client(env).get(f"/tools/software/{a_slug}")
    assert r.status_code == 200
    assert "https://example.com/homepage.png" in r.text
    assert "https://example.com/app.png" in r.text
    assert "has-app" in r.text
    assert "Show app screenshot" in r.text
    assert "Homepage screenshot, captured" in r.text
    assert "App screenshot, captured" in r.text


def test_profile_page_communities_shows_both_screenshots(env):
    lib = Library(os.environ["LINKLIB_DB"])
    c = lib.add_community("FP&A Club", "https://fpaclub.example", "CFOs", "Free", ["FP&A"], approved=1)
    c_slug = lib.get_community(c)["slug"]
    lib.set_community_screenshot_capture(c, "https://example.com/homepage.png")
    lib.set_community_app_screenshot(c, "https://example.com/app.png")
    lib.close()

    r = _client(env).get(f"/tools/communities/{c_slug}")
    assert r.status_code == 200
    assert "has-app" in r.text
    assert "App screenshot, captured" in r.text
