"""Authenticated fetch for subscriber-only domains (linklib/extract.py).

Paid newsletters (Mostly Metrics and friends) need a logged-in fetch to
return full text instead of a preview. Each configured domain's cookie
lives in its own LINKLIB_COOKIE_<DOMAIN> env var (host env, never the
repo) — see extract._cookie_env_var. The candidate domain set itself comes
from extract._opml_feed_domains (every feed's domain in preferred_sites.opml
— see the 2026-09 comment on that function for why it's no longer a
hardcoded tuple), so these tests patch that function directly rather than a
static registry attribute. These pin the domain match + that the Cookie
header is attached for configured domains only.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import extract

# Test-only domains, patched into the real lookup rather than relying on
# production's actual feed domains — these tests exercise the
# matching/fetch mechanism generically, not any specific real domain.
_TEST_DOMAINS = ("mostlymetrics.com", "lookingforleverage.com")


@pytest.fixture
def cookies(monkeypatch):
    """Not autouse — the two _opml_feed_domains tests below need the real
    implementation, so this is opted into explicitly by every test that
    wants the fake fixed domain set instead."""
    monkeypatch.setattr(extract, "_opml_feed_domains", lambda: _TEST_DOMAINS)
    monkeypatch.setenv(extract._cookie_env_var("mostlymetrics.com"), "substack.sid=AAA")
    monkeypatch.setenv(extract._cookie_env_var("lookingforleverage.com"), "substack.sid=BBB")


def test_cookie_match_by_domain_and_subdomain(cookies):
    assert extract._cookie_for("https://www.mostlymetrics.com/p/x") == "substack.sid=AAA"
    assert extract._cookie_for("https://mostlymetrics.com/p/y") == "substack.sid=AAA"
    assert extract._cookie_for("http://lookingforleverage.com/p/z") == "substack.sid=BBB"


def test_no_cookie_for_other_domains(cookies):
    assert extract._cookie_for("https://www.saastr.com/post") == ""
    assert extract._cookie_for("https://example.com/") == ""


def test_unset_env_var_for_a_registered_domain_is_ignored(cookies, monkeypatch):
    monkeypatch.delenv(extract._cookie_env_var("mostlymetrics.com"), raising=False)
    assert extract._cookie_for("https://mostlymetrics.com/p/x") == ""


def test_domain_not_in_registry_is_never_checked(cookies, monkeypatch):
    # Even with its env var set, a domain outside _opml_feed_domains() is never read.
    monkeypatch.setenv(extract._cookie_env_var("notregistered.example"), "sid=ZZZ")
    assert extract._cookie_for("https://notregistered.example/p") == ""


def test_cookie_env_var_name_normalizes_dots_and_hyphens():
    assert extract._cookie_env_var("mostlymetrics.com") == "LINKLIB_COOKIE_MOSTLYMETRICS_COM"
    assert extract._cookie_env_var("on-ly.co") == "LINKLIB_COOKIE_ON_LY_CO"


def test_opml_feed_domains_derives_from_the_feeds_table_not_a_hardcoded_list(tmp_path, monkeypatch):
    """The 2026-09 fix itself: a domain with a www-prefixed feed URL and no
    code changes anywhere still gets its cookie checked, purely because it's
    a feed in preferred_sites.opml — the exact case (Cautious Optimism) that
    exposed the old hardcoded _COOKIE_DOMAINS tuple never having been
    updated for a new paid subscription."""
    opml = tmp_path / "sites.opml"
    opml.write_text(
        '<?xml version="1.0"?><opml version="2.0"><body>'
        '<outline text="Section"><outline type="rss" text="Cautious Optimism" '
        'xmlUrl="https://www.cautiousoptimism.news/feed" '
        'htmlUrl="https://www.cautiousoptimism.news/"/></outline></body></opml>',
        encoding="utf-8",
    )
    monkeypatch.setenv("LINKLIB_SITES_OPML", str(opml))
    assert extract._opml_feed_domains() == ("cautiousoptimism.news",)
    monkeypatch.setenv(extract._cookie_env_var("cautiousoptimism.news"), "session=abc")
    assert extract._cookie_for("https://www.cautiousoptimism.news/p/x") == "session=abc"


def test_opml_feed_domains_missing_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("LINKLIB_SITES_OPML", str(tmp_path / "nope.opml"))
    assert extract._opml_feed_domains() == ()


def test_fetch_page_sends_cookie_for_configured_domain(cookies, monkeypatch):
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


def test_fetch_page_omits_cookie_for_other_domains(cookies, monkeypatch):
    seen = {}

    class _Resp:
        status_code = 200
        text = "<html><title>Free</title><body>hi</body></html>"
        def raise_for_status(self): pass

    monkeypatch.setattr(extract.requests, "get",
                        lambda url, headers=None, timeout=None: seen.update(headers=headers) or _Resp())

    extract.fetch_page("https://www.saastr.com/post")
    assert "Cookie" not in seen["headers"]
