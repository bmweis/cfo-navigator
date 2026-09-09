"""linklib/logodev.py — the active logo source (2026-09, replacing
Brandfetch — see that module's own docstring for why). Covers domain
extraction and fetch_logo_asset's response handling, including that
fallback=404 is always forced in the request, against a fake HTTP session,
never a real network call.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import logodev


class _FakeResponse:
    def __init__(self, status_code=200, text="", content=b""):
        self.status_code = status_code
        self.text = text
        self.content = content


class _FakeSession:
    def __init__(self, response):
        self._response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self._response


# --- extract_domain ---------------------------------------------------

def test_extract_domain_strips_www_and_path():
    assert logodev.extract_domain("https://www.brex.com/pricing") == "brex.com"


def test_extract_domain_handles_bare_host_no_scheme():
    assert logodev.extract_domain("carta.com") == "carta.com"


def test_extract_domain_returns_none_for_empty_or_garbage():
    assert logodev.extract_domain("") is None
    assert logodev.extract_domain("not a url at all!!") is None


# --- fetch_logo_asset ---------------------------------------------------

def test_fetch_logo_asset_success_returns_image_bytes():
    session = _FakeSession(_FakeResponse(status_code=200, content=b"\x89PNGfake"))
    asset, err = logodev.fetch_logo_asset("rightrev.com", "fake-key", session)
    assert err is None
    assert asset == (b"\x89PNGfake", "png", "icon")


def test_fetch_logo_asset_always_forces_fallback_404():
    """The one non-negotiable requirement: without fallback=404, a miss
    silently comes back as a 200 with a generated monogram — indistinguishable
    from a real logo. Every request this module makes must force it."""
    session = _FakeSession(_FakeResponse(status_code=200, content=b"x"))
    logodev.fetch_logo_asset("anything.example", "fake-key", session)
    assert len(session.calls) == 1
    _, kwargs = session.calls[0]
    assert kwargs["params"]["fallback"] == "404"


def test_fetch_logo_asset_404_is_a_real_miss():
    session = _FakeSession(_FakeResponse(status_code=404))
    asset, err = logodev.fetch_logo_asset("nowhere.example", "fake-key", session)
    assert asset is None
    assert "404" in err


def test_fetch_logo_asset_401_reports_bad_key():
    session = _FakeSession(_FakeResponse(status_code=401))
    asset, err = logodev.fetch_logo_asset("anything.example", "fake-key", session)
    assert asset is None
    assert "401" in err
    assert "LOGODEV_API_KEY" in err


def test_fetch_logo_asset_429_is_quota():
    session = _FakeSession(_FakeResponse(status_code=429))
    asset, err = logodev.fetch_logo_asset("anything.example", "fake-key", session)
    assert asset is None
    assert err.startswith("QUOTA")


def test_fetch_logo_asset_empty_body_is_not_a_silent_hit():
    """A 200 with no bytes at all is never treated as a real asset, even
    though it isn't a 404 either — defensive, shouldn't normally happen."""
    session = _FakeSession(_FakeResponse(status_code=200, content=b""))
    asset, err = logodev.fetch_logo_asset("anything.example", "fake-key", session)
    assert asset is None
    assert err is not None


def test_fetch_logo_asset_other_status_is_an_error_not_a_miss():
    session = _FakeSession(_FakeResponse(status_code=500, text="server error"))
    asset, err = logodev.fetch_logo_asset("anything.example", "fake-key", session)
    assert asset is None
    assert "500" in err


# --- download_asset -----------------------------------------------------

def test_download_asset_writes_bytes_to_disk(tmp_path):
    dest = tmp_path / "logos" / "tools" / "test.png"
    logodev.download_asset(b"fake-image-bytes", str(dest))
    assert dest.read_bytes() == b"fake-image-bytes"
