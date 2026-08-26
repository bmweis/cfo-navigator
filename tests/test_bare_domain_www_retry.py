"""Bare-domain www. retry (2026-08).

Context: scripts/diagnose_reader_backfill_failures.py confirmed against
production that codingvc.com refuses the connection at its bare domain
while www.codingvc.com serves the same page fine (6 affected saved
articles: ids 182, 187, 190, 457, 993, 1476) — a real fetcher gap, not a
data/URL-correction issue. Fixed in extract.fetch_page(): a
requests.exceptions.ConnectionError on the bare-domain attempt triggers one
retry against the www.-prefixed host, only when the URL doesn't already
have a www. host. Scoped narrowly: an HTTP-status failure (403/404/...) is
never retried this way — confirmed the same day that inc.com 403s
identically on both the bare and www hosts, so that's not a www problem.

These tests mock requests.get, since this sandbox has no outbound network
access to the real hosts involved (confirmed via the agent proxy's
connect_rejected policy denial) — see the PR/session notes for the live
production re-run this can't perform here.
"""
import pathlib
import sys

import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import extract as extract_mod


class _FakeResp:
    def __init__(self, status_code=200, text="<html><body><article><p>" +
                 "Real paragraph content, long enough to clear the floor. " * 15 +
                 "</p></article></body></html>"):
        self.status_code = status_code
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(response=self)


def test_connection_error_on_bare_domain_retries_with_www(monkeypatch):
    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append(url)
        if url == "https://codingvc.com/some-post":
            raise requests.exceptions.ConnectionError("Connection refused")
        assert url == "https://www.codingvc.com/some-post"
        return _FakeResp()

    monkeypatch.setattr(extract_mod.requests, "get", fake_get)
    page = extract_mod.fetch_page("https://codingvc.com/some-post")

    assert calls == ["https://codingvc.com/some-post", "https://www.codingvc.com/some-post"]
    assert page.fetch_error == ""
    assert page.content


def test_connection_error_persists_on_www_retry_reports_original_shape(monkeypatch):
    """If the www. retry also fails, fetch_error should describe that
    (second) failure — never silently succeed, never raise."""
    def fake_get(url, headers=None, timeout=None):
        raise requests.exceptions.ConnectionError("Connection refused")

    monkeypatch.setattr(extract_mod.requests, "get", fake_get)
    page = extract_mod.fetch_page("https://codingvc.com/some-post")

    assert page.content == ""
    assert page.title == ""
    assert "connection error" in page.fetch_error


def test_http_status_failure_is_never_retried_with_www(monkeypatch):
    """A 403/404/etc. is not a www problem — confirmed live on inc.com,
    which 403s identically on both hosts. Must not trigger the retry."""
    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append(url)
        return _FakeResp(status_code=403)

    monkeypatch.setattr(extract_mod.requests, "get", fake_get)
    page = extract_mod.fetch_page("https://inc.com/some-post")

    assert calls == ["https://inc.com/some-post"]
    assert page.fetch_error == "HTTP 403"


def test_www_retry_not_attempted_when_url_already_has_www(monkeypatch):
    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append(url)
        raise requests.exceptions.ConnectionError("Connection refused")

    monkeypatch.setattr(extract_mod.requests, "get", fake_get)
    page = extract_mod.fetch_page("https://www.codingvc.com/some-post")

    assert calls == ["https://www.codingvc.com/some-post"]
    assert page.fetch_error


def test_with_www_helper():
    assert extract_mod._with_www("https://codingvc.com/x") == "https://www.codingvc.com/x"
    assert extract_mod._with_www("https://www.codingvc.com/x") == ""
    assert extract_mod._with_www("https://codingvc.com:443/x") == "https://www.codingvc.com:443/x"


def test_successful_bare_domain_fetch_is_not_retried(monkeypatch):
    """The happy path (no connection error) makes exactly one request."""
    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append(url)
        return _FakeResp()

    monkeypatch.setattr(extract_mod.requests, "get", fake_get)
    page = extract_mod.fetch_page("https://codingvc.com/some-post")

    assert calls == ["https://codingvc.com/some-post"]
    assert page.content
