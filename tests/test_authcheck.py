"""Auth-cookie health checks (linklib/authcheck.py).

Pins the probe logic: a full-text fetch marks the cookie OK; a preview/paywall
marks it stale; status persists and stale domains are reported for the banner.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import authcheck, extract
from linklib.db import Library
from linklib.extract import PageData


@pytest.fixture
def lib(tmp_path):
    db = Library(str(tmp_path / "t.db"))
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def cookie_env(monkeypatch):
    monkeypatch.setenv("LINKLIB_AUTH_COOKIES", '{"mostlymetrics.com": "substack.sid=AAA"}')


def test_ok_when_full_text_returned(lib, monkeypatch):
    monkeypatch.setattr(authcheck, "_recent_post_url",
                        lambda dom, opml: ("https://mostlymetrics.com/p/x", "A Post"))
    monkeypatch.setattr(extract, "fetch_page",
                        lambda url, timeout=20: PageData(title="A Post",
                                                         content="x" * 5000, blocked=False))
    status = authcheck.check_auth_cookies(lib, "ignored.opml")
    assert status["mostlymetrics.com"]["ok"] is True
    assert authcheck.stale_domains(status) == []
    # persisted + re-readable
    assert authcheck.get_auth_status(lib)["mostlymetrics.com"]["ok"] is True


def test_stale_when_paywalled(lib, monkeypatch):
    monkeypatch.setattr(authcheck, "_recent_post_url",
                        lambda dom, opml: ("https://mostlymetrics.com/p/x", "A Post"))
    monkeypatch.setattr(extract, "fetch_page",
                        lambda url, timeout=20: PageData(title="A Post", content="",
                                                         blocked=True))
    status = authcheck.check_auth_cookies(lib, "ignored.opml")
    assert status["mostlymetrics.com"]["ok"] is False
    assert authcheck.stale_domains(status) == ["mostlymetrics.com"]


def test_unknown_when_no_post_found(lib, monkeypatch):
    monkeypatch.setattr(authcheck, "_recent_post_url", lambda dom, opml: ("", ""))
    status = authcheck.check_auth_cookies(lib, "ignored.opml")
    assert status["mostlymetrics.com"]["ok"] is None
    assert authcheck.stale_domains(status) == []     # unknown is not "stale"


def test_no_cookies_means_empty(lib, monkeypatch):
    monkeypatch.delenv("LINKLIB_AUTH_COOKIES", raising=False)
    assert authcheck.check_auth_cookies(lib, "ignored.opml") == {}


def test_paywall_detection_markers():
    assert extract.looks_paywalled("<p>This post is for paid subscribers</p>", "short")
    assert not extract.looks_paywalled("<p>normal article</p>", "x" * 2000)
