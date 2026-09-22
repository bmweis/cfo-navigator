"""Route-level coverage for issue #592 item 4 — the Ampersands group's bulk
"Replace & with and" preview/apply routes on /admin/voice/review-queue.
Library-level coverage for the underlying replace_spaced_ampersands /
preview_ampersand_replacement / apply_ampersand_replacement lives in
tests/test_voice_review_queue.py; this file exercises the two HTTP routes
themselves (preview shows a diff and writes nothing, apply commits and
resolves, an empty selection redirects with an error, and a stale/removed
selection at apply time is a safe no-op rather than a crash)."""
import os
import tempfile

import pytest

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


def _login_admin(appmod):
    c = _client(appmod)
    c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    return c


def test_preview_route_shows_diff_and_writes_nothing(env):
    lib = Library(os.environ["LINKLIB_DB"])
    try:
        cid = lib.add_community("Amp Route Preview Co", "https://amp-route-preview.com",
                                 "Finance & Operations leaders", "", [])
        item_id = lib.add_voice_review_item(
            "communities", cid, "demographic", "bare-ampersand", "Finance & Operations leaders",
        )
    finally:
        lib.close()

    c = _login_admin(env)
    r = c.post("/admin/voice/review-queue/bulk-replace-ampersand/preview",
               data={"item_ids": [str(item_id)]})
    assert r.status_code == 200
    assert "Finance" in r.text
    assert "Operations" in r.text
    assert f'value="{item_id}"' in r.text  # carried forward as a hidden field to the apply form

    lib2 = Library(os.environ["LINKLIB_DB"])
    try:
        row = lib2.get_community(cid)
        item = lib2.get_voice_review_item(item_id)
    finally:
        lib2.close()
    assert row["demographic"] == "Finance & Operations leaders", "preview must never write"
    assert item["status"] == "open"


def test_preview_route_shows_left_as_is_section_for_unspaced_field(env):
    lib = Library(os.environ["LINKLIB_DB"])
    try:
        cid = lib.add_community("Amp Route Unspaced Co", "https://amp-route-unspaced.com",
                                 "A firm doing S&M consulting", "", [])
        item_id = lib.add_voice_review_item(
            "communities", cid, "demographic", "bare-ampersand", "A firm doing S&M consulting",
        )
    finally:
        lib.close()

    c = _login_admin(env)
    r = c.post("/admin/voice/review-queue/bulk-replace-ampersand/preview",
               data={"item_ids": [str(item_id)]})
    assert r.status_code == 200
    assert "Left as-is" in r.text
    assert "Nothing to replace" in r.text  # the confirm button is disabled with nothing eligible


def test_preview_route_with_no_selection_redirects_with_error(env):
    c = _login_admin(env)
    r = c.post("/admin/voice/review-queue/bulk-replace-ampersand/preview",
               data={}, follow_redirects=False)
    assert r.status_code == 303
    assert "error=" in r.headers["location"]


def test_apply_route_writes_and_resolves(env):
    lib = Library(os.environ["LINKLIB_DB"])
    try:
        cid = lib.add_community("Amp Route Apply Co", "https://amp-route-apply.com",
                                 "Finance & Operations leaders", "", [])
        item_id = lib.add_voice_review_item(
            "communities", cid, "demographic", "bare-ampersand", "Finance & Operations leaders",
        )
    finally:
        lib.close()

    c = _login_admin(env)
    r = c.post("/admin/voice/review-queue/bulk-replace-ampersand/apply",
               data={"item_ids": [str(item_id)]}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/admin/voice/review-queue"

    lib2 = Library(os.environ["LINKLIB_DB"])
    try:
        row = lib2.get_community(cid)
        item = lib2.get_voice_review_item(item_id)
    finally:
        lib2.close()
    assert row["demographic"] == "Finance and Operations leaders"
    assert item["status"] == "resolved"


def test_apply_route_leaves_unspaced_row_open(env):
    lib = Library(os.environ["LINKLIB_DB"])
    try:
        cid = lib.add_community("Amp Route Apply Unspaced Co", "https://amp-route-apply-unspaced.com",
                                 "A firm doing S&M consulting", "", [])
        item_id = lib.add_voice_review_item(
            "communities", cid, "demographic", "bare-ampersand", "A firm doing S&M consulting",
        )
    finally:
        lib.close()

    c = _login_admin(env)
    c.post("/admin/voice/review-queue/bulk-replace-ampersand/apply",
           data={"item_ids": [str(item_id)]}, follow_redirects=False)

    lib2 = Library(os.environ["LINKLIB_DB"])
    try:
        row = lib2.get_community(cid)
        item = lib2.get_voice_review_item(item_id)
    finally:
        lib2.close()
    assert row["demographic"] == "A firm doing S&M consulting"
    assert item["status"] == "open"


def test_apply_route_with_bogus_item_id_is_a_safe_noop(env):
    c = _login_admin(env)
    r = c.post("/admin/voice/review-queue/bulk-replace-ampersand/apply",
               data={"item_ids": ["999999", "not-an-int"]}, follow_redirects=False)
    assert r.status_code == 303  # no crash


def test_preview_and_apply_routes_require_auth(env):
    c = _client(env)  # no login
    r1 = c.post("/admin/voice/review-queue/bulk-replace-ampersand/preview",
                data={"item_ids": ["1"]})
    assert r1.status_code == 401
    r2 = c.post("/admin/voice/review-queue/bulk-replace-ampersand/apply",
                data={"item_ids": ["1"]})
    assert r2.status_code == 401


def test_ampersand_group_shows_replace_button_only_for_bare_ampersand(env):
    # The group-level bulk-actions bar (and this new button with it) only
    # renders once a group has more than one row — see
    # _voice_review_group_bulk_actions_html's own call site — so this
    # needs 2+ ampersand rows and 2+ buzzword rows to exercise both.
    lib = Library(os.environ["LINKLIB_DB"])
    try:
        cid1 = lib.add_community("Amp Button Co", "https://amp-button.com",
                                  "Finance & Operations leaders", "", [])
        lib.add_voice_review_item(
            "communities", cid1, "demographic", "bare-ampersand", "Finance & Operations leaders",
        )
        cid2 = lib.add_community("Amp Button Co 2", "https://amp-button-2.com",
                                  "Widgets & Gadgets only", "", [])
        lib.add_voice_review_item(
            "communities", cid2, "demographic", "bare-ampersand", "Widgets & Gadgets only",
        )
        tid1 = lib.add_tool("Buzzword Tool", "This is seamless.", "https://buzzword-tool.example", [], approved=1)
        lib.add_voice_review_item("tools", tid1, "description", "buzzword", "seamless")
        tid2 = lib.add_tool("Buzzword Tool 2", "This is robust.", "https://buzzword-tool-2.example", [], approved=1)
        lib.add_voice_review_item("tools", tid2, "description", "buzzword", "robust")
    finally:
        lib.close()

    c = _login_admin(env)
    r = c.get("/admin/voice/review-queue")
    assert r.status_code == 200
    assert "Replace &amp; with and" in r.text
    assert r.text.count("Replace &amp; with and") == 1  # only on the Ampersands group, not the buzzword one
