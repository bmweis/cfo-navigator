"""/contact rate limiting: client-IP resolution and the per-IP hourly cap.

_client_ip must prefer CF-Connecting-IP (set authoritatively by Cloudflare on
proxied traffic — Cloudflare only *appends* to X-Forwarded-For, so XFF's first
hop is client-forgeable even through the proxy), falling back to XFF's first
hop, then the socket peer address.
"""
import pathlib
import sys
import tempfile, os

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


@pytest.fixture
def appmod(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    yield appmod
    if os.path.exists(db):
        os.remove(db)


def _request_with(appmod, headers: dict, client_host: str | None = "10.0.0.9"):
    """Build a real starlette Request from a raw ASGI scope."""
    from starlette.requests import Request
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/contact",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": (client_host, 12345) if client_host else None,
        "query_string": b"",
    }
    return Request(scope)


def test_cf_connecting_ip_wins_over_xff(appmod):
    req = _request_with(appmod, {
        "CF-Connecting-IP": "203.0.113.7",
        "X-Forwarded-For": "198.51.100.99, 203.0.113.7",
    })
    assert appmod._client_ip(req) == "203.0.113.7"


def test_falls_back_to_first_xff_hop(appmod):
    req = _request_with(appmod, {"X-Forwarded-For": " 198.51.100.4 , 172.16.0.1"})
    assert appmod._client_ip(req) == "198.51.100.4"


def test_blank_cf_header_is_ignored(appmod):
    req = _request_with(appmod, {
        "CF-Connecting-IP": "  ",
        "X-Forwarded-For": "198.51.100.4",
    })
    assert appmod._client_ip(req) == "198.51.100.4"


def test_falls_back_to_socket_peer(appmod):
    assert appmod._client_ip(_request_with(appmod, {})) == "10.0.0.9"


def test_no_client_at_all(appmod):
    assert appmod._client_ip(_request_with(appmod, {}, client_host=None)) == ""


def test_rate_limit_is_per_ip(appmod):
    limit = appmod.CONTACT_RATE_LIMIT_PER_HOUR
    for _ in range(limit):
        assert not appmod._contact_rate_limited("203.0.113.7")
    assert appmod._contact_rate_limited("203.0.113.7")
    # A different client is unaffected.
    assert not appmod._contact_rate_limited("203.0.113.8")


def test_contact_post_limited_by_cf_ip_not_spoofed_xff(appmod):
    """End to end: a flood from one CF-Connecting-IP is limited even when it
    rotates X-Forwarded-For every request (the pre-fix evasion)."""
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app, raise_server_exceptions=True)
    limit = appmod.CONTACT_RATE_LIMIT_PER_HOUR
    for i in range(limit):
        r = c.post("/contact",
                   data={"name": "n", "email": "e@example.com", "message": "m",
                         "ts": "1", "website": ""},
                   headers={"CF-Connecting-IP": "203.0.113.7",
                            "X-Forwarded-For": f"198.51.100.{i}"},
                   follow_redirects=False)
        assert r.status_code != 400
    r = c.post("/contact",
               data={"name": "n", "email": "e@example.com", "message": "m",
                     "ts": "1", "website": ""},
               headers={"CF-Connecting-IP": "203.0.113.7",
                        "X-Forwarded-For": "198.51.100.250"},
               follow_redirects=False)
    assert r.status_code == 400
