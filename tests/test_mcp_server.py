"""Tests for the /mcp server (Phase 1):

- Library.create_api_token / verify_api_token / revoke_api_token lifecycle
  (valid / invalid / revoked / wrong-role / inactive-user).
- The canonical-host redirect carve-out for mcp.bmweis.com and the raw
  Railway origin's /mcp path.
- The transport-level auth gate rejects an unauthenticated /mcp request
  with a 401 before any MCP protocol handling.
- The three introspection tools end to end (real streamable-HTTP client,
  a real running server — a TestClient's ASGI shortcut doesn't exercise
  the real session-manager lifecycle this transport needs), including
  virtual-table (FTS5 / sqlite-vec) handling and the sample_rows cap.
"""
import asyncio
import os
import pathlib
import socket
import sys
import tempfile
import threading
import time

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Library


def _mint(lib: Library, username: str, role: str, active: int = 1):
    lib.conn.execute(
        "INSERT INTO users (username, role, active, created_at) VALUES (?, ?, ?, '')",
        (username, role, active),
    )
    lib.conn.commit()
    user = lib.get_user(username)
    token_id, token = lib.create_api_token(user["id"], label="test")
    return user["id"], token_id, token


# -- Library-level token lifecycle (no server needed) ------------------------

def test_verify_valid_token_resolves_user_and_role():
    db = tempfile.mktemp(suffix=".db")
    try:
        lib = Library(db)
        _, _, token = _mint(lib, "bmw", "admin")
        caller = lib.verify_api_token(token)
        assert caller["username"] == "bmw"
        assert caller["role"] == "admin"
    finally:
        if os.path.exists(db):
            os.remove(db)


def test_verify_rejects_unknown_token():
    db = tempfile.mktemp(suffix=".db")
    try:
        lib = Library(db)
        assert lib.verify_api_token("not-a-real-token") is None
        assert lib.verify_api_token("") is None
        assert lib.verify_api_token(None) is None
    finally:
        if os.path.exists(db):
            os.remove(db)


def test_verify_rejects_revoked_token():
    db = tempfile.mktemp(suffix=".db")
    try:
        lib = Library(db)
        _, token_id, token = _mint(lib, "bmw", "admin")
        assert lib.verify_api_token(token) is not None
        assert lib.revoke_api_token(token_id) is True
        assert lib.verify_api_token(token) is None
        # revoking again is a no-op, not an error
        assert lib.revoke_api_token(token_id) is False
    finally:
        if os.path.exists(db):
            os.remove(db)


def test_verify_rejects_token_for_deactivated_user():
    db = tempfile.mktemp(suffix=".db")
    try:
        lib = Library(db)
        _, _, token = _mint(lib, "gone", "admin", active=0)
        assert lib.verify_api_token(token) is None
    finally:
        if os.path.exists(db):
            os.remove(db)


def test_list_api_tokens_never_exposes_hash():
    db = tempfile.mktemp(suffix=".db")
    try:
        lib = Library(db)
        _mint(lib, "bmw", "admin")
        rows = lib.list_api_tokens()
        assert len(rows) == 1
        assert "token_hash" not in rows[0]
    finally:
        if os.path.exists(db):
            os.remove(db)


# -- Canonical-host redirect carve-out ---------------------------------------

@pytest.fixture
def appmod(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PUBLIC_BASE", "https://bmweis.com")
    import importlib
    import webapp.app as appmod
    importlib.reload(appmod)
    yield appmod
    if os.path.exists(db):
        os.remove(db)


def _client(appmod):
    from fastapi.testclient import TestClient
    return TestClient(appmod.app, raise_server_exceptions=True)


def test_mcp_subdomain_serves_mcp_path_directly(appmod):
    """No redirect for /mcp on mcp.bmweis.com — a 401 (no token) proves the
    request reached the auth gate/mount rather than being 301'd away."""
    r = _client(appmod).post("/mcp", headers={"host": "mcp.bmweis.com"},
                              follow_redirects=False)
    assert r.status_code != 301
    assert "location" not in r.headers


def test_mcp_subdomain_serves_health_directly(appmod):
    r = _client(appmod).get("/health", headers={"host": "mcp.bmweis.com"},
                             follow_redirects=False)
    assert r.status_code == 200


def test_mcp_subdomain_redirects_other_paths_to_apex(appmod):
    r = _client(appmod).get("/thought-leadership", headers={"host": "mcp.bmweis.com"},
                             follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"] == "https://bmweis.com/thought-leadership"


def test_raw_railway_origin_serves_mcp_path_directly(appmod):
    """/mcp must also work on the raw Railway origin as a DNS-outage
    fallback — that host is a _LEGACY_HOSTS entry and would otherwise 301
    everything (including /mcp) to the apex."""
    r = _client(appmod).post(
        "/mcp", headers={"host": "cfo-navigator-production.up.railway.app"},
        follow_redirects=False,
    )
    assert r.status_code != 301
    assert "location" not in r.headers


def test_raw_railway_origin_still_redirects_other_paths(appmod):
    r = _client(appmod).get(
        "/thought-leadership", headers={"host": "cfo-navigator-production.up.railway.app"},
        follow_redirects=False,
    )
    assert r.status_code == 301


def test_mcp_path_on_canonical_host_untouched(appmod):
    r = _client(appmod).post("/mcp", headers={"host": "bmweis.com"}, follow_redirects=False)
    assert r.status_code != 301


# -- Transport-level auth gate (401 before any MCP handling) -----------------

def test_mcp_mount_does_not_steal_405_for_other_routes(appmod):
    """Regression pin: an earlier version mounted the MCP sub-app at an
    unrestricted empty prefix, which Starlette's router always reports as a
    FULL path match — for ANY path, method-blind — so it silently stole the
    404-vs-405 decision away from every other route in the app. GET /ask
    (only POST /ask is registered) is real, live proof: it must 405, not
    404, and it broke to 404 under the unrestricted mount."""
    r = _client(appmod).get("/ask", follow_redirects=False)
    assert r.status_code == 405


def test_mcp_missing_authorization_header_401s(appmod):
    r = _client(appmod).post("/mcp", follow_redirects=False)
    assert r.status_code == 401


def test_mcp_malformed_authorization_header_401s(appmod):
    r = _client(appmod).post("/mcp", headers={"authorization": "not-a-bearer-token"},
                              follow_redirects=False)
    assert r.status_code == 401


def test_mcp_invalid_bearer_token_401s(appmod):
    r = _client(appmod).post("/mcp", headers={"authorization": "Bearer nope"},
                              follow_redirects=False)
    assert r.status_code == 401


def test_mcp_401_body_never_echoes_presented_credential(appmod):
    r = _client(appmod).post("/mcp", headers={"authorization": "Bearer super-secret-guess"},
                              follow_redirects=False)
    assert "super-secret-guess" not in r.text


# -- End-to-end: real streamable-HTTP client against a real running server --
#
# A TestClient's ASGI shortcut doesn't run a real event loop the way
# FastMCP's session manager needs (confirmed while building this: it raises
# "Task group is not initialized" without the explicit startup-hook wiring
# webapp.app now has) — these tests boot the real app with real uvicorn on a
# free localhost port so the full protocol (initialize -> call_tool) and the
# module-level path-mount fix (exactly "/mcp", not "/mcp/mcp" or a redirect)
# both get exercised for real, not approximated.

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live_server(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PUBLIC_BASE", "https://bmweis.com")
    import importlib
    import webapp.app as appmod
    importlib.reload(appmod)

    lib = Library(db)
    _, _, admin_token = _mint(lib, "admin_user", "admin")
    _, _, plain_token = _mint(lib, "plain_user", "user")
    lib.close()

    import uvicorn
    port = _free_port()
    config = uvicorn.Config(appmod.app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    else:
        pytest.fail("server did not start in time")

    class Bundle:
        base_url = f"http://127.0.0.1:{port}"
        admin = admin_token
        plain_user = plain_token

    try:
        yield Bundle
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        if os.path.exists(db):
            os.remove(db)


def _call_tool(base_url: str, token: str, tool_name: str, args: dict | None = None):
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async def go():
        headers = {"Authorization": f"Bearer {token}"}
        async with streamablehttp_client(f"{base_url}/mcp", headers=headers) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool(tool_name, args or {})

    return asyncio.run(go())


def test_mcp_external_path_is_exactly_mcp_not_double_mounted(live_server):
    """The whole point of this test: confirm the mount fix actually works
    against the exact external URL a real connector would use — /mcp, not
    /mcp/mcp, and no redirect involved in getting there."""
    result = _call_tool(live_server.base_url, live_server.admin, "list_tables")
    assert not result.isError


def _call_tool_with_host_header(base_url: str, token: str, host_header: str, tool_name: str):
    """Like _call_tool, but with an explicit Host header — simulating a
    request that physically connects to this address but arrives with a
    different Host (exactly what Cloudflare/Railway routing produces for
    mcp.bmweis.com and cfo-navigator-production.up.railway.app in
    production, and what a real DNS-rebinding attack looks like from the
    server's point of view). This is the regression case for the 2026-09
    production incident: FastMCP's own DNS-rebinding-protection allowlist
    defaults to 127.0.0.1/localhost/::1 only, so a real production request
    (Host: mcp.bmweis.com, connecting to the app's real address, not
    127.0.0.1) previously got rejected with a bare-text 421 before our own
    auth gate ever mattered — invisible to local testing, which always
    connects AND sends Host: 127.0.0.1, matching FastMCP's default
    allowlist by construction."""
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async def go():
        headers = {"Authorization": f"Bearer {token}", "Host": host_header}
        async with streamablehttp_client(f"{base_url}/mcp", headers=headers) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool(tool_name, {})

    return asyncio.run(go())


def test_mcp_allowed_production_host_header_succeeds_from_non_matching_address(live_server):
    """Regression pin for the 2026-09 421 incident: a request that connects
    to 127.0.0.1 (this test server's real address) but carries
    Host: mcp.bmweis.com must succeed — that's exactly the shape of a real
    production request, and it's the case that broke before
    extra_allowed_hosts was wired through build_mcp."""
    result = _call_tool_with_host_header(live_server.base_url, live_server.admin,
                                          "mcp.bmweis.com", "list_tables")
    assert not result.isError


def test_mcp_allowed_railway_origin_host_header_succeeds(live_server):
    """Same regression, for the raw Railway origin — the DNS-outage
    fallback host the canonical-host-redirect carve-out also exempts."""
    result = _call_tool_with_host_header(
        live_server.base_url, live_server.admin,
        "cfo-navigator-production.up.railway.app", "list_tables",
    )
    assert not result.isError


def test_mcp_unrecognized_host_header_still_rejected(live_server):
    """The allowlist genuinely restricts, rather than the fix accidentally
    disabling DNS-rebinding protection wholesale — an arbitrary Host that
    isn't in the allowlist must still be refused."""
    with pytest.raises(Exception):
        _call_tool_with_host_header(live_server.base_url, live_server.admin,
                                     "evil.example.com", "list_tables")


def test_list_tables_labels_virtual_tables(live_server):
    result = _call_tool(live_server.base_url, live_server.admin, "list_tables")
    assert not result.isError
    # A list[dict] return comes back as FastMCP's structured content
    # ({"result": [...]}), not one JSON blob in content[0] the way a dict
    # return does (confirmed locally against the real server — a plain
    # list explodes into one content block per item instead).
    tables = result.structuredContent["result"]
    by_name = {t["name"]: t for t in tables}
    assert by_name["articles_fts"]["virtual"] is True
    assert by_name["articles_vec"]["virtual"] is True
    assert by_name["users"]["virtual"] is False
    assert by_name["users"]["row_count"] >= 1


def test_describe_table_reports_columns_for_a_virtual_table(live_server):
    result = _call_tool(live_server.base_url, live_server.admin, "describe_table",
                         {"name": "articles_fts"})
    assert not result.isError
    import json
    info = json.loads(result.content[0].text)
    assert info["virtual"] is True
    assert isinstance(info["columns"], list)


def test_describe_table_rejects_unknown_table(live_server):
    result = _call_tool(live_server.base_url, live_server.admin, "describe_table",
                         {"name": "not_a_real_table; DROP TABLE users"})
    assert result.isError


def test_sample_rows_caps_at_25(live_server):
    result = _call_tool(live_server.base_url, live_server.admin, "sample_rows",
                         {"name": "users", "n": 1000})
    assert not result.isError
    import json
    payload = json.loads(result.content[0].text)
    assert payload["count"] <= 25


def test_sample_rows_rejects_unknown_table_no_injection(live_server):
    result = _call_tool(live_server.base_url, live_server.admin, "sample_rows",
                         {"name": "users; DROP TABLE users;--"})
    assert result.isError


def test_non_admin_role_refused_by_introspection_tools(live_server):
    """A valid, unrevoked token for a non-admin user passes the transport
    gate (it only checks the token, not the role) but is refused inside the
    tool itself — the fail-closed, defense-in-depth behavior the build
    brief asked for."""
    result = _call_tool(live_server.base_url, live_server.plain_user, "list_tables")
    assert result.isError
    assert "admin" in result.content[0].text.lower()
