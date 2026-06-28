"""Authenticated fetch for subscriber-only domains (linklib/extract.py).

Paid newsletters (Looking for Leverage, Mostly Metrics) need a logged-in fetch
to return full text instead of a preview. The cookie lives in LINKLIB_AUTH_COOKIES
(host env, never the repo). These pin the domain match + that the Cookie header
is attached for configured domains only.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import extract


@pytest.fixture(autouse=True)
def cookies(monkeypatch):
    monkeypatch.setenv(
        "LINKLIB_AUTH_COOKIES",
        '{"mostlymetrics.com": "substack.sid=AAA", "lookingforleverage.com": "substack.sid=BBB"}',
    )


def test_cookie_match_by_domain_and_subdomain():
    assert extract._cookie_for("https://www.mostlymetrics.com/p/x") == "substack.sid=AAA"
    assert extract._cookie_for("https://mostlymetrics.com/p/y") == "substack.sid=AAA"
    assert extract._cookie_for("http://lookingforleverage.com/p/z") == "substack.sid=BBB"


def test_no_cookie_for_other_domains():
    assert extract._cookie_for("https://www.saastr.com/post") == ""
    assert extract._cookie_for("https://example.com/") == ""


def test_malformed_env_is_ignored(monkeypatch):
    monkeypatch.setenv("LINKLIB_AUTH_COOKIES", "not json")
    assert extract._cookie_for("https://mostlymetrics.com/p/x") == ""


def test_fetch_page_sends_cookie_for_configured_domain(monkeypatch):
    seen = {}

    class _Resp:
        status_code = 200
        text = "<html><title>Paid Post</title><body>full members-only text</body></html>"
        def raise_for_status(self): pass

    def fake_get(url, headers=None, timeout=None):
        seen["headers"] = headers
        return _Resp()

    monkeypatch.setattr(extract.requests, "get", fake_get)

    extract.fetch_page("https://www.mostlymetrics.com/p/abc")
    assert seen["headers"].get("Cookie") == "substack.sid=AAA"


def test_fetch_page_omits_cookie_for_other_domains(monkeypatch):
    seen = {}

    class _Resp:
        status_code = 200
        text = "<html><title>Free</title><body>hi</body></html>"
        def raise_for_status(self): pass

    monkeypatch.setattr(extract.requests, "get",
                        lambda url, headers=None, timeout=None: seen.update(headers=headers) or _Resp())

    extract.fetch_page("https://www.saastr.com/post")
    assert "Cookie" not in seen["headers"]
