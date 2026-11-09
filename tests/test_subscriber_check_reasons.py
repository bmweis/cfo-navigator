"""Subscriber cookie check: every outcome says WHY (2026-11).

All HTTP is stubbed (requests.get in linklib.extract); nothing touches the
network. The cookie value in these tests is the sentinel SECRET_VALUE and
must never appear in the stored record, a rendered cell or the logs.
"""
import json
import logging
import pathlib
import sys

import pytest
import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import authcheck, extract
from linklib.db import Library

SECRET = "SECRET_VALUE_9f3a"
DOMAIN = "mostlymetrics.com"
POST = "https://www.mostlymetrics.com/p/some-post"
FULL_TEXT = "<html><body><article>" + ("word " * 400) + "</article></body></html>"
CF_BODY = "<html><title>Just a moment...</title><body>cf_chl_opt</body></html>"
PAYWALL = ("<html><body><p>This post is for paid subscribers</p>"
           + ("teaser " * 30) + "</body></html>")


def _resp(status, body, url=POST, history=0, headers=None):
    r = requests.Response()
    r.status_code = status
    r._content = body.encode()
    r.encoding = "utf-8"
    r.url = url
    r.history = [object()] * history
    r.headers.update(headers or {})
    r.reason = "x"
    return r


@pytest.fixture
def lib(tmp_path):
    db = Library(str(tmp_path / "t.db"))
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv(extract._cookie_env_var(DOMAIN), f"token={SECRET}; _px3=zzz{SECRET}")
    monkeypatch.setattr(authcheck, "_recent_post_url", lambda dom, opml: (POST, "A Post"))


def _stub(monkeypatch, result, seen=None):
    def fake_get(url, headers=None, timeout=None):
        if seen is not None:
            seen.append((url, dict(headers or {})))
        if isinstance(result, Exception):
            raise result
        return result
    monkeypatch.setattr(extract.requests, "get", fake_get)


def _check(lib):
    return authcheck.check_auth_cookies(lib, "ignored.opml")[DOMAIN]


# -- each reason on its stubbed response ------------------------------------

def test_accepted(lib, monkeypatch):
    _stub(monkeypatch, _resp(200, FULL_TEXT))
    rec = _check(lib)
    assert rec["ok"] is True and rec["reason"] == "accepted"
    assert rec["reason_text"] == "Cookie accepted"
    assert rec["http_status"] == 200 and rec["final_host"] == "www.mostlymetrics.com"
    assert rec["final_path"] == "/p/some-post"
    assert rec["detail"].startswith("full text fetched")


def test_cloudflare_403_is_a_bot_challenge(lib, monkeypatch):
    _stub(monkeypatch, _resp(403, CF_BODY))
    rec = _check(lib)
    assert (rec["ok"], rec["reason"], rec["http_status"]) == (False, "bot_challenge", 403)


def test_cf_mitigated_header_is_a_bot_challenge(lib, monkeypatch):
    _stub(monkeypatch, _resp(403, "denied", headers={"cf-mitigated": "challenge"}))
    assert _check(lib)["reason"] == "bot_challenge"


def test_plain_403_and_401_are_rejected(lib, monkeypatch):
    for code in (401, 403):
        _stub(monkeypatch, _resp(code, "Forbidden"))
        rec = _check(lib)
        assert (rec["ok"], rec["reason"], rec["http_status"]) == (False, "rejected", code)


def test_200_challenge_page_is_labelled_a_bot_challenge(lib, monkeypatch):
    """Reason only: ok follows the unchanged original rule (see the PR report)."""
    body = CF_BODY + ("padding " * 100)
    _stub(monkeypatch, _resp(200, body))
    rec = _check(lib)
    assert rec["reason"] == "bot_challenge"


def test_login_redirect(lib, monkeypatch):
    _stub(monkeypatch, _resp(200, "<html><body>Please sign in</body></html>",
                             url="https://app.beehiiv.com/login?next=%2Fsecret_query", history=1))
    rec = _check(lib)
    # Reason only: a sign-in page still has some text, so ok stays True under
    # the unchanged original rule (reported in the PR as a rule to change).
    assert rec["reason"] == "login_redirect"
    assert rec["final_host"] == "app.beehiiv.com" and rec["final_path"] == "/login"
    assert "secret_query" not in json.dumps(rec)      # no query strings stored


def test_paywall_text_with_cookie_sent(lib, monkeypatch):
    _stub(monkeypatch, _resp(200, PAYWALL))
    rec = _check(lib)
    assert (rec["ok"], rec["reason"], rec["cookie_sent"]) == (False, "paywall", True)


def test_paywall_when_the_test_post_is_on_another_host(lib, monkeypatch):
    other = "https://mostlymetrics.beehiiv.com/p/x"
    monkeypatch.setattr(authcheck, "_recent_post_url", lambda dom, opml: (other, "A Post"))
    seen = []
    _stub(monkeypatch, _resp(200, PAYWALL, url=other), seen)
    rec = _check(lib)
    assert (rec["ok"], rec["reason"], rec["cookie_sent"]) == (False, "not_sent", False)
    assert "Cookie" not in seen[0][1]


def test_timeout_and_connection_error_are_unreachable(lib, monkeypatch):
    for exc in (requests.exceptions.Timeout("slow"),
                requests.exceptions.ReadTimeout("slow")):
        _stub(monkeypatch, exc)
        rec = _check(lib)
        assert (rec["ok"], rec["reason"]) == (False, "unreachable")
    _stub(monkeypatch, requests.exceptions.ConnectionError("dns"))
    assert _check(lib)["reason"] == "unreachable"


def test_other_status_is_unexpected_with_the_code(lib, monkeypatch):
    _stub(monkeypatch, _resp(503, "unavailable"))
    rec = _check(lib)
    assert rec["reason"] == "unexpected" and "HTTP 503" in rec["reason_text"]


def test_empty_200_is_unexpected(lib, monkeypatch):
    _stub(monkeypatch, _resp(200, "<html></html>"))
    rec = _check(lib)
    assert rec["reason"] == "unexpected" and "empty body" in rec["reason_text"]


def test_no_post_found(lib, monkeypatch):
    monkeypatch.setattr(authcheck, "_recent_post_url", lambda dom, opml: ("", ""))
    rec = _check(lib)
    assert (rec["ok"], rec["reason"]) == (None, "no_post")


def test_header_with_line_break_is_reported_and_not_leaked(lib, monkeypatch, caplog):
    monkeypatch.setenv(extract._cookie_env_var(DOMAIN), f"token={SECRET}\nsecond=1")
    leaky = requests.exceptions.InvalidHeader(
        f"Invalid return character or leading space in header: token={SECRET}")
    _stub(monkeypatch, leaky)
    with caplog.at_level(logging.DEBUG):
        rec = _check(lib)
    assert rec["reason"] == "bad_header"
    assert SECRET not in json.dumps(rec) and SECRET not in caplog.text


def test_non_latin1_value_is_not_sendable():
    s = extract.cookie_summary("token=abc’def")
    assert s["sendable"] is False


def test_describe_fetch_error_never_carries_a_header_value():
    exc = requests.exceptions.InvalidHeader(f"bad header token={SECRET}")
    assert extract._describe_fetch_error(exc) == "invalid header value"
    uni = UnicodeEncodeError("latin-1", "x’", 1, 2, "ordinal not in range")
    assert extract._describe_fetch_error(uni) == "invalid header value"


# -- cookie variable name and state -----------------------------------------

def test_variable_name_for_www_and_apex_forms():
    assert extract._cookie_env_var("mostlymetrics.com") == "LINKLIB_COOKIE_MOSTLYMETRICS_COM"
    # a feed URL's domain is www-stripped before the name is derived
    from urllib.parse import urlsplit
    for url in ("https://www.mostlymetrics.com/feed", "https://mostlymetrics.com/feed"):
        host = urlsplit(url).netloc
        host = host[4:] if host.startswith("www.") else host
        assert extract._cookie_env_var(host) == "LINKLIB_COOKIE_MOSTLYMETRICS_COM"


def test_cookie_is_found_for_www_and_apex_urls(monkeypatch):
    monkeypatch.setattr(extract, "_opml_feed_domains", lambda: (DOMAIN,))
    assert extract._cookie_for("https://www.mostlymetrics.com/feed")
    assert extract._cookie_for("https://mostlymetrics.com/feed")
    assert not extract._cookie_for("https://other.example/feed")


def test_variable_state_missing_empty_set(monkeypatch):
    var = extract._cookie_env_var(DOMAIN)
    monkeypatch.delenv(var, raising=False)
    assert extract.cookie_var_state(DOMAIN) == "missing"
    monkeypatch.setenv(var, "   ")
    assert extract.cookie_var_state(DOMAIN) == "empty"
    monkeypatch.setenv(var, "a=b")
    assert extract.cookie_var_state(DOMAIN) == "set"


def test_cookie_summary_names_and_bytes_only():
    s = extract.cookie_summary(f"token={SECRET}; _px3=abc")
    assert s["names"] == ["token", "_px3"] and s["name_count"] == 2
    assert s["bytes"] == len(f"token={SECRET}; _px3=abc") and s["sendable"] is True
    assert SECRET not in json.dumps(s)
    assert extract.cookie_summary("bareValueNoName")["names"] == []


# -- sent-cookie facts and no leakage ---------------------------------------

def test_record_lists_cookie_names_and_length_and_sends_the_header(lib, monkeypatch, caplog):
    seen = []
    _stub(monkeypatch, _resp(200, FULL_TEXT), seen)
    with caplog.at_level(logging.DEBUG):
        rec = _check(lib)
    assert rec["cookie_names"] == ["token", "_px3"] and rec["cookie_name_count"] == 2
    assert rec["cookie_bytes"] == len(f"token={SECRET}; _px3=zzz{SECRET}")
    assert "Cookie" in seen[0][1]                      # it really was sent
    stored = lib.get_setting(authcheck.STATUS_KEY)
    assert SECRET not in stored and SECRET not in caplog.text


# -- rendering ---------------------------------------------------------------

@pytest.fixture
def appmod(tmp_path, monkeypatch):
    monkeypatch.setenv("LINKLIB_DB", str(tmp_path / "app.db"))
    import importlib
    import webapp.app as m
    return importlib.reload(m)


def test_cell_shows_reason_status_names_and_never_the_value(appmod, lib, monkeypatch):
    _stub(monkeypatch, _resp(403, CF_BODY))
    status = {DOMAIN: _check(lib)}
    html = (appmod._cf_cookie_cell_html(DOMAIN, True, status, "Mostly Metrics")
            + appmod._cf_lastcheck_cell_html(DOMAIN, True, status, "Mostly Metrics"))
    assert "expired" in html and "Bot challenge (Cloudflare or similar)" in html
    assert "HTTP 403" in html and "2 cookies (token, _px3)" in html and "bytes" in html
    assert SECRET not in html


def test_cell_for_an_old_record_says_reason_not_recorded(appmod):
    status = {DOMAIN: {"ok": False, "checked_at": "2026-11-08T00:00:00+00:00",
                       "detail": "got a preview/paywall"}}
    html = appmod._cf_lastcheck_cell_html(DOMAIN, True, status, "MM")
    assert "Reason not recorded" in html


def test_cell_for_missing_and_empty_variable(appmod, monkeypatch):
    var = extract._cookie_env_var("example.org")
    monkeypatch.delenv(var, raising=False)
    missing = appmod._cf_cookie_cell_html("example.org", False, {}, "Ex")
    assert "variable missing" in missing
    monkeypatch.setenv(var, "")
    empty = appmod._cf_cookie_cell_html("example.org", False, {}, "Ex")
    assert "Variable empty" in empty


def test_reason_survives_a_reload(lib, monkeypatch):
    _stub(monkeypatch, _resp(403, "Forbidden"))
    _check(lib)
    again = authcheck.get_auth_status(lib)[DOMAIN]
    assert again["reason"] == "rejected" and again["http_status"] == 403
