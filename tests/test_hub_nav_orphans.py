"""Hub-nav orphan detector — a sibling to the page-index route-walk
(tests/test_page_index.py), catching the opposite failure: a real /admin
route with no corresponding hub-nav card anywhere on /admin, reachable only
by guessing the URL or via some other page's inline link. See
webapp/app.py's `hub_nav_orphans()`/`_hub_nav_all_hrefs()` block comment for
the full exclusion rules.

Found 2026-09: Communities' category CRUD (/admin/tools/communities/categories)
had no hub-nav card, while its Software parallel
(/admin/tools/software/categories) did. This suite proves the detector
actually catches that class of gap (not just passes trivially against
whatever the hand-maintained tuples currently say) and that it stays clean
once the gap is fixed.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import webapp.app as appmod
from webapp import checks


def test_no_orphans_today():
    """The real, current state: every admin route has a hub-nav card. This
    is the "passes once the gap is fixed" half of the build brief — it
    exercises the live _ADMIN_GROUPS/_LIBRARY_TOOLS/_FPA_BUDDY_TOOLS/
    _SOFTWARE_TOOLS tuples exactly as committed, not a rigged fixture."""
    assert appmod.hub_nav_orphans() == []


def test_detector_actually_catches_a_real_gap(monkeypatch):
    """Proves the detector isn't trivially passing: reproduce the exact
    2026-09 incident by removing Communities' category-CRUD card from the
    live tuples and confirming it's flagged as an orphan. The route itself
    (GET /admin/tools/communities/categories) still exists — only its
    hub-nav card is removed here, the same shape the real bug had.

    Communities nested into its own CFO Toolbox sub-group in PR 11
    (2026-09), so this card now lives in `_COMMUNITIES_TOOLS`, not
    `_TOOLBOX_TOOLS` — patched here to match."""
    trimmed = [item for item in appmod._COMMUNITIES_TOOLS
               if item[0] != "/admin/tools/communities/categories"]
    assert len(trimmed) == len(appmod._COMMUNITIES_TOOLS) - 1  # sanity: something was actually removed

    monkeypatch.setattr(appmod, "_COMMUNITIES_TOOLS", trimmed)

    orphans = appmod.hub_nav_orphans()
    assert orphans == ["/admin/tools/communities/categories"]


def test_checks_run_all_reports_hub_nav_orphans_pass():
    results = checks.run_all()
    row = next(r for r in results if r["name"] == "Hub-nav orphans")
    assert row["ok"] is True
    assert row["where"] == "Live + CI"
    assert "hub-nav card" in row["detail"]


def test_checks_run_all_reports_hub_nav_orphans_fail_when_gap_reintroduced(monkeypatch):
    trimmed = [item for item in appmod._COMMUNITIES_TOOLS
               if item[0] != "/admin/tools/communities/categories"]
    monkeypatch.setattr(appmod, "_COMMUNITIES_TOOLS", trimmed)

    results = checks.run_all()
    row = next(r for r in results if r["name"] == "Hub-nav orphans")
    assert row["ok"] is False
    assert "/admin/tools/communities/categories" in row["detail"]


@pytest.mark.parametrize("excluded_path", [
    "/admin",                                        # the hub page itself
    "/admin/tools/software/new",                      # .../new under an already-carded parent
    "/admin/tools/communities/new",                    # same
    "/admin/reader/feeds/new",                        # same
    "/admin/thought-leadership/original/new",                     # same
    "/admin/thought-leadership/third-party/new",                   # same
    "/admin/tools/resources/new",                      # same
    "/admin/overhead-spend/details",                   # named, documented exception
    "/admin/tools/software/name-duplicates",           # named, documented exception (inline-linked only)
])
def test_known_non_carded_routes_are_never_flagged(excluded_path):
    """These are real routes with no hub-nav card, on purpose — the detector
    must not flag any of them, or it would be permanently red for reasons
    that aren't real gaps."""
    orphans = appmod.hub_nav_orphans()
    assert excluded_path not in orphans


def test_path_param_routes_are_never_flagged():
    """A per-record detail/edit page (reached from its own list page's rows)
    is never expected to carry its own static hub-nav card."""
    orphans = appmod.hub_nav_orphans()
    assert not any("{" in p for p in orphans)
    # sanity: these routes really do exist and really do have no card, so
    # the exclusion is doing real work, not vacuously true
    real_detail_routes = {
        "/admin/reader/feeds/{feed_id}/edit",
        "/admin/thought-leadership/original/{item_id}/edit",
        "/admin/thought-leadership/third-party/{item_id}/edit",
        "/admin/tools/resources/{benchmark_id}/edit",
    }
    from fastapi.routing import APIRoute
    live_paths = {r.path for r in appmod.app.routes if isinstance(r, APIRoute)}
    assert real_detail_routes <= live_paths


def test_hub_nav_all_hrefs_includes_the_new_communities_categories_card():
    assert "/admin/tools/communities/categories" in appmod._hub_nav_all_hrefs()


def test_admin_page_renders_the_new_card(monkeypatch, tmp_path):
    monkeypatch.setenv("LINKLIB_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib
    importlib.reload(appmod)
    try:
        from fastapi.testclient import TestClient
        c = TestClient(appmod.app, raise_server_exceptions=True)
        c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
        r = c.get("/admin")
        assert r.status_code == 200
        assert "/admin/tools/communities/categories" in r.text
        assert "Community categories" in r.text
    finally:
        importlib.reload(appmod)  # restore module state for later tests in the same run
