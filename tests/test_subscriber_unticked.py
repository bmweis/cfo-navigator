"""An unticked Subscriber box on /admin/reader/feeds is a decision, and the
subscriber-cookie check honors it (2026-11).

Found live: Mostly Metrics went free, Brian unticked Subscriber and clicked
Re-check subscriber access, and the row still read "expired" with the coral
banner. The check took its domains from the OPML and the LINKLIB_COOKIE_<DOMAIN>
variables; nothing read the Subscriber flag.

Rule: a domain that feeds name, where no feed has Subscriber ticked, is not
probed, its stored status is dropped on the next re-check and ignored before
it, its cells show a dash, and it never raises the banner. A domain with no feed
row at all (nothing decided) is checked as before. HTTP is stubbed throughout.
"""
import importlib
import json
import pathlib
import shutil
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import authcheck, extract
from linklib.db import Library
from linklib.extract import PageData

REPO_OPML = str(pathlib.Path(__file__).resolve().parents[1] / "preferred_sites.opml")
DOMAIN = "mostlymetrics.com"
FEED_URL = "https://www.mostlymetrics.com/feed"
FEED_NAME = "Mostly Metrics test"


def _now():
    return datetime.now(timezone.utc).isoformat()


def _expired():
    return {"ok": False, "checked_at": _now(), "detail": "paywall",
            "reason": "paywall", "reason_text": "Paywall text returned",
            "http_status": 200, "final_host": "www.mostlymetrics.com",
            "final_path": "/p/x", "post_host": "www.mostlymetrics.com",
            "cookie_sent": True, "cookie_names": ["token"], "cookie_name_count": 1,
            "cookie_bytes": 9}


@pytest.fixture(autouse=True)
def cookie_env(monkeypatch):
    monkeypatch.setenv(extract._cookie_env_var(DOMAIN), "token=AAA")


@pytest.fixture
def lib(tmp_path):
    db = Library(str(tmp_path / "t.db"))
    try:
        yield db
    finally:
        db.close()


def _add_feed(lib, subscriber, url=FEED_URL, name=FEED_NAME):
    secs = lib.list_feed_sections()
    sid = secs[0]["id"] if secs else lib.add_feed_section("Test")
    return lib.add_feed(sid, name, url, has_active_subscription=subscriber)


def _forbid_fetch(monkeypatch):
    calls = []

    def boom(*a, **k):
        calls.append(a)
        raise AssertionError("fetch_page must not be called for an unticked domain")
    monkeypatch.setattr(extract, "fetch_page", boom)
    monkeypatch.setattr(authcheck, "_recent_post_url", lambda d, o: ("https://www.mostlymetrics.com/p/x", "T"))
    return calls


def _ok_fetch(monkeypatch):
    monkeypatch.setattr(authcheck, "_recent_post_url", lambda d, o: ("https://www.mostlymetrics.com/p/x", "T"))
    monkeypatch.setattr(extract, "fetch_page",
                        lambda url, timeout=20, send_cookie=True: PageData(
                            title="T", content="x" * 5000, blocked=False, http_status=200))


# -- the check ---------------------------------------------------------------

def test_unticked_domain_is_not_probed(lib, monkeypatch):
    _add_feed(lib, subscriber=False)
    calls = _forbid_fetch(monkeypatch)
    assert authcheck.check_auth_cookies(lib, "ignored.opml") == {}
    assert calls == []


def test_recheck_drops_the_stored_status(lib, monkeypatch):
    _add_feed(lib, subscriber=False)
    lib.set_setting(authcheck.STATUS_KEY, json.dumps({DOMAIN: _expired()}))
    _forbid_fetch(monkeypatch)
    authcheck.check_auth_cookies(lib, "ignored.opml")
    assert DOMAIN not in json.loads(lib.get_setting(authcheck.STATUS_KEY))


def test_status_is_ignored_before_any_recheck(lib):
    _add_feed(lib, subscriber=False)
    lib.set_setting(authcheck.STATUS_KEY, json.dumps({DOMAIN: _expired()}))
    status = authcheck.get_auth_status(lib)
    assert DOMAIN not in status
    assert authcheck.stale_domains(status) == []


def test_ticked_domain_is_still_probed(lib, monkeypatch):
    _add_feed(lib, subscriber=True)
    _ok_fetch(monkeypatch)
    assert authcheck.check_auth_cookies(lib, "ignored.opml")[DOMAIN]["ok"] is True


def test_domain_with_no_feed_row_is_still_probed(lib, monkeypatch):
    # Nothing was decided for it, so it keeps the old behavior.
    _ok_fetch(monkeypatch)
    assert DOMAIN in authcheck.check_auth_cookies(lib, "ignored.opml")


def test_one_ticked_feed_on_the_domain_keeps_it_checked(lib, monkeypatch):
    _add_feed(lib, subscriber=False)
    _add_feed(lib, subscriber=True, url="https://mostlymetrics.com/other.xml", name="Second")
    _ok_fetch(monkeypatch)
    assert DOMAIN in authcheck.check_auth_cookies(lib, "ignored.opml")


# -- the page and the banners -------------------------------------------------

@pytest.fixture
def app_env(monkeypatch, tmp_path):
    opml = tmp_path / "sites.opml"
    shutil.copy(REPO_OPML, opml)
    monkeypatch.setenv("LINKLIB_DB", str(tmp_path / "app.db"))
    monkeypatch.setenv("LINKLIB_SITES_OPML", str(opml))
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import webapp.app as appmod
    importlib.reload(appmod)
    return appmod


def _client(appmod):
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app, raise_server_exceptions=True)
    c.post("/login", data={"username": "admin", "password": "adminpass"},
           follow_redirects=False)
    return c


def _seed(appmod, subscriber):
    lib = appmod._lib()
    try:
        sid = lib.list_feed_sections()[0]["id"] if lib.list_feed_sections() else lib.add_feed_section("Test")
        lib.add_feed(sid, FEED_NAME, FEED_URL, has_active_subscription=subscriber)
        lib.set_setting(authcheck.STATUS_KEY, json.dumps({DOMAIN: _expired()}))
    finally:
        lib.close()


def _row(html):
    marker = f'<td class="ff-name">{FEED_NAME}</td>'
    i = html.index(marker)
    start = html.rindex('<tr class="ff-row"', 0, i)
    return html[start:html.index("</tr>", i)]


def test_unticked_feed_shows_dashes_and_no_banner(app_env):
    _seed(app_env, subscriber=False)
    html = _client(app_env).get("/admin/reader/feeds").text
    row = _row(html)
    assert "expired" not in row.lower()
    assert "Paywall text returned" not in row
    assert "Subscriber cookie expired" not in html
    cookie = row[row.index('<td class="ff-cookie">'):row.index('<td class="ff-lastcheck">')]
    assert "&mdash;" in cookie


def test_ticked_feed_still_shows_expired_and_banner(app_env):
    _seed(app_env, subscriber=True)
    html = _client(app_env).get("/admin/reader/feeds").text
    assert "expired" in _row(html).lower()
    assert "Subscriber cookie expired" in html


def test_recheck_route_clears_an_unticked_domain(app_env, monkeypatch):
    _seed(app_env, subscriber=False)
    _forbid_fetch(monkeypatch)
    c = _client(app_env)
    r = c.post("/admin/auth/recheck", follow_redirects=False)
    assert r.status_code == 303
    lib = app_env._lib()
    try:
        assert DOMAIN not in json.loads(lib.get_setting(authcheck.STATUS_KEY))
    finally:
        lib.close()
    assert "Subscriber cookie expired" not in c.get("/admin/reader/feeds").text


def test_edit_form_readout_says_not_checked(app_env):
    _seed(app_env, subscriber=False)
    lib = app_env._lib()
    try:
        fid = lib.list_feeds()[0]["id"]
    finally:
        lib.close()
    html = _client(app_env).get(f"/admin/reader/feeds/{fid}/edit").text
    assert "Not checked (Subscriber is unticked)" in html
