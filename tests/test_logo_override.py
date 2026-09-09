"""Manual logo override (2026-08 — Aleph/Zapier investigation).

Covers:
  - a manual override taking precedence over a fresh Brandfetch fetch
    (Library.set_tool_logo/set_community_logo refuse to overwrite once
    logo_manual_override=1, regardless of the caller — the automated
    backfill's own selection-query filtering is a separate belt on top,
    covered here too)
  - a normal (non-overridden) tool/community still auto-fetches as before —
    no regression to existing Phase D behavior
  - "Revert to automatic" clears the flag/path so a future backfill run can
    re-populate it
  - a URL/domain change flags an active override as stale instead of
    silently dropping or silently keeping it
  - scripts/backfill_logos.py's selection query excludes overridden rows
  - "Revert & re-fetch from Logo.dev" (2026-08 follow-up, source switched
    from Brandfetch to Logo.dev in 2026-09 — see linklib/logodev.py): the
    same clear action now also makes one live call for that row, via
    _live_refetch_logo — success, no API key, quota, and no-usable-asset
    paths, and that a failed live re-fetch never leaves the row worse off
    than a plain revert would have (still ends up reverted to automatic)
"""
import os
import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Library


@pytest.fixture
def lib():
    db = tempfile.mktemp(suffix=".db")
    library = Library(db)
    yield library
    library.close()
    if os.path.exists(db):
        os.remove(db)


def _add_tool(lib, name="Aleph", url="https://www.getaleph.com"):
    return lib.add_tool(name, "desc", url, ["FP&A"], approved=1)


def _add_community(lib, name="Test Community", url="https://example.com"):
    return lib.add_community(name, url, "demographic", "Free", ["FP&A"], approved=1)


# --- Tools ------------------------------------------------------------

def test_set_tool_logo_normal_tool_still_autofetches(lib):
    """No regression: a tool with no override behaves exactly as before —
    set_tool_logo (what scripts/backfill_logos.py calls) writes normally."""
    tid = _add_tool(lib)
    wrote = lib.set_tool_logo(tid, "logos/tools/aleph.svg")
    assert wrote is True
    tool = lib.get_tool(tid)
    assert tool["logo_path"] == "logos/tools/aleph.svg"
    assert tool["logo_manual_override"] == 0


def test_manual_override_wins_over_fresh_brandfetch_fetch(lib):
    """The actual bug scenario: Aleph got Zapier's logo from a Brandfetch
    fetch. An admin sets a manual override; a subsequent automated
    set_tool_logo call (simulating a future backfill run) must not
    overwrite it."""
    tid = _add_tool(lib)
    lib.set_tool_logo(tid, "logos/tools/aleph.svg")  # the wrong, Brandfetch-sourced logo
    lib.set_tool_logo_manual(tid, "logos/tools/aleph-manual.png")  # admin corrects it

    wrote = lib.set_tool_logo(tid, "logos/tools/aleph.svg")  # a future automated re-fetch
    assert wrote is False, "an automated write must not overwrite a manual override"

    tool = lib.get_tool(tid)
    assert tool["logo_path"] == "logos/tools/aleph-manual.png"
    assert tool["logo_manual_override"] == 1


def test_set_tool_logo_force_can_still_override_when_explicitly_asked(lib):
    """force=True is the one escape hatch (not used by any live caller
    today) — proves the guard is a real, deliberate check, not an
    accidental no-op."""
    tid = _add_tool(lib)
    lib.set_tool_logo_manual(tid, "logos/tools/aleph-manual.png")
    wrote = lib.set_tool_logo(tid, "logos/tools/aleph.svg", force=True)
    assert wrote is True
    assert lib.get_tool(tid)["logo_path"] == "logos/tools/aleph.svg"


def test_clear_tool_logo_override_reverts_to_automatic(lib):
    tid = _add_tool(lib)
    lib.set_tool_logo_manual(tid, "logos/tools/aleph-manual.png")
    lib.clear_tool_logo_override(tid)
    tool = lib.get_tool(tid)
    assert tool["logo_path"] == ""
    assert tool["logo_manual_override"] == 0
    assert tool["logo_override_stale"] == 0
    # And now a normal automated write works again.
    wrote = lib.set_tool_logo(tid, "logos/tools/aleph.svg")
    assert wrote is True


def test_url_domain_change_flags_override_as_stale_not_silently_dropped(lib):
    tid = _add_tool(lib)
    lib.set_tool_logo_manual(tid, "logos/tools/aleph-manual.png")
    tool = lib.get_tool(tid)
    lib.update_tool(tid, tool["name"], tool["description"], "https://totally-different-company.com",
                     tool["categories"])
    tool = lib.get_tool(tid)
    assert tool["logo_override_stale"] == 1
    # The override itself is untouched — never silently applied to the new
    # site without at least flagging it, but also never silently dropped.
    assert tool["logo_manual_override"] == 1
    assert tool["logo_path"] == "logos/tools/aleph-manual.png"


def test_url_path_only_change_does_not_flag_stale(lib):
    """A same-domain URL edit (path/query/scheme only) is not a "different
    site" and must not trip the stale flag."""
    tid = _add_tool(lib)
    lib.set_tool_logo_manual(tid, "logos/tools/aleph-manual.png")
    tool = lib.get_tool(tid)
    lib.update_tool(tid, tool["name"], tool["description"], "https://www.getaleph.com/pricing",
                     tool["categories"])
    assert lib.get_tool(tid)["logo_override_stale"] == 0


def test_url_change_with_no_override_does_not_flag_stale(lib):
    tid = _add_tool(lib)
    tool = lib.get_tool(tid)
    lib.update_tool(tid, tool["name"], tool["description"], "https://different-site.com",
                     tool["categories"])
    assert lib.get_tool(tid)["logo_override_stale"] == 0


def test_dismiss_tool_logo_stale_keeps_override(lib):
    tid = _add_tool(lib)
    lib.set_tool_logo_manual(tid, "logos/tools/aleph-manual.png")
    tool = lib.get_tool(tid)
    lib.update_tool(tid, tool["name"], tool["description"], "https://different-site.com",
                     tool["categories"])
    assert lib.get_tool(tid)["logo_override_stale"] == 1
    lib.dismiss_tool_logo_stale(tid)
    tool = lib.get_tool(tid)
    assert tool["logo_override_stale"] == 0
    assert tool["logo_manual_override"] == 1
    assert tool["logo_path"] == "logos/tools/aleph-manual.png"


def test_readded_tool_starts_with_no_override(lib):
    """A brand-new row (delete + re-add) never inherits a prior row's
    override — add_tool defaults logo_manual_override to 0."""
    tid1 = _add_tool(lib)
    lib.set_tool_logo_manual(tid1, "logos/tools/aleph-manual.png")
    lib.delete_tool(tid1)
    tid2 = _add_tool(lib)
    tool = lib.get_tool(tid2)
    assert tool["logo_manual_override"] == 0
    assert tool["logo_path"] == ""


# --- Communities (mirrors the tools coverage above) --------------------

def test_set_community_logo_normal_still_autofetches(lib):
    cid = _add_community(lib)
    wrote = lib.set_community_logo(cid, "logos/communities/test.svg")
    assert wrote is True
    assert lib.get_community(cid)["logo_path"] == "logos/communities/test.svg"


def test_community_manual_override_wins_over_fresh_fetch(lib):
    cid = _add_community(lib)
    lib.set_community_logo(cid, "logos/communities/test.svg")
    lib.set_community_logo_manual(cid, "logos/communities/test-manual.png")
    wrote = lib.set_community_logo(cid, "logos/communities/test.svg")
    assert wrote is False
    community = lib.get_community(cid)
    assert community["logo_path"] == "logos/communities/test-manual.png"
    assert community["logo_manual_override"] == 1


def test_community_clear_override_reverts_to_automatic(lib):
    cid = _add_community(lib)
    lib.set_community_logo_manual(cid, "logos/communities/test-manual.png")
    lib.clear_community_logo_override(cid)
    community = lib.get_community(cid)
    assert community["logo_path"] == ""
    assert community["logo_manual_override"] == 0


def test_community_url_domain_change_flags_stale(lib):
    cid = _add_community(lib)
    lib.set_community_logo_manual(cid, "logos/communities/test-manual.png")
    community = lib.get_community(cid)
    lib.update_community(cid, community["name"], "https://a-different-community.org",
                          community["demographic"], community["cost_band"], community["categories"])
    community = lib.get_community(cid)
    assert community["logo_override_stale"] == 1
    assert community["logo_manual_override"] == 1


# --- scripts/backfill_logos.py selection query --------------------------

def test_backfill_selection_query_excludes_manual_override_rows(lib):
    """The automated script must never even attempt to re-fetch a row with
    an active override — a second belt beyond set_tool_logo's own refusal,
    so a --apply run never spends Brand API quota it can't use."""
    from scripts.backfill_logos import _select_candidates

    tid_missing = _add_tool(lib, name="Missing Logo Tool", url="https://missing-logo.example.com")
    tid_overridden = _add_tool(lib, name="Overridden Tool", url="https://overridden.example.com")
    lib.set_tool_logo_manual(tid_overridden, "logos/tools/overridden-manual.png")

    candidates = _select_candidates(lib, limit=100)
    candidate_ids = {row["id"] for kind, row in candidates if kind == "tool"}
    assert tid_missing in candidate_ids
    assert tid_overridden not in candidate_ids


# --- Web layer: edit pages render, routes work end to end ---------------

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


def _login(client, username="admin", password="adminpass"):
    r = client.post("/login", data={"username": username, "password": password}, follow_redirects=False)
    assert r.status_code in (302, 303)


def test_tool_edit_page_renders_logo_section(env):
    lib = Library(os.environ["LINKLIB_DB"])
    tid = _add_tool(lib)
    slug = lib.get_tool(tid)["slug"]
    lib.close()

    client = _client(env)
    _login(client)
    r = client.get(f"/tools/software/{slug}/edit")
    assert r.status_code == 200
    assert "Logo" in r.text
    assert f"/admin/tools/software/{tid}/logo/set-url" in r.text
    assert f"/admin/tools/software/{tid}/logo/upload" in r.text
    assert f"/admin/tools/software/{tid}/logo/clear" in r.text


def test_community_edit_page_renders_logo_section(env):
    lib = Library(os.environ["LINKLIB_DB"])
    cid = _add_community(lib)
    slug = lib.get_community(cid)["slug"]
    lib.close()

    client = _client(env)
    _login(client)
    r = client.get(f"/tools/communities/{slug}/edit")
    assert r.status_code == 200
    assert f"/admin/tools/communities/{cid}/logo/set-url" in r.text
    assert f"/admin/tools/communities/{cid}/logo/upload" in r.text


def test_tool_edit_page_shows_manual_override_badge_and_stale_banner(env):
    lib = Library(os.environ["LINKLIB_DB"])
    tid = _add_tool(lib)
    lib.set_tool_logo_manual(tid, "logos/tools/aleph-manual.png")
    tool = lib.get_tool(tid)
    lib.update_tool(tid, tool["name"], tool["description"], "https://different-site.com", tool["categories"])
    slug = lib.get_tool(tid)["slug"]
    lib.close()

    client = _client(env)
    _login(client)
    r = client.get(f"/tools/software/{slug}/edit")
    assert r.status_code == 200
    assert "Manual override" in r.text
    assert "confirm it&#39;s still the" in r.text.lower() or "confirm it" in r.text.lower()


def test_logo_clear_route_reverts_and_redirects(env):
    lib = Library(os.environ["LINKLIB_DB"])
    tid = _add_tool(lib)
    lib.set_tool_logo_manual(tid, "logos/tools/aleph-manual.png")
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post(f"/admin/tools/software/{tid}/logo/clear", follow_redirects=False)
    assert r.status_code == 303

    lib = Library(os.environ["LINKLIB_DB"])
    tool = lib.get_tool(tid)
    lib.close()
    assert tool["logo_manual_override"] == 0
    assert tool["logo_path"] == ""


def test_logo_upload_route_rejects_non_image_and_sets_override_on_success(env):
    lib = Library(os.environ["LINKLIB_DB"])
    tid = _add_tool(lib)
    lib.close()

    client = _client(env)
    _login(client)

    # Bad upload: not a real image.
    r = client.post(f"/admin/tools/software/{tid}/logo/upload",
                     files={"file": ("logo.txt", b"not an image", "text/plain")},
                     follow_redirects=False)
    assert r.status_code == 303
    assert "logo_set=0" in r.headers["location"]

    # Real (tiny) PNG magic bytes + minimal valid content.
    png_bytes = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    r = client.post(f"/admin/tools/software/{tid}/logo/upload",
                     files={"file": ("logo.png", png_bytes, "image/png")},
                     follow_redirects=False)
    assert r.status_code == 303
    assert "logo_set=1" in r.headers["location"]

    lib = Library(os.environ["LINKLIB_DB"])
    tool = lib.get_tool(tid)
    lib.close()
    assert tool["logo_manual_override"] == 1
    assert tool["logo_path"] == f"logos/tools/{tool['slug']}-manual.png"


# --- "Revert & re-fetch from Logo.dev" (2026-08 follow-up, source switched
# to Logo.dev in 2026-09) -------------------------------------------------

def _fake_asset(image_bytes=b"fake-png-bytes", ext="png", asset_type="icon"):
    return (image_bytes, ext, asset_type)


def _fake_download_asset(image_bytes, dest_path):
    """Mirrors linklib.logodev.download_asset's own directory-creation
    behavior (mkdir -p the parent) without touching real bytes."""
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    with open(dest_path, "wb") as f:
        f.write(image_bytes)


def test_live_refetch_logo_no_api_key(env, monkeypatch):
    monkeypatch.delenv("LOGODEV_API_KEY", raising=False)
    lib = Library(os.environ["LINKLIB_DB"])
    tid = _add_tool(lib)
    tool = lib.get_tool(tid)
    ok, message = env._live_refetch_logo(lib, "tools", tid, tool)
    lib.close()
    assert ok is False
    assert "LOGODEV_API_KEY" in message


def test_live_refetch_logo_success_writes_non_manual_logo(env, monkeypatch):
    monkeypatch.setenv("LOGODEV_API_KEY", "fake-key")
    from linklib import logodev
    monkeypatch.setattr(logodev, "fetch_logo_asset", lambda domain, api_key, session=None: (_fake_asset(), None))
    monkeypatch.setattr(logodev, "download_asset", _fake_download_asset)

    lib = Library(os.environ["LINKLIB_DB"])
    tid = _add_tool(lib)
    tool = lib.get_tool(tid)
    ok, message = env._live_refetch_logo(lib, "tools", tid, tool)
    tool = lib.get_tool(tid)
    lib.close()

    assert ok is True
    assert "Re-fetched" in message
    assert tool["logo_manual_override"] == 0
    assert tool["logo_path"] == f"logos/tools/{tool['slug']}.png"


def test_live_refetch_logo_quota_message(env, monkeypatch):
    monkeypatch.setenv("LOGODEV_API_KEY", "fake-key")
    from linklib import logodev
    monkeypatch.setattr(logodev, "fetch_logo_asset",
                         lambda domain, api_key, session=None: (None, "QUOTA: 429 rate-limited"))

    lib = Library(os.environ["LINKLIB_DB"])
    tid = _add_tool(lib)
    tool = lib.get_tool(tid)
    ok, message = env._live_refetch_logo(lib, "tools", tid, tool)
    tool_after = lib.get_tool(tid)
    lib.close()

    assert ok is False
    assert "rate-limited" in message.lower()
    # Never left worse off than a plain revert: still no manual override, no path.
    assert tool_after["logo_manual_override"] == 0
    assert tool_after["logo_path"] == ""


def test_live_refetch_logo_no_usable_asset(env, monkeypatch):
    monkeypatch.setenv("LOGODEV_API_KEY", "fake-key")
    from linklib import logodev
    monkeypatch.setattr(logodev, "fetch_logo_asset",
                         lambda domain, api_key, session=None: (None, "404 — Logo.dev has no logo for this domain"))

    lib = Library(os.environ["LINKLIB_DB"])
    tid = _add_tool(lib)
    tool = lib.get_tool(tid)
    ok, message = env._live_refetch_logo(lib, "tools", tid, tool)
    lib.close()

    assert ok is False
    assert "nothing usable" in message


def test_live_refetch_logo_download_failure(env, monkeypatch):
    monkeypatch.setenv("LOGODEV_API_KEY", "fake-key")
    from linklib import logodev

    def boom(image_bytes, dest_path):
        raise OSError("disk full")

    monkeypatch.setattr(logodev, "fetch_logo_asset", lambda domain, api_key, session=None: (_fake_asset(), None))
    monkeypatch.setattr(logodev, "download_asset", boom)

    lib = Library(os.environ["LINKLIB_DB"])
    tid = _add_tool(lib)
    tool = lib.get_tool(tid)
    ok, message = env._live_refetch_logo(lib, "tools", tid, tool)
    tool_after = lib.get_tool(tid)
    lib.close()

    assert ok is False
    assert "couldn't save" in message.lower()
    assert tool_after["logo_path"] == ""


def test_logo_clear_route_without_api_key_reverts_with_explanatory_banner(env, monkeypatch):
    monkeypatch.delenv("LOGODEV_API_KEY", raising=False)
    lib = Library(os.environ["LINKLIB_DB"])
    tid = _add_tool(lib)
    lib.set_tool_logo_manual(tid, "logos/tools/aleph-manual.png")
    slug = lib.get_tool(tid)["slug"]
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post(f"/admin/tools/software/{tid}/logo/clear", follow_redirects=True)
    assert "logo_refetched=0" in str(r.url)
    assert "LOGODEV_API_KEY" in r.text

    lib = Library(os.environ["LINKLIB_DB"])
    tool = lib.get_tool(tid)
    lib.close()
    assert tool["logo_manual_override"] == 0
    assert tool["logo_path"] == ""


def test_logo_clear_route_live_refetch_success_end_to_end(env, monkeypatch):
    """The actual scenario the button is for: an admin corrects a wrong
    logo, then later clicks to re-check Logo.dev — one click both reverts
    the override and lands a fresh, non-manual logo."""
    monkeypatch.setenv("LOGODEV_API_KEY", "fake-key")
    from linklib import logodev
    monkeypatch.setattr(logodev, "fetch_logo_asset", lambda domain, api_key, session=None: (_fake_asset(), None))
    monkeypatch.setattr(logodev, "download_asset", _fake_download_asset)

    lib = Library(os.environ["LINKLIB_DB"])
    tid = _add_tool(lib)
    lib.set_tool_logo_manual(tid, "logos/tools/aleph-manual.png")
    slug = lib.get_tool(tid)["slug"]
    lib.close()

    client = _client(env)
    _login(client)
    r = client.post(f"/admin/tools/software/{tid}/logo/clear", follow_redirects=True)
    assert "logo_refetched=1" in str(r.url)
    assert "Re-fetched a fresh logo" in r.text
    assert "Auto-fetched (Logo.dev)" in r.text  # badge reflects the new, non-manual source

    lib = Library(os.environ["LINKLIB_DB"])
    tool = lib.get_tool(tid)
    lib.close()
    assert tool["logo_manual_override"] == 0
    assert tool["logo_path"] == f"logos/tools/{slug}.png"
