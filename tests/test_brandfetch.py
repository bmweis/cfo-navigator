"""linklib/brandfetch.py — the shared Brand API logic extracted (2026-08,
"revert & re-fetch" follow-up to the manual logo override feature) so
scripts/backfill_logos.py's batch run and webapp/app.py's admin live
re-fetch both call exactly one implementation. Covers the pure logic
(domain extraction, asset selection) and fetch_logo_asset's response
handling — including the domain-echo guard — against a fake HTTP session,
never a real network call.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import brandfetch


class _FakeResponse:
    def __init__(self, json_data=None, status_code=200, text="", content=b""):
        self._json_data = json_data
        self.status_code = status_code
        self.text = text
        self.content = content or (str(json_data).encode() if json_data else b"")

    def json(self):
        if self._json_data is None:
            raise ValueError("no json")
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"{self.status_code}")


class _FakeSession:
    def __init__(self, response):
        self._response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self._response


# --- extract_domain ------------------------------------------------------

def test_extract_domain_strips_www_and_path():
    assert brandfetch.extract_domain("https://www.getaleph.com/pricing") == "getaleph.com"


def test_extract_domain_handles_bare_host_no_scheme():
    assert brandfetch.extract_domain("brex.com") == "brex.com"


def test_extract_domain_returns_none_for_empty_or_garbage():
    assert brandfetch.extract_domain("") is None
    assert brandfetch.extract_domain("not a url at all!!") is None


# --- best_logo_asset -------------------------------------------------------

def test_best_logo_asset_prefers_logo_type_light_theme_svg():
    data = {
        "logos": [
            {"type": "icon", "theme": "light", "formats": [{"format": "svg", "src": "icon.svg"}]},
            {"type": "logo", "theme": "dark", "formats": [{"format": "svg", "src": "logo-dark.svg"}]},
            {"type": "logo", "theme": "light", "formats": [
                {"format": "png", "src": "logo-light.png"},
                {"format": "svg", "src": "logo-light.svg"},
            ]},
        ]
    }
    assert brandfetch.best_logo_asset(data) == ("logo-light.svg", "svg")


def test_best_logo_asset_falls_back_to_png_when_no_svg():
    data = {"logos": [{"type": "logo", "theme": "light", "formats": [{"format": "png", "src": "x.png"}]}]}
    assert brandfetch.best_logo_asset(data) == ("x.png", "png")


def test_best_logo_asset_none_when_no_usable_asset():
    assert brandfetch.best_logo_asset({"logos": []}) is None
    assert brandfetch.best_logo_asset({}) is None
    assert brandfetch.best_logo_asset({"logos": [{"type": "logo", "formats": []}]}) is None


# --- fetch_logo_asset ------------------------------------------------------

def test_fetch_logo_asset_success():
    data = {"domain": "getaleph.com", "logos": [{"type": "logo", "theme": "light",
                                                  "formats": [{"format": "svg", "src": "https://x/logo.svg"}]}]}
    session = _FakeSession(_FakeResponse(json_data=data, status_code=200))
    asset, err = brandfetch.fetch_logo_asset("getaleph.com", "fake-key", session)
    assert err is None
    assert asset == ("https://x/logo.svg", "svg")


def test_fetch_logo_asset_404():
    session = _FakeSession(_FakeResponse(status_code=404))
    asset, err = brandfetch.fetch_logo_asset("nowhere.example", "fake-key", session)
    assert asset is None
    assert "404" in err


def test_fetch_logo_asset_quota_429():
    session = _FakeSession(_FakeResponse(status_code=429))
    asset, err = brandfetch.fetch_logo_asset("anything.example", "fake-key", session)
    assert asset is None
    assert err.startswith("QUOTA")


def test_fetch_logo_asset_domain_echo_guard_rejects_mismatch():
    """The Aleph/Zapier investigation's defensive fix: a response claiming a
    different domain than what was requested is never trusted, even with a
    200 and a real-looking logos array."""
    data = {"domain": "zapier.com", "logos": [{"type": "logo", "theme": "light",
                                                "formats": [{"format": "svg", "src": "https://x/zapier.svg"}]}]}
    session = _FakeSession(_FakeResponse(json_data=data, status_code=200))
    asset, err = brandfetch.fetch_logo_asset("getaleph.com", "fake-key", session)
    assert asset is None
    assert "MISMATCH" in err
    assert "getaleph.com" in err and "zapier.com" in err


def test_fetch_logo_asset_domain_echo_guard_allows_www_variant():
    """A response echoing "www.<domain>" is still the same site — not a
    mismatch."""
    data = {"domain": "www.getaleph.com", "logos": [{"type": "logo", "theme": "light",
                                                       "formats": [{"format": "svg", "src": "https://x/logo.svg"}]}]}
    session = _FakeSession(_FakeResponse(json_data=data, status_code=200))
    asset, err = brandfetch.fetch_logo_asset("getaleph.com", "fake-key", session)
    assert err is None
    assert asset == ("https://x/logo.svg", "svg")


def test_fetch_logo_asset_no_domain_field_in_response_is_not_rejected():
    """Not every real response necessarily echoes "domain" — absence isn't
    itself grounds for rejection, only an actual mismatch is."""
    data = {"logos": [{"type": "logo", "theme": "light",
                        "formats": [{"format": "svg", "src": "https://x/logo.svg"}]}]}
    session = _FakeSession(_FakeResponse(json_data=data, status_code=200))
    asset, err = brandfetch.fetch_logo_asset("getaleph.com", "fake-key", session)
    assert err is None
    assert asset == ("https://x/logo.svg", "svg")
