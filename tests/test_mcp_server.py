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
import re
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


def test_well_known_oauth_404s_directly_on_mcp_host(appmod):
    """An MCP client probes /.well-known/oauth-* (RFC 8414/9728 discovery)
    before concluding there's no OAuth layer here. Left as a redirect, the
    client would chase it onto bmweis.com and into Cloudflare's Bot Fight
    Mode instead of getting a clean, same-origin 'no OAuth here' answer."""
    r = _client(appmod).get(
        "/.well-known/oauth-authorization-server",
        headers={"host": "mcp.bmweis.com"}, follow_redirects=False,
    )
    assert r.status_code == 404
    assert "location" not in r.headers


def test_well_known_oauth_protected_resource_404s_directly_on_mcp_host(appmod):
    r = _client(appmod).get(
        "/.well-known/oauth-protected-resource",
        headers={"host": "mcp.bmweis.com"}, follow_redirects=False,
    )
    assert r.status_code == 404
    assert "location" not in r.headers


def test_other_well_known_paths_on_mcp_host_still_redirect(appmod):
    """The exemption is scoped to /.well-known/oauth-*, not the whole
    /.well-known/ tree — anything else on the MCP host still 301s to the
    apex like every other non-/mcp, non-/health path there."""
    r = _client(appmod).get(
        "/.well-known/something-unrelated",
        headers={"host": "mcp.bmweis.com"}, follow_redirects=False,
    )
    assert r.status_code == 301


def test_well_known_oauth_404s_directly_on_raw_railway_origin(appmod):
    """The raw Railway origin is the documented DNS-outage fallback for
    /mcp (see test_raw_railway_origin_serves_mcp_path_directly) — an MCP
    client falling back to it hits the identical OAuth-discovery-chased-
    into-Cloudflare problem, so it gets the same 404 carve-out."""
    r = _client(appmod).get(
        "/.well-known/oauth-authorization-server",
        headers={"host": "cfo-navigator-production.up.railway.app"},
        follow_redirects=False,
    )
    assert r.status_code == 404
    assert "location" not in r.headers


def test_other_well_known_paths_on_railway_origin_still_redirect(appmod):
    r = _client(appmod).get(
        "/.well-known/something-unrelated",
        headers={"host": "cfo-navigator-production.up.railway.app"},
        follow_redirects=False,
    )
    assert r.status_code == 301


def test_well_known_oauth_on_www_still_redirects_not_exempted(appmod):
    """www.bmweis.com never serves /mcp at all — the exemption must not
    accidentally widen to every _LEGACY_HOSTS entry, only the one that's
    actually a documented /mcp fallback."""
    r = _client(appmod).get(
        "/.well-known/oauth-authorization-server",
        headers={"host": "www.bmweis.com"}, follow_redirects=False,
    )
    assert r.status_code == 301


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


# -- Path matching: only /mcp or /mcp/... reaches the gate -------------------
#
# Evidence from prod logs: a scanner probe to POST /mcp-builder was
# captured by the auth gate (401'd) under a loose startswith("/mcp") match.
# Path matching is exact ("/mcp" or "/mcp/...") end to end via the shared
# _mcp_path helper — these pin that a lookalike path falls through to
# ordinary routing instead of ever reaching the MCP auth gate.

def test_mcp_lookalike_path_not_captured_by_auth_gate(appmod):
    """/mcp-builder must 404 through normal routing, never a 401 from the
    MCP auth gate — even with no Authorization header at all, which would
    401 on a real /mcp path."""
    r = _client(appmod).post("/mcp-builder", follow_redirects=False)
    assert r.status_code == 404


def test_mcp_lookalike_path_not_captured_even_with_bad_auth(appmod):
    r = _client(appmod).get("/mcp-builder", headers={"authorization": "Bearer whatever"},
                             follow_redirects=False)
    assert r.status_code == 404


def test_mcp_path_with_trailing_segment_still_gated(appmod):
    """Sanity check the exact match isn't over-tightened: /mcp/anything is
    still a real MCP path and still gated."""
    r = _client(appmod).post("/mcp/", follow_redirects=False)
    assert r.status_code == 401


# -- Bearer-tolerant auth value parsing ---------------------------------------
#
# Real prod diagnostics traced a connector-vs-curl auth mismatch to format
# variance, not a token problem: one client sent a bare token with no
# "Bearer " scheme, another sent "Bearer"+token with the separating space
# lost in transit. webapp.mcp_server.bearer_token_candidates tries the
# trimmed header as the token first, then a leading-bearer-stripped retry.
# These confirm each shape now passes the gate (i.e. is never itself the
# reason for a 401 — status 400 downstream just means FastMCP's own
# streamable-HTTP handling wants a real protocol body/Content-Type, which
# these tests don't send), while a non-bearer scheme and an empty value are
# still rejected.

@pytest.fixture
def token_appmod(appmod):
    db = appmod.DB_PATH
    lib = Library(db)
    _, _, token = _mint(lib, "bearer_variant_user", "admin")
    lib.close()
    return appmod, token


def _post_mcp_with_auth(appmod, header_value=None, headers=None):
    with _client(appmod) as c:
        h = dict(headers or {})
        if header_value is not None:
            h["authorization"] = header_value
        return c.post("/mcp", headers=h, follow_redirects=False)


def test_bearer_scheme_with_space_passes_gate(token_appmod):
    appmod, token = token_appmod
    r = _post_mcp_with_auth(appmod, f"Bearer {token}")
    assert r.status_code != 401


def test_bare_token_with_no_scheme_passes_gate(token_appmod):
    appmod, token = token_appmod
    r = _post_mcp_with_auth(appmod, token)
    assert r.status_code != 401


def test_lowercase_bearer_scheme_passes_gate(token_appmod):
    appmod, token = token_appmod
    r = _post_mcp_with_auth(appmod, f"bearer {token}")
    assert r.status_code != 401


def test_bearer_scheme_with_extra_whitespace_passes_gate(token_appmod):
    appmod, token = token_appmod
    r = _post_mcp_with_auth(appmod, f"  Bearer   {token}  ")
    assert r.status_code != 401


def test_bearer_scheme_with_lost_space_passes_gate(token_appmod):
    """The exact shape one real connector attempt sent: "Bearer"+token with
    no separating space at all."""
    appmod, token = token_appmod
    r = _post_mcp_with_auth(appmod, f"Bearer{token}")
    assert r.status_code != 401


def test_basic_scheme_still_rejected(token_appmod):
    appmod, token = token_appmod
    r = _post_mcp_with_auth(appmod, f"Basic {token}")
    assert r.status_code == 401


def test_empty_authorization_value_rejected(token_appmod):
    appmod, _ = token_appmod
    r = _post_mcp_with_auth(appmod, "")
    assert r.status_code == 401


def test_whitespace_only_authorization_value_rejected(token_appmod):
    appmod, _ = token_appmod
    r = _post_mcp_with_auth(appmod, "   ")
    assert r.status_code == 401


def test_unknown_bare_token_still_rejected(token_appmod):
    """Bearer tolerance widens what shapes are ACCEPTED, not what tokens
    verify — an unknown token in any format is still a 401."""
    appmod, _ = token_appmod
    r = _post_mcp_with_auth(appmod, "totally-unknown-token-value")
    assert r.status_code == 401


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
        db_path = db

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


# -- list_tables: extension shadow-table labeling -----------------------------

def test_list_tables_labels_fts5_shadow_tables(live_server):
    """FTS5's own bookkeeping tables (_data/_idx/_docsize/_config) must be
    labeled shadow_of the parent virtual table, derived dynamically from
    the actually-detected virtual table names — not a hardcoded suffix
    list — so a consumer doesn't misread their row counts as independent
    article content."""
    result = _call_tool(live_server.base_url, live_server.admin, "list_tables")
    assert not result.isError
    tables = result.structuredContent["result"]
    by_name = {t["name"]: t for t in tables}
    for suffix in ("_data", "_idx", "_docsize", "_config"):
        shadow = by_name[f"articles_fts{suffix}"]
        assert shadow["shadow_of"] == "articles_fts"
    # The virtual table itself is not its own shadow.
    assert by_name["articles_fts"]["shadow_of"] is None
    # An ordinary content table is never mislabeled as a shadow.
    assert by_name["users"]["shadow_of"] is None


# -- sample_rows: per-cell truncation -----------------------------------------

def test_sample_rows_truncates_long_cells_by_default(live_server):
    lib = Library(live_server.db_path)
    long_value = "x" * 900
    lib.conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)",
                      ("mcp_truncation_test", long_value))
    lib.conn.commit()
    lib.close()

    result = _call_tool(live_server.base_url, live_server.admin, "sample_rows",
                         {"name": "settings", "n": 25})
    assert not result.isError
    import json
    payload = json.loads(result.content[0].text)
    row = next(r for r in payload["rows"] if r["key"] == "mcp_truncation_test")
    assert len(row["value"]) < len(long_value)
    assert "truncated" in row["value"]
    assert payload["truncated"] is True


def test_sample_rows_max_cell_chars_zero_disables_truncation(live_server):
    lib = Library(live_server.db_path)
    long_value = "y" * 900
    lib.conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)",
                      ("mcp_no_truncation_test", long_value))
    lib.conn.commit()
    lib.close()

    result = _call_tool(live_server.base_url, live_server.admin, "sample_rows",
                         {"name": "settings", "n": 25, "max_cell_chars": 0})
    assert not result.isError
    import json
    payload = json.loads(result.content[0].text)
    row = next(r for r in payload["rows"] if r["key"] == "mcp_no_truncation_test")
    assert row["value"] == long_value
    assert payload["truncated"] is False


# -- Phase 2 (retrieval): sample_rows' offset ---------------------------------

def _seed_settings_rows(db_path: str, n: int, prefix: str = "paging_test"):
    """Insert n freshly-keyed settings rows (a fresh test Library seeds
    zero settings rows — confirmed directly — so this gives a controlled,
    known-size table to page through)."""
    lib = Library(db_path)
    for i in range(n):
        lib.conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)",
                          (f"{prefix}_{i:03d}", f"value-{i:03d}"))
    lib.conn.commit()
    lib.close()


def test_sample_rows_offset_slides_the_ascending_window(live_server):
    _seed_settings_rows(live_server.db_path, 10, prefix="slide_asc")
    first = _call_tool(live_server.base_url, live_server.admin, "sample_rows",
                        {"name": "settings", "n": 3, "offset": 0})
    second = _call_tool(live_server.base_url, live_server.admin, "sample_rows",
                         {"name": "settings", "n": 3, "offset": 3})
    import json
    first_keys = [r["key"] for r in json.loads(first.content[0].text)["rows"]]
    second_keys = [r["key"] for r in json.loads(second.content[0].text)["rows"]]
    assert first_keys != second_keys
    assert not set(first_keys) & set(second_keys)


def test_sample_rows_offset_defaults_to_zero_unchanged_behavior(live_server):
    """Omitting offset must behave exactly as it always has — a pure
    additive change, not a behavior shift for every existing caller."""
    _seed_settings_rows(live_server.db_path, 5, prefix="unchanged")
    with_default = _call_tool(live_server.base_url, live_server.admin, "sample_rows",
                               {"name": "settings", "n": 5})
    with_explicit_zero = _call_tool(live_server.base_url, live_server.admin, "sample_rows",
                                     {"name": "settings", "n": 5, "offset": 0})
    assert with_default.content[0].text == with_explicit_zero.content[0].text


def test_sample_rows_settings_paging_covers_every_row_no_gap_no_overlap(live_server):
    """The literal acceptance case: a settings-shaped table past the old
    50-row dead zone, paged 25/25/5 with zero gap and zero overlap. The
    live app's own startup hooks seed a handful of settings rows before
    this test ever runs (voice_core, pricing_last_verified, and friends —
    real production behavior, not a fixture quirk), so those are cleared
    first to get a truly controlled 55-row table matching the brief's
    literal scenario, rather than asserting against a moving baseline."""
    lib = Library(live_server.db_path)
    lib.conn.execute("DELETE FROM settings")
    lib.conn.commit()
    lib.close()
    _seed_settings_rows(live_server.db_path, 55, prefix="page55")
    import json
    seen_keys: list[str] = []
    counts = []
    for offset in (0, 25, 50):
        result = _call_tool(live_server.base_url, live_server.admin, "sample_rows",
                             {"name": "settings", "n": 25, "offset": offset})
        assert not result.isError
        payload = json.loads(result.content[0].text)
        counts.append(payload["count"])
        seen_keys.extend(r["key"] for r in payload["rows"])
    assert counts == [25, 25, 5]
    # No gap, no overlap: every key appears exactly once across all three pages.
    assert len(seen_keys) == len(set(seen_keys)) == 55


def test_sample_rows_negative_offset_clamped_to_zero(live_server):
    _seed_settings_rows(live_server.db_path, 3, prefix="neg_offset")
    result = _call_tool(live_server.base_url, live_server.admin, "sample_rows",
                         {"name": "settings", "n": 3, "offset": -10})
    assert not result.isError
    import json
    payload = json.loads(result.content[0].text)
    assert payload["count"] == 3


# -- Phase 2 (retrieval): get_rows ---------------------------------------------

def test_get_rows_finds_a_row_in_the_dead_middle_of_a_55_row_table(live_server):
    """The exact motivating scenario: a table with a dead window neither
    sample_rows head nor tail window can reach — get_rows finds the row
    directly, by key, regardless of position."""
    lib = Library(live_server.db_path)
    for i in range(55):
        lib.conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)",
                          (f"htib_row_{i:03d}", f"copy-{i:03d}"))
    lib.conn.commit()
    lib.close()

    # Row 30 of 55 sits outside both a 25-row head window (rows 1-25) and a
    # 25-row tail window (rows 31-55) — the dead middle this tool exists for.
    result = _call_tool(live_server.base_url, live_server.admin, "get_rows",
                         {"name": "settings", "where_column": "key",
                          "where_value": "htib_row_029"})
    assert not result.isError
    import json
    payload = json.loads(result.content[0].text)
    assert payload["count"] == 1
    assert payload["rows"][0]["value"] == "copy-029"


def test_get_rows_would_still_work_at_5000_rows(live_server):
    """Direct acceptance check: get_rows("settings", "key", ...) finds the
    row by value, never by position — unaffected by table size."""
    lib = Library(live_server.db_path)
    for i in range(400):
        lib.conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)",
                          (f"bulk_{i:04d}", f"bulk-value-{i:04d}"))
    lib.conn.commit()
    lib.close()

    result = _call_tool(live_server.base_url, live_server.admin, "get_rows",
                         {"name": "settings", "where_column": "key",
                          "where_value": "bulk_0250"})
    assert not result.isError
    import json
    payload = json.loads(result.content[0].text)
    assert payload["count"] == 1
    assert payload["rows"][0]["value"] == "bulk-value-0250"


def test_get_rows_empty_result_is_not_an_error(live_server):
    result = _call_tool(live_server.base_url, live_server.admin, "get_rows",
                         {"name": "settings", "where_column": "key",
                          "where_value": "no-such-key-anywhere"})
    assert not result.isError
    import json
    payload = json.loads(result.content[0].text)
    assert payload["count"] == 0
    assert payload["rows"] == []
    assert payload["truncated"] is False


def test_get_rows_rejects_unknown_table_no_injection(live_server):
    result = _call_tool(live_server.base_url, live_server.admin, "get_rows",
                         {"name": "settings; DROP TABLE settings;--",
                          "where_column": "key", "where_value": "x"})
    assert result.isError


def test_get_rows_rejects_unknown_column_no_injection(live_server):
    result = _call_tool(live_server.base_url, live_server.admin, "get_rows",
                         {"name": "settings",
                          "where_column": "key\"; DROP TABLE settings;--",
                          "where_value": "x"})
    assert result.isError


def test_get_rows_rejects_a_real_column_on_the_wrong_table(live_server):
    """A column that's real somewhere in the schema but not on THIS table
    must still be rejected — validation is per-table, not a global column
    name allowlist."""
    result = _call_tool(live_server.base_url, live_server.admin, "get_rows",
                         {"name": "settings", "where_column": "username",
                          "where_value": "admin_user"})
    assert result.isError


def test_get_rows_matches_an_integer_column_via_string_value(live_server):
    """where_value is always passed as text, but SQLite's own type
    affinity still matches it against an INTEGER column correctly."""
    lib = Library(live_server.db_path)
    user = lib.get_user("admin_user")
    lib.close()
    result = _call_tool(live_server.base_url, live_server.admin, "get_rows",
                         {"name": "users", "where_column": "id",
                          "where_value": str(user["id"])})
    assert not result.isError
    import json
    payload = json.loads(result.content[0].text)
    assert payload["count"] == 1
    assert payload["rows"][0]["username"] == "admin_user"


def test_get_rows_caps_n_at_25(live_server):
    _seed_settings_rows(live_server.db_path, 40, prefix="cap_test")
    lib = Library(live_server.db_path)
    lib.conn.execute("UPDATE settings SET value = 'shared' WHERE key LIKE 'cap_test_%'")
    lib.conn.commit()
    lib.close()
    result = _call_tool(live_server.base_url, live_server.admin, "get_rows",
                         {"name": "settings", "where_column": "value",
                          "where_value": "shared", "n": 1000})
    assert not result.isError
    import json
    payload = json.loads(result.content[0].text)
    assert payload["count"] <= 25


def test_get_rows_truncates_long_cells_by_default(live_server):
    lib = Library(live_server.db_path)
    long_value = "z" * 900
    lib.conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)",
                      ("get_rows_truncation_test", long_value))
    lib.conn.commit()
    lib.close()

    result = _call_tool(live_server.base_url, live_server.admin, "get_rows",
                         {"name": "settings", "where_column": "key",
                          "where_value": "get_rows_truncation_test"})
    assert not result.isError
    import json
    payload = json.loads(result.content[0].text)
    row = payload["rows"][0]
    assert len(row["value"]) < len(long_value)
    assert "truncated" in row["value"]
    assert payload["truncated"] is True


def test_get_rows_max_cell_chars_zero_disables_truncation(live_server):
    lib = Library(live_server.db_path)
    long_value = "w" * 900
    lib.conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)",
                      ("get_rows_no_truncation_test", long_value))
    lib.conn.commit()
    lib.close()

    result = _call_tool(live_server.base_url, live_server.admin, "get_rows",
                         {"name": "settings", "where_column": "key",
                          "where_value": "get_rows_no_truncation_test",
                          "max_cell_chars": 0})
    assert not result.isError
    import json
    payload = json.loads(result.content[0].text)
    assert payload["rows"][0]["value"] == long_value
    assert payload["truncated"] is False


def test_get_rows_refused_for_non_admin_role(live_server):
    result = _call_tool(live_server.base_url, live_server.plain_user, "get_rows",
                         {"name": "settings", "where_column": "key",
                          "where_value": "x"})
    assert result.isError
    assert "admin" in result.content[0].text.lower()


# -- Buddy past questions: private / hidden rows over MCP ----------------------

def _seed_private_and_hidden_question(db_path):
    lib = Library(db_path)
    uid = lib.create_user("asker", "supersecret", role="user")
    qid = lib.record_ask_question(uid, "Private annual planning question", "A.", "m", "standard",
                                  True, False, True, is_private=True)
    hid = lib.record_ask_question(uid, "Hidden annual planning question", "A.", "m", "standard",
                                  True, False, True)
    lib.set_ask_question_hidden(hid, True)
    lib.close()
    return qid, hid


def test_non_admin_token_cannot_read_private_or_hidden_questions(live_server):
    """No non-admin MCP tool returns another user's ask_questions rows: the only
    path to the table is the admin-only introspection tools, refused for a plain
    user's token."""
    qid, hid = _seed_private_and_hidden_question(live_server.db_path)
    for tool, args in (("get_rows", {"name": "ask_questions", "where_column": "id", "where_value": str(qid)}),
                       ("sample_rows", {"name": "ask_questions"})):
        result = _call_tool(live_server.base_url, live_server.plain_user, tool, args)
        assert result.isError
        assert "admin" in result.content[0].text.lower()


def test_admin_token_sees_private_and_hidden_rows_labelled_by_their_flags(live_server):
    qid, hid = _seed_private_and_hidden_question(live_server.db_path)
    r = _call_tool(live_server.base_url, live_server.admin, "get_rows",
                   {"name": "ask_questions", "where_column": "id", "where_value": str(qid)})
    import json
    assert not r.isError and json.loads(r.content[0].text)["rows"][0]["is_private"] == 1
    r = _call_tool(live_server.base_url, live_server.admin, "get_rows",
                   {"name": "ask_questions", "where_column": "id", "where_value": str(hid)})
    assert json.loads(r.content[0].text)["rows"][0]["hidden_public"] == 1


def test_only_admin_introspection_reads_ask_questions_rows():
    """Source guard: the Toolbox, Library and Q&A MCP modules never SELECT from
    ask_questions, so a later tool that does has to come through this review."""
    import pathlib
    for name in ("mcp_toolbox.py", "mcp_library.py", "mcp_qa.py"):
        src = pathlib.Path("webapp", name).read_text()
        assert not re.search(r"(FROM|JOIN)\s+ask_questions", src, re.I), name
        assert "list_public_ask_questions" not in src and "similar_ask_candidates" not in src, name
